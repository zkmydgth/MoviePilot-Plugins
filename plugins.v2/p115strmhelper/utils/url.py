__all__ = ["Url", "UrlUtils"]


from typing import Any, Dict, Self
from urllib.parse import parse_qs, quote, urlencode, urlparse, urlunparse


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


class UrlUtils:
    """
    URL 解析与编码工具
    """

    @staticmethod
    def encode_url_fully(url: str) -> str:
        """
        对标准 URL 的路径、query、fragment 做百分号编码

        :param url: 完整 URL
        :return: 编码后的 URL；解析或编码失败时返回原字符串
        """
        try:
            parsed_url = urlparse(url)
            encoded_path = quote(parsed_url.path, safe="/")
            query_dict = parse_qs(parsed_url.query, keep_blank_values=True)
            encoded_query = urlencode(query_dict, doseq=True)
            encoded_fragment = quote(parsed_url.fragment)
            encoded_url_parts = (
                parsed_url.scheme,
                parsed_url.netloc,
                encoded_path,
                parsed_url.params,
                encoded_query,
                encoded_fragment,
            )
            return urlunparse(encoded_url_parts)
        except Exception:
            return url

    @staticmethod
    def parse_query_params(url: str) -> Dict[str, str]:
        """
        从 URL 中解析查询字符串里的全部参数（主要返回值即该 dict）

        :param url: 完整 URL、仅含 ?key=value 的片段、或无 scheme 的 path?query
        :return: 参数名到单个字符串值；同一键出现多次时取首次出现的值；无 query 时为空 dict
        """
        raw = (url or "").strip()
        parsed = urlparse(raw)
        query = parsed.query
        if not query and "?" in raw:
            query = raw.split("?", 1)[1].split("#", 1)[0]
        qs = parse_qs(query)
        return {k: vals[0] for k, vals in qs.items()}
