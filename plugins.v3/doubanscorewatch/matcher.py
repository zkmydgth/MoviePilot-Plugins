# -*- coding: utf-8 -*-
"""
豆瓣候选匹配与 AI 季映射：全部为纯函数，便于单元测试。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

#: 标题比较时忽略的标点、空白与零宽字符
_TITLE_NOISE = re.compile(r"[\s\-·:：,，.。!！?？'\"“”‘’()（）\[\]【】/、_\u200b-\u200f\ufeff]+")
#: 标题尾部的季标记，例如「第二季」「第2季」「Season 2」
_SEASON_SUFFIX = re.compile(r"(第[一二三四五六七八九十0-9]+季|season\s*\d+|s\d{1,2})$", re.IGNORECASE)
#: 标题中的季标记，兼容「第X季 / 第X部 / Season X」
_SEASON_MARKER = re.compile(r"第([一二三四五六七八九十零〇两0-9]+)[季部]|season\s*(\d+)", re.IGNORECASE)
#: 中文数字字符表
_CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}


def _cn_to_int(text: str) -> Optional[int]:
    """
    把中文数字转换为整数。

    :param text: 阿拉伯数字或中文数字
    :return: 整数；无法解析时返回 None
    """
    text = (text or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    if "十" in text:
        left, _, right = text.partition("十")
        tens = _CN_DIGITS.get(left, 1) if left else 1
        ones = _CN_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    if len(text) == 1:
        return _CN_DIGITS.get(text)
    return None


def season_marker(title: Any) -> Optional[int]:
    """
    从豆瓣条目标题中解析季号。

    :param title: 条目标题，例如「现在就出发 第四季」
    :return: 季号；标题不含季标记时返回 None
    """
    match = _SEASON_MARKER.search(str(title or ""))
    if not match:
        return None
    return _cn_to_int(match.group(1) or match.group(2) or "")


def episode_slot(candidates: List[Dict[str, Any]], current_episode: int) -> Optional[Dict[str, Any]]:
    """
    按累计集数把当前集号落到某个豆瓣条目上。

    适用于 TMDB 只有一季、豆瓣却按「第一季 / 年番1 / 年番2…」拆分的长篇动画：
    按年份排序后累加各条目集数，返回覆盖 current_episode 的那一条。

    :param candidates: 带 episodes 字段的豆瓣候选
    :param current_episode: 当前已播/已订到的集号（从 1 开始）
    :return: 命中的候选（附带 episode_start / episode_end），无命中时返回 None
    """
    if current_episode <= 0:
        return None
    pool = [
        item
        for item in candidates
        if isinstance(item.get("episodes"), int) and item["episodes"] > 0
    ]
    if not pool:
        return None
    pool.sort(key=lambda item: (str(item.get("year") or "9999"), str(item.get("douban_id") or "")))
    offset = 0
    for item in pool:
        start = offset + 1
        end = offset + int(item["episodes"])
        offset = end
        if start <= current_episode <= end:
            hit = dict(item)
            hit["episode_start"] = start
            hit["episode_end"] = end
            return hit
    return None


def normalize_title(value: Any) -> str:
    """
    规范化标题，便于做等值比较。

    :param value: 原始标题
    :return: 去掉标点与空白并转小写的标题
    """
    text = str(value or "").strip()
    if not text:
        return ""
    text = _TITLE_NOISE.sub("", text)
    return text.lower()


def strip_season_suffix(value: Any) -> str:
    """
    去掉标题尾部的季标记后再规范化。

    :param value: 原始标题
    :return: 不含季标记的规范化标题
    """
    text = _TITLE_NOISE.sub("", str(value or "").strip())
    text = _SEASON_SUFFIX.sub("", text)
    return text.lower()


def year_gap(left: Any, right: Any) -> int:
    """
    计算两个年份的距离，缺失或非法时返回一个大值。

    :param left: 年份一
    :param right: 年份二
    :return: 年份差的绝对值；无法比较时返回 999
    """
    try:
        return abs(int(str(left).strip()) - int(str(right).strip()))
    except (TypeError, ValueError):
        return 999


def select_candidates(
    candidates: List[Dict[str, Any]],
    title: str,
    year: Any,
    season: Optional[int] = None,
    limit: int = 5,
) -> Dict[str, Any]:
    """
    在豆瓣候选中挑选可自动绑定的条目。

    判定顺序：已知季号时先按标题里的季标记对齐（豆瓣按季拆分的情况）；再看整剧条目，
    最后按年份收敛（相差不超过两年）与标题完全一致/互相包含。
    只有唯一命中的候选才自动绑定，其余返回候选列表等人工或 AI 处理。

    :param candidates: 豆瓣候选条目
    :param title: 订阅标题
    :param year: 订阅年份
    :param season: 订阅季号；多季或未知时传 None
    :param limit: 返回候选的最大数量
    :return: {"auto": 自动绑定条目或 None, "candidates": 候选列表, "reason": 判定说明}
    """
    normalized = normalize_title(title)
    if not candidates:
        return {"auto": None, "candidates": [], "reason": "豆瓣无可匹配条目"}
    wanted_season: Optional[int] = None
    if season is not None:
        try:
            wanted_season = int(season)
        except (TypeError, ValueError):
            wanted_season = None
    whole = [item for item in candidates if season_marker(item.get("title")) is None]
    if wanted_season and wanted_season > 0:
        same = [
            item for item in candidates if season_marker(item.get("title")) == wanted_season
        ]
        if len(same) == 1:
            return {"auto": same[0], "candidates": [], "reason": f"第{wanted_season}季唯一命中"}
        if len(same) > 1:
            return {
                "auto": None,
                "candidates": same[: max(1, limit)],
                "reason": f"豆瓣有多个第{wanted_season}季候选",
            }
        if not whole:
            others = sorted(
                {
                    marker
                    for item in candidates
                    if (marker := season_marker(item.get("title"))) is not None
                }
            )
            hint = f"（豆瓣现有其它季：{','.join(str(x) for x in others)}）" if others else ""
            return {
                "auto": None,
                "candidates": [],
                "reason": f"豆瓣没有第{wanted_season}季条目{hint}",
            }
        candidates = whole
    elif whole:
        # 多季或未知季时优先整剧条目，避免绑到某一季
        candidates = whole
    # 年份窗口内的候选优先：豆瓣条目年份通常是该条目的首播年
    in_year = [item for item in candidates if year_gap(item.get("year"), year) <= 2]
    base = in_year or list(candidates)
    exact = [item for item in base if normalize_title(item.get("title")) == normalized]
    pool = exact or [
        item
        for item in base
        if normalized
        and (
            normalized in normalize_title(item.get("title"))
            or normalize_title(item.get("title")) in normalized
        )
    ] or base
    # 年份窗口只剩一条，且标题相关：可以直接绑定
    if len(in_year) == 1 and pool:
        return {"auto": in_year[0], "candidates": [], "reason": "年份窗口内唯一命中"}
    if len(pool) == 1 and year_gap(pool[0].get("year"), year) <= 1:
        return {"auto": pool[0], "candidates": [], "reason": "标题与年份唯一命中"}
    limited = pool[: max(1, limit)]
    if len(limited) == 1 and year_gap(limited[0].get("year"), year) <= 1:
        return {"auto": limited[0], "candidates": [], "reason": "标题唯一命中"}
    return {
        "auto": None,
        "candidates": limited,
        "reason": f"存在 {len(limited)} 个候选，需要确认",
    }


def build_ai_messages(
    title: str,
    year: Any,
    media_type: str,
    seasons: List[int],
    candidates: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    """
    构造 AI 季映射的对话消息。

    :param title: 媒体标题
    :param year: 媒体年份
    :param media_type: 媒体类型
    :param seasons: 已订阅的季号
    :param candidates: 豆瓣候选条目
    :return: system 与 user 两条消息
    """
    system = (
        "你是影视条目对齐助手，只做一件事：判断 MoviePilot/TMDB 的订阅对应豆瓣的哪个条目。"
        "豆瓣可能把整部剧收成一个条目（覆盖多季），也可能按季拆分。"
        "只能从给定候选里选择，禁止编造 ID。"
        "输出必须是 JSON，格式："
        '{"bindings":[{"douban_id":"候选ID","seasons":[1],"confidence":0.0,"reason":"简短理由"}],'
        '"uncertain":false}。'
        "如果无法确定，返回 {\"bindings\":[],\"uncertain\":true}。"
    )
    payload = {
        "media": {
            "title": title,
            "year": str(year or ""),
            "type": media_type,
            "subscribed_seasons": seasons,
        },
        "douban_candidates": [
            {
                "douban_id": item.get("douban_id"),
                "title": item.get("title"),
                "year": item.get("year"),
                "rating": item.get("rating"),
                "votes": item.get("votes"),
            }
            for item in candidates
        ],
    }
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": "请给出映射结果：\n" + json.dumps(payload, ensure_ascii=False, indent=2),
        },
    ]


def parse_ai_bindings(
    text: str,
    candidates: List[Dict[str, Any]],
    allowed_seasons: List[int],
    min_confidence: float = 0.6,
) -> List[Dict[str, Any]]:
    """
    解析并校验 AI 返回的季映射结果。

    :param text: 模型原始输出
    :param candidates: 本次允许的豆瓣候选
    :param allowed_seasons: 订阅中真实存在的季号
    :param min_confidence: 采纳的最低置信度
    :return: 通过校验的绑定列表
    """
    raw = str(text or "").strip()
    if not raw:
        return []
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.S)
    if fenced:
        raw = fenced.group(1)
    else:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            return []
        raw = raw[start : end + 1]
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(payload, dict) or payload.get("uncertain"):
        return []
    allowed_ids = {str(item.get("douban_id")): item for item in candidates}
    allowed_season_set = {int(item) for item in allowed_seasons if str(item).isdigit()}
    bindings: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in payload.get("bindings") or []:
        if not isinstance(item, dict):
            continue
        douban_id = str(item.get("douban_id") or "").strip()
        if douban_id not in allowed_ids or douban_id in seen:
            continue
        try:
            confidence = float(item.get("confidence") or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < min_confidence:
            continue
        seasons: List[int] = []
        raw_seasons = item.get("seasons")
        if isinstance(raw_seasons, list):
            for season in raw_seasons:
                try:
                    value = int(season)
                except (TypeError, ValueError):
                    continue
                if value in allowed_season_set and value not in seasons:
                    seasons.append(value)
        if not seasons:
            seasons = sorted(allowed_season_set)
        seen.add(douban_id)
        bindings.append(
            {
                "douban_id": douban_id,
                "candidate": allowed_ids[douban_id],
                "seasons": seasons,
                "confidence": confidence,
                "reason": str(item.get("reason") or "").strip(),
            }
        )
    return bindings
