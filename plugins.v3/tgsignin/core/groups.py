"""
Telegram 会话查询：列出某个账号能看到哪些群 / 频道，以及它们的数字 ID。

用途（2026-10-12 加）：给插件添加「群签到目标」时不必再到别处去查群号 —— 私有群没有
``@用户名``，只能靠数字 ID（形如 ``-1001234567890``）填进目标。

只发 ``GetDialogs`` 这类只读请求：不发送任何消息、也不改动 Telegram 侧状态。群名与
群号只回给调用方（插件页面 / 命令），不写日志。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .config import AccountConfig
from .session import build_client, secure_session_files

__all__ = [
    "MAX_GROUP_ROWS",
    "GROUP_LINE_LIMIT",
    "group_kind",
    "group_ref",
    "collect_group_rows",
    "list_chat_groups",
    "group_list_text",
    "group_pick_items",
]

# 一次最多返回多少个群组（账号在几百个群里时不至于刷屏）
MAX_GROUP_ROWS = 200
# 命令回复里最多列出的条数（Telegram 消息长度有限）
GROUP_LINE_LIMIT = 30


def group_kind(entity: Any) -> str:
    """
    返回群 / 频道的类型描述。

    :param entity: telethon 实体（Channel / Chat）
    :return str: ``超级群`` / ``频道（广播）`` / ``普通群`` / ``群组``
    """

    if getattr(entity, "broadcast", False):
        return "频道（广播）"
    if getattr(entity, "megagroup", False):
        return "超级群"
    if getattr(entity, "title", ""):
        return "普通群"
    return "群组"


def group_ref(username: str, chat_id: Any) -> str:
    """
    返回「填进目标槽」用的标识。

    公开群优先用 ``@用户名``（解析不依赖会话缓存、更稳），没有用户名才用数字 ID。

    :param username: 群 / 频道的公开用户名（可空）
    :param chat_id: 数字 ID（形如 ``-100…``）
    :return str: 目标标识；两者都没有时返回空串
    """

    name = str(username or "").strip().lstrip("@")
    if name:
        return f"@{name}"
    return str(chat_id or "").strip()


def collect_group_rows(
    dialogs: Sequence[Any],
    configured: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """
    把会话列表整理成「群组」行（过滤掉私聊与机器人）。

    排序：**私有群优先**（那正是需要查群号的），其次按群名。同一数字 ID 只保留一条。

    :param dialogs: telethon dialog 列表
    :param configured: 已配置的目标标识（用于标记「已配为目标」）
    :return List[Dict[str, Any]]: 行列表，每行含 title / chat_id / username / ref / kind / private / archived / configured
    """

    refs = {str(item) for item in (configured or []) if item}
    rows: Dict[str, Dict[str, Any]] = {}
    for dialog in dialogs:
        if not (
            getattr(dialog, "is_group", False) or getattr(dialog, "is_channel", False)
        ):
            continue
        entity = getattr(dialog, "entity", None)
        if entity is None:
            continue
        username = str(getattr(entity, "username", "") or "")
        chat_id = getattr(dialog, "id", None)
        ref = group_ref(username, chat_id)
        if not ref:
            continue
        rows[str(chat_id)] = {
            "title": str(getattr(dialog, "name", "") or getattr(entity, "title", "") or ""),
            "chat_id": str(chat_id),
            "username": username,
            "ref": ref,
            "kind": group_kind(entity),
            "private": not username,
            "archived": bool(getattr(dialog, "archived", False)),
            "configured": ref in refs or str(chat_id) in refs,
        }
    return sorted(
        rows.values(),
        key=lambda row: (not bool(row.get("private")), str(row.get("title") or "")),
    )


async def list_chat_groups(
    account: AccountConfig,
    data_dir: Path,
    proxy: Optional[Tuple[str, str, int]] = None,
    configured: Optional[Sequence[str]] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    """
    连上某个账号，只读列出它可见的群 / 频道与数字 ID。

    :param account: 账号配置
    :param data_dir: 插件数据目录
    :param proxy: 代理元组，None 表示直连
    :param configured: 已配置的目标标识（用于标记「已配为目标」）
    :param timeout: 连接超时秒数
    :return Dict[str, Any]: ``{ok, message, rows, total, truncated}``
    """

    empty: Dict[str, Any] = {
        "ok": False,
        "message": "",
        "rows": [],
        "total": 0,
        "truncated": False,
    }
    try:
        client = build_client(data_dir, account, proxy)
    except RuntimeError as error:
        return {**empty, "message": str(error)}
    try:
        await asyncio.wait_for(client.connect(), timeout=timeout)
        secure_session_files(data_dir, account.key)
        if not await client.is_user_authorized():
            return {
                **empty,
                "message": f"账号 {account.key} 未登录或 session 已失效，请重新登录",
            }
        dialogs: List[Any] = []
        # archived=None 覆盖常规会话，再补一遍归档会话（只读）
        for archived in (None, True):
            async for dialog in client.iter_dialogs(archived=archived):
                dialogs.append(dialog)
        rows = collect_group_rows(dialogs, configured)
        return {
            "ok": True,
            "message": f"共 {len(rows)} 个群 / 频道",
            "rows": rows[:MAX_GROUP_ROWS],
            "total": len(rows),
            "truncated": len(rows) > MAX_GROUP_ROWS,
        }
    except Exception as error:  # pylint: disable=broad-except
        return {**empty, "message": f"查询失败：{type(error).__name__}: {error}"}
    finally:
        try:
            await client.disconnect()
        except Exception:  # pylint: disable=broad-except
            pass


def group_list_text(
    rows: Sequence[Dict[str, Any]],
    account_label: str = "",
    limit: int = GROUP_LINE_LIMIT,
) -> str:
    """
    把群组列表渲染成命令回复文本。

    :param rows: 行列表（:func:`collect_group_rows` 的产物）
    :param account_label: 账号展示名（作为首行前缀）
    :param limit: 最多列出的条数
    :return str: 多行文本
    """

    if not rows:
        return (
            f"{account_label}没有查到任何群 / 频道。"
            "如果确实加过群，请确认登录的是同一个账号；也可以用插件详情页的"
            "「查询群组」按钮再试一次"
        )
    lines: List[str] = []
    for row in rows[:limit]:
        note = (
            "已配为目标"
            if row.get("configured")
            else ("私有群，只能填数字 ID" if row.get("private") else "公开群，可填 @用户名")
        )
        lines.append(
            f"- {str(row.get('title') or '(无标题)')}｜{row.get('chat_id')}"
            f"｜{row.get('kind')}｜{note}"
        )
    head = f"{account_label}共 {len(rows)} 个群 / 频道"
    if len(rows) > limit:
        head = f"{head}（下面只列前 {limit} 个）"
    return "\n".join([head, *lines])


def group_pick_items(
    cache: Any,
    account_key: str,
    limit: int = MAX_GROUP_ROWS,
) -> List[Dict[str, str]]:
    """
    把缓存的群组列表整理成配置表单里的下拉项（选好即可一键填入目标槽）。

    只列**指定账号**的群组：一个账号看不到别人账号的群，候选混在一起会选错（2026-10-12
    用户要求按行账号收窄）。没给账号、或该账号还没查过时返回空候选——绝不拿别的账号的群来凑。

    :param cache: ``state["groups"]`` 结构：``{账号: {"rows": [...]}}``
    :param account_key: 只列该账号的群组（必填；空值返回空列表）
    :param limit: 最多返回多少项
    :return List[Dict[str, str]]: ``[{"title": "群名（群号）", "value": 目标标识}]``
    """

    items: List[Dict[str, str]] = []
    wanted = str(account_key or "").strip()
    if not isinstance(cache, dict) or not wanted:
        return items
    payload = cache.get(wanted)
    rows = payload.get("rows") if isinstance(payload, dict) else None
    seen: set = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        value = str(row.get("ref") or "")
        if not value or value in seen:
            continue
        seen.add(value)
        items.append(
            {
                "title": f"{str(row.get('title') or '(无标题)')}（{row.get('chat_id')}）",
                "value": value,
            }
        )
        if len(items) >= limit:
            return items
    return items
