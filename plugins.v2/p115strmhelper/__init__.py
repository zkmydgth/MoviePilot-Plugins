import threading
import sys
import time
import shutil
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
from cachetools import cached, TTLCache, LRUCache
from cachetools.keys import hashkey
from p115client import P115Client
from p115client.tool.iterdir import (
    iter_files_with_path,
    get_path_to_cid,
    share_iterdir,
    get_id_to_path,
)
from p115client.tool.life import iter_life_behavior_list
from p115client.tool.util import share_extract_payload
from p115rsacipher import encrypt, decrypt

from app import schemas
from app.schemas import TransferInfo, FileItem, RefreshMediaItem, ServiceInfo
from app.schemas.types import EventType, MediaType
from app.core.config import settings
from app.core.event import eventmanager, Event
from app.core.context import MediaInfo
from app.core.meta import MetaBase
from app.core.metainfo import MetaInfoPath
from app.log import logger
from app.plugins import _PluginBase
from app.chain.transfer import TransferChain
from app.chain.media import MediaChain
from app.helper.mediaserver import MediaServerHelper
from app.utils.system import SystemUtils


p115strmhelper_lock = threading.Lock()


class IdPathCache:
    """
    文件路径ID缓存
    """

    def __init__(self, maxsize=128):
        self.id_to_dir = LRUCache(maxsize=maxsize)
        self.dir_to_id = LRUCache(maxsize=maxsize)

    def add_cache(self, id: int, directory: str):
        """
        添加缓存
        """
        self.id_to_dir[id] = directory
        self.dir_to_id[directory] = id

    def get_dir_by_id(self, id: int):
        """
        通过 ID 获取路径
        """
        return self.id_to_dir.get(id)

    def get_id_by_dir(self, directory: str):
        """
        通过路径获取 ID
        """
        return self.dir_to_id.get(directory)

    def clear(self):
        """
        清空所有缓存
        """
        self.id_to_dir.clear()
        self.dir_to_id.clear()


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


def check_response(
    resp: requests.Response,
) -> requests.Response:
    """
    检查 HTTP 响应，如果状态码 ≥ 400 则抛出 HTTPError
    """
    if resp.status_code >= 400:
        raise HTTPError(
            f"HTTP Error {resp.status_code}: {resp.text}",
            response=resp,
        )
    return resp


def get_download_url(pickcode: str, cookie: str):
    """
    获取下载链接
    """
    resp = requests.post(
        "http://proapi.115.com/android/2.0/ufile/download",
        data={"data": encrypt(f'{{"pick_code":"{pickcode}"}}').decode("utf-8")},
        headers={
            "User-Agent": settings.USER_AGENT,
            "Cookie": cookie,
        },
    )
    check_response(resp)
    json = loads(cast(bytes, resp.content))
    if not json["state"]:
        raise OSError(EIO, json)
    data = json["data"] = loads(decrypt(json["data"]))
    data["file_name"] = unquote(urlsplit(data["url"]).path.rpartition("/")[-1])
    return Url.of(data["url"], data)


def save_mediainfo_file(
    file_path: Path, file_name: str, download_url: str, cookie: str
):
    """
    保存媒体信息文件
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(
        download_url,
        stream=True,
        timeout=30,
        headers={
            "User-Agent": settings.USER_AGENT,
            "Cookie": cookie,
        },
    ) as response:
        response.raise_for_status()
        with open(file_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
    logger.info(f"【媒体信息文件】保存 {file_name} 文件成功: {file_path}")


class FullSyncStrmHelper:
    """
    全量生成 STRM 文件
    """

    def __init__(
        self,
        client,
        user_rmt_mediaext: str,
        user_download_mediaext: str,
        server_address: str,
        cookie: str,
        auto_download_mediainfo: bool = False,
    ):
        self.rmt_mediaext = [
            f".{ext.strip()}" for ext in user_rmt_mediaext.replace("，", ",").split(",")
        ]
        self.download_mediaext = [
            f".{ext.strip()}"
            for ext in user_download_mediaext.replace("，", ",").split(",")
        ]
        self.auto_download_mediainfo = auto_download_mediainfo
        self.client = client
        self.cookie = cookie
        self.strm_count = 0
        self.mediainfo_count = 0
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

                    if self.auto_download_mediainfo:
                        if file_path.suffix in self.download_mediaext:
                            pickcode = item["pickcode"]
                            if not pickcode:
                                logger.error(
                                    f"【全量STRM生成】{original_file_name} 不存在 pickcode 值，无法下载该文件"
                                )
                                continue
                            download_url = get_download_url(
                                pickcode=pickcode, cookie=self.cookie
                            )

                            if not download_url:
                                logger.error(
                                    f"【全量STRM生成】{original_file_name} 下载链接获取失败，无法下载该文件"
                                )
                                continue

                            save_mediainfo_file(
                                file_path=Path(file_path),
                                file_name=original_file_name,
                                download_url=download_url,
                                cookie=self.cookie,
                            )
                            self.mediainfo_count += 1
                            continue

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
                    self.strm_count += 1
                    logger.info(
                        "【全量STRM生成】生成 STRM 文件成功: %s", str(new_file_path)
                    )
            except Exception as e:
                logger.error(f"【全量STRM生成】全量生成 STRM 文件失败: {e}")
                return False
        logger.info(
            f"【全量STRM生成】全量生成 STRM 文件完成，总共生成 {self.strm_count} 个 STRM 文件，下载 {self.mediainfo_count} 个媒体数据文件"
        )
        return True

    def get_generate_total(self):
        """
        输出总共生成文件个数
        """
        return self.strm_count, self.mediainfo_count


class ShareStrmHelper:
    """
    根据分享生成STRM
    """

    def __init__(
        self,
        client,
        user_rmt_mediaext: str,
        user_download_mediaext: str,
        share_media_path: str,
        local_media_path: str,
        server_address: str,
        cookie: str,
        auto_download_mediainfo: bool = False,
    ):
        self.rmt_mediaext = [
            f".{ext.strip()}" for ext in user_rmt_mediaext.replace("，", ",").split(",")
        ]
        self.download_mediaext = [
            f".{ext.strip()}"
            for ext in user_download_mediaext.replace("，", ",").split(",")
        ]
        self.auto_download_mediainfo = auto_download_mediainfo
        self.client = client
        self.strm_count = 0
        self.mediainfo_count = 0
        self.cookie = cookie
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

        if self.auto_download_mediainfo:
            if file_path.suffix in self.download_mediaext:
                payload = {
                    "share_code": share_code,
                    "receive_code": receive_code,
                    "file_id": file_id,
                }
                resp = requests.post(
                    "http://proapi.115.com/app/share/downurl",
                    data={"data": encrypt(dumps(payload)).decode("utf-8")},
                    headers={
                        "User-Agent": settings.USER_AGENT,
                        "Cookie": self.cookie,
                    },
                )
                check_response(resp)
                json = loads(cast(bytes, resp.content))
                if not json["state"]:
                    raise OSError(EIO, json)
                data = json["data"] = loads(decrypt(json["data"]))
                if not (data and (url_info := data["url"])):
                    raise FileNotFoundError(ENOENT, json)
                data["file_id"] = data.pop("fid")
                data["file_name"] = data.pop("fn")
                data["file_size"] = int(data.pop("fs"))
                download_url = Url.of(url_info["url"], data)

                if not download_url:
                    logger.error(
                        f"【分享STRM生成】{original_file_name} 下载链接获取失败，无法下载该文件"
                    )
                    return

                save_mediainfo_file(
                    file_path=Path(file_path),
                    file_name=original_file_name,
                    download_url=download_url,
                    cookie=self.cookie,
                )
                self.mediainfo_count += 1
                logger.info("【分享STRM生成】休眠 1s 后继续生成")
                time.sleep(1)
                return

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
        self.strm_count += 1
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
                if self.strm_count != 0 and self.strm_count % 100 == 0:
                    logger.info("【分享STRM生成】休眠 2s 后继续生成")
                    time.sleep(2)
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
            f"【分享STRM生成】分享生成 STRM 文件完成，总共生成 {self.strm_count} 个 STRM 文件，下载 {self.mediainfo_count} 个媒体数据文件"
        )
        return self.strm_count, self.mediainfo_count


class P115StrmHelper(_PluginBase):
    # 插件名称
    plugin_name = "115网盘STRM助手"
    # 插件描述
    plugin_desc = "115网盘STRM生成一条龙服务"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/jxxghp/MoviePilot-Frontend/refs/heads/v2/src/assets/images/misc/u115.png"
    # 插件版本
    plugin_version = "1.5.2"
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
    transferchain = None
    mediachain = None

    # 目录ID缓存
    id_path_cache = None
    # 生活事件缓存
    cache_delete_pan_transfer_list = None
    cache_creata_pan_transfer_list = None

    _client = None
    _scheduler = None
    _enabled = False
    _once_full_sync_strm = False
    _cookies = None
    _password = None
    moviepilot_address = None
    _user_rmt_mediaext = None
    _user_download_mediaext = None
    _transfer_monitor_enabled = False
    _transfer_monitor_scrape_metadata_enabled = False
    _transfer_monitor_paths = None
    _transfer_mp_mediaserver_paths = None
    _transfer_monitor_mediaservers = None
    _transfer_monitor_media_server_refresh_enabled = False
    _timing_full_sync_strm = False
    _full_sync_auto_download_mediainfo_enabled = False
    _cron_full_sync_strm = None
    _full_sync_strm_paths = None
    _mediaservers = None
    _monitor_life_enabled = False
    _monitor_life_auto_download_mediainfo_enabled = False
    _monitor_life_paths = None
    _monitor_life_mp_mediaserver_paths = None
    _monitor_life_media_server_refresh_enabled = False
    _monitor_life_mediaservers = None
    _monitor_life_auto_remove_local_enabled = False
    _monitor_life_scrape_metadata_enabled = False
    _share_strm_enabled = False
    _share_strm_auto_download_mediainfo_enabled = False
    _user_share_code = None
    _user_receive_code = None
    _user_share_link = None
    _user_share_pan_path = None
    _user_share_local_path = None
    _clear_recyclebin_enabled = False
    _clear_receive_path_enabled = False
    _cron_clear = None
    _pan_transfer_enabled = False
    _pan_transfer_paths = None
    # 退出事件
    _event = ThreadEvent()
    monitor_stop_event = None
    monitor_life_thread = None

    def init_plugin(self, config: dict = None):
        """
        初始化插件
        """
        self.mediaserver_helper = MediaServerHelper()
        self.transferchain = TransferChain()
        self.mediachain = MediaChain()
        self.monitor_stop_event = threading.Event()

        self.id_path_cache = IdPathCache()
        self.cache_delete_pan_transfer_list = []
        self.cache_creata_pan_transfer_list = []

        if config:
            self._enabled = config.get("enabled")
            self._once_full_sync_strm = config.get("once_full_sync_strm")
            self._cookies = config.get("cookies")
            self._password = config.get("password")
            self.moviepilot_address = config.get("moviepilot_address")
            self._user_rmt_mediaext = config.get("user_rmt_mediaext")
            self._user_download_mediaext = config.get("user_download_mediaext")
            self._transfer_monitor_enabled = config.get("transfer_monitor_enabled")
            self._transfer_monitor_scrape_metadata_enabled = config.get(
                "transfer_monitor_scrape_metadata_enabled"
            )
            self._transfer_monitor_paths = config.get("transfer_monitor_paths")
            self._transfer_mp_mediaserver_paths = config.get(
                "transfer_mp_mediaserver_paths"
            )
            self._transfer_monitor_media_server_refresh_enabled = config.get(
                "transfer_monitor_media_server_refresh_enabled"
            )
            self._transfer_monitor_mediaservers = (
                config.get("transfer_monitor_mediaservers") or []
            )
            self._timing_full_sync_strm = config.get("timing_full_sync_strm")
            self._full_sync_auto_download_mediainfo_enabled = config.get(
                "full_sync_auto_download_mediainfo_enabled"
            )
            self._cron_full_sync_strm = config.get("cron_full_sync_strm")
            self._full_sync_strm_paths = config.get("full_sync_strm_paths")
            self._monitor_life_enabled = config.get("monitor_life_enabled")
            self._monitor_life_auto_download_mediainfo_enabled = config.get(
                "monitor_life_auto_download_mediainfo_enabled"
            )
            self._monitor_life_paths = config.get("monitor_life_paths")
            self._monitor_life_mp_mediaserver_paths = config.get(
                "monitor_life_mp_mediaserver_paths"
            )
            self._monitor_life_media_server_refresh_enabled = config.get(
                "monitor_life_media_server_refresh_enabled"
            )
            self._monitor_life_mediaservers = (
                config.get("monitor_life_mediaservers") or []
            )
            self._monitor_life_auto_remove_local_enabled = config.get(
                "monitor_life_auto_remove_local_enabled"
            )
            self._monitor_life_scrape_metadata_enabled = config.get(
                "monitor_life_scrape_metadata_enabled"
            )
            self._share_strm_enabled = config.get("share_strm_enabled")
            self._share_strm_auto_download_mediainfo_enabled = config.get(
                "share_strm_auto_download_mediainfo_enabled"
            )
            self._user_share_code = config.get("user_share_code")
            self._user_receive_code = config.get("user_receive_code")
            self._user_share_link = config.get("user_share_link")
            self._user_share_pan_path = config.get("user_share_pan_path")
            self._user_share_local_path = config.get("user_share_local_path")
            self._clear_recyclebin_enabled = config.get("clear_recyclebin_enabled")
            self._clear_receive_path_enabled = config.get("clear_receive_path_enabled")
            self._cron_clear = config.get("cron_clear")
            self._pan_transfer_enabled = config.get("pan_transfer_enabled")
            self._pan_transfer_paths = config.get("pan_transfer_paths")
            if not self._user_rmt_mediaext:
                self._user_rmt_mediaext = "mp4,mkv,ts,iso,rmvb,avi,mov,mpeg,mpg,wmv,3gp,asf,m4v,flv,m2ts,tp,f4v"
            if not self._user_download_mediaext:
                self._user_download_mediaext = "srt,ssa,ass"
            if not self._cron_full_sync_strm:
                self._cron_full_sync_strm = "0 */7 * * *"
            if not self._cron_clear:
                self._cron_clear = "0 */7 * * *"
            if not self._user_share_pan_path:
                self._user_share_pan_path = "/"
            self.__update_config()

        if self.__check_python_version() is False:
            self._enabled, self._once_full_sync_strm = False, False
            self.__update_config()
            return False

        try:
            self._client = P115Client(self._cookies)
        except Exception as e:
            logger.error(f"115网盘客户端创建失败: {e}")

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

        if self._enabled and (
            (self._monitor_life_enabled and self._monitor_life_paths)
            or (self._pan_transfer_enabled and self._pan_transfer_paths)
        ):
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
    def transfer_service_infos(self) -> Optional[Dict[str, ServiceInfo]]:
        """
        监控MP整理 媒体服务器服务信息
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

    @property
    def monitor_life_service_infos(self) -> Optional[Dict[str, ServiceInfo]]:
        """
        监控生活事件 媒体服务器服务信息
        """
        if not self._monitor_life_mediaservers:
            logger.warning("尚未配置媒体服务器，请检查配置")
            return None

        services = self.mediaserver_helper.get_services(
            name_filters=self._monitor_life_mediaservers
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
        """
        定义远程控制命令
        :return: 命令关键字、事件、描述、附带数据
        """
        return [
            {
                "cmd": "/p115_full_sync",
                "event": EventType.PluginAction,
                "desc": "全量同步115网盘文件",
                "category": "",
                "data": {"action": "p115_full_sync"},
            },
            {
                "cmd": "/p115_add_share",
                "event": EventType.PluginAction,
                "desc": "转存分享到待整理目录",
                "category": "",
                "data": {"action": "p115_add_share"},
            },
        ]

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
                "methods": ["GET", "POST", "HEAD"],
                "summary": "302跳转",
                "description": "115网盘302跳转",
            }
        ]

    def get_service(self) -> List[Dict[str, Any]]:
        """
        注册插件公共服务
        """
        cron_service = []
        if (
            self._cron_full_sync_strm
            and self._timing_full_sync_strm
            and self._full_sync_strm_paths
        ):
            cron_service.append(
                {
                    "id": "P115StrmHelper_full_sync_strm_files",
                    "name": "定期全量同步115媒体库",
                    "trigger": CronTrigger.from_crontab(self._cron_full_sync_strm),
                    "func": self.full_sync_strm_files,
                    "kwargs": {},
                }
            )
        if self._cron_clear and (
            self._clear_recyclebin_enabled or self._clear_receive_path_enabled
        ):
            cron_service.append(
                {
                    "id": "P115StrmHelper_main_cleaner",
                    "name": "定期清理115空间",
                    "trigger": CronTrigger.from_crontab(self._cron_clear),
                    "func": self.main_cleaner,
                    "kwargs": {},
                }
            )
        if cron_service:
            return cron_service

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
        """
        transfer_monitor_tab = [
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
                                    "model": "transfer_monitor_enabled",
                                    "label": "整理事件监控",
                                },
                            }
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 3},
                        "content": [
                            {
                                "component": "VSwitch",
                                "props": {
                                    "model": "transfer_monitor_scrape_metadata_enabled",
                                    "label": "STRM自动刮削",
                                },
                            }
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 3},
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
                        "props": {"cols": 12, "md": 3},
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
                                        {"title": config.name, "value": config.name}
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
                                    "placeholder": "一行一个，格式：本地STRM目录#网盘媒体库目录\n例如：\n/volume1/strm/movies#/媒体库/电影\n/volume1/strm/tv#/媒体库/剧集",
                                    "hint": "监控MoviePilot整理入库事件，自动在此处配置的本地目录生成对应的STRM文件。",
                                    "persistent-hint": True,
                                },
                            },
                            {
                                "component": "VAlert",
                                "props": {
                                    "type": "info",
                                    "variant": "tonal",
                                    "density": "compact",
                                    "class": "mt-2",
                                },
                                "content": [
                                    {"component": "div", "text": "格式示例："},
                                    {
                                        "component": "div",
                                        "props": {"class": "ml-2"},
                                        "text": "本地路径1#网盘路径1",
                                    },
                                    {
                                        "component": "div",
                                        "props": {"class": "ml-2"},
                                        "text": "本地路径2#网盘路径2",
                                    },
                                ],
                            },
                        ],
                    }
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
                                    "model": "transfer_mp_mediaserver_paths",
                                    "label": "媒体服务器映射替换",
                                    "rows": 2,
                                    "placeholder": "一行一个，格式：媒体库服务器映射目录#MP映射目录\n例如：\n/media#/data",
                                    "hint": "用于媒体服务器映射路径和MP映射路径不一样时自动刷新媒体服务器入库",
                                    "persistent-hint": True,
                                },
                            },
                            {
                                "component": "VAlert",
                                "props": {
                                    "type": "info",
                                    "variant": "tonal",
                                    "density": "compact",
                                    "class": "mt-2",
                                },
                                "content": [
                                    {
                                        "component": "div",
                                        "text": "媒体服务器映射路径和MP映射路径不一样时请配置此项，如果不配置则无法正常刷新",
                                    },
                                    {
                                        "component": "div",
                                        "text": "当映射路径一样时可省略此配置",
                                    },
                                ],
                            },
                        ],
                    }
                ],
            },
        ]

        full_sync_tab = [
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
                                    "model": "once_full_sync_strm",
                                    "label": "立刻全量同步",
                                },
                            }
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 3},
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
                        "props": {"cols": 12, "md": 3},
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
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 3},
                        "content": [
                            {
                                "component": "VSwitch",
                                "props": {
                                    "model": "full_sync_auto_download_mediainfo_enabled",
                                    "label": "下载媒体数据文件",
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
                                    "placeholder": "一行一个，格式：本地STRM目录#网盘媒体库目录\n例如：\n/volume1/strm/movies#/媒体库/电影\n/volume1/strm/tv#/媒体库/剧集",
                                    "hint": "全量扫描配置的网盘目录，并在对应的本地目录生成STRM文件。",
                                    "persistent-hint": True,
                                },
                            },
                            {
                                "component": "VAlert",
                                "props": {
                                    "type": "info",
                                    "variant": "tonal",
                                    "density": "compact",
                                    "class": "mt-2",
                                },
                                "content": [
                                    {"component": "div", "text": "格式示例："},
                                    {
                                        "component": "div",
                                        "props": {"class": "ml-2"},
                                        "text": "本地路径1#网盘路径1",
                                    },
                                    {
                                        "component": "div",
                                        "props": {"class": "ml-2"},
                                        "text": "本地路径2#网盘路径2",
                                    },
                                ],
                            },
                        ],
                    }
                ],
            },
        ]

        life_events_tab = [
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
                                    "model": "monitor_life_enabled",
                                    "label": "监控115生活事件",
                                },
                            }
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 3},
                        "content": [
                            {
                                "component": "VSwitch",
                                "props": {
                                    "model": "monitor_life_auto_download_mediainfo_enabled",
                                    "label": "下载媒体数据文件",
                                },
                            }
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 3},
                        "content": [
                            {
                                "component": "VSwitch",
                                "props": {
                                    "model": "monitor_life_scrape_metadata_enabled",
                                    "label": "STRM自动刮削",
                                },
                            }
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 3},
                        "content": [
                            {
                                "component": "VSwitch",
                                "props": {
                                    "model": "monitor_life_auto_remove_local_enabled",
                                    "label": "网盘删除本地同步删除",
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
                        "props": {"cols": 12, "md": 4},
                        "content": [
                            {
                                "component": "VSwitch",
                                "props": {
                                    "model": "monitor_life_media_server_refresh_enabled",
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
                                    "model": "monitor_life_mediaservers",
                                    "label": "媒体服务器",
                                    "items": [
                                        {"title": config.name, "value": config.name}
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
                                    "model": "monitor_life_paths",
                                    "label": "监控115生活事件目录",
                                    "rows": 5,
                                    "placeholder": "一行一个，格式：本地STRM目录#网盘媒体库目录\n例如：\n/volume1/strm/movies#/媒体库/电影\n/volume1/strm/tv#/媒体库/剧集",
                                    "hint": "监控115生活（上传、移动、接收文件、删除、复制）事件，自动在此处配置的本地目录生成对应的STRM文件或删除。",
                                    "persistent-hint": True,
                                },
                            },
                            {
                                "component": "VAlert",
                                "props": {
                                    "type": "info",
                                    "variant": "tonal",
                                    "density": "compact",
                                    "class": "mt-2",
                                },
                                "content": [
                                    {"component": "div", "text": "格式示例："},
                                    {
                                        "component": "div",
                                        "props": {"class": "ml-2"},
                                        "text": "本地路径1#网盘路径1",
                                    },
                                    {
                                        "component": "div",
                                        "props": {"class": "ml-2"},
                                        "text": "本地路径2#网盘路径2",
                                    },
                                ],
                            },
                        ],
                    }
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
                                    "model": "monitor_life_mp_mediaserver_paths",
                                    "label": "媒体服务器映射替换",
                                    "rows": 2,
                                    "placeholder": "一行一个，格式：媒体库服务器映射目录#MP映射目录\n例如：\n/media#/data",
                                    "hint": "用于媒体服务器映射路径和MP映射路径不一样时自动刷新媒体服务器入库",
                                    "persistent-hint": True,
                                },
                            },
                            {
                                "component": "VAlert",
                                "props": {
                                    "type": "info",
                                    "variant": "tonal",
                                    "density": "compact",
                                    "class": "mt-2",
                                },
                                "content": [
                                    {
                                        "component": "div",
                                        "text": "媒体服务器映射路径和MP映射路径不一样时请配置此项，如果不配置则无法正常刷新",
                                    },
                                    {
                                        "component": "div",
                                        "text": "当映射路径一样时可省略此配置",
                                    },
                                ],
                            },
                        ],
                    }
                ],
            },
        ]

        share_generate_tab = [
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
                                "component": "VSwitch",
                                "props": {
                                    "model": "share_strm_auto_download_mediainfo_enabled",
                                    "label": "下载媒体数据文件",
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
                        "props": {"cols": 12, "md": 4},
                        "content": [
                            {
                                "component": "VTextField",
                                "props": {
                                    "model": "user_share_link",
                                    "label": "分享链接",
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
            {
                "component": "VRow",
                "content": [
                    {
                        "component": "VAlert",
                        "props": {
                            "type": "info",
                            "variant": "tonal",
                            "density": "compact",
                            "class": "mt-2",
                        },
                        "content": [
                            {
                                "component": "div",
                                "text": "分享链接/分享码和分享密码 只需要二选一配置即可",
                            },
                            {
                                "component": "div",
                                "text": "同时填写分享链接，分享码和分享密码时，优先读取分享链接",
                            },
                        ],
                    },
                ],
            },
        ]

        cleanup_tab = [
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
                                    "type": "warning",
                                    "variant": "tonal",
                                    "density": "compact",
                                    "text": "注意，清空 回收站/我的接收 后文件不可恢复，如果产生重要数据丢失本程序不负责！",
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
                                    "model": "clear_recyclebin_enabled",
                                    "label": "清空回收站",
                                },
                            }
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 3},
                        "content": [
                            {
                                "component": "VSwitch",
                                "props": {
                                    "model": "clear_receive_path_enabled",
                                    "label": "清空我的接收目录",
                                },
                            }
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 3},
                        "content": [
                            {
                                "component": "VTextField",
                                "props": {"model": "password", "label": "115访问密码"},
                            }
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 3},
                        "content": [
                            {
                                "component": "VCronField",
                                "props": {"model": "cron_clear", "label": "清理周期"},
                            }
                        ],
                    },
                ],
            },
        ]

        pan_transfer_tab = [
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
                                    "model": "pan_transfer_enabled",
                                    "label": "网盘整理",
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
                                    "model": "pan_transfer_paths",
                                    "label": "网盘待整理目录",
                                    "rows": 5,
                                    "placeholder": "一行一个，格式：网盘待整理目录\n例如：\n/影视/待整理",
                                    "hint": "网盘待整理目录",
                                    "persistent-hint": True,
                                },
                            },
                        ],
                    }
                ],
            },
            {
                "component": "VAlert",
                "props": {
                    "type": "info",
                    "variant": "tonal",
                    "density": "compact",
                    "class": "mt-2",
                },
                "content": [
                    {
                        "component": "div",
                        "text": "使用本功能需要先进入 设定-目录 进行配置",
                    },
                    {
                        "component": "div",
                        "text": "添加目录配置卡，按需配置媒体类型和媒体类别，资源存储选择115网盘，资源目录输入网盘待整理文件夹",
                    },
                    {
                        "component": "div",
                        "text": "自动整理模式选择手动整理，媒体库存储依旧选择115网盘，并配置好媒体库路径，整理方式选择移动，按需配置分类、重命名、通知",
                    },
                    {
                        "component": "div",
                        "text": "配置完成目录设置后只需要在上方 网盘待整理目录 填入 网盘待整理文件夹 即可",
                    },
                ],
            },
            {
                "component": "VAlert",
                "props": {
                    "type": "warning",
                    "variant": "tonal",
                    "density": "compact",
                    "class": "mt-2",
                    "text": "注意：配置目录时不能选择刮削元数据，否则可能导致风控！",
                },
            },
            {
                "component": "VAlert",
                "props": {
                    "type": "warning",
                    "variant": "tonal",
                    "density": "compact",
                    "class": "mt-2",
                    "text": "注意：115生活事件监控会忽略网盘整理触发的移动事件，所以必须使用MP整理事件监控生成STRM",
                },
            },
        ]

        return [
            {
                "component": "VCard",
                "props": {"variant": "outlined", "class": "mb-3"},
                "content": [
                    {
                        "component": "VCardTitle",
                        "props": {"class": "d-flex align-center"},
                        "content": [
                            {
                                "component": "VIcon",
                                "props": {
                                    "icon": "mdi-cog",
                                    "color": "primary",
                                    "class": "mr-2",
                                },
                            },
                            {"component": "span", "text": "基础设置"},
                        ],
                    },
                    {"component": "VDivider"},
                    {
                        "component": "VCardText",
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
                                                    "label": "MoviePilot 内网访问地址",
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
                                    }
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
                                                    "model": "user_download_mediaext",
                                                    "label": "可下载媒体数据文件扩展名",
                                                },
                                            }
                                        ],
                                    }
                                ],
                            },
                        ],
                    },
                ],
            },
            {
                "component": "VCard",
                "props": {"variant": "outlined"},
                "content": [
                    {
                        "component": "VTabs",
                        "props": {"model": "tab", "grow": True, "color": "primary"},
                        "content": [
                            {
                                "component": "VTab",
                                "props": {"value": "tab-transfer"},
                                "content": [
                                    {
                                        "component": "VIcon",
                                        "props": {
                                            "icon": "mdi-file-move-outline",
                                            "start": True,
                                            "color": "#1976D2",
                                        },
                                    },
                                    {"component": "span", "text": "监控MP整理"},
                                ],
                            },
                            {
                                "component": "VTab",
                                "props": {"value": "tab-sync"},
                                "content": [
                                    {
                                        "component": "VIcon",
                                        "props": {
                                            "icon": "mdi-sync",
                                            "start": True,
                                            "color": "#4CAF50",
                                        },
                                    },
                                    {"component": "span", "text": "全量同步"},
                                ],
                            },
                            {
                                "component": "VTab",
                                "props": {"value": "tab-life"},
                                "content": [
                                    {
                                        "component": "VIcon",
                                        "props": {
                                            "icon": "mdi-calendar-heart",
                                            "start": True,
                                            "color": "#9C27B0",
                                        },
                                    },
                                    {"component": "span", "text": "监控115生活事件"},
                                ],
                            },
                            {
                                "component": "VTab",
                                "props": {"value": "tab-share"},
                                "content": [
                                    {
                                        "component": "VIcon",
                                        "props": {
                                            "icon": "mdi-share-variant-outline",
                                            "start": True,
                                            "color": "#009688",
                                        },
                                    },
                                    {"component": "span", "text": "分享生成STRM"},
                                ],
                            },
                            {
                                "component": "VTab",
                                "props": {"value": "tab-cleanup"},
                                "content": [
                                    {
                                        "component": "VIcon",
                                        "props": {
                                            "icon": "mdi-broom",
                                            "start": True,
                                            "color": "#FF9800",
                                        },
                                    },
                                    {"component": "span", "text": "定期清理"},
                                ],
                            },
                            {
                                "component": "VTab",
                                "props": {"value": "tab-pan-transfer"},
                                "content": [
                                    {
                                        "component": "VIcon",
                                        "props": {
                                            "icon": "mdi-transfer",
                                            "start": True,
                                            "color": "#418291",
                                        },
                                    },
                                    {"component": "span", "text": "网盘整理"},
                                ],
                            },
                        ],
                    },
                    {"component": "VDivider"},
                    {
                        "component": "VWindow",
                        "props": {"model": "tab"},
                        "content": [
                            {
                                "component": "VWindowItem",
                                "props": {"value": "tab-transfer"},
                                "content": [
                                    {
                                        "component": "VCardText",
                                        "content": transfer_monitor_tab,
                                    }
                                ],
                            },
                            {
                                "component": "VWindowItem",
                                "props": {"value": "tab-sync"},
                                "content": [
                                    {"component": "VCardText", "content": full_sync_tab}
                                ],
                            },
                            {
                                "component": "VWindowItem",
                                "props": {"value": "tab-life"},
                                "content": [
                                    {
                                        "component": "VCardText",
                                        "content": life_events_tab,
                                    }
                                ],
                            },
                            {
                                "component": "VWindowItem",
                                "props": {"value": "tab-share"},
                                "content": [
                                    {
                                        "component": "VCardText",
                                        "content": share_generate_tab,
                                    }
                                ],
                            },
                            {
                                "component": "VWindowItem",
                                "props": {"value": "tab-cleanup"},
                                "content": [
                                    {"component": "VCardText", "content": cleanup_tab}
                                ],
                            },
                            {
                                "component": "VWindowItem",
                                "props": {"value": "tab-pan-transfer"},
                                "content": [
                                    {
                                        "component": "VCardText",
                                        "content": pan_transfer_tab,
                                    }
                                ],
                            },
                        ],
                    },
                ],
            },
        ], {
            "enabled": False,
            "once_full_sync_strm": False,
            "cookies": "",
            "password": "",
            "moviepilot_address": "",
            "user_rmt_mediaext": "mp4,mkv,ts,iso,rmvb,avi,mov,mpeg,mpg,wmv,3gp,asf,m4v,flv,m2ts,tp,f4v",
            "user_download_mediaext": "srt,ssa,ass",
            "transfer_monitor_enabled": False,
            "transfer_monitor_scrape_metadata_enabled": False,
            "transfer_monitor_paths": "",
            "transfer_mp_mediaserver_paths": "",
            "transfer_monitor_media_server_refresh_enabled": False,
            "transfer_monitor_mediaservers": [],
            "timing_full_sync_strm": False,
            "full_sync_auto_download_mediainfo_enabled": False,
            "cron_full_sync_strm": "0 */7 * * *",
            "full_sync_strm_paths": "",
            "monitor_life_enabled": False,
            "monitor_life_auto_download_mediainfo_enabled": False,
            "monitor_life_paths": "",
            "monitor_life_mp_mediaserver_paths": "",
            "monitor_life_media_server_refresh_enabled": False,
            "monitor_life_mediaservers": [],
            "monitor_life_auto_remove_local_enabled": False,
            "monitor_life_scrape_metadata_enabled": False,
            "share_strm_enabled": False,
            "share_strm_auto_download_mediainfo_enabled": False,
            "user_share_code": "",
            "user_receive_code": "",
            "user_share_link": "",
            "user_share_pan_path": "/",
            "user_share_local_path": "",
            "clear_recyclebin_enabled": False,
            "clear_receive_path_enabled": False,
            "cron_clear": "0 */7 * * *",
            "pan_transfer_enabled": False,
            "pan_transfer_paths": "",
            "tab": "tab-transfer",
        }

    def get_page(self) -> List[dict]:
        pass

    def __update_config(self):
        self.update_config(
            {
                "enabled": self._enabled,
                "once_full_sync_strm": self._once_full_sync_strm,
                "cookies": self._cookies,
                "password": self._password,
                "moviepilot_address": self.moviepilot_address,
                "user_rmt_mediaext": self._user_rmt_mediaext,
                "user_download_mediaext": self._user_download_mediaext,
                "transfer_monitor_enabled": self._transfer_monitor_enabled,
                "transfer_monitor_scrape_metadata_enabled": self._transfer_monitor_scrape_metadata_enabled,
                "transfer_monitor_paths": self._transfer_monitor_paths,
                "transfer_mp_mediaserver_paths": self._transfer_mp_mediaserver_paths,
                "transfer_monitor_media_server_refresh_enabled": self._transfer_monitor_media_server_refresh_enabled,
                "transfer_monitor_mediaservers": self._transfer_monitor_mediaservers,
                "timing_full_sync_strm": self._timing_full_sync_strm,
                "full_sync_auto_download_mediainfo_enabled": self._full_sync_auto_download_mediainfo_enabled,
                "cron_full_sync_strm": self._cron_full_sync_strm,
                "full_sync_strm_paths": self._full_sync_strm_paths,
                "monitor_life_enabled": self._monitor_life_enabled,
                "monitor_life_auto_download_mediainfo_enabled": self._monitor_life_auto_download_mediainfo_enabled,
                "monitor_life_paths": self._monitor_life_paths,
                "monitor_life_mp_mediaserver_paths": self._monitor_life_mp_mediaserver_paths,
                "monitor_life_media_server_refresh_enabled": self._monitor_life_media_server_refresh_enabled,
                "monitor_life_mediaservers": self._monitor_life_mediaservers,
                "monitor_life_auto_remove_local_enabled": self._monitor_life_auto_remove_local_enabled,
                "monitor_life_scrape_metadata_enabled": self._monitor_life_scrape_metadata_enabled,
                "share_strm_enabled": self._share_strm_enabled,
                "share_strm_auto_download_mediainfo_enabled": self._share_strm_auto_download_mediainfo_enabled,
                "user_share_code": self._user_share_code,
                "user_receive_code": self._user_receive_code,
                "user_share_link": self._user_share_link,
                "user_share_pan_path": self._user_share_pan_path,
                "user_share_local_path": self._user_share_local_path,
                "clear_recyclebin_enabled": self._clear_recyclebin_enabled,
                "clear_receive_path_enabled": self._clear_receive_path_enabled,
                "cron_clear": self._cron_clear,
                "pan_transfer_enabled": self._pan_transfer_enabled,
                "pan_transfer_paths": self._pan_transfer_paths,
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

    def __get_run_transfer_path(self, paths, transfer_path):
        """
        判断路径是否为整理路径
        """
        transfer_paths = paths.split("\n")
        for path in transfer_paths:
            if not path:
                continue
            if self.has_prefix(transfer_path, path):
                return True
        return False

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

    def media_transfer(self, event, file_path: Path, rmt_mediaext):
        """
        运行媒体文件整理
        :param event: 事件
        :param file_path: 文件路径
        :param rmt_mediaext: 媒体文件后缀名
        """
        file_category = event["file_category"]
        file_id = event["file_id"]
        if file_category == 0:
            # 文件夹情况，遍历文件夹，获取整理文件
            # 缓存顶层文件夹ID
            self.cache_delete_pan_transfer_list.append(str(event["file_id"]))
            for item in iter_files_with_path(
                self._client, cid=int(file_id), cooldown=2
            ):
                file_path = Path(item["path"])
                # 缓存文件夹ID
                if str(item["parent_id"]) not in self.cache_delete_pan_transfer_list:
                    self.cache_delete_pan_transfer_list.append(str(item["parent_id"]))
                if file_path.suffix in rmt_mediaext:
                    # 缓存文件ID
                    self.cache_creata_pan_transfer_list.append(str(item["id"]))
                    self.transferchain.do_transfer(
                        fileitem=FileItem(
                            storage="u115",
                            fileid=str(item["id"]),
                            parent_fileid=str(item["parent_id"]),
                            path=str(file_path).replace("\\", "/"),
                            type="file",
                            name=file_path.name,
                            basename=file_path.stem,
                            extension=file_path.suffix[1:],
                            size=item["size"],
                            pickcode=item["pickcode"],
                            modify_time=item["ctime"],
                        )
                    )
                    logger.info(f"【网盘整理】{file_path} 加入整理列队")
        else:
            # 文件情况，直接整理
            if file_path.suffix in rmt_mediaext:
                # 缓存文件ID
                self.cache_creata_pan_transfer_list.append(str(event["file_id"]))
                self.transferchain.do_transfer(
                    fileitem=FileItem(
                        storage="u115",
                        fileid=str(file_id),
                        parent_fileid=str(event["parent_id"]),
                        path=str(file_path).replace("\\", "/"),
                        type="file",
                        name=file_path.name,
                        basename=file_path.stem,
                        extension=file_path.suffix[1:],
                        size=event["file_size"],
                        pickcode=event["pick_code"],
                        modify_time=event["update_time"],
                    )
                )
                logger.info(f"【网盘整理】{file_path} 加入整理列队")

    def media_scrape_metadata(
        self,
        path,
        item_name: str = "",
        mediainfo: MediaInfo = None,
        meta: MetaBase = None,
    ):
        """
        媒体刮削服务
        :param path: 媒体文件路径
        :param item_name: 媒体名称
        :param meta: 元数据
        :param mediainfo: 媒体信息
        """
        item_name = item_name if item_name else Path(path).name
        logger.info(f"【媒体刮削】{item_name} 开始刮削元数据")
        if mediainfo:
            # 整理文件刮削
            if mediainfo.type == MediaType.MOVIE:
                # 电影刮削上级文件夹
                dir_path = Path(path).parent
                fileitem = FileItem(
                    storage="local",
                    type="dir",
                    path=str(dir_path),
                    name=dir_path.name,
                    basename=dir_path.stem,
                    modify_time=dir_path.stat().st_mtime,
                )
            else:
                # 电视剧刮削文件夹
                # 通过重命名格式判断根目录文件夹
                # 计算重命名中的文件夹层数
                rename_format_level = len(settings.TV_RENAME_FORMAT.split("/")) - 1
                logger.info(rename_format_level)
                if rename_format_level < 1:
                    file_path = Path(path)
                    fileitem = FileItem(
                        storage="local",
                        type="file",
                        path=str(file_path).replace("\\", "/"),
                        name=file_path.name,
                        basename=file_path.stem,
                        extension=file_path.suffix[1:],
                        size=file_path.stat().st_size,
                        modify_time=file_path.stat().st_mtime,
                    )
                else:
                    dir_path = Path(Path(path).parents[rename_format_level - 1])
                    logger.info()
                    fileitem = FileItem(
                        storage="local",
                        type="dir",
                        path=str(dir_path),
                        name=dir_path.name,
                        basename=dir_path.stem,
                        modify_time=dir_path.stat().st_mtime,
                    )
            self.mediachain.scrape_metadata(
                fileitem=fileitem, meta=meta, mediainfo=mediainfo
            )
        else:
            # 对于没有 mediainfo 的媒体文件刮削
            # 先获取上级目录 mediainfo
            mediainfo, meta = None, None
            dir_path = Path(path).parent
            meta = MetaInfoPath(dir_path)
            mediainfo = self.mediachain.recognize_by_meta(meta)
            if not meta or not mediainfo:
                # 如果上级目录没有媒体信息则使用传入的路径
                logger.warn(f"{dir_path} 无法识别文件媒体信息！")
                finish_path = Path(path)
                meta = MetaInfoPath(finish_path)
                mediainfo = self.mediachain.recognize_by_meta(meta)
            else:
                if mediainfo.type == MediaType.TV:
                    # 如果是电视剧，再次获取上级目录媒体信息，兼容电视剧命名，获取 mediainfo
                    mediainfo, meta = None, None
                    dir_path = dir_path.parent
                    meta = MetaInfoPath(dir_path)
                    mediainfo = self.mediachain.recognize_by_meta(meta)
                    if not meta or not mediainfo:
                        # 存在 mediainfo 则使用本级目录
                        finish_path = dir_path
                    else:
                        # 否则使用上级目录
                        logger.warn(f"{dir_path} 无法识别文件媒体信息！")
                        finish_path = Path(path).parent
                        meta = MetaInfoPath(finish_path)
                        mediainfo = self.mediachain.recognize_by_meta(meta)
            fileitem = FileItem(
                storage="local",
                type="dir",
                path=str(finish_path),
                name=finish_path.name,
                basename=finish_path.stem,
                modify_time=finish_path.stat().st_mtime,
            )
            self.mediachain.scrape_metadata(
                fileitem=fileitem, meta=meta, mediainfo=mediainfo
            )

        logger.info(f"【媒体刮削】{item_name} 刮削元数据完成")

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
        # 媒体信息
        mediainfo: MediaInfo = item.get("mediainfo")
        # 元数据信息
        meta: MetaBase = item.get("meta")

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

        if self._transfer_monitor_scrape_metadata_enabled:
            self.media_scrape_metadata(
                path=strm_target_path,
                item_name=item_dest_name,
                mediainfo=mediainfo,
                meta=meta,
            )

        if self._transfer_monitor_media_server_refresh_enabled:
            if not self.transfer_service_infos:
                return

            logger.info(f"【监控整理STRM生成】 {item_dest_name} 开始刷新媒体服务器")

            if self._transfer_mp_mediaserver_paths:
                status, mediaserver_path, moviepilot_path = self.__get_media_path(
                    self._transfer_mp_mediaserver_paths, strm_target_path
                )
                if status:
                    logger.info(
                        f"【监控整理STRM生成】 {item_dest_name} 刷新媒体服务器目录替换中..."
                    )
                    strm_target_path = strm_target_path.replace(
                        moviepilot_path, mediaserver_path
                    ).replace("\\", "/")
                    logger.info(
                        f"【监控整理STRM生成】刷新媒体服务器目录替换: {moviepilot_path} --> {mediaserver_path}"
                    )
                    logger.info(
                        f"【监控整理STRM生成】刷新媒体服务器目录: {strm_target_path}"
                    )
            items = [
                RefreshMediaItem(
                    title=mediainfo.title,
                    year=mediainfo.year,
                    type=mediainfo.type,
                    category=mediainfo.category,
                    target_path=Path(strm_target_path),
                )
            ]
            for name, service in self.transfer_service_infos.items():
                if hasattr(service.instance, "refresh_library_by_items"):
                    service.instance.refresh_library_by_items(items)
                elif hasattr(service.instance, "refresh_root_library"):
                    service.instance.refresh_root_library()
                else:
                    logger.warning(
                        f"【监控整理STRM生成】 {item_dest_name} {name} 不支持刷新"
                    )

    @eventmanager.register(EventType.PluginAction)
    def p115_full_sync(self, event: Event):
        """
        远程全量同步
        """
        if event:
            event_data = event.event_data
            if not event_data or event_data.get("action") != "p115_full_sync":
                return
            self.post_message(
                channel=event.event_data.get("channel"),
                title="开始115网盘媒体库全量同步 ...",
                userid=event.event_data.get("user"),
            )
        strm_count, mediainfo_count = self.full_sync_strm_files()
        if event:
            self.post_message(
                channel=event.event_data.get("channel"),
                title=f"全量生成 STRM 文件完成，总共生成 {strm_count} 个 STRM 文件，下载 {mediainfo_count} 个媒体数据文件",
                userid=event.event_data.get("user"),
            )

    @eventmanager.register(EventType.PluginAction)
    def p115_add_share(self, event: Event):
        """
        远程分享转存
        """
        if event:
            event_data = event.event_data
            if not event_data or event_data.get("action") != "p115_add_share":
                return
            args = event_data.get("arg_str")
            if not args:
                logger.error(f"【分享转存】缺少参数：{event_data}")
                self.post_message(
                    channel=event.event_data.get("channel"),
                    title="参数错误！ /p115_add_share 分享链接",
                    userid=event.event_data.get("user"),
                )
                return
        data = share_extract_payload(args)
        share_code = data["share_code"]
        receive_code = data["receive_code"]
        logger.info(
            f"【分享转存】解析分享链接 share_code={share_code} receive_code={receive_code}"
        )
        if not share_code or not receive_code:
            logger.error(f"【分享转存】解析分享链接失败：{args}")
            self.post_message(
                channel=event.event_data.get("channel"),
                title=f"解析分享链接失败：{args}",
                userid=event.event_data.get("user"),
            )
            return
        parent_path = self._pan_transfer_paths.split("\n")[0]
        parent_id = self.id_path_cache.get_id_by_dir(directory=str(parent_path))
        if not parent_id:
            parent_id = get_id_to_path(self._client, path=parent_path)
            logger.info(f"【分享转存】获取到转存目录 ID：{parent_id}")
            self.id_path_cache.add_cache(id=int(parent_id), directory=str(parent_path))
        payload = {
            "share_code": share_code,
            "receive_code": receive_code,
            "file_id": 0,
            "cid": int(parent_id),
            "is_check": 0,
        }
        logger.info(f"【分享转存】开始转存：{share_code}")
        self.post_message(
            channel=event.event_data.get("channel"),
            title=f"开始转存：{share_code}",
            userid=event.event_data.get("user"),
        )
        resp = self._client.share_receive(payload)
        if resp["state"]:
            logger.info(f"【分享转存】转存 {share_code} 到 {parent_path} 成功！")
            self.post_message(
                channel=event.event_data.get("channel"),
                title=f"转存 {share_code} 到 {parent_path} 成功！",
                userid=event.event_data.get("user"),
            )
        else:
            logger.info(f"【分享转存】转存 {share_code} 失败：{resp['error']}")
            self.post_message(
                channel=event.event_data.get("channel"),
                title=f"转存 {share_code} 失败：{resp['error']}",
                userid=event.event_data.get("user"),
            )
        return

    def full_sync_strm_files(self):
        """
        全量同步
        """
        if (
            not self._full_sync_strm_paths
            or not self.moviepilot_address
            or not self._user_download_mediaext
        ):
            return

        strm_helper = FullSyncStrmHelper(
            user_rmt_mediaext=self._user_rmt_mediaext,
            user_download_mediaext=self._user_download_mediaext,
            auto_download_mediainfo=self._full_sync_auto_download_mediainfo_enabled,
            client=self._client,
            cookie=self._cookies,
            server_address=self.moviepilot_address,
        )
        strm_helper.generate_strm_files(
            full_sync_strm_paths=self._full_sync_strm_paths,
        )
        return strm_helper.get_generate_total()

    def share_strm_files(self):
        """
        分享生成STRM
        """
        if (
            not self._user_share_pan_path
            or not self._user_share_local_path
            or not self.moviepilot_address
        ):
            return

        if self._user_share_link:
            data = share_extract_payload(self._user_share_link)
            share_code = data["share_code"]
            receive_code = data["receive_code"]
            logger.info(
                f"【分享STRM生成】解析分享链接 share_code={share_code} receive_code={receive_code}"
            )
        else:
            if not self._user_share_code or not self._user_receive_code:
                return
            share_code = self._user_share_code
            receive_code = self._user_receive_code

        try:
            strm_helper = ShareStrmHelper(
                user_rmt_mediaext=self._user_rmt_mediaext,
                user_download_mediaext=self._user_download_mediaext,
                auto_download_mediainfo=self._share_strm_auto_download_mediainfo_enabled,
                client=self._client,
                server_address=self.moviepilot_address,
                share_media_path=self._user_share_pan_path,
                local_media_path=self._user_share_local_path,
                cookie=self._cookies,
            )
            strm_helper.get_share_list_creata_strm(
                cid=0,
                share_code=share_code,
                receive_code=receive_code,
            )
            strm_helper.get_generate_total()
        except Exception as e:
            logger.error(f"【分享STRM生成】运行失败: {e}")
            return

    def monitor_life_strm_files(self):
        """
        监控115生活事件

        {
            1: "upload_image_file",  上传图片 生成 STRM
            2: "upload_file",        上传文件/目录 生成 STRM
            3: "star_image",         标星图片 无操作
            4: "star_file",          标星文件/目录 无操作
            5: "move_image_file",    移动图片 生成 STRM
            6: "move_file",          移动文件/目录 生成 STRM
            7: "browse_image",       浏览图片 无操作
            8: "browse_video",       浏览视频 无操作
            9: "browse_audio",       浏览音频 无操作
            10: "browse_document",   浏览文档 无操作
            14: "receive_files",     接收文件 生成 STRM
            17: "new_folder",        创建新目录 无操作
            18: "copy_folder",       复制文件夹 生成 STRM
            19: "folder_label",      标签文件夹 无操作
            20: "folder_rename",     重命名文件夹 无操作
            22: "delete_file",       删除文件/文件夹 删除 STRM
        }

        注意: 目前没有重命名文件，复制文件的操作事件
        """

        def refresh_mediaserver(file_path: str, file_name: str):
            """
            刷新媒体服务器
            """
            if self._monitor_life_media_server_refresh_enabled:
                if not self.monitor_life_service_infos:
                    return
                logger.info(f"【监控生活事件】 {file_name} 开始刷新媒体服务器")
                if self._monitor_life_mp_mediaserver_paths:
                    status, mediaserver_path, moviepilot_path = self.__get_media_path(
                        self._monitor_life_mp_mediaserver_paths, file_path
                    )
                    if status:
                        logger.info(
                            f"【监控生活事件】 {file_name} 刷新媒体服务器目录替换中..."
                        )
                        file_path = file_path.replace(
                            moviepilot_path, mediaserver_path
                        ).replace("\\", "/")
                        logger.info(
                            f"【监控生活事件】刷新媒体服务器目录替换: {moviepilot_path} --> {mediaserver_path}"
                        )
                        logger.info(f"【监控生活事件】刷新媒体服务器目录: {file_path}")
                items = [
                    RefreshMediaItem(
                        title=None,
                        year=None,
                        type=None,
                        category=None,
                        target_path=Path(file_path),
                    )
                ]
                for name, service in self.monitor_life_service_infos.items():
                    if hasattr(service.instance, "refresh_library_by_items"):
                        service.instance.refresh_library_by_items(items)
                    elif hasattr(service.instance, "refresh_root_library"):
                        service.instance.refresh_root_library()
                    else:
                        logger.warning(f"【监控生活事件】{file_name} {name} 不支持刷新")

        def creata_strm(event, file_path):
            """
            创建 STRM 文件
            """
            pickcode = event["pick_code"]
            file_category = event["file_category"]
            file_id = event["file_id"]
            status, target_dir, pan_media_dir = self.__get_media_path(
                self._monitor_life_paths, file_path
            )
            if not status:
                return
            logger.debug("【监控生活事件】匹配到网盘文件夹路径: %s", str(pan_media_dir))

            if file_category == 0:
                # 文件夹情况，遍历文件夹
                for item in iter_files_with_path(
                    self._client, cid=int(file_id), cooldown=2
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

                    if self._monitor_life_auto_download_mediainfo_enabled:
                        if file_path.suffix in download_mediaext:
                            pickcode = item["pickcode"]
                            if not pickcode:
                                logger.error(
                                    f"【监控生活事件】{original_file_name} 不存在 pickcode 值，无法下载该文件"
                                )
                                continue
                            download_url = get_download_url(
                                pickcode=pickcode, cookie=self._cookies
                            )

                            if not download_url:
                                logger.error(
                                    f"【监控生活事件】{original_file_name} 下载链接获取失败，无法下载该文件"
                                )
                                continue

                            save_mediainfo_file(
                                file_path=Path(file_path),
                                file_name=original_file_name,
                                download_url=download_url,
                                cookie=self._cookies,
                            )
                            continue

                    if file_path.suffix not in rmt_mediaext:
                        logger.warn(
                            "【监控生活事件】跳过网盘路径: %s",
                            str(file_path).replace(str(target_dir), "", 1),
                        )
                        continue

                    pickcode = item["pickcode"]
                    if not pickcode:
                        pickcode = item["pick_code"]

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
                    strm_url = f"{self.moviepilot_address.rstrip('/')}/api/v1/plugin/P115StrmHelper/redirect_url?apikey={settings.API_TOKEN}&pickcode={pickcode}"

                    with open(new_file_path, "w", encoding="utf-8") as file:
                        file.write(strm_url)
                    logger.info(
                        "【监控生活事件】生成 STRM 文件成功: %s", str(new_file_path)
                    )
                    if self._monitor_life_scrape_metadata_enabled:
                        self.media_scrape_metadata(
                            path=new_file_path,
                        )
                    # 刷新媒体服务器
                    refresh_mediaserver(str(new_file_path), str(original_file_name))
            else:
                # 文件情况，直接生成
                file_path = Path(target_dir) / Path(file_path).relative_to(
                    pan_media_dir
                )
                file_target_dir = file_path.parent
                original_file_name = file_path.name
                file_name = file_path.stem + ".strm"
                new_file_path = file_target_dir / file_name

                if self._monitor_life_auto_download_mediainfo_enabled:
                    if file_path.suffix in download_mediaext:
                        if not pickcode:
                            logger.error(
                                f"【监控生活事件】{original_file_name} 不存在 pickcode 值，无法下载该文件"
                            )
                            return
                        download_url = get_download_url(
                            pickcode=pickcode, cookie=self._cookies
                        )

                        if not download_url:
                            logger.error(
                                f"【监控生活事件】{original_file_name} 下载链接获取失败，无法下载该文件"
                            )
                            return

                        save_mediainfo_file(
                            file_path=Path(file_path),
                            file_name=original_file_name,
                            download_url=download_url,
                            cookie=self._cookies,
                        )
                        return

                if file_path.suffix not in rmt_mediaext:
                    logger.warn(
                        "【监控生活事件】跳过网盘路径: %s",
                        str(file_path).replace(str(target_dir), "", 1),
                    )
                    return

                new_file_path.parent.mkdir(parents=True, exist_ok=True)

                if not pickcode:
                    logger.error(
                        f"【监控生活事件】{original_file_name} 不存在 pickcode 值，无法生成 STRM 文件"
                    )
                    return
                if not (len(pickcode) == 17 and str(pickcode).isalnum()):
                    logger.error(
                        f"【监控生活事件】错误的 pickcode 值 {pickcode}，无法生成 STRM 文件"
                    )
                    return
                strm_url = f"{self.moviepilot_address.rstrip('/')}/api/v1/plugin/P115StrmHelper/redirect_url?apikey={settings.API_TOKEN}&pickcode={pickcode}"

                with open(new_file_path, "w", encoding="utf-8") as file:
                    file.write(strm_url)
                logger.info(
                    "【监控生活事件】生成 STRM 文件成功: %s", str(new_file_path)
                )
                if self._monitor_life_scrape_metadata_enabled:
                    self.media_scrape_metadata(
                        path=new_file_path,
                    )
                # 刷新媒体服务器
                refresh_mediaserver(str(new_file_path), str(original_file_name))

        def remove_strm(event):
            """
            删除 STRM 文件
            """

            def __remove_parent_dir(file_path: Path):
                """
                删除父目录
                """
                # 删除空目录
                # 判断当前媒体父路径下是否有媒体文件，如有则无需遍历父级
                if not SystemUtils.exits_files(file_path.parent, ["strm"]):
                    # 判断父目录是否为空, 为空则删除
                    i = 0
                    for parent_path in file_path.parents:
                        i += 1
                        if i > 3:
                            break
                        if str(parent_path.parent) != str(file_path.root):
                            # 父目录非根目录，才删除父目录
                            if not SystemUtils.exits_files(parent_path, ["strm"]):
                                # 当前路径下没有媒体文件则删除
                                shutil.rmtree(parent_path)
                                logger.warn(
                                    f"【监控生活事件】本地空目录 {parent_path} 已删除"
                                )

            def __get_file_path(
                file_name: str, file_size: str, file_id: str, file_category: int
            ):
                """
                通过 还原文件/文件夹 再删除 获取文件路径
                """
                for item in self._client.recyclebin_list()["data"]:
                    if (
                        file_category == 0
                        and str(item["file_name"]) == file_name
                        and str(item["type"]) == "2"
                    ) or (
                        file_category != 0
                        and str(item["file_name"]) == file_name
                        and str(item["file_size"]) == file_size
                    ):
                        resp = self._client.recyclebin_revert(item["id"])
                        if resp["state"]:
                            time.sleep(1)
                            path = get_path_to_cid(self._client, cid=int(item["cid"]))
                            time.sleep(1)
                            self._client.fs_delete(file_id)
                            return str(Path(path) / item["file_name"])
                        else:
                            return None
                return None

            file_category = event["file_category"]
            file_path = __get_file_path(
                file_name=str(event["file_name"]),
                file_size=str(event["file_size"]),
                file_id=str(event["file_id"]),
                file_category=file_category,
            )
            if not file_path:
                return

            # 优先匹配待整理目录，如果删除的目录为待整理目录则不进行操作
            if self._pan_transfer_enabled and self._pan_transfer_paths:
                if self.__get_run_transfer_path(
                    paths=self._pan_transfer_paths, transfer_path=file_path
                ):
                    logger.debug(
                        f"【监控生活事件】: {file_path} 为待整理目录下的路径，不做处理"
                    )
                    return

            # 匹配是否是媒体文件夹目录
            status, target_dir, pan_media_dir = self.__get_media_path(
                self._monitor_life_paths, file_path
            )
            if not status:
                return
            logger.debug("【监控生活事件】匹配到网盘文件夹路径: %s", str(pan_media_dir))

            file_path = Path(target_dir) / Path(file_path).relative_to(pan_media_dir)
            if file_path.suffix in rmt_mediaext:
                file_target_dir = file_path.parent
                file_name = file_path.stem + ".strm"
                file_path = file_target_dir / file_name
            logger.info(
                f"【监控生活事件】删除本地{'文件夹' if file_category == 0 else '文件'}: {file_path}"
            )
            if file_category == 0:
                shutil.rmtree(Path(file_path))
            else:
                Path(file_path).unlink(missing_ok=True)
                __remove_parent_dir(Path(file_path))
            logger.info(f"【监控生活事件】{file_path} 已删除")

        def new_creata_path(event):
            """
            处理新出现的路径
            """
            # 1.获取绝对文件路径
            file_name = event["file_name"]
            file_path = (
                Path(get_path_to_cid(self._client, cid=int(event["parent_id"])))
                / file_name
            )
            # 匹配逻辑 整理路径目录 > 生成STRM文件路径目录
            # 2.匹配是否为整理路径目录
            if self._pan_transfer_enabled and self._pan_transfer_paths:
                if self.__get_run_transfer_path(
                    paths=self._pan_transfer_paths, transfer_path=file_path
                ):
                    self.media_transfer(
                        event=event,
                        file_path=Path(file_path),
                        rmt_mediaext=rmt_mediaext,
                    )
                    return
            # 3.匹配是否为生成STRM文件路径目录
            if self._monitor_life_enabled and self._monitor_life_paths:
                if str(event["file_id"]) in self.cache_creata_pan_transfer_list:
                    # 检查是否命中缓存，命中则不处理
                    self.cache_creata_pan_transfer_list.remove(str(event["file_id"]))
                else:
                    creata_strm(event=event, file_path=file_path)

        logger.info("【监控生活事件】生活事件监控启动中...")
        try:
            # 删除缓存，避免删除无限循环
            delete_list = []
            for events_batch in iter_life_behavior_list(self._client, cooldown=int(10)):
                if self.monitor_stop_event.is_set():
                    logger.info("【监控生活事件】收到停止信号，退出上传事件监控")
                    break
                if not events_batch:
                    time.sleep(10)
                    continue
                for event in events_batch:
                    rmt_mediaext = [
                        f".{ext.strip()}"
                        for ext in self._user_rmt_mediaext.replace("，", ",").split(",")
                    ]
                    download_mediaext = [
                        f".{ext.strip()}"
                        for ext in self._user_download_mediaext.replace(
                            "，", ","
                        ).split(",")
                    ]
                    if (
                        int(event["type"]) != 1
                        and int(event["type"]) != 2
                        and int(event["type"]) != 5
                        and int(event["type"]) != 6
                        and int(event["type"]) != 14
                        and int(event["type"]) != 18
                        and int(event["type"]) != 22
                    ):
                        continue

                    if (
                        int(event["type"]) == 1
                        or int(event["type"]) == 2
                        or int(event["type"]) == 5
                        or int(event["type"]) == 6
                        or int(event["type"]) == 14
                        or int(event["type"]) == 18
                    ):
                        # 新路径事件处理
                        new_creata_path(event=event)

                    if int(event["type"]) == 22:
                        # 删除文件/文件夹事件处理
                        if event["file_id"] in delete_list:
                            # 通过 delete_list 避免反复删除
                            delete_list.remove(event["file_id"])
                        elif (
                            str(event["file_id"]) in self.cache_delete_pan_transfer_list
                        ):
                            # 检查是否命中删除文件夹缓存，命中则无需处理
                            self.cache_delete_pan_transfer_list.remove(
                                str(event["file_id"])
                            )
                        else:
                            if (
                                self._monitor_life_enabled
                                and self._monitor_life_paths
                                and self._monitor_life_auto_remove_local_enabled
                            ):
                                delete_list.append(event["file_id"])
                                remove_strm(event=event)

        except Exception as e:
            logger.error(f"【监控生活事件】生活事件监控运行失败: {e}")
            logger.info("【监控生活事件】30s 后尝试重新启动生活事件监控")
            time.sleep(30)
            self.monitor_life_strm_files()
        logger.info("【监控生活事件】已退出生活事件监控")
        return

    def main_cleaner(self):
        """
        主清理模块
        """
        if self._clear_receive_path_enabled:
            self.clear_receive_path()

        if self._clear_recyclebin_enabled:
            self.clear_recyclebin()

    def clear_recyclebin(self):
        """
        清空回收站
        """
        try:
            logger.info("【回收站清理】开始清理回收站")
            self._client.recyclebin_clean(password=self._password)
            logger.info("【回收站清理】回收站已清空")
        except Exception as e:
            logger.error(f"【回收站清理】清理回收站运行失败: {e}")
            return

    def clear_receive_path(self):
        """
        清空我的接收
        """
        try:
            logger.info("【我的接收清理】开始清理我的接收")
            parent_id = int(self._client.fs_dir_getid("/我的接收")["id"])
            if parent_id == 0:
                logger.info("【我的接收清理】我的接收目录为空，无需清理")
                return
            logger.info(f"【我的接收清理】我的接收目录 ID 获取成功: {parent_id}")
            self._client.fs_delete(parent_id)
            logger.info("【我的接收清理】我的接收已清空")
        except Exception as e:
            logger.error(f"【我的接收清理】清理我的接收运行失败: {e}")
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
