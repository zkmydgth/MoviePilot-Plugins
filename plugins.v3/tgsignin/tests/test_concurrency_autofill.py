"""
并发签到与 AI 归纳关键词写回的回归测试（TgSignin 1.1.0）。

覆盖：

1. **并发**：`concurrency=1` 与旧版行为一致（严格串行）；`concurrency>1` 时同账号内
   多路并发且不超过上限；结果仍按原始目标顺序返回；命中 FloodWait 退避后重试一次；
   `run_all` 正确把并发上限透传给 `run_account`。
2. **审计落盘**：`store.record_ai_keywords` 记录新增词并在超限时截断。
3. **插件写回**：`TgSignin._apply_ai_keywords` 只增不删、写入前查重（本栏 + 其它栏），
   有新增时持久化配置与内存词表。
"""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tests  # noqa: F401  触发宿主桩与插件路径注入

from tgsignin import TgSignin
from tgsignin.core import signin as signin_mod
from tgsignin.core.config import AccountConfig, BotTarget, SIGN_TYPE_COMMAND
from tgsignin.core.signin import _flood_wait_seconds, _jitter_seconds
from tgsignin.core.store import AI_KEYWORD_LOG_LIMIT, load_state, record_ai_keywords

ACCOUNT = AccountConfig(key="acc1", label="账号1", phone="+10000000000")
TARGETS = [
    BotTarget(
        account_key="acc1",
        bot_username=f"@bot{index}",
        sign_type=SIGN_TYPE_COMMAND,
        action_text="/checkin",
    )
    for index in range(4)
]


class _FakeClient:
    """最小可用的 TelegramClient 替身。"""

    def __init__(self) -> None:
        self.connected = False

    async def connect(self) -> None:
        """标记已连接。"""
        self.connected = True

    async def is_user_authorized(self) -> bool:
        """始终返回已登录。"""
        return True

    async def disconnect(self) -> None:
        """标记已断开。"""
        self.connected = False


class TestConcurrencyHelpers(unittest.TestCase):
    """并发相关的纯函数。"""

    def test_flood_wait_seconds(self) -> None:
        """能从错误文本里提取 FloodWait 秒数，普通错误返回 None。"""
        self.assertEqual(
            _flood_wait_seconds("FloodWaitError: A wait of 42 seconds is required"), 42
        )
        self.assertEqual(_flood_wait_seconds("FLOOD_WAIT_5"), 5)
        self.assertIsNone(_flood_wait_seconds("随便一个错误"))
        self.assertIsNone(_flood_wait_seconds(""))

    def test_jitter_bounds(self) -> None:
        """抖动随序号增长但不超过上限。"""
        self.assertEqual(_jitter_seconds(0), 0)
        self.assertGreater(_jitter_seconds(1), 0)
        self.assertLessEqual(_jitter_seconds(99), 0.3)


class TestRunAccountConcurrency(unittest.TestCase):
    """账号内并发行为。"""

    def _run(self, concurrency: int):
        """
        跑一次 run_account（打桩客户端与单目标执行）。

        :param concurrency: 并发上限
        :return tuple: ``(结果, 账号级错误, 统计)``
        """

        stats = {"inflight": 0, "max_inflight": 0, "calls": 0}

        async def _fake_target(client, target, data_dir, account, *args, **kwargs):
            stats["calls"] += 1
            stats["inflight"] += 1
            stats["max_inflight"] = max(stats["max_inflight"], stats["inflight"])
            # 单目标耗时明显大于抖动（真实场景是 15 秒等待），才能观察到并发
            await asyncio.sleep(0.3)
            stats["inflight"] -= 1
            return {
                "bot": target.bot_username,
                "account": account.key,
                "ok": True,
                "status": "签到成功",
                "time": "2026-10-08 09:00:00",
            }

        with mock.patch.object(
            signin_mod, "build_client", return_value=_FakeClient()
        ), mock.patch.object(signin_mod, "_signin_target", _fake_target):
            results, error = asyncio.run(
                signin_mod.run_account(
                    ACCOUNT, TARGETS, Path("/tmp"), None, concurrency=concurrency
                )
            )
        return results, error, stats

    def test_serial_when_concurrency_is_one(self) -> None:
        """concurrency=1：严格串行（与 1.0.x 行为一致）。"""
        results, error, stats = self._run(1)
        self.assertEqual(error, "")
        self.assertEqual(stats["max_inflight"], 1)
        self.assertEqual(stats["calls"], len(TARGETS))
        self.assertEqual([r["bot"] for r in results], [t.bot_username for t in TARGETS])

    def test_parallel_when_concurrency_above_one(self) -> None:
        """concurrency=3：并发确实发生且不超过上限，结果仍按原顺序。"""
        results, error, stats = self._run(3)
        self.assertEqual(error, "")
        self.assertGreaterEqual(stats["max_inflight"], 2)
        self.assertLessEqual(stats["max_inflight"], 3)
        self.assertEqual(stats["calls"], len(TARGETS))
        self.assertEqual([r["bot"] for r in results], [t.bot_username for t in TARGETS])

    def test_flood_wait_retries_once(self) -> None:
        """命中 FloodWait：退避后重试一次，最终成功。"""
        calls = {"n": 0}

        async def _fake_target(client, target, data_dir, account, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return {
                    "bot": target.bot_username,
                    "account": account.key,
                    "ok": False,
                    "error": "FloodWaitError: A wait of 1 seconds is required",
                    "time": "2026-10-08 09:00:00",
                }
            return {
                "bot": target.bot_username,
                "account": account.key,
                "ok": True,
                "status": "签到成功",
                "time": "2026-10-08 09:00:00",
            }

        with mock.patch.object(
            signin_mod, "build_client", return_value=_FakeClient()
        ), mock.patch.object(signin_mod, "_signin_target", _fake_target):
            results, _ = asyncio.run(
                signin_mod.run_account(
                    ACCOUNT, TARGETS[:1], Path("/tmp"), None, concurrency=2
                )
            )
        self.assertEqual(calls["n"], 2)
        self.assertTrue(results[0]["ok"])

    def test_run_all_passes_concurrency(self) -> None:
        """run_all 把并发上限透传给 run_account。"""
        captured = {}

        async def _fake_run_account(account, targets, data_dir, proxy, **kwargs):
            captured.update(kwargs)
            return [], ""

        with mock.patch.object(signin_mod, "run_account", _fake_run_account):
            asyncio.run(
                signin_mod.run_all(
                    [ACCOUNT], TARGETS, Path("/tmp"), None, concurrency=3
                )
            )
        self.assertEqual(captured.get("concurrency"), 3)


class TestAiKeywordAuditLog(unittest.TestCase):
    """审计日志的落盘与截断。"""

    def test_records_and_truncates(self) -> None:
        """新增词会被记录；超过保留条数时只留最近的。"""
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            state = record_ai_keywords(
                data_dir,
                [
                    {
                        "time": "2026-10-08 09:00:00",
                        "account": "acc1",
                        "bot": "@HDHaven_Bot",
                        "verdict": "failure",
                        "keyword": "服务暂不可用",
                    }
                ],
            )
            self.assertEqual(len(state["ai_keyword_log"]), 1)
            self.assertEqual(
                load_state(data_dir)["ai_keyword_log"][0]["keyword"], "服务暂不可用"
            )
            # 空输入不改变已有记录
            again = record_ai_keywords(data_dir, [])
            self.assertEqual(len(again["ai_keyword_log"]), 1)
            # 超限截断
            many = [
                {
                    "time": "t",
                    "account": "acc1",
                    "bot": "@b",
                    "verdict": "success",
                    "keyword": f"词{index}",
                }
                for index in range(AI_KEYWORD_LOG_LIMIT + 10)
            ]
            trimmed = record_ai_keywords(data_dir, many)
            self.assertEqual(len(trimmed["ai_keyword_log"]), AI_KEYWORD_LOG_LIMIT)


class TestPluginAutofill(unittest.TestCase):
    """插件侧写回：只增不删 + 写入前查重（用户 2026-10-08 明确要求）。"""

    def setUp(self) -> None:
        """构造插件实例并打桩持久化与数据目录。"""
        self._tmp = tempfile.TemporaryDirectory()
        self.plugin = TgSignin()
        self.plugin._ai_keyword_autofill = True
        self.plugin._success_keywords = ["签到成功"]
        self.plugin._repeated_keywords = ["已签到"]
        self.plugin._failure_keywords = ["暂不可用"]
        self.plugin._raw_config = {}
        self.persisted: dict = {}
        self.plugin.update_config = lambda payload: self.persisted.update(payload)
        self.plugin.get_data_path = lambda: Path(self._tmp.name)

    def tearDown(self) -> None:
        """清理临时数据目录。"""
        self._tmp.cleanup()

    def _results(self):
        """构造一轮带 AI 结论与候选词的结果。"""
        return [
            {
                "account": "acc1",
                "bot": "@HDHaven_Bot",
                "ai_verdict": "failure",
                # 第 1 个已在失败栏、第 2 个与成功栏重复、第 3 个是新的
                "ai_keywords": ["暂不可用", "签到成功", "服务开小差"],
                "time": "2026-10-08 09:00:00",
            },
            {
                "account": "acc1",
                "bot": "@bb_emby_bot",
                "ai_verdict": "success",
                "ai_keywords": ["签到成功", "获得积分"],
                "time": "2026-10-08 09:00:00",
            },
            {
                "account": "acc1",
                "bot": "@okemby_bot",
                "ai_verdict": "repeated",
                "ai_keywords": ["今天已签到啦"],
                "time": "2026-10-08 09:00:00",
            },
        ]

    def test_only_new_words_are_appended(self) -> None:
        """只增不删；本栏已有与其它栏已有的候选都被跳过。"""
        audit = self.plugin._apply_ai_keywords(self._results())
        self.assertEqual(self.plugin._failure_keywords, ["暂不可用", "服务开小差"])
        self.assertEqual(self.plugin._success_keywords, ["签到成功", "获得积分"])
        self.assertEqual(self.plugin._repeated_keywords, ["已签到", "今天已签到啦"])
        self.assertEqual(
            {item["keyword"] for item in audit},
            {"今天已签到啦", "获得积分", "服务开小差"},
        )

    def test_persists_and_logs(self) -> None:
        """有新增时持久化配置（含三栏词表）并写审计。"""
        self.plugin._apply_ai_keywords(self._results())
        self.assertIn("服务开小差", self.persisted.get("failure_keywords", ""))
        self.assertIn("获得积分", self.persisted.get("success_keywords", ""))
        self.assertIn("今天已签到啦", self.persisted.get("repeated_keywords", ""))
        log = load_state(Path(self._tmp.name))["ai_keyword_log"]
        self.assertEqual(len(log), 3)
        self.assertEqual({item["verdict"] for item in log}, {"failure", "success", "repeated"})

    def test_idempotent_when_no_new_words(self) -> None:
        """候选词都已存在时不写配置、不记审计。"""
        same = [
            {
                "account": "acc1",
                "bot": "@HDHaven_Bot",
                "ai_verdict": "failure",
                "ai_keywords": ["暂不可用"],
                "time": "t",
            }
        ]
        audit = self.plugin._apply_ai_keywords(same)
        self.assertEqual(audit, [])
        self.assertEqual(self.persisted, {})
        self.assertEqual(load_state(Path(self._tmp.name))["ai_keyword_log"], [])

    def test_disabled_switch_does_nothing(self) -> None:
        """开关关闭时完全不处理（默认关闭）。"""
        self.plugin._ai_keyword_autofill = False
        self.assertEqual(self.plugin._apply_ai_keywords(self._results()), [])
        self.assertEqual(self.plugin._failure_keywords, ["暂不可用"])
        self.assertEqual(self.persisted, {})

    def test_ignores_results_without_verdict(self) -> None:
        """没有 ai_verdict（未确认）的结果不参与归纳。"""
        audit = self.plugin._apply_ai_keywords(
            [
                {
                    "account": "acc1",
                    "bot": "@x",
                    "ai_state": "unknown",
                    "ai_keywords": ["随便聊聊"],
                    "time": "t",
                }
            ]
        )
        self.assertEqual(audit, [])
        self.assertEqual(self.persisted, {})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
