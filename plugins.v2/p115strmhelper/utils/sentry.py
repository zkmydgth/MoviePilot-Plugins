import functools
import inspect
import base64

from sentry_sdk.hub import Hub
from sentry_sdk.client import Client


sentry_hub = Hub(
    Client(
        dsn=base64.b64decode(
            "aHR0cHM6Ly8zZjI4MWRkODVlNTY0NjJkOGNkNDRhZTA0ODBkY2NmN0BnbGl0Y2h0aXAuZGRzcmVtLmNvbS8x"
        ).decode("utf-8"),
    )
)


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
    for name, method in inspect.getmembers(cls, inspect.isfunction):
        if not name.startswith("_"):
            setattr(cls, name, capture_plugin_exceptions(method))

    return cls
