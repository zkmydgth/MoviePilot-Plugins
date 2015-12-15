"""
运行状态与最近签到结果的持久化。

状态文件放在插件数据目录（``/config/plugins/<插件ID>/state.json``），
保存最近一次运行、每个账号的登录信息，以及每个 bot 的最近一次结果，
供插件详情页与 API 展示；不写进会话记忆，也不含任何凭据明文。
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Sequence

from .retry import record_attempts

__all__ = [
    "state_path",
    "load_state",
    "save_state",
    "write_json_atomic",
    "read_json",
    "record_login",
    "record_login_event",
    "record_run",
    "recent_results",
    "record_ai_keywords",
    "AI_KEYWORD_LOG_LIMIT",
]

# 每个 bot 保留的历史结果条数上限
MAX_HISTORY_PER_BOT = 10
# 页面展示的最近结果条数
PAGE_RESULT_LIMIT = 30
# AI 归纳关键词的审计日志保留条数
AI_KEYWORD_LOG_LIMIT = 100
# 状态/待登录文件权限：只给属主读写（2026-10-11 加固）
STATE_FILE_MODE = 0o600
# 进程内写锁：多个后台线程各自「读-改-写」时避免丢更新
_WRITE_LOCK = threading.Lock()


def write_json_atomic(path: Path, data: Any, mode: int = STATE_FILE_MODE) -> None:
    """
    原子写入 JSON（临时文件 + ``os.replace``），并把权限收紧到 ``mode``。

    :param path: 目标文件路径
    :param data: 可序列化对象
    :param mode: 文件权限（默认 0600）
    :return None
    """

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, mode)
    except OSError:  # pragma: no cover - 权限设置失败不阻断写入
        pass
    os.replace(tmp, target)


def read_json(path: Path, default: Any = None) -> Any:
    """
    读取 JSON 文件（不存在或损坏时返回默认值）。

    :param path: 文件路径
    :param default: 读取失败时的返回值
    :return Any: 解析结果或默认值
    """

    target = Path(path)
    if not target.exists():
        return default
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return default


def state_path(data_dir: Path) -> Path:
    """
    返回状态文件路径。

    :param data_dir: 插件数据目录
    :return Path: ``state.json`` 路径
    """

    return Path(data_dir) / "state.json"


def load_state(data_dir: Path) -> Dict[str, Any]:
    """
    读取状态文件（不存在或损坏时返回空骨架）。

    :param data_dir: 插件数据目录
    :return Dict[str, Any]: 状态字典
    """

    path = state_path(data_dir)
    default: Dict[str, Any] = {
        "last_run_at": "",
        "last_source": "",
        "last_summary": "",
        "accounts": {},
        "history": [],
        "results": [],
        "ai_keyword_log": [],
    }
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return default
    if not isinstance(data, dict):
        return default
    for key, value in default.items():
        data.setdefault(key, value)
    return data


def save_state(data_dir: Path, state: Dict[str, Any]) -> None:
    """
    写入状态文件。

    :param data_dir: 插件数据目录
    :param state: 状态字典
    :return None
    """

    path = state_path(data_dir)
    with _WRITE_LOCK:
        write_json_atomic(path, state)


def record_login(data_dir: Path, account_key: str, info: Dict[str, Any]) -> None:
    """
    记录某个账号的登录信息。

    :param data_dir: 插件数据目录
    :param account_key: 账号标识
    :param info: ``{name, username, user_id, phone}``
    :return None
    """

    state = load_state(data_dir)
    entry = state.setdefault("accounts", {}).setdefault(account_key, {})
    entry.update(info or {})
    entry["logged_in"] = True
    save_state(data_dir, state)


def record_run(
    data_dir: Path,
    results: Sequence[Dict[str, Any]],
    source: str,
    summary: str = "",
) -> Dict[str, Any]:
    """
    记录一次签到运行的结果，并维护每个 bot 的历史。

    :param data_dir: 插件数据目录
    :param results: 本次结果列表
    :param source: 触发来源（定时 / 手动 / 命令）
    :param summary: 一句话摘要
    :return Dict[str, Any]: 更新后的状态字典
    """

    state = load_state(data_dir)
    first = results[0].get("time", "") if results else ""
    state["last_run_at"] = first
    state["last_source"] = source
    state["last_summary"] = summary
    state["results"] = list(results)

    history: List[Dict[str, Any]] = list(state.get("history") or [])
    for item in results:
        history.append(
            {
                "time": item.get("time", ""),
                "account": item.get("account", ""),
                "bot": item.get("bot", ""),
                "method": item.get("method", ""),
                "ok": bool(item.get("ok")),
                "reply": str(item.get("reply", ""))[:300],
                "error": str(item.get("error", ""))[:300],
                # 详情页还要展示的字段（2026-10-11 修：此前只落 7 个字段，
                # 导致页面「AI 复核」列恒空、状态细分与弹窗文本丢失）
                "status": str(item.get("status", ""))[:60],
                "alert": str(item.get("alert", ""))[:300],
                "ai_state": str(item.get("ai_state", ""))[:40],
                "ai_verdict": str(item.get("ai_verdict", ""))[:40],
                "ai_message": str(item.get("ai_message", ""))[:300],
                "ai_keywords": [
                    str(word) for word in (item.get("ai_keywords") or [])
                ][:10],
            }
        )
    # 只保留最近 N 条，避免状态文件无限膨胀
    per_bot_limit = MAX_HISTORY_PER_BOT
    keep: List[Dict[str, Any]] = []
    counter: Dict[str, int] = {}
    for item in reversed(history):
        key = f"{item.get('account')}|{item.get('bot')}"
        counter[key] = counter.get(key, 0) + 1
        if counter[key] <= per_bot_limit:
            keep.append(item)
    state["history"] = list(reversed(keep))
    # 记录每个目标的当日成败与尝试次数，供「失败重试」窗口判定使用（见 core/retry.py）
    state = record_attempts(state, results)
    save_state(data_dir, state)
    return state


def record_login_event(
    data_dir: Path,
    account_key: str,
    action: str,
    ok: bool,
    message: str,
) -> Dict[str, Any]:
    """
    记录一次登录动作（发码/确认）的结果，供详情页与通知展示。

    :param data_dir: 插件数据目录
    :param account_key: 账号标识
    :param action: 动作名（发送验证码 / 确认登录）
    :param ok: 是否成功
    :param message: 结果描述
    :return Dict[str, Any]: 更新后的状态字典
    """

    from datetime import datetime, timedelta, timezone  # pylint: disable=import-outside-toplevel

    state = load_state(data_dir)
    state["last_login"] = {
        "time": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S"),
        "account": account_key,
        "action": action,
        "ok": bool(ok),
        "message": str(message)[:300],
    }
    save_state(data_dir, state)
    return state


def recent_results(state: Dict[str, Any], limit: int = PAGE_RESULT_LIMIT) -> List[Dict[str, Any]]:
    """
    取最近的签到结果（新→旧）。

    :param state: 状态字典
    :param limit: 最多返回条数
    :return List[Dict[str, Any]]: 结果列表
    """

    history: Sequence[Dict[str, Any]] = state.get("history") or []
    return list(reversed(list(history)))[:limit]


def record_ai_keywords(
    data_dir: Path,
    entries: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    记录本次 AI 归纳**实际新增**的关键词（审计用，供详情页回看）。

    只记录真正写进词表的新词；被查重跳过的候选不在此列（那属于「已存在，未重复添加」）。

    :param data_dir: 插件数据目录
    :param entries: 每条形如 ``{time, account, bot, verdict, keyword}``
    :return Dict[str, Any]: 更新后的状态字典；无新增时原样返回
    """

    if not entries:
        return load_state(data_dir)
    state = load_state(data_dir)
    log: List[Dict[str, Any]] = list(state.get("ai_keyword_log") or [])
    log.extend(dict(item) for item in entries)
    state["ai_keyword_log"] = log[-AI_KEYWORD_LOG_LIMIT:]
    save_state(data_dir, state)
    return state
