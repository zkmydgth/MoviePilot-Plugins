import glob
import json
import os
import re
import shutil
import tempfile
import threading
import time
import zipfile
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.helper.directory import DirectoryHelper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import NotificationType
from app.utils.string import StringUtils


class ConfigBackup(_PluginBase):
    """配置备份插件：定时/手动备份系统配置、数据库与插件配置，支持还原。"""

    # 插件名称
    plugin_name = "配置备份"
    # 插件描述
    plugin_desc = "定时备份 MoviePilot 系统配置、数据库及插件配置到指定目录，支持保留数量自动清理、手动触发和一键还原。"
    # 插件版本
    plugin_version = "1.3.2"
    # 插件作者
    plugin_author = "zkmydgth"
    # 插件配置项ID前缀
    plugin_config_prefix = "configbackup_"
    # 加载顺序
    plugin_order = 30
    # 可使用的用户级别
    auth_level = 1

    # 私有属性
    _enabled = False
    _cron = None
    _backup_dir = None
    _keep_count = 10
    _backup_plugins = True
    _extra_paths = None
    _notify = False
    _onlyonce = False

    # 还原操作锁（防止并发还原）
    _restore_lock = threading.Lock()

    # 备份文件名前缀
    _prefix = "bk_"

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
            self._backup_plugins = bool(config.get("backup_plugins", True))
            self._extra_paths = config.get("extra_paths") or ""
            self._notify = bool(config.get("notify"))
            self._onlyonce = bool(config.get("onlyonce"))

        if self._onlyonce:
            self._onlyonce = False
            self.update_config({
                "enabled": self._enabled,
                "cron": self._cron,
                "backup_dir": self._backup_dir,
                "keep_count": self._keep_count,
                "backup_plugins": self._backup_plugins,
                "extra_paths": self._extra_paths,
                "notify": self._notify,
                "onlyonce": False,
            })
            self.__backup()

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

        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VAlert",
                        "props": {
                            "type": "info",
                            "text": "使用说明：定时备份 MoviePilot 系统配置、PostgreSQL 数据库与插件配置，"
                                    "支持保留最近 N 份与一键还原（还原前自动先备份当前状态作安全网）。"
                                    "注意：数据库备份/还原要求 MoviePilot 使用 PostgreSQL（兼容 10+ 含 18.x），"
                                    "SQLite/MySQL 等将自动跳过数据库部分。首次使用建议先手动备份一次验证。",
                        },
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
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
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "onlyonce",
                                            "label": "立即运行一次",
                                            "hint": "保存配置后立即执行一次备份",
                                        }
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "notify",
                                            "label": "发送通知",
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
                                            "hint": "超过该数量的最旧备份将自动删除",
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
                                        }
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "backup_plugins",
                                            "label": "备份插件配置",
                                            "hint": "同时备份 /config/plugins 插件数据与配置",
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
            "backup_plugins": True,
            "extra_paths": "",
            "notify": False,
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
                    }
                },
            },
        ]
        if pending:
            actions.append(
                {
                    "component": "VBtn",
                    "props": {
                        "color": "grey",
                        "variant": "tonal",
                        "size": "small",
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
                        "text": f"待还原：{pending.get('filename', '')}（备份于 {pending.get('time', '')}）"
                                f"。点击上方【确认还原】执行还原，还原前将自动备份当前状态；"
                                f"点击【取消还原】放弃本次操作。",
                    },
                }
            )

        if not backup_files:
            return [
                {
                    "component": "div",
                    "props": {"class": "pa-4"},
                    "content": header + [
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
                                        "icon": "mdi-restore",
                                        "title": "选择该备份进行还原",
                                    },
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
                                        "icon": "mdi-delete",
                                        "title": "删除该备份",
                                    },
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

    def api_backup(self) -> Dict[str, Any]:
        """API：手动触发配置备份。"""
        success, msg = self.__backup()
        return {"success": success, "message": msg}

    def api_list(self) -> Dict[str, Any]:
        """API：获取备份文件列表。"""
        bk_path = Path(self._backup_dir) if self._backup_dir else self.get_data_path()
        return {"success": True, "data": self.__list_backups(bk_path)}

    def api_delete(self, filename: str = "") -> Dict[str, Any]:
        """API：按文件名删除备份文件。"""
        if not filename:
            return {"success": False, "message": "缺少文件名参数"}
        # 防止路径穿越
        safe_name = Path(filename).name
        if safe_name != filename or not safe_name.startswith(self._prefix):
            return {"success": False, "message": "非法文件名"}
        bk_path = Path(self._backup_dir) if self._backup_dir else self.get_data_path()
        target = bk_path / safe_name
        if not target.exists():
            return {"success": False, "message": "备份文件不存在"}
        try:
            if target.is_file():
                target.unlink()
            elif target.is_dir():
                shutil.rmtree(target)
            logger.info(f"删除备份文件 {target} 成功")
            return {"success": True, "message": f"删除备份 {safe_name} 成功"}
        except Exception as e:
            logger.error(f"删除备份文件 {target} 失败: {e}")
            return {"success": False, "message": f"删除失败: {e}"}

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
                return {"success": True, "message": "已取消还原操作"}

            # 执行还原
            if confirm == "1":
                if not self._restore_lock.acquire(blocking=False):
                    return {"success": False, "message": "已有还原操作正在进行，请稍后再试"}
                try:
                    pending = self.__get_pending_restore()
                    if not pending or not pending.get("filename"):
                        return {"success": False, "message": "没有待还原的备份，请先在列表中选择备份文件"}
                    zip_path = self.__resolve_backup_path(pending["filename"])
                    if not zip_path or not zip_path.exists():
                        self.__set_pending_restore(None)
                        return {"success": False, "message": f"待还原的备份文件不存在：{pending['filename']}"}
                    # 还原前自动备份当前状态（安全网）
                    bk_ok, bk_msg = self.__backup()
                    # 执行还原
                    ok, msg = self.__restore(zip_path)
                    # 无论成败都清除待确认状态
                    self.__set_pending_restore(None)
                    if ok and self._notify:
                        self.post_message(
                            mtype=NotificationType.SiteMessage,
                            title="【配置还原完成】",
                            text=msg,
                        )
                    full_msg = f"还原前已自动备份当前状态（{bk_msg}）。{msg}" if bk_ok else msg
                    return {"success": ok, "message": full_msg}
                finally:
                    self._restore_lock.release()

            # 选择待还原备份
            if not filename:
                return {"success": False, "message": "缺少文件名参数"}
            safe_name = Path(filename).name
            if safe_name != filename or not safe_name.startswith(self._prefix):
                return {"success": False, "message": "非法文件名"}
            zip_path = self.__resolve_backup_path(safe_name)
            if not zip_path or not zip_path.exists():
                return {"success": False, "message": "备份文件不存在"}
            # 校验 zip 完整性
            try:
                with zipfile.ZipFile(zip_path, "r") as zf:
                    bad = zf.testzip()
                if bad:
                    return {"success": False, "message": f"备份文件已损坏（{bad}）"}
            except Exception as e:
                return {"success": False, "message": f"无法读取备份文件: {e}"}
            ctime = datetime.fromtimestamp(os.path.getctime(zip_path)).strftime("%Y-%m-%d %H:%M:%S")
            self.__set_pending_restore({
                "filename": safe_name,
                "time": ctime,
                "size": os.path.getsize(zip_path),
            })
            logger.info(f"已选择待还原备份 {safe_name}")
            return {"success": True, "message": f"已选择备份 {safe_name}，请点击页面顶部的【确认还原】按钮执行还原"}
        except Exception as e:
            logger.error(f"还原操作失败: {e}")
            return {"success": False, "message": f"还原操作失败: {e}"}

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

    def __get_pending_restore(self) -> Optional[Dict[str, Any]]:
        """读取待确认还原状态。"""
        try:
            f = self.__pending_file()
            if f.exists():
                data = json.loads(f.read_text(encoding="utf-8"))
                if data and data.get("filename"):
                    return data
        except Exception as e:
            logger.debug(f"读取待还原状态失败: {e}")
        return None

    def __set_pending_restore(self, data: Optional[Dict[str, Any]]):
        """写入或清除待确认还原状态。"""
        try:
            f = self.__pending_file()
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
            cfg_restored = []
            for name in ("app.env", "category.yaml", "user.db"):
                src = restore_dir / name
                if src.exists() and src.is_file():
                    shutil.copy(src, Path(settings.CONFIG_PATH) / name)
                    cfg_restored.append(name)
            cookies_src = restore_dir / "cookies"
            if cookies_src.exists() and cookies_src.is_dir():
                cookies_dst = Path(settings.CONFIG_PATH) / "cookies"
                cookies_dst.mkdir(parents=True, exist_ok=True)
                shutil.copytree(cookies_src, cookies_dst, dirs_exist_ok=True)
                cfg_restored.append("cookies")
            if cfg_restored:
                msgs.append(f"系统配置文件还原成功（{'、'.join(cfg_restored)}）")
            else:
                msgs.append("备份中无系统配置文件，跳过")

            # 3. 插件配置还原
            plugins_src = restore_dir / "plugins"
            if plugins_src.exists() and plugins_src.is_dir():
                plugins_dst = Path(settings.CONFIG_PATH) / "plugins"
                plugins_dst.mkdir(parents=True, exist_ok=True)
                shutil.copytree(plugins_src, plugins_dst, dirs_exist_ok=True)
                msgs.append("插件配置还原成功")
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
                        shutil.copytree(src, Path(line), dirs_exist_ok=True)
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
            key=os.path.getctime,
            reverse=True,
        )
        for f in files:
            result.append({
                "name": Path(f).name,
                "path": f,
                "size": os.path.getsize(f),
                "time": datetime.fromtimestamp(os.path.getctime(f)).strftime("%Y-%m-%d %H:%M:%S"),
            })
        return result

    def __backup(self) -> Tuple[bool, str]:
        """
        执行配置备份：数据库导出、配置文件复制、插件配置复制、压缩与清理。

        :return: (是否成功, 结果信息)
        """
        logger.info(f"开始配置备份，当前时间 {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}")

        # 备份保存路径
        bk_path = Path(self._backup_dir) if self._backup_dir else self.get_data_path()
        try:
            bk_path.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(f"创建备份目录失败: {bk_path} {e}")
            return False, f"创建备份目录失败: {e}"

        # 临时备份目录
        backup_name = f"{self._prefix}{time.strftime('%Y%m%d%H%M%S')}"
        temp_dir = bk_path / backup_name
        zip_file = str(bk_path / backup_name) + ".zip"
        msgs = []

        try:
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True)

            # 1. 备份数据库
            db_success, db_msg = self.__dump_database(temp_dir)
            msgs.append(db_msg)

            # 2. 备份系统配置文件
            cfg_success, cfg_msg = self.__copy_config_files(temp_dir)
            msgs.append(cfg_msg)

            # 3. 备份插件配置
            plugin_success = True
            if self._backup_plugins:
                plugin_success, plugin_msg = self.__copy_plugins(temp_dir)
                msgs.append(plugin_msg)

            # 4. 备份附加路径
            extra_success = True
            if self._extra_paths:
                extra_success, extra_msg = self.__copy_extra_paths(temp_dir)
                msgs.append(extra_msg)

            if not (db_success and cfg_success and plugin_success and extra_success):
                return False, "；".join(msgs)

            # 5. 压缩
            shutil.make_archive(str(bk_path / backup_name), "zip", str(temp_dir))
            shutil.rmtree(str(temp_dir))
            zip_size = os.path.getsize(zip_file)
            msgs.append(f"备份完成：{backup_name}.zip（{StringUtils.str_filesize(zip_size)}）")
            success = True
        except Exception as e:
            logger.error(f"创建备份失败: {e}")
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
            return False, f"创建备份失败: {e}"

        # 6. 清理旧备份
        del_cnt = self.__clean_old_backups(bk_path)
        if del_cnt > 0:
            msgs.append(f"自动清理旧备份 {del_cnt} 份")

        msg = "；".join(msgs)

        # 7. 发送通知
        if self._notify:
            self.post_message(
                mtype=NotificationType.SiteMessage,
                title="【配置备份完成】",
                text=msg,
            )

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

    def __copy_config_files(self, temp_dir: Path) -> Tuple[bool, str]:
        """
        复制 /config 根目录下的系统配置文件。

        :param temp_dir: 备份临时目录
        :return: (是否成功, 结果信息)
        """
        config_path = Path(settings.CONFIG_PATH)
        copied = []
        try:
            # 数据库文件（SQLite 场景）
            if settings.DB_TYPE == "sqlite":
                for f in config_path.glob("user.db*"):
                    shutil.copy(f, temp_dir)
                    copied.append(f.name)
            # 其他配置文件
            for name in ("app.env", "category.yaml", "user.db"):
                src = config_path / name
                if src.exists() and src.is_file():
                    shutil.copy(src, temp_dir)
                    copied.append(name)
            # cookies 目录
            cookies = config_path / "cookies"
            if cookies.exists() and cookies.is_dir():
                shutil.copytree(cookies, temp_dir / "cookies", dirs_exist_ok=True)
                copied.append("cookies")
            return True, f"系统配置文件备份成功（{'、'.join(copied) if copied else '无'}）"
        except Exception as e:
            logger.error(f"系统配置文件备份失败: {e}")
            return False, f"系统配置文件备份失败: {e}"

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
        for line in self._extra_paths.splitlines():
            line = line.strip()
            if not line:
                continue
            src = Path(line)
            if not src.exists():
                failed.append(line)
                continue
            try:
                target = extra_dir / src.name
                if src.is_dir():
                    shutil.copytree(src, target, dirs_exist_ok=True)
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
        files = sorted(glob.glob(f"{bk_path}/{self._prefix}*.zip"), key=os.path.getctime)
        del_cnt = len(files) - int(self._keep_count)
        if del_cnt <= 0:
            return 0
        logger.info(
            f"备份文件数量 {len(files)}，保留 {self._keep_count}，需删除 {del_cnt} 份"
        )
        for i in range(del_cnt):
            try:
                Path(files[i]).unlink()
                logger.debug(f"删除旧备份 {files[i]} 成功")
            except Exception as e:
                logger.error(f"删除旧备份 {files[i]} 失败: {e}")
        return del_cnt
