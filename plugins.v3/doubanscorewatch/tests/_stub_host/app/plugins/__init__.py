# -*- coding: utf-8 -*-
"""``app.plugins`` 替身：提供插件基类的数据持久化与通知录制。"""

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
        self.sent_messages: List[Dict[str, Any]] = []

    def init_plugin(self, config: Optional[dict] = None) -> None:
        """初始化插件（子类覆盖）。"""
        del config

    def get_state(self) -> bool:
        """返回启用状态（子类覆盖）。"""
        return False

    def get_api(self) -> List[Dict[str, Any]]:
        """返回 API 列表（子类覆盖）。"""
        return []

    def get_form(self):
        """返回配置表单（子类覆盖）。"""
        return [], {}

    def get_page(self):
        """返回详情页（子类覆盖）。"""
        return []

    def get_service(self):
        """返回定时服务（子类覆盖）。"""
        return []

    def stop_service(self) -> None:
        """停止服务（子类覆盖）。"""
        return None

    def get_command(self):
        """返回命令列表（子类覆盖）。"""
        return []

    def save_data(self, key: str, value: Any, plugin_id: Optional[str] = None) -> None:
        """保存插件数据。"""
        del plugin_id
        self._data[key] = value

    def get_data(self, key: Optional[str] = None, plugin_id: Optional[str] = None) -> Any:
        """读取插件数据。"""
        del plugin_id
        if key is None:
            return dict(self._data)
        return self._data.get(key)

    def del_data(self, key: str, plugin_id: Optional[str] = None) -> Any:
        """删除插件数据。"""
        del plugin_id
        return self._data.pop(key, None)

    def post_message(self, **kwargs: Any) -> None:
        """录制一条待发送通知。"""
        self.sent_messages.append(kwargs)
