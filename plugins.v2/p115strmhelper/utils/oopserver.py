import time
import base64
from typing import Any, Optional, Dict

import requests

from ..core.config import configer


class OOPServerRequest:
    """
    数据增强服务请求
    """

    def __init__(self, max_retries: int = 3, backoff_factor: float = 0.5):
        """
        初始化请求类

        :param max_retries: 最大重试次数
        :param backoff_factor: 重试间隔时间因子
        """
        self.encrypted_base_url = "DkIVPxRiQUVHYXI+Cjo4EQFXVykmHT8cCzQJGw=="
        self.encryption_key = (
            "f6aOgXnjvPGMoHNtsy3MUoZq%WfvBFspc3QXOwxy4bhJST@*Hno6r^Qe5JusRbpC"
        )
        self.base_url = self._decrypt_string(
            self.encrypted_base_url, self.encryption_key
        )

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

    @staticmethod
    def _decrypt_string(encoded_string: str, key: str) -> str:
        """
        对Base64编码的加密字符串进行解密
        """
        try:
            encrypted_bytes = base64.b64decode(encoded_string.encode("utf-8"))
            encrypted_text = encrypted_bytes.decode("utf-8")

            decrypted_chars = []
            for i, char in enumerate(encrypted_text):
                key_char = key[i % len(key)]
                decrypted_char = ord(char) ^ ord(key_char)
                decrypted_chars.append(chr(decrypted_char))

            return "".join(decrypted_chars)
        except (ValueError, TypeError) as e:
            raise RuntimeError(f"解密服务器地址失败: {e}") from e

    def make_request(
        self,
        path: str,
        method: str = "POST",
        headers: Optional[Dict[str, str]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        timeout: float = 10.0,
    ) -> Optional[requests.Response]:
        """
        执行安全请求

        :param path: 请求Path
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

        full_url = self.base_url + path

        for attempt in range(self.max_retries):
            try:
                response = self.session.request(method, full_url, **kwargs)

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


class OOPServerHelper:
    """
    数据增强服务集成模块
    """

    @staticmethod
    def check_feature(name: str = "") -> Dict:
        """
        判断是否有权限使用此增强功能
        """
        if not name:
            return {
                "machine_id": "",
                "feature_name": "",
                "enabled": False,
            }

        try:
            oopserver = OOPServerRequest()
            machine_id = configer.get_config("MACHINE_ID")
            resp = oopserver.make_request(
                path=f"/machine/feature/{name}",
                method="GET",
                headers={"x-machine-id": machine_id},
                timeout=10.0,
            )
            if resp is not None and resp.status_code == 200:
                return resp.json()
            return {
                "machine_id": machine_id,
                "feature_name": name,
                "enabled": False,
            }
        except Exception:
            return {
                "machine_id": "",
                "feature_name": name,
                "enabled": False,
            }
