# -*- coding: utf-8 -*-
"""宿主桩：app.plugins._PluginBase。

只提供插件基类的**最小可用实现**：ConfigBackup 依赖基类存放
插件数据目录、配置、以及 get_data_path()/get_config()/save_data() 等。
"""

from typing import Any, Dict, Optional


class _PluginBase:
    """
    插件基类桩。

    ``get_data_path`` 默认返回工作目录（由测试在实例上设置 ``_data_path``），
    这样 ConfigBackup 的 ``pending_restore.json`` 会落在临时目录里，不污染系统。
    """

    # 由测试注入
    _stub_data_path = None
    _stub_config: Dict[str, Any] = {}

    def __init__(self):
        pass

    # ------------------------------------------------------------------
    def get_data_path(self):
        from pathlib import Path

        if self._stub_data_path is None:
            raise RuntimeError("测试未设置 _stub_data_path")
        path = Path(self._stub_data_path)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get_config(self, key: Optional[str] = None):
        if key is None:
            return dict(self._stub_config)
        return self._stub_config.get(key)

    def save_data(self, key: str, value: Any) -> None:
        self._stub_config[key] = value

    def update_config(self, config: Dict[str, Any]) -> None:
        self._stub_config.update(config)

    def post_message(self, *args, **kwargs) -> None:
        """通知桩：记录调用，便于断言。"""
        calls = getattr(self, "_stub_messages", None)
        if calls is None:
            calls = []
            self._stub_messages = calls
        calls.append((args, kwargs))
