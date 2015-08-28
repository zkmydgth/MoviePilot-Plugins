"""
用 MoviePilot 内置 AI 复核「未确认」的签到结果（默认关闭）。

只在结果落到「未确认」（bot 有回复，但既不含成功词也不含失败词）且用户**显式开启**时调用：

- 未开启、未配置 MP 智能助手、超时、模型输出无法解析 —— 一律**不改变**原判（保持「未确认」）；
- AI 判定为失败时会连同 `ok=False` 一起回流，从而进入「失败重试」窗口；
- 任何异常都被吞掉并记日志，**绝不让 AI 成为签到链路的硬依赖**（沿用 LunaTV 插件的同款做法）。

密钥只从 MP 设置读取，插件不保存、不打印任何 `LLM_*` 值。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from typing import Any, Dict, Mapping, Optional, Tuple

__all__ = [
    "AiSigninJudge",
    "AI_VERDICT_SUCCESS",
    "AI_VERDICT_FAILURE",
    "AI_DEFAULT_TIMEOUT_SECONDS",
]

# AI 复核的两种结论（与 signin 的 STATUS_* 解耦，避免循环导入）
AI_VERDICT_SUCCESS = "success"
AI_VERDICT_FAILURE = "failure"

# 默认超时（秒）：AI 只是复核，不能拖垮签到主流程
AI_DEFAULT_TIMEOUT_SECONDS = 30.0

# 系统提示：要求只输出 JSON，便于稳定解析
_SYSTEM_PROMPT = (
    "你在判断一次 Telegram 签到（check-in / 打卡）是否成功。"
    "输入会给出 bot 回复文本、按钮弹窗文本与签到方式。"
    "判定口径：回复表明「签到已完成 / 打卡成功 / 今天已经签到过」→ success；"
    "回复表明「签到失败 / 服务不可用 / 稍后重试 / 异常」→ failure；"
    "信息不足以判断（例如只回了菜单、只回了无关文案）→ unknown。"
    '只输出 JSON，不要解释：{"result":"success"} / {"result":"failure"} / {"result":"unknown"}。'
)

_FENCE = re.compile(r"^```(?:json)?\s*([\s\S]*?)\s*```$", re.IGNORECASE)


class AiSigninJudge:
    """调用 MoviePilot 内置 AI 复核签到结果。"""

    def __init__(
        self,
        enabled: bool = False,
        timeout: float = AI_DEFAULT_TIMEOUT_SECONDS,
        logger: Any = None,
    ) -> None:
        """
        初始化复核器。

        :param enabled: 是否启用（默认关闭；关闭时 judge() 直接返回 None）
        :param timeout: 单次复核的超时秒数
        :param logger: 可选的日志器（Info/Warning 级别）
        """

        self.enabled = bool(enabled)
        self.timeout = max(1.0, float(timeout or AI_DEFAULT_TIMEOUT_SECONDS))
        self.logger = logger

    def _warn(self, message: str, *args: Any) -> None:
        """
        记录警告日志（无日志器时静默）。

        :param message: 日志模板
        :param args: 模板参数
        """

        if self.logger is None:
            return
        try:
            self.logger.warning(message, *args)
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
            "api_key": getattr(settings, "LLM_API_KEY", None),
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
                "message": "未开启「自动使用 AI 确认」",
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

    @classmethod
    def parse_verdict(cls, text: str) -> Optional[str]:
        """
        把模型输出解析为复核结论。

        :param text: 模型输出文本（JSON、代码块包裹或纯文本均可）
        :return Optional[str]: ``AI_VERDICT_SUCCESS`` / ``AI_VERDICT_FAILURE`` / None（无法判定）
        """

        raw = str(text or "").strip()
        if not raw:
            return None
        match = _FENCE.match(raw)
        if match:
            raw = match.group(1).strip()
        if not raw.startswith("{"):
            start, end = raw.find("{"), raw.rfind("}")
            if start >= 0 and end > start:
                raw = raw[start : end + 1]
        if raw.startswith("{"):
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = {}
            value = str((data or {}).get("result") or "").strip().lower()
            if value in {"success", "ok", "true", "成功"}:
                return AI_VERDICT_SUCCESS
            if value in {"failure", "fail", "false", "失败"}:
                return AI_VERDICT_FAILURE
            if value:
                return None
        lowered = raw.lower()
        if "success" in lowered or "成功" in raw:
            return AI_VERDICT_SUCCESS
        if "failure" in lowered or "fail" in lowered or "失败" in raw:
            return AI_VERDICT_FAILURE
        return None

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

    async def judge(self, item: Mapping[str, Any]) -> Optional[str]:
        """
        复核一条「未确认」的签到结果。

        :param item: 单条签到结果（使用 reply / alert / method / bot 字段）
        :return Optional[str]: 复核结论；无法判定或未启用时返回 None
        """

        if not self.enabled:
            return None
        config, message = self._settings_dict()
        if not config:
            self._warn("【TgSignin】AI 复核跳过：%s", message)
            return None
        payload = json.dumps(
            {
                "bot": item.get("bot"),
                "method": item.get("method"),
                "reply": str(item.get("reply") or ""),
                "alert": str(item.get("alert") or ""),
            },
            ensure_ascii=False,
        )
        try:
            llm = await asyncio.wait_for(self._build_llm(config), timeout=self.timeout)
            response = await asyncio.wait_for(
                self._invoke(llm, payload), timeout=self.timeout
            )
            verdict = self.parse_verdict(
                self._extract_text(getattr(response, "content", response))
            )
            if verdict is None:
                self._warn("【TgSignin】AI 复核结果无法解析，保持「未确认」")
            return verdict
        except asyncio.TimeoutError:
            self._warn("【TgSignin】AI 复核超时（%.0f 秒），保持「未确认」", self.timeout)
            return None
        except Exception as error:  # pylint: disable=broad-except
            self._warn(
                "【TgSignin】AI 复核失败（%s: %s），保持「未确认」",
                type(error).__name__,
                error,
            )
            return None
