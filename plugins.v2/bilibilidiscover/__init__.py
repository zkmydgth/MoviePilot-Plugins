from typing import Any, List, Dict, Tuple

from cachetools import cached, TTLCache

from app import schemas
from app.core.config import settings
from app.core.event import eventmanager, Event
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import DiscoverSourceEventData
from app.schemas.types import ChainEventType
from app.utils.http import RequestUtils


CHANNEL_PARAMS = {
    "tv": {
        "_type": "1",
        "st": "5",
        "season_type": "5",
        "media_type": "tv",
        "name": "电视剧",
    },
    "movie": {
        "_type": "1",
        "st": "2",
        "season_type": "2",
        "media_type": "movie",
        "name": "电影",
    },
    "documentary": {
        "_type": "1",
        "st": "3",
        "season_type": "3",
        "media_type": "tv",
        "name": "纪录片",
    },
    "bangumi": {
        "_type": "1",
        "st": "1",
        "season_type": "1",
        "media_type": "tv",
        "name": "番剧",
    },
    "guo": {
        "_type": "1",
        "st": "4",
        "season_type": "4",
        "media_type": "tv",
        "name": "国产动画",
    },
    "variety": {
        "_type": "1",
        "st": "7",
        "season_type": "7",
        "media_type": "tv",
        "name": "综艺",
    },
}


class BilibiliDiscover(_PluginBase):
    # 插件名称
    plugin_name = "哔哩哔哩探索"
    # 插件描述
    plugin_desc = "让探索支持哔哩哔哩的数据浏览。"
    # 插件图标
    plugin_icon = "Bilibili_E.png"
    # 插件版本
    plugin_version = "0.0.1"
    # 插件作者
    plugin_author = "DDSRem"
    # 作者主页
    author_url = "https://blog.ddsrem.com"
    # 插件配置项ID前缀
    plugin_config_prefix = "bilibilidiscover_"
    # 加载顺序
    plugin_order = 99
    # 可使用的用户级别
    auth_level = 1

    # 私有属性
    _enabled = False

    def init_plugin(self, config: dict = None):
        if config:
            self._enabled = config.get("enabled")

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        return [
            {
                "path": "/bilibili_discover",
                "endpoint": self.bilibili_discover,
                "methods": ["GET"],
                "summary": "哔哩哔哩探索数据源",
                "description": "获取哔哩哔哩探索数据",
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
                            }
                        ],
                    }
                ],
            }
        ], {"enabled": False}

    def get_page(self) -> List[dict]:
        pass

    @cached(cache=TTLCache(maxsize=32, ttl=1800))
    def __request(
        self, mtype: str, page_num: int, page_size: int, **kwargs
    ) -> List[schemas.MediaInfo]:
        """
        请求 哔哩哔哩 API
        """
        api_url = "https://api.bilibili.com/pgc/season/index/result"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Referer": "https://www.bilibili.com",
        }
        params = {
            "type": CHANNEL_PARAMS[mtype]["_type"],
            "st": CHANNEL_PARAMS[mtype]["st"],
            "season_type": CHANNEL_PARAMS[mtype]["season_type"],
            "pn": page_num,
            "ps": page_size,
        }
        if kwargs:
            params.update(kwargs)
        res = RequestUtils(headers=headers).get_res(
            api_url,
            params=params,
        )
        if res is None:
            raise Exception("无法连接哔哩哔哩，请检查网络连接！")
        if not res.ok:
            raise Exception(f"请求哔哩哔哩 API失败：{res.text}")
        return res.json().get("data").get("list")

    def bilibili_discover(
        self,
        mtype: str = "tv",
        page: int = 1,
        count: int = 30,
    ) -> List[schemas.MediaInfo]:
        """
        获取哔哩哔哩探索数据
        """

        def __movie_to_media(movie_info: dict) -> schemas.MediaInfo:
            """
            电影数据转换为MediaInfo
            """
            logger.info(movie_info)
            return schemas.MediaInfo(
                type="电影",
                title=movie_info.get("title"),
                mediaid_prefix="bilibili",
                media_id=str(movie_info.get("media_id")),
                poster_path=movie_info.get("cover"),
            )

        def __series_to_media(series_info: dict) -> schemas.MediaInfo:
            """
            电视剧数据转换为MediaInfo
            """
            logger.info(series_info)
            return schemas.MediaInfo(
                type="电视剧",
                title=series_info.get("title"),
                mediaid_prefix="bilibili",
                media_id=str(series_info.get("media_id")),
                poster_path=series_info.get("cover"),
            )

        try:
            params = {
                "mtype": mtype,
                "page_num": page,
                "page_size": count,
            }
            result = self.__request(**params)
        except Exception as err:
            logger.error(str(err))
            return []
        if not result:
            return []
        if mtype == "movie":
            results = [__movie_to_media(movie) for movie in result]
        else:
            results = [__series_to_media(series) for series in result]
        return results

    @staticmethod
    def bilibili_filter_ui() -> List[dict]:
        """
        哔哩哔哩过滤参数UI配置
        """
        mtype_ui = [
            {
                "component": "VChip",
                "props": {"filter": True, "tile": True, "value": value},
                "text": CHANNEL_PARAMS[value]['name'],
            }
            for value in CHANNEL_PARAMS
        ]

        ui = [
            {
                "component": "div",
                "props": {"class": "flex justify-start items-center"},
                "content": [
                    {
                        "component": "div",
                        "props": {"class": "mr-5"},
                        "content": [{"component": "VLabel", "text": "类型"}],
                    },
                    {
                        "component": "VChipGroup",
                        "props": {"model": "mtype"},
                        "content": mtype_ui,
                    },
                ],
            },
        ]

        return ui

    @eventmanager.register(ChainEventType.DiscoverSource)
    def discover_source(self, event: Event):
        """
        监听识别事件，使用ChatGPT辅助识别名称
        """
        if not self._enabled:
            return
        event_data: DiscoverSourceEventData = event.event_data
        bilibili_source = schemas.DiscoverMediaSource(
            name="哔哩哔哩",
            mediaid_prefix="bilibili",
            api_path=f"plugin/BilibiliDiscover/bilibili_discover?apikey={settings.API_TOKEN}",
            filter_params={
                "mtype": "tv",
            },
            filter_ui=self.bilibili_filter_ui(),
        )
        if not event_data.extra_sources:
            event_data.extra_sources = [bilibili_source]
        else:
            event_data.extra_sources.append(bilibili_source)

    def stop_service(self):
        """
        退出插件
        """
        pass
