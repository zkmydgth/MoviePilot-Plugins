import time
from datetime import datetime

from p123client import P123Client, check_response


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
