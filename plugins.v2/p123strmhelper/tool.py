import time
from urllib.parse import quote
from collections import deque
from datetime import datetime

from p123 import P123Client, check_response


class P123AutoClient:
    """
    123云盘客户端
    """

    def __init__(self, passport, password):
        self._client = None
        self._passport = passport
        self._password = password

    def __getattr__(self, name):
        if self._client is None:
            self._client = P123Client(self._passport, self._password)

        def wrapped(*args, **kwargs):
            attr = getattr(self._client, name)
            if not callable(attr):
                return attr
            result = attr(*args, **kwargs)
            if (
                isinstance(result, dict)
                and result.get("code") == 401
                and result.get("message") == "tokens number has exceeded the limit"
            ):
                self._client = P123Client(self._passport, self._password)
                attr = getattr(self._client, name)
                if not callable(attr):
                    return attr
                return attr(*args, **kwargs)
            return result

        return wrapped


class P123OpenAutoClient:
    """
    123云盘开发者客户端
    """

    def __init__(self, client_id, client_secret):
        self._client = None
        self._token_expiry = 0
        self._client_id = client_id
        self._client_secret = client_secret
        self._open_headers = {"Platform": "open_platform"}

    @staticmethod
    def parse_expired_at(expired_at_str):
        """
        格式化过期事件
        """
        dt = datetime.strptime(expired_at_str, "%Y-%m-%dT%H:%M:%S%z")
        return dt.timestamp()

    def refresh_token(self):
        """
        刷新 Token
        """
        payload = {
            "clientID": self._client_id,
            "clientSecret": self._client_secret,
        }
        resp = P123Client.open_access_token(payload, headers=self._open_headers)
        check_response(resp)
        token_data = resp.get("data", {})
        self._client = P123Client(token=token_data.get("accessToken", ""))
        self._token_expiry = self.parse_expired_at(token_data.get("expiredAt", ""))

    def __getattr__(self, name):
        if self._client is None:
            self.refresh_token()

        def wrapped(*args, **kwargs):
            if time.time() >= self._token_expiry:
                self.refresh_token()
            attr = getattr(self._client, name)
            if not callable(attr):
                return attr
            if "headers" in kwargs:
                kwargs["headers"].update(self._open_headers)
            else:
                kwargs["headers"] = self._open_headers
            return attr(*args, **kwargs)

        return wrapped


def iterdir(
    client: P123Client,
    parent_id: int = 0,
    interval: int | float = 0,
    only_file: bool = False,
):
    """
    遍历文件列表
    广度优先搜索
    return:
        迭代器
    Yields:
        dict: 文件或目录信息（包含路径）
    """
    queue = deque()
    queue.append((parent_id, ""))
    while queue:
        current_parent_id, current_path = queue.popleft()
        page = 1
        _next = 0
        while True:
            payload = {
                "limit": 100,
                "next": _next,
                "Page": page,
                "parentFileId": current_parent_id,
                "inDirectSpace": "false",
            }
            if interval != 0:
                time.sleep(interval)
            resp = client.fs_list(payload)
            check_response(resp)
            item_list = resp.get("data").get("InfoList")
            if not item_list:
                break
            for item in item_list:
                is_dir = bool(item["Type"])
                item_path = (
                    f"{current_path}/{item['FileName']}"
                    if current_path
                    else item["FileName"]
                )
                if is_dir:
                    queue.append((int(item["FileId"]), item_path))
                if is_dir and only_file:
                    continue
                if is_dir:
                    yield {
                        **item,
                        "relpath": item_path,
                        "is_dir": is_dir,
                        "id": int(item["FileId"]),
                        "parent_id": int(current_parent_id),
                        "name": item["FileName"],
                    }
                else:
                    yield {
                        **item,
                        "relpath": item_path,
                        "is_dir": is_dir,
                        "id": int(item["FileId"]),
                        "parent_id": int(current_parent_id),
                        "name": item["FileName"],
                        "size": int(item["Size"]),
                        "md5": item["Etag"],
                        "uri": f"123://{quote(item['FileName'])}|{int(item['Size'])}|{item['Etag']}?{item['S3KeyFlag']}",
                    }
            if resp.get("data").get("Next") == "-1":
                break
            else:
                page += 1
                _next = resp.get("data").get("Next")


def share_iterdir(
    share_key: str,
    share_pwd: str = "",
    parent_id: int = 0,
    interval: int | float = 0,
    only_file: bool = False,
):
    """
    遍历分享列表
    广度优先搜索
    return:
        迭代器
    Yields:
        dict: 文件或目录信息（包含路径）
    """
    queue = deque()
    queue.append((parent_id, ""))
    while queue:
        current_parent_id, current_path = queue.popleft()
        page = 1
        while True:
            payload = {
                "ShareKey": share_key,
                "SharePwd": share_pwd,
                "limit": 100,
                "next": 0,
                "Page": page,
                "parentFileId": current_parent_id,
            }
            if interval != 0:
                time.sleep(interval)
            resp = P123Client.share_fs_list(payload)
            check_response(resp)
            item_list = resp.get("data").get("InfoList")
            if not item_list:
                break
            for item in item_list:
                is_dir = bool(item["Type"])
                item_path = (
                    f"{current_path}/{item['FileName']}"
                    if current_path
                    else item["FileName"]
                )
                if is_dir:
                    queue.append((int(item["FileId"]), item_path))
                if is_dir and only_file:
                    continue
                if is_dir:
                    yield {
                        **item,
                        "relpath": item_path,
                        "is_dir": is_dir,
                        "id": int(item["FileId"]),
                        "parent_id": int(current_parent_id),
                        "name": item["FileName"],
                    }
                else:
                    yield {
                        **item,
                        "relpath": item_path,
                        "is_dir": is_dir,
                        "id": int(item["FileId"]),
                        "parent_id": int(current_parent_id),
                        "name": item["FileName"],
                        "size": int(item["Size"]),
                        "md5": item["Etag"],
                        "uri": f"123://{quote(item['FileName'])}|{int(item['Size'])}|{item['Etag']}?{item['S3KeyFlag']}",
                    }
            if resp.get("data").get("Next") == "-1":
                break
            else:
                page += 1
