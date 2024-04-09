from typing import Any, List, Dict, Tuple

from app import schemas
from app.core.config import settings
from app.core.event import eventmanager, Event
from app.core.cache import cached
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import DiscoverSourceEventData
from app.schemas.types import ChainEventType
from app.utils.http import RequestUtils

from .ui_generator import (
    guo_ui,
    tv_ui,
    movie_ui,
    bangumi_ui,
    variety_ui,
    documentary_ui,
)


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
        "name": "国创",
    },
    "variety": {
        "_type": "1",
        "st": "7",
        "season_type": "7",
        "media_type": "tv",
        "name": "综艺",
    },
}
BILIBILI_API_URL = "https://api.bilibili.com/pgc/season/index/result"
BANGUMI_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com",
}


class BilibiliDiscover(_PluginBase):
    # 插件名称
    plugin_name = "哔哩哔哩探索"
    # 插件描述
    plugin_desc = "让探索支持哔哩哔哩的数据浏览。"
    # 插件图标
    plugin_icon = "Bilibili_E.png"
    # 插件版本
    plugin_version = "1.0.5"
    # 插件作者
    plugin_author = "DDSRem"
    # 作者主页
    author_url = "https://github.com/DDSRem"
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

    @cached(region="bilibili_discover", ttl=1800, skip_none=True)
    def __request_bilibili_api(
        self, mtype: str, page_num: int, page_size: int, **kwargs
    ) -> List[Dict]:
        """
        请求 哔哩哔哩 API
        """
        params = {
            "type": CHANNEL_PARAMS[mtype]["_type"],
            "st": CHANNEL_PARAMS[mtype]["st"],
            "season_type": CHANNEL_PARAMS[mtype]["season_type"],
            "page": page_num,
            "pagesize": page_size,
        }
        if kwargs:
            params.update(kwargs)
        try:
            res = RequestUtils(headers=BANGUMI_HEADERS).get_res(
                BILIBILI_API_URL,
                params=params,
            )
            if res is None:
                raise ConnectionError("无法连接哔哩哔哩，请检查网络连接！")
            if not res.ok:
                raise ValueError(f"请求哔哩哔哩 API失败：{res.text}")
            return res.json().get("data").get("list")
        except Exception as e:
            logger.error(f"获取哔哩哔哩数据失败: {str(e)}")
            raise

    def bilibili_discover(
        self,
        mtype: str = "tv",
        release_date: str = None,
        year: str = None,
        sort: str = None,
        season_status: str = None,
        style_id: str = None,
        season_month: str = None,
        _copyright: str = None,
        is_finish: str = None,
        area: str = None,
        spoken_language_type: str = None,
        season_version: str = None,
        order: str = None,
        producer_id: str = None,
        page: int = 1,
        count: int = 20,
    ) -> List[schemas.MediaInfo]:
        """
        获取哔哩哔哩探索数据
        """

        def __movie_to_media(movie_info: dict) -> schemas.MediaInfo:
            """
            电影数据转换为MediaInfo
            """
            vote_average = None
            if movie_info.get("score"):
                vote_average = movie_info.get("score")
            return schemas.MediaInfo(
                type="电影",
                title=movie_info.get("title"),
                mediaid_prefix="bilibili",
                media_id=str(movie_info.get("media_id")),
                poster_path=movie_info.get("cover"),
                vote_average=vote_average,
            )

        def __series_to_media(series_info: dict) -> schemas.MediaInfo:
            """
            电视剧数据转换为MediaInfo
            """
            vote_average = None
            if series_info.get("score"):
                vote_average = series_info.get("score")
            return schemas.MediaInfo(
                type="电视剧",
                title=series_info.get("title"),
                mediaid_prefix="bilibili",
                media_id=str(series_info.get("media_id")),
                poster_path=series_info.get("cover"),
                vote_average=vote_average,
            )

        try:
            params = {
                "mtype": mtype,
                "page_num": page,
                "page_size": count,
            }
            if year:
                params.update({"year": year})
            if release_date:
                params.update({"release_date": release_date})
            if sort:
                params.update({"sort": sort})
            if season_status:
                params.update({"season_status": season_status})
            if style_id:
                params.update({"style_id": style_id})
            if season_month:
                params.update({"season_month": season_month})
            if _copyright:
                params.update({"copyright": _copyright})
            if is_finish:
                params.update({"is_finish": is_finish})
            if area:
                params.update({"area": area})
            if spoken_language_type:
                params.update({"spoken_language_type": spoken_language_type})
            if season_version:
                params.update({"season_version": season_version})
            if order:
                params.update({"order": order})
            if producer_id:
                params.update({"producer_id": producer_id})
            result = self.__request_bilibili_api(**params)
        except Exception as err:
            logger.error(str(err))
            return []
        if not result:
            return []
        if (
            mtype == "movie"
            or (mtype == "bangumi" and str(season_version) == "2")
            or (mtype == "documentary" and str(style_id) == "-10")
        ):
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
                "text": CHANNEL_PARAMS[value]["name"],
            }
            for value in CHANNEL_PARAMS
        ]

        year = {
            "Id": "release_date",
            "Text": "年份",
            "Options": [
                {"Value": "[2024-01-01 00:00:00,2025-01-01 00:00:00]", "Text": "2024"},
                {"Value": "[2023-01-01 00:00:00,2024-01-01 00:00:00)", "Text": "2023"},
                {"Value": "[2022-01-01 00:00:00,2023-01-01 00:00:00)", "Text": "2022"},
                {"Value": "[2021-01-01 00:00:00,2022-01-01 00:00:00)", "Text": "2021"},
                {"Value": "[2020-01-01 00:00:00,2021-01-01 00:00:00)", "Text": "2020"},
                {"Value": "[2019-01-01 00:00:00,2020-01-01 00:00:00)", "Text": "2019"},
                {"Value": "[2018-01-01 00:00:00,2019-01-01 00:00:00)", "Text": "2018"},
                {"Value": "[2017-01-01 00:00:00,2018-01-01 00:00:00)", "Text": "2017"},
                {"Value": "[2016-01-01 00:00:00,2017-01-01 00:00:00)", "Text": "2016"},
                {
                    "Value": "[2010-01-01 00:00:00,2016-01-01 00:00:00)",
                    "Text": "2015-2010",
                },
                {
                    "Value": "[2005-01-01 00:00:00,2010-01-01 00:00:00)",
                    "Text": "2009-2005",
                },
                {
                    "Value": "[2000-01-01 00:00:00,2005-01-01 00:00:00)",
                    "Text": "2004-2000",
                },
                {
                    "Value": "[1990-01-01 00:00:00,2000-01-01 00:00:00)",
                    "Text": "90年代",
                },
                {
                    "Value": "[1980-01-01 00:00:00,1990-01-01 00:00:00)",
                    "Text": "80年代",
                },
                {"Value": "[,1980-01-01 00:00:00)", "Text": "更早"},
            ],
        }
        year_ui = [
            {
                "component": "VChip",
                "props": {"filter": True, "tile": True, "value": value["Value"]},
                "text": value["Text"],
            }
            for value in year["Options"]
        ]

        year1 = {
            "Id": "year",
            "Text": "年份",
            "Options": [
                {"Value": "[2024,2025)", "Text": "2024"},
                {"Value": "[2023,2024)", "Text": "2023"},
                {"Value": "[2022,2023)", "Text": "2022"},
                {"Value": "[2021,2022)", "Text": "2021"},
                {"Value": "[2020,2021)", "Text": "2020"},
                {"Value": "[2019,2020)", "Text": "2019"},
                {"Value": "[2018,2019)", "Text": "2018"},
                {"Value": "[2017,2018)", "Text": "2017"},
                {"Value": "[2016,2017)", "Text": "2016"},
                {"Value": "[2010,2016)", "Text": "2015-2010"},
                {"Value": "[2005,2010)", "Text": "2009-2005"},
                {"Value": "[2000,2005)", "Text": "2004-2000"},
                {"Value": "[1990,2000)", "Text": "90年代"},
                {"Value": "[1980,1990)", "Text": "80年代"},
                {"Value": "[,1980)", "Text": "更早"},
            ],
        }
        year1_ui = [
            {
                "component": "VChip",
                "props": {"filter": True, "tile": True, "value": value["Value"]},
                "text": value["Text"],
            }
            for value in year1["Options"]
        ]

        season_status = {
            "Id": "season_status",
            "Text": "付费",
            "Options": [
                {"Value": "1", "Text": "免费"},
                {"Value": "4,6", "Text": "大会员"},
                {"Value": "2,6", "Text": "付费"},
            ],
        }
        season_status_ui = [
            {
                "component": "VChip",
                "props": {"filter": True, "tile": True, "value": value["Value"]},
                "text": value["Text"],
            }
            for value in season_status["Options"]
        ]
        season_status1_ui = [
            {
                "component": "VChip",
                "props": {"filter": True, "tile": True, "value": value["Value"]},
                "text": value["Text"],
            }
            for value in season_status["Options"][:2]
        ]

        sort = {
            "Id": "sort",
            "Text": "排序",
            "Options": [
                {"Value": "0", "Text": "降序"},
                {"Value": "1", "Text": "升序"},
            ],
        }
        sort_ui = [
            {
                "component": "VChip",
                "props": {"filter": True, "tile": True, "value": value["Value"]},
                "text": value["Text"],
            }
            for value in sort["Options"]
        ]

        ui = [
            {
                "component": "div",
                "props": {"class": "flex justify-start items-center"},
                "content": [
                    {
                        "component": "div",
                        "props": {"class": "mr-5"},
                        "content": [{"component": "VLabel", "text": "种类"}],
                    },
                    {
                        "component": "VChipGroup",
                        "props": {"model": "mtype"},
                        "content": mtype_ui,
                    },
                ],
            },
            {
                "component": "div",
                "props": {"class": "flex justify-start items-center"},
                "content": [
                    {
                        "component": "div",
                        "props": {"class": "mr-5"},
                        "content": [{"component": "VLabel", "text": sort["Text"]}],
                    },
                    {
                        "component": "VChipGroup",
                        "props": {"model": sort["Id"]},
                        "content": sort_ui,
                    },
                ],
            },
            {
                "component": "div",
                "props": {
                    "class": "flex justify-start items-center",
                    "show": "{{mtype == 'bangumi' || mtype == 'guo'}}",
                },
                "content": [
                    {
                        "component": "div",
                        "props": {"class": "mr-5"},
                        "content": [{"component": "VLabel", "text": year1["Text"]}],
                    },
                    {
                        "component": "VChipGroup",
                        "props": {"model": year1["Id"]},
                        "content": year1_ui,
                    },
                ],
            },
            {
                "component": "div",
                "props": {
                    "class": "flex justify-start items-center",
                    "show": "{{mtype == 'tv' || mtype == 'documentary' || mtype == 'movie'}}",
                },
                "content": [
                    {
                        "component": "div",
                        "props": {"class": "mr-5"},
                        "content": [{"component": "VLabel", "text": year["Text"]}],
                    },
                    {
                        "component": "VChipGroup",
                        "props": {"model": year["Id"]},
                        "content": year_ui,
                    },
                ],
            },
            {
                "component": "div",
                "props": {
                    "class": "flex justify-start items-center",
                    "show": "{{mtype == 'tv' || mtype == 'variety'}}",
                },
                "content": [
                    {
                        "component": "div",
                        "props": {"class": "mr-5"},
                        "content": [
                            {"component": "VLabel", "text": season_status["Text"]}
                        ],
                    },
                    {
                        "component": "VChipGroup",
                        "props": {"model": season_status["Id"]},
                        "content": season_status1_ui,
                    },
                ],
            },
            {
                "component": "div",
                "props": {
                    "class": "flex justify-start items-center",
                    "show": "{{mtype == 'guo' || mtype == 'documentary' || mtype == 'movie' || mtype == 'bangumi'}}",
                },
                "content": [
                    {
                        "component": "div",
                        "props": {"class": "mr-5"},
                        "content": [
                            {"component": "VLabel", "text": season_status["Text"]}
                        ],
                    },
                    {
                        "component": "VChipGroup",
                        "props": {"model": season_status["Id"]},
                        "content": season_status_ui,
                    },
                ],
            },
        ]
        for item in bangumi_ui():
            ui.insert(-4, item)
        for item in guo_ui():
            ui.insert(-4, item)
        for item in documentary_ui():
            ui.insert(-4, item)
        for item in movie_ui():
            ui.insert(-4, item)
        for item in tv_ui():
            ui.insert(-4, item)
        for item in variety_ui():
            ui.insert(-4, item)

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
                "release_date": None,
                "year": None,
                "sort": None,
                "season_status": None,
                "style_id": None,
                "season_month": None,
                "_copyright": None,
                "is_finish": None,
                "area": None,
                "spoken_language_type": None,
                "season_version": None,
                "order": None,
                "producer_id": None,
            },
            filter_ui=self.bilibili_filter_ui(),
            depends={
                "release_date": ["mtype"],
                "year": ["mtype"],
                "sort": ["mtype"],
                "season_status": ["mtype"],
                "style_id": ["mtype"],
                "season_month": ["mtype"],
                "_copyright": ["mtype"],
                "is_finish": ["mtype"],
                "area": ["mtype"],
                "spoken_language_type": ["mtype"],
                "season_version": ["mtype"],
                "edition": ["mtype"],
                "order": ["mtype"],
                "producer_id": ["mtype"],
            },
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
