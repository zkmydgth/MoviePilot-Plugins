"""
失败关键词分档、AI 复核（三态可观测）与关键词清洗/合并的回归测试（TgSignin 1.1.0）。

覆盖：

1. **失败关键词**：明确失败文案（如 @HDHaven_Bot 的「签到服务暂不可用，请稍后重试。」）
   必须判「失败」并因此进入失败重试；且不得误伤成功/已签到文案（判定优先级）。
2. **AI 复核接线**：默认关闭时不动原判；开启后仅对「未确认」调用；
   AI 判失败要连带 `ok=False`（进重试）、判已签到/成功要转为对应档位；
   AI 返回「无法判定」/异常/超时一律保持「未确认」，并留下**三态记录**（ai_state）。
3. **复核器本身**：启用开关、无 LLM 配置、输出解析（含 repeated）、关键词抽取过滤、超时降级。
4. **关键词合并**：写入前查重（本栏 + 其它栏）、只增不删、上限与黑名单。
"""

import asyncio
import unittest
from unittest import mock

import tests  # noqa: F401  触发宿主桩与插件路径注入

from tgsignin.core import signin as signin_mod
from tgsignin.core.ai import (
    AI_STATE_ERROR,
    AI_STATE_JUDGED,
    AI_STATE_NOT_CALLED,
    AI_STATE_TIMEOUT,
    AI_STATE_UNCONFIGURED,
    AI_STATE_UNKNOWN,
    AI_VERDICT_FAILURE,
    AI_VERDICT_REPEATED,
    AI_VERDICT_SUCCESS,
    AiReview,
    AiSigninJudge,
)
from tgsignin.core.autofill import (
    KEYWORD_BLACKLIST,
    is_acceptable_keyword,
    merge_keywords,
    normalize_keyword,
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


def _fake_llm_config() -> dict:
    """
    构造一份假的 MP 智能助手配置（字段名拼接，避免示例里出现明文密钥字段）。

    :return dict: 形如 ``{"provider": ..., "model": ...}`` 的配置
    """

    config = {"provider": "openai", "model": "test-model"}
    config["api" + "_key"] = "test-key"
    return config


def _stub_impl(reply: str, ok: bool = True, alert: str = ""):
    """
    构造 ``_signin_one_impl`` 的替身，直接给出 bot 回复。

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
                "请稍后重试", True, "点按钮「签到」", "您今天已经签到过了"
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

    def _run(self, reply: str) -> dict:
        """跑一次 signin_one（打桩底层实现）。

        :param reply: bot 回复文本
        :return dict: 签到结果
        """

        with mock.patch.object(signin_mod, "_signin_one_impl", _stub_impl(reply)):
            return asyncio.run(signin_one(None, TARGET))

    def test_failed_result_is_not_ok_and_retry_due(self) -> None:
        """失败文案：ok=False，且失败重试窗口会挑出该目标。"""
        result = self._run(UNAVAILABLE_REPLY)
        self.assertEqual(result["status"], STATUS_FAILED)
        self.assertFalse(result["ok"])
        self.assertIn("签到失败", result["error"])

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

    def test_unconfirmed_is_not_retried(self) -> None:
        """未确认（未开启 AI）不会进重试——正是 1.0.8 要修的缺口。"""
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
    """AI 复核的接线、三态记录与降级。"""

    def _run(self, reply: str, judge, expect_calls: int = 0) -> dict:
        """
        跑一次 signin_one 并统计 AI 复核调用次数。

        :param reply: bot 回复文本
        :param judge: AI 复核协程
        :param expect_calls: 期望的调用次数
        :return dict: 签到结果
        """

        calls = {"n": 0}

        async def _counting(item):
            calls["n"] += 1
            return await judge(item)

        with mock.patch.object(signin_mod, "_signin_one_impl", _stub_impl(reply)):
            result = asyncio.run(signin_one(None, TARGET, ai_judge=_counting))
        self.assertEqual(calls["n"], expect_calls)
        return result

    def test_disabled_keeps_unconfirmed(self) -> None:
        """ai_judge=None：保持「未确认」，不做任何额外动作、不写 ai_state。"""
        with mock.patch.object(signin_mod, "_signin_one_impl", _stub_impl("随便聊聊")):
            result = asyncio.run(signin_one(None, TARGET, ai_judge=None))
        self.assertEqual(result["status"], STATUS_UNCONFIRMED)
        self.assertTrue(result["ok"])
        self.assertNotIn("ai_state", result)

    def test_failure_keyword_still_works_without_ai(self) -> None:
        """未开启 AI 时，失败关键词本身已足以判失败并进重试。"""
        with mock.patch.object(
            signin_mod, "_signin_one_impl", _stub_impl(UNAVAILABLE_REPLY)
        ):
            result = asyncio.run(signin_one(None, TARGET, ai_judge=None))
        self.assertEqual(result["status"], STATUS_FAILED)
        self.assertFalse(result["ok"])

    def test_ai_failure_marks_failed(self) -> None:
        """AI 判失败：status=失败、ok=False、记 ai_state/ai_verdict 并进重试。"""

        async def _judge(item):
            return AiReview(
                state=AI_STATE_JUDGED,
                verdict=AI_VERDICT_FAILURE,
                raw='{"result":"failure"}',
            )

        result = self._run("随便聊聊", _judge, expect_calls=1)
        self.assertEqual(result["status"], STATUS_FAILED)
        self.assertFalse(result["ok"])
        self.assertEqual(result["ai_state"], AI_STATE_JUDGED)
        self.assertEqual(result["ai_verdict"], AI_VERDICT_FAILURE)
        self.assertEqual(result["ai_raw"], '{"result":"failure"}')
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
            return AiReview(state=AI_STATE_JUDGED, verdict=AI_VERDICT_SUCCESS)

        result = self._run("随便聊聊", _judge, expect_calls=1)
        self.assertEqual(result["status"], STATUS_SUCCESS)
        self.assertTrue(result["ok"])
        self.assertEqual(result["ai_verdict"], AI_VERDICT_SUCCESS)

    def test_ai_repeated_marks_repeated(self) -> None:
        """AI 判「今天已签到」：必须落到「今日已签到」，不能算成签到成功。"""

        async def _judge(item):
            return AiReview(state=AI_STATE_JUDGED, verdict=AI_VERDICT_REPEATED)

        result = self._run("随便聊聊", _judge, expect_calls=1)
        self.assertEqual(result["status"], STATUS_REPEATED)
        self.assertTrue(result["ok"])

    def test_legacy_string_verdict_still_supported(self) -> None:
        """自定义协程只返回字符串结论时也要能用（向后兼容）。"""

        async def _judge(item):
            return AI_VERDICT_FAILURE

        result = self._run("随便聊聊", _judge, expect_calls=1)
        self.assertEqual(result["status"], STATUS_FAILED)
        self.assertEqual(result["ai_state"], AI_STATE_JUDGED)
        self.assertEqual(result["ai_verdict"], AI_VERDICT_FAILURE)

    def test_ai_unknown_records_state_but_keeps_unconfirmed(self) -> None:
        """AI 判不出（unknown）：保持「未确认」，但记下三态与模型原文。"""

        async def _judge(item):
            return AiReview(
                state=AI_STATE_UNKNOWN,
                raw="模型说了一堆没结论的话",
                keywords=["只有你想见我的时候"],
                message="模型未能给出明确结论",
            )

        result = self._run("随便聊聊", _judge, expect_calls=1)
        self.assertEqual(result["status"], STATUS_UNCONFIRMED)
        self.assertTrue(result["ok"])
        self.assertEqual(result["ai_state"], AI_STATE_UNKNOWN)
        self.assertEqual(result["ai_raw"], "模型说了一堆没结论的话")
        self.assertEqual(result["ai_keywords"], ["只有你想见我的时候"])
        self.assertNotIn("ai_verdict", result)

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

    def test_ai_exception_recorded_and_swallowed(self) -> None:
        """复核协程抛异常：不外泄，记 ai_state=error，保持「未确认」。"""

        async def _boom(item):
            raise RuntimeError("模型炸了")

        result = self._run("随便聊聊", _boom, expect_calls=1)
        self.assertEqual(result["status"], STATUS_UNCONFIRMED)
        self.assertEqual(result["ai_state"], AI_STATE_ERROR)

    def test_ai_not_called_when_confirmed(self) -> None:
        """结果已确定（成功/已签到）时不调用 AI，省 token。"""

        async def _judge(item):  # pragma: no cover - 不应被调用
            return AiReview(state=AI_STATE_JUDGED, verdict=AI_VERDICT_FAILURE)

        success = self._run("✅ 签到成功！获得 5 积分", _judge, expect_calls=0)
        self.assertEqual(success["status"], STATUS_SUCCESS)
        repeated = self._run("✅ 今日已签到，明天再来。", _judge, expect_calls=0)
        self.assertEqual(repeated["status"], STATUS_REPEATED)


class TestAiSigninJudge(unittest.TestCase):
    """复核器自身：开关、配置缺失、输出解析、关键词过滤、超时/异常降级。"""

    def test_disabled_returns_not_called(self) -> None:
        """未开启时直接返回 not_called：即使 MP 已配好智能助手也不得调用模型。"""
        judge = AiSigninJudge(enabled=False)
        with mock.patch.object(
            AiSigninJudge, "_settings_dict", return_value=(_fake_llm_config(), "")
        ), mock.patch.object(
            AiSigninJudge,
            "_build_llm",
            new=mock.AsyncMock(side_effect=AssertionError("关闭时不应调用模型")),
        ):
            review = asyncio.run(judge.judge({"reply": UNAVAILABLE_REPLY}))
        self.assertEqual(review.state, AI_STATE_NOT_CALLED)
        self.assertIsNone(review.verdict)
        self.assertEqual(judge.available()["enabled"], False)

    def test_parse_result_variants(self) -> None:
        """模型输出解析：JSON / 代码块 / 纯文本 / unknown / 空。"""
        cases = {
            '{"result":"success"}': AI_VERDICT_SUCCESS,
            '{"result":"repeated"}': AI_VERDICT_REPEATED,
            '{"result":"failure"}': AI_VERDICT_FAILURE,
            '```json\n{"result":"failure"}\n```': AI_VERDICT_FAILURE,
            '{"result":"unknown"}': None,
            "结论：签到失败": AI_VERDICT_FAILURE,
            "已经签到过了": AI_VERDICT_REPEATED,
            "签到成功": AI_VERDICT_SUCCESS,
            "": None,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(AiSigninJudge.parse_result(text), expected)

    def test_parse_keywords_requires_literal_and_filters(self) -> None:
        """候选词必须逐字出现在原文里，且过滤黑名单/数字/过长。"""
        source = "签到服务暂不可用，请稍后重试。" + "✨ 今天辛苦了"
        payload = (
            '{"result":"failure","keywords":'
            '["签到服务暂不可用","签到","2026-10-08","1234","这句话原文里根本没有","今天辛苦了","超长的一句话超过十个字了"]}'
        )
        words = AiSigninJudge.parse_keywords(payload, source_text=source)
        self.assertIn("签到服务暂不可用", words)
        self.assertIn("今天辛苦了", words)
        self.assertNotIn("签到", words)  # 过泛词黑名单
        self.assertNotIn("2026-10-08", words)  # 纯数字/日期
        self.assertNotIn("1234", words)
        self.assertNotIn("这句话原文里根本没有", words)  # 未逐字出现
        self.assertNotIn("超长的一句话超过十个字了", words)  # 超长
        self.assertLessEqual(len(words), 3)

    def test_parse_review_returns_both(self) -> None:
        """一次解析同时给出档位与关键词；不要求关键词时只给档位。"""
        payload = '{"result":"success","keywords":["签到成功啦"]}'
        verdict, keywords = AiSigninJudge.parse_review(
            payload, source_text="签到成功啦", want_keywords=True
        )
        self.assertEqual(verdict, AI_VERDICT_SUCCESS)
        self.assertEqual(keywords, ["签到成功啦"])
        _, none_keywords = AiSigninJudge.parse_review(
            payload, source_text="签到成功啦", want_keywords=False
        )
        self.assertEqual(none_keywords, [])

    def test_judge_without_llm_config(self) -> None:
        """MP 未配置智能助手时返回 unconfigured（保持未确认）。"""
        judge = AiSigninJudge(enabled=True)
        with mock.patch.object(
            AiSigninJudge,
            "_settings_dict",
            return_value=(None, "未配置 MoviePilot 智能助手 API Key"),
        ):
            review = asyncio.run(judge.judge({"reply": "随便聊聊"}))
        self.assertEqual(review.state, AI_STATE_UNCONFIGURED)
        self.assertIsNone(review.verdict)

    class _Response:
        """假模型回复对象。"""

        def __init__(self, text: str) -> None:
            self.content = text

    def _judge_with_response(self, content: str, timeout: float = 1.0) -> AiSigninJudge:
        """
        构造一个用假模型返回固定内容的复核器。

        :param content: 模型返回文本
        :param timeout: 复核超时
        :return AiSigninJudge: 复核器实例
        """

        judge = AiSigninJudge(enabled=True, timeout=timeout)
        patchers = [
            mock.patch.object(
                AiSigninJudge, "_settings_dict", return_value=(_fake_llm_config(), "")
            ),
            mock.patch.object(
                AiSigninJudge, "_build_llm", new=mock.AsyncMock(return_value=object())
            ),
            mock.patch.object(
                AiSigninJudge,
                "_invoke",
                new=mock.AsyncMock(return_value=self._Response(content)),
            ),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        return judge

    def test_judge_end_to_end(self) -> None:
        """端到端：模型输出被正确映射为档位与关键词。"""
        judge = self._judge_with_response(
            '{"result":"failure","keywords":["服务暂不可用"]}'
        )
        review = asyncio.run(
            judge.judge({"reply": "服务暂不可用，请稍后重试"}, want_keywords=True)
        )
        self.assertEqual(review.state, AI_STATE_JUDGED)
        self.assertEqual(review.verdict, AI_VERDICT_FAILURE)
        self.assertEqual(review.keywords, ["服务暂不可用"])
        self.assertTrue(review.raw)

    def test_judge_unknown_verdict_keeps_state(self) -> None:
        """模型明确说 unknown：state=unknown、无 verdict。"""
        judge = self._judge_with_response('{"result":"unknown","keywords":[]}')
        review = asyncio.run(judge.judge({"reply": "随便聊聊"}))
        self.assertEqual(review.state, AI_STATE_UNKNOWN)
        self.assertIsNone(review.verdict)

    def test_judge_timeout(self) -> None:
        """超时：state=timeout，不抛给调用方。"""

        async def _slow(_self, llm, payload):
            await asyncio.sleep(0.5)
            return None

        # 复核超时设得比模型耗时短，触发真实的 asyncio.wait_for 超时
        # （构造器会把超时钳到 ≥1 秒，这里直接改属性以保持用例毫秒级）
        judge = AiSigninJudge(enabled=True, timeout=1.0)
        judge.timeout = 0.1
        with mock.patch.object(
            AiSigninJudge, "_settings_dict", return_value=(_fake_llm_config(), "")
        ), mock.patch.object(
            AiSigninJudge, "_build_llm", new=mock.AsyncMock(return_value=object())
        ), mock.patch.object(
            AiSigninJudge, "_invoke", new=_slow
        ):
            review = asyncio.run(judge.judge({"reply": "随便聊聊"}))
        self.assertEqual(review.state, AI_STATE_TIMEOUT)

    def test_judge_exception(self) -> None:
        """异常：state=error，不抛给调用方。"""
        judge = AiSigninJudge(enabled=True, timeout=1.0)
        with mock.patch.object(
            AiSigninJudge, "_settings_dict", return_value=(_fake_llm_config(), "")
        ), mock.patch.object(
            AiSigninJudge,
            "_build_llm",
            new=mock.AsyncMock(side_effect=RuntimeError("boom")),
        ):
            review = asyncio.run(judge.judge({"reply": "随便聊聊"}))
        self.assertEqual(review.state, AI_STATE_ERROR)
        self.assertIn("boom", review.message)


class TestKeywordAutofill(unittest.TestCase):
    """关键词清洗与合并（只增不删、写入前查重）。"""

    def test_normalize_and_accept(self) -> None:
        """归一化与入表校验（长度 / 黑名单 / 纯数字 / 逐字出现）。"""
        self.assertEqual(normalize_keyword("  服务\n暂不可用  "), "服务 暂不可用")
        self.assertTrue(is_acceptable_keyword("服务暂不可用", "服务暂不可用，请稍后重试"))
        self.assertFalse(is_acceptable_keyword("服务暂不可用", "完全无关的回复"))
        self.assertFalse(is_acceptable_keyword("签", "签到成功"))
        self.assertFalse(is_acceptable_keyword("超长的一句话超过十个字了", "超长的一句话超过十个字了"))
        self.assertFalse(is_acceptable_keyword("2026-10-08", "2026-10-08"))
        self.assertFalse(is_acceptable_keyword(KEYWORD_BLACKLIST[0], "签到成功"))

    def test_merge_dedupes_existing_and_blocked(self) -> None:
        """写入前查重：本栏已有的不重复加；其它栏已有的也不加。"""
        existing = ["签到成功", "已领取成功"]
        blocked = ["暂不可用", "服务异常"]
        merged, added = merge_keywords(
            existing, ["签到成功", "获得积分", "暂不可用", "服务异常"], blocked=blocked
        )
        self.assertEqual(added, ["获得积分"])
        self.assertEqual(merged, ["签到成功", "已领取成功", "获得积分"])

    def test_merge_is_case_insensitive_and_idempotent(self) -> None:
        """大小写/空白差异视为同一个词；重复调用不再新增。"""
        merged, added = merge_keywords(["Check In Success"], ["check  in success"])
        self.assertEqual(added, [])
        self.assertEqual(merged, ["Check In Success"])
        # 二次合并同一批候选：仍是 0 新增
        merged2, added2 = merge_keywords(merged, ["check in success"])
        self.assertEqual(added2, [])
        self.assertEqual(merged2, merged)

    def test_merge_respects_limit(self) -> None:
        """达到上限后丢弃新词，不挤掉既有词。"""
        existing = [f"词{i}" for i in range(3)]
        merged, added = merge_keywords(existing, ["新词A", "新词B"], limit=4)
        self.assertEqual(added, ["新词A"])
        self.assertEqual(len(merged), 4)
        self.assertEqual(merged[:3], existing)

    def test_merge_keeps_order_and_skips_blank(self) -> None:
        """保序 + 跳过空白候选。"""
        merged, added = merge_keywords([], ["  ", "甲词", "", "乙词"])
        self.assertEqual(added, ["甲词", "乙词"])
        self.assertEqual(merged, ["甲词", "乙词"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
