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
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

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
    "build_notify_text",
    "classify_result",
    "STATUS_SUCCESS",
    "STATUS_REPEATED",
    "STATUS_UNCONFIRMED",
    "STATUS_FAILED",
    "now_text",
]

# 通知正文里每行 bot 回复的截断长度
_SNIPPET_LIMIT = 60
# 通知正文最多列出的明细条数
_DETAIL_LIMIT = 10

# 结果状态分类（按 bot 回复内容判定）
STATUS_SUCCESS = "签到成功"
STATUS_REPEATED = "今日已签到"
STATUS_UNCONFIRMED = "未确认"
STATUS_FAILED = "失败"

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
) -> Tuple[bool, str, str, str]:
    """
    在最近消息里点文字含关键词的按钮，并读回回复。

    :param client: 已连接的 TelegramClient
    :param entity: bot 实体
    :param keyword: 按钮关键词（如「签到」）
    :param wait_seconds: 点击后等待秒数
    :return Tuple[bool, str, str, str]: ``(是否点到, 回复文本, 弹窗文本, 错误信息)``；
        弹窗文本来自 Telegram 的 callback 应答（bot 用 ``answerCallbackQuery``
        弹的那句提示，例如「您今天已经签到过了」），拿不到时为空串
    """

    messages = await client.get_messages(entity, limit=_MSG_SCAN_LIMIT)
    for message in messages:
        for row in (getattr(message, "buttons", None) or []):
            for button in row:
                text = str(getattr(button, "text", "") or "")
                if keyword and keyword in text:
                    # click() 对 inline 按钮返回 BotCallbackAnswer（含 .message/.alert）
                    answer = await button.click()
                    alert = str(getattr(answer, "message", "") or "")
                    await asyncio.sleep(wait_seconds)
                    latest = await client.get_messages(entity, limit=3)
                    return True, _pick_reply(latest), alert, ""
    return False, "", "", f"最近 {_MSG_SCAN_LIMIT} 条消息里没找到含「{keyword}」的按钮"


def classify_result(
    reply: str,
    ok: bool,
    method: str = "",
    alert: str = "",
) -> str:
    """
    按 bot 回复内容给签到结果分档。

    判据来自交接单实测：emby 类 bot 真签到成功会回「🎉 签到成功 | N 子弹…」，
    重复签到只回主菜单并弹一句提示（callback 应答）；HDHaven 重复签到回
    「✅ 今日已签到，明天再来。」。

    :param reply: bot 回复文本
    :param ok: 本次是否判定为成功（有回复即算动作完成）
    :param method: 签到方式描述（用于区分「点了按钮但只回菜单」）
    :param alert: 点击按钮时 Telegram 返回的弹窗提示（callback 应答文本）
    :return str: STATUS_SUCCESS / STATUS_REPEATED / STATUS_UNCONFIRMED / STATUS_FAILED
    """

    if not ok:
        return STATUS_FAILED
    alert_text = str(alert or "")
    text = str(reply or "")
    if alert_text and "已签到" in alert_text:
        return STATUS_REPEATED
    if "签到成功" in text or "签到成功" in alert_text:
        return STATUS_SUCCESS
    if "已签到" in text or "已签到" in alert_text:
        return STATUS_REPEATED
    if "按钮" in str(method or ""):
        # 按钮点到了、bot 只回菜单：按交接单，这就是「今日已签到、不再重复发放」，
        # 部分 bot 会另发一条弹窗提示（alert），拿不到也不影响判定
        return STATUS_REPEATED
    return STATUS_UNCONFIRMED


async def signin_one(
    client: Any,
    target: BotTarget,
) -> Dict[str, Any]:
    """
    对单个 bot 执行一次签到，并补上结果状态分类。

    :param client: 已登录的 TelegramClient
    :param target: 签到目标配置
    :return Dict[str, Any]: 结果字典（含 status）
    """

    result = await _signin_one_impl(client, target)
    result["status"] = classify_result(
        str(result.get("reply") or ""),
        bool(result.get("ok")),
        str(result.get("method") or ""),
    )
    return result


async def _signin_one_impl(
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
        "alert": "",
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
            clicked, reply, alert, error = await _click_button(
                client, entity, target.action_text, target.wait_seconds
            )
            result["ok"] = clicked
            result["reply"] = reply
            result["alert"] = alert
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
            item = await signin_one(client, target)
            # 带上显示名：通知正文里显示「账号1(acc1)」比纯标识好认
            item["account_label"] = account.display()
            results.append(item)
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
                    "account_label": account.display(),
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


def _snippet(text: Any, limit: int = _SNIPPET_LIMIT) -> str:
    """
    把 bot 回复/错误压缩成一行摘要。

    :param text: 原始文本
    :param limit: 最大长度
    :return str: 单行摘要
    """

    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit] + "…"


def _account_name(item: Mapping[str, Any]) -> str:
    """
    取结果里的账号显示名（没有时退回标识）。

    :param item: 一条签到结果
    :return str: 如 ``账号1(acc1)``
    """

    return str(item.get("account_label") or item.get("account") or "")


def _status_note(item: Mapping[str, Any]) -> str:
    """
    生成结果行的状态补充说明。

    用来区分「bot 弹窗说已签到」「回复里说已签到」「只回了菜单」等不同情形，
    避免所有重复签到都显示成同一句话。

    :param item: 一条签到结果
    :return str: 形如 ``（bot 弹窗：您今天已经签到过了）``；无需补充时返回空串
    """

    status = str(item.get("status") or STATUS_SUCCESS)
    alert = " ".join(str(item.get("alert") or "").split())
    reply = " ".join(str(item.get("reply") or "").split())
    if status == STATUS_SUCCESS:
        if alert:
            return f"（bot 弹窗：{_snippet(alert, 40)}）"
        return f"｜{_snippet(reply)}" if reply else ""
    if status == STATUS_REPEATED:
        if alert:
            return f"（bot 弹窗：{_snippet(alert, 40)}）"
        if "已签到" in reply:
            return f"｜{_snippet(reply)}"
        return "（按钮式：已点击，bot 未返回签到结果）"
    if status == STATUS_UNCONFIRMED:
        return "（未在回复里看到签到结果）"
    return ""


def build_notify_text(
    results: Sequence[Dict[str, Any]],
    source: str,
    mode: str,
) -> Optional[str]:
    """
    按通知方式生成签到通知正文。

    :param results: 本次签到结果
    :param source: 触发来源（定时/手动/命令）
    :param mode: 通知方式（``core.config.NOTIFY_MODE_*``）
    :return Optional[str]: 需要通知时的正文；不需要通知时返回 None
    """

    from .config import (  # pylint: disable=import-outside-toplevel
        NOTIFY_MODE_FAILURE,
        NOTIFY_MODE_NONE,
        NOTIFY_MODE_SUCCESS,
    )

    if mode == NOTIFY_MODE_NONE or not results:
        return None
    total = len(results)
    failed = [item for item in results if not item.get("ok")]
    if failed and mode == NOTIFY_MODE_SUCCESS:
        return None
    if not failed and mode == NOTIFY_MODE_FAILURE:
        return None

    time_text = str(results[0].get("time") or now_text())
    repeated = sum(1 for item in results if item.get("status") == STATUS_REPEATED)
    if failed:
        head = f"{total - len(failed)}/{total} 成功，{len(failed)} 项失败"
    else:
        head = f"{total}/{total} 全部成功"
        if repeated:
            head += f"（其中 {repeated} 项为今日已签到）"
    lines: List[str] = [f"{head}（{time_text} · {source}）", ""]
    if failed:
        lines.append("失败明细：")
        for item in failed[:_DETAIL_LIMIT]:
            detail = item.get("error") or item.get("reply") or "无回复"
            lines.append(
                f"- {_account_name(item)} → {item.get('bot')}：{_snippet(detail)}"
            )
        if len(failed) > _DETAIL_LIMIT:
            lines.append(f"…等 {len(failed)} 项")
    else:
        for item in list(results)[:_DETAIL_LIMIT]:
            status = str(item.get("status") or STATUS_SUCCESS)
            lines.append(
                f"- {_account_name(item)} → {item.get('bot')}：{status}{_status_note(item)}"
            )
        if total > _DETAIL_LIMIT:
            lines.append(f"…等 {total} 项")
    return "\n".join(lines)
