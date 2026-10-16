import requests
from requests.exceptions import HTTPError


def check_response(
    resp: requests.Response,
) -> requests.Response:
    """
    检查 HTTP 响应，如果状态码 ≥ 400 则抛出 HTTPError
    """
    if resp.status_code >= 400:
        raise HTTPError(
            f"HTTP Error {resp.status_code}: {resp.text}",
            response=resp,
        )
    return resp
