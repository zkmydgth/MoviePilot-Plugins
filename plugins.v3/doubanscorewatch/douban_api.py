# -*- coding: utf-8 -*-
"""
豆瓣评分查询：优先复用宿主内置豆瓣接口，必要时用自定义 Cookie 抓网页兜底。
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.runtime.log import logger


class DoubanScoreClient:
    """豆瓣评分查询客户端。"""

    #: 网页兜底使用的浏览器 UA
    _web_user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )

    def __init__(self, cookie: str = "", timeout: int = 20) -> None:
        """
        初始化查询客户端。

        :param cookie: 自定义豆瓣 Cookie，留空时只走匿名接口
        :param timeout: 网页兜底请求超时秒数
        """
        self._cookie = (cookie or "").strip()
        self._timeout = max(5, int(timeout or 20))

    @staticmethod
    def _api() -> Any:
        """
        惰性取得宿主豆瓣接口实例，避免插件加载阶段拉起网络依赖。

        :return: DoubanApi 实例
        """
        from app.modules.douban.apiv2 import DoubanApi

        return DoubanApi()

    @staticmethod
    def _parse_rating(payload: Dict[str, Any]) -> tuple[float, int]:
        """
        从豆瓣响应中取出评分与评价人数。

        :param payload: 豆瓣详情或搜索结果中的条目
        :return: (评分, 评价人数)
        """
        rating = payload.get("rating") if isinstance(payload, dict) else None
        if not isinstance(rating, dict):
            return 0.0, 0
        try:
            value = float(rating.get("value") or 0)
        except (TypeError, ValueError):
            value = 0.0
        try:
            votes = int(rating.get("count") or 0)
        except (TypeError, ValueError):
            votes = 0
        return value, votes

    def search(self, keyword: str, media_type: str) -> List[Dict[str, Any]]:
        """
        按标题搜索豆瓣条目。

        :param keyword: 搜索关键词
        :param media_type: 媒体类型（电影/电视剧）
        :return: 候选条目列表，含豆瓣 ID、标题、年份、评分与人数
        """
        keyword = (keyword or "").strip()
        if not keyword:
            return []
        candidates = self._to_candidates(self._search_items(keyword), media_type)
        if candidates:
            return candidates
        # 空结果常见于豆瓣风控或索引延迟，降速重试一次再判定
        time.sleep(1.5)
        candidates = self._to_candidates(self._search_items(keyword), media_type)
        if not candidates:
            logger.info(f"【豆瓣开分】豆瓣未搜到「{keyword}」（{media_type}），稍后重试")
        return candidates

    def _search_items(self, keyword: str) -> List[Any]:
        """
        调用宿主豆瓣搜索接口并返回原始条目列表。

        宿主对豆瓣响应有最长 72 小时的进程内缓存，且空结果同样会被缓存；
        这里用每次不同的 _ts 作为缓存区分值，保证开分检测不会读到旧结果。

        :param keyword: 搜索关键词
        :return: 条目列表；请求失败时返回空列表
        """
        try:
            api = self._api()
            ts = self._fresh_ts()
            invoke = getattr(api, "_DoubanApi__invoke_search", None)
            if invoke is not None:
                search_path = getattr(api, "_urls", {}).get("search", "/search/weixin")
                response = invoke(search_path, q=keyword, start=0, count=20, _ts=ts) or {}
            else:
                response = api.search(keyword, count=20, ts=ts) or {}
        except Exception as error:  # noqa: BLE001 - 查询失败不应中断整轮检查
            logger.warning(f"【豆瓣开分】搜索 {keyword} 失败：{error}")
            return []
        items = response.get("items") if isinstance(response, dict) else None
        return items if isinstance(items, list) else []

    def _to_candidates(self, items: List[Any], media_type: str) -> List[Dict[str, Any]]:
        """
        把豆瓣搜索结果转换为候选条目。

        :param items: 搜索返回的条目列表
        :param media_type: 媒体类型
        :return: 候选条目列表
        """
        candidates: List[Dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("type_name") != media_type:
                continue
            target = item.get("target") if isinstance(item.get("target"), dict) else {}
            douban_id = target.get("id")
            if not douban_id:
                continue
            rating, votes = self._parse_rating(target)
            candidates.append(
                {
                    "douban_id": str(douban_id),
                    "title": str(target.get("title") or "").strip(),
                    "year": str(target.get("year") or "").strip(),
                    "rating": rating,
                    "votes": votes,
                    "type": media_type,
                }
            )
        return candidates

    def detail(self, douban_id: str, media_type: str) -> Optional[Dict[str, Any]]:
        """
        读取单个豆瓣条目的最新评分。

        :param douban_id: 豆瓣条目原生标识
        :param media_type: 媒体类型（电影/电视剧）
        :return: 含评分、人数、标题、年份与首播日期的字典；失败时返回 None
        """
        douban_id = str(douban_id or "").strip()
        if not douban_id:
            return None
        payload = self._detail_from_api(douban_id, media_type)
        rating, votes = self._parse_rating(payload or {})
        if not payload or (rating <= 0 and self._cookie):
            fallback = self._detail_from_web(douban_id)
            if fallback and (fallback.get("rating") or 0) > 0:
                return fallback
        if not payload:
            return None
        if isinstance(payload.get("code"), int) and payload.get("code") not in (0,):
            logger.warning(
                f"【豆瓣开分】条目 {douban_id} 接口返回异常：{payload.get('msg') or payload.get('localized_message')}"
            )
            return None
        pubdate = payload.get("pubdate")
        if isinstance(pubdate, list):
            pubdate = pubdate[0] if pubdate else ""
        return {
            "douban_id": douban_id,
            "title": str(payload.get("title") or "").strip(),
            "year": str(payload.get("year") or "").strip(),
            "rating": rating,
            "votes": votes,
            "pubdate": str(pubdate or "").strip(),
            "episodes": payload.get("episodes_count"),
            "type": media_type,
        }

    def _detail_from_api(self, douban_id: str, media_type: str) -> Optional[Dict[str, Any]]:
        """
        通过宿主豆瓣接口读取条目详情。

        同样绕过宿主的响应缓存，避免评分变化被缓存拖到最长 72 小时后才可见。

        :param douban_id: 豆瓣条目标识
        :param media_type: 媒体类型
        :return: 接口原始响应或 None
        """
        try:
            api = self._api()
            urls = getattr(api, "_urls", {}) or {}
            path_key = "tv_detail" if media_type == "电视剧" else "movie_detail"
            invoke = getattr(api, "_DoubanApi__invoke_search", None)
            if invoke is not None:
                path = urls.get(path_key) or ("/tv/" if media_type == "电视剧" else "/movie/")
                return invoke(f"{path}{douban_id}", _ts=self._fresh_ts())
            if media_type == "电视剧":
                return api.tv_detail(douban_id)
            return api.movie_detail(douban_id)
        except Exception as error:  # noqa: BLE001 - 接口异常交给网页兜底
            logger.warning(f"【豆瓣开分】读取条目 {douban_id} 失败：{error}")
            return None

    @staticmethod
    def _fresh_ts() -> str:
        """
        生成用于绕过宿主缓存的请求时间戳。

        :return: 精确到微秒的时间文本
        """
        return datetime.now().strftime("%Y%m%d%H%M%S%f")

    def _detail_from_web(self, douban_id: str) -> Optional[Dict[str, Any]]:
        """
        使用自定义 Cookie 抓取豆瓣网页作为兜底。

        :param douban_id: 豆瓣条目标识
        :return: 含评分与人数的字典；未配置 Cookie 或解析失败时返回 None
        """
        if not self._cookie:
            return None
        try:
            from app.adapters.network.http import RequestUtils

            response = RequestUtils(use_session=True, timeout=self._timeout).get_res(
                url=f"https://movie.douban.com/subject/{douban_id}/",
                headers={
                    "User-Agent": self._web_user_agent,
                    "Cookie": self._cookie,
                    "Accept-Language": "zh-CN,zh;q=0.9",
                },
            )
            if response is None or response.status_code != 200:
                return None
            html = response.text or ""
        except Exception as error:  # noqa: BLE001 - 网页兜底失败按无数据处理
            logger.warning(f"【豆瓣开分】网页兜底读取 {douban_id} 失败：{error}")
            return None
        rating_match = re.search(r'rating_num[^>]*>\s*([\d.]+)\s*<', html)
        votes_match = re.search(r"([\d,]+)\s*人评价", html)
        if not rating_match:
            return None
        try:
            rating = float(rating_match.group(1))
        except ValueError:
            return None
        votes = 0
        if votes_match:
            votes = int(votes_match.group(1).replace(",", ""))
        title_match = re.search(r'<span property="v:itemreviewed">([^<]+)</span>', html)
        year_match = re.search(r'<span class="year">\((\d{4})\)</span>', html)
        return {
            "douban_id": douban_id,
            "title": title_match.group(1).strip() if title_match else "",
            "year": year_match.group(1) if year_match else "",
            "rating": rating,
            "votes": votes,
            "pubdate": "",
            "type": "",
        }

    def test(self, douban_id: str, media_type: str = "电视剧") -> Dict[str, Any]:
        """
        测试当前接口配置能否取到指定条目。

        :param douban_id: 豆瓣条目标识
        :param media_type: 媒体类型
        :return: 包含 success、message 与数据的字典
        """
        detail = self.detail(douban_id, media_type)
        if not detail:
            return {
                "success": False,
                "message": "无法读取该豆瓣条目，请检查豆瓣 ID 或 Cookie",
                "data": None,
            }
        return {
            "success": True,
            "message": f"读取成功：{detail['title']} {detail['rating']} 分 / {detail['votes']} 人",
            "data": detail,
        }
