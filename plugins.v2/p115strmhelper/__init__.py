import sqlite3
import subprocess
import threading
from datetime import datetime, timedelta
from threading import Event as ThreadEvent
from typing import Any, List, Dict, Tuple, Self, cast
from errno import EIO, ENOENT
from urllib.parse import quote, unquote, urlsplit
from pathlib import Path

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Request, Response
import requests
from requests.exceptions import HTTPError
from orjson import dumps, loads
from cachetools import cached, TTLCache
from .p115rsacipher import decrypt, encrypt
from .p115updatedb_query import iter_children, get_path, get_pickcode, id_to_path

from app import schemas
from app.core.config import settings
from app.core.event import eventmanager, Event
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import TransferInfo, FileItem
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
    解析数据库，生成 STRM 文件
    """

    def __init__(self, dbfile: str):
        self.dbfile = dbfile
        self.connection = sqlite3.connect(dbfile)
        self.path_list = []
        self.rmt_mediaext = [
            ".mp4",
            ".mkv",
            ".ts",
            ".iso",
            ".rmvb",
            ".avi",
            ".mov",
            ".mpeg",
            ".mpg",
            ".wmv",
            ".3gp",
            ".asf",
            ".m4v",
            ".flv",
            ".m2ts",
            ".tp",
            ".f4v",
        ]

    def get_id_by_path(self, path):
        """
        通过文件夹路径获取文件夹 ID
        """
        return id_to_path(self.connection, path, False)

    def get_video_file_path(self, parent_id: int):
        """
        获取视频文件路径
        """
        for attr in iter_children(self.connection, parent_id):
            if attr["is_dir"] == 1:
                self.get_video_file_path(attr["id"])
            else:
                path = get_path(self.connection, attr["id"])
                file_parent_id = attr["id"]
                self.path_list.append([path, file_parent_id])
        return self.path_list

    def generate_strm_files_db(self, pan_media_dir, target_dir, server_address):
        """
        依据数据库生成 STRM 文件
        """
        parent_id = self.get_id_by_path(pan_media_dir)
        if parent_id != 0:
            removal_path = get_path(self.connection, parent_id)
        else:
            removal_path = ""
        path_list = self.get_video_file_path(parent_id)

        target_dir = target_dir.rstrip("/")
        server_address = server_address.rstrip("/")

        for file_path, file_parent_id in path_list:
            file_path = Path(target_dir) / Path(file_path).relative_to(removal_path)
            file_target_dir = file_path.parent
            original_file_name = file_path.name
            file_name = file_path.stem + ".strm"
            new_file_path = file_target_dir / file_name

            if file_path.suffix not in self.rmt_mediaext:
                logger.warn(
                    "跳过网盘路径: %s", str(file_path).replace(str(target_dir), "", 1)
                )
                continue

            pickcode = get_pickcode(self.connection, file_parent_id)
            new_file_path.parent.mkdir(parents=True, exist_ok=True)

            if not pickcode:
                logger.error(
                    f"{original_file_name} 不存在 pickcode 值，无法生成 STRM 文件"
                )
                continue
            if not (len(pickcode) == 17 and str(pickcode).isalnum()):
                logger.error(f"错误的 pickcode 值 {pickcode}，无法生成 STRM 文件")
                continue
            strm_url = f"{server_address}/api/v1/plugin/P115StrmHelper/redirect_url?apikey={settings.API_TOKEN}&pickcode={pickcode}"

            with open(new_file_path, "w", encoding="utf-8") as file:
                file.write(strm_url)
            logger.info("生成 STRM 文件成功: %s", str(new_file_path))


class P115StrmHelper(_PluginBase):
    # 插件名称
    plugin_name = "115网盘STRM助手"
    # 插件描述
    plugin_desc = "115网盘STRM生成一条龙服务"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/jxxghp/MoviePilot-Frontend/refs/heads/v2/src/assets/images/misc/u115.png"
    # 插件版本
    plugin_version = "0.0.4"
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
    _scheduler = None
    _enabled = False
    _once_full_sync_strm = False
    cookies = None
    pan_media_dir = None
    local_media_dir = None
    moviepilot_address = None
    # 退出事件
    _event = ThreadEvent()

    def __init__(self):
        """
        初始化
        """
        super().__init__()
        # 类名小写
        class_name = self.__class__.__name__.lower()
        self.__config_path = settings.PLUGIN_DATA_PATH / class_name
        self.__db_filename = "file_list.db"

    def init_plugin(self, config: dict = None):
        """
        初始化插件
        """
        Path(self.__config_path).mkdir(parents=True, exist_ok=True)

        if config:
            self._enabled = config.get("enabled")
            self._once_full_sync_strm = config.get("once_full_sync_strm")
            self.cookies = config.get("cookies")
            self.pan_media_dir = config.get("pan_media_dir")
            self.local_media_dir = config.get("local_media_dir")
            self.moviepilot_address = config.get("moviepilot_address")
            self.__update_config()

        # 停止现有任务
        self.stop_service()

        if self._once_full_sync_strm:
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

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        """
        url: {server_url}/api/v1/plugin/P115StrmHelper/redirect_url?apikey={APIKEY}&pickcode={PICKCODE}
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
                                "props": {"cols": 12, "md": 6},
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
                                "props": {"cols": 12, "md": 6},
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
                                            "model": "cookies",
                                            "label": "115 Cookie",
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
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "local_media_dir",
                                            "label": "本地 STRM 媒体库路径",
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
                                            "model": "pan_media_dir",
                                            "label": "115 网盘媒体库路径",
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
            "pan_media_dir": "",
            "local_media_dir": "",
        }

    def get_page(self) -> List[dict]:
        pass

    def __update_config(self):
        self.update_config(
            {
                "enabled": self._enabled,
                "once_full_sync_strm": self._once_full_sync_strm,
                "cookies": self.cookies,
                "moviepilot_address": self.moviepilot_address,
                "pan_media_dir": self.pan_media_dir,
                "local_media_dir": self.local_media_dir,
            }
        )

    @cached(cache=TTLCache(maxsize=1, ttl=2 * 60))
    def redirect_url(
        self,
        request: Request,
        pickcode: str = "",
        file_name: str = "",
        app: str = "",
    ):
        """
        115网盘302跳转
        """

        def check_response(resp: requests.Response) -> requests.Response:
            """
            检查 HTTP 响应，如果状态码 ≥ 400 则抛出 HTTPError
            """
            if resp.status_code >= 400:
                raise HTTPError(
                    f"HTTP Error {resp.status_code}: {resp.text}", response=resp
                )
            return resp

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
                    headers={"User-Agent": user_agent, "Cookie": self.cookies},
                )
            else:
                resp = requests.post(
                    f"http://proapi.115.com/{app or 'android'}/2.0/ufile/download",
                    data={
                        "data": encrypt(f'{{"pick_code":"{pickcode}"}}').decode("utf-8")
                    },
                    headers={"User-Agent": user_agent, "Cookie": self.cookies},
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

        if not pickcode:
            logger.debug("Missing pickcode parameter")
            return "Missing pickcode parameter"

        if not (len(pickcode) == 17 and pickcode.isalnum()):
            logger.debug(f"Bad pickcode: {pickcode} {file_name}")
            return f"Bad pickcode: {pickcode} {file_name}"

        user_agent = request.headers.get("User-Agent") or b""
        logger.debug(f"获取到客户端UA: {user_agent}")

        try:
            url = get_downurl(pickcode.lower(), user_agent, app=app)
            logger.info(f"获取 115 下载地址成功: {url}")
        except Exception as e:
            logger.error(f"获取 115 下载地址失败: {e}")

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
                if pan_path.startswith(pan_media_dir):
                    pan_path = pan_path[len(pan_media_dir) :].lstrip("/").lstrip("\\")
                file_path = Path(target_dir) / pan_path
                file_name = basename + ".strm"
                new_file_path = file_path / file_name
                new_file_path.parent.mkdir(parents=True, exist_ok=True)
                with open(new_file_path, "w", encoding="utf-8") as file:
                    file.write(url)
                logger.info("生成 STRM 文件成功: %s", str(new_file_path))
                return True
            except Exception as e:  # noqa: F841
                logger.error("生成 %s 文件失败: %s", str(new_file_path), e)
                return False

        if (
            not self._enabled
            or not self.local_media_dir
            or not self.pan_media_dir
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

        if not itemdir_dest_path.startswith(self.pan_media_dir):
            logger.debug(f"{item_dest_name} 路径匹配不符合，跳过整理")
            return

        if item_bluray:
            logger.warning(
                f"{item_dest_name} 为蓝光原盘，不支持生成 STRM 文件: {item_dest_path}"
            )
            return

        if not item_dest_pickcode:
            logger.error(f"{item_dest_name} 不存在 pickcode 值，无法生成 STRM 文件")
            return
        if not (len(item_dest_pickcode) == 17 and str(item_dest_pickcode).isalnum()):
            logger.error(f"错误的 pickcode 值 {item_dest_name}，无法生成 STRM 文件")
            return
        strm_url = f"{self.moviepilot_address.rstrip('/')}/api/v1/plugin/P115StrmHelper/redirect_url?apikey={settings.API_TOKEN}&pickcode={item_dest_pickcode}"

        if not generate_strm_files(
            self.local_media_dir,
            self.pan_media_dir,
            item_dest_path,
            item_dest_basename,
            strm_url,
        ):
            return

        # TODO 生成后调用主程序刮削

    def full_sync_strm_files(self):
        """
        全量同步
        """
        try:
            result = subprocess.run(
                [
                    f"{str(Path(self.__config_path) / 'p115dbhelper')}",
                    f"--cookies={self.cookies}",
                    f"--dbfile_path={str(Path(self.__config_path) / self.__db_filename)}",
                    f"--media_path={self.pan_media_dir}",
                ],
                check=True,
            )
            logger.info(result.stdout)
        except subprocess.CalledProcessError as e:
            logger.error(f"批量拉取网盘文件结构失败: {e}")
            return
        except Exception as e:
            logger.error(f"批量拉取网盘文件结构失败: {e}")
            return

        strm_helper = FullSyncStrmHelper(
            str(Path(self.__config_path) / self.__db_filename)
        )
        strm_helper.generate_strm_files_db(
            self.pan_media_dir, self.local_media_dir, self.moviepilot_address
        )

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
        except Exception as e:
            print(str(e))
