import time
from typing import Any, Self, Optional, Dict

import requests

from ..core.config import configer


class Url(str):
    def __new__(cls, val: Any = "", /, *args, **kwds):
        return super().__new__(cls, val)

    def __init__(self, val: Any = "", /, *args, **kwds):
        self.__dict__.update(*args, **kwds)

    def __getattr__(self, attr: str, /):
        try:
            return self.__dict__[attr]
        except KeyError as e:
            raise AttributeError(attr) from e

    def __getitem__(self, key, /):
        try:
            if isinstance(key, str):
                return self.__dict__[key]
        except KeyError:
            return super().__getitem__(key)  # type: ignore

    def __repr__(self, /) -> str:
        cls = type(self)
        if (module := cls.__module__) == "__main__":
            name = cls.__qualname__
        else:
            name = f"{module}.{cls.__qualname__}"
        return f"{name}({super().__repr__()}, {self.__dict__!r})"

    @classmethod
    def of(cls, val: Any = "", /, ns: None | dict = None) -> Self:
        self = cls.__new__(cls, val)
        if ns is not None:
            self.__dict__ = ns
        return self

    def get(self, key, /, default=None):
        return self.__dict__.get(key, default)

    def items(self, /):
        return self.__dict__.items()

    def keys(self, /):
        return self.__dict__.keys()

    def values(self, /):
        return self.__dict__.values()


class SecureRequest:
    """
    安全请求
    """

    def __init__(self, max_retries: int = 3, backoff_factor: float = 0.5):
        """
        初始化安全请求类

        :param max_retries: 最大重试次数
        :param backoff_factor: 重试间隔时间因子
        """
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.session = requests.Session()

        self.session.headers.update(
            {
                "User-Agent": configer.get_user_agent(),
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
            }
        )

    def make_request(
        self,
        url: str,
        method: str = "POST",
        headers: Optional[Dict[str, str]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        timeout: float = 10.0,
    ) -> Optional[requests.Response]:
        """
        执行安全请求

        :param url: 请求URL
        :param method: HTTP方法 (GET, POST等)
        :param headers: 请求头
        :param json_data: JSON请求体
        :param timeout: 超时时间(秒)
        :return: 响应对象或None
        """
        final_headers = {
            "accept": "application/json",
            "Content-Type": "application/json",
        }
        if headers:
            final_headers.update(headers)

        kwargs = {"headers": final_headers, "timeout": timeout}
        if json_data and method.upper() in ["POST", "PUT", "PATCH"]:
            kwargs["json"] = json_data

        last_exception = None

        for attempt in range(self.max_retries):
            try:
                response = self.session.request(method, url, **kwargs)

                if response.status_code >= 400:
                    raise requests.exceptions.HTTPError(
                        f"HTTP error occurred: {response.status_code} - {response.reason}"
                    )

                return response

            except requests.exceptions.RequestException as e:
                last_exception = e
                if attempt < self.max_retries - 1:
                    sleep_time = self.backoff_factor * (2**attempt)
                    time.sleep(sleep_time)

        if last_exception:
            raise last_exception
        return None
