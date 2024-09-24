"""
签到执行：按钮式与命令式两条路径。

签到逻辑来自交接单里已在真实 Telegram 环境实测通过的实现（5/5 成功）：

- **按钮式**（emby 类 bot）：先发 ``/start`` 拉出菜单 → 在最近几条消息里找
  文字含关键词的 inline 按钮 → 点击 → 等待 → 读回最新回复；
- **命令式**（如 HDHaven）：直接发命令 → 等待 → 读回 bot 回复。

只要 bot 有回复即视为「签到动作已完成」——已签到、签到成功、仅回菜单都算，
因为 bot 侧的重复签到通常不报错（详情见交接单 §0.3④）。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .config import (
    SIGN_TYPE_BUTTON,
    SIGN_TYPE_COMMAND,
    AccountConfig,
    BotTarget,
)
from .session import build_client

__all__ = [
    "signin_one",
    "run_account",
    "run_all",
    "summarize_results",
    "now_text",
]

# 读取 bot 消息的条数上限（够覆盖菜单与回复）
_MSG_SCAN_LIMIT = 5
TZ = timezone(timedelta(hours=8))


def now_text() -> str:
    """
    返回当前北京时间字符串（日志与结果都用它）。

    :return str: ``YYYY-MM-DD HH:MM:SS``
    """

    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


def _pick_reply(messages: Sequence[Any]) -> str:
    """
    从消息列表里挑一条 bot 的文本回复。

    :param messages: telethon 消息列表（新→旧）
    :return str: 回复文本；没有则返回空串
    """

    for message in messages:
        if not getattr(message, "out", False) and getattr(message, "text", None):
            return str(message.text)
    if messages:
        return str(getattr(messages[0], "text", "") or "")
    return ""


async def _click_button(
    client: Any,
    entity: Any,
    keyword: str,
    wait_seconds: int,
) -> Tuple[bool, str, str]:
    """
    在最近消息里点文字含关键词的按钮，并读回回复。

    :param client: 已连接的 TelegramClient
    :param entity: bot 实体
    :param keyword: 按钮关键词（如「签到」）
    :param wait_seconds: 点击后等待秒数
    :return Tuple[bool, str, str]: ``(是否点到, 回复文本, 错误信息)``
    """

    messages = await client.get_messages(entity, limit=_MSG_SCAN_LIMIT)
    for message in messages:
        for row in (getattr(message, "buttons", None) or []):
            for button in row:
                text = str(getattr(button, "text", "") or "")
                if keyword and keyword in text:
                    await button.click()
                    await asyncio.sleep(wait_seconds)
                    latest = await client.get_messages(entity, limit=3)
                    return True, _pick_reply(latest), ""
    return False, "", f"最近 {_MSG_SCAN_LIMIT} 条消息里没找到含「{keyword}」的按钮"


async def signin_one(
    client: Any,
    target: BotTarget,
) -> Dict[str, Any]:
    """
    对单个 bot 执行一次签到。

    :param client: 已登录的 TelegramClient
    :param target: 签到目标配置
    :return Dict[str, Any]: ``{ok, reply, error, method, bot, time}``
    """

    result: Dict[str, Any] = {
        "bot": target.bot_username,
        "account": target.account_key,
        "method": target.method_desc(),
        "ok": False,
        "reply": "",
        "error": "",
        "time": now_text(),
    }
    try:
        entity = await client.get_entity(target.bot_username)
    except Exception as error:  # pylint: disable=broad-except
        result["error"] = f"找不到 bot {target.bot_username}：{error}"
        return result

    try:
        if target.sign_type == SIGN_TYPE_BUTTON:
            await client.send_message(entity, "/start")
            await asyncio.sleep(max(3, min(target.wait_seconds, 30)))
            clicked, reply, error = await _click_button(
                client, entity, target.action_text, target.wait_seconds
            )
            result["ok"] = clicked
            result["reply"] = reply
            result["error"] = error
            return result

        if target.sign_type == SIGN_TYPE_COMMAND:
            await client.send_message(entity, target.action_text or "/checkin")
            await asyncio.sleep(target.wait_seconds)
            latest = await client.get_messages(entity, limit=3)
            reply = _pick_reply(latest)
            result["ok"] = bool(reply)
            result["reply"] = reply
            result["error"] = "" if reply else "发送后没等到 bot 回复"
            return result
    except Exception as error:  # pylint: disable=broad-except
        result["error"] = f"执行失败：{type(error).__name__}: {error}"
        return result

    result["error"] = f"未知的签到方式：{target.sign_type}"
    return result


async def run_account(
    account: AccountConfig,
    targets: Sequence[BotTarget],
    data_dir: Path,
    proxy: Optional[Tuple[str, str, int]],
) -> Tuple[List[Dict[str, Any]], str]:
    """
    对单个账号执行它名下所有启用的签到目标。

    :param account: 账号配置
    :param targets: 该账号的目标列表
    :param data_dir: 插件数据目录
    :param proxy: 代理元组，None 表示直连
    :return Tuple[List[Dict[str, Any]], str]: ``(结果列表, 账号级错误)``；
        账号级错误非空时结果列表为空
    """

    try:
        client = build_client(data_dir, account, proxy)
    except RuntimeError as error:
        return [], str(error)

    try:
        await client.connect()
        if not await client.is_user_authorized():
            return [], f"账号 {account.key} 未登录或 session 已失效，请重新登录"
        results: List[Dict[str, Any]] = []
        for target in targets:
            results.append(await signin_one(client, target))
        return results, ""
    except Exception as error:  # pylint: disable=broad-except
        return [], f"账号 {account.key} 执行异常：{type(error).__name__}: {error}"
    finally:
        try:
            await client.disconnect()
        except Exception:  # pylint: disable=broad-except
            pass


async def run_all(
    accounts: Iterable[AccountConfig],
    targets: Sequence[BotTarget],
    data_dir: Path,
    proxy: Optional[Tuple[str, str, int]],
    only_account: Optional[str] = None,
    only_bot: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    按账号维度依次签到（同一时刻只连一个账号，避免并发触发风控）。

    :param accounts: 全部账号
    :param targets: 全部签到目标
    :param data_dir: 插件数据目录
    :param proxy: 代理元组，None 表示直连
    :param only_account: 只跑该账号标识（None 表示全部）
    :param only_bot: 只跑该 bot 用户名（None 表示全部）
    :return List[Dict[str, Any]]: 扁平的结果列表
    """

    results: List[Dict[str, Any]] = []
    for account in accounts:
        if only_account and account.key != only_account:
            continue
        account_targets = [
            target
            for target in targets
            if target.account_key == account.key
            and target.enabled
            and (not only_bot or target.bot_username == only_bot)
        ]
        if not account_targets:
            continue
        account_results, account_error = await run_account(
            account, account_targets, data_dir, proxy
        )
        if account_error:
            results.append(
                {
                    "bot": "-",
                    "account": account.key,
                    "method": "-",
                    "ok": False,
                    "reply": "",
                    "error": account_error,
                    "time": now_text(),
                }
            )
        results.extend(account_results)
    return results


def summarize_results(results: Sequence[Dict[str, Any]]) -> str:
    """
    生成签到结果的一句话摘要。

    :param results: 结果列表
    :return str: 如 ``3/5 成功``；无结果时为 ``没有可执行的目标``
    """

    if not results:
        return "没有可执行的目标"
    ok = sum(1 for item in results if item.get("ok"))
    return f"{ok}/{len(results)} 成功"
