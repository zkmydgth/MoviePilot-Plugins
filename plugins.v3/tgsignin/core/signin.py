"""
签到执行：按钮式与命令式两条路径。

签到逻辑来自交接单里已在真实 Telegram 环境实测通过的实现（5/5 成功）：

- **按钮式**（emby 类 bot）：先发 ``/start`` 拉出菜单 → 在最近几条消息里找
  文字含关键词的 inline 按钮 → 点击 → 等待 → 读回最新回复；
- **命令式**（如示例 bot）：直接发命令 → 等待 → 读回 bot 回复。

**只有「本次发送之后收到的」消息才算数**：bot 离线时最近消息全是上一次的残留
（旧菜单、上次那句「🎉 签到成功」），按时间过滤才不会被误判成成功；本次一条新消息都没有
= bot 无任何返回 → 直接判失败，不硬说成功（2026-10-07 实测的假成功根因）。
已签到、签到成功、仅回菜单都算「签到动作已完成」（bot 侧重复签到通常不报错，详见交接单 §0.3④）。
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .config import (
    DEFAULT_FAILURE_KEYWORDS,
    DEFAULT_REPEATED_KEYWORDS,
    DEFAULT_SUCCESS_KEYWORDS,
    SIGN_TYPE_BUTTON,
    SIGN_TYPE_COMMAND,
    AccountConfig,
    BotTarget,
)
from .ai import (
    AI_STATE_ERROR,
    AI_STATE_JUDGED,
    AI_STATE_UNKNOWN,
    AI_VERDICT_FAILURE,
    AI_VERDICT_REPEATED,
    AI_VERDICT_SUCCESS,
    AiReview,
)
from .retry import signed_today
from .session import build_client, secure_session_files
from .store import load_state

__all__ = [
    "signin_one",
    "normalize_bot",
    "probe_target",
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

# AI 复核结论 → 签到状态（repeated 也必须认，否则「今天已签到」会被算成成功）
_AI_VERDICT_TO_STATUS = {
    AI_VERDICT_SUCCESS: STATUS_SUCCESS,
    AI_VERDICT_REPEATED: STATUS_REPEATED,
    AI_VERDICT_FAILURE: STATUS_FAILED,
}

# 并发签到：每路启动抖动与 FloodWait 退避上限
_JITTER_STEP_SECONDS = 0.05
_JITTER_MAX_SECONDS = 0.3
_FLOOD_WAIT_CAP_SECONDS = 600
_FLOOD_WAIT_RE = re.compile(
    r"(?:FloodWait|Flood|FLOOD_WAIT)[^0-9]{0,20}(\d{1,5})", re.IGNORECASE
)

# 读取 bot 消息的条数上限（够覆盖菜单与回复）
_MSG_SCAN_LIMIT = 5
# 按钮式：等待菜单出现的轮询间隔（秒）
_MENU_POLL_INTERVAL_SECONDS = 2.0
# 失败信息里最多列出的候选按钮文案个数
_BUTTON_HINT_LIMIT = 5
TZ = timezone(timedelta(hours=8))


def now_text() -> str:
    """
    返回当前北京时间字符串（日志与结果都用它）。

    :return str: ``YYYY-MM-DD HH:MM:SS``
    """

    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


def normalize_bot(name: str) -> str:
    """
    归一化 bot 用户名（去 ``@``、忽略大小写与空白），用于命令参数匹配。

    :param name: 原始 bot 用户名（可带或不带 ``@``）
    :return str: 归一化后的字符串
    """

    return str(name or "").strip().lstrip("@").lower()


def _split_candidates(text: str) -> List[str]:
    """
    把「按钮文字」配置拆成多个候选（支持 ``|`` / ``,`` / ``，`` / ``、`` 分隔）。

    :param text: 配置里的按钮文字或命令
    :return List[str]: 候选列表（去空、保序、去重）
    """

    parts = re.split(r"[|,，、]", str(text or ""))
    result: List[str] = []
    for part in parts:
        word = part.strip()
        if word and word not in result:
            result.append(word)
    return result


def _matches_candidates(text: str, candidates: Sequence[str]) -> bool:
    """
    按钮文案是否命中任一候选（忽略大小写）。

    :param text: 按钮文案
    :param candidates: 候选列表
    :return bool: 命中返回 True
    """

    lowered = str(text or "").lower()
    return any(str(word).lower() in lowered for word in candidates if word)


def _find_menu(
    messages: Sequence[Any],
    candidates: Sequence[str],
) -> Optional[Tuple[Any, Any]]:
    """
    在消息列表里找一条带候选按钮的消息（不分新旧，用于兜底）。

    :param messages: telethon 消息列表（新→旧）
    :param candidates: 按钮文案候选
    :return Optional[Tuple[Any, Any]]: ``(消息, 按钮)``；没找到返回 None
    """

    for message in messages:
        for row in (getattr(message, "buttons", None) or []):
            for button in row:
                text = str(getattr(button, "text", "") or "")
                if _matches_candidates(text, candidates):
                    return message, button
    return None


def _collect_button_texts(messages: Sequence[Any]) -> List[str]:
    """
    收集消息里出现过的按钮文案（用于失败时提示，帮用户改「按钮文字」）。

    :param messages: telethon 消息列表
    :return List[str]: 去重后的按钮文案（保序）
    """

    found: List[str] = []
    for message in messages:
        for row in (getattr(message, "buttons", None) or []):
            for button in row:
                text = str(getattr(button, "text", "") or "").strip()
                if text and text not in found:
                    found.append(text)
    return found


async def _click_and_read(
    client: Any,
    entity: Any,
    message: Any,
    button: Any,
    wait_seconds: int,
    sent_at: Optional[Any] = None,
) -> Tuple[bool, str, str, str]:
    """
    点击按钮并读回「点击之后」的新回复 / 弹窗。

    判据（2026-10-07 定案）：只认 ``sent_at``（本次 /start）之后的新消息，
    点旧菜单兜底时同样如此，避免把历史残留当成本次结果。

    :param client: 已连接的 TelegramClient
    :param entity: bot 实体
    :param message: 承载按钮的那条消息
    :param button: 待点击的按钮
    :param wait_seconds: 点击后等待秒数
    :param sent_at: 本次 /start 的发送时刻（新证据的时间下界）
    :return Tuple[bool, str, str, str]: ``(是否点到, 回复文本, 弹窗文本, 错误信息)``
    """

    # click() 对 inline 按钮返回 BotCallbackAnswer（含 .message/.alert）
    answer = await button.click()
    alert = str(getattr(answer, "message", "") or "")
    # 证据下界：菜单自身不算「点击后的新回复」，但也不能早于本次 /start
    menu_date = getattr(message, "date", None)
    cutoff = sent_at
    if menu_date is not None:
        edge = menu_date + timedelta(microseconds=1)
        cutoff = edge if cutoff is None or edge > cutoff else cutoff
    await asyncio.sleep(max(1, min(int(wait_seconds), 60)))
    latest = await client.get_messages(entity, limit=3)
    return True, _pick_reply(latest, cutoff), alert, ""


def _fresh_messages(
    messages: Sequence[Any],
    sent_at: Optional[Any] = None,
) -> List[Any]:
    """
    只保留「本次发送之后」收到的消息。

    :param messages: telethon 消息列表（新→旧）
    :param sent_at: 本次发送时刻（telethon 的 tz-aware datetime）；None 表示不做时间过滤
    :return List[Any]: 过滤后的消息列表；没有带时间的消息时返回空列表
    """

    if sent_at is None:
        return list(messages)
    fresh: List[Any] = []
    for message in messages:
        sent_date = getattr(message, "date", None)
        if sent_date is None:
            continue
        try:
            if sent_date >= sent_at:
                fresh.append(message)
        except TypeError:  # pragma: no cover - 时间类型异常时按「不新鲜」处理
            continue
    return fresh


def _pick_reply(messages: Sequence[Any], sent_at: Optional[Any] = None) -> str:
    """
    从消息列表里挑一条「本次」bot 的文本回复。

    :param messages: telethon 消息列表（新→旧）
    :param sent_at: 本次发送时刻；None 表示不做时间过滤
    :return str: 回复文本；没有则返回空串
    """

    latest = _fresh_messages(messages, sent_at)
    for message in latest:
        if not getattr(message, "out", False) and getattr(message, "text", None):
            return str(message.text)
    if latest:
        return str(getattr(latest[0], "text", "") or "")
    return ""


async def _click_button(
    client: Any,
    entity: Any,
    keyword: str,
    wait_seconds: int,
    sent_at: Optional[Any] = None,
) -> Tuple[bool, str, str, str]:
    """
    在最近消息里点文字含关键词的按钮，并读回回复。

    :param client: 已连接的 TelegramClient
    :param entity: bot 实体
    :param keyword: 按钮关键词（如「签到」）
    :param wait_seconds: 点击后等待秒数
    :param sent_at: 本次 /start 的发送时刻，用于只认本次菜单（None 表示不过滤）
    :return Tuple[bool, str, str, str]: ``(是否点到, 回复文本, 弹窗文本, 错误信息)``；
        弹窗文本来自 Telegram 的 callback 应答（bot 用 ``answerCallbackQuery``
        弹的那句提示，例如「您今天已经签到过了」），拿不到时为空串
    """

    candidates = _split_candidates(keyword) or [str(keyword or "")]
    budget = max(_MENU_POLL_INTERVAL_SECONDS * 2, float(max(3, int(wait_seconds))))
    # 轮询次数按预算折算（用次数而非墙钟，测试里 sleep 是桩也能毫秒级跑完）
    polls = max(1, int(budget / _MENU_POLL_INTERVAL_SECONDS))
    observed: List[str] = []
    fallback: Optional[Tuple[Any, Any]] = None
    # 轮询等菜单（2026-10-11 修）：bot 回菜单慢、或菜单按钮文案与配置不同，
    # 单次 sleep 后只扫一遍必然假失败（@example_bot_a 的历史高频失败即此）
    for _ in range(polls):
        messages = await client.get_messages(entity, limit=_MSG_SCAN_LIMIT)
        fresh = _fresh_messages(messages, sent_at)
        for message in fresh:
            for row in (getattr(message, "buttons", None) or []):
                for button in row:
                    text = str(getattr(button, "text", "") or "")
                    if text and text not in observed:
                        observed.append(text)
        menu = _find_menu(fresh, candidates)
        if menu is not None:
            return await _click_and_read(
                client, entity, menu[0], menu[1], wait_seconds, sent_at
            )
        if fallback is None:
            # 没等到新菜单：留一条「最近一条含候选按钮的旧消息」兜底，
            # 点击后仍然只认本次之后的新证据（不会因此误判成功）
            fallback = _find_menu(messages, candidates)
        await asyncio.sleep(_MENU_POLL_INTERVAL_SECONDS)
    if fallback is not None:
        return await _click_and_read(
            client, entity, fallback[0], fallback[1], wait_seconds, sent_at
        )
    if not observed:
        observed = _collect_button_texts(
            await client.get_messages(entity, limit=_MSG_SCAN_LIMIT)
        )
    hint = "、".join(observed[:_BUTTON_HINT_LIMIT]) or "（最近消息里没有任何按钮）"
    return (
        False,
        "",
        "",
        f"本次发送后 bot 没有新消息/新按钮（最近 {_MSG_SCAN_LIMIT} 条里没找到含"
        f"「{keyword}」的按钮；实际看到的按钮：{hint}）",
    )


def _matches_keywords(text: str, keywords: Sequence[str]) -> bool:
    """
    文本是否命中关键词表（忽略大小写）。

    实测口径：emby 类 bot 的弹窗文案是「您今天已经签到过了」，不含「已签到」三字，
    所以默认词表覆盖多种说法，并允许用户在配置里追加（2026-10-07 用户定案）。

    :param text: bot 回复或弹窗文本
    :param keywords: 关键词列表
    :return bool: 命中任一关键词返回 True
    """

    lowered = str(text or "").lower()
    return any(str(word).lower() in lowered for word in keywords if word)


def _coerce_review(value: Any) -> AiReview:
    """
    把 AI 复核协程的返回值统一成 :class:`AiReview`。

    兼容两种写法：新版协程返回 ``AiReview``；自定义/旧版协程可能只返回结论字符串。

    :param value: 复核协程返回值（AiReview / 结论字符串 / None）
    :return AiReview: 统一后的复核记录
    """

    if isinstance(value, AiReview):
        return value
    if isinstance(value, str) and value in _AI_VERDICT_TO_STATUS:
        return AiReview(state=AI_STATE_JUDGED, verdict=value)
    return AiReview(state=AI_STATE_UNKNOWN, message="AI 未给出结论")


def classify_result(
    reply: str,
    ok: bool,
    method: str = "",
    alert: str = "",
    already_signed_today: bool = False,
    success_keywords: Optional[Sequence[str]] = None,
    repeated_keywords: Optional[Sequence[str]] = None,
    failure_keywords: Optional[Sequence[str]] = None,
) -> str:
    """
    按 bot 回复内容给签到结果分档。

    判据来自交接单实测：emby 类 bot 真签到成功会回「🎉 签到成功 | N 子弹…」，
    重复签到只回主菜单并弹一句提示（callback 应答）；示例 bot 重复签到回
    「✅ 今日已签到，明天再来。」。

    :param reply: bot 回复文本
    :param ok: 本次是否判定为成功（有回复即算动作完成）
    :param method: 签到方式描述（用于区分「点了按钮但只回菜单」）
    :param alert: 点击按钮时 Telegram 返回的弹窗提示（callback 应答文本）
    :param already_signed_today: 今天此前是否已经签到成功过（按钮式只回菜单时用于分档）
    :param success_keywords: 自定义「签到成功」关键词（None 用内置默认）
    :param repeated_keywords: 自定义「已签到」关键词（None 用内置默认）
    :param failure_keywords: 自定义「签到失败」关键词（None 用内置默认）
    :return str: STATUS_SUCCESS / STATUS_REPEATED / STATUS_UNCONFIRMED / STATUS_FAILED
    """

    if not ok:
        return STATUS_FAILED
    success_words = tuple(success_keywords or DEFAULT_SUCCESS_KEYWORDS)
    repeated_words = tuple(repeated_keywords or DEFAULT_REPEATED_KEYWORDS)
    failure_words = tuple(failure_keywords or DEFAULT_FAILURE_KEYWORDS)
    alert_text = str(alert or "")
    text = str(reply or "")
    if not text.strip() and not alert_text.strip():
        # 本次没有任何返回内容（既无回复也无弹窗）：不能当成成功
        return STATUS_FAILED
    if alert_text and _matches_keywords(alert_text, repeated_words):
        return STATUS_REPEATED
    if _matches_keywords(text, success_words) or _matches_keywords(
        alert_text, success_words
    ):
        return STATUS_SUCCESS
    if _matches_keywords(text, repeated_words) or _matches_keywords(
        alert_text, repeated_words
    ):
        return STATUS_REPEATED
    if _matches_keywords(text, failure_words) or _matches_keywords(
        alert_text, failure_words
    ):
        # 明确失败文案（如「签到服务暂不可用，请稍后重试。」）：
        # 判失败 → signin_one 会把 ok 置 False → 进入失败重试窗口（2026-10-08 用户定案）
        return STATUS_FAILED
    if "按钮" in str(method or ""):
        # 按钮点到了、bot 只回菜单（没有任何签到结果）：
        # 仅当「今天此前已经签到成功过」才算「今日已签到、不再重复发放」；
        # 否则说明 bot 根本没给出签到结果 → 判失败（2026-10-07 用户定案）
        if already_signed_today:
            return STATUS_REPEATED
        return STATUS_FAILED
    return STATUS_UNCONFIRMED


async def signin_one(
    client: Any,
    target: BotTarget,
    already_signed_today: bool = False,
    success_keywords: Optional[Sequence[str]] = None,
    repeated_keywords: Optional[Sequence[str]] = None,
    failure_keywords: Optional[Sequence[str]] = None,
    ai_judge: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    对单个 bot 执行一次签到，并补上结果状态分类。

    :param client: 已登录的 TelegramClient
    :param target: 签到目标配置
    :param already_signed_today: 今天此前是否已经签到成功过
    :param success_keywords: 自定义「签到成功」关键词（None 用内置默认）
    :param repeated_keywords: 自定义「已签到」关键词（None 用内置默认）
    :param failure_keywords: 自定义「签到失败」关键词（None 用内置默认）
    :param ai_judge: 可选的 AI 复核协程（仅「未确认」时调用；默认 None = 不启用）
    :return Dict[str, Any]: 结果字典（含 status）
    """

    result = await _signin_one_impl(client, target)
    status = classify_result(
        str(result.get("reply") or ""),
        bool(result.get("ok")),
        str(result.get("method") or ""),
        str(result.get("alert") or ""),
        already_signed_today=already_signed_today,
        success_keywords=success_keywords,
        repeated_keywords=repeated_keywords,
        failure_keywords=failure_keywords,
    )
    if status == STATUS_UNCONFIRMED and ai_judge is not None:
        # 「未确认」时才用 MP 内置 AI 复核（默认关闭）：
        # 未开启 / 未配置 / 超时 / 模型判不出 —— 一律保持「未确认」，不影响其它判定。
        # 复核记录（三态 + 模型原文 + 归纳词）一并写进结果，便于回溯（2026-10-08）。
        try:
            review = _coerce_review(await ai_judge(result))
        except Exception as error:  # pylint: disable=broad-except
            review = AiReview(
                state=AI_STATE_ERROR, message=f"{type(error).__name__}: {error}"
            )
        result.update(review.as_result_fields())
        if review.verdict in _AI_VERDICT_TO_STATUS:
            status = _AI_VERDICT_TO_STATUS[review.verdict]
    result["status"] = status
    if status == STATUS_FAILED and result.get("ok"):
        # 失败不能计入成功：失败会进失败重试窗口
        result["ok"] = False
        if not result.get("error"):
            if result.get("ai_verdict") == AI_VERDICT_FAILURE:
                result["error"] = (
                    "AI 复核判定本次签到失败："
                    + str(result.get("reply") or result.get("alert") or "无回复")[:120]
                )
            elif "按钮" in str(result.get("method") or ""):
                result["error"] = (
                    "bot 只回了菜单/没有给出签到结果，且今天此前没有签到成功记录"
                )
            else:
                result["error"] = "bot 回复被判定为签到失败：" + str(
                    result.get("reply") or result.get("alert") or "无回复"
                )[:120]
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
            start_message = await client.send_message(entity, "/start")
            sent_at = getattr(start_message, "date", None)
            # 等菜单交给 _click_button 内部轮询（不再单次 sleep 后只扫一遍）
            clicked, reply, alert, error = await _click_button(
                client, entity, target.action_text, target.wait_seconds, sent_at
            )
            # 只有「点到按钮 + bot 有返回（回复或弹窗）」才算动作完成；
            # 点到旧按钮而 bot 零返回 = bot 可能离线，必须如实判失败
            has_evidence = bool(str(reply or "").strip() or str(alert or "").strip())
            result["ok"] = bool(clicked and has_evidence)
            result["reply"] = reply
            result["alert"] = alert
            result["error"] = error or (
                ""
                if result["ok"]
                else "已点击按钮但 bot 无任何返回（bot 可能离线或已停止服务）"
            )
            return result

        if target.sign_type == SIGN_TYPE_COMMAND:
            command_message = await client.send_message(
                entity, target.action_text or "/checkin"
            )
            sent_at = getattr(command_message, "date", None)
            await asyncio.sleep(target.wait_seconds)
            latest = await client.get_messages(entity, limit=3)
            reply = _pick_reply(latest, sent_at)
            result["ok"] = bool(reply)
            result["reply"] = reply
            result["error"] = "" if reply else "发送后没等到 bot 回复（bot 可能离线）"
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
    success_keywords: Optional[Sequence[str]] = None,
    repeated_keywords: Optional[Sequence[str]] = None,
    failure_keywords: Optional[Sequence[str]] = None,
    ai_judge: Optional[Any] = None,
    concurrency: int = 1,
) -> Tuple[List[Dict[str, Any]], str]:
    """
    对单个账号执行它名下所有启用的签到目标。

    :param account: 账号配置
    :param targets: 该账号的目标列表
    :param data_dir: 插件数据目录
    :param proxy: 代理元组，None 表示直连
    :param success_keywords: 自定义「签到成功」关键词（None 用内置默认）
    :param repeated_keywords: 自定义「已签到」关键词（None 用内置默认）
    :param failure_keywords: 自定义「签到失败」关键词（None 用内置默认）
    :param ai_judge: 可选的 AI 复核协程（仅「未确认」时调用）
    :param concurrency: 并发上限（1 = 串行，与 1.0.x 行为一致；2-4 = 同账号内「每 bot 一路」并发）
    :return Tuple[List[Dict[str, Any]], str]: ``(结果列表, 账号级错误)``；
        账号级错误非空时结果列表为空
    """

    try:
        client = build_client(data_dir, account, proxy)
    except RuntimeError as error:
        return [], str(error)

    # 整轮超时预算：每目标（等待秒数 + 45 秒），下限 120 秒（2026-10-11 加，
    # 防止连接卡住长期占线程、与下一次定时重叠）
    per_target = max(15, max((target.wait_seconds for target in targets), default=15)) + 45
    budget = max(120.0, len(targets) * per_target)

    async def _run_account() -> Tuple[List[Dict[str, Any]], str]:
        """
        连接账号并把该账号下的目标跑完（串行或按并发度）。

        :return Tuple[List[Dict[str, Any]], str]: ``(结果列表, 账号级错误)``
        """

        await client.connect()
        # session 里是 Telegram 授权密钥：连上后立刻收紧到 0600（2026-10-11 加固）
        secure_session_files(data_dir, account.key)
        if not await client.is_user_authorized():
            return [], f"账号 {account.key} 未登录或 session 已失效，请重新登录"
        if max(1, int(concurrency)) <= 1:
            # 串行：与 1.0.x 一致的行为（默认路径），同样接入 FloodWait 退避重试
            results: List[Dict[str, Any]] = []
            for target in targets:
                results.append(
                    await _signin_target_with_flood_retry(
                        client,
                        target,
                        data_dir,
                        account,
                        success_keywords,
                        repeated_keywords,
                        failure_keywords,
                        ai_judge,
                    )
                )
            return results, ""
        # 并发：同一账号内「每个 bot 一路」；账号之间仍串行（避免同 IP 多账号并发特征）
        results = await _run_targets_concurrently(
            client,
            targets,
            data_dir,
            account,
            concurrency,
            success_keywords=success_keywords,
            repeated_keywords=repeated_keywords,
            failure_keywords=failure_keywords,
            ai_judge=ai_judge,
        )
        return results, ""

    try:
        return await asyncio.wait_for(_run_account(), timeout=budget)
    except asyncio.TimeoutError:
        return [], f"账号 {account.key} 执行超时（>{int(budget)}s），已中止本轮"
    except Exception as error:  # pylint: disable=broad-except
        return [], f"账号 {account.key} 执行异常：{type(error).__name__}: {error}"
    finally:
        try:
            await client.disconnect()
        except Exception:  # pylint: disable=broad-except
            pass


async def _signin_target(
    client: Any,
    target: BotTarget,
    data_dir: Path,
    account: AccountConfig,
    success_keywords: Optional[Sequence[str]],
    repeated_keywords: Optional[Sequence[str]],
    failure_keywords: Optional[Sequence[str]],
    ai_judge: Optional[Any],
) -> Dict[str, Any]:
    """
    跑单个签到目标并补上账号显示名（串行与并发路径共用）。

    :param client: 已登录的 TelegramClient
    :param target: 签到目标
    :param data_dir: 插件数据目录（用于读「今天是否已成功过」）
    :param account: 目标所属账号
    :param success_keywords: 自定义「签到成功」关键词
    :param repeated_keywords: 自定义「已签到」关键词
    :param failure_keywords: 自定义「签到失败」关键词
    :param ai_judge: 可选的 AI 复核协程
    :return Dict[str, Any]: 单条签到结果
    """

    item = await signin_one(
        client,
        target,
        already_signed_today=signed_today(load_state(data_dir), target),
        success_keywords=success_keywords,
        repeated_keywords=repeated_keywords,
        failure_keywords=failure_keywords,
        ai_judge=ai_judge,
    )
    # 带上显示名：通知正文里显示「账号1(acc1)」比纯标识好认
    item["account_label"] = account.display()
    return item


def _jitter_seconds(index: int) -> float:
    """
    返回第 index 路的启动抖动秒数（用于错开同秒并发特征）。

    :param index: 该目标在账号内的序号（从 0 开始）
    :return float: 抖动秒数，上限为 ``_JITTER_MAX_SECONDS``
    """

    return min(_JITTER_MAX_SECONDS, _JITTER_STEP_SECONDS * max(0, int(index)))


def _flood_wait_seconds(text: str) -> Optional[int]:
    """
    从错误文本里提取 Telegram FloodWait 要求的等待秒数。

    :param text: 结果里的 error 文本
    :return Optional[int]: 需要等待的秒数；没命中返回 None
    """

    match = _FLOOD_WAIT_RE.search(str(text or ""))
    if not match:
        return None
    try:
        return max(1, int(match.group(1)))
    except (TypeError, ValueError):
        return None


async def _signin_target_with_flood_retry(
    client: Any,
    target: BotTarget,
    data_dir: Path,
    account: AccountConfig,
    success_keywords: Optional[Sequence[str]] = None,
    repeated_keywords: Optional[Sequence[str]] = None,
    failure_keywords: Optional[Sequence[str]] = None,
    ai_judge: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    跑一次目标；命中 Telegram FloodWait 时按提示等待后**重试一次**。

    退避期间不占并发信号量（2026-10-11 修：此前在信号量内 sleep 会把整路并发一起堵住），
    上限提高到 600 秒（原 60 秒常小于 Telegram 实际要求，到点重试仍被限流）；
    串行路径（并发=1）也走这里，不再完全没有退避。

    :param client: 已登录的 TelegramClient
    :param target: 签到目标
    :param data_dir: 插件数据目录
    :param account: 账号配置
    :param success_keywords: 自定义「签到成功」关键词
    :param repeated_keywords: 自定义「已签到」关键词
    :param failure_keywords: 自定义「签到失败」关键词
    :param ai_judge: 可选的 AI 复核协程
    :return Dict[str, Any]: 单条签到结果
    """

    item = await _signin_target(
        client,
        target,
        data_dir,
        account,
        success_keywords,
        repeated_keywords,
        failure_keywords,
        ai_judge,
    )
    wait_seconds = _flood_wait_seconds(str(item.get("error") or ""))
    if wait_seconds is None:
        return item
    await asyncio.sleep(min(wait_seconds, _FLOOD_WAIT_CAP_SECONDS))
    return await _signin_target(
        client,
        target,
        data_dir,
        account,
        success_keywords,
        repeated_keywords,
        failure_keywords,
        ai_judge,
    )


async def _run_targets_concurrently(
    client: Any,
    targets: Sequence[BotTarget],
    data_dir: Path,
    account: AccountConfig,
    concurrency: int,
    *,
    success_keywords: Optional[Sequence[str]] = None,
    repeated_keywords: Optional[Sequence[str]] = None,
    failure_keywords: Optional[Sequence[str]] = None,
    ai_judge: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """
    并发跑同一账号下的多个目标（每 bot 一路）。

    - 并发上限由 ``concurrency`` 对应的信号量控制；
    - 每路启动加 ≤300ms 抖动，降低同秒并发特征；
    - 命中 Telegram FloodWait 时按提示退避（上限 60 秒）并**重试一次**，不影响其它路；
    - 结果按**原始目标顺序**返回，保证通知与状态文件内容稳定。

    :param client: 已登录的 TelegramClient（同一账号共用）
    :param targets: 该账号下启用的目标
    :param data_dir: 插件数据目录
    :param account: 账号配置
    :param concurrency: 并发上限
    :param success_keywords: 自定义「签到成功」关键词
    :param repeated_keywords: 自定义「已签到」关键词
    :param failure_keywords: 自定义「签到失败」关键词
    :param ai_judge: 可选的 AI 复核协程
    :return List[Dict[str, Any]]: 结果列表（与 targets 同序）
    """

    semaphore = asyncio.Semaphore(max(1, int(concurrency)))

    async def _one(target: BotTarget, index: int) -> Dict[str, Any]:
        """跑一路：限流 + 抖动，命中 FloodWait 时退避重试一次。"""

        async with semaphore:
            await asyncio.sleep(_jitter_seconds(index))
            return await _signin_target_with_flood_retry(
                client,
                target,
                data_dir,
                account,
                success_keywords,
                repeated_keywords,
                failure_keywords,
                ai_judge,
            )

    gathered = await asyncio.gather(
        *(_one(target, index) for index, target in enumerate(targets))
    )
    return list(gathered)


async def probe_target(
    target: BotTarget,
    data_dir: Path,
    account: AccountConfig,
    proxy: Optional[Tuple[str, str, int]] = None,
    wait_seconds: int = 15,
) -> Dict[str, Any]:
    """
    测试单个目标：连上去发一次 ``/start``（或命令），把**实际看到的按钮文案**列出来。

    只回证据、不判成败，用于「按钮文字」配置的自助修正（2026-10-11 加）。

    :param target: 签到目标
    :param data_dir: 插件数据目录
    :param account: 账号配置
    :param proxy: 代理元组，None 表示直连
    :param wait_seconds: 等待秒数
    :return Dict[str, Any]: ``{ok, message, buttons}``
    """

    try:
        client = build_client(data_dir, account, proxy)
    except RuntimeError as error:
        return {"ok": False, "message": str(error), "buttons": []}
    try:
        await asyncio.wait_for(client.connect(), timeout=25)
        secure_session_files(data_dir, account.key)
        if not await client.is_user_authorized():
            return {
                "ok": False,
                "message": f"账号 {account.key} 未登录或 session 已失效，请重新登录",
                "buttons": [],
            }
        entity = await client.get_entity(target.bot_username)
        if target.sign_type == SIGN_TYPE_BUTTON:
            await client.send_message(entity, "/start")
        else:
            await client.send_message(entity, target.action_text or "/checkin")
        await asyncio.sleep(max(3, min(int(wait_seconds), 30)))
        messages = await client.get_messages(entity, limit=_MSG_SCAN_LIMIT)
        buttons = _collect_button_texts(messages)
        reply = _pick_reply(messages, None)
        return {
            "ok": True,
            "buttons": buttons,
            "message": (
                f"{target.account_key} → {target.bot_username}："
                f"看到的按钮 {('、'.join(buttons) if buttons else '（无）')}；"
                f"最近回复：{str(reply)[:80] or '（无）'}"
            ),
        }
    except Exception as error:  # pylint: disable=broad-except
        return {
            "ok": False,
            "message": f"测试失败：{type(error).__name__}: {error}",
            "buttons": [],
        }
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
    only_targets: Optional[Sequence[BotTarget]] = None,
    success_keywords: Optional[Sequence[str]] = None,
    repeated_keywords: Optional[Sequence[str]] = None,
    failure_keywords: Optional[Sequence[str]] = None,
    ai_judge: Optional[Any] = None,
    concurrency: int = 1,
) -> List[Dict[str, Any]]:
    """
    按账号维度依次签到（同一时刻只连一个账号，避免并发触发风控）。

    :param accounts: 全部账号
    :param targets: 全部签到目标
    :param data_dir: 插件数据目录
    :param proxy: 代理元组，None 表示直连
    :param only_account: 只跑该账号标识（None 表示全部）
    :param only_bot: 只跑该 bot 用户名（None 表示全部）
    :param only_targets: 只跑给定的目标集合（失败重试用），None 表示按账号/bot 过滤
    :param success_keywords: 自定义「签到成功」关键词（None 用内置默认）
    :param repeated_keywords: 自定义「已签到」关键词（None 用内置默认）
    :param failure_keywords: 自定义「签到失败」关键词（None 用内置默认）
    :param ai_judge: 可选的 AI 复核协程（仅「未确认」时调用）
    :param concurrency: 并发上限（1 = 串行；2-4 = 同账号内「每 bot 一路」并发）
    :return List[Dict[str, Any]]: 扁平的结果列表
    """

    allowed = (
        {f"{item.account_key}|{item.bot_username}" for item in only_targets}
        if only_targets is not None
        else None
    )
    results: List[Dict[str, Any]] = []
    for account in accounts:
        if only_account and account.key != only_account:
            continue
        account_targets = [
            target
            for target in targets
            if target.account_key == account.key
            and target.enabled
            and (
                not only_bot
                or normalize_bot(target.bot_username) == normalize_bot(only_bot)
            )
            and (
                allowed is None
                or f"{target.account_key}|{target.bot_username}" in allowed
            )
        ]
        if not account_targets:
            continue
        account_results, account_error = await run_account(
            account,
            account_targets,
            data_dir,
            proxy,
            success_keywords=success_keywords,
            repeated_keywords=repeated_keywords,
            failure_keywords=failure_keywords,
            ai_judge=ai_judge,
            concurrency=concurrency,
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
    unconfirmed = sum(
        1 for item in results if item.get("status") == STATUS_UNCONFIRMED
    )
    # 「未确认」保持 ok=True（不算失败）但不能并进成功数（2026-10-11 修）
    ok = sum(
        1
        for item in results
        if item.get("ok") and item.get("status") != STATUS_UNCONFIRMED
    )
    if unconfirmed:
        return f"{ok}/{len(results)} 成功，{unconfirmed} 项未确认"
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
        if item.get("ai_verdict") == AI_VERDICT_SUCCESS:
            # 回复里没有成功词，是 AI 复核判定的成功：通知里标注，便于回溯
            return (
                f"（AI 复核判定成功）｜{_snippet(reply)}"
                if reply
                else "（AI 复核判定成功）"
            )
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
    unconfirmed = sum(
        1 for item in results if item.get("status") == STATUS_UNCONFIRMED
    )
    ok_count = total - len(failed) - unconfirmed
    if failed:
        head = f"{ok_count}/{total} 成功，{len(failed)} 项失败"
        if unconfirmed:
            head += f"，{unconfirmed} 项未确认"
    elif unconfirmed:
        # 未确认既非成功也非失败：别写成「全部成功」（2026-10-11 修）
        head = f"{ok_count}/{total} 成功，{unconfirmed} 项未确认"
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
