import base64
import re
import shutil
import threading
import time
import traceback
from collections import defaultdict
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta
from errno import EIO, ENOENT
from functools import wraps
from itertools import chain, batched
from pathlib import Path
from queue import Queue, Empty
from threading import Event as ThreadEvent, Timer
from typing import Any, List, Dict, Tuple, cast, Optional, MutableMapping, Union
from urllib.parse import quote, unquote, urlsplit, urlencode

import pytz
import requests
from app.chain.media import MediaChain
from app.chain.storage import StorageChain
from app.chain.transfer import TransferChain
from app.core.config import settings
from app.core.context import MediaInfo
from app.core.event import eventmanager, Event
from app.core.meta import MetaBase
from app.core.metainfo import MetaInfo
from app.helper.mediaserver import MediaServerHelper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import (
    TransferInfo,
    FileItem,
    RefreshMediaItem,
    ServiceInfo,
    NotificationType,
)
from app.schemas.types import EventType, MessageChannel
from app.utils.system import SystemUtils
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from cachetools import cached, TTLCache
from fastapi import Request, Response
from orjson import dumps, loads
from p115client import P115Client
from p115client.exception import DataError
from p115client.tool.fs_files import iter_fs_files
from p115client.tool.iterdir import iter_files_with_path, get_path_to_cid, share_iterdir
from p115client.tool.life import iter_life_behavior_once, life_show
from p115client.tool.util import share_extract_payload
from p115rsacipher import encrypt, decrypt
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver

from .core.cache import IdPathCache
from .core.config import configer
from .core.scrape_metadata import media_scrape_metadata
from .core.u115_open import U115OpenHelper
from .db_manager import ct_db_manager
from .db_manager.init import init_db, update_db
from .db_manager.oper import FileDbHelper
from .interactive.framework.callbacks import decode_action, Action
from .interactive.framework.manager import BaseSessionManager
from .interactive.framework.schemas import TSession
from .interactive.handler import ActionHandler
from .interactive.session import Session
from .interactive.views import ViewRenderer
from .helper.mediainfo_download import MediaInfoDownloader
from .helper.strm import FullSyncStrmHelper, ShareStrmHelper, IncrementSyncStrmHelper
from .utils.http import check_response
from .utils.path import PathMatchingHelper
from .utils.url import Url

# 实例化一个该插件专用的 SessionManager
session_manager = BaseSessionManager(session_class=Session)

directory_upload_dict = defaultdict(threading.Lock)


class FileMonitorHandler(FileSystemEventHandler):
    """
    目录监控响应类
    """

    def __init__(self, monpath: str, sync: Any, **kwargs):
        super(FileMonitorHandler, self).__init__(**kwargs)
        self._watch_path = monpath
        self.sync = sync

    def on_created(self, event):
        """
        创建
        """
        self.sync.event_handler(
            event=event,
            text="创建",
            mon_path=self._watch_path,
            event_path=event.src_path,
        )

    def on_moved(self, event):
        """
        移动
        """
        self.sync.event_handler(
            event=event,
            text="移动",
            mon_path=self._watch_path,
            event_path=event.dest_path,
        )


class P115StrmHelper(_PluginBase):
    # 插件名称
    plugin_name = "115网盘STRM助手"
    # 插件描述
    plugin_desc = "115网盘STRM生成一条龙服务"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/jxxghp/MoviePilot-Frontend/refs/heads/v2/src/assets/images/misc/u115.png"
    # 插件版本
    plugin_version = "1.8.31"
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
    pathmatchinghelper = None
    mediainfodownloader = None

    # 目录ID缓存
    id_path_cache = None

    # 生活事件缓存
    cache_delete_pan_transfer_list = None
    cache_creata_pan_transfer_list = None
    cache_top_delete_pan_transfer_list = None
    cache_create_strm_file_dict = None

    # 生活事件监控通知系统
    _monitor_life_notification_queue = None
    _monitor_life_notification_timer = None

    # 网盘客户端
    _client = None
    u115openhelper = None

    # 目录监控
    _observer = []

    # 任务客户端
    _scheduler = None

    # 分享转存队列
    _add_share_queue = None
    _add_share_worker_thread = None
    _add_share_worker_lock = None

    # 退出事件
    _event = ThreadEvent()
    monitor_stop_event = None
    monitor_life_thread = None

    @staticmethod
    def logs_oper(oper_name: str):
        """
        数据库操作汇报装饰器
        - 捕获异常并记录日志
        - 5秒内合并多条消息，避免频繁发送通知
        """

        def decorator(func):
            @wraps(func)
            def wrapper(self, *args, **kwargs):
                level, text = "success", f"{oper_name} 成功"
                try:
                    result = func(self, *args, **kwargs)
                    return result
                except Exception as e:
                    logger.error(f"{oper_name} 失败：{str(e)}", exc_info=True)
                    level, text = "error", f"{oper_name} 失败：{str(e)}"
                    return False
                finally:
                    if hasattr(self, "add_message"):
                        self.add_message(title=oper_name, text=text, level=level)

            return wrapper

        return decorator

    def __init__(self, config: dict = None):
        """
        初始化
        """
        super().__init__()

        # 初始化配置项
        configer.load_from_dict(config or {})

        # 类名小写
        class_name = self.__class__.__name__.lower()
        # 插件配置文件路径
        self.__plugin_config_path = settings.PLUGIN_DATA_PATH / class_name
        # 数据库文件路径
        self.__db_path = (
            settings.PLUGIN_DATA_PATH / class_name / "p115strmhelper_file.db"
        )
        self.__database_path = (
            settings.ROOT_PATH / "app/plugins" / class_name / "database"
        )

        # 临时文件路径
        temp_path = settings.PLUGIN_DATA_PATH / class_name / "temp"
        if not Path(temp_path).exists():
            Path(temp_path).mkdir(parents=True, exist_ok=True)

        # 初始化数据库
        self.init_database()

        # 实例化处理器和渲染器
        self.action_handler = ActionHandler()
        self.view_renderer = ViewRenderer()

    def init_plugin(self, config: dict = None):
        """
        初始化插件
        """
        self.pathmatchinghelper = PathMatchingHelper()
        self.monitor_stop_event = threading.Event()

        self.id_path_cache = IdPathCache()
        self.cache_delete_pan_transfer_list = []
        self.cache_creata_pan_transfer_list = []
        self.cache_top_delete_pan_transfer_list: Dict[str, List] = {}
        self.cache_create_strm_file_dict: MutableMapping[str, List] = TTLCache(
            maxsize=1_000_000, ttl=600
        )

        self._monitor_life_notification_queue = defaultdict(
            lambda: {"strm_count": 0, "mediainfo_count": 0}
        )
        self._monitor_life_notification_timer = None

        self._add_share_queue = Queue()
        self._add_share_worker_thread = None
        self._add_share_worker_lock = threading.Lock()

        if config:
            configer.update_config(config)
            self.__update_config()

        # 停止现有任务
        self.stop_service()

        if configer.get_config("enabled"):
            self.init_database()

            try:
                self._client = P115Client(configer.get_config("cookies"))
                self.mediainfodownloader = MediaInfoDownloader(
                    cookie=configer.get_config("cookies")
                )
                self.u115openhelper = U115OpenHelper()
            except Exception as e:
                logger.error(f"115网盘客户端创建失败: {e}")

            # 目录上传监控服务
            if configer.get_config("directory_upload_enabled"):
                for item in configer.get_config("directory_upload_path"):
                    if not item:
                        continue
                    mon_path = item.get("src", "")
                    if not mon_path:
                        continue
                    try:
                        if (
                            configer.get_config("directory_upload_mode")
                            == "compatibility"
                        ):
                            # 兼容模式，目录同步性能降低且NAS不能休眠，但可以兼容挂载的远程共享目录如SMB
                            observer = PollingObserver(timeout=10)
                        else:
                            # 内部处理系统操作类型选择最优解
                            observer = Observer(timeout=10)
                        self._observer.append(observer)
                        observer.schedule(
                            FileMonitorHandler(mon_path, self),
                            path=mon_path,
                            recursive=True,
                        )
                        observer.daemon = True
                        observer.start()
                        logger.info(f"【目录上传】{mon_path} 实时监控服务启动")
                    except Exception as e:
                        err_msg = str(e)
                        if "inotify" in err_msg and "reached" in err_msg:
                            logger.warn(
                                f"【目录上传】监控服务启动出现异常：{err_msg}，请在宿主机上（不是docker容器内）执行以下命令并重启："
                                + """
                                    echo fs.inotify.max_user_watches=524288 | sudo tee -a /etc/sysctl.conf
                                    echo fs.inotify.max_user_instances=524288 | sudo tee -a /etc/sysctl.conf
                                    sudo sysctl -p
                                    """
                            )
                        else:
                            logger.error(
                                f"【目录上传】{mon_path} 启动实时监控失败：{err_msg}"
                            )

            if (
                configer.get_config("monitor_life_enabled")
                and configer.get_config("monitor_life_paths")
                and configer.get_config("monitor_life_event_modes")
            ) or (
                configer.get_config("pan_transfer_enabled")
                and configer.get_config("pan_transfer_paths")
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

    @logs_oper("初始化数据库")
    def init_database(self) -> bool:
        """
        初始化数据库
        """
        if not Path(self.__plugin_config_path).exists():
            Path(self.__plugin_config_path).mkdir(parents=True, exist_ok=True)
        if not ct_db_manager.is_initialized():
            # 初始化数据库会话
            ct_db_manager.init_database(db_path=self.__db_path)
            # 表单补全
            init_db(
                engine=ct_db_manager.Engine,
            )
            # 更新数据库
            update_db(
                db_path=self.__db_path,
                database_dir=self.__database_path,
            )
        return True

    def get_state(self) -> bool:
        """
        插件状态
        """
        return configer.get_config("enabled")

    @property
    def transfer_service_infos(self) -> Optional[Dict[str, ServiceInfo]]:
        """
        监控MP整理 媒体服务器服务信息
        """
        if not configer.get_config("transfer_monitor_mediaservers"):
            logger.warning("尚未配置媒体服务器，请检查配置")
            return None

        mediaserver_helper = MediaServerHelper()

        services = mediaserver_helper.get_services(
            name_filters=configer.get_config("transfer_monitor_mediaservers")
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
        if not configer.get_config("monitor_life_mediaservers"):
            logger.warning("尚未配置媒体服务器，请检查配置")
            return None

        mediaserver_helper = MediaServerHelper()

        services = mediaserver_helper.get_services(
            name_filters=configer.get_config("monitor_life_mediaservers")
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
        # {
        #     "cmd": "/p115_search",
        #     "event": EventType.PluginAction,
        #     "desc": "搜索指定资源",
        #     "category": "",
        #     "data": {"action": "p115_search"},
        # },
        return [
            {
                "cmd": "/p115_full_sync",
                "event": EventType.PluginAction,
                "desc": "全量同步115网盘文件",
                "category": "",
                "data": {"action": "p115_full_sync"},
            },
            {
                "cmd": "/p115_inc_sync",
                "event": EventType.PluginAction,
                "desc": "增量同步115网盘文件",
                "category": "",
                "data": {"action": "p115_inc_sync"},
            },
            {
                "cmd": "/p115_add_share",
                "event": EventType.PluginAction,
                "desc": "转存分享到待整理目录",
                "category": "",
                "data": {"action": "p115_add_share"},
            },
            {
                "cmd": "/p115_strm",
                "event": EventType.PluginAction,
                "desc": "全量生成指定网盘目录STRM",
                "category": "",
                "data": {"action": "p115_strm"},
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
                "endpoint": self._redirect_url,
                "methods": ["GET", "POST", "HEAD"],
                "summary": "302跳转",
                "description": "115网盘302跳转",
            },
            {
                "path": "/add_transfer_share",
                "endpoint": self.add_transfer_share,
                "methods": ["GET"],
                "summary": "添加分享转存整理",
            },
            {
                "path": "/user_storage_status",
                "endpoint": self._get_user_storage_status,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "获取115用户基本信息和空间状态",
            },
            {
                "path": "/get_config",
                "endpoint": self._get_config_api,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "获取配置",
            },
            {
                "path": "/save_config",
                "endpoint": self._save_config_api,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "保存配置",
            },
            {
                "path": "/get_status",
                "endpoint": self._get_status_api,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "获取状态",
            },
            {
                "path": "/full_sync",
                "endpoint": self._trigger_full_sync_api,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "执行全量同步",
            },
            {
                "path": "/share_sync",
                "endpoint": self._trigger_share_sync_api,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "执行分享同步",
            },
            {
                "path": "/browse_dir",
                "endpoint": self._browse_dir_api,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "浏览目录",
            },
            {
                "path": "/get_qrcode",
                "endpoint": self._get_qrcode_api,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "获取登录二维码",
            },
            {
                "path": "/check_qrcode",
                "endpoint": self._check_qrcode_api,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "检查二维码状态",
            },
        ]

    def get_service(self) -> List[Dict[str, Any]]:
        """
        注册插件公共服务
        """
        cron_service = []
        if (
            configer.get_config("cron_full_sync_strm")
            and configer.get_config("timing_full_sync_strm")
            and configer.get_config("full_sync_strm_paths")
        ):
            cron_service.append(
                {
                    "id": "P115StrmHelper_full_sync_strm_files",
                    "name": "定期全量同步115媒体库",
                    "trigger": CronTrigger.from_crontab(
                        configer.get_config("cron_full_sync_strm")
                    ),
                    "func": self.full_sync_strm_files,
                    "kwargs": {},
                }
            )
        if configer.get_config("cron_clear") and (
            configer.get_config("clear_recyclebin_enabled")
            or configer.get_config("clear_receive_path_enabled")
        ):
            cron_service.append(
                {
                    "id": "P115StrmHelper_main_cleaner",
                    "name": "定期清理115空间",
                    "trigger": CronTrigger.from_crontab(
                        configer.get_config("cron_clear")
                    ),
                    "func": self.main_cleaner,
                    "kwargs": {},
                }
            )
        if configer.get_config("increment_sync_strm_enabled") and configer.get_config(
            "increment_sync_strm_paths"
        ):
            cron_service.append(
                {
                    "id": "P115StrmHelper_increment_sync_strm",
                    "name": "115网盘定期增量同步",
                    "trigger": CronTrigger.from_crontab(
                        configer.get_config("increment_sync_cron")
                    ),
                    "func": self.increment_sync_strm_files,
                    "kwargs": {},
                }
            )
        if cron_service:
            return cron_service

    def __update_config(self):
        self.update_config(configer.get_all_configs())

    @staticmethod
    def get_render_mode() -> Tuple[str, Optional[str]]:
        """
        返回插件使用的前端渲染模式
        :return: 前端渲染模式，前端文件目录
        """
        return "vue", "dist/assets"

    def get_form(self) -> Tuple[Optional[List[dict]], Dict[str, Any]]:
        """
        为Vue组件模式返回初始配置数据。
        Vue模式下，第一个参数返回None，第二个参数返回初始配置数据。
        """
        return None, self._get_config_api()

    def get_page(self) -> Optional[List[dict]]:
        """
        Vue模式不使用Vuetify页面定义
        """
        return None

    def _get_path_by_cid(self, cid: int):
        """
        通过 cid 获取路径
        先从缓存获取，再从数据库获取，最后通过API获取
        """
        _databasehelper = FileDbHelper()
        dir_path = self.id_path_cache.get_dir_by_id(cid)
        if not dir_path:
            data = _databasehelper.get_by_id(id=cid)
            if data:
                dir_path = data.get("path", None)
                if dir_path:
                    logger.debug(f"获取 {cid} 路径（数据库）: {dir_path}")
                    self.id_path_cache.add_cache(id=cid, directory=str(dir_path))
                    return Path(dir_path)
            dir_path = get_path_to_cid(self._client, cid=cid)
            self.id_path_cache.add_cache(id=cid, directory=str(dir_path))
            if not dir_path:
                logger.error(f"获取 {cid} 路径失败")
                return None
            logger.debug(f"获取 {cid} 路径（API）: {dir_path}")
            return Path(dir_path)
        logger.debug(f"获取 {cid} 路径（缓存）: {dir_path}")
        return Path(dir_path)

    def media_transfer(self, event, file_path: Path, rmt_mediaext):
        """
        运行媒体文件整理
        :param event: 事件
        :param file_path: 文件路径
        :param rmt_mediaext: 媒体文件后缀名
        """
        transferchain = TransferChain()
        file_category = event["file_category"]
        file_id = event["file_id"]
        if file_category == 0:
            cache_top_path = False
            cache_file_id_list = []
            logger.info(f"【网盘整理】开始处理 {file_path} 文件夹中...")
            # 文件夹情况，遍历文件夹，获取整理文件
            # 缓存顶层文件夹ID
            if str(event["file_id"]) not in self.cache_delete_pan_transfer_list:
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
                    if str(item["id"]) not in self.cache_creata_pan_transfer_list:
                        self.cache_creata_pan_transfer_list.append(str(item["id"]))
                    # 判断此顶层目录MP是否能处理
                    if str(item["parent_id"]) != event["file_id"]:
                        cache_top_path = True
                    if str(item["id"]) not in cache_file_id_list:
                        cache_file_id_list.append(str(item["id"]))
                    transferchain.do_transfer(
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
                if (
                    file_path.suffix in settings.RMT_AUDIOEXT
                    or file_path.suffix in settings.RMT_SUBEXT
                ):
                    # 如果是MP可处理的音轨或字幕文件，则缓存文件ID
                    if str(item["id"]) not in self.cache_creata_pan_transfer_list:
                        self.cache_creata_pan_transfer_list.append(str(item["id"]))

            # 顶层目录MP无法处理时添加到缓存字典中
            if cache_top_path and cache_file_id_list:
                if str(event["file_id"]) in self.cache_top_delete_pan_transfer_list:
                    # 如果存在相同ID的根目录则合并
                    cache_file_id_list = list(
                        dict.fromkeys(
                            chain(
                                cache_file_id_list,
                                self.cache_top_delete_pan_transfer_list[
                                    str(event["file_id"])
                                ],
                            )
                        )
                    )
                    del self.cache_top_delete_pan_transfer_list[str(event["file_id"])]
                self.cache_top_delete_pan_transfer_list[str(event["file_id"])] = (
                    cache_file_id_list
                )
        else:
            # 文件情况，直接整理
            if file_path.suffix in rmt_mediaext:
                # 缓存文件ID
                if str(event["file_id"]) not in self.cache_creata_pan_transfer_list:
                    self.cache_creata_pan_transfer_list.append(str(event["file_id"]))
                transferchain.do_transfer(
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

    def event_handler(self, event, mon_path: str, text: str, event_path: str):
        """
        处理文件变化
        :param event: 事件
        :param mon_path: 监控目录
        :param text: 事件描述
        :param event_path: 事件文件路径
        """
        if not event.is_directory:
            # 文件发生变化
            logger.debug(f"【目录上传】文件 {text}: {event_path}")
            self.__handle_file(event_path=event_path, mon_path=mon_path)

    def __handle_file(self, event_path: str, mon_path: str):
        """
        同步一个文件
        :param event_path: 事件文件路径
        :param mon_path: 监控目录
        """
        file_path = Path(event_path)
        storagechain = StorageChain()
        try:
            if not file_path.exists():
                return
            # 全程加锁
            with directory_upload_dict[str(file_path.absolute())]:
                # 回收站隐藏文件不处理
                if (
                    event_path.find("/@Recycle/") != -1
                    or event_path.find("/#recycle/") != -1
                    or event_path.find("/.") != -1
                    or event_path.find("/@eaDir") != -1
                ):
                    logger.debug(f"【目录上传】{event_path} 是回收站或隐藏的文件")
                    return

                # 蓝光目录不处理
                if re.search(r"BDMV[/\\]STREAM", event_path, re.IGNORECASE):
                    return

                # 先判断文件是否存在
                file_item = storagechain.get_file_item(storage="local", path=file_path)
                if not file_item:
                    logger.warn(f"【目录上传】{event_path} 未找到对应的文件")
                    return

                # 获取此监控目录配置
                for item in configer.get_config("directory_upload_path"):
                    if not item:
                        continue
                    if mon_path == item.get("src", ""):
                        delete = item.get("delete", False)
                        dest_remote = item.get("dest_remote", "")
                        dest_local = item.get("dest_local", "")
                        break

                if file_path.suffix in [
                    f".{ext.strip()}"
                    for ext in configer.get_config("directory_upload_uploadext")
                    .replace("，", ",")
                    .split(",")
                ]:
                    # 处理上传
                    if not dest_remote:
                        logger.error(
                            f"【目录上传】{file_path} 未找到对应的上传网盘目录"
                        )
                        return

                    target_file_path = Path(dest_remote) / Path(file_path).relative_to(
                        mon_path
                    )

                    # 网盘目录创建流程
                    def __find_dir(
                        _fileitem: FileItem, _name: str
                    ) -> Optional[FileItem]:
                        """
                        查找下级目录中匹配名称的目录
                        """
                        for sub_folder in storagechain.list_files(_fileitem):
                            if sub_folder.type != "dir":
                                continue
                            if sub_folder.name == _name:
                                return sub_folder
                        return None

                    target_fileitem = storagechain.get_file_item(
                        storage="u115", path=target_file_path.parent
                    )
                    if not target_fileitem:
                        # 逐级查找和创建目录
                        target_fileitem = FileItem(storage="u115", path="/")
                        for part in target_file_path.parent.parts[1:]:
                            dir_file = __find_dir(target_fileitem, part)
                            if dir_file:
                                target_fileitem = dir_file
                            else:
                                dir_file = storagechain.create_folder(
                                    target_fileitem, part
                                )
                                if not dir_file:
                                    logger.error(
                                        f"【目录上传】创建目录 {target_fileitem.path}{part} 失败！"
                                    )
                                    return
                                target_fileitem = dir_file

                    # 上传流程
                    if storagechain.upload_file(
                        target_fileitem, file_path, file_path.name
                    ):
                        logger.info(
                            f"【目录上传】{file_path} 上传到网盘 {target_file_path} 成功 "
                        )
                    else:
                        logger.error(f"【目录上传】{file_path} 上传网盘失败")
                        return

                elif file_path.suffix in [
                    f".{ext.strip()}"
                    for ext in configer.get_config("directory_upload_copyext")
                    .replace("，", ",")
                    .split(",")
                ]:
                    # 处理非上传文件
                    if dest_local:
                        target_file_path = Path(dest_local) / Path(
                            file_path
                        ).relative_to(mon_path)
                        # 创建本地目录
                        target_file_path.parent.mkdir(parents=True, exist_ok=True)
                        # 复制文件
                        status, msg = SystemUtils.copy(file_path, target_file_path)
                        if status == 0:
                            logger.info(
                                f"【目录上传】{file_path} 复制到 {target_file_path} 成功 "
                            )
                        else:
                            logger.error(f"【目录上传】{file_path} 复制失败: {msg}")
                            return
                else:
                    # 未匹配后缀的文件直接跳过
                    return

                # 处理源文件是否删除
                if delete:
                    logger.info(f"【目录上传】删除源文件：{file_path}")
                    file_path.unlink(missing_ok=True)
                    for file_dir in file_path.parents:
                        if len(str(file_dir)) <= len(str(Path(mon_path))):
                            break
                        files = SystemUtils.list_files(file_dir)
                        if not files:
                            logger.warn(f"【目录上传】删除空目录：{file_dir}")
                            shutil.rmtree(file_dir, ignore_errors=True)

        except Exception as e:
            logger.error(
                f"【目录上传】目录监控发生错误：{str(e)} - {traceback.format_exc()}"
            )
            return

    @eventmanager.register(EventType.TransferComplete)
    def delete_top_pan_transfer_path(self, event: Event):
        """
        处理网盘整理MP无法删除的顶层目录
        """

        if not configer.get_config("pan_transfer_enabled") or not configer.get_config(
            "pan_transfer_paths"
        ):
            return

        if not self.cache_top_delete_pan_transfer_list:
            return

        item = event.event_data
        if not item:
            return

        item_transfer: TransferInfo = item.get("transferinfo")
        dest_fileitem: FileItem = item_transfer.target_item
        src_fileitem: FileItem = item.get("fileitem")

        if item_transfer.transfer_type != "move":
            return

        if dest_fileitem.storage != "u115" or src_fileitem.storage != "u115":
            return

        if not self.pathmatchinghelper.get_run_transfer_path(
            paths=configer.get_config("pan_transfer_paths"),
            transfer_path=src_fileitem.path,
        ):
            return

        remove_id = ""
        # 遍历删除字典
        for key, item_list in self.cache_top_delete_pan_transfer_list.items():
            # 只有目前处理完成的这个文件ID在处理列表中，才表明匹配到了该删除的顶层目录
            if str(dest_fileitem.fileid) in item_list:
                # 从列表中删除这个ID
                self.cache_top_delete_pan_transfer_list[key] = [
                    item for item in item_list if item != str(dest_fileitem.fileid)
                ]
                # 记录需删除的顶层目录
                remove_id = key
                break

        if remove_id:
            # 只有需删除的顶层目录下面的文件全部整理完成才进行删除操作
            if not self.cache_top_delete_pan_transfer_list.get(remove_id):
                del self.cache_top_delete_pan_transfer_list[remove_id]
                resp = self._client.fs_delete(int(remove_id))
                if resp["state"]:
                    logger.info(f"【网盘整理】删除 {remove_id} 文件夹成功")
                else:
                    logger.error(f"【网盘整理】删除 {remove_id} 文件夹失败: {resp}")

        return

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
                if self.pathmatchinghelper.has_prefix(pan_path, pan_media_dir):
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
                return True, str(new_file_path)
            except Exception as e:  # noqa: F841
                logger.error(
                    "【监控整理STRM生成】生成 %s 文件失败: %s", str(new_file_path), e
                )
                return False, None

        if (
            not configer.get_config("enabled")
            or not configer.get_config("transfer_monitor_enabled")
            or not configer.get_config("transfer_monitor_paths")
            or not configer.get_config("moviepilot_address")
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

        item_dest_storage = item_transfer.target_item.storage
        if item_dest_storage != "u115":
            return

        # 网盘目的地目录
        itemdir_dest_path = item_transfer.target_diritem.path
        # 网盘目的地路径（包含文件名称）
        item_dest_path = item_transfer.target_item.path
        # 网盘目的地文件名称
        item_dest_name = item_transfer.target_item.name
        # 网盘目的地文件名称（不包含后缀）
        item_dest_basename = item_transfer.target_item.basename
        # 网盘目的地文件 pickcode
        item_dest_pickcode = item_transfer.target_item.pickcode
        # 是否蓝光原盘
        item_bluray = SystemUtils.is_bluray_dir(Path(itemdir_dest_path))
        # 目标字幕文件清单
        subtitle_list = getattr(item_transfer, "subtitle_list_new", [])
        # 目标音频文件清单
        audio_list = getattr(item_transfer, "audio_list_new", [])

        __itemdir_dest_path, local_media_dir, pan_media_dir = (
            self.pathmatchinghelper.get_media_path(
                configer.get_config("transfer_monitor_paths"), itemdir_dest_path
            )
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
        strm_url = f"{configer.get_config('moviepilot_address').rstrip('/')}/api/v1/plugin/P115StrmHelper/redirect_url?apikey={settings.API_TOKEN}&pickcode={item_dest_pickcode}"
        if configer.get_config("strm_url_format") == "pickname":
            strm_url += f"&file_name={item_dest_name}"

        _databasehelper = FileDbHelper()
        _databasehelper.upsert_batch(
            _databasehelper.process_fileitem(fileitem=item_transfer.target_item)
        )

        status, strm_target_path = generate_strm_files(
            target_dir=local_media_dir,
            pan_media_dir=pan_media_dir,
            item_dest_path=item_dest_path,
            basename=item_dest_basename,
            url=strm_url,
        )
        if not status:
            return

        try:
            storagechain = StorageChain()
            if subtitle_list:
                logger.info("【监控整理STRM生成】开始下载字幕文件")
                for _path in subtitle_list:
                    fileitem = storagechain.get_file_item(
                        storage="u115", path=Path(_path)
                    )
                    _databasehelper.upsert_batch(
                        _databasehelper.process_fileitem(fileitem)
                    )
                    download_url = self.mediainfodownloader.get_download_url(
                        pickcode=fileitem.pickcode
                    )
                    if not download_url:
                        logger.error(
                            f"【监控整理STRM生成】{Path(_path).name} 下载链接获取失败，无法下载该文件"
                        )
                        continue
                    _file_path = Path(local_media_dir) / Path(_path).relative_to(
                        pan_media_dir
                    )
                    self.mediainfodownloader.save_mediainfo_file(
                        file_path=Path(_file_path),
                        file_name=_file_path.name,
                        download_url=download_url,
                    )

            if audio_list:
                logger.info("【监控整理STRM生成】开始下载音频文件")
                for _path in audio_list:
                    fileitem = storagechain.get_file_item(
                        storage="u115", path=Path(_path)
                    )
                    _databasehelper.upsert_batch(
                        _databasehelper.process_fileitem(fileitem)
                    )
                    download_url = self.mediainfodownloader.get_download_url(
                        pickcode=fileitem.pickcode
                    )
                    if not download_url:
                        logger.error(
                            f"【监控整理STRM生成】{Path(_path).name} 下载链接获取失败，无法下载该文件"
                        )
                        continue
                    _file_path = Path(local_media_dir) / Path(_path).relative_to(
                        pan_media_dir
                    )
                    self.mediainfodownloader.save_mediainfo_file(
                        file_path=Path(_file_path),
                        file_name=_file_path.name,
                        download_url=download_url,
                    )
        except Exception as e:
            logger.error(f"【监控整理STRM生成】媒体信息文件下载出现未知错误: {e}")

        scrape_metadata = True
        if configer.get_config("transfer_monitor_scrape_metadata_enabled"):
            if configer.get_config("transfer_monitor_scrape_metadata_exclude_paths"):
                if self.pathmatchinghelper.get_scrape_metadata_exclude_path(
                    configer.get_config(
                        "transfer_monitor_scrape_metadata_exclude_paths"
                    ),
                    str(strm_target_path),
                ):
                    logger.debug(
                        f"【监控整理STRM生成】匹配到刮削排除目录，不进行刮削: {strm_target_path}"
                    )
                    scrape_metadata = False
            if scrape_metadata:
                media_scrape_metadata(
                    path=strm_target_path,
                    item_name=item_dest_name,
                    mediainfo=mediainfo,
                    meta=meta,
                )

        if configer.get_config("transfer_monitor_media_server_refresh_enabled"):
            if not self.transfer_service_infos:
                return

            logger.info(f"【监控整理STRM生成】 {item_dest_name} 开始刷新媒体服务器")

            if configer.get_config("transfer_mp_mediaserver_paths"):
                status, mediaserver_path, moviepilot_path = (
                    self.pathmatchinghelper.get_media_path(
                        configer.get_config("transfer_mp_mediaserver_paths"),
                        strm_target_path,
                    )
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
        if not event:
            return
        event_data = event.event_data
        if not event_data or event_data.get("action") != "p115_full_sync":
            return
        self.post_message(
            channel=event.event_data.get("channel"),
            title="开始115网盘媒体库全量同步 ...",
            userid=event.event_data.get("user"),
        )
        self.full_sync_strm_files()

    @eventmanager.register(EventType.PluginAction)
    def p115_inc_sync(self, event: Event):
        """
        远程增量同步
        """
        if not event:
            return
        event_data = event.event_data
        if not event_data or event_data.get("action") != "p115_inc_sync":
            return
        self.post_message(
            channel=event.event_data.get("channel"),
            title="开始115网盘媒体库增量同步 ...",
            userid=event.event_data.get("user"),
        )
        self.increment_sync_strm_files(send_msg=True)

    @eventmanager.register(EventType.PluginAction)
    def p115_strm(self, event: Event):
        """
        全量生成指定网盘目录STRM
        """
        if not event:
            return
        event_data = event.event_data
        if not event_data or event_data.get("action") != "p115_strm":
            return
        args = event_data.get("arg_str")
        if not args:
            logger.error(f"【全量STRM生成】缺少参数：{event_data}")
            self.post_message(
                channel=event.event_data.get("channel"),
                title="参数错误！ /p115_strm 网盘路径",
                userid=event.event_data.get("user"),
            )
            return
        if (
            not configer.get_config("full_sync_strm_paths")
            or not configer.get_config("moviepilot_address")
            or not configer.get_config("user_download_mediaext")
        ):
            self.post_message(
                channel=event.event_data.get("channel"),
                title="全量同步配置错误，请前往插件配置！",
                userid=event.event_data.get("user"),
            )
            return
        status, paths = self.pathmatchinghelper.get_p115_strm_path(
            paths=configer.get_config("full_sync_strm_paths"), media_path=args
        )
        if not status:
            self.post_message(
                channel=event.event_data.get("channel"),
                title=f"{args} 匹配目录失败，请检查输入路径和插件配置！",
                userid=event.event_data.get("user"),
            )
            return
        strm_helper = FullSyncStrmHelper(
            user_rmt_mediaext=configer.get_config("user_rmt_mediaext"),
            user_download_mediaext=configer.get_config("user_download_mediaext"),
            auto_download_mediainfo=configer.get_config(
                "full_sync_auto_download_mediainfo_enabled"
            ),
            client=self._client,
            mediainfodownloader=self.mediainfodownloader,
            server_address=configer.get_config("moviepilot_address"),
            pan_transfer_enabled=configer.get_config("pan_transfer_enabled"),
            pan_transfer_paths=configer.get_config("pan_transfer_paths"),
            strm_url_format=configer.get_config("strm_url_format"),
            overwrite_mode=configer.get_config("full_sync_overwrite_mode"),
            remove_unless_strm=configer.get_config("full_sync_remove_unless_strm"),
        )
        self.post_message(
            channel=event.event_data.get("channel"),
            title=f"开始 {args} 全量同步 ...",
            userid=event.event_data.get("user"),
        )
        strm_helper.generate_strm_files(
            full_sync_strm_paths=paths,
        )
        (
            strm_count,
            mediainfo_count,
            strm_fail_count,
            mediainfo_fail_count,
            remove_unless_strm_count,
        ) = strm_helper.get_generate_total()
        text = f"""
📂 网盘路径：{args}
📄 生成STRM文件 {strm_count} 个
⬇️ 下载媒体文件 {mediainfo_count} 个
❌ 生成STRM失败 {strm_fail_count} 个
🚫 下载媒体失败 {mediainfo_fail_count} 个
"""
        if remove_unless_strm_count != 0:
            text += f"🗑️ 清理无效STRM文件 {remove_unless_strm_count} 个"
        self.post_message(
            channel=event.event_data.get("channel"),
            userid=event.event_data.get("user"),
            title="✅【115网盘】全量生成 STRM 文件完成",
            text=text,
        )

    @eventmanager.register(EventType.PluginAction)
    def p115_search(self, event: Event):
        """
        处理搜索请求
        """
        if not event:
            return
        event_data = event.event_data
        if not event_data or event_data.get("action") != "p115_search":
            return
        args = event_data.get("arg_str")
        if not args:
            logger.error(f"【搜索】缺少参数：{event_data}")
            self.post_message(
                channel=event.event_data.get("channel"),
                title="参数错误！ /p115_search 搜索关键词",
                userid=event.event_data.get("user"),
            )
            return

        try:
            session = session_manager.get_or_create(
                event_data, plugin_id=self.__class__.__name__
            )

            search_keyword = args.strip()
            action = Action(command="search", view="search_list", value=search_keyword)

            immediate_messages = self.action_handler.process(session, action)
            # 报错，截断后续运行
            if immediate_messages:
                for msg in immediate_messages:
                    self.__send_message(session, text=msg.get("text"), title="错误")
                    return

            # 设置页面为search_list
            session.go_to("search_list")
            self._render_and_send(session)
        except Exception as e:
            logger.error(f"处理 search 命令失败: {e}", exc_info=True)

    @eventmanager.register(EventType.MessageAction)
    def message_action(self, event: Event):
        """
        处理按钮点击回调
        """
        try:
            event_data = event.event_data
            callback_text = event_data.get("text", "")

            # 1. 解码 Action callback_text = c:xxx|w:xxx|v|xxx
            session_id, action = decode_action(callback_text=callback_text)
            if not session_id or not action:
                # 如果解码失败或不属于本插件，则忽略
                return

            # 2. 获取会话
            session = session_manager.get(session_id)
            if not session:
                context = {
                    "channel": event_data.get("channel"),
                    "source": event_data.get("source"),
                    "userid": event_data.get("userid") or event_data.get("user"),
                    "original_message_id": event_data.get("original_message_id"),
                    "original_chat_id": event_data.get("original_chat_id"),
                }
                self.post_message(
                    **context,
                    title="⚠️ 会话已过期",
                    text="操作已超时。\n请重新发起 `/p115_search` 命令。",
                )
                return

            # 3. 更新会话上下文
            session.update_message_context(event_data)

            # 4. 委托给 ActionHandler 处理业务逻辑
            immediate_messages = self.action_handler.process(session, action)
            if immediate_messages:
                for msg in immediate_messages:
                    self.__send_message(session, text=msg.get("text"), title="错误")
                    return

            # 5. 渲染新视图并发送
            self._render_and_send(session)
        except Exception as e:
            logger.debug(f"出错了：{e}", exc_info=True)

    def _render_and_send(self, session: TSession):
        """
        根据 Session 的当前状态，渲染视图并发送/编辑消息。
        """
        # 1. 委托给 ViewRenderer 生成界面数据
        render_data = self.view_renderer.render(session)

        # 2. 发送或编辑消息
        self.__send_message(session, render_data=render_data)

        # 3. 处理会话结束逻辑
        if session.view.name in ["subscribe_success", "close"]:
            # 深复制会话的删除消息数据
            delete_message_data = deepcopy(session.get_delete_message_data())
            session_manager.end(session.session_id)
            # 等待一段时间让用户看到最后一条消息
            time.sleep(5)
            self.__delete_message(**delete_message_data)

    def __send_message(
        self, session: TSession, render_data: Optional[dict] = None, **kwargs
    ):
        """
        统一的消息发送接口。
        """
        context = asdict(session.message)
        if render_data:
            context.update(render_data)
        context.update(kwargs)
        # 将 user key改名成 userid，规避传入值只是user
        userid = context.get("user")
        if userid:
            context["userid"] = userid
            # 删除多余的 user 键
            context.pop("user", None)
        self.post_message(**context)

    def __delete_message(
        self,
        channel: MessageChannel,
        source: str,
        message_id: Union[str, int],
        chat_id: Optional[Union[str, int]] = None,
    ) -> bool:
        """
        删除会话中的原始消息。
        """
        # 兼容旧版本无删除方法
        if hasattr(self.chain, "delete_message"):
            return self.chain.delete_message(
                channel=channel, source=source, message_id=message_id, chat_id=chat_id
            )
        return False

    def _ensure_add_share_worker_running(self):
        """
        确保工作线程正在运行
        """
        with self._add_share_worker_lock:
            if (
                self._add_share_worker_thread is None
                or not self._add_share_worker_thread.is_alive()
            ):
                self._add_share_worker_thread = threading.Thread(
                    target=self._process_add_share_queue, daemon=True
                )
                self._add_share_worker_thread.start()

    def _process_add_share_queue(self):
        """
        处理队列中的任务
        """
        while True:
            try:
                # 获取任务，设置超时避免永久阻塞
                task = self._add_share_queue.get(timeout=60)  # 60秒无任务则退出
                url, channel, userid = task

                # 执行任务
                self.__add_share(url, channel, userid)

                # 任务间隔
                time.sleep(3)

                # 标记任务完成
                self._add_share_queue.task_done()

            except Empty:
                logger.debug("【分享转存】释放分享转存队列进程")
                break
            except Exception as e:
                logger.error(f"【分享转存】任务处理异常: {e}")
                time.sleep(5)

    def __add_share_recognize_mediainfo(self, share_code: str, receive_code: str):
        """
        分享转存识别媒体信息
        """
        file_num = 0
        file_mediainfo = None
        for item in share_iterdir(
            self._client,
            receive_code=receive_code,
            share_code=share_code,
            cid=0,
        ):
            if file_num == 1:
                file_num = 2
                break
            item_name = item["name"]
            file_num += 1
        if file_num == 1:
            mediachain = MediaChain()
            file_meta = MetaInfo(title=item_name)
            file_mediainfo = mediachain.recognize_by_meta(file_meta)
        return file_mediainfo

    def __add_share(self, url, channel, userid):
        """
        分享转存
        """
        if not configer.get_config("pan_transfer_enabled") or not configer.get_config(
            "pan_transfer_paths"
        ):
            self.post_message(
                channel=channel,
                title="配置错误！ 请先进入插件界面配置网盘整理",
                userid=userid,
            )
            return
        try:
            data = share_extract_payload(url)
            share_code = data["share_code"]
            receive_code = data["receive_code"]
            logger.info(
                f"【分享转存】解析分享链接 share_code={share_code} receive_code={receive_code}"
            )
            if not share_code or not receive_code:
                logger.error(f"【分享转存】解析分享链接失败：{url}")
                self.post_message(
                    channel=channel,
                    title=f"解析分享链接失败：{url}",
                    userid=userid,
                )
                return
            parent_path = configer.get_config("pan_transfer_paths").split("\n")[0]
            parent_id = self.id_path_cache.get_id_by_dir(directory=str(parent_path))
            if not parent_id:
                parent_id = self._client.fs_dir_getid(parent_path)["id"]
                logger.info(f"【分享转存】获取到转存目录 ID：{parent_id}")
                self.id_path_cache.add_cache(
                    id=int(parent_id), directory=str(parent_path)
                )
            payload = {
                "share_code": share_code,
                "receive_code": receive_code,
                "file_id": 0,
                "cid": int(parent_id),
                "is_check": 0,
            }
            logger.info(f"【分享转存】开始转存：{share_code}")
            self.post_message(
                channel=channel,
                title=f"开始转存：{share_code}",
                userid=userid,
            )

            # 尝试识别媒体信息
            file_mediainfo = self.__add_share_recognize_mediainfo(
                share_code=share_code, receive_code=receive_code
            )

            resp = self._client.share_receive(payload)
            if resp["state"]:
                logger.info(f"【分享转存】转存 {share_code} 到 {parent_path} 成功！")
                if not file_mediainfo:
                    self.post_message(
                        channel=channel,
                        title=f"转存 {share_code} 到 {parent_path} 成功！",
                        userid=userid,
                    )
                else:
                    self.post_message(
                        channel=channel,
                        title=f"转存 {file_mediainfo.title}（{file_mediainfo.year}）成功",
                        text=f"\n简介: {file_mediainfo.overview}",
                        image=file_mediainfo.poster_path,
                        userid=userid,
                    )
            else:
                logger.info(f"【分享转存】转存 {share_code} 失败：{resp['error']}")
                self.post_message(
                    channel=channel,
                    title=f"转存 {share_code} 失败：{resp['error']}",
                    userid=userid,
                )
            return
        except Exception as e:
            logger.error(f"【分享转存】运行失败: {e}")
            return

    def add_share(self, url, channel, userid):
        """
        将分享任务加入队列
        """
        self._add_share_queue.put((url, channel, userid))
        logger.info(
            f"【分享转存】{url} 任务已加入分享转存队列，当前队列大小：{self._add_share_queue.qsize()}"
        )
        self._ensure_add_share_worker_running()

    def add_transfer_share(self, share_url: str = "") -> Dict:
        """
        添加分享转存整理
        """
        if not configer.get_config("pan_transfer_enabled") or not configer.get_config(
            "pan_transfer_paths"
        ):
            return {
                "code": -1,
                "error": "配置错误",
                "message": "用户未配置网盘整理",
            }

        if not share_url:
            return {
                "code": -1,
                "error": "参数错误",
                "message": "未传入分享链接",
            }

        data = share_extract_payload(share_url)
        share_code = data["share_code"]
        receive_code = data["receive_code"]
        logger.info(
            f"【分享转存API】解析分享链接 share_code={share_code} receive_code={receive_code}"
        )
        if not share_code or not receive_code:
            logger.error(f"【分享转存API】解析分享链接失败：{share_url}")
            return {
                "code": -1,
                "error": "解析失败",
                "message": "解析分享链接失败",
            }

        file_mediainfo = self.__add_share_recognize_mediainfo(
            share_code=share_code, receive_code=receive_code
        )

        parent_path = configer.get_config("pan_transfer_paths").split("\n")[0]
        parent_id = self.id_path_cache.get_id_by_dir(directory=str(parent_path))
        if not parent_id:
            parent_id = self._client.fs_dir_getid(parent_path)["id"]
            logger.info(f"【分享转存API】获取到转存目录 ID：{parent_id}")
            self.id_path_cache.add_cache(id=int(parent_id), directory=str(parent_path))

        payload = {
            "share_code": share_code,
            "receive_code": receive_code,
            "file_id": 0,
            "cid": int(parent_id),
            "is_check": 0,
        }
        resp = self._client.share_receive(payload)
        if resp["state"]:
            logger.info(f"【分享转存API】转存 {share_code} 到 {parent_path} 成功！")
            if configer.get_config("notify"):
                if not file_mediainfo:
                    self.post_message(
                        mtype=NotificationType.Plugin,
                        title=f"转存 {share_code} 到 {parent_path} 成功！",
                    )
                else:
                    self.post_message(
                        mtype=NotificationType.Plugin,
                        title=f"转存 {file_mediainfo.title}（{file_mediainfo.year}）成功",
                        text=f"\n简介: {file_mediainfo.overview}",
                        image=file_mediainfo.poster_path,
                    )
            return {
                "code": 0,
                "success": True,
                "message": "转存成功",
                "data": {
                    "media_info": asdict(file_mediainfo) if file_mediainfo else None,
                    "save_parent": {"path": parent_path, "id": parent_id},
                },
                "timestamp": datetime.now().isoformat(),
            }

        logger.info(f"【分享转存API】转存 {share_code} 失败：{resp['error']}")
        return {
            "code": -1,
            "error": "转存失败",
            "message": resp["error"],
        }

    @eventmanager.register(EventType.UserMessage)
    def user_add_share(self, event: Event):
        """
        远程分享转存
        """
        if not configer.get_config("enabled"):
            return
        text = event.event_data.get("text")
        userid = event.event_data.get("userid")
        channel = event.event_data.get("channel")
        if not text:
            return
        if not text.startswith("http"):
            return
        if not bool(re.match(r"^https?://(.*\.)?115[^/]*\.[a-zA-Z]{2,}(?:\/|$)", text)):
            return
        self.add_share(
            url=text,
            channel=channel,
            userid=userid,
        )
        return

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
        self.add_share(
            url=args,
            channel=event.event_data.get("channel"),
            userid=event.event_data.get("user"),
        )
        return

    def full_sync_strm_files(self):
        """
        全量同步
        """
        if (
            not configer.get_config("full_sync_strm_paths")
            or not configer.get_config("moviepilot_address")
            or not configer.get_config("user_download_mediaext")
        ):
            return

        strm_helper = FullSyncStrmHelper(
            user_rmt_mediaext=configer.get_config("user_rmt_mediaext"),
            user_download_mediaext=configer.get_config("user_download_mediaext"),
            auto_download_mediainfo=configer.get_config(
                "full_sync_auto_download_mediainfo_enabled"
            ),
            client=self._client,
            mediainfodownloader=self.mediainfodownloader,
            server_address=configer.get_config("moviepilot_address"),
            pan_transfer_enabled=configer.get_config("pan_transfer_enabled"),
            pan_transfer_paths=configer.get_config("pan_transfer_paths"),
            strm_url_format=configer.get_config("strm_url_format"),
            overwrite_mode=configer.get_config("full_sync_overwrite_mode"),
            remove_unless_strm=configer.get_config("full_sync_remove_unless_strm"),
        )
        strm_helper.generate_strm_files(
            full_sync_strm_paths=configer.get_config("full_sync_strm_paths"),
        )
        (
            strm_count,
            mediainfo_count,
            strm_fail_count,
            mediainfo_fail_count,
            remove_unless_strm_count,
        ) = strm_helper.get_generate_total()
        if configer.get_config("notify"):
            text = f"""
📄 生成STRM文件 {strm_count} 个
⬇️ 下载媒体文件 {mediainfo_count} 个
❌ 生成STRM失败 {strm_fail_count} 个
🚫 下载媒体失败 {mediainfo_fail_count} 个
"""
            if remove_unless_strm_count != 0:
                text += f"🗑️ 清理无效STRM文件 {remove_unless_strm_count} 个"
            self.post_message(
                mtype=NotificationType.Plugin,
                title="✅【115网盘】全量生成 STRM 文件完成",
                text=text,
            )

    def increment_sync_strm_files(self, send_msg: bool = False):
        """
        增量同步
        """
        if (
            not configer.get_config("increment_sync_strm_paths")
            or not configer.get_config("moviepilot_address")
            or not configer.get_config("user_download_mediaext")
        ):
            return

        strm_helper = IncrementSyncStrmHelper(
            user_rmt_mediaext=configer.get_config("user_rmt_mediaext"),
            user_download_mediaext=configer.get_config("user_download_mediaext"),
            auto_download_mediainfo=configer.get_config(
                "increment_sync_auto_download_mediainfo_enabled"
            ),
            client=self._client,
            mediainfodownloader=self.mediainfodownloader,
            server_address=configer.get_config("moviepilot_address"),
            pan_transfer_enabled=configer.get_config("pan_transfer_enabled"),
            pan_transfer_paths=configer.get_config("pan_transfer_paths"),
            strm_url_format=configer.get_config("strm_url_format"),
            id_path_cache=self.id_path_cache,
            mp_mediaserver_paths=configer.get_config(
                "increment_sync_mp_mediaserver_paths"
            ),
            scrape_metadata_enabled=configer.get_config(
                "increment_sync_scrape_metadata_enabled"
            ),
            scrape_metadata_exclude_paths=configer.get_config(
                "increment_sync_scrape_metadata_exclude_paths"
            ),
            media_server_refresh_enabled=configer.get_config(
                "increment_sync_media_server_refresh_enabled"
            ),
            mediaservers=configer.get_config("increment_sync_mediaservers"),
        )
        strm_helper.generate_strm_files(
            sync_strm_paths=configer.get_config("increment_sync_strm_paths"),
        )
        (
            strm_count,
            mediainfo_count,
            strm_fail_count,
            mediainfo_fail_count,
        ) = strm_helper.get_generate_total()
        if configer.get_config("notify") and (
            send_msg
            or (
                strm_count != 0
                or mediainfo_count != 0
                or strm_fail_count != 0
                or mediainfo_fail_count != 0
            )
        ):
            text = f"""
📄 生成STRM文件 {strm_count} 个
⬇️ 下载媒体文件 {mediainfo_count} 个
❌ 生成STRM失败 {strm_fail_count} 个
🚫 下载媒体失败 {mediainfo_fail_count} 个
"""
            self.post_message(
                mtype=NotificationType.Plugin,
                title="✅【115网盘】增量生成 STRM 文件完成",
                text=text,
            )

    def share_strm_files(self):
        """
        分享生成STRM
        """
        if (
            not configer.get_config("user_share_pan_path")
            or not configer.get_config("user_share_local_path")
            or not configer.get_config("moviepilot_address")
        ):
            return

        if configer.get_config("user_share_link"):
            data = share_extract_payload(configer.get_config("user_share_link"))
            share_code = data["share_code"]
            receive_code = data["receive_code"]
            logger.info(
                f"【分享STRM生成】解析分享链接 share_code={share_code} receive_code={receive_code}"
            )
        else:
            if not configer.get_config("user_share_code") or not configer.get_config(
                "user_receive_code"
            ):
                return
            share_code = configer.get_config("user_share_code")
            receive_code = configer.get_config("user_receive_code")

        try:
            strm_helper = ShareStrmHelper(
                user_rmt_mediaext=configer.get_config("user_rmt_mediaext"),
                user_download_mediaext=configer.get_config("user_download_mediaext"),
                auto_download_mediainfo=configer.get_config(
                    "share_strm_auto_download_mediainfo_enabled"
                ),
                client=self._client,
                server_address=configer.get_config("moviepilot_address"),
                share_media_path=configer.get_config("user_share_pan_path"),
                local_media_path=configer.get_config("user_share_local_path"),
                strm_url_format=configer.get_config("strm_url_format"),
                mediainfodownloader=self.mediainfodownloader,
            )
            strm_helper.get_share_list_creata_strm(
                cid=0,
                share_code=share_code,
                receive_code=receive_code,
            )
            strm_helper.download_mediainfo()
            strm_count, mediainfo_count, strm_fail_count, mediainfo_fail_count = (
                strm_helper.get_generate_total()
            )
            if configer.get_config("notify"):
                self.post_message(
                    mtype=NotificationType.Plugin,
                    title="✅【115网盘】分享生成 STRM 文件完成",
                    text=f"\n📄 生成STRM文件 {strm_count} 个\n"
                    + f"⬇️ 下载媒体文件 {mediainfo_count} 个\n"
                    + f"❌ 生成STRM失败 {strm_fail_count} 个\n"
                    + f"🚫 下载媒体失败 {mediainfo_fail_count} 个",
                )
        except Exception as e:
            logger.error(f"【分享STRM生成】运行失败: {e}")
            return

    @eventmanager.register(EventType.TransferComplete)
    def fix_monitor_life_strm(self, event: Event):
        """
        监控整理事件
        处理115生活事件生成MP整理STRM文件名称错误
        """

        def refresh_mediaserver(file_path: str, file_name: str):
            """
            刷新媒体服务器
            """
            if configer.get_config("monitor_life_media_server_refresh_enabled"):
                if not self.monitor_life_service_infos:
                    return
                logger.info(f"【监控生活事件】 {file_name} 开始刷新媒体服务器")
                if configer.get_config("monitor_life_mp_mediaserver_paths"):
                    status, mediaserver_path, moviepilot_path = (
                        self.pathmatchinghelper.get_media_path(
                            configer.get_config("monitor_life_mp_mediaserver_paths"),
                            file_path,
                        )
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

        def file_rename(fileitem: FileItem, refresh: bool = False):
            """
            重命名
            """
            target_path = Path(fileitem.path).parent
            file_item = self.cache_create_strm_file_dict.get(str(fileitem.fileid), None)
            if not file_item:
                return
            if fileitem.name != file_item[0]:
                # 文件名称不一致，表明网盘文件被重命名，需要将本地文件重命名
                target_file_path = Path(file_item[1]) / Path(
                    target_path / fileitem.name
                ).relative_to(file_item[2]).with_suffix(".strm")
                life_path = Path(file_item[1]) / Path(
                    target_path / file_item[0]
                ).relative_to(file_item[2]).with_suffix(".strm")
                # 如果重命名后的文件存在，先删除再重命名
                try:
                    if target_file_path.exists():
                        target_file_path.unlink(missing_ok=True)
                    life_path.rename(target_file_path)
                    _databasehelper.update_path_by_id(
                        id=int(fileitem.fileid),
                        new_path=str(target_path / fileitem.name),
                    )
                    _databasehelper.update_name_by_id(
                        id=int(fileitem.fileid),
                        new_name=str(fileitem.name),
                    )
                    self.cache_create_strm_file_dict.pop(str(fileitem.fileid), None)
                    logger.info(
                        f"【监控生活事件】修正文件名称: {life_path} --> {target_file_path}"
                    )
                    if refresh:
                        refresh_mediaserver(
                            file_path=str(target_file_path),
                            file_name=str(target_file_path.name),
                        )
                    return
                except Exception as e:
                    logger.error(f"【监控生活事件】修正文件名称失败: {e}")

        # 生活事件已开启
        if (
            not configer.get_config("monitor_life_enabled")
            or not configer.get_config("monitor_life_paths")
            or not configer.get_config("monitor_life_event_modes")
        ):
            return

        # 生活事件在运行
        if not bool(self.monitor_life_thread and self.monitor_life_thread.is_alive()):
            return

        item = event.event_data
        if not item:
            return

        # 整理信息
        item_transfer: TransferInfo = item.get("transferinfo")
        # 目的地文件 fileitem
        dest_fileitem: FileItem = item_transfer.target_item
        # 目标字幕文件清单
        subtitle_list = getattr(item_transfer, "subtitle_list_new", [])
        # 目标音频文件清单
        audio_list = getattr(item_transfer, "audio_list_new", [])

        _databasehelper = FileDbHelper()

        file_rename(fileitem=dest_fileitem, refresh=True)

        storagechain = StorageChain()
        if subtitle_list:
            for _path in subtitle_list:
                fileitem = storagechain.get_file_item(storage="u115", path=Path(_path))
                file_rename(fileitem=fileitem)

        if audio_list:
            for _path in audio_list:
                fileitem = storagechain.get_file_item(storage="u115", path=Path(_path))
                file_rename(fileitem=fileitem)

    def monitor_life_strm_files(self):
        """
        监控115生活事件

        {
            1: "upload_image_file",  上传图片 生成 STRM;写入数据库
            2: "upload_file",        上传文件/目录 生成 STRM;写入数据库
            3: "star_image",         标星图片 无操作
            4: "star_file",          标星文件/目录 无操作
            5: "move_image_file",    移动图片 生成 STRM;写入数据库
            6: "move_file",          移动文件/目录 生成 STRM;写入数据库
            7: "browse_image",       浏览图片 无操作
            8: "browse_video",       浏览视频 无操作
            9: "browse_audio",       浏览音频 无操作
            10: "browse_document",   浏览文档 无操作
            14: "receive_files",     接收文件 生成 STRM;写入数据库
            17: "new_folder",        创建新目录 写入数据库
            18: "copy_folder",       复制文件夹 生成 STRM;写入数据库
            19: "folder_label",      标签文件夹 无操作
            20: "folder_rename",     重命名文件夹 无操作
            22: "delete_file",       删除文件/文件夹 删除 STRM;移除数据库
        }

        注意: 目前没有重命名文件，复制文件的操作事件
        """

        def _schedule_notification():
            """
            安排通知发送，如果一分钟内没有新事件则发送
            """
            if self._monitor_life_notification_timer:
                self._monitor_life_notification_timer.cancel()

            self._monitor_life_notification_timer = Timer(60.0, _send_notification)
            self._monitor_life_notification_timer.start()

        def _send_notification():
            """
            发送合并后的通知
            """
            if "life" not in self._monitor_life_notification_queue:
                return

            counts = self._monitor_life_notification_queue["life"]
            if counts["strm_count"] == 0 and counts["mediainfo_count"] == 0:
                return

            text_parts = []
            if counts["strm_count"] > 0:
                text_parts.append(f"📄 生成STRM文件 {counts['strm_count']} 个")
            if counts["mediainfo_count"] > 0:
                text_parts.append(f"⬇️ 下载媒体文件 {counts['mediainfo_count']} 个")

            if text_parts and configer.get_config("notify"):
                self.post_message(
                    mtype=NotificationType.Plugin,
                    title="✅【115网盘】生活事件生成 STRM 文件",
                    text="\n" + "\n".join(text_parts),
                )

            # 重置计数器
            self._monitor_life_notification_queue["life"] = {
                "strm_count": 0,
                "mediainfo_count": 0,
            }

        def refresh_mediaserver(file_path: str, file_name: str):
            """
            刷新媒体服务器
            """
            if configer.get_config("monitor_life_media_server_refresh_enabled"):
                if not self.monitor_life_service_infos:
                    return
                logger.info(f"【监控生活事件】 {file_name} 开始刷新媒体服务器")
                if configer.get_config("monitor_life_mp_mediaserver_paths"):
                    status, mediaserver_path, moviepilot_path = (
                        self.pathmatchinghelper.get_media_path(
                            configer.get_config("monitor_life_mp_mediaserver_paths"),
                            file_path,
                        )
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
            _databasehelper = FileDbHelper()

            pickcode = event["pick_code"]
            file_category = event["file_category"]
            file_id = event["file_id"]
            status, target_dir, pan_media_dir = self.pathmatchinghelper.get_media_path(
                configer.get_config("monitor_life_paths"), file_path
            )
            if not status:
                return
            logger.debug("【监控生活事件】匹配到网盘文件夹路径: %s", str(pan_media_dir))

            if file_category == 0:
                # 文件夹情况，遍历文件夹
                mediainfo_count = 0
                strm_count = 0
                _databasehelper.upsert_batch(
                    _databasehelper.process_life_dir_item(
                        event=event, file_path=file_path
                    )
                )
                for batch in batched(
                    iter_files_with_path(self._client, cid=int(file_id), cooldown=2),
                    7_000,
                ):
                    processed = []
                    for item in batch:
                        _process_item = _databasehelper.process_item(item)
                        if _process_item not in processed:
                            processed.extend(_process_item)
                        if item["is_dir"] or item["is_directory"]:
                            continue
                        if "creata" in configer.get_config("monitor_life_event_modes"):
                            file_path = item["path"]
                            file_path = Path(target_dir) / Path(file_path).relative_to(
                                pan_media_dir
                            )
                            file_target_dir = file_path.parent
                            original_file_name = file_path.name
                            file_name = file_path.stem + ".strm"
                            new_file_path = file_target_dir / file_name

                            if configer.get_config(
                                "monitor_life_auto_download_mediainfo_enabled"
                            ):
                                if file_path.suffix in download_mediaext:
                                    pickcode = item["pickcode"]
                                    if not pickcode:
                                        logger.error(
                                            f"【监控生活事件】{original_file_name} 不存在 pickcode 值，无法下载该文件"
                                        )
                                        continue
                                    download_url = (
                                        self.mediainfodownloader.get_download_url(
                                            pickcode=pickcode
                                        )
                                    )

                                    if not download_url:
                                        logger.error(
                                            f"【监控生活事件】{original_file_name} 下载链接获取失败，无法下载该文件"
                                        )
                                        continue

                                    self.mediainfodownloader.save_mediainfo_file(
                                        file_path=Path(file_path),
                                        file_name=original_file_name,
                                        download_url=download_url,
                                    )
                                    mediainfo_count += 1
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
                            strm_url = f"{configer.get_config('moviepilot_address').rstrip('/')}/api/v1/plugin/P115StrmHelper/redirect_url?apikey={settings.API_TOKEN}&pickcode={pickcode}"
                            if configer.get_config("strm_url_format") == "pickname":
                                strm_url += f"&file_name={original_file_name}"

                            with open(new_file_path, "w", encoding="utf-8") as file:
                                file.write(strm_url)
                            logger.info(
                                "【监控生活事件】生成 STRM 文件成功: %s",
                                str(new_file_path),
                            )
                            strm_count += 1
                            scrape_metadata = True
                            if configer.get_config(
                                "monitor_life_scrape_metadata_enabled"
                            ):
                                if configer.get_config(
                                    "monitor_life_scrape_metadata_exclude_paths"
                                ):
                                    if self.pathmatchinghelper.get_scrape_metadata_exclude_path(
                                        configer.get_config(
                                            "monitor_life_scrape_metadata_exclude_paths"
                                        ),
                                        str(new_file_path),
                                    ):
                                        logger.debug(
                                            f"【监控生活事件】匹配到刮削排除目录，不进行刮削: {new_file_path}"
                                        )
                                        scrape_metadata = False
                                if scrape_metadata:
                                    media_scrape_metadata(
                                        path=new_file_path,
                                    )
                            # 刷新媒体服务器
                            refresh_mediaserver(
                                str(new_file_path), str(original_file_name)
                            )
                    _databasehelper.upsert_batch(processed)
                if configer.get_config("notify"):
                    if strm_count > 0 or mediainfo_count > 0:
                        self._monitor_life_notification_queue["life"]["strm_count"] += (
                            strm_count
                        )
                        self._monitor_life_notification_queue["life"][
                            "mediainfo_count"
                        ] += mediainfo_count
                        _schedule_notification()
            else:
                _databasehelper.upsert_batch(
                    _databasehelper.process_life_file_item(
                        event=event, file_path=file_path
                    )
                )
                if "creata" in configer.get_config("monitor_life_event_modes"):
                    # 文件情况，直接生成
                    file_path = Path(target_dir) / Path(file_path).relative_to(
                        pan_media_dir
                    )
                    file_target_dir = file_path.parent
                    original_file_name = file_path.name
                    file_name = file_path.stem + ".strm"
                    new_file_path = file_target_dir / file_name

                    if configer.get_config(
                        "monitor_life_auto_download_mediainfo_enabled"
                    ):
                        if file_path.suffix in download_mediaext:
                            if not pickcode:
                                logger.error(
                                    f"【监控生活事件】{original_file_name} 不存在 pickcode 值，无法下载该文件"
                                )
                                return
                            download_url = self.mediainfodownloader.get_download_url(
                                pickcode=pickcode
                            )

                            if not download_url:
                                logger.error(
                                    f"【监控生活事件】{original_file_name} 下载链接获取失败，无法下载该文件"
                                )
                                return

                            self.mediainfodownloader.save_mediainfo_file(
                                file_path=Path(file_path),
                                file_name=original_file_name,
                                download_url=download_url,
                            )
                            # 下载的元数据写入缓存，与整理事件对比
                            self.cache_create_strm_file_dict[str(event["file_id"])] = [
                                event["file_name"],
                                target_dir,
                                pan_media_dir,
                            ]
                            if configer.get_config("notify"):
                                self._monitor_life_notification_queue["life"][
                                    "mediainfo_count"
                                ] += 1
                                _schedule_notification()
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
                    strm_url = f"{configer.get_config('moviepilot_address').rstrip('/')}/api/v1/plugin/P115StrmHelper/redirect_url?apikey={settings.API_TOKEN}&pickcode={pickcode}"
                    if configer.get_config("strm_url_format") == "pickname":
                        strm_url += f"&file_name={original_file_name}"

                    with open(new_file_path, "w", encoding="utf-8") as file:
                        file.write(strm_url)
                    logger.info(
                        "【监控生活事件】生成 STRM 文件成功: %s", str(new_file_path)
                    )
                    # 生成的STRM写入缓存，与整理事件对比
                    self.cache_create_strm_file_dict[str(event["file_id"])] = [
                        event["file_name"],
                        target_dir,
                        pan_media_dir,
                    ]
                    if configer.get_config("notify"):
                        self._monitor_life_notification_queue["life"]["strm_count"] += 1
                        _schedule_notification()
                    scrape_metadata = True
                    if configer.get_config("monitor_life_scrape_metadata_enabled"):
                        if configer.get_config(
                            "monitor_life_scrape_metadata_exclude_paths"
                        ):
                            if self.pathmatchinghelper.get_scrape_metadata_exclude_path(
                                configer.get_config(
                                    "monitor_life_scrape_metadata_exclude_paths"
                                ),
                                str(new_file_path),
                            ):
                                logger.debug(
                                    f"【监控生活事件】匹配到刮削排除目录，不进行刮削: {new_file_path}"
                                )
                                scrape_metadata = False
                        if scrape_metadata:
                            media_scrape_metadata(
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

            # def __get_file_path(
            #     file_name: str, file_size: str, file_id: str, file_category: int
            # ):
            #     """
            #     通过 还原文件/文件夹 再删除 获取文件路径
            #     """
            #     for item in self._client.recyclebin_list()["data"]:
            #         if (
            #             file_category == 0
            #             and str(item["file_name"]) == file_name
            #             and str(item["type"]) == "2"
            #         ) or (
            #             file_category != 0
            #             and str(item["file_name"]) == file_name
            #             and str(item["file_size"]) == file_size
            #         ):
            #             resp = self._client.recyclebin_revert(item["id"])
            #             if resp["state"]:
            #                 time.sleep(1)
            #                 path = get_path_to_cid(self._client, cid=int(item["cid"]))
            #                 time.sleep(1)
            #                 self._client.fs_delete(file_id)
            #                 return str(Path(path) / item["file_name"])
            #             else:
            #                 return None
            #     return None

            _databasehelper = FileDbHelper()

            file_path = None
            file_category = event["file_category"]
            file_item = _databasehelper.get_by_id(int(event["file_id"]))
            if file_item:
                file_path = file_item.get("path", "")
            if not file_path:
                logger.debug(
                    f"【监控生活事件】{event['file_name']} 无法通过数据库获取路径，防止误删不处理"
                )
                return
            logger.debug(f"【监控生活事件】通过数据库获取路径：{file_path}")

            pan_file_path = file_path
            # 优先匹配待整理目录，如果删除的目录为待整理目录则不进行操作
            if configer.get_config("pan_transfer_enabled") and configer.get_config(
                "pan_transfer_paths"
            ):
                if self.pathmatchinghelper.get_run_transfer_path(
                    paths=configer.get_config("pan_transfer_paths"),
                    transfer_path=file_path,
                ):
                    logger.debug(
                        f"【监控生活事件】{file_path} 为待整理目录下的路径，不做处理"
                    )
                    return

            # 匹配是否是媒体文件夹目录
            status, target_dir, pan_media_dir = self.pathmatchinghelper.get_media_path(
                configer.get_config("monitor_life_paths"), file_path
            )
            if not status:
                return
            logger.debug("【监控生活事件】匹配到网盘文件夹路径: %s", str(pan_media_dir))

            storagechain = StorageChain()
            fileitem = storagechain.get_file_item(storage="u115", path=Path(file_path))
            if fileitem:
                logger.warn(
                    f"【监控生活事件】网盘 {file_path} 目录存在，跳过本地删除: {fileitem}"
                )
                # 这里如果路径存在则更新数据库信息
                _databasehelper.upsert_batch(
                    _databasehelper.process_fileitem(fileitem=fileitem)
                )
                return

            file_path = Path(target_dir) / Path(file_path).relative_to(pan_media_dir)
            if file_path.suffix in rmt_mediaext:
                file_target_dir = file_path.parent
                file_name = file_path.stem + ".strm"
                file_path = file_target_dir / file_name
            logger.info(
                f"【监控生活事件】删除本地{'文件夹' if file_category == 0 else '文件'}: {file_path}"
            )
            try:
                if not Path(file_path).exists():
                    logger.warn(f"【监控生活事件】本地 {file_path} 不存在，跳过删除")
                    return
                if file_category == 0:
                    shutil.rmtree(Path(file_path))
                else:
                    Path(file_path).unlink(missing_ok=True)
                    __remove_parent_dir(Path(file_path))
                _databasehelper.remove_by_path_batch(str(pan_file_path))
                logger.info(f"【监控生活事件】{file_path} 已删除")
            except Exception as e:
                logger.error(f"【监控生活事件】{file_path} 删除失败: {e}")

        def new_creata_path(event):
            """
            处理新出现的路径
            """
            # 1.获取绝对文件路径
            file_name = event["file_name"]
            dir_path = self._get_path_by_cid(int(event["parent_id"]))
            file_path = Path(dir_path) / file_name
            # 匹配逻辑 整理路径目录 > 生成STRM文件路径目录
            # 2.匹配是否为整理路径目录
            if configer.get_config("pan_transfer_enabled") and configer.get_config(
                "pan_transfer_paths"
            ):
                if self.pathmatchinghelper.get_run_transfer_path(
                    paths=configer.get_config("pan_transfer_paths"),
                    transfer_path=file_path,
                ):
                    self.media_transfer(
                        event=event,
                        file_path=Path(file_path),
                        rmt_mediaext=rmt_mediaext,
                    )
                    return
            # 3.匹配是否为生成STRM文件路径目录
            if configer.get_config("monitor_life_enabled") and configer.get_config(
                "monitor_life_paths"
            ):
                if str(event["file_id"]) in self.cache_creata_pan_transfer_list:
                    # 检查是否命中缓存
                    self.cache_creata_pan_transfer_list.remove(str(event["file_id"]))
                    if "transfer" in configer.get_config("monitor_life_event_modes"):
                        creata_strm(event=event, file_path=file_path)
                else:
                    creata_strm(event=event, file_path=file_path)

        resp = life_show(self._client)
        if not resp["state"]:
            logger.error(f"【监控生活事件】生活事件开启失败: {resp}")
            return
        logger.info("【监控生活事件】生活事件监控启动中...")
        try:
            from_time = time.time()
            from_id = 0
            while True:
                if self.monitor_stop_event.is_set():
                    logger.info("【监控生活事件】收到停止信号，退出上传事件监控")
                    break
                while True:
                    if not TransferChain().get_queue_tasks():
                        break
                    logger.debug(
                        "【监控生活事件】MoviePilot 整理运行中，等待整理完成后继续监控生活事件..."
                    )
                    time.sleep(20)
                events_batch: List = []
                first_loop: bool = True
                for event in iter_life_behavior_once(
                    self._client,
                    from_time,
                    from_id,
                    app="web",
                    cooldown=2,
                ):
                    if first_loop:
                        from_id = int(event["id"])
                        from_time = int(event["update_time"])
                        first_loop = False
                    events_batch.append(event)
                if not events_batch:
                    time.sleep(20)
                    continue
                for event in reversed(events_batch):
                    rmt_mediaext = [
                        f".{ext.strip()}"
                        for ext in configer.get_config("user_rmt_mediaext")
                        .replace("，", ",")
                        .split(",")
                    ]
                    download_mediaext = [
                        f".{ext.strip()}"
                        for ext in configer.get_config("user_download_mediaext")
                        .replace("，", ",")
                        .split(",")
                    ]
                    if (
                        int(event["type"]) != 1
                        and int(event["type"]) != 2
                        and int(event["type"]) != 5
                        and int(event["type"]) != 6
                        and int(event["type"]) != 14
                        and int(event["type"]) != 17
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
                        if str(event["file_id"]) in self.cache_delete_pan_transfer_list:
                            # 检查是否命中删除文件夹缓存，命中则无需处理
                            self.cache_delete_pan_transfer_list.remove(
                                str(event["file_id"])
                            )
                        else:
                            if (
                                configer.get_config("monitor_life_enabled")
                                and configer.get_config("monitor_life_paths")
                                and "remove"
                                in configer.get_config("monitor_life_event_modes")
                            ):
                                remove_strm(event=event)

                    if int(event["type"]) == 17:
                        # 对于创建文件夹事件直接写入数据库
                        _databasehelper = FileDbHelper()
                        file_name = event["file_name"]
                        dir_path = self._get_path_by_cid(int(event["parent_id"]))
                        file_path = Path(dir_path) / file_name
                        _databasehelper.upsert_batch(
                            _databasehelper.process_life_dir_item(
                                event=event, file_path=file_path
                            )
                        )

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
        if configer.get_config("clear_receive_path_enabled"):
            self.clear_receive_path()

        if configer.get_config("clear_recyclebin_enabled"):
            self.clear_recyclebin()

    def clear_recyclebin(self):
        """
        清空回收站
        """
        try:
            logger.info("【回收站清理】开始清理回收站")
            self._client.recyclebin_clean(password=configer.get_config("password"))
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
            if self._observer:
                for observer in self._observer:
                    try:
                        observer.stop()
                        observer.join()
                    except Exception as e:
                        print(str(e))
                logger.info("【目录上传】目录监控已关闭")
            self._observer = []
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

    @cached(cache=TTLCache(maxsize=1, ttl=60 * 60))
    def _get_user_storage_status(self) -> Dict[str, Any]:
        """
        获取115用户基本信息和空间使用情况。
        """
        if not configer.get_config("cookies"):
            return {
                "success": False,
                "error_message": "115 Cookies 未配置，无法获取信息。",
                "user_info": None,
                "storage_info": None,
            }

        try:
            if not self._client:
                try:
                    _temp_client = P115Client(configer.get_config("cookies"))
                    logger.info("【用户存储状态】P115Client 初始化成功")
                except Exception as e:
                    logger.error(f"【用户存储状态】P115Client 初始化失败: {e}")
                    return {
                        "success": False,
                        "error_message": f"115客户端初始化失败: {e}",
                        "user_info": None,
                        "storage_info": None,
                    }
            else:
                _temp_client = self._client

            # 获取用户信息
            user_info_resp = _temp_client.user_my_info()
            user_details: Dict = None
            if user_info_resp.get("state"):
                data = user_info_resp.get("data", {})
                vip_data = data.get("vip", {})
                face_data = data.get("face", {})
                user_details = {
                    "name": data.get("uname"),
                    "is_vip": vip_data.get("is_vip"),
                    "is_forever_vip": vip_data.get("is_forever"),
                    "vip_expire_date": vip_data.get("expire_str")
                    if not vip_data.get("is_forever")
                    else "永久",
                    "avatar": face_data.get("face_s"),
                }
                logger.info(
                    f"【用户存储状态】获取用户信息成功: {user_details.get('name')}"
                )
            else:
                error_msg = (
                    user_info_resp.get("message", "获取用户信息失败")
                    if user_info_resp
                    else "获取用户信息响应为空"
                )
                logger.error(f"【用户存储状态】获取用户信息失败: {error_msg}")
                return {
                    "success": False,
                    "error_message": f"获取115用户信息失败: {error_msg}",
                    "user_info": None,
                    "storage_info": None,
                }

            # 获取空间信息
            space_info_resp = _temp_client.fs_index_info(payload=0)
            storage_details = None
            if space_info_resp.get("state"):
                data = space_info_resp.get("data", {}).get("space_info", {})
                storage_details = {
                    "total": data.get("all_total", {}).get("size_format"),
                    "used": data.get("all_use", {}).get("size_format"),
                    "remaining": data.get("all_remain", {}).get("size_format"),
                }
                logger.info(
                    f"【用户存储状态】获取空间信息成功: 总-{storage_details.get('total')}"
                )
            else:
                error_msg = (
                    space_info_resp.get("error", "获取空间信息失败")
                    if space_info_resp
                    else "获取空间信息响应为空"
                )
                logger.error(f"【用户存储状态】获取空间信息失败: {error_msg}")
                return {
                    "success": False,
                    "error_message": f"获取115空间信息失败: {error_msg}",
                    "user_info": user_details,
                    "storage_info": None,
                }

            return {
                "success": True,
                "user_info": user_details,
                "storage_info": storage_details,
            }

        except Exception as e:
            logger.error(f"【用户存储状态】获取信息时发生意外错误: {e}", exc_info=True)
            error_str_lower = str(e).lower()
            if (
                isinstance(e, DataError)
                and ("errno 61" in error_str_lower or "enodata" in error_str_lower)
                and "<!doctype html>" in error_str_lower
            ):
                specific_error_message = "获取115账户信息失败：Cookie无效或已过期，请在插件配置中重新扫码登录。"
            elif (
                "cookie" in error_str_lower
                or "登录" in error_str_lower
                or "登陆" in error_str_lower
            ):
                specific_error_message = (
                    f"获取115账户信息失败：{str(e)} 请检查Cookie或重新登录。"
                )
            else:
                specific_error_message = f"处理请求时发生错误: {str(e)}"

            result_to_return = {
                "success": False,
                "error_message": specific_error_message,
                "user_info": None,
                "storage_info": None,
            }
            return result_to_return

    def _get_config_api(self) -> Dict:
        """
        获取配置
        """
        config = configer.get_all_configs()

        mediaserver_helper = MediaServerHelper()
        config["mediaservers"] = [
            {"title": config.name, "value": config.name}
            for config in mediaserver_helper.get_configs().values()
        ]
        return config

    async def _save_config_api(self, request: Request) -> Dict:
        """
        异步保存配置
        """
        try:
            data = await request.json()
            configer.update_config(data)

            # 持久化存储配置
            self.__update_config()

            # 重新初始化插件
            self.init_plugin(config=self.get_config())

            return {"code": 0, "msg": "保存成功"}
        except Exception as e:
            return {"code": 1, "msg": f"保存失败: {str(e)}"}

    def _get_status_api(self) -> Dict:
        """
        获取插件状态
        """
        return {
            "code": 0,
            "data": {
                "enabled": configer.get_config("enabled"),
                "has_client": bool(self._client),
                "running": (
                    bool(self._scheduler.get_jobs()) if self._scheduler else False
                )
                or bool(
                    self.monitor_life_thread and self.monitor_life_thread.is_alive()
                )
                or bool(self._observer),
            },
        }

    def _trigger_full_sync_api(self) -> Dict:
        """
        触发全量同步
        """
        try:
            if not configer.get_config("enabled") or not configer.get_config("cookies"):
                return {"code": 1, "msg": "插件未启用或未配置cookie"}

            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            self._scheduler.add_job(
                func=self.full_sync_strm_files,
                trigger="date",
                run_date=datetime.now(tz=pytz.timezone(settings.TZ))
                + timedelta(seconds=3),
                name="115网盘助手全量生成STRM",
            )
            if self._scheduler.get_jobs():
                self._scheduler.print_jobs()
                self._scheduler.start()

            return {"code": 0, "msg": "全量同步任务已启动"}
        except Exception as e:
            return {"code": 1, "msg": f"启动全量同步任务失败: {str(e)}"}

    def _trigger_share_sync_api(self) -> Dict:
        """
        触发分享同步
        """
        try:
            if not configer.get_config("enabled") or not configer.get_config("cookies"):
                return {"code": 1, "msg": "插件未启用或未配置cookie"}

            if not configer.get_config("user_share_link") and not (
                configer.get_config("user_share_code")
                and configer.get_config("user_receive_code")
            ):
                return {"code": 1, "msg": "未配置分享链接或分享码"}

            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            self._scheduler.add_job(
                func=self.share_strm_files,
                trigger="date",
                run_date=datetime.now(tz=pytz.timezone(settings.TZ))
                + timedelta(seconds=3),
                name="115网盘助手分享生成STRM",
            )
            if self._scheduler.get_jobs():
                self._scheduler.print_jobs()
                self._scheduler.start()

            return {"code": 0, "msg": "分享同步任务已启动"}
        except Exception as e:
            return {"code": 1, "msg": f"启动分享同步任务失败: {str(e)}"}

    @cached(cache=TTLCache(maxsize=64, ttl=2 * 60))
    def _browse_dir_api(self, request: Request) -> Dict:
        """
        浏览目录
        """
        path = Path(request.query_params.get("path", "/"))
        is_local = request.query_params.get("is_local", "false").lower() == "true"
        if is_local:
            try:
                if not path.exists():
                    return {"code": 1, "msg": f"目录不存在: {path}"}
                dirs = []
                files = []
                for item in path.iterdir():
                    if item.is_dir():
                        dirs.append(
                            {"name": item.name, "path": str(item), "is_dir": True}
                        )
                    else:
                        files.append(
                            {"name": item.name, "path": str(item), "is_dir": False}
                        )
                return {
                    "code": 0,
                    "path": path,
                    "items": sorted(dirs, key=lambda x: x["name"]),
                }
            except Exception as e:
                return {"code": 1, "msg": f"浏览本地目录失败: {str(e)}"}
        else:
            if not self._client or not configer.get_config("cookies"):
                return {"code": 1, "msg": "未配置cookie或客户端初始化失败"}

            try:
                dir_info = self._client.fs_dir_getid(str(path))
                if not dir_info:
                    return {"code": 1, "msg": f"获取目录ID失败: {path}"}
                cid = int(dir_info["id"])

                items = []
                for batch in iter_fs_files(self._client, cid):
                    for item in batch.get("data", []):
                        if "fc" in item:
                            items.append(
                                {
                                    "name": item.get("n"),
                                    "path": f"{str(path).rstrip('/')}/{item.get('n')}",
                                    "is_dir": True,
                                }
                            )
                return {
                    "code": 0,
                    "path": str(path),
                    "items": sorted(items, key=lambda x: x["name"]),
                }
            except Exception as e:
                logger.error(f"浏览网盘目录 API 原始错误: {str(e)}")
                return {"code": 1, "msg": f"浏览网盘目录失败: {str(e)}"}

    def _get_qrcode_api(
        self, request: Request = None, client_type_override: Optional[str] = None
    ) -> Dict:
        """
        获取登录二维码
        """
        try:
            final_client_type = client_type_override
            if not final_client_type:
                final_client_type = (
                    request.query_params.get("client_type", "alipaymini")
                    if request
                    else "alipaymini"
                )
            # 二维码支持的客户端类型验证
            allowed_types = [
                "web",
                "android",
                "115android",
                "ios",
                "115ios",
                "alipaymini",
                "wechatmini",
                "115ipad",
                "tv",
                "qandroid",
            ]
            if final_client_type not in allowed_types:
                final_client_type = "alipaymini"

            logger.info(f"【扫码登入】二维码API - 使用客户端类型: {final_client_type}")

            resp = requests.get(
                "https://qrcodeapi.115.com/api/1.0/web/1.0/token/", timeout=10
            )
            if not resp.ok:
                error_msg = f"获取二维码token失败: {resp.status_code} - {resp.text}"
                return {
                    "code": -1,
                    "error": error_msg,
                    "message": error_msg,
                    "success": False,
                }
            resp_info = resp.json().get("data", {})
            uid = resp_info.get("uid", "")
            _time = resp_info.get("time", "")
            sign = resp_info.get("sign", "")

            resp = requests.get(
                f"https://qrcodeapi.115.com/api/1.0/web/1.0/qrcode?uid={uid}",
                timeout=10,
            )
            if not resp.ok:
                error_msg = f"获取二维码图片失败: {resp.status_code} - {resp.text}"
                return {
                    "code": -1,
                    "error": error_msg,
                    "message": error_msg,
                    "success": False,
                }

            qrcode_base64 = base64.b64encode(resp.content).decode("utf-8")

            tips_map = {
                "alipaymini": "请使用115客户端或支付宝扫描二维码登录",
                "wechatmini": "请使用115客户端或微信扫描二维码登录",
                "android": "请使用115安卓客户端扫描登录",
                "115android": "请使用115安卓客户端扫描登录",
                "ios": "请使用115 iOS客户端扫描登录",
                "115ios": "请使用115 iOS客户端扫描登录",
                "web": "请使用115网页版扫码登录",
                "115ipad": "请使用115 PAD客户端扫描登录",
                "tv": "请使用115 TV客户端扫描登录",
                "qandroid": "请使用115 qandroid客户端扫描登录",
            }
            tips = tips_map.get(final_client_type, "请扫描二维码登录")

            return {
                "code": 0,
                "uid": uid,
                "time": _time,
                "sign": sign,
                "qrcode": f"data:image/png;base64,{qrcode_base64}",
                "tips": tips,
                "client_type": final_client_type,
                "success": True,
            }

        except Exception as e:
            logger.error(f"【扫码登入】获取二维码异常: {e}", exc_info=True)
            return {
                "code": -1,
                "error": f"获取登录二维码出错: {str(e)}",
                "message": f"获取登录二维码出错: {str(e)}",
                "success": False,
            }

    def _check_qrcode_api_internal(
        self,
        uid: str,
        _time: str,
        sign: str,
        client_type: str,
    ) -> Dict:
        """
        检查二维码状态并处理登录
        """
        try:
            if not uid:
                error_msg = "无效的二维码ID，参数uid不能为空"
                return {"code": -1, "error": error_msg, "message": error_msg}

            resp = requests.get(
                f"https://qrcodeapi.115.com/get/status/?uid={uid}&time={_time}&sign={sign}",
                timeout=10,
            )
            status_code = resp.json().get("data").get("status")
        except Exception as e:
            error_msg = f"检查二维码状态异常: {str(e)}"
            logger.error(f"【扫码登入】检查二维码状态异常: {e}", exc_info=True)
            return {"code": -1, "error": error_msg, "message": error_msg}

        status_map = {
            0: {"code": 0, "status": "waiting", "msg": "等待扫码"},
            1: {"code": 0, "status": "scanned", "msg": "已扫码，等待确认"},
            2: {"code": 0, "status": "success", "msg": "已确认，正在登录"},
            -1: {"code": -1, "error": "二维码已过期", "message": "二维码已过期"},
            -2: {"code": -1, "error": "用户取消登录", "message": "用户取消登录"},
        }

        if status_code in status_map:
            result = status_map[status_code].copy()
            if status_code == 2:
                try:
                    resp = requests.post(
                        f"https://passportapi.115.com/app/1.0/{client_type}/1.0/login/qrcode/",
                        data={"app": client_type, "account": uid},
                        timeout=10,
                    )
                    login_data = resp.json()
                except Exception as e:
                    return {
                        "code": -1,
                        "error": f"获取登录结果请求失败: {e}",
                        "message": f"获取登录结果请求失败: {e}",
                    }

                if login_data.get("state") and login_data.get("data"):
                    cookie_data = login_data.get("data", {})
                    cookie_string = ""
                    if "cookie" in cookie_data and isinstance(
                        cookie_data["cookie"], dict
                    ):
                        for name, value in cookie_data["cookie"].items():
                            if name and value:
                                cookie_string += f"{name}={value}; "
                    if cookie_string:
                        _cookies = cookie_string.strip()
                        configer.update_config({"cookies": _cookies})
                        self.__update_config()
                        try:
                            self._client = P115Client(_cookies)
                            result["cookie"] = cookie_string
                        except Exception as ce:
                            return {
                                "code": -1,
                                "error": f"Cookie获取成功，但客户端初始化失败: {str(ce)}",
                                "message": f"Cookie获取成功，但客户端初始化失败: {str(ce)}",
                            }
                    else:
                        return {
                            "code": -1,
                            "error": "登录成功但未能正确解析Cookie",
                            "message": "登录成功但未能正确解析Cookie",
                        }
                else:
                    specific_error = login_data.get(
                        "message", login_data.get("error", "未知错误")
                    )
                    return {
                        "code": -1,
                        "error": f"获取登录会话数据失败: {specific_error}",
                        "message": f"获取登录会话数据失败: {specific_error}",
                    }
            return result
        elif status_code is None:
            return {
                "code": -1,
                "error": "无法解析二维码状态",
                "message": "无法解析二维码状态",
            }
        else:
            return {
                "code": -1,
                "error": f"未知的115业务状态码: {status_code}",
                "message": f"未知的115业务状态码: {status_code}",
            }

    def _check_qrcode_api(self, request: Request) -> Dict:
        """
        检查二维码状态
        """
        uid = request.query_params.get("uid", "")
        _time = request.query_params.get("time", "")
        sign = request.query_params.get("sign", "")
        client_type = request.query_params.get("client_type", "alipaymini")
        return self._check_qrcode_api_internal(
            uid=uid, _time=_time, sign=sign, client_type=client_type
        )

    @cached(cache=TTLCache(maxsize=48, ttl=2 * 60))
    def _redirect_url(
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
                f"{api}?{urlencode(payload)}",
                headers={"Cookie": configer.get_config("cookies")},
            )
            check_response(resp)
            json = loads(cast(bytes, resp.content))
            if get_first(json, "errno", "errNo") == 20021:
                payload.pop("suffix")
                resp = requests.get(
                    f"{api}?{urlencode(payload)}",
                    headers={"Cookie": configer.get_config("cookies")},
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
                headers={"Cookie": configer.get_config("cookies")},
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
                    headers={
                        "User-Agent": user_agent,
                        "Cookie": configer.get_config("cookies"),
                    },
                )
            else:
                resp = requests.post(
                    f"http://proapi.115.com/{app or 'android'}/2.0/ufile/download",
                    data={
                        "data": encrypt(f'{{"pick_code":"{pickcode}"}}').decode("utf-8")
                    },
                    headers={
                        "User-Agent": user_agent,
                        "Cookie": configer.get_config("cookies"),
                    },
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
                    headers={"Cookie": configer.get_config("cookies")},
                )
            else:
                resp = requests.post(
                    "http://proapi.115.com/app/share/downurl",
                    data={"data": encrypt(dumps(payload)).decode("utf-8")},
                    headers={"Cookie": configer.get_config("cookies")},
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
                if configer.get_config("link_redirect_mode") == "cookie":
                    url = get_downurl(pickcode.lower(), user_agent, app=app)
                else:
                    resp_url = self.u115openhelper.get_download_url(
                        pickcode=pickcode.lower(), user_agent=user_agent
                    )
                    data: Dict = {}
                    data["file_name"] = unquote(
                        urlsplit(resp_url).path.rpartition("/")[-1]
                    )
                    url = Url.of(resp_url, data)
                logger.info(
                    f"【302跳转服务】获取 115 下载地址成功: {url} {url['file_name']}"
                )
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
