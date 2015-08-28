"""
失败关键词分档 + 「自动使用 AI 确认」复核的回归测试（TgSignin 1.0.8）。

覆盖三件事：

1. **失败关键词**：明确失败文案（如 @HDHaven_Bot 的「签到服务暂不可用，请稍后重试。」）
   必须判「失败」并因此进入失败重试；且不得误伤成功/已签到文案（判定优先级）。
2. **AI 复核接线**：默认关闭时不动原判；开启后仅对「未确认」调用；
   AI 判失败要连带 `ok=False`（进重试），AI 判成功要转为成功；
   AI 返回 None / 抛异常 / 超时一律保持「未确认」，绝不影响签到主流程。
3. **复核器本身**：`AiSigninJudge` 的启用开关、无 LLM 配置、输出解析与超时降级。
"""

import asyncio
import unittest
from unittest import mock

import tests  # noqa: F401  触发宿主桩与插件路径注入

from tgsignin.core import signin as signin_mod
from tgsignin.core.ai import (
    AI_VERDICT_FAILURE,
    AI_VERDICT_SUCCESS,
    AiSigninJudge,
)
from tgsignin.core.config import (
    DEFAULT_FAILURE_KEYWORDS,
    SIGN_TYPE_COMMAND,
    BotTarget,
)
from tgsignin.core.retry import evaluate_retry, retry_key, today_text
from tgsignin.core.signin import (
    STATUS_FAILED,
    STATUS_REPEATED,
    STATUS_SUCCESS,
    STATUS_UNCONFIRMED,
    classify_result,
    signin_one,
)

# 用户实测的原始文案
UNAVAILABLE_REPLY = "签到服务暂不可用，请稍后重试。"

TARGET = BotTarget(
    account_key="acc1",
    bot_username="@HDHaven_Bot",
    sign_type=SIGN_TYPE_COMMAND,
    action_text="/checkin",
)


def _stub_impl(reply: str, ok: bool = True, alert: str = ""):
    """
    构造 `_signin_one_impl` 的替身，直接给出 bot 回复。

    :param reply: bot 回复文本
    :param ok: 交互是否完成
    :param alert: 按钮弹窗文本
    :return: 可 await 的替身函数
    """

    async def _impl(client, target):
        return {
            "ok": ok,
            "reply": reply,
            "alert": alert,
            "error": "",
            "method": target.method_desc(),
            "bot": target.bot_username,
            "time": "2026-10-08 09:00:00",
        }

    return _impl


class TestFailureKeywords(unittest.TestCase):
    """失败关键词分档与优先级。"""

    def test_hdhaven_unavailable_is_failed(self) -> None:
        """「签到服务暂不可用，请稍后重试。」必须判失败（本次需求的核心用例）。"""
        self.assertEqual(
            classify_result(UNAVAILABLE_REPLY, True, "发命令「/checkin」"),
            STATUS_FAILED,
        )

    def test_builtin_failure_variants(self) -> None:
        """内置词表覆盖常见失败说法。"""
        for text in (
            "服务异常，请稍后再试",
            "系统繁忙，请重试",
            "签到失败，请检查账号",
            "Service Unavailable, try again later",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    classify_result(text, True, "发命令「/checkin」"), STATUS_FAILED
                )
        self.assertIn("暂不可用", DEFAULT_FAILURE_KEYWORDS)

    def test_custom_failure_keywords(self) -> None:
        """自定义失败关键词生效；未配置时同一文案仍是「未确认」。"""
        self.assertEqual(
            classify_result(
                "系统维护中，稍后开放",
                True,
                "发命令「/checkin」",
                failure_keywords=("维护中",),
            ),
            STATUS_FAILED,
        )
        self.assertEqual(
            classify_result("系统维护中，稍后开放", True, "发命令「/checkin」"),
            STATUS_UNCONFIRMED,
        )

    def test_success_keyword_wins(self) -> None:
        """成功文案优先于失败词：不能被「稍后重试」这类尾缀拖成失败。"""
        self.assertEqual(
            classify_result(
                "✅ 签到成功，如未到账请稍后重试", True, "发命令「/checkin」"
            ),
            STATUS_SUCCESS,
        )

    def test_repeated_alert_wins(self) -> None:
        """弹窗已签到优先于正文里的失败词。"""
        self.assertEqual(
            classify_result(
                "请稍后重试",
                True,
                "点按钮「签到」",
                "您今天已经签到过了",
            ),
            STATUS_REPEATED,
        )

    def test_unrelated_text_still_unconfirmed(self) -> None:
        """无关文案仍是「未确认」，不会被误判为失败。"""
        self.assertEqual(
            classify_result("我不知道你在说什么", True, "发命令「/checkin」"),
            STATUS_UNCONFIRMED,
        )


class TestFailurePathIntegration(unittest.TestCase):
    """失败关键词 → ok=False → 进入失败重试的完整链路。"""

    def _run(self, reply: str):
        """跑一次 signin_one（打桩底层实现）。

        :param reply: bot 回复文本
        :return Dict[str, Any]: 签到结果
        """

        with mock.patch.object(signin_mod, "_signin_one_impl", _stub_impl(reply)):
            return asyncio.run(signin_one(None, TARGET))

    def test_failed_result_is_not_ok_and_retry_due(self) -> None:
        """失败文案：ok=False，且失败重试窗口会挑出该目标。"""
        result = self._run(UNAVAILABLE_REPLY)
        self.assertEqual(result["status"], STATUS_FAILED)
        self.assertFalse(result["ok"])
        self.assertIn("签到失败", result["error"])

        today = today_text()
        state = {
            "signin_state": {
                retry_key(TARGET): {
                    "date": today,
                    "ok": result["ok"],
                    "last_attempt_at": 0,
                }
            }
        }
        due, _ = evaluate_retry([TARGET], state, retry_interval_hours=6)
        self.assertIn(TARGET, due)

    def test_unconfirmed_is_not_retried(self) -> None:
        """未确认（未开启 AI）不会进重试——正是本次要修的缺口。"""
        result = self._run("我不知道你在说什么")
        self.assertEqual(result["status"], STATUS_UNCONFIRMED)
        self.assertTrue(result["ok"])

        state = {
            "signin_state": {
                retry_key(TARGET): {
                    "date": today_text(),
                    "ok": result["ok"],
                    "last_attempt_at": 0,
                }
            }
        }
        due, _ = evaluate_retry([TARGET], state, retry_interval_hours=6)
        self.assertEqual(due, [])


class TestAiConfirm(unittest.TestCase):
    """AI 复核的接线与降级。"""

    def _run(self, reply: str, judge, expect_calls: int = 0):
        """
        跑一次 signin_one 并统计 AI 复核调用次数。

        :param reply: bot 回复文本
        :param judge: AI 复核协程
        :param expect_calls: 期望的调用次数
        :return Dict[str, Any]: 签到结果
        """

        calls = {"n": 0}
        real_judge = judge

        async def _counting(item):
            calls["n"] += 1
            return await real_judge(item)

        with mock.patch.object(signin_mod, "_signin_one_impl", _stub_impl(reply)):
            result = asyncio.run(signin_one(None, TARGET, ai_judge=_counting))
        self.assertEqual(calls["n"], expect_calls)
        return result

    def test_disabled_keeps_unconfirmed(self) -> None:
        """ai_judge=None：保持「未确认」，不做任何额外动作。"""
        with mock.patch.object(
            signin_mod, "_signin_one_impl", _stub_impl("随便聊聊")
        ):
            result = asyncio.run(signin_one(None, TARGET, ai_judge=None))
        self.assertEqual(result["status"], STATUS_UNCONFIRMED)
        self.assertTrue(result["ok"])
        self.assertNotIn("ai_verdict", result)

    def test_failure_keyword_still_works_without_ai(self) -> None:
        """未开启 AI 时，失败关键词本身已足以判失败并进重试。"""
        with mock.patch.object(
            signin_mod, "_signin_one_impl", _stub_impl(UNAVAILABLE_REPLY)
        ):
            result = asyncio.run(signin_one(None, TARGET, ai_judge=None))
        self.assertEqual(result["status"], STATUS_FAILED)
        self.assertFalse(result["ok"])

    def test_ai_failure_marks_failed(self) -> None:
        """AI 判失败：status=失败、ok=False、记得下 ai_verdict 并进重试。"""

        async def _judge(item):
            return AI_VERDICT_FAILURE

        result = self._run("随便聊聊", _judge, expect_calls=1)
        self.assertEqual(result["status"], STATUS_FAILED)
        self.assertFalse(result["ok"])
        self.assertEqual(result["ai_verdict"], AI_VERDICT_FAILURE)
        self.assertIn("AI 复核判定本次签到失败", result["error"])

        state = {
            "signin_state": {
                retry_key(TARGET): {
                    "date": today_text(),
                    "ok": result["ok"],
                    "last_attempt_at": 0,
                }
            }
        }
        due, _ = evaluate_retry([TARGET], state, retry_interval_hours=6)
        self.assertIn(TARGET, due)

    def test_ai_success_marks_success(self) -> None:
        """AI 判成功：status=签到成功、ok 保持 True。"""

        async def _judge(item):
            return AI_VERDICT_SUCCESS

        result = self._run("随便聊聊", _judge, expect_calls=1)
        self.assertEqual(result["status"], STATUS_SUCCESS)
        self.assertTrue(result["ok"])
        self.assertEqual(result["ai_verdict"], AI_VERDICT_SUCCESS)

    def test_ai_none_and_error_keep_unconfirmed(self) -> None:
        """AI 返回 None 或抛异常：保持「未确认」，异常不外泄。"""

        async def _none(item):
            return None

        async def _boom(item):
            raise RuntimeError("模型炸了")

        for judge in (_none, _boom):
            with self.subTest(judge=judge.__name__):
                result = self._run("随便聊聊", judge, expect_calls=1)
                self.assertEqual(result["status"], STATUS_UNCONFIRMED)
                self.assertTrue(result["ok"])
                self.assertNotIn("ai_verdict", result)

    def test_ai_not_called_when_confirmed(self) -> None:
        """结果已确定（成功/已签到）时不调用 AI，省 token。"""

        async def _judge(item):  # pragma: no cover - 不应被调用
            return AI_VERDICT_FAILURE

        success = self._run("✅ 签到成功！获得 5 积分", _judge, expect_calls=0)
        self.assertEqual(success["status"], STATUS_SUCCESS)
        repeated = self._run("✅ 今日已签到，明天再来。", _judge, expect_calls=0)
        self.assertEqual(repeated["status"], STATUS_REPEATED)


class TestAiSigninJudge(unittest.TestCase):
    """复核器自身：开关、配置缺失、输出解析与超时降级。"""

    def test_disabled_returns_none(self) -> None:
        """未开启时直接返回 None：即使 MP 已配好智能助手也不得调用模型。"""
        judge = AiSigninJudge(enabled=False)
        with mock.patch.object(
            AiSigninJudge,
            "_settings_dict",
            return_value=({"model": "m", "api_key": "k"}, ""),
        ), mock.patch.object(
            AiSigninJudge,
            "_build_llm",
            new=mock.AsyncMock(side_effect=AssertionError("关闭时不应调用模型")),
        ):
            self.assertIsNone(asyncio.run(judge.judge({"reply": UNAVAILABLE_REPLY})))
        self.assertEqual(judge.available()["enabled"], False)

    def test_parse_verdict_variants(self) -> None:
        """模型输出解析：JSON / 代码块 / 纯文本 / 无法判定。"""
        cases = {
            '{"result":"success"}': AI_VERDICT_SUCCESS,
            '```json\n{"result":"failure"}\n```': AI_VERDICT_FAILURE,
            "结论：签到失败": AI_VERDICT_FAILURE,
            "签到成功": AI_VERDICT_SUCCESS,
            '{"result":"unknown"}': None,
            "": None,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(AiSigninJudge.parse_verdict(text), expected)

    def test_judge_without_llm_config(self) -> None:
        """MP 未配置智能助手时返回 None（保持未确认）。"""
        judge = AiSigninJudge(enabled=True)
        with mock.patch.object(
            AiSigninJudge, "_settings_dict", return_value=(None, "未配置 MoviePilot 智能助手 API Key")
        ):
            self.assertIsNone(asyncio.run(judge.judge({"reply": "随便聊聊"})))
        summary = judge.available()
        self.assertTrue(summary["enabled"])

    def _judge_with_response(self, content: str):
        """
        构造一个用假模型返回固定内容的复核器。

        :param content: 模型返回文本
        :return AiSigninJudge: 复核器实例
        """

        class _Response:
            """假模型回复对象。"""

            def __init__(self, text: str) -> None:
                self.content = text

        judge = AiSigninJudge(enabled=True, timeout=1.0)
        patcher_settings = mock.patch.object(
            AiSigninJudge, "_settings_dict", return_value=({"model": "m", "api_key": "k"}, "")
        )
        patcher_build = mock.patch.object(
            AiSigninJudge, "_build_llm", new=mock.AsyncMock(return_value=object())
        )
        patcher_invoke = mock.patch.object(
            AiSigninJudge, "_invoke", new=mock.AsyncMock(return_value=_Response(content))
        )
        patcher_settings.start()
        patcher_build.start()
        patcher_invoke.start()
        self.addCleanup(patcher_settings.stop)
        self.addCleanup(patcher_build.stop)
        self.addCleanup(patcher_invoke.stop)
        return judge

    def test_judge_end_to_end(self) -> None:
        """端到端：模型输出被正确映射为复核结论。"""
        judge = self._judge_with_response('{"result":"failure"}')
        self.assertEqual(
            asyncio.run(judge.judge({"reply": "随便聊聊"})), AI_VERDICT_FAILURE
        )
        judge = self._judge_with_response('{"result":"success"}')
        self.assertEqual(
            asyncio.run(judge.judge({"reply": "随便聊聊"})), AI_VERDICT_SUCCESS
        )

    def test_judge_timeout_and_exception(self) -> None:
        """超时或异常都返回 None（不抛给调用方）。"""

        async def _slow(llm, payload):
            await asyncio.sleep(0.5)
            return None

        judge = AiSigninJudge(enabled=True, timeout=1.0)
        with mock.patch.object(
            AiSigninJudge, "_settings_dict", return_value=({"model": "m", "api_key": "k"}, "")
        ), mock.patch.object(
            AiSigninJudge, "_build_llm", new=mock.AsyncMock(return_value=object())
        ), mock.patch.object(AiSigninJudge, "_invoke", new=_slow):
            self.assertIsNone(asyncio.run(judge.judge({"reply": "随便聊聊"})))

        with mock.patch.object(
            AiSigninJudge, "_settings_dict", return_value=({"model": "m", "api_key": "k"}, "")
        ), mock.patch.object(
            AiSigninJudge, "_build_llm", new=mock.AsyncMock(side_effect=RuntimeError("boom"))
        ):
            self.assertIsNone(asyncio.run(judge.judge({"reply": "随便聊聊"})))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
