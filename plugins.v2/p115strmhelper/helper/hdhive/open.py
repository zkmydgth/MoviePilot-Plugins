__all__ = [
    "HDHiveAPIError",
    "HDHiveAuthError",
    "HDHiveForbiddenError",
    "HDHiveNotFoundError",
    "HDHiveRateLimitError",
    "HDHiveInsufficientPointsError",
    "MediaType",
    "VideoResolution",
    "Source",
    "SubtitleLanguage",
    "SubtitleType",
    "HDHiveOpenClient",
]

from typing import Any, Literal

from httpx import Client, Response


class HDHiveAPIError(Exception):
    """
    HDHive API 错误的基类
    """

    def __init__(
        self,
        code: str,
        message: str,
        description: str | None = None,
        http_status: int | None = None,
    ) -> None:
        """
        :param code: 服务端业务错误码或回退为 HTTP 状态码字符串
        :param message: 错误摘要
        :param description: 可选详细说明
        :param http_status: 响应 HTTP 状态码
        """
        self.code = code
        self.message = message
        self.description = description
        self.http_status = http_status
        super().__init__(
            f"[{code}] {message}" + (f" — {description}" if description else "")
        )


class HDHiveAuthError(HDHiveAPIError):
    """
    401 或鉴权相关错误

    例如 MISSING_API_KEY、INVALID_API_KEY、DISABLED_API_KEY、EXPIRED_API_KEY
    """


class HDHiveForbiddenError(HDHiveAPIError):
    """
    403 禁止访问

    例如 VIP_REQUIRED、ENDPOINT_DISABLED
    """


class HDHiveNotFoundError(HDHiveAPIError):
    """
    404 资源不存在
    """


class HDHiveRateLimitError(HDHiveAPIError):
    """
    429 限流或配额

    例如 RATE_LIMIT_EXCEEDED、ENDPOINT_QUOTA_EXCEEDED
    """


class HDHiveInsufficientPointsError(HDHiveAPIError):
    """
    402 积分不足（INSUFFICIENT_POINTS）
    """


_ERROR_MAP: dict[int, type[HDHiveAPIError]] = {
    401: HDHiveAuthError,
    403: HDHiveForbiddenError,
    404: HDHiveNotFoundError,
    402: HDHiveInsufficientPointsError,
    429: HDHiveRateLimitError,
}

_CODE_MAP: dict[str, type[HDHiveAPIError]] = {
    "MISSING_API_KEY": HDHiveAuthError,
    "INVALID_API_KEY": HDHiveAuthError,
    "DISABLED_API_KEY": HDHiveAuthError,
    "EXPIRED_API_KEY": HDHiveAuthError,
    "VIP_REQUIRED": HDHiveForbiddenError,
    "ENDPOINT_DISABLED": HDHiveForbiddenError,
    "ENDPOINT_QUOTA_EXCEEDED": HDHiveRateLimitError,
    "RATE_LIMIT_EXCEEDED": HDHiveRateLimitError,
    "INSUFFICIENT_POINTS": HDHiveInsufficientPointsError,
}


def _raise_for_response(resp: Response) -> None:
    """
    解析统一 JSON 错误体并抛出对应异常；成功响应则直接返回

    :param resp: httpx 响应对象
    :raises HDHiveAPIError: 业务失败或 HTTP 错误时
    """
    try:
        body: dict[str, Any] = resp.json()
    except Exception:
        resp.raise_for_status()
        return

    if body.get("success"):
        return

    code: str = str(body.get("code", resp.status_code))
    message: str = body.get("message", "Unknown error")
    description: str | None = body.get("description")
    http_status: int = resp.status_code

    exc_cls = _CODE_MAP.get(code) or _ERROR_MAP.get(http_status, HDHiveAPIError)
    raise exc_cls(
        code=code, message=message, description=description, http_status=http_status
    )


MediaType = Literal["movie", "tv"]

VideoResolution = Literal["480P", "720P", "1080P", "2K", "4K", "8K"]
Source = Literal[
    "蓝光原盘/ISO",
    "蓝光原盘/REMUX",
    "BDRip/BluRayEncode",
    "WEB-DL/WEBRip",
    "HDTV/HDTVRip",
]
SubtitleLanguage = Literal[
    "生肉",
    "简中",
    "繁中",
    "简日",
    "繁日",
    "简英",
    "繁英",
    "简韩",
    "繁韩",
    "简日双语",
    "繁日双语",
    "简英双语",
]
SubtitleType = Literal["外挂", "内封", "内嵌"]


class HDHiveOpenClient:
    """
    HDHive Open API 同步客户端

    基址为 ``https://hdhive.com/api/open``，请求头携带 ``X-API-Key``
    """

    BASE_URL = "https://hdhive.com/api/open"

    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = 30.0,
        client: Client | None = None,
    ) -> None:
        """
        初始化客户端

        :param api_key: HDHive API 密钥
        :param timeout: 单次请求超时秒数
        :param client: 可选外部 ``Client``；传入时会合并 ``X-API-Key`` 头
        """
        self._api_key = api_key
        self._owns_client = client is None
        self._client = client or Client(
            base_url=self.BASE_URL,
            headers={"X-API-Key": api_key},
            timeout=timeout,
        )
        if not self._owns_client:
            self._client.headers.update({"X-API-Key": api_key})

    def __enter__(self) -> "HDHiveOpenClient":
        """
        进入上下文，返回自身

        :return: 当前客户端实例
        """
        return self

    def __exit__(self, *_: Any) -> None:
        """
        退出上下文时关闭自有的底层 Client
        """
        self.close()

    def close(self) -> None:
        """
        若构造时未传入外部 Client，则关闭内部持有的 httpx Client
        """
        if self._owns_client:
            self._client.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        """
        发起请求并解析 JSON，失败时抛出 ``HDHiveAPIError`` 子类

        :param method: HTTP 方法
        :param path: 相对 ``BASE_URL`` 的路径
        :param params: 查询参数
        :param json: JSON 请求体
        :return: ``(data, meta)`` 元组，与 Open API 响应结构一致
        """
        resp = self._client.request(method, path, params=params, json=json)
        _raise_for_response(resp)
        body: dict[str, Any] = resp.json()
        return body.get("data"), body.get("meta")

    # ------------------------------------------------------------------
    # 通用接口
    # ------------------------------------------------------------------

    def ping(self) -> dict[str, Any]:
        """
        ``GET /ping``：健康检查并校验 API Key

        :return: 响应 ``data`` 字段
        """
        data, _ = self._request("GET", "/ping")
        return data

    def get_quota(self) -> dict[str, Any]:
        """
        ``GET /quota``：查询 API Key 配额信息

        :return: 响应 ``data`` 字段
        """
        data, _ = self._request("GET", "/quota")
        return data

    def get_usage(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        """
        ``GET /usage``：按日期区间查询用量（``YYYY-MM-DD``）

        :param start_date: 起始日期，可选
        :param end_date: 结束日期，可选
        :return: 响应 ``data`` 字段
        """
        params: dict[str, Any] = {}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        data, _ = self._request("GET", "/usage", params=params or None)
        return data

    def get_usage_today(self) -> dict[str, Any]:
        """
        ``GET /usage/today``：当日用量统计

        :return: 响应 ``data`` 字段
        """
        data, _ = self._request("GET", "/usage/today")
        return data

    # ------------------------------------------------------------------
    # 资源相关
    # ------------------------------------------------------------------

    def get_resources(
        self,
        media_type: MediaType,
        tmdb_id: str | int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """
        ``GET /resources/:type/:tmdb_id``：按 TMDB ID 列出资源

        :param media_type: ``movie`` 或 ``tv``
        :param tmdb_id: TMDB 作品 ID
        :return: ``(data 列表, meta)``，``meta`` 中含 ``total`` 等分页信息
        """
        data, meta = self._request("GET", f"/resources/{media_type}/{tmdb_id}")
        return data or [], meta or {}

    def unlock_resource(self, slug: str) -> dict[str, Any]:
        """
        ``POST /resources/unlock``：消耗积分解锁资源

        :param slug: 资源 slug
        :return: 含 ``url``、``access_code``、``full_url``、``already_owned`` 等字段
        """
        data, _ = self._request("POST", "/resources/unlock", json={"slug": slug})
        return data

    def check_resource(self, url: str) -> dict[str, Any]:
        """
        ``POST /check/resource``：识别网盘类型并从链接解析提取码等

        :param url: 资源链接
        :return: 含 ``website``、``url``、``base_link``、``access_code``、
            ``default_unlock_points`` 等字段
        """
        data, _ = self._request("POST", "/check/resource", json={"url": url})
        return data

    # ------------------------------------------------------------------
    # 仅 Premium
    # ------------------------------------------------------------------

    def get_me(self) -> dict[str, Any]:
        """
        ``GET /me``：当前用户信息（需 Premium）

        :return: 响应 ``data`` 字段
        """
        data, _ = self._request("GET", "/me")
        return data

    def checkin(self, is_gambler: bool = False) -> dict[str, Any]:
        """
        ``POST /checkin``：每日签到（需 Premium）

        :param is_gambler: 是否开启赌徒模式（高风险高回报）
        :return: 含 ``checked_in``（bool）、``message``（str）等字段
        """
        body: dict[str, Any] = {}
        if is_gambler:
            body["is_gambler"] = True
        data, _ = self._request("POST", "/checkin", json=body or None)
        return data

    def get_vip_weekly_free_quota(self) -> dict[str, Any]:
        """
        ``GET /vip/weekly-free-quota``：永久 VIP 每周免费解锁配额（需 Premium）

        :return: 响应 ``data`` 字段
        """
        data, _ = self._request("GET", "/vip/weekly-free-quota")
        return data

    # ------------------------------------------------------------------
    # 分享管理
    # ------------------------------------------------------------------

    def get_shares(
        self,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """
        ``GET /shares``：分页列出当前用户的分享

        :param page: 页码
        :param page_size: 每页条数
        :return: ``(data 列表, meta)``，``meta`` 含 ``total``、``page``、``page_size``
        """
        params = {"page": page, "page_size": page_size}
        data, meta = self._request("GET", "/shares", params=params)
        return data or [], meta or {}

    def get_share(self, slug: str) -> dict[str, Any]:
        """
        ``GET /shares/:slug``：按 slug 获取分享详情

        :param slug: 分享 slug
        :return: 响应 ``data`` 字段
        """
        data, _ = self._request("GET", f"/shares/{slug}")
        return data

    def create_share(
        self,
        url: str,
        *,
        tmdb_id: str | int | None = None,
        media_type: MediaType | None = None,
        movie_id: int | None = None,
        tv_id: int | None = None,
        collection_id: int | None = None,
        title: str | None = None,
        access_code: str | None = None,
        share_size: str | None = None,
        video_resolution: list[VideoResolution] | None = None,
        source: list[Source] | None = None,
        subtitle_language: list[SubtitleLanguage] | None = None,
        subtitle_type: list[SubtitleType] | None = None,
        remark: str | None = None,
        unlock_points: int | None = None,
        is_anonymous: bool = False,
        hide_link: bool = True,
    ) -> dict[str, Any]:
        """
        ``POST /shares``：创建分享

        必须提供 ``tmdb_id`` + ``media_type``，或 ``movie_id`` / ``tv_id`` / ``collection_id``
        之一以关联媒体条目

        :param url: 分享链接
        :param tmdb_id: TMDB ID，可选
        :param media_type: 媒体类型，可选
        :param movie_id: 电影 ID，可选
        :param tv_id: 剧集 ID，可选
        :param collection_id: 合集 ID，可选
        :param title: 标题，可选
        :param access_code: 提取码，可选
        :param share_size: 体积描述，可选
        :param video_resolution: 分辨率列表，可选
        :param source: 片源列表，可选
        :param subtitle_language: 字幕语言列表，可选
        :param subtitle_type: 字幕类型列表，可选
        :param remark: 备注，可选
        :param unlock_points: 解锁所需积分，可选
        :param is_anonymous: 是否匿名
        :param hide_link: 是否隐藏链接
        :return: 响应 ``data`` 字段
        """
        body: dict[str, Any] = {"url": url}
        if tmdb_id is not None:
            body["tmdb_id"] = str(tmdb_id)
        if media_type is not None:
            body["media_type"] = media_type
        if movie_id is not None:
            body["movie_id"] = movie_id
        if tv_id is not None:
            body["tv_id"] = tv_id
        if collection_id is not None:
            body["collection_id"] = collection_id
        if title is not None:
            body["title"] = title
        if access_code is not None:
            body["access_code"] = access_code
        if share_size is not None:
            body["share_size"] = share_size
        if video_resolution is not None:
            body["video_resolution"] = video_resolution
        if source is not None:
            body["source"] = source
        if subtitle_language is not None:
            body["subtitle_language"] = subtitle_language
        if subtitle_type is not None:
            body["subtitle_type"] = subtitle_type
        if remark is not None:
            body["remark"] = remark
        if unlock_points is not None:
            body["unlock_points"] = unlock_points
        body["is_anonymous"] = is_anonymous
        body["hide_link"] = hide_link
        data, _ = self._request("POST", "/shares", json=body)
        return data

    def update_share(
        self,
        slug: str,
        *,
        title: str | None = None,
        url: str | None = None,
        access_code: str | None = None,
        share_size: str | None = None,
        video_resolution: list[VideoResolution] | None = None,
        source: list[Source] | None = None,
        subtitle_language: list[SubtitleLanguage] | None = None,
        subtitle_type: list[SubtitleType] | None = None,
        remark: str | None = None,
        unlock_points: int | None = None,
        is_anonymous: bool | None = None,
        hide_link: bool | None = None,
        notify: bool | None = None,
    ) -> dict[str, Any]:
        """
        ``PATCH /shares/:slug``：部分更新分享（仅提交有值的字段）

        :param slug: 分享 slug
        :param title: 标题，可选
        :param url: 链接，可选
        :param access_code: 提取码，可选
        :param share_size: 体积，可选
        :param video_resolution: 分辨率列表，可选
        :param source: 片源列表，可选
        :param subtitle_language: 字幕语言列表，可选
        :param subtitle_type: 字幕类型列表，可选
        :param remark: 备注，可选
        :param unlock_points: 解锁积分，可选
        :param is_anonymous: 是否匿名，可选
        :param hide_link: 是否隐藏链接，可选
        :param notify: 是否通知关注者，可选
        :return: 响应 ``data`` 字段
        :raises ValueError: 未提供任何可更新字段时
        """
        body: dict[str, Any] = {}
        if title is not None:
            body["title"] = title
        if url is not None:
            body["url"] = url
        if access_code is not None:
            body["access_code"] = access_code
        if share_size is not None:
            body["share_size"] = share_size
        if video_resolution is not None:
            body["video_resolution"] = video_resolution
        if source is not None:
            body["source"] = source
        if subtitle_language is not None:
            body["subtitle_language"] = subtitle_language
        if subtitle_type is not None:
            body["subtitle_type"] = subtitle_type
        if remark is not None:
            body["remark"] = remark
        if unlock_points is not None:
            body["unlock_points"] = unlock_points
        if is_anonymous is not None:
            body["is_anonymous"] = is_anonymous
        if hide_link is not None:
            body["hide_link"] = hide_link
        if notify is not None:
            body["notify"] = notify
        if not body:
            raise ValueError("update_share requires at least one field to update")
        data, _ = self._request("PATCH", f"/shares/{slug}", json=body)
        return data

    def delete_share(self, slug: str) -> None:
        """
        ``DELETE /shares/:slug``：删除分享（消耗 1 积分）

        :param slug: 分享 slug
        """
        self._request("DELETE", f"/shares/{slug}")
