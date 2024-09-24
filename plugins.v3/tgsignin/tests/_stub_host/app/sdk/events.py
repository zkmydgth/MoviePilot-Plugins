"""宿主桩：``app.sdk.events`` 的事件与注册器。"""

from typing import Any, Callable, List, Optional


class Event:
    """事件桩：只有 ``event_data`` 与 ``event_type``。"""

    def __init__(self, event_type: Any = None, event_data: Optional[dict] = None) -> None:
        """
        构造事件。

        :param event_type: 事件类型
        :param event_data: 事件数据
        """
        self.event_type = event_type
        self.event_data = event_data or {}


class _StubEventManager:
    """事件注册器桩：把注册的函数原样返回并记录。"""

    def __init__(self) -> None:
        """初始化注册表。"""
        self.registered: List[Callable[..., Any]] = []

    def register(self, event_types: Any = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """
        装饰器桩。

        :param event_types: 订阅的事件类型
        :return Callable: 装饰器
        """
        del event_types

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            """
            记录并原样返回被装饰函数。

            :param func: 被装饰函数
            :return Callable: 原函数
            """
            self.registered.append(func)
            return func

        return decorator


eventmanager = _StubEventManager()
