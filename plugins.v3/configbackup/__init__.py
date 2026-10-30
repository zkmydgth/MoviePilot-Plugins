import glob
import hashlib
import json
import os
import posixpath
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import zipfile
from datetime import datetime
from email.utils import parsedate_to_datetime
from io import StringIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
from apscheduler.triggers.cron import CronTrigger

from app.runtime.config import settings
from app.application.directory import DirectoryHelper
from app.runtime.log import logger
from app.plugins import _PluginBase
from app.schemas import MessageType

from .version import VERSION
from .webdav_client import WebDAVClient, WebDAVError
from app.sdk.string import StringUtils

try:  # 加密是可选能力：宿主环境缺 cryptography 时只禁用加密，不影响明文备份
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
except Exception:  # pragma: no cover - 依赖缺失分支由加密入口给出明确提示
    Cipher = None


#: 加密包文件头标识（后续版本变更格式时用于识别）
_ENC_MAGIC = b"MPBK1"
#: 加密包 KDF 迭代次数（PBKDF2-HMAC-SHA256）
_ENC_KDF_ROUNDS = 390000
#: 加密包盐长度（字节）
_ENC_SALT_LEN = 16
#: 加密包 nonce 长度（字节）
_ENC_NONCE_LEN = 12
#: PG 客户端命令超时（秒）：大库导出/还原可能很久，给足但不能无限挂住
_PG_CMD_TIMEOUT = 1800
#: 备份文件名规则：bk_<时间标识>.zip 或 .zip.enc
#: 时间标识允许 14 位紧凑格式（现行）与历史的下划线格式（兼容老包）
_BACKUP_NAME_RE = re.compile(r"^bk_([0-9A-Za-z_-]{1,64})\.zip(\.enc)?$")
#: 紧凑时间格式（文件名内嵌时间戳）
_COMPACT_STAMP_RE = re.compile(r"^\d{14}$")


class _FileRollback:
    """
    还原过程中的文件回滚记录器。

    做法是「先把会被覆盖的现有内容移进暂存目录，再写入新内容」：同卷内移动是
    rename，几乎不耗时，也不需要额外复制几百 MB。任何一步失败都能按相反顺序恢复。
    """

    def __init__(self, work_dir: Path):
        """
        初始化回滚记录器。

        :param work_dir: 暂存目录（必须位于目标同一文件系统，才能用 rename 移动）
        """
        self._work_dir = Path(work_dir)
        self._work_dir.mkdir(parents=True, exist_ok=True)
        self._moves: List[Tuple[Path, Path]] = []
        self._created: List[Path] = []
        self._counter = 0

    def _slot(self) -> Path:
        """生成暂存目录内的唯一位置。"""
        self._counter += 1
        return self._work_dir / f"{self._counter:04d}"

    def preserve(self, path: Path) -> None:
        """
        把已存在的目标移到暂存区，等待还原成功后删除。

        :param path: 即将被覆盖的文件或目录
        """
        path = Path(path)
        if not path.exists() and not path.is_symlink():
            return
        slot = self._slot()
        shutil.move(str(path), str(slot))
        self._moves.append((path, slot))

    def mark_created(self, path: Path) -> None:
        """
        记录本次新写入的路径；回滚时先删掉它，再把原内容移回。

        :param path: 新写入的文件或目录
        """
        self._created.append(Path(path))

    def rollback(self) -> List[str]:
        """
        回滚全部改动，返回无法恢复的条目（用于告警）。

        :return: 恢复失败的路径说明列表
        """
        failed: List[str] = []
        for path in reversed(self._created):
            try:
                if path.is_symlink() or path.is_file():
                    path.unlink()
                elif path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
            except OSError as e:
                failed.append(f"{path}（清理新内容失败：{e}）")
        for original, slot in reversed(self._moves):
            try:
                original.parent.mkdir(parents=True, exist_ok=True)
                if original.exists() or original.is_symlink():
                    if original.is_dir() and not original.is_symlink():
                        shutil.rmtree(original, ignore_errors=True)
                    else:
                        original.unlink()
                shutil.move(str(slot), str(original))
            except OSError as e:
                failed.append(f"{original}（恢复原内容失败：{e}，备份在 {slot}）")
        return failed

    def commit(self) -> None:
        """还原成功：删除暂存区里的旧内容。"""
        shutil.rmtree(self._work_dir, ignore_errors=True)


class ConfigBackup(_PluginBase):
    """配置备份插件：定时/手动备份系统配置、数据库与插件配置，支持还原。"""

    # 插件名称
    plugin_name = "配置备份"
    # 插件描述
    plugin_desc = "定时备份 MoviePilot 系统配置、数据库及插件配置到指定目录，支持保留数量自动清理、手动触发和一键还原。"
    # 插件版本（从 version.py 读取，避免与包版本不同步）
    plugin_version = VERSION
    # 插件作者
    plugin_author = "zkmydgth"
    # 插件配置项ID前缀
    plugin_config_prefix = "configbackup_"
    # 加载顺序
    plugin_order = 30
    # 可使用的用户级别
    auth_level = 1

    # 备份内容可勾选的部分（元组顺序即表单选项与摘要的展示顺序）
    _PART_DATABASE = "database"
    _PART_SYSTEM = "system"
    _PART_COOKIES = "cookies"
    _PART_PLUGINS = "plugins"
    _PART_EXTRA = "extra"
    _ALL_PARTS = (_PART_DATABASE, _PART_SYSTEM, _PART_COOKIES, _PART_PLUGINS, _PART_EXTRA)
    #: 各部分的展示名（表单选项与备份包摘要共用，改文案只改这里）
    _PART_LABELS = {
        "database": "数据库",
        "system": "系统配置",
        "cookies": "站点 Cookie",
        "plugins": "插件配置",
        "extra": "附加路径",
    }

    # 私有属性
    _enabled = False
    _cron = None
    _backup_dir = None
    _keep_count = 10
    _keep_days = 7
    _backup_parts = _ALL_PARTS
    _extra_paths = None
    # --- WebDAV 远端备份（v3.2.0 模块 B）---
    _webdav_enabled = False
    _webdav_url = ""
    _webdav_user = ""
    _webdav_pass = ""
    _webdav_dir = "/MoviePilot"
    _webdav_keep = ""
    _webdav_timeout = 300
    _webdav_allow_http = False
    _encrypt_pass = ""
    _notify = False
    _notify_type = "插件"
    _onlyonce = False

    #: 还原进行中标记：期间拒绝手动备份/删除/远端操作，避免与还原互相踩踏
    _restoring = False
    #: 配置解析告警（非法 cron、非法目录等），在日志与详情页提示
    _config_warnings: List[str] = []

    # 还原操作锁（防止并发还原）
    _restore_lock = threading.Lock()
    # 备份操作锁（防止「立即运行一次」后台线程与定时任务/手动触发撞车）
    _backup_lock = threading.Lock()

    # 备份文件名前缀
    _prefix = "bk_"

    # 待确认状态有效期（秒）：超时自动作废，避免隔天回来仍挂着「确认还原/确认删除」按钮
    _pending_ttl = 600

    def init_plugin(self, config: dict = None):
        """根据插件配置初始化运行状态（非法值回退默认并记录告警，不让插件加载失败）。"""
        # 停止现有任务
        self.stop_service()

        self._enabled = False
        self._restoring = False
        self._config_warnings = []
        if config:
            self._enabled = bool(config.get("enabled"))
            self._cron = str(config.get("cron") or "").strip()
            if self._cron:
                try:
                    CronTrigger.from_crontab(self._cron)
                except Exception:
                    self._config_warnings.append(f"定时表达式非法：{self._cron}，已停用定时备份")
                    self._cron = ""
            self._backup_dir = self.__sanitize_dir(
                config.get("backup_dir"), "/config/backup", "备份目录"
            )
            self._keep_count = self.__safe_int(config.get("keep_count"), 10, 1, 1000)
            self._keep_days = self.__safe_int(config.get("keep_days"), 0, 0, 3650)
            self._backup_parts = self.__parse_parts(config)
            self._extra_paths = self.__sanitize_extra_paths(config.get("extra_paths"))
            self._webdav_enabled = bool(config.get("webdav_enabled"))
            self._webdav_url = str(config.get("webdav_url") or "").strip()
            self._webdav_user = str(config.get("webdav_user") or "")
            self._webdav_pass = str(config.get("webdav_pass") or "")
            self._webdav_dir = str(config.get("webdav_dir") or "/MoviePilot").strip()
            self._webdav_keep = config.get("webdav_keep")
            self._webdav_timeout = self.__safe_int(config.get("webdav_timeout"), 300, 10, 7200)
            self._webdav_allow_http = bool(config.get("webdav_allow_http"))
            self._encrypt_pass = str(config.get("encrypt_pass") or "")
            self._notify = bool(config.get("notify"))
            self._notify_type = str(config.get("notify_type") or "插件")
            self._onlyonce = bool(config.get("onlyonce"))

        if self._config_warnings:
            logger.warning("配置存在非法值，已回退默认：" + "；".join(self._config_warnings))

        if self._onlyonce:
            self._onlyonce = False
            self.update_config({
                "enabled": self._enabled,
                "cron": self._cron,
                "backup_dir": self._backup_dir,
                "keep_count": self._keep_count,
                "keep_days": self._keep_days,
                "backup_parts": list(self._backup_parts),
                "extra_paths": self._extra_paths,
                "webdav_enabled": self._webdav_enabled,
                "webdav_url": self._webdav_url,
                "webdav_user": self._webdav_user,
                "webdav_pass": self._webdav_pass,
                "webdav_dir": self._webdav_dir,
                "webdav_keep": self._webdav_keep,
                "webdav_timeout": self._webdav_timeout,
                "webdav_allow_http": self._webdav_allow_http,
                "encrypt_pass": self._encrypt_pass,
                "notify": self._notify,
                "notify_type": self._notify_type,
                "onlyonce": False,
            })
            # 后台执行：备份目录在网盘、插件配置多时可能耗时几十秒，
            # 同步跑会卡住「保存配置」请求、触发网关超时。
            threading.Thread(
                target=self.__backup,
                name="ConfigBackup-Once",
                daemon=True,
            ).start()

    @staticmethod
    def __safe_int(value: Any, default: int, minimum: int, maximum: int) -> int:
        """
        解析整数配置：非法或越界时回退默认值。

        :param value: 原始配置值
        :param default: 默认值
        :param minimum: 允许的最小值
        :param maximum: 允许的最大值
        :return: 可安全使用的整数
        """
        try:
            parsed = int(str(value).strip())
        except (TypeError, ValueError):
            return default
        if parsed < minimum or parsed > maximum:
            return default
        return parsed

    def __sanitize_dir(self, value: Any, default: str, label: str) -> str:
        """
        校验目录配置：必须是绝对路径，且不能是根目录或系统伪文件系统。

        :param value: 原始配置值
        :param default: 默认目录
        :param label: 配置项名称（告警文案用）
        :return: 规范化后的绝对路径
        """
        text = str(value or "").strip()
        if not text:
            return default
        normalized = posixpath.normpath(text)
        forbidden = ("/", "/proc", "/sys", "/dev", "/run")
        if not normalized.startswith("/") or normalized in forbidden \
                or normalized.startswith(("/proc/", "/sys/", "/dev/", "/run/")):
            self._config_warnings.append(f"{label}非法：{text}，已回退 {default}")
            return default
        return normalized

    def __sanitize_extra_paths(self, value: Any) -> str:
        """
        逐行校验附加备份路径，丢弃相对路径、根目录与系统伪文件系统。

        :param value: 原始多行配置
        :return: 清洗后的多行配置（非法行被移除）
        """
        lines: List[str] = []
        for raw in str(value or "").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            path_part, sep, exclude_part = raw.partition("|")
            path_text = path_part.strip()
            normalized = posixpath.normpath(path_text)
            forbidden = ("/", "/proc", "/sys", "/dev", "/run")
            if not normalized.startswith("/") or normalized in forbidden \
                    or normalized.startswith(("/proc/", "/sys/", "/dev/", "/run/")):
                self._config_warnings.append(f"附加路径非法，已忽略：{path_text}")
                continue
            lines.append(f"{normalized}{sep}{exclude_part}" if sep else normalized)
        return "\n".join(lines)


    @classmethod
    def __parse_parts(cls, config: Dict[str, Any]) -> Tuple[str, ...]:
        """
        解析「备份内容」配置，并与老配置对齐。

        - 未配置 ``backup_parts``（老版本升级上来的）→ 由老开关 ``backup_plugins`` 推导：
          关掉过「备份插件配置」的，迁移后就不含 ``plugins``，行为与升级前一致。
        - 配置了但一项都没勾 → 返回空元组，调用方据此拒绝备份（不打空包）。
        - 未知取值一律丢弃，并按 ``_ALL_PARTS`` 的固定顺序归位。

        :param config: 插件配置
        :return: 勾选的部分（按固定顺序）
        """
        raw = config.get("backup_parts")
        if raw is None:
            if bool(config.get("backup_plugins", True)):
                return tuple(cls._ALL_PARTS)
            return tuple(p for p in cls._ALL_PARTS if p != cls._PART_PLUGINS)
        if isinstance(raw, str):
            raw = re.split(r"[,\s]+", raw)
        selected = set(raw or [])
        return tuple(p for p in cls._ALL_PARTS if p in selected)

    def get_state(self) -> bool:
        """获取插件启用状态。"""
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """返回插件远程命令列表。"""
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        """返回插件 API 列表。"""
        return [
            {
                "path": "/backup",
                "endpoint": self.api_backup,
                "methods": ["GET"],
                "summary": "手动触发配置备份",
                "description": "立即执行一次配置备份",
            },
            {
                "path": "/list",
                "endpoint": self.api_list,
                "methods": ["GET"],
                "summary": "获取备份文件列表",
                "description": "获取备份目录下所有备份文件",
            },
            {
                "path": "/delete",
                "endpoint": self.api_delete,
                "methods": ["GET"],
                "summary": "删除指定备份文件",
                "description": "按文件名删除备份文件",
            },
            {
                "path": "/restore",
                "endpoint": self.api_restore,
                "methods": ["GET"],
                "summary": "还原配置备份",
                "description": "选择备份文件后确认还原；filename 用于选择待还原文件，confirm=1 执行还原，confirm=cancel 取消",
            },
            {
                "path": "/webdav/test",
                "endpoint": self.api_webdav_test,
                "methods": ["GET"],
                "summary": "测试 WebDAV 连接",
                "description": "按当前配置对远端目录做一次只读探测（Depth:0 的 PROPFIND），不写入任何文件",
            },
            {
                "path": "/webdav/list",
                "endpoint": self.api_webdav_list,
                "methods": ["GET"],
                "summary": "获取远端备份列表",
                "description": "列出 WebDAV 远端目录中的备份包",
            },
            {
                "path": "/webdav/download",
                "endpoint": self.api_webdav_download,
                "methods": ["GET"],
                "summary": "从远端下载备份",
                "description": "把远端备份包下载到本地备份目录（下载后校验完整性）",
            },
            {
                "path": "/webdav/restore",
                "endpoint": self.api_webdav_restore,
                "methods": ["GET"],
                "summary": "下载并还原远端备份",
                "description": "先把远端包下载到本地，再走与本地还原相同的两阶段确认（confirm=1 执行 / confirm=cancel 取消）",
            },
        ]

    def get_service(self) -> List[Dict[str, Any]]:
        """注册插件公共服务（cron 非法时跳过定时任务，不影响插件加载）。"""
        if self._enabled and self._cron:
            try:
                trigger = CronTrigger.from_crontab(self._cron)
            except Exception as e:
                logger.error(f"定时表达式非法，已跳过定时备份：{self._cron}（{e}）")
                return []
            return [{
                "id": "ConfigBackup",
                "name": "配置备份定时服务",
                "trigger": trigger,
                "func": self.__backup,
                "kwargs": {}
            }]
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """拼装插件配置页面，返回页面配置与数据结构。"""
        # 动态生成备份目录候选列表（下拉可选，也可手动输入其它目录）
        dir_items: List[Dict[str, str]] = [
            {"title": "/config/backup（默认）", "value": "/config/backup"}
        ]
        try:
            directory_helper = DirectoryHelper()
            for item in directory_helper.get_download_dirs():
                if item.download_path and not any(i["value"] == item.download_path for i in dir_items):
                    dir_items.append({"title": f"下载目录：{item.download_path}", "value": item.download_path})
            for item in directory_helper.get_library_dirs():
                if item.library_path and not any(i["value"] == item.library_path for i in dir_items):
                    dir_items.append({"title": f"媒体库目录：{item.library_path}", "value": item.library_path})
        except Exception as e:
            logger.debug(f"获取系统目录失败: {e}")
        # 已保存的备份目录若不在候选列表中，追加进去避免丢失
        if self._backup_dir and not any(i["value"] == self._backup_dir for i in dir_items):
            dir_items.append({"title": f"自定义：{self._backup_dir}", "value": self._backup_dir})
        # 通知场景候选（MessageType 是「场景」不是渠道，渠道由宿主按场景路由）
        notify_items: List[Dict[str, str]] = [
            {"title": m.value, "value": m.value} for m in MessageType
        ]

        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "info",
                                            "variant": "tonal",
                                            "text": "使用说明：定时备份 MoviePilot 系统配置、数据库、站点 Cookie 与插件配置，"
                                                    "支持按个数与天数保留、自动清理与一键还原（还原前自动先做一份本地安全备份）。"
                                                    "备份内容可在下方「备份内容」中勾选，默认全选即全量。"
                                                    "数据库：PostgreSQL 用 pg_dump 导出完整结构与数据（含主键、唯一约束、外键与索引），"
                                                    "还原走单事务，失败自动回滚；SQLite 以数据库快照（user.db）方式备份并归入「数据库」项。"
                                                    "还原为覆盖式：包内出现的配置会被替换成备份那一刻的内容；"
                                                    "包内没有的顶层项不会被删除。还原期间请勿操作 MoviePilot，完成后需重启使其完全生效。"
                                                    "备份包内含 API Token、站点 Cookie 与数据库，请设置「备份加密口令」后再上传网盘。",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "enabled",
                                            "label": "启用插件",
                                        }
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "onlyonce",
                                            "label": "立即运行一次",
                                            "hint": "保存配置后立即执行一次备份（在后台执行，不阻塞保存）",
                                            "persistent-hint": True,
                                        }
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "notify",
                                            "label": "发送通知",
                                            "hint": "备份成功与失败都会通知",
                                            "persistent-hint": True,
                                        }
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "notify_type",
                                            "label": "通知场景",
                                            "items": notify_items,
                                            "hint": "具体发到微信还是 Telegram，由宿主的「通知设置」按该场景配置决定",
                                            "persistent-hint": True,
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 12},
                                "content": [
                                    {
                                        "component": "VCronField",
                                        "props": {
                                            "model": "cron",
                                            "label": "定时触发",
                                            "placeholder": "5 位 cron 表达式，如 0 3 * * *（每天 3 点）",
                                        }
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "keep_count",
                                            "label": "保留备份个数",
                                            "type": "number",
                                            "min": "1",
                                            "hint": "超过该数量的最旧备份将自动删除；与「保留天数」满足其一即保留",
                                            "persistent-hint": True,
                                        }
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "keep_days",
                                            "label": "保留天数",
                                            "type": "number",
                                            "min": "0",
                                            "hint": "该天数内的备份一律不删；填 0 表示只看个数",
                                            "persistent-hint": True,
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VCombobox",
                                        "props": {
                                            "model": "backup_dir",
                                            "label": "备份目录",
                                            "items": dir_items,
                                            "placeholder": "/config/backup",
                                            "hint": "可从下拉选择常用目录，也可直接输入其它目录路径（默认 /config/backup）",
                                            "persistent-hint": True,
                                        }
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "backup_parts",
                                            "label": "备份内容",
                                            "multiple": True,
                                            "chips": True,
                                            "items": [
                                                {"title": self._PART_LABELS[p], "value": p}
                                                for p in self._ALL_PARTS
                                            ],
                                            "hint": "默认全选（= 全量备份）；取消勾选即只备份所选部分，"
                                                    "一项都不选会拒绝备份（不打空包）。"
                                                    "「数据库」= PostgreSQL 的 SQL 导出，或 SQLite 的 user.db 文件；"
                                                    "「系统配置」= app.env 与 category.yaml；"
                                                    "「站点 Cookie」= /config/cookies。",
                                            "persistent-hint": True,
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VTextarea",
                                        "props": {
                                            "model": "extra_paths",
                                            "label": "附加备份路径",
                                            "rows": 3,
                                            "placeholder": "每行一个文件或目录路径，将一并复制进备份包",
                                            "hint": "目录可写 \"路径|*.log,node_modules,cache\" 排除不需要的内容（逗号分隔）；"
                                                    "还原时只接受当前配置里列出的路径，包内其它路径会被跳过",
                                            "persistent-hint": True,
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "encrypt_pass",
                                            "label": "备份加密口令",
                                            "type": "password",
                                            "hint": "设置后新备份为 AES-256-GCM 加密包（.zip.enc）；留空则明文。"
                                                    "备份包含 API Token、站点 Cookie 与数据库，强烈建议设置；"
                                                    "启用 WebDAV 上传时必须设置",
                                            "persistent-hint": True,
                                        }
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "warning",
                                            "variant": "tonal",
                                            "text": "加密口令不会写入备份包。口令丢失后已加密的备份无法还原，请另行妥善保存。",
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "webdav_enabled",
                                            "label": "上传到 WebDAV",
                                            "hint": "包自检通过后上传；上传失败整次备份标红，但本地包仍生成且可用",
                                            "persistent-hint": True,
                                        }
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 9},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "webdav_url",
                                            "label": "WebDAV 地址",
                                            "placeholder": "https://dav.jianguoyun.com/dav/MoviePilot",
                                            "hint": "含目录的完整地址；保存后可到插件详情页点「测试 WebDAV 连接」",
                                            "persistent-hint": True,
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "webdav_user",
                                            "label": "用户名",
                                            "persistent-hint": True,
                                        }
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "webdav_pass",
                                            "label": "密码 / 应用密码",
                                            "type": "password",
                                            "hint": "建议用网盘的应用专用密码，不要用登录密码",
                                            "persistent-hint": True,
                                        }
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "webdav_dir",
                                            "label": "远端目录",
                                            "placeholder": "/MoviePilot",
                                            "hint": "存放备份包的远端目录，不存在会自动逐级创建",
                                            "persistent-hint": True,
                                        }
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "webdav_keep",
                                            "label": "远端保留份数",
                                            "type": "number",
                                            "min": "0",
                                            "hint": "留空则跟随「保留备份个数」；与「保留天数」满足其一即保留",
                                            "persistent-hint": True,
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "webdav_timeout",
                                            "label": "WebDAV 超时（秒）",
                                            "type": "number",
                                            "min": "10",
                                            "hint": "大数据库包上传/下载慢，建议 ≥300",
                                            "persistent-hint": True,
                                        }
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "webdav_allow_http",
                                            "label": "允许明文 HTTP",
                                            "hint": "默认只允许 HTTPS；仅内网自建网盘才可开启（Basic 密码会明文传输）",
                                            "persistent-hint": True,
                                        }
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "warning",
                                            "variant": "tonal",
                                            "text": "上传要求先设置「备份加密口令」：远端只传加密包，避免凭据随明文备份扩散。",
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "cron": "",
            "backup_dir": "/config/backup",
            "keep_count": 10,
            "keep_days": 7,
            "backup_parts": list(self._ALL_PARTS),
            "extra_paths": "",
            "encrypt_pass": "",
            "webdav_enabled": False,
            "webdav_url": "",
            "webdav_user": "",
            "webdav_pass": "",
            "webdav_dir": "/MoviePilot",
            "webdav_keep": "",
            "webdav_timeout": 300,
            "webdav_allow_http": False,
            "notify": False,
            "notify_type": "插件",
            "onlyonce": False,
        }

    def get_page(self) -> List[dict]:
        """返回插件详情页面：手动触发、还原与备份文件列表。"""
        if not self._enabled:
            return [
                {
                    "component": "VAlert",
                    "props": {"type": "info", "text": "插件未启用，请先在设置中启用并保存配置。"},
                }
            ]

        # 备份文件列表
        bk_path = Path(self._backup_dir) if self._backup_dir else self.get_data_path()
        backup_files = self.__list_backups(bk_path)
        # 当前待确认还原的备份
        pending = self.__get_pending_restore()
        # 当前待确认删除的备份
        pending_del = self.__get_pending_delete()

        # 顶部操作按钮
        actions = [
            {
                "component": "VBtn",
                "props": {
                    "color": "primary",
                    "variant": "flat",
                    "size": "small",
                    "class": "mr-2",
                },
                "text": "立即备份",
                "events": {
                    "click": {
                        "api": "plugin/ConfigBackup/backup",
                        "method": "get",
                        "params": {"apikey": settings.API_TOKEN},
                    }
                },
            },
        ]
        # 「确认还原」仅在已选中待还原备份时出现：
        # 未选中时该按钮点了必然失败（后端返回「没有待还原的备份」），
        # 因此不再无条件展示，改为由列表中的【还原】按钮先选中、再确认的两阶段交互。
        if pending:
            actions.append(
                {
                    "component": "VBtn",
                    "props": {
                        "color": "error",
                        "variant": "flat",
                        "size": "small",
                        "prependIcon": "mdi-restore",
                    },
                    "text": "确认还原",
                    "events": {
                        "click": {
                            "api": "plugin/ConfigBackup/restore",
                            "method": "get",
                            "params": {
                                "apikey": settings.API_TOKEN,
                                "confirm": "1",
                            },
                        },
                    },
                }
            )
            actions.append(
                {
                    "component": "VBtn",
                    "props": {
                        "color": "grey",
                        "variant": "tonal",
                        "size": "small",
                        "class": "ml-2",
                        "prependIcon": "mdi-close",
                    },
                    "text": "取消还原",
                    "events": {
                        "click": {
                            "api": "plugin/ConfigBackup/restore",
                            "method": "get",
                            "params": {
                                "apikey": settings.API_TOKEN,
                                "confirm": "cancel",
                            },
                        }
                    },
                }
            )

        # 「确认删除」仅在已选中待删除备份时出现：
        # 与还原一致采用两阶段交互，避免列表里误点【删除】直接抹掉备份。
        if pending_del:
            actions.append(
                {
                    "component": "VBtn",
                    "props": {
                        "color": "error",
                        "variant": "flat",
                        "size": "small",
                        "class": "ml-2",
                        "prependIcon": "mdi-delete-forever",
                    },
                    "text": "确认删除",
                    "events": {
                        "click": {
                            "api": "plugin/ConfigBackup/delete",
                            "method": "get",
                            "params": {
                                "apikey": settings.API_TOKEN,
                                "confirm": "1",
                            },
                        },
                    },
                }
            )
            actions.append(
                {
                    "component": "VBtn",
                    "props": {
                        "color": "grey",
                        "variant": "tonal",
                        "size": "small",
                        "class": "ml-2",
                        "prependIcon": "mdi-close",
                    },
                    "text": "取消删除",
                    "events": {
                        "click": {
                            "api": "plugin/ConfigBackup/delete",
                            "method": "get",
                            "params": {
                                "apikey": settings.API_TOKEN,
                                "confirm": "cancel",
                            },
                        }
                    },
                }
            )

        header = [
            {
                "component": "div",
                "props": {"class": "d-flex align-center flex-wrap"},
                "content": actions,
            },
            {
                "component": "p",
                "props": {"class": "text-subtitle-2 mt-2"},
                "text": f"备份目录：{bk_path}（保留 {self._keep_count} 份）",
            },
            {
                "component": "div",
                "props": {"class": "d-flex align-center flex-wrap mt-2"},
                "content": [
                    {
                        "component": "VBtn",
                        "props": {
                            "color": "primary",
                            "variant": "tonal",
                            "size": "small",
                            "prependIcon": "mdi-cloud-check",
                            "title": "对配置的 WebDAV 地址做一次只读探测，不写入任何文件",
                        },
                        "text": "测试 WebDAV 连接",
                        "events": {
                            "click": {
                                "api": "plugin/ConfigBackup/webdav/test",
                                "method": "get",
                                "params": {"apikey": settings.API_TOKEN},
                            }
                        },
                    },
                    {
                        "component": "span",
                        "props": {"class": "text-caption ml-2"},
                        "text": self.__webdav_status_text(),
                    },
                ],
            },
        ]

        # 待还原提示
        if pending:
            header.append(
                {
                    "component": "VAlert",
                    "props": {
                        "type": "warning",
                        "variant": "tonal",
                        "class": "mt-2",
                        "text": f"已选中待还原备份：{pending.get('filename', '')}"
                                f"（备份于 {pending.get('time', '')}，含 {pending.get('summary', '未知内容')}）。"
                                f"请点击上方【确认还原】执行（还原为覆盖式：备份包中含有的配置会回到备份那一刻，"
                                f"备份后新增的插件配置与站点 Cookie 将被清除；还原前会自动先备份当前状态作为安全网），"
                                f"或点击【取消还原】放弃本次操作。",
                    },
                }
            )
        else:
            header.append(
                {
                    "component": "VAlert",
                    "props": {
                        "type": "info",
                        "variant": "tonal",
                        "class": "mt-2",
                        "text": "还原操作分两步：先在下表点击目标备份行的【还原】按钮选中它，"
                                "然后点击上方出现的【确认还原】执行。",
                    },
                }
            )

        # 待删除提示
        if pending_del:
            header.append(
                {
                    "component": "VAlert",
                    "props": {
                        "type": "error",
                        "variant": "tonal",
                        "class": "mt-2",
                        "text": f"已选中待删除备份：{pending_del.get('filename', '')}。"
                                f"该文件将被永久删除且不可恢复，"
                                f"请点击上方【确认删除】执行，或点击【取消删除】放弃本次操作。",
                    },
                }
            )

        # 远端备份区（v3.2.0）：远端不可达时退化成一条告警，绝不因此让详情页打不开。
        # ⚠️ 必须在下面的 "本地列表为空" 早返回**之前**算好 —— 否则本地一个包都没有时
        # 远端列表会被整段吞掉（"本地空、远端有"恰恰是最需要看远的场景）。
        remote_blocks: List[dict] = self.__render_remote_section() if self._webdav_enabled else []

        if not backup_files:
            return [
                {
                    "component": "div",
                    "props": {"class": "pa-4"},
                    "content": header + remote_blocks + [
                        {
                            "component": "p",
                            "props": {"class": "text-center mt-4"},
                            "text": "暂无备份文件",
                        }
                    ],
                }
            ]

        # 备份文件表格
        rows = []
        for item in backup_files:
            name = item["name"]
            rows.append(
                {
                    "component": "tr",
                    "content": [
                        {
                            "component": "td",
                            "props": {"class": "text-truncate"},
                            "content": [
                                {
                                    "component": "div",
                                    "props": {"class": "d-flex align-center"},
                                    "content": [
                                        {
                                            "component": "p",
                                            "props": {"class": "mb-0"},
                                            "text": name,
                                        },
                                    ],
                                }
                            ],
                        },
                        {
                            "component": "td",
                            "content": [
                                {
                                    "component": "p",
                                    "props": {"class": "mb-0"},
                                    "text": StringUtils.str_filesize(item["size"]),
                                }
                            ],
                        },
                        {
                            "component": "td",
                            "content": [
                                {
                                    "component": "p",
                                    "props": {"class": "mb-0 text-caption"},
                                    "text": item.get("summary", ""),
                                }
                            ],
                        },
                        {
                            "component": "td",
                            "content": [
                                {
                                    "component": "p",
                                    "props": {"class": "mb-0"},
                                    "text": item["time"],
                                }
                            ],
                        },
                        {
                            "component": "td",
                            "props": {"class": "text-right", "style": "white-space: nowrap;"},
                            "content": [
                                {
                                    "component": "VBtn",
                                    "props": {
                                        "color": "primary",
                                        "variant": "text",
                                        "size": "x-small",
                                        "prependIcon": "mdi-restore",
                                        "title": "选择该备份，然后在顶部点【确认还原】执行",
                                    },
                                    "text": "还原",
                                    "events": {
                                        "click": {
                                            "api": "plugin/ConfigBackup/restore",
                                            "method": "get",
                                            "params": {
                                                "apikey": settings.API_TOKEN,
                                                "filename": name,
                                            },
                                        }
                                    },
                                },
                                {
                                    "component": "VBtn",
                                    "props": {
                                        "color": "error",
                                        "variant": "text",
                                        "size": "x-small",
                                        "prependIcon": "mdi-delete",
                                        "title": "选择该备份，然后在顶部点【确认删除】执行",
                                        "class": "ml-2",
                                    },
                                    "text": "删除",
                                    "events": {
                                        "click": {
                                            "api": "plugin/ConfigBackup/delete",
                                            "method": "get",
                                            "params": {
                                                "apikey": settings.API_TOKEN,
                                                "filename": name,
                                            },
                                        }
                                    },
                                },
                            ],
                        },
                    ],
                }
            )

        return [
            {
                "component": "div",
                "props": {"class": "pa-4"},
                "content": header
                + remote_blocks
                + [
                    {
                        "component": "VTable",
                        "props": {"density": "compact", "hover": True},
                        "content": [
                            {
                                "component": "thead",
                                "content": [
                                    {
                                        "component": "tr",
                                        "content": [
                                            {"component": "th", "props": {"class": "text-left"}, "text": "备份文件"},
                                            {"component": "th", "props": {"class": "text-left"}, "text": "大小"},
                                            {"component": "th", "props": {"class": "text-left"}, "text": "内容"},
                                            {"component": "th", "props": {"class": "text-left"}, "text": "创建时间"},
                                            {"component": "th", "props": {"class": "text-right"}, "text": "操作"},
                                        ],
                                    }
                                ],
                            },
                            {"component": "tbody", "content": rows},
                        ],
                    }
                ],
            }
        ]

    def stop_service(self):
        """停止插件后台服务并释放资源。"""
        return None

    def api_webdav_test(self) -> Dict[str, Any]:
        """API：测试 WebDAV 连接（只读探测，不在远端留任何东西）。"""
        if not self._webdav_url:
            return {"success": False, "message": "未配置 WebDAV 地址", "data": None}
        ok, msg = self.__webdav_client().test()
        return {"success": ok, "message": msg, "data": None}

    def api_webdav_list(self) -> Dict[str, Any]:
        """API：列出远端备份包。"""
        try:
            items = self.__list_remote_backups()
        except WebDAVError as e:
            return {"success": False, "message": str(e), "data": []}
        return {"success": True, "message": "获取成功", "data": items}

    def api_webdav_download(self, filename: str = "") -> Dict[str, Any]:
        """API：把远端备份包下载到本地备份目录。"""
        if not filename:
            return {"success": False, "message": "缺少文件名参数", "data": None}
        try:
            path = self.__download_remote_backup(filename)
        except WebDAVError as e:
            return {"success": False, "message": str(e), "data": None}
        return {"success": True, "message": f"已下载到本地备份目录：{path.name}", "data": None}

    def api_webdav_restore(self, filename: str = "", confirm: str = "") -> Dict[str, Any]:
        """
        API：下载并还原远端备份（复用本地还原的两阶段确认与安全网）。

        - filename 且无 confirm：下载到本地 → 选中为待还原
        - confirm=1：执行还原（与本地【确认还原】走完全同一条链路）
        - confirm=cancel：取消待确认还原
        """
        if confirm:
            # 直接复用本地还原的执行/取消分支，保证安全网与锁行为完全一致
            return self.api_restore(filename="", confirm=confirm)
        if not filename:
            return {"success": False, "message": "缺少文件名参数", "data": None}
        try:
            path = self.__download_remote_backup(filename)
        except WebDAVError as e:
            return {"success": False, "message": str(e), "data": None}
        result = self.api_restore(filename=path.name, source="remote")
        if result.get("success"):
            result["message"] = f"已下载并选中：{path.name}。{result.get('message', '')}"
        return result

    def api_backup(self) -> Dict[str, Any]:
        """API：手动触发配置备份（还原进行中一律拒绝）。"""
        if self._restoring:
            return {"success": False, "message": "还原正在进行，暂不允许备份", "data": None}
        success, msg = self.__backup()
        return {"success": success, "message": msg, "data": None}

    def api_list(self) -> Dict[str, Any]:
        """API：获取备份文件列表。"""
        return {"success": True, "message": "获取成功",
                "data": self.__list_backups(self.__backup_dir_path())}

    def api_delete(self, filename: str = "", confirm: str = "") -> Dict[str, Any]:
        """
        API：删除备份文件（两阶段确认）。

        - filename 非空且 confirm 为空：选中待删除备份（写入待确认状态）
        - confirm=1：执行删除
        - confirm=cancel：取消待确认删除
        """
        # 取消删除
        if confirm == "cancel":
            self.__set_pending_delete(None)
            return {"success": True, "message": "已取消删除操作", "data": None}

        if self._restoring:
            return {"success": False, "message": "还原正在进行，暂不允许删除备份", "data": None}

        # 执行删除
        if confirm == "1":
            pending = self.__get_pending_delete()
            if not pending or not pending.get("filename"):
                return {"success": False, "message": "没有待删除的备份，请先在列表中选择备份文件", "data": None}
            safe_name = pending["filename"]
            target = self.__resolve_backup_path(safe_name)
            if not target or not target.exists():
                self.__set_pending_delete(None)
                return {"success": False, "message": f"待删除的备份文件不存在：{safe_name}", "data": None}
            try:
                if target.is_file():
                    target.unlink()
                elif target.is_dir():
                    shutil.rmtree(target)
                self.__set_pending_delete(None)
                logger.info(f"删除备份文件 {target} 成功")
                return {"success": True, "message": f"删除备份 {safe_name} 成功", "data": None}
            except Exception as e:
                self.__set_pending_delete(None)
                logger.error(f"删除备份文件 {target} 失败: {e}")
                return {"success": False, "message": f"删除失败: {e}", "data": None}

        # 选择待删除备份
        if not filename:
            return {"success": False, "message": "缺少文件名参数", "data": None}
        # 防止路径穿越：与 __resolve_backup_path 保持同一套命名约定
        # （bk_ 前缀 + .zip / .zip.enc 后缀），避免出现"删除比还原更宽松"的口子。
        safe_name = Path(filename).name
        if safe_name != filename or not self.__is_backup_name(safe_name):
            return {"success": False, "message": "非法文件名", "data": None}
        bk_path = self.__backup_dir_path()
        target = bk_path / safe_name
        if not target.exists():
            return {"success": False, "message": "备份文件不存在", "data": None}
        # 选中删除时，清掉待还原状态，避免两个确认按钮同时出现造成误操作
        self.__set_pending_restore(None)
        self.__set_pending_delete({"filename": safe_name})
        logger.info(f"已选择待删除备份 {safe_name}")
        return {
            "success": True,
            "message": f"已选择备份 {safe_name}，请点击页面顶部的【确认删除】按钮执行删除",
            "data": None,
        }

    def api_restore(self, filename: str = "", confirm: str = "",
                    source: str = "local") -> Dict[str, Any]:
        """
        API：配置还原（两阶段确认）。

        - filename 为空且 confirm 为空：选中待还原备份（写入待确认状态）
        - confirm=1：执行还原
        - confirm=cancel：取消待确认还原
        - source=remote：标记该包来自远端下载（未加密时拒绝执行还原）
        """
        try:
            # 取消还原
            if confirm == "cancel":
                self.__set_pending_restore(None)
                return {"success": True, "message": "已取消还原操作", "data": None}

            # 执行还原
            if confirm == "1":
                if not self._restore_lock.acquire(blocking=False):
                    return {"success": False, "message": "已有还原操作正在进行，请稍后再试", "data": None}
                try:
                    pending = self.__get_pending_restore()
                    if not pending or not pending.get("filename"):
                        return {"success": False, "message": "没有待还原的备份，请先在列表中选择备份文件", "data": None}
                    zip_path = self.__resolve_backup_path(pending["filename"])
                    if not zip_path or not zip_path.exists():
                        self.__set_pending_restore(None)
                        return {"success": False, "message": f"待还原的备份文件不存在：{pending['filename']}", "data": None}
                    # 远端下载的包可能被第三方替换：未加密时无法验证来源，一律拒绝还原
                    if pending.get("source") == "remote" and not zip_path.name.endswith(".enc"):
                        self.__set_pending_restore(None)
                        return {
                            "success": False,
                            "message": "该包从远端下载且未加密，无法验证来源，已拒绝还原。"
                                       "请先设置「备份加密口令」并让远端只保存加密包。",
                            "data": None,
                        }
                    # 还原前自动备份当前状态（安全网）：只做本地、不发通知、不清理待还原包
                    bk_ok, bk_msg = self.__backup(
                        notify=False, local_only=True, protect={zip_path.name}
                    )
                    if not bk_ok:
                        # 完全还原会先删后写，安全网没兜住就不许动——
                        # 否则一次失败的安全网 + 一次失败的还原 = 什么都没了。
                        self.__set_pending_restore(None)
                        return {
                            "success": False,
                            "message": f"还原前安全备份失败，已中止还原（未改动任何配置）：{bk_msg}",
                            "data": None,
                        }
                    # 执行还原（期间拒绝并发的备份/删除/远端操作）
                    self._restoring = True
                    try:
                        ok, msg = self.__restore(zip_path)
                    finally:
                        self._restoring = False
                    # 无论成败都清除待确认状态
                    self.__set_pending_restore(None)
                    if ok and self._notify:
                        self.post_message(
                            mtype=self.__notify_mtype(),
                            title="【配置还原完成】",
                            text=msg,
                        )
                    full_msg = f"还原前已自动备份当前状态（{bk_msg}）。{msg}" if bk_ok else msg
                    return {"success": ok, "message": full_msg, "data": None}
                finally:
                    self._restore_lock.release()

            # 选择待还原备份
            if not filename:
                return {"success": False, "message": "缺少文件名参数", "data": None}
            safe_name = Path(filename).name
            if safe_name != filename or not self.__is_backup_name(safe_name):
                return {"success": False, "message": "非法文件名", "data": None}
            zip_path = self.__resolve_backup_path(safe_name)
            if not zip_path or not zip_path.exists():
                return {"success": False, "message": "备份文件不存在", "data": None}
            # 校验备份包完整性：加密包会先做口令解密与 GCM 认证，失败即拒绝
            if not self.__verify_backup(zip_path):
                return {
                    "success": False,
                    "message": "备份文件校验失败（可能已损坏、口令错误或包被篡改）",
                    "data": None,
                }
            ctime = datetime.fromtimestamp(os.path.getctime(zip_path)).strftime("%Y-%m-%d %H:%M:%S")
            # 选中还原时，清掉待删除状态，避免两个确认按钮同时出现造成误操作
            self.__set_pending_delete(None)
            self.__set_pending_restore({
                "filename": safe_name,
                "time": ctime,
                "size": os.path.getsize(zip_path),
                "summary": self.__summarize(str(zip_path)),
                "source": "remote" if source == "remote" else "local",
            })
            logger.info(f"已选择待还原备份 {safe_name}")
            return {"success": True, "message": f"已选择备份 {safe_name}，请点击页面顶部的【确认还原】按钮执行还原", "data": None}
        except Exception as e:
            logger.error(f"还原操作失败: {e}")
            return {"success": False, "message": f"还原操作失败: {e}", "data": None}

    @classmethod
    def __is_backup_name(cls, name: str) -> bool:
        """
        判断是否为插件生成的备份文件名（明文 zip 或加密 zip.enc）。

        :param name: 文件名
        :return: 是否合法
        """
        return bool(_BACKUP_NAME_RE.fullmatch(str(name or "")))

    @staticmethod
    def __name_stamp(name: str) -> Optional[float]:
        """
        从备份文件名解析时间戳（兼容紧凑格式与历史下划线格式）。

        :param name: 文件名
        :return: 时间戳（秒）；解析不出返回 None
        """
        matched = _BACKUP_NAME_RE.fullmatch(str(name or ""))
        if not matched:
            return None
        stamp = matched.group(1)
        for fmt in ("%Y%m%d%H%M%S", "%Y%m%d_%H%M%S"):
            try:
                return datetime.strptime(stamp, fmt).timestamp()
            except ValueError:
                continue
        return None

    def __backup_dir_path(self) -> Path:
        """当前备份目录（未配置时使用插件数据目录）。"""
        return Path(self._backup_dir) if self._backup_dir else self.get_data_path()

    def __iter_local_backups(self, bk_path: Path) -> List[Path]:
        """
        列出备份目录下的备份文件（明文与加密两种），按时间升序。

        :param bk_path: 备份目录
        :return: 备份文件路径列表
        """
        if not bk_path.exists():
            return []
        found: List[Path] = []
        for pattern in ("bk_*.zip", "bk_*.zip.enc"):
            for raw in glob.glob(str(bk_path / pattern)):
                path = Path(raw)
                if self.__is_backup_name(path.name):
                    found.append(path)
        return sorted(found, key=lambda p: self.__backup_time(str(p)))

    def __resolve_backup_path(self, filename: str) -> Optional[Path]:
        """
        将备份文件名解析为备份目录下的绝对路径（防路径穿越）。

        :param filename: 备份文件名
        :return: 绝对路径或 None
        """
        safe_name = Path(filename).name
        if safe_name != filename or not self.__is_backup_name(safe_name):
            return None
        return self.__backup_dir_path() / safe_name

    def __backup_password(self) -> str:
        """当前加密口令（空串表示不加密）。"""
        return str(self._encrypt_pass or "")

    @staticmethod
    def __derive_key(password: str, salt: bytes) -> bytes:
        """
        由口令与盐派生 32 字节 AES 密钥（PBKDF2-HMAC-SHA256）。

        :param password: 用户口令
        :param salt: 随机盐
        :return: 32 字节密钥
        """
        return hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, _ENC_KDF_ROUNDS, dklen=32
        )

    def __encrypt_backup(self, zip_path: Path, manifest: Dict[str, Any]) -> Path:
        """
        把明文 zip 加密为 ``.zip.enc``（AES-256-GCM 流式认证加密）。

        包头保留一份不含敏感内容的清单，供列表展示摘要；包头同时作为 AAD 参与认证，
        任何篡改都会导致解密失败。

        :param zip_path: 明文备份包路径
        :param manifest: 备份清单
        :return: 加密后的路径
        """
        password = self.__backup_password()
        if not password:
            return zip_path
        if Cipher is None:
            raise RuntimeError("当前环境缺少 cryptography，无法加密备份；请留空口令或安装依赖")
        salt = os.urandom(_ENC_SALT_LEN)
        nonce = os.urandom(_ENC_NONCE_LEN)
        key = self.__derive_key(password, salt)
        header = {
            "plugin": manifest.get("plugin") or self.plugin_name,
            "version": manifest.get("version") or VERSION,
            "created": manifest.get("created") or time.strftime("%Y-%m-%d %H:%M:%S"),
            "db_type": manifest.get("db_type") or "",
            "parts": manifest.get("parts") or [],
            "database": bool(manifest.get("database")),
            "plugins": bool(manifest.get("plugins")),
            "extra_count": int(manifest.get("extra_count") or 0),
            "files": int(manifest.get("files") or 0),
            "schema": manifest.get("schema") or {},
            "encrypted": True,
        }
        header_bytes = json.dumps(header, ensure_ascii=False, sort_keys=True).encode("utf-8")
        prefix = (
            _ENC_MAGIC + b"\x01" + salt + nonce + len(header_bytes).to_bytes(4, "big")
            + header_bytes
        )
        target = zip_path.with_name(zip_path.name + ".enc")
        part = target.with_name(target.name + ".part")
        encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
        encryptor.authenticate_additional_data(prefix)
        try:
            with zip_path.open("rb") as src, part.open("wb") as dst:
                dst.write(prefix)
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    dst.write(encryptor.update(chunk))
                dst.write(encryptor.finalize())
                dst.write(encryptor.tag)
            os.chmod(part, 0o600)
            os.replace(part, target)
        except Exception:
            try:
                if part.exists():
                    part.unlink()
            except OSError:
                pass
            raise
        try:
            zip_path.unlink()
        except OSError as e:
            logger.warning(f"加密完成但删除明文备份失败：{zip_path}（{e}）")
        return target

    @classmethod
    def __read_enc_header(cls, enc_path: Path) -> Optional[Dict[str, Any]]:
        """
        读取加密包的明文包头（不接触密文，供列表展示摘要）。

        :param enc_path: 加密包路径
        :return: 包头字典；格式非法时返回 None
        """
        try:
            with Path(enc_path).open("rb") as fh:
                magic = fh.read(len(_ENC_MAGIC))
                if magic != _ENC_MAGIC:
                    return None
                version = fh.read(1)
                if version != b"\x01":
                    return None
                fh.read(_ENC_SALT_LEN + _ENC_NONCE_LEN)
                raw_len = fh.read(4)
                if len(raw_len) != 4:
                    return None
                header_len = int.from_bytes(raw_len, "big")
                if header_len <= 0 or header_len > 1024 * 1024:
                    return None
                raw = fh.read(header_len)
                data = json.loads(raw.decode("utf-8"))
                return data if isinstance(data, dict) else None
        except Exception:
            return None

    def __decrypt_backup(self, enc_path: Path, dest_path: Path) -> None:
        """
        解密 ``.zip.enc`` 到指定路径（长度与 GCM 认证校验，失败即删除半成品）。

        :param enc_path: 加密包路径
        :param dest_path: 解密输出路径
        """
        password = self.__backup_password()
        if not password:
            raise WebDAVError("该备份已加密，请先在插件配置中填写「备份加密口令」")
        if Cipher is None:
            raise WebDAVError("当前环境缺少 cryptography，无法解密备份")
        enc_path = Path(enc_path)
        part = Path(dest_path).with_name(Path(dest_path).name + ".part")
        try:
            size = enc_path.stat().st_size
            with enc_path.open("rb") as fh:
                magic = fh.read(len(_ENC_MAGIC))
                if magic != _ENC_MAGIC:
                    raise WebDAVError("加密备份格式非法（文件头不匹配）")
                version = fh.read(1)
                if version != b"\x01":
                    raise WebDAVError("加密备份版本不受支持")
                salt = fh.read(_ENC_SALT_LEN)
                nonce = fh.read(_ENC_NONCE_LEN)
                raw_len = fh.read(4)
                if len(raw_len) != 4:
                    raise WebDAVError("加密备份格式非法（包头不完整）")
                header_len = int.from_bytes(raw_len, "big")
                if header_len <= 0 or header_len > 1024 * 1024:
                    raise WebDAVError("加密备份格式非法（包头长度异常）")
                header_bytes = fh.read(header_len)
                if len(header_bytes) != header_len:
                    raise WebDAVError("加密备份格式非法（包头被截断）")
                data_start = fh.tell()
                if size < data_start + 16:
                    raise WebDAVError("加密备份不完整（缺少认证标签）")
                tag_start = size - 16
                fh.seek(tag_start)
                tag = fh.read(16)
                fh.seek(data_start)
                prefix = (_ENC_MAGIC + b"\x01" + salt + nonce
                          + raw_len + header_bytes)
                key = self.__derive_key(password, salt)
                decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
                decryptor.authenticate_additional_data(prefix)
                remaining = tag_start - data_start
                with part.open("wb") as dst:
                    while remaining > 0:
                        chunk = fh.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise WebDAVError("加密备份不完整（密文被截断）")
                        remaining -= len(chunk)
                        dst.write(decryptor.update(chunk))
                    dst.write(decryptor.finalize())
            os.replace(part, dest_path)
        except InvalidTag as e:
            try:
                if part.exists():
                    part.unlink()
            except OSError:
                pass
            raise WebDAVError("解密失败：口令错误或备份包已被篡改") from e
        except WebDAVError:
            try:
                if part.exists():
                    part.unlink()
            except OSError:
                pass
            raise
        except Exception as e:
            try:
                if part.exists():
                    part.unlink()
            except OSError:
                pass
            raise WebDAVError(f"解密失败：{e}") from e

    def __materialize_backup(self, path: Path) -> Tuple[Optional[Path], Optional[Path], Optional[str]]:
        """
        把备份包准备成可读的 zip：加密包先解密到临时文件。

        :param path: 备份包路径（zip 或 zip.enc）
        :return: (可读 zip 路径, 临时文件路径, 错误信息)
        """
        path = Path(path)
        if not path.name.endswith(".enc"):
            return path, None, None
        temp_dir = Path(tempfile.mkdtemp(
            prefix="configbackup_dec_",
            dir=settings.TEMP_PATH if settings.TEMP_PATH else None,
        ))
        target = temp_dir / "package.zip"
        try:
            self.__decrypt_backup(path, target)
            return target, target, None
        except WebDAVError as e:
            shutil.rmtree(temp_dir, ignore_errors=True)
            return None, None, str(e)

    def __verify_backup(self, path: Path) -> bool:
        """
        校验备份包完整性：加密包先解密（含 GCM 认证）再逐个成员验 CRC。

        :param path: 备份包路径
        :return: True 表示可正常解压且认证通过
        """
        zip_path, temp_file, error = self.__materialize_backup(path)
        if error or zip_path is None:
            logger.error(f"备份包校验失败 {path}: {error}")
            return False
        try:
            return self.__verify_zip(str(zip_path))
        finally:
            if temp_file is not None:
                shutil.rmtree(temp_file.parent, ignore_errors=True)


    def __pending_file(self) -> Path:
        """待确认还原状态文件路径。"""
        return self.get_data_path() / "pending_restore.json"

    def __pending_delete_file(self) -> Path:
        """待确认删除状态文件路径。"""
        return self.get_data_path() / "pending_delete.json"

    @classmethod
    def __is_expired(cls, data: Dict[str, Any]) -> bool:
        """
        判断待确认状态是否已过期。

        老格式（无 ts 字段）不判过期，保持向后兼容——升级后不该把用户
        正在进行的确认操作无声清掉。

        :param data: 待确认状态
        :return: True 表示已超过 _pending_ttl，应作废
        """
        ts = data.get("ts")
        if not isinstance(ts, (int, float)):
            return False
        return (time.time() - ts) > cls._pending_ttl

    @classmethod
    def __stamp(cls, data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """写入待确认状态前打上时间戳。"""
        if isinstance(data, dict) and "ts" not in data:
            data = dict(data)
            data["ts"] = time.time()
        return data

    def __get_pending_delete(self) -> Optional[Dict[str, Any]]:
        """读取待确认删除状态（过期自动作废）。"""
        try:
            f = self.__pending_delete_file()
            if f.exists():
                data = json.loads(f.read_text(encoding="utf-8"))
                if data and data.get("filename"):
                    if self.__is_expired(data):
                        logger.info("待确认删除状态已过期，自动清除")
                        self.__set_pending_delete(None)
                        return None
                    return data
        except Exception as e:
            logger.debug(f"读取待删除状态失败: {e}")
        return None

    def __set_pending_delete(self, data: Optional[Dict[str, Any]]):
        """写入或清除待确认删除状态。"""
        try:
            f = self.__pending_delete_file()
            data = self.__stamp(data)
            if not data:
                if f.exists():
                    f.unlink()
                return
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"写入待删除状态失败: {e}")

    def __get_pending_restore(self) -> Optional[Dict[str, Any]]:
        """读取待确认还原状态（过期自动作废）。"""
        try:
            f = self.__pending_file()
            if f.exists():
                data = json.loads(f.read_text(encoding="utf-8"))
                if data and data.get("filename"):
                    if self.__is_expired(data):
                        logger.info("待确认还原状态已过期，自动清除")
                        self.__set_pending_restore(None)
                        return None
                    return data
        except Exception as e:
            logger.debug(f"读取待还原状态失败: {e}")
        return None

    def __set_pending_restore(self, data: Optional[Dict[str, Any]]):
        """写入或清除待确认还原状态。"""
        try:
            f = self.__pending_file()
            data = self.__stamp(data)
            if not data:
                if f.exists():
                    f.unlink()
                return
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"写入待还原状态失败: {e}")

    def __restore(self, zip_path: Path) -> Tuple[bool, str]:
        """
        执行配置还原：先还原文件（可回滚），再在单事务内还原数据库。

        文件先行的原因：数据库能靠事务自行回滚，文件不能。先做文件、失败立即恢复，
        最后做数据库，任何一步失败都能回到还原前的状态。

        :param zip_path: 备份包路径（zip 或 zip.enc）
        :return: (是否成功, 结果信息)
        """
        msgs: List[str] = []
        restore_dir: Optional[Path] = None
        temp_zip: Optional[Path] = None
        rollback: Optional[_FileRollback] = None
        try:
            source_zip, temp_zip, error = self.__materialize_backup(zip_path)
            if error or source_zip is None:
                return False, error or "无法读取备份文件"
            with zipfile.ZipFile(source_zip, "r") as zf:
                bad = zf.testzip()
                if bad:
                    return False, f"备份文件损坏（{bad}）"
            manifest = self.__read_manifest(source_zip) or {}

            restore_dir = Path(tempfile.mkdtemp(
                prefix="configbackup_restore_",
                dir=settings.TEMP_PATH if settings.TEMP_PATH else None,
            ))
            with zipfile.ZipFile(source_zip, "r") as zf:
                zf.extractall(restore_dir)
            logger.info(f"备份包已解压: {restore_dir}")

            # 1. 文件阶段：系统配置、Cookie、插件配置、附加路径（全部可回滚）
            rollback = _FileRollback(Path(tempfile.mkdtemp(
                prefix="configbackup_rb_",
                dir=settings.TEMP_PATH if settings.TEMP_PATH else None,
            )))
            file_ok, file_msgs = self.__restore_files(restore_dir, rollback)
            msgs.extend(file_msgs)
            if not file_ok:
                failed = rollback.rollback()
                msgs.append("回滚失败：" + "；".join(failed) if failed
                            else "已回滚文件改动，配置保持还原前状态")
                return False, "；".join(msgs)

            # 2. 数据库阶段：单事务执行，失败自动回滚；成功后校验结构与备份时一致
            sql_file = restore_dir / "postgresql_backup.sql"
            if sql_file.exists():
                if settings.DB_TYPE == "postgresql":
                    expected = manifest.get("schema") if isinstance(manifest, dict) else None
                    db_ok, db_msg = self.__restore_database(sql_file, expected or None)
                    msgs.append(db_msg)
                    if not db_ok:
                        failed = rollback.rollback()
                        msgs.append("回滚失败：" + "；".join(failed) if failed
                                    else "已回滚文件改动，配置保持还原前状态")
                        return False, "；".join(msgs)
                else:
                    msgs.append("备份含 PostgreSQL SQL，但当前数据库非 PostgreSQL，跳过数据库还原")
            else:
                msgs.append("备份中无数据库文件，跳过")

            rollback.commit()
            msgs.append("还原完成，请重启 MoviePilot 使配置完全生效")
            logger.info("；".join(msgs))
            return True, "；".join(msgs)
        except Exception as e:
            logger.error(f"还原失败: {e}")
            if rollback is not None:
                failed = rollback.rollback()
                if failed:
                    msgs.append("回滚失败：" + "；".join(failed))
            msgs.append(f"还原失败: {e}")
            return False, "；".join(msgs)
        finally:
            if restore_dir and restore_dir.exists():
                shutil.rmtree(restore_dir, ignore_errors=True)
            if temp_zip is not None:
                shutil.rmtree(temp_zip.parent, ignore_errors=True)

    def __restore_files(self, restore_dir: Path, rollback: _FileRollback) -> Tuple[bool, List[str]]:
        """
        还原系统配置、站点 Cookie、插件配置与附加路径（带逐项回滚记录）。

        :param restore_dir: 解压后的备份目录
        :param rollback: 回滚记录器
        :return: (是否全部成功, 结果信息列表)
        """
        msgs: List[str] = []
        config_path = Path(settings.CONFIG_PATH)
        cfg_restored: List[str] = []

        # 1. 系统配置文件：先暂存旧文件，再以临时文件 + 原子替换写入
        for name in ("app.env", "category.yaml", "user.db"):
            src = restore_dir / name
            if not (src.exists() and src.is_file()):
                continue
            dst = config_path / name
            if name == "user.db":
                # 旧 WAL 与新主库混在一起会读出错乱数据；一并纳入回滚
                for suffix in ("-wal", "-shm"):
                    rollback.preserve(config_path / f"user.db{suffix}")
            rollback.preserve(dst)
            tmp = dst.with_name(dst.name + ".restore_tmp")
            try:
                shutil.copy2(src, tmp)
                os.replace(tmp, dst)
                os.chmod(dst, 0o600)
            finally:
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
            rollback.mark_created(dst)
            cfg_restored.append(name)

        cookies_src = restore_dir / "cookies"
        if cookies_src.exists() and cookies_src.is_dir():
            self.__overlay_with_rollback(cookies_src, config_path / "cookies", rollback)
            cfg_restored.append("cookies")
        if cfg_restored:
            msgs.append(f"系统配置文件还原成功（{'、'.join(cfg_restored)}）")
        else:
            msgs.append("备份中无系统配置文件，跳过")

        # 2. 插件配置
        plugins_src = restore_dir / "plugins"
        if plugins_src.exists() and plugins_src.is_dir():
            n = self.__overlay_with_rollback(plugins_src, config_path / "plugins", rollback)
            msgs.append(f"插件配置还原成功（覆盖 {n} 项）")
        else:
            msgs.append("备份中无插件配置，跳过")

        # 3. 附加路径（只接受当前配置里列出的路径，包内其它路径一律跳过）
        extra_dir = restore_dir / "extra"
        manifest = extra_dir / "extra_paths.txt"
        if manifest.exists() and manifest.is_file():
            extra_ok, extra_msg = self.__restore_extra_paths(extra_dir, manifest, rollback)
            msgs.append(extra_msg)
            if not extra_ok:
                return False, msgs
        else:
            msgs.append("备份中无附加路径，跳过")
        return True, msgs

    @staticmethod
    def __overlay(src_dir: Path, dst_dir: Path) -> int:
        """
        覆盖式还原一个目录：备份包里出现过的顶层项**先删后写**，包里没有的不动。

        为什么不直接 rmtree 目标目录：老备份包可能只装了部分内容，
        整目录清空会把备份之后新增的东西一并抹掉，风险远大于收益。
        为什么不能只 copytree 合并：残留文件会让「还原」名不副实——
        备份之后装的插件配置留了下来，旧版本插件起来读到不匹配的配置。

        :param src_dir: 备份包内的目录
        :param dst_dir: 目标目录
        :return: 覆盖的顶层项数量
        """
        count = 0
        dst_dir.mkdir(parents=True, exist_ok=True)
        for item in src_dir.iterdir():
            target = dst_dir / item.name
            if target.is_symlink():
                target.unlink()
            elif target.exists():
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                else:
                    target.unlink()
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                shutil.copy(item, target)
            count += 1
        return count

    @staticmethod
    def __overlay_with_rollback(src_dir: Path, dst_dir: Path, rollback: _FileRollback) -> int:
        """
        覆盖式还原目录，并把被替换的旧内容登记到回滚器。

        ``symlinks=True`` 保留符号链接本身，避免把链接指向的外部文件内容复制进来。

        :param src_dir: 备份包内的目录
        :param dst_dir: 目标目录
        :param rollback: 回滚记录器
        :return: 覆盖的顶层项数量
        """
        count = 0
        dst_dir.mkdir(parents=True, exist_ok=True)
        for item in src_dir.iterdir():
            target = dst_dir / item.name
            rollback.preserve(target)
            if item.is_dir():
                shutil.copytree(item, target, symlinks=True)
            else:
                shutil.copy2(item, target, follow_symlinks=False)
            rollback.mark_created(target)
            count += 1
        return count

    def __extra_allow_roots(self) -> List[str]:
        """
        当前配置允许还原的附加路径根（包内清单只能落在这些根之内）。

        :return: 规范化后的绝对路径列表
        """
        roots: List[str] = []
        for raw in str(self._extra_paths or "").splitlines():
            path_part = raw.split("|", 1)[0].strip()
            if not path_part:
                continue
            normalized = posixpath.normpath(path_part)
            if normalized.startswith("/") and normalized not in roots:
                roots.append(normalized)
        return roots

    @staticmethod
    def __path_allowed(path: str, roots: List[str]) -> bool:
        """
        判断待还原路径是否位于允许根之内。

        :param path: 绝对路径
        :param roots: 允许的根列表
        :return: 是否允许
        """
        if not path.startswith("/"):
            return False
        normalized = posixpath.normpath(path)
        for root in roots:
            if normalized == root or normalized.startswith(root.rstrip("/") + "/"):
                return True
        return False

    def __restore_database(self, sql_file: Path, expected: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
        """
        执行数据库还原：优先用 psql 单事务执行完整 pg_dump 脚本。

        还原完成后在同一事务内校验主键、唯一约束、外键与索引数量；少于备份时的数量
        即抛错回滚，避免把「丢了约束」的库提交上去。

        :param sql_file: pg_dump 生成的 SQL 文件
        :param expected: 备份清单里的结构统计（老包可能没有）
        :return: (是否成功, 结果信息)
        """
        psql = shutil.which("psql")
        if not psql:
            return self.__restore_database_psycopg2(sql_file, expected)
        combined = sql_file.with_name("restore_with_check.sql")
        try:
            text = sql_file.read_text(encoding="utf-8", errors="replace")
            text += "\n\n" + self.__schema_check_sql(expected) + "\n"
            combined.write_text(text, encoding="utf-8")
            result = subprocess.run(
                [psql, "--no-psqlrc", "--quiet", "--single-transaction",
                 "--set", "ON_ERROR_STOP=1", "--file", str(combined)],
                env=self.__pg_env(), capture_output=True, text=True,
                timeout=_PG_CMD_TIMEOUT,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "").strip().splitlines()
                tail = " ".join(detail[-3:])[:600]
                logger.error(f"数据库还原失败（psql 返回 {result.returncode}）：{tail}")
                return False, f"数据库还原失败（事务已回滚）：{tail}"
            logger.info("数据库还原成功（psql 单事务，结构校验通过）")
            return True, "数据库还原成功（含主键/唯一约束/外键/索引校验）"
        except subprocess.TimeoutExpired:
            logger.error(f"数据库还原超时（>{_PG_CMD_TIMEOUT}s）")
            return False, f"数据库还原超时（>{_PG_CMD_TIMEOUT} 秒），事务已回滚"
        except Exception as e:
            logger.error(f"数据库还原失败: {e}")
            return False, f"数据库还原失败: {e}"
        finally:
            if combined.exists():
                try:
                    combined.unlink()
                except OSError:
                    pass

    @staticmethod
    def __schema_check_sql(expected: Optional[Dict[str, Any]]) -> str:
        """
        生成还原后的结构校验 SQL（数量少于备份时抛异常，触发整事务回滚）。

        :param expected: 备份清单中的结构统计
        :return: 可执行的 DO 块
        """
        if not expected:
            return "SELECT 1;"
        checks = [
            ("tables", "SELECT count(*) FROM pg_tables WHERE schemaname='public'"),
            ("primary_keys", "SELECT count(*) FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid "
                             "JOIN pg_namespace n ON n.oid=t.relnamespace "
                             "WHERE n.nspname='public' AND c.contype='p'"),
            ("unique_constraints", "SELECT count(*) FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid "
                                   "JOIN pg_namespace n ON n.oid=t.relnamespace "
                                   "WHERE n.nspname='public' AND c.contype='u'"),
            ("foreign_keys", "SELECT count(*) FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid "
                             "JOIN pg_namespace n ON n.oid=t.relnamespace "
                             "WHERE n.nspname='public' AND c.contype='f'"),
            ("indexes", "SELECT count(*) FROM pg_indexes WHERE schemaname='public'"),
            ("sequences", "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                          "WHERE n.nspname='public' AND c.relkind='S'"),
        ]
        lines = ["DO $mpcb$", "DECLARE", "  v integer;", "BEGIN"]
        for key, query in checks:
            want = expected.get(key)
            if not isinstance(want, int) or want < 0:
                continue
            lines.append(f"  {query} INTO v;")
            lines.append(
                f"  IF v < {want} THEN RAISE EXCEPTION "
                f"'还原后结构校验失败：{key} 仅 % 个，备份时为 {want} 个', v; END IF;"
            )
        lines.extend(["END", "$mpcb$;"])
        return "\n".join(lines)

    def __pg_env(self) -> Dict[str, str]:
        """
        构造 PG 客户端环境变量：密码走 PGPASSWORD，不进命令行与日志。

        :return: 子进程环境变量
        """
        env = os.environ.copy()
        env["PGPASSWORD"] = str(settings.DB_POSTGRESQL_PASSWORD or "")
        env["PGCONNECT_TIMEOUT"] = "10"
        env["PGOPTIONS"] = "-c lock_timeout=20000 -c statement_timeout=0"
        return env

    def __restore_database_psycopg2(self, sql_file: Path, expected: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
        """
        无 psql 时的数据库还原回退路径：用 psycopg2 解析 pg_dump 脚本并单事务执行。

        :param sql_file: pg_dump 生成的 SQL 文件
        :param expected: 备份清单里的结构统计
        :return: (是否成功, 结果信息)
        """
        conn = None
        try:
            conn = psycopg2.connect(
                host=str(settings.DB_POSTGRESQL_HOST),
                port=str(settings.DB_POSTGRESQL_PORT),
                user=str(settings.DB_POSTGRESQL_USERNAME),
                password=str(settings.DB_POSTGRESQL_PASSWORD),
                dbname=str(settings.DB_POSTGRESQL_DATABASE),
                connect_timeout=10,
            )
            conn.autocommit = False
            cur = conn.cursor()
            cur.execute("SET LOCAL lock_timeout = '20s'")
            lines = sql_file.read_text(encoding="utf-8", errors="replace").splitlines()
            i = 0
            copy_cnt = 0
            stmt_cnt = 0
            while i < len(lines):
                line = lines[i].strip()
                m = re.match(r'^COPY "(.+)" FROM stdin;$', line)
                if m:
                    table = m.group(1)
                    j = i + 1
                    buf = []
                    while j < len(lines) and lines[j].strip() != "\\.":
                        buf.append(lines[j])
                        j += 1
                    data = "\n".join(buf)
                    if buf:
                        data += "\n"
                    cur.copy_expert(f'COPY "{table}" FROM STDIN', StringIO(data))
                    copy_cnt += 1
                    i = j + 1
                else:
                    # 跳过空行与注释行；其余按语句收集（可能跨多行，直到分号结束）后执行
                    if line and not line.startswith("--"):
                        stmt_lines = [line]
                        while not stmt_lines[-1].endswith(";"):
                            i += 1
                            if i >= len(lines):
                                break
                            nxt = lines[i].strip()
                            if nxt and not nxt.startswith("--"):
                                stmt_lines.append(nxt)
                        cur.execute("\n".join(stmt_lines))
                        stmt_cnt += 1
                    i += 1
            if expected:
                cur.execute(self.__schema_check_sql(expected).replace("DO $mpcb$", "DO $mpcb$"))
            conn.commit()
            logger.info(f"数据库还原成功（{copy_cnt} 张表数据，{stmt_cnt} 条语句）")
            return True, f"数据库还原成功（{copy_cnt} 张表）"
        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            logger.error(f"数据库还原失败: {e}")
            return False, f"数据库还原失败: {e}"
        finally:
            if conn:
                conn.close()

    def __restore_extra_paths(self, extra_dir: Path, manifest: Path,
                              rollback: Optional[_FileRollback] = None) -> Tuple[bool, str]:
        """
        按备份清单还原附加路径到原始位置（只允许当前配置声明过的路径）。

        包内清单可能被篡改，因此目标路径不与「当前插件配置的附加路径」匹配时一律跳过，
        不会按包里的绝对路径写到任意位置。

        :param extra_dir: 备份包内 extra 目录
        :param manifest: 原始路径清单文件
        :param rollback: 回滚记录器（为空时退化为不带回滚的旧行为）
        :return: (是否成功, 结果信息)
        """
        restored = 0
        failed: List[str] = []
        skipped: List[str] = []
        roots = self.__extra_allow_roots()
        try:
            for raw in manifest.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line:
                    continue
                # 新格式：目录名<TAB>原始路径；旧格式：仅原始路径
                folder, _, path_text = line.partition("\t")
                if path_text:
                    candidate = extra_dir / folder
                else:
                    path_text = folder
                    candidate = extra_dir / Path(path_text).name
                target_text = path_text.strip()
                if not self.__path_allowed(target_text, roots):
                    skipped.append(target_text)
                    continue
                if not candidate.exists():
                    # 兼容旧包：某些版本直接以 basename 存放
                    fallback = extra_dir / Path(target_text).name
                    if fallback.exists():
                        candidate = fallback
                    else:
                        failed.append(target_text)
                        continue
                try:
                    target = Path(target_text)
                    if rollback is not None:
                        rollback.preserve(target)
                        if candidate.is_dir():
                            self.__overlay_with_rollback(candidate, target, rollback)
                        else:
                            target.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(candidate, target)
                            rollback.mark_created(target)
                    elif candidate.is_dir():
                        self.__overlay(candidate, target)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy(candidate, target)
                    restored += 1
                except Exception as e:
                    logger.warning(f"附加路径还原失败 {target_text}: {e}")
                    failed.append(target_text)
            prefix = f"附加路径：跳过 {len(skipped)} 项不在当前配置白名单内的路径" if skipped else ""
            if failed:
                detail = f"附加路径还原部分失败（成功 {restored}，失败 {len(failed)}：{', '.join(failed[:3])}）"
                return False, "；".join([p for p in (detail, prefix) if p])
            if restored:
                return True, "；".join([p for p in (f"附加路径还原成功（{restored} 项）", prefix) if p])
            return True, prefix or "附加路径清单为空，跳过"
        except Exception as e:
            logger.error(f"附加路径还原失败: {e}")
            return False, f"附加路径还原失败: {e}"

    def __list_backups(self, bk_path: Path) -> List[Dict[str, Any]]:
        """
        获取备份目录下所有备份文件列表（按创建时间倒序，支持加密包）。

        :param bk_path: 备份目录
        :return: 备份文件信息列表
        """
        result: List[Dict[str, Any]] = []
        try:
            files = list(reversed(self.__iter_local_backups(bk_path)))
        except OSError as e:
            logger.error(f"读取备份目录失败 {bk_path}: {e}")
            return result
        for path in files:
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            result.append({
                "name": path.name,
                "path": str(path),
                "size": size,
                "time": datetime.fromtimestamp(self.__backup_time(str(path))).strftime("%Y-%m-%d %H:%M:%S"),
                "summary": self.__summarize(str(path)),
            })
        return result

    # ------------------------------------------------------------------
    # WebDAV 远端备份（v3.2.0 模块 B）
    # ------------------------------------------------------------------
    def __webdav_client(self) -> WebDAVClient:
        """
        按当前配置构造 WebDAV 客户端。

        :return: 客户端实例
        """
        return WebDAVClient(
            self._webdav_url, self._webdav_user, self._webdav_pass,
            timeout=self._webdav_timeout, allow_http=self._webdav_allow_http,
        )

    def __remote_dir(self) -> str:
        """远端备份目录（去掉首尾斜杠，供客户端拼接）。"""
        return (self._webdav_dir or "/MoviePilot").strip().strip("/")

    def __remote_path(self, name: str) -> str:
        """
        某个备份包在远端的相对路径。

        :param name: 备份文件名
        :return: 远端相对路径
        """
        remote_dir = self.__remote_dir()
        return f"{remote_dir}/{name}" if remote_dir else name

    def __remote_keep(self) -> int:
        """远端保留份数：留空（或非法值）则跟随「保留备份个数」。"""
        try:
            value = int(str(self._webdav_keep).strip()) if str(self._webdav_keep or "").strip() else 0
        except (TypeError, ValueError):
            value = 0
        return value if value > 0 else int(self._keep_count or 10)

    def __webdav_status_text(self) -> str:
        """详情页上的一句话 WebDAV 状态（未启用时提示去哪开）。"""
        if not self._webdav_enabled:
            return "未启用 WebDAV 上传（可在插件配置里开启）"
        return f"远端目录：/{self.__remote_dir()}（保留 {self.__remote_keep()} 份）"

    def __remote_time(self, entry: Dict[str, Any]) -> float:
        """
        远端条目的时间戳：优先解析文件名 ``bk_<14位时间>.zip[.enc]``，其次 HTTP 日期。

        与本地一致 —— 文件名时间戳最稳（跨时区、跨客户端都一致），
        服务端返回的修改时间只是兜底。

        :param entry: 远端列表项
        :return: 时间戳（秒），取不到给 0
        """
        name = str(entry.get("name") or "")
        stamped = self.__name_stamp(name)
        if stamped is not None:
            return stamped
        try:
            return parsedate_to_datetime(str(entry.get("modified") or "")).timestamp()
        except Exception:
            return 0.0

    def __list_remote_backups(self) -> List[Dict[str, Any]]:
        """
        列出远端备份包（按时间倒序）。

        只认与本地同规则的 ``bk_<14位时间>.zip`` —— 远端目录里可能还放着用户
        自己的其它文件，不按前缀过滤就可能被后面的清理逻辑误删。

        :return: 列表项（name / size / time / path）
        """
        entries = self.__webdav_client().list_dir(self.__remote_dir())
        result: List[Dict[str, Any]] = []
        for entry in entries:
            name = str(entry.get("name") or "")
            if entry.get("is_dir") or not self.__is_backup_name(name):
                continue
            timestamp = self.__remote_time(entry)
            result.append({
                "name": name,
                "size": int(entry.get("size") or 0),
                "time": datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S") if timestamp else "",
                "mtime": timestamp,
                "path": entry.get("path") or self.__remote_path(name),
            })
        result.sort(key=lambda item: item["mtime"], reverse=True)
        return result

    def __download_remote_backup(self, name: str) -> Path:
        """
        把远端备份包下载到本地备份目录（下载后立刻校验完整性）。

        不覆盖本地同名文件：同名文件可能是唯一可用的一份本地备份，先让用户处理。

        :param name: 远端文件名（``bk_*.zip`` 或 ``bk_*.zip.enc``）
        :return: 本地文件路径
        """
        safe_name = Path(name).name
        if safe_name != name or not self.__is_backup_name(safe_name):
            raise WebDAVError("非法文件名")
        bk_path = self.__backup_dir_path()
        bk_path.mkdir(parents=True, exist_ok=True)
        target = bk_path / safe_name
        if target.exists():
            raise WebDAVError("本地已存在同名备份，已取消下载；请先删除或改名本地文件")
        size = self.__webdav_client().download(self.__remote_path(safe_name), target)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
        if not self.__verify_backup(target):
            # 损坏包必须删掉：留着会占保留名额，还会让用户以为它可用
            try:
                target.unlink()
            except Exception:
                pass
            raise WebDAVError("下载的备份包校验失败（可能不完整、口令错误或被篡改），已删除")
        logger.info(f"远端备份已下载：{target}（{size} 字节）")
        return target

    def __upload_to_webdav(self, zip_file: Path) -> Tuple[bool, str]:
        """
        把备份包上传到 WebDAV。

        定案（§7.3）：上传失败**算整次备份失败**（通知标红），但文案必须写清
        「本地备份已生成、可用」—— 否则用户会以为这次备份白做了。

        :param zip_file: 本地备份包路径
        :return: (是否成功, 结果信息)
        """
        if not self.__backup_password():
            return False, (f"本地备份已生成 {zip_file.name}（可用）；"
                           "上传失败：未设置「备份加密口令」，拒绝把含凭据的明文包上传到远端")
        if not zip_file.name.endswith(".enc"):
            return False, (f"本地备份已生成 {zip_file.name}（可用）；"
                           "上传失败：备份包未加密，拒绝上传")
        remote_path = self.__remote_path(zip_file.name)
        try:
            size = self.__webdav_client().upload(zip_file, remote_path)
            logger.info(f"备份包已上传到 WebDAV：{remote_path}（{size} 字节）")
            return True, f"已上传到 WebDAV（{remote_path}）"
        except WebDAVError as e:
            logger.error(f"上传备份到 WebDAV 失败：{e}")
            # 只有"凭证有效但中途出错"才可能留下半包；认证/权限失败不会写进任何东西
            if "401" not in str(e) and "403" not in str(e):
                self.__remove_remote_quietly(zip_file.name)
            return False, f"本地备份已生成 {zip_file.name}（可用）；上传失败：{e}"
        except Exception as e:  # pragma: no cover - 兜底
            logger.error(f"上传备份到 WebDAV 异常：{e}")
            return False, f"本地备份已生成 {zip_file.name}（可用）；上传失败：{e}"

    def __remove_remote_quietly(self, name: str) -> None:
        """
        尽力清掉远端可能残留的半包；失败只记日志，绝不掩盖原始错误。

        :param name: 远端文件名
        """
        if not self._webdav_url:
            return
        try:
            self.__webdav_client().delete(self.__remote_path(name))
            logger.info(f"已清理远端残留文件：{name}")
        except Exception as e:
            logger.debug(f"清理远端残留文件失败（忽略）：{e}")

    def __clean_remote_backups(self) -> int:
        """
        按「远端保留份数 + 保留天数」清理远端旧备份，返回删除份数。

        与本地清理同一套规则（两者**满足其一即保留**），只是数据源换成远端列表；
        只在本次上传成功后才调用（§7.3）。

        :return: 删除的份数
        """
        items = self.__list_remote_backups()
        if not items:
            return 0
        keep = self.__remote_keep()
        keep_days = int(self._keep_days or 0)
        cutoff = time.time() - keep_days * 86400 if keep_days > 0 else None
        client = self.__webdav_client()
        deleted = 0
        # 已按时间倒序：下标 >= keep 的才是"超出份数"的候选
        for item in items[keep:]:
            if cutoff is not None and item["mtime"] and item["mtime"] >= cutoff:
                continue
            try:
                client.delete(self.__remote_path(item["name"]))
                deleted += 1
            except WebDAVError as e:
                logger.warning(f"删除远端旧备份失败 {item['name']}：{e}")
        return deleted

    def __render_remote_section(self) -> List[dict]:
        """
        渲染详情页的「远端备份」区。

        远端不可达时退化成一条告警：详情页打不开会让用户连**本地**备份都看不到，
        比"远端列表为空"严重得多。

        :return: 组件列表
        """
        title = {
            "component": "p",
            "props": {"class": "text-subtitle-2 mt-4"},
            "text": f"远端备份（WebDAV：/{self.__remote_dir()}）",
        }
        try:
            items = self.__list_remote_backups()
        except WebDAVError as e:
            return [title, {
                "component": "VAlert",
                "props": {
                    "type": "warning", "variant": "tonal", "class": "mt-2",
                    "text": f"远端列表获取失败：{e}",
                },
            }]
        if not items:
            return [title, {
                "component": "VAlert",
                "props": {
                    "type": "info", "variant": "tonal", "class": "mt-2",
                    "text": "远端暂无备份包。",
                },
            }]

        rows = []
        for item in items:
            name = item["name"]
            rows.append({
                "component": "tr",
                "content": [
                    {"component": "td", "content": [
                        {"component": "p", "props": {"class": "mb-0"}, "text": name}]},
                    {"component": "td", "content": [{
                        "component": "p",
                        "props": {"class": "mb-0"},
                        "text": StringUtils.str_filesize(item["size"]) if item["size"] else "-",
                    }]},
                    {"component": "td", "content": [
                        {"component": "p", "props": {"class": "mb-0"}, "text": item["time"]}]},
                    {
                        "component": "td",
                        "props": {"class": "text-right", "style": "white-space: nowrap;"},
                        "content": [
                            {
                                "component": "VBtn",
                                "props": {
                                    "color": "primary", "variant": "text", "size": "x-small",
                                    "prependIcon": "mdi-download",
                                    "title": "下载到本地备份目录（之后可在本地列表里还原）",
                                },
                                "text": "下载",
                                "events": {
                                    "click": {
                                        "api": "plugin/ConfigBackup/webdav/download",
                                        "method": "get",
                                        "params": {"apikey": settings.API_TOKEN, "filename": name},
                                    }
                                },
                            },
                            {
                                "component": "VBtn",
                                "props": {
                                    "color": "warning", "variant": "text", "size": "x-small",
                                    "prependIcon": "mdi-cloud-download", "class": "ml-2",
                                    "title": "下载到本地并选中为待还原，再到本地列表点【确认还原】",
                                },
                                "text": "下载并还原",
                                "events": {
                                    "click": {
                                        "api": "plugin/ConfigBackup/webdav/restore",
                                        "method": "get",
                                        "params": {"apikey": settings.API_TOKEN, "filename": name},
                                    }
                                },
                            },
                        ],
                    },
                ],
            })
        return [title, {
            "component": "VTable",
            "props": {"density": "compact", "hover": True},
            "content": [
                {
                    "component": "thead",
                    "content": [{
                        "component": "tr",
                        "content": [
                            {"component": "th", "props": {"class": "text-left"}, "text": "远端备份文件"},
                            {"component": "th", "props": {"class": "text-left"}, "text": "大小"},
                            {"component": "th", "props": {"class": "text-left"}, "text": "备份时间"},
                            {"component": "th", "props": {"class": "text-right"}, "text": "操作"},
                        ],
                    }],
                },
                {"component": "tbody", "content": rows},
            ],
        }]

    def __notify_mtype(self) -> MessageType:
        """
        按配置解析通知场景；未知值回落为「插件」。

        注意 MessageType 是**通知场景**（站点/插件/手动处理…），不是渠道；
        实际发到微信还是 Telegram 由宿主按场景路由，用户在通知设置里配。

        :return: 通知场景枚举
        """
        try:
            return MessageType(self._notify_type)
        except Exception:
            return MessageType.Plugin

    def __backup(self, notify: bool = True, local_only: bool = False,
                 protect: Optional[set] = None) -> Tuple[bool, str]:
        """
        执行配置备份（对外入口）：包装 __run_backup，统一负责通知。

        成功与失败都会按配置发通知——备份失败恰恰是最需要被感知的事件，
        只在成功分支通知会让定时备份的失败彻底静默（只剩日志）。

        :param notify: 是否发送通知（还原前的安全备份传 False）
        :param local_only: 是否只做本地备份（还原前的安全备份不传远端）
        :param protect: 清理旧备份时必须保留的文件名集合
        :return: (是否成功, 结果信息)
        """
        success, msg = self.__run_backup(local_only=local_only, protect=protect)
        if notify and self._notify:
            try:
                self.post_message(
                    mtype=self.__notify_mtype(),
                    title="【配置备份完成】" if success else "【配置备份失败】",
                    text=msg,
                )
            except Exception as e:
                logger.error(f"发送备份通知失败: {e}")
        return success, msg

    def __collect_schema_stats(self) -> Dict[str, int]:
        """
        统计当前库的结构数量，写入备份清单，供还原后核对。

        :return: 表/主键/唯一约束/外键/索引/序列数量；非 PostgreSQL 或查询失败时为空
        """
        if settings.DB_TYPE != "postgresql":
            return {}
        conn = None
        try:
            conn = psycopg2.connect(
                host=str(settings.DB_POSTGRESQL_HOST),
                port=str(settings.DB_POSTGRESQL_PORT),
                user=str(settings.DB_POSTGRESQL_USERNAME),
                password=str(settings.DB_POSTGRESQL_PASSWORD),
                dbname=str(settings.DB_POSTGRESQL_DATABASE),
                connect_timeout=10,
            )
            cur = conn.cursor()
            queries = {
                "tables": "SELECT count(*) FROM pg_tables WHERE schemaname='public'",
                "primary_keys": "SELECT count(*) FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid "
                                "JOIN pg_namespace n ON n.oid=t.relnamespace "
                                "WHERE n.nspname='public' AND c.contype='p'",
                "unique_constraints": "SELECT count(*) FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid "
                                      "JOIN pg_namespace n ON n.oid=t.relnamespace "
                                      "WHERE n.nspname='public' AND c.contype='u'",
                "foreign_keys": "SELECT count(*) FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid "
                                "JOIN pg_namespace n ON n.oid=t.relnamespace "
                                "WHERE n.nspname='public' AND c.contype='f'",
                "indexes": "SELECT count(*) FROM pg_indexes WHERE schemaname='public'",
                "sequences": "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                             "WHERE n.nspname='public' AND c.relkind='S'",
            }
            stats: Dict[str, int] = {}
            for key, sql in queries.items():
                cur.execute(sql)
                stats[key] = int(cur.fetchone()[0])
            return stats
        except Exception as e:
            logger.warning(f"读取数据库结构统计失败（还原时将跳过结构核对）：{e}")
            return {}
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def __write_manifest(self, temp_dir: Path) -> Dict[str, Any]:
        """
        在备份包内写入清单：这个包是什么时候、在什么环境下、装了哪些内容。

        没有它，用户只能靠文件名猜——选错包还原的代价是配置被覆盖。
        老备份包本来就没有这个文件，读取方必须容忍缺失。

        :param temp_dir: 备份临时目录
        :return: 写入的清单内容
        """
        extra_manifest = temp_dir / "extra" / "extra_paths.txt"
        try:
            extra_count = len([
                ln for ln in extra_manifest.read_text(encoding="utf-8").splitlines() if ln.strip()
            ]) if extra_manifest.exists() else 0
        except Exception:
            extra_count = 0
        parts = [p for p in self._ALL_PARTS if p in (self._backup_parts or ())]
        info = {
            "plugin": self.plugin_name,
            "version": VERSION,
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "db_type": str(settings.DB_TYPE),
            #: 本次勾选了哪些部分（v3.2.0 起写入；老读取方忽略即可）
            "parts": parts,
            # 以下三个字段保留给 v3.1.1 及以前的读取方，语义仍是「包里实际有什么」
            "database": (temp_dir / "postgresql_backup.sql").exists()
                        or bool(list(temp_dir.glob("user.db*"))),
            "plugins": (temp_dir / "plugins").exists(),
            "extra_count": extra_count,
            "files": sum(1 for f in temp_dir.rglob("*") if f.is_file()),
            #: 备份时的库结构数量（还原后核对，防止还原出丢约束的库）
            "schema": self.__collect_schema_stats(),
            #: 是否加密（加密容器会重写该字段）
            "encrypted": False,
        }
        try:
            (temp_dir / "backup_manifest.json").write_text(
                json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"写入备份清单失败: {e}")
        return info

    @staticmethod
    def __read_manifest(zip_path: Path) -> Optional[Dict[str, Any]]:
        """
        读取备份包内的清单；老包无此文件时返回 None（不得因此拒绝还原）。

        :param zip_path: 备份包路径
        :return: 清单内容或 None
        """
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                if "backup_manifest.json" not in zf.namelist():
                    return None
                return json.loads(zf.read("backup_manifest.json").decode("utf-8"))
        except Exception:
            return None

    @classmethod
    def __summarize(cls, zip_path: str) -> str:
        """
        把清单压成一行摘要，用于列表展示（加密包读明文包头，不解密整包）。

        :param zip_path: 备份包路径
        :return: 摘要文本
        """
        path = Path(zip_path)
        if path.name.endswith(".enc"):
            data = cls.__read_enc_header(path)
            if not data:
                return "加密包（包头无法读取）"
            text = cls.__summarize_data(data)
            return f"{text}（已加密）"
        data = cls.__read_manifest(path)
        if not data:
            return "老备份包（无清单）"
        return cls.__summarize_data(data)

    @classmethod
    def __summarize_data(cls, data: Dict[str, Any]) -> str:
        """
        由清单内容生成一行摘要。

        :param data: 清单或加密包头
        :return: 摘要文本
        """
        selected = data.get("parts")
        if not selected:
            # 老包（v3.1.1 及以前没有 parts 字段）：按包里实际有什么展示
            return cls.__summarize_legacy(data)
        labels = []
        for part in cls._ALL_PARTS:
            if part not in selected:
                continue
            if part == cls._PART_DATABASE:
                labels.append(f"数据库({data.get('db_type') or '未知'})")
            elif part == cls._PART_EXTRA:
                count = data.get("extra_count") or 0
                labels.append(f"附加×{count}" if count else "附加路径")
            else:
                labels.append(cls._PART_LABELS[part])
        return "＋".join(labels) if labels else "无内容记录"

    @staticmethod
    def __summarize_legacy(data: Dict[str, Any]) -> str:
        """
        老备份包（清单里没有 parts 字段）的摘要：按包里实际有的内容展示。

        :param data: 备份清单
        :return: 摘要文本
        """
        labels = []
        if data.get("database"):
            labels.append(f"数据库({data.get('db_type') or '未知'})")
        if data.get("plugins"):
            labels.append("插件配置")
        if data.get("extra_count"):
            labels.append(f"附加×{data['extra_count']}")
        return "＋".join(labels) if labels else "仅系统配置"

    @staticmethod
    def __verify_zip(zip_file: str) -> bool:
        """
        校验备份包完整性（逐个成员的 CRC）。

        :param zip_file: 备份包路径
        :return: True 表示可正常解压
        """
        try:
            with zipfile.ZipFile(zip_file, "r") as zf:
                return zf.testzip() is None
        except Exception as e:
            logger.error(f"备份包校验失败 {zip_file}: {e}")
            return False

    @staticmethod
    def __backup_time(f: str) -> float:
        """
        取备份文件的时间戳：优先解析文件名里的 bk_<YYYYmmddHHMMSS>.zip[.enc]。

        不用 ctime——文件被 rsync / 拷贝到本机后 ctime 会变成拷贝时间，
        按它排序会把最新备份当成最旧的删掉。解析不出再退回 mtime。

        :param f: 备份文件路径
        :return: 时间戳（秒）
        """
        m = _BACKUP_NAME_RE.fullmatch(Path(f).name)
        if m:
            stamped = ConfigBackup.__name_stamp(Path(f).name)
            if stamped is not None:
                return stamped
        try:
            return os.path.getmtime(f)
        except Exception:
            return 0.0

    def __run_backup(self, local_only: bool = False,
                     protect: Optional[set] = None) -> Tuple[bool, str]:
        """
        执行配置备份（带并发保护）：同一时刻只允许一个备份任务在跑。

        「立即运行一次」改为后台线程后，可能与定时触发/手动点击撞车，
        并发写同一个临时目录名会互相覆盖，所以这里串行化。

        :param local_only: 是否跳过 WebDAV 上传
        :param protect: 清理旧备份时必须保留的文件名集合
        :return: (是否成功, 结果信息)
        """
        if self._restoring:
            return False, "还原正在进行，本次备份跳过"
        if not self._backup_lock.acquire(blocking=False):
            return False, "已有备份任务正在执行，本次跳过"
        try:
            return self.__do_backup(local_only=local_only, protect=protect)
        finally:
            self._backup_lock.release()

    def __do_backup(self, local_only: bool = False,
                    protect: Optional[set] = None) -> Tuple[bool, str]:
        """
        执行配置备份：按勾选的备份内容逐项收集、压缩、加密、自检与清理。

        :param local_only: 是否跳过 WebDAV 上传（还原前的安全备份用）
        :param protect: 清理旧备份时必须保留的文件名集合
        :return: (是否成功, 结果信息)
        """
        logger.info(f"开始配置备份，当前时间 {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}")

        # 一项都没勾选：直接拒绝，不打空包——空包会让「什么都没有」被当成一次有效备份
        parts = tuple(p for p in self._ALL_PARTS if p in (self._backup_parts or ()))
        if not parts:
            msg = "未勾选任何备份内容，已取消本次备份（请在「备份内容」中至少选择一项）"
            logger.warning(msg)
            return False, msg

        # 备份保存路径
        bk_path = Path(self._backup_dir) if self._backup_dir else self.get_data_path()
        try:
            bk_path.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(f"创建备份目录失败: {bk_path} {e}")
            return False, f"创建备份目录失败: {e}"

        # 临时备份目录：放系统临时区，不要放在备份目录里——备份目录常挂在网盘上，
        # 几万个小文件跨网 IO 既慢又容易被中断。
        backup_name = f"{self._prefix}{time.strftime('%Y%m%d%H%M%S')}"
        temp_dir = Path(tempfile.mkdtemp(
            prefix="configbackup_",
            dir=settings.TEMP_PATH if settings.TEMP_PATH else None,
        ))
        zip_file = str(bk_path / backup_name) + ".zip"
        msgs = []

        try:
            # 1. 备份数据库（PostgreSQL 导出 SQL；SQLite 复制 user.db 文件）
            db_success = True
            if self._PART_DATABASE in parts:
                db_success, db_msg = self.__backup_database(temp_dir)
                msgs.append(db_msg)

            # 2. 备份系统配置（app.env / category.yaml）
            cfg_success = True
            if self._PART_SYSTEM in parts:
                cfg_success, cfg_msg = self.__copy_system_files(temp_dir)
                msgs.append(cfg_msg)

            # 3. 备份站点 Cookie
            cookie_success = True
            if self._PART_COOKIES in parts:
                cookie_success, cookie_msg = self.__copy_cookies(temp_dir)
                msgs.append(cookie_msg)

            # 4. 备份插件配置
            plugin_success = True
            if self._PART_PLUGINS in parts:
                plugin_success, plugin_msg = self.__copy_plugins(temp_dir)
                msgs.append(plugin_msg)

            # 5. 备份附加路径（勾了但没配路径时跳过，不算失败）
            extra_success = True
            if self._PART_EXTRA in parts and self._extra_paths:
                extra_success, extra_msg = self.__copy_extra_paths(temp_dir)
                msgs.append(extra_msg)

            if not (db_success and cfg_success and cookie_success
                    and plugin_success and extra_success):
                return False, "；".join(msgs)

            # 6. 写入清单（含库结构统计，供还原后核对）
            manifest = self.__write_manifest(temp_dir)

            # 7. 压缩：先在临时区成包，再整体移入备份目录（网盘场景更快，也更原子）
            shutil.make_archive(str(temp_dir), "zip", str(temp_dir))
            shutil.rmtree(str(temp_dir), ignore_errors=True)
            shutil.move(str(temp_dir) + ".zip", zip_file)
            final_path = Path(zip_file)

            # 8. 加密（设置了口令时）：本地与远端都只保留加密包
            if self.__backup_password():
                if Cipher is None:
                    raise RuntimeError("当前环境缺少 cryptography，无法按配置加密备份")
                final_path = self.__encrypt_backup(final_path, manifest)
                msgs.append("备份包已加密（AES-256-GCM）")
            try:
                os.chmod(final_path, 0o600)
            except OSError:
                pass

            # 9. 自检：损坏的包改名隔离，避免占保留名额、被当成可还原备份
            if not self.__verify_backup(final_path):
                broken = str(final_path) + ".broken"
                try:
                    os.replace(final_path, broken)
                except Exception as e:
                    logger.error(f"隔离损坏备份失败 {final_path}: {e}")
                msgs.append("备份包完整性校验失败，已隔离为 .broken")
                return False, "；".join(msgs)
            zip_size = final_path.stat().st_size
            msgs.append(f"备份完成：{final_path.name}（{StringUtils.str_filesize(zip_size)}）")
            success = True

            # 10. 上传到 WebDAV：必须在包自检通过之后；上传失败算整次失败
            if self._webdav_enabled and not local_only:
                upload_ok, upload_msg = self.__upload_to_webdav(final_path)
                msgs.append(upload_msg)
                success = success and upload_ok
        except Exception as e:
            logger.error(f"创建备份失败: {e}")
            # 加密流程中途失败时，不能让明文包留在备份目录
            try:
                plain = Path(zip_file)
                if self.__backup_password() and plain.exists():
                    plain.unlink()
            except OSError:
                pass
            return False, f"创建备份失败: {e}"
        finally:
            shutil.rmtree(str(temp_dir), ignore_errors=True)
            leftover = Path(str(temp_dir) + ".zip")
            if leftover.exists():
                try:
                    leftover.unlink()
                except OSError:
                    pass

        # 11. 清理旧备份（还原中的待还原包受保护）
        del_cnt = self.__clean_old_backups(bk_path, protect=protect)
        if del_cnt > 0:
            msgs.append(f"自动清理旧备份 {del_cnt} 份")

        # 12. 远端清理：只在本次上传成功后做，失败不影响本地结果
        if self._webdav_enabled and not local_only and success:
            try:
                remote_del = self.__clean_remote_backups()
                if remote_del > 0:
                    msgs.append(f"远端清理旧备份 {remote_del} 份")
            except WebDAVError as e:
                msgs.append(f"远端清理失败：{e}")

        msg = "；".join(msgs)
        logger.info(msg)
        return success, msg

    def __dump_database(self, temp_dir: Path) -> Tuple[bool, str]:
        """
        使用 pg_dump 导出 PostgreSQL 数据库（完整结构、约束、索引与数据）。

        为什么不再自写 SQL：手写导出极易漏掉主键、唯一约束、外键与索引；还原出来的库
        会让宿主依赖唯一索引的写入（插入冲突更新）全部失败。pg_dump 在单个可重复读
        快照内导出，跨表数据也保持一致。

        :param temp_dir: 备份临时目录
        :return: (是否成功, 结果信息)
        """
        if settings.DB_TYPE != "postgresql":
            return True, "当前数据库非 PostgreSQL，跳过数据库备份"
        pg_dump = shutil.which("pg_dump")
        if not pg_dump:
            return False, "数据库备份失败：未找到 pg_dump，无法生成可完整还原的备份"
        sql_file = temp_dir / "postgresql_backup.sql"
        cmd = [
            pg_dump, "--clean", "--if-exists", "--no-owner", "--no-privileges",
            "--format=plain", "--encoding=UTF8", "--file", str(sql_file),
            "--host", str(settings.DB_POSTGRESQL_HOST),
            "--port", str(settings.DB_POSTGRESQL_PORT),
            "--username", str(settings.DB_POSTGRESQL_USERNAME),
            "--dbname", str(settings.DB_POSTGRESQL_DATABASE),
        ]
        try:
            result = subprocess.run(
                cmd, env=self.__pg_env(), capture_output=True, text=True,
                timeout=_PG_CMD_TIMEOUT,
            )
            if result.returncode != 0:
                tail = " ".join((result.stderr or "").strip().splitlines()[-3:])[:600]
                logger.error(f"数据库备份失败（pg_dump 返回 {result.returncode}）：{tail}")
                if sql_file.exists():
                    sql_file.unlink()
                return False, f"数据库备份失败：{tail}"
            if not sql_file.exists() or sql_file.stat().st_size < 1024:
                return False, "数据库备份失败：pg_dump 输出为空"
            logger.info(f"数据库备份成功: {sql_file}（{sql_file.stat().st_size} 字节）")
            return True, "数据库备份成功（pg_dump 完整导出）"
        except subprocess.TimeoutExpired:
            logger.error(f"数据库备份超时（>{_PG_CMD_TIMEOUT}s）")
            if sql_file.exists():
                sql_file.unlink()
            return False, f"数据库备份超时（>{_PG_CMD_TIMEOUT} 秒）"
        except Exception as e:
            logger.error(f"数据库备份失败: {e}")
            if sql_file.exists():
                sql_file.unlink()
            return False, f"数据库备份失败: {e}"

    def __backup_database(self, temp_dir: Path) -> Tuple[bool, str]:
        """
        备份「数据库」部分：按数据库类型分派。

        PostgreSQL 走 SQL 导出；SQLite 走文件复制；其它类型跳过（返回成功）。

        :param temp_dir: 备份临时目录
        :return: (是否成功, 结果信息)
        """
        if settings.DB_TYPE == "postgresql":
            return self.__dump_database(temp_dir)
        if settings.DB_TYPE == "sqlite":
            return self.__copy_database_files(temp_dir)
        return True, "当前数据库类型非 PostgreSQL / SQLite，跳过数据库备份"

    def __copy_database_files(self, temp_dir: Path) -> Tuple[bool, str]:
        """
        用 SQLite 官方备份接口生成一致快照（user.db）。

        直接复制正在使用的数据库文件可能拿到撕裂页；``Connection.backup`` 会在读事务
        内生成自包含副本，不需要再复制 -wal/-shm。

        :param temp_dir: 备份临时目录
        :return: (是否成功, 结果信息)
        """
        db_file = Path(settings.CONFIG_PATH) / "user.db"
        # 先做 checkpoint（文件不存在时该调用本身是空操作），
        # 保证后续任何读库方式看到的都是已落盘状态
        self.__checkpoint_sqlite(db_file)
        if not db_file.exists():
            return True, "数据库文件备份成功（未找到 user.db）"
        target = temp_dir / "user.db"
        source = None
        dest = None
        try:
            try:
                # 一致快照：读事务内复制页，避免拿到撕裂的数据库文件
                source = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True, timeout=10)
                dest = sqlite3.connect(str(target))
                source.backup(dest)
                dest.close()
                dest = None
                source.close()
                source = None
                mode = "SQLite 一致快照"
            except sqlite3.DatabaseError as e:
                # 极小概率：库文件本身不可读（如仍有进程独占或非 SQLite 格式），
                # 退回按文件复制，至少保住主库文件
                logger.warning(f"SQLite 一致快照失败，退回文件复制：{e}")
                for conn in (dest, source):
                    if conn is not None:
                        try:
                            conn.close()
                        except Exception:
                            pass
                source = dest = None
                if target.exists():
                    target.unlink()
                shutil.copy2(db_file, target)
                mode = "文件复制（快照接口不可用）"
            try:
                os.chmod(target, 0o600)
            except OSError:
                pass
            return True, f"数据库文件备份成功（{mode} user.db）"
        except Exception as e:
            logger.error(f"数据库文件备份失败: {e}")
            try:
                if target.exists():
                    target.unlink()
            except OSError:
                pass
            return False, f"数据库文件备份失败: {e}"
        finally:
            for conn in (dest, source):
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

    def __copy_system_files(self, temp_dir: Path) -> Tuple[bool, str]:
        """
        复制「系统配置」部分：/config 根目录下的 app.env 与 category.yaml。

        注意：SQLite 的 user.db 属「数据库」部分，不再混在这里——
        否则勾「系统配置」会把数据库一起带走（v3.2.0 拆分的原因）。

        :param temp_dir: 备份临时目录
        :return: (是否成功, 结果信息)
        """
        config_path = Path(settings.CONFIG_PATH)
        copied = []
        try:
            for name in ("app.env", "category.yaml"):
                src = config_path / name
                if src.exists() and src.is_file():
                    shutil.copy(src, temp_dir)
                    copied.append(name)
            return True, f"系统配置备份成功（{'、'.join(copied) if copied else '无'}）"
        except Exception as e:
            logger.error(f"系统配置备份失败: {e}")
            return False, f"系统配置备份失败: {e}"

    def __copy_cookies(self, temp_dir: Path) -> Tuple[bool, str]:
        """
        复制「站点 Cookie」部分：/config/cookies 目录。

        :param temp_dir: 备份临时目录
        :return: (是否成功, 结果信息)
        """
        cookies = Path(settings.CONFIG_PATH) / "cookies"
        if not cookies.exists() or not cookies.is_dir():
            return True, "无站点 Cookie，跳过"
        try:
            shutil.copytree(cookies, temp_dir / "cookies", dirs_exist_ok=True)
            return True, "站点 Cookie 备份成功"
        except Exception as e:
            logger.error(f"站点 Cookie 备份失败: {e}")
            return False, f"站点 Cookie 备份失败: {e}"

    @staticmethod
    def __checkpoint_sqlite(db_file: Path) -> None:
        """
        对 SQLite 数据库执行 WAL checkpoint（TRUNCATE），把 WAL 内容写回主库文件。

        WAL 模式下最近的事务可能还留在 -wal 里，直接复制 user.db 会拿到不完整
        快照；TRUNCATE 之后主库文件自包含，只复制它即可得到一致状态。
        失败不阻断备份（退化成原来的行为，由还原侧清理残留 WAL 兜底）。

        :param db_file: user.db 路径
        """
        if not db_file.exists():
            return
        conn = None
        try:
            conn = sqlite3.connect(str(db_file), timeout=10)
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            conn.commit()
        except Exception as e:
            logger.warning(f"SQLite WAL checkpoint 失败（按原样复制）: {e}")
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def __copy_plugins(self, temp_dir: Path) -> Tuple[bool, str]:
        """
        复制插件配置目录 /config/plugins（排除缓存与日志）。

        :param temp_dir: 备份临时目录
        :return: (是否成功, 结果信息)
        """
        src = Path(settings.CONFIG_PATH) / "plugins"
        if not src.exists():
            return True, "插件配置目录不存在，跳过"
        try:
            dst = temp_dir / "plugins"
            shutil.copytree(
                src,
                dst,
                ignore=shutil.ignore_patterns(
                    "__pycache__",
                    "*.pyc",
                    "*.log",
                    "temp",
                    "cache",
                    ".cache",
                    # Telegram 等插件的 .session 是账号授权密钥（等同一份登录态）：
                    # 备份包会落在共享目录并长期保留，不能把授权密钥一起打包（2026-10-30 加）
                    "*.session",
                    "*.session-*",
                ),
                dirs_exist_ok=True,
            )
            size = sum(f.stat().st_size for f in dst.rglob("*") if f.is_file())
            return True, f"插件配置备份成功（{StringUtils.str_filesize(size)}）"
        except Exception as e:
            logger.error(f"插件配置备份失败: {e}")
            return False, f"插件配置备份失败: {e}"

    def __copy_extra_paths(self, temp_dir: Path) -> Tuple[bool, str]:
        """
        复制附加备份路径，并写入原始路径清单（供还原使用）。

        :param temp_dir: 备份临时目录
        :return: (是否成功, 结果信息)
        """
        failed = []
        copied = 0
        extra_dir = temp_dir / "extra"
        manifest_lines = []
        used_names: Dict[str, int] = {}
        for index, raw in enumerate(self._extra_paths.splitlines(), start=1):
            raw = raw.strip()
            if not raw:
                continue
            # 支持 "路径|排除模式1,模式2"：附加路径里常混着缓存/日志，
            # 全量 copytree 会把它撑成几个 G。
            path_part, _, excl_part = raw.partition("|")
            line = path_part.strip()
            excludes = [p.strip() for p in excl_part.split(",") if p.strip()]
            src = Path(line)
            if not src.exists():
                failed.append(line)
                continue
            try:
                # 同名不同路径才加序号前缀，避免互相覆盖；唯一名保持可读
                used = used_names.get(src.name, 0) + 1
                used_names[src.name] = used
                folder = f"{index:04d}_{src.name}" if used > 1 else src.name
                target = extra_dir / folder
                if src.is_dir():
                    shutil.copytree(
                        src,
                        target,
                        dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(*excludes) if excludes else None,
                    )
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, target)
                # 清单记录「目录名 + 原始路径」，还原时按目录名找回对应副本
                manifest_lines.append(f"{folder}\t{src}")
                copied += 1
            except Exception as e:
                logger.warning(f"附加路径备份失败 {line}: {e}")
                failed.append(line)
        # 写入原始路径清单，供还原时恢复到原位置
        if manifest_lines:
            try:
                extra_dir.mkdir(parents=True, exist_ok=True)
                (extra_dir / "extra_paths.txt").write_text(
                    "\n".join(manifest_lines), encoding="utf-8"
                )
            except Exception as e:
                logger.warning(f"写入附加路径清单失败: {e}")
        if failed:
            return False, f"附加路径备份部分失败（成功 {copied}，失败 {len(failed)}：{', '.join(failed[:3])}）"
        if copied:
            return True, f"附加路径备份成功（{copied} 项）"
        return True, "无附加路径"

    def __clean_old_backups(self, bk_path: Path, protect: Optional[set] = None) -> int:
        """
        按保留个数清理最旧的备份文件（支持加密包，可保护指定文件）。

        :param bk_path: 备份目录
        :param protect: 不允许删除的文件名集合（还原中的待还原包）
        :return: 实际删除数量
        """
        if not self._keep_count or self._keep_count <= 0:
            return 0
        files = self.__iter_local_backups(bk_path)
        protected = {str(name) for name in (protect or ())}
        candidates = [p for p in files if p.name not in protected]
        del_cnt = len(files) - int(self._keep_count)
        if del_cnt <= 0:
            return 0
        # 保留天数保护：该天数内的一律不删。两个条件是「满足其一即保留」——
        # 高频定时 + 小 keep_count 时，光靠个数会把最近几小时的备份也清光。
        keep_days = int(self._keep_days or 0)
        if keep_days > 0:
            cutoff = time.time() - keep_days * 86400
            old_enough = 0
            for path in files:
                if self.__backup_time(str(path)) >= cutoff:
                    break
                old_enough += 1
            del_cnt = min(del_cnt, old_enough)
        if del_cnt <= 0:
            return 0
        logger.info(
            f"备份文件数量 {len(files)}，保留 {self._keep_count} 份 / {keep_days} 天，需删除 {del_cnt} 份"
        )
        deleted = 0
        for path in candidates[:del_cnt]:
            try:
                path.unlink()
                deleted += 1
                logger.debug(f"删除旧备份 {path} 成功")
            except Exception as e:
                logger.error(f"删除旧备份 {path} 失败: {e}")
        return deleted
        return del_cnt
