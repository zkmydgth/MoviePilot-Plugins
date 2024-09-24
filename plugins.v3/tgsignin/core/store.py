"""
运行状态与最近签到结果的持久化。

状态文件放在插件数据目录（``/config/plugins/<插件ID>/state.json``），
保存最近一次运行、每个账号的登录信息，以及每个 bot 的最近一次结果，
供插件详情页与 API 展示；不写进会话记忆，也不含任何凭据明文。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Sequence

__all__ = [
    "state_path",
    "load_state",
    "save_state",
    "record_login",
    "record_run",
    "recent_results",
]

# 每个 bot 保留的历史结果条数上限
MAX_HISTORY_PER_BOT = 10
# 页面展示的最近结果条数
PAGE_RESULT_LIMIT = 30


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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


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
