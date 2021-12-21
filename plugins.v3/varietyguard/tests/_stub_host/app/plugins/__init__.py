# -*- coding: utf-8 -*-
"""``app.plugins`` 替身：插件基类。"""

from typing import Any, Dict, List, Optional


class _PluginBase:
    """插件基类替身：提供配置读写、数据持久化与通知录制。"""

    plugin_name: str = ""
    plugin_desc: str = ""
    plugin_icon: str = ""
    plugin_version: str = ""
    plugin_author: str = ""
    plugin_config_prefix: str = ""
    plugin_order: int = 0
    auth_level: int = 0

    def __init__(self) -> None:
        self._config: Dict[str, Any] = {}
        #: 插件数据（key -> value）
        self._data: Dict[str, Any] = {}
        #: 已发送通知，供测试断言
        self.sent_messages: List[Dict[str, Any]] = []

    def init_plugin(self, config: Optional[dict] = None) -> None:
        """初始化插件（子类覆盖）。"""
        if config:
            self._config.update(config)

    def get_state(self) -> bool:
        """返回启用状态（子类覆盖）。"""
        return False

    def get_command(self) -> List[Dict[str, Any]]:
        """返回命令列表（子类覆盖）。"""
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        """返回 API 列表（子类覆盖）。"""
        return []

    def get_form(self):
        """返回配置表单（子类覆盖）。"""
        return [], {}

    def get_page(self):
        """返回详情页（子类覆盖）。"""
        return

    def get_service(self):
        """返回后台服务（子类覆盖）。"""
        return

    def stop_service(self) -> None:
        """停止插件（子类覆盖）。"""
        return

    def update_config(self, config: Dict[str, Any], plugin_id: Optional[str] = None) -> bool:
        """保存配置。"""
        self._config.update(config or {})
        return True

    def get_config(self, plugin_id: Optional[str] = None) -> Any:
        """读取配置。"""
        return dict(self._config)

    def save_data(self, key: str, value: Any, plugin_id: Optional[str] = None) -> None:
        """保存插件数据。"""
        self._data[key] = value

    def get_data(self, key: Optional[str] = None, plugin_id: Optional[str] = None) -> Any:
        """读取插件数据。"""
        if key is None:
            return dict(self._data)
        return self._data.get(key)

    def del_data(self, key: str, plugin_id: Optional[str] = None) -> Any:
        """删除插件数据。"""
        return self._data.pop(key, None)

    def post_message(self, mtype: Any = None, title: Optional[str] = None,
                     text: Optional[str] = None, **kwargs: Any) -> None:
        """记录一条通知（不真正发送）。"""
        self.sent_messages.append({"mtype": mtype, "title": title, "text": text})
