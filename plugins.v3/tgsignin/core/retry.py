"""
失败重试窗口判定（纯函数，便于单测）。

规则（2026-10-07 用户定案）：

- 某个 bot 当天签到**失败**后，每隔 ``重试间隔`` 小时重试一次，直到成功；
- 窗口按**自然日**（北京时区 00:00）重置：状态里存的日期不是今天 → 当天失败记录清零，
  等这一天的正常签到（定时/手动）先跑，失败后才进入重试；
- ``重试间隔 = 0`` 表示关闭重试。

状态存在 ``state.json`` 的 ``signin_state``：``{"<账号>|<bot>": {date, ok, attempts, last_attempt_at}}``。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "TZ",
    "today_text",
    "retry_key",
    "signed_today",
    "evaluate_retry",
    "record_attempts",
]

TZ = timezone(timedelta(hours=8))


def today_text(now: Optional[datetime] = None) -> str:
    """
    返回北京时区「今天」的日期字符串。

    :param now: 指定时刻，None 表示当前时刻
    :return str: ``YYYY-MM-DD``
    """

    moment = now or datetime.now(TZ)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=TZ)
    return moment.astimezone(TZ).strftime("%Y-%m-%d")


def retry_key(target: Any) -> str:
    """
    计算某个签到目标在状态里的键。

    :param target: 签到目标（需有 account_key / bot_username）
    :return str: ``<账号>|<bot>``
    """

    return f"{getattr(target, 'account_key', '')}|{getattr(target, 'bot_username', '')}"


def _moment(now: Optional[datetime]) -> datetime:
    """
    归一化时刻为带时区的北京时间。

    :param now: 指定时刻或 None
    :return datetime: 带 tzinfo 的时刻
    """

    moment = now or datetime.now(TZ)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=TZ)
    return moment.astimezone(TZ)


def evaluate_retry(
    targets: Sequence[Any],
    state: Mapping[str, Any],
    now: Optional[datetime] = None,
    retry_interval_hours: int = 6,
) -> Tuple[List[Any], Dict[str, Any]]:
    """
    算出本次需要重试的目标，并返回更新后的状态。

    :param targets: 全部签到目标
    :param state: 当前状态字典
    :param now: 指定时刻（测试用），None 表示当前时刻
    :param retry_interval_hours: 重试间隔小时数，0 表示关闭重试
    :return Tuple[List[Any], Dict[str, Any]]: ``(需要重试的目标, 更新后的状态)``
    """

    moment = _moment(now)
    today = today_text(moment)
    interval_seconds = max(0, int(retry_interval_hours)) * 3600
    signin_state: Dict[str, Any] = {
        str(key): dict(value) if isinstance(value, Mapping) else {}
        for key, value in dict(state.get("signin_state") or {}).items()
    }
    due: List[Any] = []
    for target in targets:
        if not getattr(target, "enabled", True):
            continue
        key = retry_key(target)
        record = signin_state.get(key) or {}
        if record.get("date") != today:
            # 新的一天：窗口重置，等当天正常签到先跑（失败后才重试）
            signin_state[key] = {"date": today}
            continue
        if record.get("ok") is not False:
            # 今天没有失败记录（未跑过或已成功）：不需要重试
            continue
        if interval_seconds <= 0:
            continue
        last_attempt = float(record.get("last_attempt_at") or 0)
        if last_attempt and moment.timestamp() - last_attempt < interval_seconds:
            continue
        due.append(target)
    updated = dict(state)
    updated["signin_state"] = signin_state
    return due, updated


def signed_today(
    state: Mapping[str, Any],
    target: Any,
    now: Optional[datetime] = None,
) -> bool:
    """
    判断某个目标「今天此前是否已经签到成功过」。

    按钮式 bot 在已签到后往往只重发一次菜单、不给任何签到结果，
    这时只能靠「今天是否已经成功过」来分档（2026-10-07 用户定案）。

    :param state: 当前状态字典
    :param target: 签到目标
    :param now: 指定时刻（测试用），None 表示当前时刻
    :return bool: 今天此前成功过返回 True
    """

    today = today_text(now)
    record = dict(state.get("signin_state") or {}).get(retry_key(target)) or {}
    if record.get("date") != today:
        return False
    return bool(record.get("ok_today"))


def record_attempts(
    state: Mapping[str, Any],
    results: Sequence[Mapping[str, Any]],
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    把一次运行的结果写进 ``signin_state``（按自然日累计尝试次数）。

    只按账号+bot 记录结果；``ok`` 反映**最近一次**尝试，供重试窗口判定使用。

    :param state: 当前状态字典
    :param results: 本次结果列表（需含 account / bot / ok）
    :param now: 指定时刻（测试用），None 表示当前时刻
    :return Dict[str, Any]: 更新后的状态字典
    """

    moment = _moment(now)
    today = today_text(moment)
    stamp = moment.timestamp()
    signin_state: Dict[str, Any] = {
        str(key): dict(value) if isinstance(value, Mapping) else {}
        for key, value in dict(state.get("signin_state") or {}).items()
    }
    for item in results:
        key = f"{item.get('account', '')}|{item.get('bot', '')}"
        record = signin_state.get(key) or {}
        if record.get("date") != today:
            record = {"date": today, "attempts": 0}
        record["ok"] = bool(item.get("ok"))
        # 今天只要成功过一次就置位（供「只回菜单」分档使用），当天重置时清零
        record["ok_today"] = bool(record.get("ok_today")) or bool(item.get("ok"))
        record["attempts"] = int(record.get("attempts") or 0) + 1
        record["last_attempt_at"] = stamp
        record["last_status"] = str(item.get("status") or "")
        signin_state[key] = record
    updated = dict(state)
    updated["signin_state"] = signin_state
    # 顺带保留一份可读摘要，供详情页/日志排查
    updated["signin_state_date"] = today
    return updated
