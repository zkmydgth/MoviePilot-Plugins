from collections.abc import Mapping
from typing import cast, Dict
from errno import EIO, ENOENT
from urllib.parse import parse_qsl, unquote, urlsplit, urlencode

import requests
from orjson import dumps, loads
from p115rsacipher import encrypt, decrypt

from app.log import logger

from ..core.u115_open import U115OpenHelper
from ..core.config import configer
from ..core.cache import r302cacher
from ..utils.http import check_response
from ..utils.url import Url


class Redirect:
    """
    302 跳转模块
    """

    def __init__(self):
        self.u115openhelper = U115OpenHelper()

    @staticmethod
    def get_first(m: Mapping, *keys, default=None):
        for k in keys:
            if k in m:
                return m[k]
        return default

    def share_get_id_for_name(
        self,
        share_code: str,
        receive_code: str,
        name: str,
        parent_id: int = 0,
    ) -> int:
        """
        分享通过名字获取ID
        """
        api = "http://web.api.115.com/share/search"
        payload = {
            "share_code": share_code,
            "receive_code": receive_code,
            "search_value": name,
            "cid": parent_id,
            "limit": 1,
            "type": 99,
        }
        suffix = name.rpartition(".")[-1]
        if suffix.isalnum():
            payload["suffix"] = suffix
        resp = requests.get(
            f"{api}?{urlencode(payload)}",
            headers={"Cookie": configer.get_config("cookies")},
        )
        check_response(resp)
        json = loads(cast(bytes, resp.content))
        if self.get_first(json, "errno", "errNo") == 20021:
            payload.pop("suffix")
            resp = requests.get(
                f"{api}?{urlencode(payload)}",
                headers={"Cookie": configer.get_config("cookies")},
            )
            check_response(resp)
            json = loads(cast(bytes, resp.content))
        if not json["state"] or not json["data"]["count"]:
            raise FileNotFoundError(ENOENT, json)
        info = json["data"]["list"][0]
        if info["n"] != name:
            raise FileNotFoundError(ENOENT, f"name not found: {name!r}")
        id = int(info["fid"])
        return id

    def get_receive_code(self, share_code: str) -> str:
        """
        获取接收码
        """
        resp = requests.get(
            f"http://web.api.115.com/share/shareinfo?share_code={share_code}",
            headers={"Cookie": configer.get_config("cookies")},
        )
        check_response(resp)
        json = loads(cast(bytes, resp.content))
        if not json["state"]:
            raise FileNotFoundError(ENOENT, json)
        receive_code = json["data"]["receive_code"]
        return receive_code

    def get_downurl_cookie(
        self,
        pickcode: str,
        user_agent: str = "",
    ) -> Url:
        """
        获取下载链接
        """
        if not user_agent:
            cache_ua = "NoUA"
        else:
            cache_ua = user_agent
        if r302cacher.get(pickcode, cache_ua):
            cache_url = r302cacher.get(pickcode, cache_ua)
            logger.debug(f"【302跳转服务】缓存获取 {pickcode} {cache_ua} {cache_url}")
            return Url.of(
                cache_url,
                {"file_name": unquote(urlsplit(cache_url).path.rpartition("/")[-1])},
            )
        resp = requests.post(
            "http://proapi.115.com/android/2.0/ufile/download",
            data={"data": encrypt(f'{{"pick_code":"{pickcode}"}}').decode("utf-8")},
            headers={
                "User-Agent": user_agent,
                "Cookie": configer.get_config("cookies"),
            },
        )
        check_response(resp)
        json = loads(cast(bytes, resp.content))
        if not json["state"]:
            raise OSError(EIO, json)
        data = json["data"] = loads(decrypt(json["data"]))
        data["file_name"] = unquote(urlsplit(data["url"]).path.rpartition("/")[-1])
        url = Url.of(data["url"], data)
        expires_time = (
            int(next(v for k, v in parse_qsl(urlsplit(url).query) if k == "t")) - 60 * 5
        )
        r302cacher.set(pickcode, cache_ua, str(url), expires_time)
        logger.debug(
            f"【302跳转服务】添加至缓存 {pickcode} {cache_ua} {url} {expires_time}"
        )
        return url

    def get_downurl_open(
        self,
        pickcode: str,
        user_agent: str = "",
    ) -> Url:
        """
        获取下载链接
        """
        if not user_agent:
            cache_ua = "NoUA"
        else:
            cache_ua = user_agent
        if r302cacher.get(pickcode, cache_ua):
            cache_url = r302cacher.get(pickcode, cache_ua)
            logger.debug(f"【302跳转服务】缓存获取 {pickcode} {cache_ua} {cache_url}")
            return Url.of(
                cache_url,
                {"file_name": unquote(urlsplit(cache_url).path.rpartition("/")[-1])},
            )
        resp_url = self.u115openhelper.get_download_url(
            pickcode=pickcode.lower(), user_agent=user_agent
        )
        data: Dict = {}
        data["file_name"] = unquote(urlsplit(resp_url).path.rpartition("/")[-1])
        expires_time = (
            int(next(v for k, v in parse_qsl(urlsplit(resp_url).query) if k == "t"))
            - 60 * 5
        )
        r302cacher.set(pickcode, cache_ua, resp_url, expires_time)
        logger.debug(
            f"【302跳转服务】添加至缓存 {pickcode} {cache_ua} {resp_url} {expires_time}"
        )
        return Url.of(resp_url, data)

    def get_share_downurl(
        self,
        share_code: str,
        receive_code: str,
        file_id: int,
    ) -> Url:
        """
        获取分享下载链接
        """
        if r302cacher.get(f"{share_code}{receive_code}{file_id}", "ShareUA"):
            cache_url = r302cacher.get(
                f"{share_code}{receive_code}{file_id}", "ShareUA"
            )
            logger.debug(
                f"【302跳转服务】分享缓存获取 {share_code} {receive_code} {file_id} {cache_url}"
            )
            return Url.of(
                cache_url,
                {"file_name": unquote(urlsplit(cache_url).path.rpartition("/")[-1])},
            )
        payload = {
            "share_code": share_code,
            "receive_code": receive_code,
            "file_id": file_id,
        }
        resp = requests.post(
            "http://proapi.115.com/app/share/downurl",
            data={"data": encrypt(dumps(payload)).decode("utf-8")},
            headers={"Cookie": configer.get_config("cookies")},
        )
        check_response(resp)
        json = loads(cast(bytes, resp.content))
        if not json["state"]:
            if json.get("errno") == 4100008:
                receive_code = self.get_receive_code(share_code)
                return self.get_share_downurl(share_code, receive_code, file_id)
            raise OSError(EIO, json)
        data = json["data"] = loads(decrypt(json["data"]))
        if not (data and (url_info := data["url"])):
            raise FileNotFoundError(ENOENT, json)
        data["file_id"] = data.pop("fid")
        data["file_name"] = data.pop("fn")
        data["file_size"] = int(data.pop("fs"))
        url = Url.of(url_info["url"], data)
        expires_time = (
            int(next(v for k, v in parse_qsl(urlsplit(url).query) if k == "t")) - 60 * 5
        )
        r302cacher.set(
            f"{share_code}{receive_code}{file_id}", "ShareUA", str(url), expires_time
        )
        logger.debug(
            f"【302跳转服务】分享添加至缓存 {share_code} {receive_code} {file_id} {url} {expires_time}"
        )
        return url
