# -*- coding: utf-8 -*-
"""
``app.plugins`` 替身：插件基类。

真实 ``_PluginBase`` 提供插件元数据、配置读写、消息通知与数据持久化。
替身提供等价面，配置存于实例内部，支持 ``get_config`` / ``update_config``
往返，从而可验证配置迁移逻辑。
"""

from typing import Any, Dict, List, Optional


class _PluginBase:
    """插件基类替身。"""

    # 子类覆盖的元数据
    plugin_name: str = ""
    plugin_desc: str = ""
    plugin_icon: str = ""
    plugin_version: str = ""
    plugin_author: str = ""
    author_url: str = ""
    plugin_config_prefix: str = ""
    plugin_order: int = 0
    auth_level: int = 0

    def __init__(self) -> None:
        self._config: Dict[str, Any] = {}
        #: 记录发出的通知，供测试断言
        self.sent_messages: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def init_plugin(self, config: Optional[dict] = None) -> None:
        """初始化插件（子类覆盖，此处仅兜底）。"""
        if config:
            self._config.update(config)

    def get_state(self) -> bool:
        """返回插件启用状态。"""
        return bool(self._config.get("enabled"))

    def stop_service(self) -> None:
        """停止插件服务。"""

    # ------------------------------------------------------------------
    # 配置
    # ------------------------------------------------------------------
    def get_config(self, key: Optional[str] = None, default: Any = None) -> Any:
        """
        读取配置。

        :param key: 为空返回整个配置 dict（与真实实现一致）
        :param default: key 缺失时的默认值
        :return: 配置值
        """
        if key is None:
            return dict(self._config)
        return self._config.get(key, default)

    def update_config(self, config: dict) -> None:
        """更新配置（整 dict 覆盖，与真实实现一致）。"""
        self._config = dict(config or {})

    # ------------------------------------------------------------------
    # 消息通知
    # ------------------------------------------------------------------
    def post_message(self, mtype: Any = None, title: str = "", text: str = "",
                     **kwargs: Any) -> None:
        """发送消息（替身仅记录，不实际发送）。"""
        self.sent_messages.append(
            {"type": mtype, "title": title, "text": text, "extra": kwargs}
        )

    # ------------------------------------------------------------------
    # 定时服务
    # ------------------------------------------------------------------
    def get_service(self) -> List[Dict[str, Any]]:
        """返回插件的定时服务列表。"""
        return []

    # ------------------------------------------------------------------
    # 数据持久化
    # ------------------------------------------------------------------
    def save_data(self, key: str, value: Any) -> None:
        """保存插件数据。"""
        self._config.setdefault("__data__", {})[key] = value

    def get_data(self, key: str) -> Any:
        """读取插件数据。"""
        return (self._config.get("__data__") or {}).get(key)

    def del_data(self, key: str) -> None:
        """删除插件数据。"""
        (self._config.get("__data__") or {}).pop(key, None)


PluginBase = _PluginBase

__all__ = ["_PluginBase", "PluginBase"]
