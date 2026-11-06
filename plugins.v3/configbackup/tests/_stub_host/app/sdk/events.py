"""``app.sdk.events`` 包替身：事件总线与事件对象。"""

from typing import Any, Callable


class Event:
    """事件对象替身。"""

    def __init__(self, event_type: Any = None, event_data: Any = None):
        self.event_type = event_type
        self.event_data = event_data


class _EventManager:
    """事件管理器替身：注册即返回原函数，测试可直接调用被测方法。"""

    def register(self, event_type: Any = None, **kwargs: Any) -> Callable[..., Any]:
        """返回不做任何登记、也不包装函数的装饰器。"""

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            return func

        return decorator

    def send_event(self, *args: Any, **kwargs: Any) -> None:
        """替身不发事件。"""
        return None


eventmanager = _EventManager()
