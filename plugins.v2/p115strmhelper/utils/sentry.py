import functools
import inspect
import base64

import sentry_sdk
from sentry_sdk.hub import Hub
from sentry_sdk.client import Client


sentry_hub = Hub(
    Client(
        dsn=base64.b64decode(
            "aHR0cHM6Ly8zZjI4MWRkODVlNTY0NjJkOGNkNDRhZTA0ODBkY2NmN0BnbGl0Y2h0aXAuZGRzcmVtLmNvbS8x"
        ).decode("utf-8"),
    )
)


_original_capture_exception = sentry_sdk.capture_exception


def _patched_capture_exception(*args, **kwargs):
    """
    当前 Hub 是插件 sentry_hub 时才真正执行上报
    """
    if Hub.current is sentry_hub:
        _original_capture_exception(*args, **kwargs)
    else:
        pass


sentry_sdk.capture_exception = _patched_capture_exception


def capture_plugin_exceptions(func):
    """
    函数装饰器
    """
    if getattr(func, "_sentry_captured", False):
        return func

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with sentry_hub:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                sentry_hub.capture_exception(e)
                raise  # pylint: disable=W0706

    wrapper._sentry_captured = True
    return wrapper


def capture_all_class_exceptions(cls):
    """
    类装饰器
    """
    for name, attr in cls.__dict__.items():
        if name.startswith("_"):
            continue

        original_function = None
        is_static = False
        is_class = False

        if isinstance(attr, staticmethod):
            original_function = attr.__func__
            is_static = True
        elif isinstance(attr, classmethod):
            original_function = attr.__func__
            is_class = True
        elif inspect.isfunction(attr):
            original_function = attr

        if original_function:
            wrapped_function = capture_plugin_exceptions(original_function)

            if is_static:
                # 重新包装为 staticmethod
                final_method = staticmethod(wrapped_function)
            elif is_class:
                # 重新包装为 classmethod
                final_method = classmethod(wrapped_function)
            else:
                final_method = wrapped_function

            setattr(cls, name, final_method)

    return cls
