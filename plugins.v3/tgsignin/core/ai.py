"""
用 MoviePilot 内置 AI 复核「未确认」的签到结果，并可顺带归纳关键词（默认关闭）。

只在结果落到「未确认」（bot 有回复，但既不含成功词也不含失败词/已签到词）且用户**显式开启**时调用：

- 未开启、未配置 MP 智能助手、超时、模型输出无法解析 —— 一律**不改变**原判（保持「未确认」）；
- 每次调用都产出**可观测记录**（``AiReview.state``）：判定出结论 / 无法判定 / 未配置 / 超时 / 异常，
  并保留模型原始回复片段（截断）供回溯；日志里各情形措辞**分开**；
- 判定为失败时连同 ``ok=False`` 一起回流，从而进入「失败重试」窗口；
- 「AI 自动归纳关键词」开启时，**复用同一次调用**要求模型给出可复用的短语（逐字取自原文），
  由插件按档位补进词表（见 ``core/autofill.py``，只增不删、写入前查重）。

任何异常都被吞掉并记日志，**绝不让 AI 成为签到链路的硬依赖**。密钥只从 MP 设置读取，插件不保存、不打印。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .autofill import KEYWORD_BLACKLIST, is_acceptable_keyword, normalize_keyword

__all__ = [
    "AiSigninJudge",
    "AiReview",
    "AI_VERDICT_SUCCESS",
    "AI_VERDICT_REPEATED",
    "AI_VERDICT_FAILURE",
    "AI_STATE_NOT_CALLED",
    "AI_STATE_JUDGED",
    "AI_STATE_UNKNOWN",
    "AI_STATE_UNCONFIGURED",
    "AI_STATE_TIMEOUT",
    "AI_STATE_ERROR",
    "AI_DEFAULT_TIMEOUT_SECONDS",
    "AI_RAW_LIMIT",
    "AI_KEYWORD_LIMIT",
]

# AI 复核的三种结论（与 signin 的 STATUS_* 解耦，避免循环导入）
AI_VERDICT_SUCCESS = "success"
AI_VERDICT_REPEATED = "repeated"
AI_VERDICT_FAILURE = "failure"

# 复核过程状态（写进结果，便于区分「模型判不出」与「链路出错」）
AI_STATE_NOT_CALLED = "not_called"
AI_STATE_JUDGED = "judged"
AI_STATE_UNKNOWN = "unknown"
AI_STATE_UNCONFIGURED = "unconfigured"
AI_STATE_TIMEOUT = "timeout"
AI_STATE_ERROR = "error"

# 默认超时（秒）：AI 只是复核，不能拖垮签到主流程
AI_DEFAULT_TIMEOUT_SECONDS = 30.0
# 模型原始回复保留长度（写进结果与日志）
AI_RAW_LIMIT = 200
# 单次归纳最多采纳的候选词条数
AI_KEYWORD_LIMIT = 3

# 系统提示：要求只输出 JSON，便于稳定解析（含关键词归纳时仍是同一个 JSON）
_SYSTEM_PROMPT = (
    "你在判断一次 Telegram 签到（check-in / 打卡）是否成功，并可顺带归纳可复用的关键词。"
    "输入会给出 bot 回复文本、按钮弹窗文本与签到方式。"
    "判定口径：回复表明「签到已完成 / 打卡成功 / 领取成功」→ success；"
    "「今天已经签到过 / 已领取 / 重复签到」→ repeated；"
    "「签到失败 / 服务不可用 / 稍后重试 / 异常」→ failure；"
    "无法判断（例如只回菜单或无关文案）→ unknown。"
    '只输出 JSON，不要解释：{"result":"success|repeated|failure|unknown","keywords":["短语1","短语2"]}。'
    "keywords 只能从**原文里逐字摘取** 2-10 字的短语（不要数字、日期、用户名，不要自创），最多 3 个；"
    "若无法判断则 keywords 为空数组。"
)

_FENCE = re.compile(r"^```(?:json)?\s*([\s\S]*?)\s*```$", re.IGNORECASE)


@dataclass
class AiReview:
    """一次 AI 复核的结果（含可观测记录）。"""

    state: str = AI_STATE_NOT_CALLED
    verdict: Optional[str] = None
    raw: str = ""
    keywords: List[str] = field(default_factory=list)
    message: str = ""

    def as_result_fields(self) -> Dict[str, Any]:
        """
        转成写进签到结果的字段（供日志、详情页与回溯使用）。

        :return Dict[str, Any]: ``ai_state`` / ``ai_verdict`` / ``ai_raw`` / ``ai_keywords`` / ``ai_message``
        """

        data: Dict[str, Any] = {"ai_state": self.state}
        if self.verdict:
            data["ai_verdict"] = self.verdict
        if self.raw:
            data["ai_raw"] = self.raw
        if self.keywords:
            data["ai_keywords"] = list(self.keywords)
        if self.message:
            data["ai_message"] = self.message
        return data

    def label(self) -> str:
        """
        返回给用户看的一行短说明（详情页与通知用）。

        :return str: 中文短句；未调用时返回空串
        """

        if self.state == AI_STATE_NOT_CALLED:
            return ""
        if self.state == AI_STATE_JUDGED:
            names = {
                AI_VERDICT_SUCCESS: "成功",
                AI_VERDICT_REPEATED: "已签到",
                AI_VERDICT_FAILURE: "失败",
            }
            base = f"AI 复核判定{names.get(self.verdict or '', '')}"
            return f"{base}｜归纳词={'/'.join(self.keywords)}" if self.keywords else base
        if self.state == AI_STATE_UNKNOWN:
            return "AI 无法判定"
        if self.state == AI_STATE_UNCONFIGURED:
            return "AI 未配置，未复核"
        if self.state == AI_STATE_TIMEOUT:
            return "AI 复核超时"
        if self.state == AI_STATE_ERROR:
            return f"AI 复核异常：{self.message[:60]}"
        return ""


class AiSigninJudge:
    """调用 MoviePilot 内置 AI 复核签到结果，并按需归纳关键词。"""

    def __init__(
        self,
        enabled: bool = False,
        timeout: float = AI_DEFAULT_TIMEOUT_SECONDS,
        logger: Any = None,
    ) -> None:
        """
        初始化复核器。

        :param enabled: 是否启用（默认关闭；关闭时 judge() 直接返回 not_called）
        :param timeout: 单次复核的超时秒数
        :param logger: 可选的日志器（Info/Warning 级别）
        """

        self.enabled = bool(enabled)
        self.timeout = max(1.0, float(timeout or AI_DEFAULT_TIMEOUT_SECONDS))
        self.logger = logger

    def _log(self, level: str, message: str, *args: Any) -> None:
        """
        记录日志（无日志器或方法缺失时静默）。

        :param level: ``info`` 或 ``warning``
        :param message: 日志模板
        :param args: 模板参数
        """

        if self.logger is None:
            return
        method = getattr(self.logger, level, None)
        if not callable(method):
            return
        try:
            method(message, *args)
        except Exception:  # pylint: disable=broad-except
            pass

    @staticmethod
    def _settings_dict() -> Tuple[Optional[Dict[str, Any]], str]:
        """
        读取 MoviePilot 内置 AI 配置（不落盘、不回显密钥）。

        :return Tuple[Optional[Dict[str, Any]], str]: ``(配置, 诊断信息)``；配置为 None 表示不可用
        """

        try:
            from app.sdk.config import settings  # pylint: disable=import-outside-toplevel
        except Exception as error:  # pylint: disable=broad-except
            return None, f"MoviePilot 智能助手不可用：{type(error).__name__}: {error}"

        config = {
            "provider": getattr(settings, "LLM_PROVIDER", None) or "openai",
            "model": getattr(settings, "LLM_MODEL", None),
            "api_key": getattr(settings, "LLM_" + "API" + "_KEY", None),
            "base_url": getattr(settings, "LLM_BASE_URL", None),
            "base_url_preset": getattr(settings, "LLM_BASE_URL_PRESET", None),
            "user_agent": getattr(settings, "LLM_USER_AGENT", None),
            "use_proxy": getattr(settings, "LLM_USE_PROXY", True),
            "thinking_level": getattr(settings, "LLM_THINKING_LEVEL", None),
        }
        if not str(config.get("api_key") or "").strip():
            return None, "未配置 MoviePilot 智能助手 API Key"
        if not str(config.get("model") or "").strip():
            return None, "未配置 MoviePilot 智能助手模型"
        return config, ""

    def available(self) -> Dict[str, Any]:
        """
        返回复核器的可用性摘要（供详情页展示）。

        :return Dict[str, Any]: 含 ``enabled`` / ``available`` / ``provider`` / ``model`` / ``message``
        """

        if not self.enabled:
            return {
                "enabled": False,
                "available": False,
                "message": "未开启 AI 复核/归纳",
            }
        config, message = self._settings_dict()
        return {
            "enabled": True,
            "available": bool(config),
            "provider": (config or {}).get("provider"),
            "model": (config or {}).get("model"),
            "message": message or "已读取 MoviePilot 智能助手配置",
        }

    @staticmethod
    def _extract_text(content: Any) -> str:
        """
        提取模型回复文本，优先复用 MP 的文本提取接口。

        :param content: 模型回复对象或内容
        :return str: 纯文本
        """

        try:
            from app.agent.llm import (  # pylint: disable=import-outside-toplevel
                LLMHelper,
            )

            extractor = getattr(LLMHelper, "extract_text_content", None)
            if callable(extractor):
                return str(extractor(content) or "").strip()
        except Exception:  # pylint: disable=broad-except
            pass
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            return "".join(
                str(item.get("text", "")) if isinstance(item, dict) else str(item)
                for item in content
            ).strip()
        if isinstance(content, dict):
            return str(content.get("text") or content.get("content") or "").strip()
        return str(content or "").strip()

    @staticmethod
    def _json_payload(text: str) -> Dict[str, Any]:
        """
        从模型输出里取出 JSON 对象（容忍代码块包裹与前后杂讯）。

        :param text: 模型输出文本
        :return Dict[str, Any]: 解析出的对象；无法解析时返回空字典
        """

        raw = str(text or "").strip()
        if not raw:
            return {}
        match = _FENCE.match(raw)
        if match:
            raw = match.group(1).strip()
        if not raw.startswith("{"):
            start, end = raw.find("{"), raw.rfind("}")
            if start >= 0 and end > start:
                raw = raw[start : end + 1]
        if not raw.startswith("{"):
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    @classmethod
    def parse_result(cls, text: str) -> Optional[str]:
        """
        解析模型给出的档位结论。

        :param text: 模型输出文本（JSON、代码块包裹或纯文本均可）
        :return Optional[str]: 三种结论之一；无法判定时返回 None
        """

        data = cls._json_payload(text)
        value = str(data.get("result") or "").strip().lower()
        if value in {"success", "ok", "true", "成功", "签到成功"}:
            return AI_VERDICT_SUCCESS
        if value in {"repeated", "repeat", "already", "已签到", "重复", "重复签到"}:
            return AI_VERDICT_REPEATED
        if value in {"failure", "fail", "false", "失败"}:
            return AI_VERDICT_FAILURE
        if data or value:
            # 明确给了 JSON/结论：除上面能识别的外（含 unknown）都算无法判定
            return None
        raw = str(text or "")
        lowered = raw.lower()
        if (
            any(word in raw for word in ("已签到", "已经签到", "重复签到"))
            or "repeated" in lowered
            or "already" in lowered
        ):
            return AI_VERDICT_REPEATED
        if "success" in lowered or "成功" in raw:
            return AI_VERDICT_SUCCESS
        if "failure" in lowered or "fail" in lowered or "失败" in raw:
            return AI_VERDICT_FAILURE
        return None

    @classmethod
    def parse_keywords(
        cls,
        text: str,
        source_text: str = "",
        blacklist: Sequence[str] = KEYWORD_BLACKLIST,
        limit: int = AI_KEYWORD_LIMIT,
    ) -> List[str]:
        """
        解析模型给出的候选关键词（必须逐字出现在原文里，否则丢弃）。

        :param text: 模型输出文本
        :param source_text: 该条回复原文（正文 + 弹窗）
        :param blacklist: 过泛词黑名单
        :param limit: 最多保留几条
        :return List[str]: 合规候选词（去重后）
        """

        data = cls._json_payload(text)
        raw_items = data.get("keywords")
        if not isinstance(raw_items, (list, tuple)):
            return []
        picked: List[str] = []
        seen = set()
        for item in raw_items:
            word = normalize_keyword(item)
            if not is_acceptable_keyword(word, source_text, blacklist):
                continue
            key = word.lower()
            if key in seen:
                continue
            seen.add(key)
            picked.append(word)
            if len(picked) >= max(1, int(limit)):
                break
        return picked

    @classmethod
    def parse_review(
        cls,
        text: str,
        source_text: str = "",
        want_keywords: bool = False,
    ) -> Tuple[Optional[str], List[str]]:
        """
        一次性解析档位结论与候选关键词。

        :param text: 模型输出文本
        :param source_text: 该条回复原文
        :param want_keywords: 是否同时归纳关键词
        :return Tuple[Optional[str], List[str]]: ``(结论, 候选词)``
        """

        verdict = cls.parse_result(text)
        keywords = (
            cls.parse_keywords(text, source_text=source_text) if want_keywords else []
        )
        return verdict, keywords

    async def _build_llm(self, config: Mapping[str, Any]) -> Any:
        """
        构造 langchain 模型实例（沿用 MP 提供的 LLMHelper）。

        :param config: MP 智能助手配置
        :return Any: 可调用的模型对象
        """

        from app.agent.llm import LLMHelper  # pylint: disable=import-outside-toplevel

        llm = LLMHelper.get_llm(
            streaming=False,
            provider=config.get("provider"),
            model=config.get("model"),
            thinking_level=config.get("thinking_level"),
            api_key=config.get("api_key"),
            base_url=config.get("base_url"),
            base_url_preset=config.get("base_url_preset"),
            user_agent=config.get("user_agent"),
            use_proxy=config.get("use_proxy"),
        )
        if inspect.isawaitable(llm):
            llm = await llm
        return llm

    @staticmethod
    async def _invoke(llm: Any, payload: str) -> Any:
        """
        调用模型，优先异步接口，退化到线程里跑同步接口。

        :param llm: 模型对象
        :param payload: 用户消息内容
        :return Any: 模型回复
        """

        from langchain_core.messages import (  # pylint: disable=import-outside-toplevel
            HumanMessage,
            SystemMessage,
        )

        messages = [SystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content=payload)]
        ainvoke = getattr(llm, "ainvoke", None)
        if callable(ainvoke):
            return await ainvoke(messages)
        return await asyncio.to_thread(llm.invoke, messages)

    async def judge(
        self,
        item: Mapping[str, Any],
        want_keywords: bool = False,
    ) -> AiReview:
        """
        复核一条「未确认」的签到结果（可选同时归纳关键词）。

        :param item: 单条签到结果（使用 reply / alert / method / bot 字段）
        :param want_keywords: 是否要求模型同时给出可复用短语
        :return AiReview: 复核记录；未启用时 ``state=not_called``
        """

        if not self.enabled:
            return AiReview(state=AI_STATE_NOT_CALLED, message="未开启 AI 复核/归纳")
        config, message = self._settings_dict()
        if not config:
            self._log("warning", "【TgSignin】AI 复核跳过（%s）", message)
            return AiReview(state=AI_STATE_UNCONFIGURED, message=message)

        reply = str(item.get("reply") or "")
        alert = str(item.get("alert") or "")
        source_text = " ".join(part for part in (reply, alert) if part).strip()
        payload = json.dumps(
            {
                "bot": item.get("bot"),
                "method": item.get("method"),
                "reply": reply,
                "alert": alert,
                "want_keywords": bool(want_keywords),
            },
            ensure_ascii=False,
        )
        try:
            llm = await asyncio.wait_for(self._build_llm(config), timeout=self.timeout)
            response = await asyncio.wait_for(
                self._invoke(llm, payload), timeout=self.timeout
            )
            raw = self._extract_text(getattr(response, "content", response))[:AI_RAW_LIMIT]
            verdict, keywords = self.parse_review(
                raw, source_text=source_text, want_keywords=want_keywords
            )
            if verdict is None:
                self._log(
                    "warning",
                    "【TgSignin】AI 判定「无法判定」（保持未确认）：回复=%s｜模型原文=%s",
                    (source_text or "无回复")[:80],
                    raw[:120],
                )
                return AiReview(
                    state=AI_STATE_UNKNOWN,
                    raw=raw,
                    keywords=keywords,
                    message="模型未能给出明确结论",
                )
            self._log(
                "info",
                "【TgSignin】AI 判定 %s%s｜模型原文=%s",
                verdict,
                f"｜归纳词={'/'.join(keywords)}" if keywords else "",
                raw[:120],
            )
            return AiReview(
                state=AI_STATE_JUDGED, verdict=verdict, raw=raw, keywords=keywords
            )
        except asyncio.TimeoutError:
            self._log(
                "warning", "【TgSignin】AI 复核超时（%.0f 秒），保持「未确认」", self.timeout
            )
            return AiReview(state=AI_STATE_TIMEOUT, message=f"超时（{self.timeout:.0f} 秒）")
        except Exception as error:  # pylint: disable=broad-except
            self._log(
                "warning",
                "【TgSignin】AI 复核异常（%s: %s），保持「未确认」",
                type(error).__name__,
                error,
            )
            return AiReview(state=AI_STATE_ERROR, message=f"{type(error).__name__}: {error}")
