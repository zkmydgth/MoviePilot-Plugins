# -*- coding: utf-8 -*-
"""
``app.core.event`` 替身：事件对象与事件管理器。

``eventmanager.register`` 作为装饰器使用，替身只需原样返回被装饰函数，
同时记录注册情况供测试断言。
"""

from typing import Any, Callable, Dict, List, Optional


class Event:
    """事件对象。"""

    def __init__(self, etype: Any = None, data: Optional[Dict[str, Any]] = None) -> None:
        self.event_type = etype
        self.event_data = data or {}

    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        return f"Event(type={self.event_type!r}, data={self.event_data!r})"


class EventManager:
    """事件管理器替身。"""

    def __init__(self) -> None:
        #: {(事件类型, 处理器) 用于断言注册行为}
        self.registered: List[Dict[str, Any]] = []
        #: 事件类型 -> 处理器列表，供 emit 使用
        self._handlers: Dict[Any, List[Callable[..., Any]]] = {}

    def register(self, etype: Any = None, **kwargs: Any) -> Callable[..., Any]:
        """
        注册事件处理器（用作装饰器）。

        :param etype: 事件类型
        :return: 装饰器函数
        """
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.registered.append({"event": etype, "func": func})
            self._handlers.setdefault(etype, []).append(func)
            return func
        return decorator

    def emit(self, etype: Any, data: Optional[Dict[str, Any]] = None) -> List[Any]:
        """
        触发事件，同步调用全部处理器。

        :param etype: 事件类型
        :param data: 事件数据
        :return: 各处理器返回值列表
        """
        results = []
        for func in self._handlers.get(etype, []):
            results.append(func(Event(etype, data)))
        return results

    def clear(self) -> None:
        """清空注册记录（测试隔离用）。"""
        self.registered.clear()
        self._handlers.clear()


eventmanager = EventManager()

__all__ = ["Event", "EventManager", "eventmanager"]
