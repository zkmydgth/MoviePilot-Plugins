import glob
import json
import os
import re
import shutil
import sqlite3
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
    _notify = False
    _notify_type = "插件"
    _onlyonce = False

    # 还原操作锁（防止并发还原）
    _restore_lock = threading.Lock()
    # 备份操作锁（防止「立即运行一次」后台线程与定时任务/手动触发撞车）
    _backup_lock = threading.Lock()

    # 备份文件名前缀
    _prefix = "bk_"

    # 待确认状态有效期（秒）：超时自动作废，避免隔天回来仍挂着「确认还原/确认删除」按钮
    _pending_ttl = 600

    def init_plugin(self, config: dict = None):
        """根据插件配置初始化运行状态。"""
        # 停止现有任务
        self.stop_service()

        self._enabled = False
        if config:
            self._enabled = bool(config.get("enabled"))
            self._cron = config.get("cron") or ""
            self._backup_dir = config.get("backup_dir") or ""
            self._keep_count = int(config.get("keep_count") or 10)
            self._keep_days = int(config.get("keep_days") or 0)
            self._backup_parts = self.__parse_parts(config)
            self._extra_paths = config.get("extra_paths") or ""
            self._webdav_enabled = bool(config.get("webdav_enabled"))
            self._webdav_url = config.get("webdav_url") or ""
            self._webdav_user = config.get("webdav_user") or ""
            self._webdav_pass = config.get("webdav_pass") or ""
            self._webdav_dir = (config.get("webdav_dir") or "/MoviePilot").strip()
            self._webdav_keep = config.get("webdav_keep")
            self._webdav_timeout = int(config.get("webdav_timeout") or 300)
            self._notify = bool(config.get("notify"))
            self._notify_type = config.get("notify_type") or "插件"
            self._onlyonce = bool(config.get("onlyonce"))

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
        """注册插件公共服务。"""
        if self._enabled and self._cron:
            return [{
                "id": "ConfigBackup",
                "name": "配置备份定时服务",
                "trigger": CronTrigger.from_crontab(self._cron),
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
                                                    "支持按个数与天数保留、自动清理与一键还原（还原前自动先备份当前状态作安全网）。"
                                                    "备份内容可在下方「备份内容」中勾选，默认全选即全量。"
                                                    "数据库：PostgreSQL 以 SQL 方式备份还原（兼容 10+ 含 18.x）；"
                                                    "SQLite 以数据库文件（user.db）方式备份并归入「数据库」项（备份前自动 checkpoint 落盘）；"
                                                    "其它类型跳过数据库部分。还原为覆盖式：备份包中含有的配置会回到备份那一刻，"
                                                    "备份后新增的插件配置与站点 Cookie 将被清除。首次使用建议先手动备份一次验证。",
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
                                            "hint": "目录可写 \"路径|*.log,node_modules,cache\" 排除不需要的内容（逗号分隔）",
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
                                "props": {"cols": 12, "md": 9},
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "warning",
                                            "variant": "tonal",
                                            "text": "注意：备份包含站点 Cookie 与数据库，默认不加密就上传到网盘 —— 请确认该 WebDAV 服务端可信。",
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
            "webdav_enabled": False,
            "webdav_url": "",
            "webdav_user": "",
            "webdav_pass": "",
            "webdav_dir": "/MoviePilot",
            "webdav_keep": "",
            "webdav_timeout": 300,
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
        result = self.api_restore(filename=path.name)
        if result.get("success"):
            result["message"] = f"已下载并选中：{path.name}。{result.get('message', '')}"
        return result

    def api_backup(self) -> Dict[str, Any]:
        """API：手动触发配置备份。"""
        success, msg = self.__backup()
        return {"success": success, "message": msg, "data": None}

    def api_list(self) -> Dict[str, Any]:
        """API：获取备份文件列表。"""
        bk_path = Path(self._backup_dir) if self._backup_dir else self.get_data_path()
        return {"success": True, "message": "获取成功", "data": self.__list_backups(bk_path)}

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
        # （bk_ 前缀 + .zip 后缀），避免出现"删除比还原更宽松"的口子。
        safe_name = Path(filename).name
        if safe_name != filename or not safe_name.startswith(self._prefix) \
                or not safe_name.endswith(".zip"):
            return {"success": False, "message": "非法文件名", "data": None}
        bk_path = Path(self._backup_dir) if self._backup_dir else self.get_data_path()
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

    def api_restore(self, filename: str = "", confirm: str = "") -> Dict[str, Any]:
        """
        API：配置还原（两阶段确认）。

        - filename 为空且 confirm 为空：选中待还原备份（写入待确认状态）
        - confirm=1：执行还原
        - confirm=cancel：取消待确认还原
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
                    # 还原前自动备份当前状态（安全网）
                    bk_ok, bk_msg = self.__backup()
                    if not bk_ok:
                        # 完全还原会先删后写，安全网没兜住就不许动——
                        # 否则一次失败的安全网 + 一次失败的还原 = 什么都没了。
                        self.__set_pending_restore(None)
                        return {
                            "success": False,
                            "message": f"还原前安全备份失败，已中止还原（未改动任何配置）：{bk_msg}",
                            "data": None,
                        }
                    # 执行还原
                    ok, msg = self.__restore(zip_path)
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
            if safe_name != filename or not safe_name.startswith(self._prefix) \
                    or not safe_name.endswith(".zip"):
                return {"success": False, "message": "非法文件名", "data": None}
            zip_path = self.__resolve_backup_path(safe_name)
            if not zip_path or not zip_path.exists():
                return {"success": False, "message": "备份文件不存在", "data": None}
            # 校验 zip 完整性
            try:
                with zipfile.ZipFile(zip_path, "r") as zf:
                    bad = zf.testzip()
                if bad:
                    return {"success": False, "message": f"备份文件已损坏（{bad}）", "data": None}
            except Exception as e:
                return {"success": False, "message": f"无法读取备份文件: {e}", "data": None}
            ctime = datetime.fromtimestamp(os.path.getctime(zip_path)).strftime("%Y-%m-%d %H:%M:%S")
            # 选中还原时，清掉待删除状态，避免两个确认按钮同时出现造成误操作
            self.__set_pending_delete(None)
            self.__set_pending_restore({
                "filename": safe_name,
                "time": ctime,
                "size": os.path.getsize(zip_path),
                "summary": self.__summarize(str(zip_path)),
            })
            logger.info(f"已选择待还原备份 {safe_name}")
            return {"success": True, "message": f"已选择备份 {safe_name}，请点击页面顶部的【确认还原】按钮执行还原", "data": None}
        except Exception as e:
            logger.error(f"还原操作失败: {e}")
            return {"success": False, "message": f"还原操作失败: {e}", "data": None}

    def __resolve_backup_path(self, filename: str) -> Optional[Path]:
        """
        将备份文件名解析为备份目录下的绝对路径（防路径穿越）。

        :param filename: 备份文件名
        :return: 绝对路径或 None
        """
        safe_name = Path(filename).name
        if safe_name != filename or not safe_name.startswith(self._prefix) \
                or not safe_name.endswith(".zip"):
            return None
        bk_path = Path(self._backup_dir) if self._backup_dir else self.get_data_path()
        return bk_path / safe_name

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
        执行配置还原：解压备份包，按顺序还原数据库、系统配置、插件配置与附加路径。

        :param zip_path: 备份包路径
        :return: (是否成功, 结果信息)
        """
        msgs = []
        restore_dir: Optional[Path] = None
        try:
            # 校验并解压
            with zipfile.ZipFile(zip_path, "r") as zf:
                bad = zf.testzip()
                if bad:
                    return False, f"备份文件损坏（{bad}）"
            restore_dir = Path(tempfile.mkdtemp(
                prefix="configbackup_restore_",
                dir=settings.TEMP_PATH if settings.TEMP_PATH else None,
            ))
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(restore_dir)
            logger.info(f"备份包已解压: {restore_dir}")

            # 1. 数据库还原（可回滚，先执行）
            sql_file = restore_dir / "postgresql_backup.sql"
            if sql_file.exists():
                if settings.DB_TYPE == "postgresql":
                    db_ok, db_msg = self.__restore_database(sql_file)
                    msgs.append(db_msg)
                    if not db_ok:
                        return False, "；".join(msgs)
                else:
                    msgs.append("备份含数据库 SQL，但当前数据库非 PostgreSQL，跳过数据库还原")
            else:
                msgs.append("备份中无数据库文件，跳过")

            # 2. 系统配置文件还原
            # 先清掉目标端残留的 -wal/-shm：旧 WAL 与新主库混在一起会读出错乱数据，
            # 而还原侧只写回 user.db 本身（备份时已 checkpoint，主库自包含）。
            for suffix in ("-wal", "-shm"):
                stale = Path(settings.CONFIG_PATH) / f"user.db{suffix}"
                if stale.exists():
                    try:
                        stale.unlink()
                        logger.info(f"已清除残留数据库文件 {stale.name}")
                    except Exception as e:
                        logger.warning(f"清除残留数据库文件失败 {stale.name}: {e}")
            cfg_restored = []
            for name in ("app.env", "category.yaml", "user.db"):
                src = restore_dir / name
                if src.exists() and src.is_file():
                    shutil.copy(src, Path(settings.CONFIG_PATH) / name)
                    cfg_restored.append(name)
            cookies_src = restore_dir / "cookies"
            if cookies_src.exists() and cookies_src.is_dir():
                cookies_dst = Path(settings.CONFIG_PATH) / "cookies"
                self.__overlay(cookies_src, cookies_dst)
                cfg_restored.append("cookies")
            if cfg_restored:
                msgs.append(f"系统配置文件还原成功（{'、'.join(cfg_restored)}）")
            else:
                msgs.append("备份中无系统配置文件，跳过")

            # 3. 插件配置还原
            plugins_src = restore_dir / "plugins"
            if plugins_src.exists() and plugins_src.is_dir():
                plugins_dst = Path(settings.CONFIG_PATH) / "plugins"
                n = self.__overlay(plugins_src, plugins_dst)
                msgs.append(f"插件配置还原成功（覆盖 {n} 项）")
            else:
                msgs.append("备份中无插件配置，跳过")

            # 4. 附加路径还原（按备份时记录的清单）
            extra_dir = restore_dir / "extra"
            manifest = extra_dir / "extra_paths.txt"
            if manifest.exists() and manifest.is_file():
                extra_ok, extra_msg = self.__restore_extra_paths(extra_dir, manifest)
                msgs.append(extra_msg)
                if not extra_ok:
                    return False, "；".join(msgs)
            else:
                msgs.append("备份中无附加路径，跳过")

            # 5. 清理临时目录
            shutil.rmtree(restore_dir, ignore_errors=True)
            restore_dir = None

            msgs.append("还原完成，请重启 MoviePilot 使配置完全生效")
            logger.info("；".join(msgs))
            return True, "；".join(msgs)
        except Exception as e:
            logger.error(f"还原失败: {e}")
            return False, f"还原失败: {e}"
        finally:
            if restore_dir and restore_dir.exists():
                shutil.rmtree(restore_dir, ignore_errors=True)

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

    def __restore_database(self, sql_file: Path) -> Tuple[bool, str]:
        """
        执行数据库还原：解析备份 SQL（pg_dump 标准格式），逐表重建并导入数据。

        :param sql_file: 备份 SQL 文件
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
            lines = sql_file.read_text(encoding="utf-8").splitlines()
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

    def __restore_extra_paths(self, extra_dir: Path, manifest: Path) -> Tuple[bool, str]:
        """
        按备份清单还原附加路径到原始位置。

        :param extra_dir: 备份包内 extra 目录
        :param manifest: 原始路径清单文件
        :return: (是否成功, 结果信息)
        """
        restored = 0
        failed = []
        try:
            for line in manifest.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                src = extra_dir / Path(line).name
                if not src.exists():
                    failed.append(line)
                    continue
                try:
                    if src.is_dir():
                        self.__overlay(src, Path(line))
                    else:
                        Path(line).parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy(src, line)
                    restored += 1
                except Exception as e:
                    logger.warning(f"附加路径还原失败 {line}: {e}")
                    failed.append(line)
            if failed:
                return False, f"附加路径还原部分失败（成功 {restored}，失败 {len(failed)}：{', '.join(failed[:3])}）"
            if restored:
                return True, f"附加路径还原成功（{restored} 项）"
            return True, "附加路径清单为空，跳过"
        except Exception as e:
            logger.error(f"附加路径还原失败: {e}")
            return False, f"附加路径还原失败: {e}"

    def __list_backups(self, bk_path: Path) -> List[Dict[str, Any]]:
        """
        获取备份目录下所有备份文件列表（按创建时间倒序）。

        :param bk_path: 备份目录
        :return: 备份文件信息列表
        """
        result = []
        if not bk_path.exists():
            return result
        files = sorted(
            glob.glob(f"{bk_path}/{self._prefix}*.zip"),
            key=self.__backup_time,
            reverse=True,
        )
        for f in files:
            result.append({
                "name": Path(f).name,
                "path": f,
                "size": os.path.getsize(f),
                "time": datetime.fromtimestamp(self.__backup_time(f)).strftime("%Y-%m-%d %H:%M:%S"),
                "summary": self.__summarize(f),
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
            timeout=self._webdav_timeout,
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
        远端条目的时间戳：优先解析文件名 ``bk_<14位时间>.zip``，其次 HTTP 日期。

        与本地一致 —— 文件名时间戳最稳（跨时区、跨客户端都一致），
        服务端返回的修改时间只是兜底。

        :param entry: 远端列表项
        :return: 时间戳（秒），取不到给 0
        """
        name = str(entry.get("name") or "")
        matched = re.match(rf"^{re.escape(self._prefix)}(\d{{14}})\.zip$", name)
        if matched:
            try:
                return datetime.strptime(matched.group(1), "%Y%m%d%H%M%S").timestamp()
            except Exception:
                pass
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
            if entry.get("is_dir") or not name.startswith(self._prefix) or not name.endswith(".zip"):
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

        :param name: 远端文件名（``bk_*.zip``）
        :return: 本地文件路径
        """
        safe_name = Path(name).name
        if safe_name != name or not safe_name.startswith(self._prefix) \
                or not safe_name.endswith(".zip"):
            raise WebDAVError("非法文件名")
        bk_path = Path(self._backup_dir) if self._backup_dir else self.get_data_path()
        bk_path.mkdir(parents=True, exist_ok=True)
        target = bk_path / safe_name
        size = self.__webdav_client().download(self.__remote_path(safe_name), target)
        if not self.__verify_zip(str(target)):
            # 损坏包必须删掉：留着会占保留名额，还会让用户以为它可用
            try:
                target.unlink()
            except Exception:
                pass
            raise WebDAVError("下载的备份包校验失败（可能不完整），已删除")
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

    def __backup(self) -> Tuple[bool, str]:
        """
        执行配置备份（对外入口）：包装 __run_backup，统一负责通知。

        成功与失败都会按配置发通知——备份失败恰恰是最需要被感知的事件，
        只在成功分支通知会让定时备份的失败彻底静默（只剩日志）。

        :return: (是否成功, 结果信息)
        """
        success, msg = self.__run_backup()
        if self._notify:
            try:
                self.post_message(
                    mtype=self.__notify_mtype(),
                    title="【配置备份完成】" if success else "【配置备份失败】",
                    text=msg,
                )
            except Exception as e:
                logger.error(f"发送备份通知失败: {e}")
        return success, msg

    def __write_manifest(self, temp_dir: Path) -> None:
        """
        在备份包内写入清单：这个包是什么时候、在什么环境下、装了哪些内容。

        没有它，用户只能靠文件名猜——选错包还原的代价是配置被覆盖。
        老备份包本来就没有这个文件，读取方必须容忍缺失。

        :param temp_dir: 备份临时目录
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
        }
        try:
            (temp_dir / "backup_manifest.json").write_text(
                json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"写入备份清单失败: {e}")

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

    @staticmethod
    def __summarize(zip_path: str) -> str:
        """
        把清单压成一行摘要，用于列表展示。

        :param zip_path: 备份包路径
        :return: 摘要文本
        """
        data = ConfigBackup.__read_manifest(Path(zip_path))
        if not data:
            return "老备份包（无清单）"
        selected = data.get("parts")
        if not selected:
            # 老包（v3.1.1 及以前没有 parts 字段）：按包里实际有什么展示
            return ConfigBackup.__summarize_legacy(data)
        labels = []
        for part in ConfigBackup._ALL_PARTS:
            if part not in selected:
                continue
            if part == ConfigBackup._PART_DATABASE:
                labels.append(f"数据库({data.get('db_type') or '未知'})")
            elif part == ConfigBackup._PART_EXTRA:
                count = data.get("extra_count") or 0
                labels.append(f"附加×{count}" if count else "附加路径")
            else:
                labels.append(ConfigBackup._PART_LABELS[part])
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
        取备份文件的时间戳：优先解析文件名里的 bk_<YYYYmmddHHMMSS>.zip。

        不用 ctime——文件被 rsync / 拷贝到本机后 ctime 会变成拷贝时间，
        按它排序会把最新备份当成最旧的删掉。解析不出再退回 mtime。

        :param f: 备份文件路径
        :return: 时间戳（秒）
        """
        m = re.match(r"^bk_(\d{14})\.zip$", Path(f).name)
        if m:
            try:
                return datetime.strptime(m.group(1), "%Y%m%d%H%M%S").timestamp()
            except Exception:
                pass
        try:
            return os.path.getmtime(f)
        except Exception:
            return 0.0

    def __run_backup(self) -> Tuple[bool, str]:
        """
        执行配置备份（带并发保护）：同一时刻只允许一个备份任务在跑。

        「立即运行一次」改为后台线程后，可能与定时触发/手动点击撞车，
        并发写同一个临时目录名会互相覆盖，所以这里串行化。

        :return: (是否成功, 结果信息)
        """
        if not self._backup_lock.acquire(blocking=False):
            return False, "已有备份任务正在执行，本次跳过"
        try:
            return self.__do_backup()
        finally:
            self._backup_lock.release()

    def __do_backup(self) -> Tuple[bool, str]:
        """
        执行配置备份：按勾选的备份内容逐项收集、压缩、自检与清理。

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

            # 6. 写入清单（记录这个包含什么，供还原前确认选中的是哪一份）
            self.__write_manifest(temp_dir)

            # 7. 压缩：先在临时区成包，再整体移入备份目录（网盘场景更快，也更原子）
            shutil.make_archive(str(temp_dir), "zip", str(temp_dir))
            shutil.rmtree(str(temp_dir), ignore_errors=True)
            shutil.move(str(temp_dir) + ".zip", zip_file)

            # 6.1 自检：损坏的包改名隔离，避免占保留名额、被当成可还原备份
            if not self.__verify_zip(zip_file):
                broken = zip_file + ".broken"
                try:
                    os.replace(zip_file, broken)
                except Exception as e:
                    logger.error(f"隔离损坏备份失败 {zip_file}: {e}")
                msgs.append("备份包完整性校验失败，已隔离为 .broken")
                return False, "；".join(msgs)
            zip_size = os.path.getsize(zip_file)
            msgs.append(f"备份完成：{backup_name}.zip（{StringUtils.str_filesize(zip_size)}）")
            success = True

            # 6.2 上传到 WebDAV：必须在包自检通过之后（§7.3）；
            # 上传失败算整次失败（通知标红），但文案已写明本地包可用
            if self._webdav_enabled:
                upload_ok, upload_msg = self.__upload_to_webdav(Path(zip_file))
                msgs.append(upload_msg)
                success = success and upload_ok
        except Exception as e:
            logger.error(f"创建备份失败: {e}")
            shutil.rmtree(str(temp_dir), ignore_errors=True)
            leftover = Path(str(temp_dir) + ".zip")
            if leftover.exists():
                leftover.unlink()
            return False, f"创建备份失败: {e}"

        # 7. 清理旧备份
        del_cnt = self.__clean_old_backups(bk_path)
        if del_cnt > 0:
            msgs.append(f"自动清理旧备份 {del_cnt} 份")

        # 7.1 远端清理：只在本次上传成功后做（§7.3），失败不影响本地结果
        if self._webdav_enabled and success:
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
        使用 psycopg2 导出 PostgreSQL 数据库（结构 + 数据）为 SQL 文件。

        :param temp_dir: 备份临时目录
        :return: (是否成功, 结果信息)
        """
        if settings.DB_TYPE != "postgresql":
            return True, "当前数据库非 PostgreSQL，跳过数据库备份"

        sql_file = temp_dir / "postgresql_backup.sql"
        try:
            conn = psycopg2.connect(
                host=str(settings.DB_POSTGRESQL_HOST),
                port=str(settings.DB_POSTGRESQL_PORT),
                user=str(settings.DB_POSTGRESQL_USERNAME),
                password=str(settings.DB_POSTGRESQL_PASSWORD),
                dbname=str(settings.DB_POSTGRESQL_DATABASE),
                connect_timeout=10,
            )
            conn.autocommit = True
            cur = conn.cursor()

            with sql_file.open("w", encoding="utf-8") as f:
                f.write("-- MoviePilot 配置备份（ConfigBackup 插件导出）\n")
                f.write(f"-- 导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("BEGIN;\n\n")

                # 获取全部业务表
                cur.execute(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname='public' ORDER BY tablename"
                )
                tables = [r[0] for r in cur.fetchall()]

                for table in tables:
                    # 生成 CREATE TABLE
                    cur.execute(
                        "SELECT column_name, data_type, udt_name, character_maximum_length, "
                        "numeric_precision, numeric_scale, is_nullable, column_default, "
                        "is_identity, identity_generation "
                        "FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name=%s "
                        "ORDER BY ordinal_position",
                        (table,),
                    )
                    cols = cur.fetchall()
                    if not cols:
                        continue
                    col_defs = []
                    seq_defs = []
                    for col in cols:
                        (name, dtype, udt, clen, nprec, nscale,
                         nullable, default, is_identity, identity_gen) = col
                        # 类型映射
                        if dtype == "USER-DEFINED":
                            ctype = udt
                        elif dtype in ("character varying", "character"):
                            ctype = f"{dtype}({clen})" if clen else dtype
                        elif dtype == "numeric":
                            ctype = f"numeric({nprec},{nscale})" if nprec else "numeric"
                        else:
                            ctype = dtype
                        line = f'  "{name}" {ctype}'
                        # 自增列（identity 或 serial）
                        if is_identity == "YES":
                            line += " GENERATED BY DEFAULT AS IDENTITY"
                        elif default:
                            # 兼容 serial 场景：提取序列定义
                            m = re.search(r"nextval\('([^']+)'::regclass\)", default)
                            if m:
                                seq_defs.append((m.group(1), name))
                            line += f" DEFAULT {default}"
                        if nullable == "NO":
                            line += " NOT NULL"
                        col_defs.append(line)
                    f.write(f'DROP TABLE IF EXISTS "{table}";\n')
                    f.write(f'CREATE TABLE "{table}" (\n' + ",\n".join(col_defs) + "\n);\n")

                    # 导出数据（pg_dump 标准格式：COPY FROM stdin + \. 结束）
                    f.write(f'\n-- 数据：{table}\n')
                    f.write(f'COPY "{table}" FROM stdin;\n')
                    cur.copy_expert(f'COPY "{table}" TO STDOUT', f)
                    f.write('\\.\n\n')

                    # 重建 serial 序列（identity 序列由建表语句自动创建）
                    for seq_name, col_name in seq_defs:
                        f.write(
                            f"CREATE SEQUENCE IF NOT EXISTS {seq_name} "
                            f"OWNED BY \"{table}\".\"{col_name}\";\n"
                        )

                # 索引
                cur.execute(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE schemaname='public' AND indexdef NOT LIKE '% PRIMARY KEY%' "
                    "AND indexdef NOT LIKE '% UNIQUE INDEX%'"
                )
                f.write("\n-- 索引\n")
                for indexname, indexdef in cur.fetchall():
                    f.write(f"{indexdef};\n")

                # 序列值同步
                f.write("\n-- 序列当前值\n")
                cur.execute(
                    "SELECT c.relname AS seq_name, t.relname AS table_name, a.attname AS col_name "
                    "FROM pg_class c "
                    "JOIN pg_depend d ON d.objid = c.oid AND d.classid = 'pg_class'::regclass "
                    "JOIN pg_class t ON t.oid = d.refobjid "
                    "JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = d.refobjsubid "
                    "WHERE c.relkind = 'S'"
                )
                for seq_name, table_name, col_name in cur.fetchall():
                    f.write(
                        f"SELECT setval('{seq_name}', "
                        f"COALESCE((SELECT MAX(\"{col_name}\") FROM \"{table_name}\"), 1));\n"
                    )

                f.write("\nCOMMIT;\n")

            cur.close()
            conn.close()
            logger.info(f"数据库备份成功: {sql_file}")
            return True, "数据库备份成功"
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
        复制 SQLite 数据库文件（user.db 及其 -wal / -shm）。

        复制前先 checkpoint 把 WAL 写回主库；一并复制 -wal/-shm 是为了
        让还原后的 SQLite 能自行回放（主库自包含时它们本就是空的）。

        :param temp_dir: 备份临时目录
        :return: (是否成功, 结果信息)
        """
        config_path = Path(settings.CONFIG_PATH)
        copied = []
        try:
            self.__checkpoint_sqlite(config_path / "user.db")
            for f in config_path.glob("user.db*"):
                if f.is_file():
                    shutil.copy(f, temp_dir)
                    copied.append(f.name)
            return True, f"数据库文件备份成功（{'、'.join(copied) if copied else '未找到 user.db'}）"
        except Exception as e:
            logger.error(f"数据库文件备份失败: {e}")
            return False, f"数据库文件备份失败: {e}"

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
                    "__pycache__", "*.pyc", "*.log", "temp", "cache", ".cache"
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
        for raw in self._extra_paths.splitlines():
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
                target = extra_dir / src.name
                if src.is_dir():
                    shutil.copytree(
                        src,
                        target,
                        dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(*excludes) if excludes else None,
                    )
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy(src, target)
                manifest_lines.append(str(src))
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

    def __clean_old_backups(self, bk_path: Path) -> int:
        """
        按保留个数清理最旧的备份文件。

        :param bk_path: 备份目录
        :return: 删除数量
        """
        if not self._keep_count or self._keep_count <= 0:
            return 0
        files = sorted(glob.glob(f"{bk_path}/{self._prefix}*.zip"), key=self.__backup_time)
        del_cnt = len(files) - int(self._keep_count)
        if del_cnt <= 0:
            return 0
        # 保留天数保护：该天数内的一律不删。两个条件是「满足其一即保留」——
        # 高频定时 + 小 keep_count 时，光靠个数会把最近几小时的备份也清光。
        keep_days = int(self._keep_days or 0)
        if keep_days > 0:
            cutoff = time.time() - keep_days * 86400
            old_enough = 0
            for f in files:
                if self.__backup_time(f) >= cutoff:
                    break
                old_enough += 1
            del_cnt = min(del_cnt, old_enough)
        if del_cnt <= 0:
            return 0
        logger.info(
            f"备份文件数量 {len(files)}，保留 {self._keep_count} 份 / {keep_days} 天，需删除 {del_cnt} 份"
        )
        for i in range(del_cnt):
            try:
                Path(files[i]).unlink()
                logger.debug(f"删除旧备份 {files[i]} 成功")
            except Exception as e:
                logger.error(f"删除旧备份 {files[i]} 失败: {e}")
        return del_cnt
