"""
AI 归纳出的关键词：清洗、查重与安全合并（**只增不删**）。

设计约束（2026-10-08 用户定案）：

- 写入前**先查重**：候选词若已存在于本栏或**其它任一栏**词表，直接跳过，绝不重复添加；
- 只追加新词，绝不修改或删除既有词；
- 候选词必须**逐字出现在该条回复原文里**（AI 不得自创）；
- 长度 2-10 字、不含纯数字/符号、不在过泛词黑名单；
- 每栏有上限（``KEYWORD_LIST_LIMIT``），到顶时丢弃新候选而不是挤掉既有词。
"""

from __future__ import annotations

import re
from typing import Iterable, List, Sequence, Tuple

from .config import KEYWORD_LIST_LIMIT

__all__ = [
    "KEYWORD_MIN_LEN",
    "KEYWORD_MAX_LEN",
    "KEYWORD_BLACKLIST",
    "normalize_keyword",
    "is_acceptable_keyword",
    "merge_keywords",
]

# 短语长度窗口
KEYWORD_MIN_LEN = 2
KEYWORD_MAX_LEN = 10
# 过泛词黑名单：单独出现不足以判定档位，禁止入表
KEYWORD_BLACKLIST = (
    "签到",
    "打卡",
    "成功",
    "失败",
    "已签",
    "领取",
    "奖励",
    "谢谢",
    "感谢",
    "稍后",
    "重试",
    "今天",
    "明天",
)

_WHITESPACE = re.compile(r"\s+")
# 纯数字 / 纯符号（含空白）：这类片段随日期或编号变化，入表无意义
_NUMERIC_ONLY = re.compile(r"[\d\W_]+")


def normalize_keyword(text: object) -> str:
    """
    归一化候选关键词（去掉首尾空白并把连续空白压成一个空格）。

    :param text: 原始候选词
    :return str: 归一化后的词；无有效内容时返回空串
    """

    return _WHITESPACE.sub(" ", str(text or "")).strip()


def is_acceptable_keyword(
    word: str,
    source_text: str = "",
    blacklist: Sequence[str] = KEYWORD_BLACKLIST,
) -> bool:
    """
    判断候选关键词是否可入表。

    :param word: 候选词（应已归一化）
    :param source_text: 该条回复原文；非空时要求候选词逐字出现在其中
    :param blacklist: 过泛词黑名单
    :return bool: 可入表返回 True
    """

    if not word or len(word) < KEYWORD_MIN_LEN or len(word) > KEYWORD_MAX_LEN:
        return False
    if word in set(blacklist):
        return False
    if _NUMERIC_ONLY.fullmatch(word):
        return False
    if source_text and word not in str(source_text):
        return False
    return True


def merge_keywords(
    existing: Sequence[str],
    candidates: Iterable[str],
    limit: int = KEYWORD_LIST_LIMIT,
    blocked: Sequence[str] = (),
) -> Tuple[List[str], List[str]]:
    """
    把候选词合并进既有词表（去重、保序、只增不删、超上限丢弃新词）。

    查重口径（用户 2026-10-08 明确要求）：
    - 与本栏既有词比对 → 已有则跳过（按大小写/空白归一后比较，保留既有写法）；
    - 与 ``blocked``（通常传**其它两栏**的现有词）比对 → 已有则跳过，避免同一个短语
      同时出现在不同档位（例如既算成功词又算失败词）；
    - 候选词之间也去重，只留首次出现。

    :param existing: 本栏既有词表
    :param candidates: 候选词（可能含重复或不合规项）
    :param limit: 合并后词表的最大条数
    :param blocked: 其它栏既有词（视为已存在，不重复添加）
    :return Tuple[List[str], List[str]]: ``(合并后的词表, 本次实际新增的词)``
    """

    merged: List[str] = [str(item) for item in existing]
    seen = {normalize_keyword(item).lower() for item in merged}
    blocked_keys = {normalize_keyword(item).lower() for item in blocked}
    added: List[str] = []
    for candidate in candidates:
        word = normalize_keyword(candidate)
        if not word:
            continue
        key = word.lower()
        if key in seen or key in blocked_keys:
            # 已存在（本栏或其它栏）→ 不重复添加
            continue
        if len(merged) >= max(1, int(limit)):
            break
        merged.append(word)
        seen.add(key)
        added.append(word)
    return merged, added
