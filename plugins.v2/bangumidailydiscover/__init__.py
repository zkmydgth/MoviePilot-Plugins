import dataclasses
from datetime import datetime
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


WEEKDAYS = [
    (0, "全部"),
    (1, "星期一"),
    (2, "星期二"),
    (3, "星期三"),
    (4, "星期四"),
    (5, "星期五"),
    (6, "星期六"),
    (7, "星期日"),
]

BANGUMI_API_URL = "https://api.bgm.tv/calendar"
BANGUMI_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://api.bgm.tv/",
}


@dataclasses.dataclass
class Option:
    value: int
    text: str


class BangumiDailyDiscover(_PluginBase):
    # 插件名称
    plugin_name = "Bangumi每日放送探索"
    # 插件描述
    plugin_desc = "让探索支持Bangumi每日放送的数据浏览。"
    # 插件图标
    plugin_icon = "Bangumi_A.png"
    # 插件版本
    plugin_version = "1.0.6"
    # 插件作者
    plugin_author = "DDSRem"
    # 作者主页
    author_url = "https://github.com/DDSRem"
    # 插件配置项ID前缀
    plugin_config_prefix = "bangumidailydiscover_"
    # 加载顺序
    plugin_order = 99
    # 可使用的用户级别
    auth_level = 1

    # 私有属性
    _enabled = False

    def init_plugin(self, config: dict = None):
        if config:
            self._enabled = config.get("enabled", False)

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        return [
            {
                "path": "/bangumidaily_discover",
                "endpoint": self.bangumidaily_discover,
                "methods": ["GET"],
                "summary": "Bangumi每日放送探索数据源",
                "description": "获取Bangumi每日放送探索数据",
            }
        ]

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
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

    @staticmethod
    def _fetch_raw_bangumi_data() -> List[dict] | None:
        """
        仅负责请求原始的Bangumi API数据，不加缓存
        """
        try:
            res = RequestUtils(headers=BANGUMI_HEADERS).get_res(BANGUMI_API_URL)
            if res is None:
                logger.error("无法连接Bangumi每日放送，请检查网络连接！")
                return None
            if not res.ok:
                logger.error(f"请求Bangumi每日放送 API失败：{res.text}")
                return None
            return res.json()
        except Exception as e:
            logger.error(f"请求Bangumi数据时发生异常: {str(e)}")
            return None

    @cached(region="bangumi_daily_discover", ttl=1800, skip_none=True)
    def _get_processed_bangumi_data(self) -> Dict[str, List[schemas.MediaInfo]] | None:
        """
        获取、处理并缓存数据
        """
        raw_data = self._fetch_raw_bangumi_data()
        if not raw_data:
            return None

        processed_data: Dict[str, List[schemas.MediaInfo]] = {
            str(day[0]): [] for day in WEEKDAYS
        }

        all_items = []

        for day_entry in raw_data:
            # 获取星期ID
            weekday_id_num = day_entry.get("weekday", {}).get("id")
            if weekday_id_num is None:
                continue

            weekday_id = str(weekday_id_num)

            # 将当前星期的所有番剧转换为MediaInfo对象
            converted_items = [
                self.__convert_to_media_info(item)
                for item in day_entry.get("items", [])
            ]

            # 存入对应星期的列表
            if weekday_id in processed_data:
                processed_data[weekday_id].extend(converted_items)

            # 同时也将所有番剧存入"全部"列表
            all_items.extend(converted_items)

        processed_data["0"] = all_items
        return processed_data

    def bangumidaily_discover(
        self,
        weekday: str = "0",
        page: int = 1,
        count: int = 20,
    ) -> List[schemas.MediaInfo]:
        """
        对外提供API接口
        """
        try:
            processed_data = self._get_processed_bangumi_data()
            if not processed_data:
                return []

            results = processed_data.get(str(weekday), [])

            # 执行分页
            start_idx = (page - 1) * count
            end_idx = start_idx + count
            return results[start_idx:end_idx]

        except Exception as e:
            logger.error(f"获取Bangumi每日放送数据失败: {str(e)}", exc_info=True)
            return []

    @staticmethod
    def __convert_to_media_info(series_info: dict) -> schemas.MediaInfo:
        """
        将Bangumi数据转换为MediaInfo对象
        """
        rating_info = series_info.get("rating", {})
        title = series_info.get("name_cn") or series_info.get("name", "")
        images = series_info.get("images", {}) or {}

        return schemas.MediaInfo(
            type="电视剧",
            source="bangumi",
            title=title,
            mediaid_prefix="bangumidaily",
            media_id=str(series_info.get("id", "")),
            bangumi_id=series_info.get("id", None),
            poster_path=images.get("large", ""),
            vote_average=rating_info.get("score", 0),
            first_air_date=series_info.get("air_date", ""),
        )

    @staticmethod
    def bangumidaily_filter_ui() -> List[dict]:
        """
        Bangumi每日放送过滤参数UI配置
        """
        today_weekday = datetime.today().weekday() + 1
        options = [Option(value=w[0], text=w[1]) for w in WEEKDAYS]

        def sort_key(opt: Option):
            if opt.value == 0:
                return (0, 0)
            if opt.value == today_weekday:
                return (1, 0)
            return (2, opt.value)

        sorted_options = sorted(options, key=sort_key)

        return [
            {
                "component": "div",
                "props": {"class": "flex justify-start items-center"},
                "content": [
                    {
                        "component": "div",
                        "props": {"class": "mr-5"},
                        "content": [{"component": "VLabel", "text": "星期"}],
                    },
                    {
                        "component": "VChipGroup",
                        "props": {"model": "weekday"},
                        "content": [
                            {
                                "component": "VChip",
                                "props": {
                                    "filter": True,
                                    "tile": True,
                                    "value": opt.value,
                                },
                                "text": opt.text,
                            }
                            for opt in sorted_options
                        ],
                    },
                ],
            },
        ]

    @eventmanager.register(ChainEventType.DiscoverSource)
    def discover_source(self, event: Event):
        """
        监听识别事件，添加Bangumi每日放送数据源
        """
        if not self._enabled:
            return

        event_data: DiscoverSourceEventData = event.event_data
        bangumidaily_source = schemas.DiscoverMediaSource(
            name="Bangumi每日放送",
            mediaid_prefix="bangumidaily",
            api_path=f"plugin/BangumiDailyDiscover/bangumidaily_discover?apikey={settings.API_TOKEN}",
            filter_params={"weekday": "0"},
            filter_ui=self.bangumidaily_filter_ui(),
        )

        if event_data.extra_sources is None:
            event_data.extra_sources = [bangumidaily_source]
        else:
            event_data.extra_sources.append(bangumidaily_source)

    def stop_service(self):
        """
        退出插件
        """
        pass
