from typing import Any, List, Dict, Tuple, Self, cast
from errno import EIO, ENOENT
from urllib.parse import quote, unquote, urlsplit
from pathlib import Path
from fastapi import Request, Response
import requests
from requests.exceptions import HTTPError
from orjson import dumps, loads
from .p115rsacipher import decrypt, encrypt

from app import schemas
from app.core.config import settings
from app.core.event import eventmanager, Event
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import TransferInfo, FileItem
from app.schemas.types import EventType
from app.utils.system import SystemUtils


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


class P115StrmHelper(_PluginBase):
    # 插件名称
    plugin_name = "115网盘STRM助手"
    # 插件描述
    plugin_desc = "115网盘STRM生成一条龙服务"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/jxxghp/MoviePilot-Frontend/refs/heads/v2/src/assets/images/misc/u115.png"
    # 插件版本
    plugin_version = "0.0.1"
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
    _enabled = False
    cookies = None
    pan_media_dir = None
    local_media_dir = None
    moviepilot_address = None

    def init_plugin(self, config: dict = None):
        if config:
            self._enabled = config.get("enabled")
            self.cookies = config.get("cookies")
            self.pan_media_dir = config.get("pan_media_dir")
            self.local_media_dir = config.get("local_media_dir")
            self.moviepilot_address = config.get("moviepilot_address")

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
                                            "persistent-hint": True,
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
                                            "model": "moviepilot_address",
                                            "label": "MoviePilot 外网访问地址",
                                            "persistent-hint": True,
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
                                            "persistent-hint": True,
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
                                            "model": "pan_media_dir",
                                            "label": "115 网盘媒体库路径",
                                            "persistent-hint": True,
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
            "cookies": "",
            "moviepilot_address": "",
            "pan_media_dir": "",
            "local_media_dir": "",
        }

    def get_page(self) -> List[dict]:
        pass

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

        url = get_downurl(pickcode.lower(), user_agent, app=app)
        logger.info(f"获取 115 下载地址成功: {url}")

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

        def generate_strm_files(target_dir, pan_media_dir, pan_path, basename, url):
            """
            依据网盘路径生成 STRM 文件
            """
            pan_media_dir = str(Path(pan_media_dir))
            pan_path = str(Path(pan_path))
            if pan_path.startswith(pan_media_dir):
                pan_path = pan_path[len(pan_media_dir) :].lstrip("/").lstrip("\\")
            file_path = Path(target_dir) / pan_path
            file_name = basename + ".strm"
            new_file_path = file_path / file_name
            new_file_path.parent.mkdir(parents=True, exist_ok=True)
            if Path(new_file_path).exists(follow_symlinks=False):
                logger.info(f"更新 STRM 文件: {new_file_path}")
            else:
                logger.info(f"生成 STRM 文件: {new_file_path}")
            try:
                with open(new_file_path, "w", encoding="utf-8") as file:
                    file.write(url)
                logger.info("生成 STRM 文件成功： %s", str(new_file_path))
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

        if item_transfer.storage != "u115":
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
        strm_url = f"{self.moviepilot_address.rstrip('/')}/api/v1/plugin/P115StrmHelper/redirect_url?apikey={settings.API_TOKEN}&pickcode={item_dest_pickcode}"

        if not generate_strm_files(
            self.local_media_dir,
            self.pan_media_dir,
            itemdir_dest_path,
            item_dest_basename,
            strm_url,
        ):
            return

        eventmanager.send_event(
            EventType.MetadataScrape,
            {
                "meta": item.meta,
                "mediainfo": item.mediainfo,
                "fileitem": item_transfer.target_diritem,
            },
        )

    def stop_service(self):
        """
        退出插件
        """
        pass
