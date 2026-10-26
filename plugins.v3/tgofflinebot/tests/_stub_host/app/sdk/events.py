# -*- coding: utf-8 -*-
"""
``app.sdk.events`` 替身：提供事件类型与注册器。
"""

from typing import Any, Callable, Dict, List


class Event:
    """最小事件对象。"""

    def __init__(self, event_data: Any = None) -> None:
        """保存事件数据。"""
        self.event_data = event_data


class _EventManager:
    """记录注册的函数，便于断言订阅是否成立。"""

    def __init__(self) -> None:
        """初始化注册表。"""
        self.handlers: Dict[Any, List[Callable[..., Any]]] = {}

    def register(self, event_type: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """
        返回装饰器，登记函数并原样返回。

        :param event_type: 事件类型
        :return: 装饰器
        """
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            """登记并返回原函数。"""
            self.handlers.setdefault(event_type, []).append(func)
            return func

        return decorator


eventmanager = _EventManager()
