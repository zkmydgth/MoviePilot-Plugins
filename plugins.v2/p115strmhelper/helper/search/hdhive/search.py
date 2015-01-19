from typing import Any, Dict, List, Optional

from app.log import logger
from app.schemas.types import MediaType

from ....core.config import configer
from ...hdhive.open import HDHiveAPIError, HDHiveOpenClient


def _media_type_to_hdhive(value: Any) -> Optional[str]:
    """
    将交互 resource_dict 的 type 转为 HDHive Open API 的 movie / tv
    """
    if value is None:
        return None
    if isinstance(value, MediaType):
        if value == MediaType.MOVIE:
            return "movie"
        if value == MediaType.TV:
            return "tv"
        return None
    s = str(value).strip()
    if s in (MediaType.MOVIE.value, "电影"):
        return "movie"
    if s in (MediaType.TV.value, "电视剧"):
        return "tv"
    low = s.lower()
    if low == "movie":
        return "movie"
    if low == "tv":
        return "tv"
    return None


def fetch_resources_impl(
    resource_dict: Dict[str, Any], source_tag: str
) -> List[Dict[str, Any]]:
    """
    调用 get_resources，过滤 pan_type=115，映射为与 TG 合并兼容的字典列表
    """
    api_key = (configer.get_config("hdhive_api_key") or "").strip()
    if not api_key:
        return []

    mt = _media_type_to_hdhive(resource_dict.get("type"))
    tmdb_id = resource_dict.get("tmdb_id")
    if not mt or tmdb_id is None:
        logger.debug("【HDHive】跳过：无有效 media_type 或 tmdb_id: %s", resource_dict)
        return []

    try:
        with HDHiveOpenClient(api_key) as client:
            items, _meta = client.get_resources(mt, tmdb_id)
    except HDHiveAPIError as e:
        logger.warning("【HDHive】get_resources 失败: %s", e)
        return []
    except Exception as e:
        logger.warning("【HDHive】get_resources 异常: %s", e, exc_info=True)
        return []

    out: List[Dict[str, Any]] = []
    for row in items:
        if not isinstance(row, dict):
            continue
        if str(row.get("pan_type") or "") != "115":
            continue
        slug = row.get("slug")
        if not slug:
            continue
        title = (row.get("title") or "").strip() or "未命名"
        media_url = (row.get("media_url") or "").strip()
        out.append(
            {
                "shareurl": "",
                "taskname": title,
                "content": "",
                "tags": [],
                "channel_id": "",
                "channel_name": "HDHive",
                "source": source_tag,
                "hdhive_slug": str(slug),
                "hdhive_media_url": media_url,
                "hdhive_raw": row,
            }
        )
    return out
