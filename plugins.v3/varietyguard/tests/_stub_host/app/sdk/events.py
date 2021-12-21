# -*- coding: utf-8 -*-
"""``app.sdk.events`` 替身：事件对象与事件管理器。"""

from typing import Any, Callable, Dict, List, Optional


class Event:
    """事件对象替身。"""

    def __init__(self, etype: Any = None, data: Optional[Dict[str, Any]] = None) -> None:
        self.event_type = etype
        self.event_data = data or {}


class EventManager:
    """事件管理器替身：``register`` 原样返回被装饰函数并记录注册行为。"""

    def __init__(self) -> None:
        self.registered: List[Dict[str, Any]] = []

    def register(self, etype: Any = None, **kwargs: Any) -> Callable[..., Any]:
        """注册事件处理器（用作装饰器）。"""
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.registered.append({"event": etype, "func": func})
            return func

        return decorator


eventmanager = EventManager()
