import threading
import sys
import time
from collections.abc import Mapping
from datetime import datetime, timedelta
from threading import Event as ThreadEvent
from typing import Any, List, Dict, Tuple, Self, cast, Optional
from errno import EIO, ENOENT
from urllib.parse import quote, unquote, urlsplit, urlencode
from pathlib import Path

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import Request, Response
import requests
from requests.exceptions import HTTPError
from orjson import dumps, loads
from cachetools import cached, TTLCache
from p115client import P115Client
from p115client.tool.iterdir import iter_files_with_path, get_path_to_cid, share_iterdir
from p115client.tool.life import iter_life_behavior_list
from p115rsacipher import encrypt, decrypt

from app import schemas
from app.core.config import settings
from app.core.event import eventmanager, Event
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import TransferInfo, FileItem, RefreshMediaItem, ServiceInfo
from app.core.context import MediaInfo
from app.helper.mediaserver import MediaServerHelper
from app.schemas.types import EventType
from app.utils.system import SystemUtils


p115strmhelper_lock = threading.Lock()


class Url(str):
    def __new__(cls, val: Any = "", /, *args, **kwds):
        return super().__new__(cls, val)

    def __init__(self, val: Any = "", /, *args, **kwds):
        self.__dict__.update(*args, **kwds)

    def __getattr__(self, attr: str, /):
        try:
            return self.__dict__[attr]
        except KeyError as e:
            raise AttributeError(attr) from e

    def __getitem__(self, key, /):
        try:
            if isinstance(key, str):
                return self.__dict__[key]
        except KeyError:
            return super().__getitem__(key)  # type: ignore

    def __repr__(self, /) -> str:
        cls = type(self)
        if (module := cls.__module__) == "__main__":
            name = cls.__qualname__
        else:
            name = f"{module}.{cls.__qualname__}"
        return f"{name}({super().__repr__()}, {self.__dict__!r})"

    @classmethod
    def of(cls, val: Any = "", /, ns: None | dict = None) -> Self:
        self = cls.__new__(cls, val)
        if ns is not None:
            self.__dict__ = ns
        return self

    def get(self, key, /, default=None):
        return self.__dict__.get(key, default)

    def items(self, /):
        return self.__dict__.items()

    def keys(self, /):
        return self.__dict__.keys()

    def values(self, /):
        return self.__dict__.values()


class FullSyncStrmHelper:
    """
    全量生成 STRM 文件
    """

    def __init__(
        self,
        cookies: str,
        user_rmt_mediaext: str,
        server_address: str,
    ):
        self.rmt_mediaext = [
            f".{ext.strip()}" for ext in user_rmt_mediaext.replace("，", ",").split(",")
        ]
        self.client = P115Client(cookies)
        self.count = 0
        self.server_address = server_address.rstrip("/")

    def generate_strm_files(self, full_sync_strm_paths):
        """
        生成 STRM 文件
        """
        media_paths = full_sync_strm_paths.split("\n")
        for path in media_paths:
            if not path:
                continue
            parts = path.split("#", 1)
            pan_media_dir = parts[1]
            target_dir = parts[0]

            try:
                parent_id = int(self.client.fs_dir_getid(pan_media_dir)["id"])
                logger.info(f"【全量STRM生成】网盘媒体目录 ID 获取成功: {parent_id}")
            except Exception as e:
                logger.error(f"【全量STRM生成】网盘媒体目录 ID 获取失败: {e}")
                return False

            try:
                for item in iter_files_with_path(
                    self.client, cid=parent_id, cooldown=2
                ):
                    if item["is_dir"] or item["is_directory"]:
                        continue
                    file_path = item["path"]
                    file_path = Path(target_dir) / Path(file_path).relative_to(
                        pan_media_dir
                    )
                    file_target_dir = file_path.parent
                    original_file_name = file_path.name
                    file_name = file_path.stem + ".strm"
                    new_file_path = file_target_dir / file_name

                    if file_path.suffix not in self.rmt_mediaext:
                        logger.warn(
                            "【全量STRM生成】跳过网盘路径: %s",
                            str(file_path).replace(str(target_dir), "", 1),
                        )
                        continue

                    pickcode = item["pickcode"]
                    if not pickcode:
                        pickcode = item["pick_code"]

                    new_file_path.parent.mkdir(parents=True, exist_ok=True)

                    if not pickcode:
                        logger.error(
                            f"【全量STRM生成】{original_file_name} 不存在 pickcode 值，无法生成 STRM 文件"
                        )
                        continue
                    if not (len(pickcode) == 17 and str(pickcode).isalnum()):
                        logger.error(
                            f"【全量STRM生成】错误的 pickcode 值 {pickcode}，无法生成 STRM 文件"
                        )
                        continue
                    strm_url = f"{self.server_address}/api/v1/plugin/P115StrmHelper/redirect_url?apikey={settings.API_TOKEN}&pickcode={pickcode}"

                    with open(new_file_path, "w", encoding="utf-8") as file:
                        file.write(strm_url)
                    self.count += 1
                    logger.info(
                        "【全量STRM生成】生成 STRM 文件成功: %s", str(new_file_path)
                    )
            except Exception as e:
                logger.error(f"【全量STRM生成】全量生成 STRM 文件失败: {e}")
                return False
        logger.info(
            f"【全量STRM生成】全量生成 STRM 文件完成，总共生成 {self.count} 个文件"
        )
        return True


class ShareStrmHelper:
    """
    根据分享生成STRM
    """

    def __init__(
        self,
        cookies: str,
        user_rmt_mediaext: str,
        share_media_path: str,
        local_media_path: str,
        server_address: str,
    ):
        self.rmt_mediaext = [
            f".{ext.strip()}" for ext in user_rmt_mediaext.replace("，", ",").split(",")
        ]
        self.client = P115Client(cookies)
        self.count = 0
        self.share_media_path = share_media_path
        self.local_media_path = local_media_path
        self.server_address = server_address.rstrip("/")

    def has_prefix(self, full_path, prefix_path):
        """
        判断路径是否包含
        """
        full = Path(full_path).parts
        prefix = Path(prefix_path).parts

        if len(prefix) > len(full):
            return False

        return full[: len(prefix)] == prefix

    def generate_strm_files(
        self,
        share_code: str,
        receive_code: str,
        file_id: str,
        file_path: str,
    ):
        """
        生成 STRM 文件
        """
        if not self.has_prefix(file_path, self.share_media_path):
            logger.debug(
                "【分享STRM生成】此文件不在用户设置分享目录下，跳过网盘路径: %s",
                str(file_path).replace(str(self.local_media_path), "", 1),
            )
            return
        file_path = Path(self.local_media_path) / Path(file_path).relative_to(
            self.share_media_path
        )
        file_target_dir = file_path.parent
        original_file_name = file_path.name
        file_name = file_path.stem + ".strm"
        new_file_path = file_target_dir / file_name

        if file_path.suffix not in self.rmt_mediaext:
            logger.warn(
                "【分享STRM生成】文件后缀不匹配，跳过网盘路径: %s",
                str(file_path).replace(str(self.local_media_path), "", 1),
            )
            return

        new_file_path.parent.mkdir(parents=True, exist_ok=True)

        if not file_id:
            logger.error(
                f"【分享STRM生成】{original_file_name} 不存在 id 值，无法生成 STRM 文件"
            )
            return
        if not share_code:
            logger.error(
                f"【分享STRM生成】{original_file_name} 不存在 share_code 值，无法生成 STRM 文件"
            )
            return
        if not receive_code:
            logger.error(
                f"【分享STRM生成】{original_file_name} 不存在 receive_code 值，无法生成 STRM 文件"
            )
            return
        strm_url = f"{self.server_address}/api/v1/plugin/P115StrmHelper/redirect_url?apikey={settings.API_TOKEN}&share_code={share_code}&receive_code={receive_code}&id={file_id}"

        with open(new_file_path, "w", encoding="utf-8") as file:
            file.write(strm_url)
        self.count += 1
        logger.info("【分享STRM生成】生成 STRM 文件成功: %s", str(new_file_path))

    def get_share_list_creata_strm(
        self,
        cid: int = 0,
        current_path: str = "",
        share_code: str = "",
        receive_code: str = "",
    ):
        """
        获取分享文件，生成 STRM
        """
        for item in share_iterdir(
            self.client, receive_code=receive_code, share_code=share_code, cid=int(cid)
        ):
            item_path = (
                f"{current_path}/{item['name']}" if current_path else "/" + item["name"]
            )

            if item["is_directory"] or item["is_dir"]:
                self.get_share_list_creata_strm(
                    cid=int(item["id"]),
                    current_path=item_path,
                    share_code=share_code,
                    receive_code=receive_code,
                )
            else:
                item_with_path = dict(item)
                item_with_path["path"] = item_path
                self.generate_strm_files(
                    share_code=share_code,
                    receive_code=receive_code,
                    file_id=item_with_path["id"],
                    file_path=item_with_path["path"],
                )

    def get_generate_total(self):
        """
        输出总共生成文件个数
        """
        logger.info(
            f"【分享STRM生成】分享生成 STRM 文件完成，总共生成 {self.count} 个文件"
        )


class P115StrmHelper(_PluginBase):
    # 插件名称
    plugin_name = "115网盘STRM助手"
    # 插件描述
    plugin_desc = "115网盘STRM生成一条龙服务"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/jxxghp/MoviePilot-Frontend/refs/heads/v2/src/assets/images/misc/u115.png"
    # 插件版本
    plugin_version = "1.2.0"
    # 插件作者
    plugin_author = "DDSRem"
    # 作者主页
    author_url = "https://github.com/DDSRem"
    # 插件配置项ID前缀
    plugin_config_prefix = "p115strmhelper_"
    # 加载顺序
    plugin_order = 99
    # 可使用的用户级别
    auth_level = 1

    # 私有属性
    mediaserver_helper = None
    _scheduler = None
    _enabled = False
    _once_full_sync_strm = False
    _cookies = None
    moviepilot_address = None
    _user_rmt_mediaext = None
    _transfer_monitor_enabled = False
    _transfer_monitor_paths = None
    _transfer_monitor_mediaservers = None
    _transfer_monitor_media_server_refresh_enabled = False
    _timing_full_sync_strm = False
    _cron_full_sync_strm = None
    _full_sync_strm_paths = None
    _mediaservers = None
    _monitor_life_enabled = False
    _monitor_life_paths = None
    _share_strm_enabled = False
    _user_share_code = None
    _user_receive_code = None
    _user_share_pan_path = None
    _user_share_local_path = None
    # 退出事件
    _event = ThreadEvent()
    monitor_stop_event = None
    monitor_life_thread = None

    def init_plugin(self, config: dict = None):
        """
        初始化插件
        """
        self.mediaserver_helper = MediaServerHelper()
        self.monitor_stop_event = threading.Event()

        if config:
            self._enabled = config.get("enabled")
            self._once_full_sync_strm = config.get("once_full_sync_strm")
            self._cookies = config.get("cookies")
            self.moviepilot_address = config.get("moviepilot_address")
            self._user_rmt_mediaext = config.get("user_rmt_mediaext")
            self._transfer_monitor_enabled = config.get("transfer_monitor_enabled")
            self._transfer_monitor_paths = config.get("transfer_monitor_paths")
            self._transfer_monitor_media_server_refresh_enabled = config.get(
                "transfer_monitor_media_server_refresh_enabled"
            )
            self._transfer_monitor_mediaservers = (
                config.get("transfer_monitor_mediaservers") or []
            )
            self._timing_full_sync_strm = config.get("timing_full_sync_strm")
            self._cron_full_sync_strm = config.get("cron_full_sync_strm")
            self._full_sync_strm_paths = config.get("full_sync_strm_paths")
            self._monitor_life_enabled = config.get("monitor_life_enabled")
            self._monitor_life_paths = config.get("monitor_life_paths")
            self._share_strm_enabled = config.get("share_strm_enabled")
            self._user_share_code = config.get("user_share_code")
            self._user_receive_code = config.get("user_receive_code")
            self._user_share_pan_path = config.get("user_share_pan_path")
            self._user_share_local_path = config.get("user_share_local_path")
            if not self._user_rmt_mediaext:
                self._user_rmt_mediaext = "mp4,mkv,ts,iso,rmvb,avi,mov,mpeg,mpg,wmv,3gp,asf,m4v,flv,m2ts,tp,f4v"
            if not self._cron_full_sync_strm:
                self._cron_full_sync_strm = "0 */7 * * *"
            if not self._user_share_pan_path:
                self._user_share_pan_path = "/"
            self.__update_config()

        if self.__check_python_version() is False:
            self._enabled, self._once_full_sync_strm = False, False
            self.__update_config()
            return False

        # 停止现有任务
        self.stop_service()

        if self._enabled and self._once_full_sync_strm:
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            self._scheduler.add_job(
                func=self.full_sync_strm_files,
                trigger="date",
                run_date=datetime.now(tz=pytz.timezone(settings.TZ))
                + timedelta(seconds=3),
                name="115网盘助手立刻全量同步",
            )
            self._once_full_sync_strm = False
            self.__update_config()
            if self._scheduler.get_jobs():
                self._scheduler.print_jobs()
                self._scheduler.start()

        if self._enabled and self._share_strm_enabled:
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            self._scheduler.add_job(
                func=self.share_strm_files,
                trigger="date",
                run_date=datetime.now(tz=pytz.timezone(settings.TZ))
                + timedelta(seconds=3),
                name="115网盘助手分享生成STRM",
            )
            self._share_strm_enabled = False
            self.__update_config()
            if self._scheduler.get_jobs():
                self._scheduler.print_jobs()
                self._scheduler.start()

        if self._monitor_life_enabled and self._monitor_life_paths:
            self.monitor_stop_event.clear()
            if self.monitor_life_thread:
                if not self.monitor_life_thread.is_alive():
                    self.monitor_life_thread = threading.Thread(
                        target=self.monitor_life_strm_files, daemon=True
                    )
                    self.monitor_life_thread.start()
            else:
                self.monitor_life_thread = threading.Thread(
                    target=self.monitor_life_strm_files, daemon=True
                )
                self.monitor_life_thread.start()

    def get_state(self) -> bool:
        return self._enabled

    @property
    def service_infos(self) -> Optional[Dict[str, ServiceInfo]]:
        """
        服务信息
        """
        if not self._transfer_monitor_mediaservers:
            logger.warning("尚未配置媒体服务器，请检查配置")
            return None

        services = self.mediaserver_helper.get_services(
            name_filters=self._transfer_monitor_mediaservers
        )
        if not services:
            logger.warning("获取媒体服务器实例失败，请检查配置")
            return None

        active_services = {}
        for service_name, service_info in services.items():
            if service_info.instance.is_inactive():
                logger.warning(f"媒体服务器 {service_name} 未连接，请检查配置")
            else:
                active_services[service_name] = service_info

        if not active_services:
            logger.warning("没有已连接的媒体服务器，请检查配置")
            return None

        return active_services

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        """
        BASE_URL: {server_url}/api/v1/plugin/P115StrmHelper/redirect_url?apikey={APIKEY}
        0. 查询 pickcode
            url: ${BASE_URL}&pickcode=ecjq9ichcb40lzlvx
        1. 带（任意）名字查询 pickcode
            url: ${BASE_URL}&file_name=Novembre.2022.FRENCH.2160p.BluRay.DV.HEVC.DTS-HD.MA.5.1.mkv&pickcode=ecjq9ichcb40lzlvx
        2. 查询分享文件（如果是你自己的分享，则无须提供密码 receive_code）
            url: ${BASE_URL}&share_code=sw68md23w8m&receive_code=q353&id=2580033742990999218
            url: ${BASE_URL}&share_code=sw68md23w8m&id=2580033742990999218
        3. 用 file_name 查询分享文件（直接以路径作为 file_name，且不要有 id 查询参数。如果是你自己的分享，则无须提供密码 receive_code）
            url: ${BASE_URL}&file_name=Cosmos.S01E01.1080p.AMZN.WEB-DL.DD%2B5.1.H.264-iKA.mkv&share_code=sw68md23w8m&receive_code=q353
            url: ${BASE_URL}&file_name=Cosmos.S01E01.1080p.AMZN.WEB-DL.DD%2B5.1.H.264-iKA.mkv&share_code=sw68md23w8m
        """
        return [
            {
                "path": "/redirect_url",
                "endpoint": self.redirect_url,
                "methods": ["GET"],
                "summary": "302跳转",
                "description": "115网盘302跳转",
            }
        ]

    def get_service(self) -> List[Dict[str, Any]]:
        """
        注册插件公共服务
        """
        if (
            self._cron_full_sync_strm
            and self._timing_full_sync_strm
            and self._full_sync_strm_paths
        ):
            return [
                {
                    "id": "P115StrmHelper",
                    "name": "定期全量同步115媒体库",
                    "trigger": CronTrigger.from_crontab(self._cron_full_sync_strm),
                    "func": self.full_sync_strm_files,
                    "kwargs": {},
                }
            ]

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
        """
        return [
            {
                "component": "VForm",
                "content": [
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
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "cookies",
                                            "label": "115 Cookie",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "moviepilot_address",
                                            "label": "MoviePilot 外网访问地址",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "user_rmt_mediaext",
                                            "label": "可整理媒体文件扩展名",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {
                                    "cols": 12,
                                },
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "info",
                                            "variant": "tonal",
                                            "text": "监控MP整理生成STRM配置",
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
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "transfer_monitor_enabled",
                                            "label": "整理事件监控",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "transfer_monitor_media_server_refresh_enabled",
                                            "label": "媒体服务器刷新",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "multiple": True,
                                            "chips": True,
                                            "clearable": True,
                                            "model": "transfer_monitor_mediaservers",
                                            "label": "媒体服务器",
                                            "items": [
                                                {
                                                    "title": config.name,
                                                    "value": config.name,
                                                }
                                                for config in self.mediaserver_helper.get_configs().values()
                                            ],
                                        },
                                    }
                                ],
                            },
                        ],
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
                                            "model": "transfer_monitor_paths",
                                            "label": "整理事件监控目录",
                                            "rows": 5,
                                            "placeholder": "配置格式为 本地STRM媒体库目录#网盘媒体库目录，一行一个",
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
                                "props": {
                                    "cols": 12,
                                },
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "info",
                                            "variant": "tonal",
                                            "text": "全量同步配置",
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
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "once_full_sync_strm",
                                            "label": "立刻全量同步",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "timing_full_sync_strm",
                                            "label": "定期全量同步",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VCronField",
                                        "props": {
                                            "model": "cron_full_sync_strm",
                                            "label": "运行全量同步周期",
                                        },
                                    }
                                ],
                            },
                        ],
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
                                            "model": "full_sync_strm_paths",
                                            "label": "全量同步目录",
                                            "rows": 5,
                                            "placeholder": "配置格式为 本地STRM媒体库目录#网盘媒体库目录，一行一个",
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
                                "props": {
                                    "cols": 12,
                                },
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "info",
                                            "variant": "tonal",
                                            "text": "115生活事件监控配置",
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
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "monitor_life_enabled",
                                            "label": "监控115生活事件",
                                        },
                                    }
                                ],
                            },
                        ],
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
                                            "model": "monitor_life_paths",
                                            "label": "监控115生活事件目录",
                                            "rows": 5,
                                            "placeholder": "配置格式为 本地STRM媒体库目录#网盘媒体库目录，一行一个",
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
                                "props": {
                                    "cols": 12,
                                },
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "info",
                                            "variant": "tonal",
                                            "text": "分享生成STRM配置",
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
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "share_strm_enabled",
                                            "label": "运行分享生成STRM",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "user_share_code",
                                            "label": "分享码",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "user_receive_code",
                                            "label": "分享密码",
                                        },
                                    }
                                ],
                            },
                        ],
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
                                            "model": "user_share_pan_path",
                                            "label": "分享文件夹路径",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "user_share_local_path",
                                            "label": "本地生成STRM路径",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                ],
            },
        ], {
            "enable": False,
            "once_full_sync_strm": False,
            "cookies": "",
            "moviepilot_address": "",
            "user_rmt_mediaext": "mp4,mkv,ts,iso,rmvb,avi,mov,mpeg,mpg,wmv,3gp,asf,m4v,flv,m2ts,tp,f4v",
            "transfer_monitor_enabled": False,
            "transfer_monitor_paths": "",
            "transfer_monitor_media_server_refresh_enabled": False,
            "timing_full_sync_strm": False,
            "cron_full_sync_strm": "0 */7 * * *",
            "full_sync_strm_paths": "",
            "monitor_life_enabled": False,
            "monitor_life_paths": "",
            "share_strm_enabled": False,
            "user_share_code": "",
            "user_receive_code": "",
            "user_share_pan_path": "/",
            "user_share_local_path": "",
        }

    def get_page(self) -> List[dict]:
        pass

    def __update_config(self):
        self.update_config(
            {
                "enabled": self._enabled,
                "once_full_sync_strm": self._once_full_sync_strm,
                "cookies": self._cookies,
                "moviepilot_address": self.moviepilot_address,
                "user_rmt_mediaext": self._user_rmt_mediaext,
                "transfer_monitor_enabled": self._transfer_monitor_enabled,
                "transfer_monitor_paths": self._transfer_monitor_paths,
                "transfer_monitor_media_server_refresh_enabled": self._transfer_monitor_media_server_refresh_enabled,
                "transfer_monitor_mediaservers": self._transfer_monitor_mediaservers,
                "timing_full_sync_strm": self._timing_full_sync_strm,
                "cron_full_sync_strm": self._cron_full_sync_strm,
                "full_sync_strm_paths": self._full_sync_strm_paths,
                "monitor_life_enabled": self._monitor_life_enabled,
                "monitor_life_paths": self._monitor_life_paths,
                "share_strm_enabled": self._share_strm_enabled,
                "user_share_code": self._user_share_code,
                "user_receive_code": self._user_receive_code,
                "user_share_pan_path": self._user_share_pan_path,
                "user_share_local_path": self._user_share_local_path,
            }
        )

    @staticmethod
    def __check_python_version() -> bool:
        """
        检查Python版本
        """
        if not (sys.version_info.major == 3 and sys.version_info.minor >= 12):
            logger.error(
                "当前MoviePilot使用的Python版本不支持本插件，请升级到Python 3.12及以上的版本使用！"
            )
            return False
        return True

    def has_prefix(self, full_path, prefix_path):
        """
        判断路径是否包含
        """
        full = Path(full_path).parts
        prefix = Path(prefix_path).parts

        if len(prefix) > len(full):
            return False

        return full[: len(prefix)] == prefix

    def __get_media_path(self, paths, media_path):
        """
        获取媒体目录路径
        """
        media_paths = paths.split("\n")
        for path in media_paths:
            if not path:
                continue
            parts = path.split("#", 1)
            if self.has_prefix(media_path, parts[1]):
                return True, parts[0], parts[1]
        return False, None, None

    @cached(cache=TTLCache(maxsize=1, ttl=2 * 60))
    def redirect_url(
        self,
        request: Request,
        pickcode: str = "",
        file_name: str = "",
        id: int = 0,
        share_code: str = "",
        receive_code: str = "",
        app: str = "",
    ):
        """
        115网盘302跳转
        """

        def get_first(m: Mapping, *keys, default=None):
            for k in keys:
                if k in m:
                    return m[k]
            return default

        def check_response(resp: requests.Response) -> requests.Response:
            """
            检查 HTTP 响应，如果状态码 ≥ 400 则抛出 HTTPError
            """
            if resp.status_code >= 400:
                raise HTTPError(
                    f"HTTP Error {resp.status_code}: {resp.text}", response=resp
                )
            return resp

        def share_get_id_for_name(
            share_code: str,
            receive_code: str,
            name: str,
            parent_id: int = 0,
        ) -> int:
            api = "http://web.api.115.com/share/search"
            payload = {
                "share_code": share_code,
                "receive_code": receive_code,
                "search_value": name,
                "cid": parent_id,
                "limit": 1,
                "type": 99,
            }
            suffix = name.rpartition(".")[-1]
            if suffix.isalnum():
                payload["suffix"] = suffix
            resp = requests.get(
                f"{api}?{urlencode(payload)}", headers={"Cookie": self._cookies}
            )
            check_response(resp)
            json = loads(cast(bytes, resp.content))
            if get_first(json, "errno", "errNo") == 20021:
                payload.pop("suffix")
                resp = requests.get(
                    f"{api}?{urlencode(payload)}", headers={"Cookie": self._cookies}
                )
                check_response(resp)
                json = loads(cast(bytes, resp.content))
            if not json["state"] or not json["data"]["count"]:
                raise FileNotFoundError(ENOENT, json)
            info = json["data"]["list"][0]
            if info["n"] != name:
                raise FileNotFoundError(ENOENT, f"name not found: {name!r}")
            id = int(info["fid"])
            return id

        def get_receive_code(share_code: str) -> str:
            resp = requests.get(
                f"http://web.api.115.com/share/shareinfo?share_code={share_code}",
                headers={"Cookie": self._cookies},
            )
            check_response(resp)
            json = loads(cast(bytes, resp.content))
            if not json["state"]:
                raise FileNotFoundError(ENOENT, json)
            receive_code = json["data"]["receive_code"]
            return receive_code

        def get_downurl(
            pickcode: str,
            user_agent: str = "",
            app: str = "android",
        ) -> Url:
            """
            获取下载链接
            """
            if app == "chrome":
                resp = requests.post(
                    "http://proapi.115.com/app/chrome/downurl",
                    data={
                        "data": encrypt(f'{{"pickcode":"{pickcode}"}}').decode("utf-8")
                    },
                    headers={"User-Agent": user_agent, "Cookie": self._cookies},
                )
            else:
                resp = requests.post(
                    f"http://proapi.115.com/{app or 'android'}/2.0/ufile/download",
                    data={
                        "data": encrypt(f'{{"pick_code":"{pickcode}"}}').decode("utf-8")
                    },
                    headers={"User-Agent": user_agent, "Cookie": self._cookies},
                )
            check_response(resp)
            json = loads(cast(bytes, resp.content))
            if not json["state"]:
                raise OSError(EIO, json)
            data = json["data"] = loads(decrypt(json["data"]))
            if app == "chrome":
                info = next(iter(data.values()))
                url_info = info["url"]
                if not url_info:
                    raise FileNotFoundError(ENOENT, dumps(json).decode("utf-8"))
                url = Url.of(url_info["url"], info)
            else:
                data["file_name"] = unquote(
                    urlsplit(data["url"]).path.rpartition("/")[-1]
                )
                url = Url.of(data["url"], data)
            return url

        def get_share_downurl(
            share_code: str,
            receive_code: str,
            file_id: int,
            app: str = "",
        ) -> Url:
            payload = {
                "share_code": share_code,
                "receive_code": receive_code,
                "file_id": file_id,
            }
            if app:
                resp = requests.get(
                    f"http://proapi.115.com/{app}/2.0/share/downurl?{urlencode(payload)}",
                    headers={"Cookie": self._cookies},
                )
            else:
                resp = requests.post(
                    "http://proapi.115.com/app/share/downurl",
                    data={"data": encrypt(dumps(payload)).decode("utf-8")},
                    headers={"Cookie": self._cookies},
                )
            check_response(resp)
            json = loads(cast(bytes, resp.content))
            if not json["state"]:
                if json.get("errno") == 4100008:
                    receive_code = get_receive_code(share_code)
                    return get_share_downurl(share_code, receive_code, file_id, app=app)
                raise OSError(EIO, json)
            if app:
                data = json["data"]
            else:
                data = json["data"] = loads(decrypt(json["data"]))
            if not (data and (url_info := data["url"])):
                raise FileNotFoundError(ENOENT, json)
            data["file_id"] = data.pop("fid")
            data["file_name"] = data.pop("fn")
            data["file_size"] = int(data.pop("fs"))
            url = Url.of(url_info["url"], data)
            return url

        if share_code:
            try:
                if not receive_code:
                    receive_code = get_receive_code(share_code)
                elif len(receive_code) != 4:
                    return f"Bad receive_code: {receive_code}"
                if not id:
                    if file_name:
                        id = share_get_id_for_name(
                            share_code,
                            receive_code,
                            file_name,
                        )
                if not id:
                    return f"Please specify id or name: share_code={share_code!r}"
                url = get_share_downurl(share_code, receive_code, id, app=app)
                logger.info(f"【302跳转服务】获取 115 下载地址成功: {url}")
            except Exception as e:
                logger.error(f"【302跳转服务】获取 115 下载地址失败: {e}")
                return f"获取 115 下载地址失败: {e}"
        else:
            if not pickcode:
                logger.debug("【302跳转服务】Missing pickcode parameter")
                return "Missing pickcode parameter"

            if not (len(pickcode) == 17 and pickcode.isalnum()):
                logger.debug(f"【302跳转服务】Bad pickcode: {pickcode} {file_name}")
                return f"Bad pickcode: {pickcode} {file_name}"

            user_agent = request.headers.get("User-Agent") or b""
            logger.debug(f"【302跳转服务】获取到客户端UA: {user_agent}")

            try:
                url = get_downurl(pickcode.lower(), user_agent, app=app)
                logger.info(f"【302跳转服务】获取 115 下载地址成功: {url}")
            except Exception as e:
                logger.error(f"【302跳转服务】获取 115 下载地址失败: {e}")
                return f"获取 115 下载地址失败: {e}"

        return Response(
            status_code=302,
            headers={
                "Location": url,
                "Content-Disposition": f'attachment; filename="{quote(url["file_name"])}"',
            },
            media_type="application/json; charset=utf-8",
            content=dumps({"status": "redirecting", "url": url}),
        )

    @eventmanager.register(EventType.TransferComplete)
    def generate_strm(self, event: Event):
        """
        监控目录整理生成 STRM 文件
        """

        def generate_strm_files(
            target_dir: Path,
            pan_media_dir: Path,
            item_dest_path: Path,
            basename: str,
            url: str,
        ):
            """
            依据网盘路径生成 STRM 文件
            """
            try:
                pan_media_dir = str(Path(pan_media_dir))
                pan_path = Path(item_dest_path).parent
                pan_path = str(Path(pan_path))
                if self.has_prefix(pan_path, pan_media_dir):
                    pan_path = pan_path[len(pan_media_dir) :].lstrip("/").lstrip("\\")
                file_path = Path(target_dir) / pan_path
                file_name = basename + ".strm"
                new_file_path = file_path / file_name
                new_file_path.parent.mkdir(parents=True, exist_ok=True)
                with open(new_file_path, "w", encoding="utf-8") as file:
                    file.write(url)
                logger.info(
                    "【监控整理STRM生成】生成 STRM 文件成功: %s", str(new_file_path)
                )
                return True, new_file_path
            except Exception as e:  # noqa: F841
                logger.error(
                    "【监控整理STRM生成】生成 %s 文件失败: %s", str(new_file_path), e
                )
                return False, None

        if (
            not self._enabled
            or not self._transfer_monitor_enabled
            or not self._transfer_monitor_paths
            or not self.moviepilot_address
        ):
            return

        item = event.event_data
        if not item:
            return

        # 转移信息
        item_transfer: TransferInfo = item.get("transferinfo")

        item_dest_storage: FileItem = item_transfer.target_item.storage
        if item_dest_storage != "u115":
            return

        # 网盘目的地目录
        itemdir_dest_path: FileItem = item_transfer.target_diritem.path
        # 网盘目的地路径（包含文件名称）
        item_dest_path: FileItem = item_transfer.target_item.path
        # 网盘目的地文件名称
        item_dest_name: FileItem = item_transfer.target_item.name
        # 网盘目的地文件名称（不包含后缀）
        item_dest_basename: FileItem = item_transfer.target_item.basename
        # 网盘目的地文件 pickcode
        item_dest_pickcode: FileItem = item_transfer.target_item.pickcode
        # 是否蓝光原盘
        item_bluray = SystemUtils.is_bluray_dir(Path(itemdir_dest_path))

        __itemdir_dest_path, local_media_dir, pan_media_dir = self.__get_media_path(
            self._transfer_monitor_paths, itemdir_dest_path
        )
        if not __itemdir_dest_path:
            logger.debug(
                f"【监控整理STRM生成】{item_dest_name} 路径匹配不符合，跳过整理"
            )
            return
        logger.debug("【监控整理STRM生成】匹配到网盘文件夹路径: %s", str(pan_media_dir))

        if item_bluray:
            logger.warning(
                f"【监控整理STRM生成】{item_dest_name} 为蓝光原盘，不支持生成 STRM 文件: {item_dest_path}"
            )
            return

        if not item_dest_pickcode:
            logger.error(
                f"【监控整理STRM生成】{item_dest_name} 不存在 pickcode 值，无法生成 STRM 文件"
            )
            return
        if not (len(item_dest_pickcode) == 17 and str(item_dest_pickcode).isalnum()):
            logger.error(
                f"【监控整理STRM生成】错误的 pickcode 值 {item_dest_name}，无法生成 STRM 文件"
            )
            return
        strm_url = f"{self.moviepilot_address.rstrip('/')}/api/v1/plugin/P115StrmHelper/redirect_url?apikey={settings.API_TOKEN}&pickcode={item_dest_pickcode}"

        status, strm_target_path = generate_strm_files(
            target_dir=local_media_dir,
            pan_media_dir=pan_media_dir,
            item_dest_path=item_dest_path,
            basename=item_dest_basename,
            url=strm_url,
        )
        if not status:
            return

        if self._transfer_monitor_media_server_refresh_enabled:
            if not self.service_infos:
                return

            time.sleep(2)
            mediainfo: MediaInfo = item.get("mediainfo")
            items = [
                RefreshMediaItem(
                    title=mediainfo.title,
                    year=mediainfo.year,
                    type=mediainfo.type,
                    category=mediainfo.category,
                    target_path=Path(strm_target_path),
                )
            ]

            for name, service in self.service_infos.items():
                if hasattr(service.instance, "refresh_library_by_items"):
                    service.instance.refresh_library_by_items(items)
                elif hasattr(service.instance, "refresh_root_library"):
                    service.instance.refresh_root_library()
                else:
                    logger.warning(f"【监控整理STRM生成】{name} 不支持刷新")

    def full_sync_strm_files(self):
        """
        全量同步
        """
        if not self._full_sync_strm_paths or not self.moviepilot_address:
            return

        strm_helper = FullSyncStrmHelper(
            user_rmt_mediaext=self._user_rmt_mediaext,
            cookies=self._cookies,
            server_address=self.moviepilot_address,
        )
        strm_helper.generate_strm_files(
            full_sync_strm_paths=self._full_sync_strm_paths,
        )

    def share_strm_files(self):
        """
        分享生成STRM
        """
        if (
            not self._user_share_code
            or not self._user_receive_code
            or not self._user_share_pan_path
            or not self._user_share_local_path
            or not self.moviepilot_address
        ):
            return

        try:
            strm_helper = ShareStrmHelper(
                user_rmt_mediaext=self._user_rmt_mediaext,
                cookies=self._cookies,
                server_address=self.moviepilot_address,
                share_media_path=self._user_share_pan_path,
                local_media_path=self._user_share_local_path,
            )
            strm_helper.get_share_list_creata_strm(
                cid=0,
                share_code=self._user_share_code,
                receive_code=self._user_receive_code,
            )
            strm_helper.get_generate_total()
        except Exception as e:
            logger.error(f"【分享STRM生成】运行失败: {e}")
            return

    def monitor_life_strm_files(self):
        """
        监控115生活事件
        """
        client = P115Client(self._cookies)
        rmt_mediaext = [
            f".{ext.strip()}"
            for ext in self._user_rmt_mediaext.replace("，", ",").split(",")
        ]
        logger.info("【监控生活事件】上传事件监控启动中...")
        try:
            for events_batch in iter_life_behavior_list(client, cooldown=int(10)):
                if self.monitor_stop_event.is_set():
                    logger.info("【监控生活事件】收到停止信号，退出上传事件监控")
                    break
                if not events_batch:
                    time.sleep(10)
                    continue
                for event in events_batch:
                    if (
                        int(event["type"]) != 1
                        and int(event["type"]) != 2
                        and int(event["type"]) != 6
                    ):
                        continue
                    pickcode = event["pick_code"]
                    file_name = event["file_name"]
                    file_path = (
                        Path(get_path_to_cid(client, cid=int(event["parent_id"])))
                        / file_name
                    )
                    status, target_dir, pan_media_dir = self.__get_media_path(
                        self._monitor_life_paths, file_path
                    )
                    if not status:
                        continue
                    logger.debug(
                        "【监控生活事件】匹配到网盘文件夹路径: %s", str(pan_media_dir)
                    )
                    file_path = Path(target_dir) / Path(file_path).relative_to(
                        pan_media_dir
                    )
                    file_target_dir = file_path.parent
                    original_file_name = file_path.name
                    file_name = file_path.stem + ".strm"
                    new_file_path = file_target_dir / file_name

                    if file_path.suffix not in rmt_mediaext:
                        logger.warn(
                            "【监控生活事件】跳过网盘路径: %s",
                            str(file_path).replace(str(target_dir), "", 1),
                        )
                        continue

                    new_file_path.parent.mkdir(parents=True, exist_ok=True)

                    if not pickcode:
                        logger.error(
                            f"【监控生活事件】{original_file_name} 不存在 pickcode 值，无法生成 STRM 文件"
                        )
                        continue
                    if not (len(pickcode) == 17 and str(pickcode).isalnum()):
                        logger.error(
                            f"【监控生活事件】错误的 pickcode 值 {pickcode}，无法生成 STRM 文件"
                        )
                        continue
                    strm_url = f"{self.moviepilot_address}/api/v1/plugin/P115StrmHelper/redirect_url?apikey={settings.API_TOKEN}&pickcode={pickcode}"

                    with open(new_file_path, "w", encoding="utf-8") as file:
                        file.write(strm_url)
                    logger.info(
                        "【监控生活事件】生成 STRM 文件成功: %s", str(new_file_path)
                    )
        except Exception as e:
            logger.error(f"【监控生活事件】上传事件监控运行失败: {e}")
            return
        logger.info("【监控生活事件】已退出上传事件监控")
        return

    def stop_service(self):
        """
        退出插件
        """
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._event.set()
                    self._scheduler.shutdown()
                    self._event.clear()
                self._scheduler = None
            self.monitor_stop_event.set()
        except Exception as e:
            print(str(e))
