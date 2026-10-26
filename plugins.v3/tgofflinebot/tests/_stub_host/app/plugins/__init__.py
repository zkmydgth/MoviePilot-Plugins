# -*- coding: utf-8 -*-
"""
``app.plugins`` 替身：只提供本插件用到的插件基类能力。

配置与运行数据都放在实例内部，便于断言写回内容，不触碰真实数据库。
"""

from typing import Any, Dict, Optional


class _PluginBase:
    """插件基类替身。"""

    plugin_name: str = ""
    plugin_desc: str = ""
    plugin_icon: str = ""
    plugin_version: str = ""
    plugin_author: str = ""
    plugin_config_prefix: str = ""
    plugin_order: int = 0
    auth_level: int = 0

    def __init__(self) -> None:
        """初始化配置与数据容器。"""
        self._config: Dict[str, Any] = {}
        self._data: Dict[str, Any] = {}
        self.sent_messages: list = []

    def get_config(self, key: Optional[str] = None, default: Any = None) -> Any:
        """
        读取配置。

        :param key: 为空时返回整个配置字典
        :param default: 键缺失时的默认值
        :return: 配置值
        """
        if key is None:
            return dict(self._config)
        return self._config.get(key, default)

    def update_config(self, config: Dict[str, Any], plugin_id: Optional[str] = None) -> bool:
        """
        写回配置。

        :param config: 完整配置字典
        :param plugin_id: 插件标识，替身忽略
        :return: 固定为 True
        """
        self._config = dict(config)
        return True

    def save_data(self, key: str, value: Any, plugin_id: Optional[str] = None) -> None:
        """
        保存运行数据。

        :param key: 数据键
        :param value: 数据值
        :param plugin_id: 插件标识，替身忽略
        """
        self._data[key] = value

    def get_data(self, key: Optional[str] = None, plugin_id: Optional[str] = None) -> Any:
        """
        读取运行数据。

        :param key: 数据键，为空返回全部
        :param plugin_id: 插件标识，替身忽略
        :return: 数据值
        """
        if key is None:
            return dict(self._data)
        return self._data.get(key)

    def post_message(self, **kwargs: Any) -> None:
        """
        记录通知调用，用于断言插件不发送任何 MoviePilot 通知。

        :param kwargs: 通知参数
        """
        self.sent_messages.append(kwargs)
