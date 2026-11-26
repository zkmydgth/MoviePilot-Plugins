# -*- coding: utf-8 -*-
"""``app.plugins`` 桩：只提供插件基类的数据持久化与通知录制。"""

from typing import Any, Dict, List, Optional


class _PluginBase:
    """插件基类替身。"""

    plugin_name = ""
    plugin_desc = ""
    plugin_icon = ""
    plugin_version = ""
    plugin_author = ""
    plugin_config_prefix = ""
    plugin_order = 0
    auth_level = 0

    def __init__(self) -> None:
        """初始化数据容器与通知录制列表。"""
        self._data: Dict[str, Any] = {}
        self.messages: List[Dict[str, Any]] = []
        self.saved_config: Dict[str, Any] = {}

    def init_plugin(self, config: Optional[dict] = None) -> None:
        """初始化插件（由被测类覆盖）。"""
        del config

    def get_state(self) -> bool:
        """返回启用状态（由被测类覆盖）。"""
        return False

    def get_command(self) -> List[Dict[str, Any]]:
        """返回命令列表（由被测类覆盖）。"""
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        """返回 API 列表（由被测类覆盖）。"""
        return []

    def get_form(self):
        """返回配置表单（由被测类覆盖）。"""
        return [], {}

    def get_page(self):
        """返回详情页（由被测类覆盖）。"""
        return []

    def get_service(self) -> List[Dict[str, Any]]:
        """返回定时服务（由被测类覆盖）。"""
        return []

    def stop_service(self) -> None:
        """停止服务（由被测类覆盖）。"""
        return None

    def save_data(self, key: str, value: Any, plugin_id: Optional[str] = None) -> None:
        """保存插件数据。"""
        del plugin_id
        self._data[key] = value

    def get_data(self, key: Optional[str] = None, plugin_id: Optional[str] = None) -> Any:
        """读取插件数据。"""
        del plugin_id
        return self._data.get(key)

    def update_config(self, config: Dict[str, Any], plugin_id: Optional[str] = None) -> bool:
        """记录配置更新。"""
        del plugin_id
        self.saved_config = dict(config)
        return True

    def post_message(self, **kwargs: Any) -> None:
        """录制待发送通知。"""
        self.messages.append(kwargs)
