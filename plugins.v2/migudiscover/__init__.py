from typing import Any, List, Dict, Tuple, Optional

from cachetools import cached, TTLCache

from app import schemas
from app.core.config import settings
from app.core.event import eventmanager, Event
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import DiscoverSourceEventData
from app.schemas.types import ChainEventType
from app.utils.http import RequestUtils


class MiGuDiscover(_PluginBase):
    # 插件名称
    plugin_name = "咪咕视频探索"
    # 插件描述
    plugin_desc = "让探索支持咪咕视频的数据浏览。"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/DDS-Derek/MoviePilot-Plugins/main/icons/CCTV_A.png"
    # 插件版本
    plugin_version = "1.0"
    # 插件作者
    plugin_author = "DDSRem"
    # 作者主页
    author_url = "https://blog.ddsrem.com"
    # 插件配置项ID前缀
    plugin_config_prefix = "migudiscover_"
    # 加载顺序
    plugin_order = 99
    # 可使用的用户级别
    auth_level = 1

    # 私有属性
    _base_api = "https://jadeite.migu.cn/search/v3/category"
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
                "path": "/migu_discover",
                "endpoint": self.migu_discover,
                "methods": ["GET"],
                "summary": "咪咕视频探索数据源",
                "description": "获取咪咕视频探索数据",
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
        self,
        page_num: int,
        page_size: int,
        fc: Optional[str] = None,
        area: Optional[str] = None,
        sc: Optional[str] = None,
        year: Optional[str] = None,
        fl: Optional[str] = None,
    ) -> List[schemas.MediaInfo]:
        """
        请求CCTV API
        """
        api_url = self._base_api
        params = {
            "p": str(page_num),
            "n": str(page_size),
            "serviceId": "cbox",
            "sort": "desc",
        }
        if fc:
            params.update({"fc": fc})
        if area:
            params.update({"area": area})
        if sc:
            params.update({"sc": sc})
        if year:
            params.update({"year": year})
        if fl:
            params.update({"fl": fl})
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Referer": "https://app.cctv.com/",
        }
        res = RequestUtils(headers=headers).get_res(
            api_url,
            params=params,
        )
        if res is None:
            raise Exception("无法连接CCTV，请检查网络连接！")
        if not res.ok:
            raise Exception(f"请求CCTV API失败：{res.text}")
        return self._parse_response(res.json())

    def migu_discover(
        self,
        packId: str = "1002581,1003861,1003863,1003866,1002601,1004761,1004121,1004641,1005521,1005261",
        mediaType: str = None,
        mediaYear: str = None,
        mediaArea: str = None,
        rankingType: str = None,
        payType: str = None,
        page: int = 1,
        count: int = 30,
    ) -> List[schemas.MediaInfo]:
        """
        获取TheTVDB探索数据
        """

        def __movie_to_media(movie_info) -> schemas.MediaInfo:
            return schemas.MediaInfo(
                type="电影",
                title=re.sub("[《》]", "", movie_info.title),
                mediaid_prefix="cctv",
                media_id=movie_info.id,
                poster_path=movie_info.image,
            )

        def __series_to_media(series_info: dict) -> schemas.MediaInfo:
            return schemas.MediaInfo(
                type="电视剧",
                title=re.sub("[《》]", "", series_info.title),
                mediaid_prefix="cctv",
                media_id=series_info.id,
                poster_path=series_info.image,
            )

        try:
            if page * count > 50:
                req_page = 50 // count
            else:
                req_page = page
            result = self.__request(
                page_num=req_page,
                page_size=50,
                fc=fc,
                area=area,
                sc=sc,
                year=year,
                fl=fl,
            )
        except Exception as err:
            logger.error(str(err))
            return []
        if not result:
            return []
        if fc == "电影":
            results = [__movie_to_media(movie) for movie in result.data.list[:]]
        else:
            results = [__series_to_media(series) for series in result.data.list[:]]
        return results[(page - 1) * count : page * count]

    @staticmethod
    def cctv_filter_ui() -> List[dict]:
        """
        TheTVDB过滤参数UI配置
        """

        fc = ["电视剧", "电影", "动画片", "纪录片", "特别节目"]

        fc_ui = [
            {
                "component": "VChip",
                "props": {"filter": True, "tile": True, "value": value},
                "text": value,
            }
            for value in fc
        ]

        area = [
            "内地（大陆）",
            "港澳台",
            "欧美",
            "日韩",
            "其他",
            "中国大陆",
            "香港",
            "美国",
            "欧洲",
            "泰国",
        ]

        area_ui = [
            {
                "component": "VChip",
                "props": {"filter": True, "tile": True, "value": value},
                "text": value,
            }
            for value in area
        ]

        sc = [
            "谍战",
            "悬疑",
            "刑侦",
            "历史",
            "古装",
            "武侠",
            "军事",
            "战争",
            "喜剧",
            "青春",
            "言情",
            "偶像",
            "家庭",
            "年代",
            "革命",
            "农村",
            "都市",
            "其他",
        ]

        sc_ui = [
            {
                "component": "VChip",
                "props": {"filter": True, "tile": True, "value": value},
                "text": value,
            }
            for value in sc
        ]

        year = [str(i) for i in range(2025, 1996, -1)]

        year_ui = [
            {
                "component": "VChip",
                "props": {"filter": True, "tile": True, "value": value},
                "text": value,
            }
            for value in year
        ]

        fl = [chr(i) for i in range(ord("A"), ord("Z") + 1)]

        fl_ui = [
            {
                "component": "VChip",
                "props": {"filter": True, "tile": True, "value": value},
                "text": value,
            }
            for value in fl
        ]

        return [
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
                        "props": {"model": "fc"},
                        "content": fc_ui,
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
                        "content": [{"component": "VLabel", "text": "地区"}],
                    },
                    {
                        "component": "VChipGroup",
                        "props": {"model": "area"},
                        "content": area_ui,
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
                        "content": [{"component": "VLabel", "text": "类型"}],
                    },
                    {
                        "component": "VChipGroup",
                        "props": {"model": "sc"},
                        "content": sc_ui,
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
                        "content": [{"component": "VLabel", "text": "年份"}],
                    },
                    {
                        "component": "VChipGroup",
                        "props": {"model": "year"},
                        "content": year_ui,
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
                        "content": [{"component": "VLabel", "text": "字母顺序"}],
                    },
                    {
                        "component": "VChipGroup",
                        "props": {"model": "fl"},
                        "content": fl_ui,
                    },
                ],
            },
        ]

    @eventmanager.register(ChainEventType.DiscoverSource)
    def discover_source(self, event: Event):
        """
        监听识别事件，使用ChatGPT辅助识别名称
        """
        if not self._enabled:
            return
        event_data: DiscoverSourceEventData = event.event_data
        cctv_source = schemas.DiscoverMediaSource(
            name="CCTV",
            mediaid_prefix="cctv",
            api_path=f"plugin/CCTVDiscover/migu_discover?apikey={settings.API_TOKEN}",
            filter_params={
                "fc": "电视剧",
                "area": None,
                "sc": None,
                "year": None,
                "fl": None,
            },
            filter_ui=self.cctv_filter_ui(),
        )
        if not event_data.extra_sources:
            event_data.extra_sources = [cctv_source]
        else:
            event_data.extra_sources.append(cctv_source)

    def stop_service(self):
        """
        退出插件
        """
        pass
