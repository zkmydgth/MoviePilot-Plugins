import time
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

        for attempt in range(self.max_retries):
            try:
                response = self.session.request(
                    method, "https://115server.ddsrem.com" + path, **kwargs
                )

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
