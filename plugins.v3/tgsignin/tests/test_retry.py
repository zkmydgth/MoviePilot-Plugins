"""
失败重试窗口判定测试（core/retry.py）。

覆盖：从未尝试不重试、到点重试、未到间隔不重试、成功不重试、
跨自然日重置、间隔 0 关闭、停用目标跳过，以及尝试次数累计。
"""

import unittest
from datetime import datetime, timedelta, timezone

import tests  # noqa: F401  触发宿主桩与插件路径注入

from tgsignin.core.config import BotTarget
from tgsignin.core.retry import (
    evaluate_retry,
    record_attempts,
    retry_key,
    signed_today,
    today_text,
)

NOW = datetime(2026, 10, 7, 15, 0, tzinfo=timezone(timedelta(hours=8)))


def _target(bot: str = "@okemby_bot", enabled: bool = True) -> BotTarget:
    """
    构造一个签到目标。

    :param bot: bot 用户名
    :param enabled: 是否启用
    :return BotTarget: 目标
    """

    return BotTarget(
        account_key="acc1",
        bot_username=bot,
        sign_type="button",
        action_text="签到",
        enabled=enabled,
    )


def _failed_state(seconds_ago: int, date: str = "2026-10-07") -> dict:
    """
    构造一份「今天失败过」的状态。

    :param seconds_ago: 距离现在多少秒前尝试过
    :param date: 记录所属日期
    :return dict: 状态字典
    """

    target = _target()
    return {
        "signin_state": {
            retry_key(target): {
                "date": date,
                "ok": False,
                "attempts": 1,
                "last_attempt_at": (NOW - timedelta(seconds=seconds_ago)).timestamp(),
                "last_status": "失败",
            }
        }
    }


class TestEvaluateRetry(unittest.TestCase):
    """重试窗口判定。"""

    def test_never_attempted_not_due(self) -> None:
        """从未尝试过（无记录）不重试。"""
        due, state = evaluate_retry([_target()], {}, now=NOW)
        self.assertEqual(due, [])
        self.assertEqual(
            state["signin_state"][retry_key(_target())], {"date": "2026-10-07"}
        )

    def test_failed_due_after_interval(self) -> None:
        """今天失败且已过 6 小时 → 到期重试。"""
        target = _target()
        due, _ = evaluate_retry(
            [target], _failed_state(7 * 3600), now=NOW, retry_interval_hours=6
        )
        self.assertEqual([item.bot_username for item in due], ["@okemby_bot"])

    def test_failed_not_due_before_interval(self) -> None:
        """未到重试间隔 → 不重试。"""
        due, _ = evaluate_retry(
            [_target()], _failed_state(2 * 3600), now=NOW, retry_interval_hours=6
        )
        self.assertEqual(due, [])

    def test_success_not_retried(self) -> None:
        """今天已成功 → 不重试。"""
        state = _failed_state(9 * 3600)
        state["signin_state"][retry_key(_target())]["ok"] = True
        due, _ = evaluate_retry([_target()], state, now=NOW)
        self.assertEqual(due, [])

    def test_new_day_resets_window(self) -> None:
        """跨自然日 → 窗口重置，等当天正常签到先跑。"""
        due, state = evaluate_retry(
            [_target()], _failed_state(60, date="2026-10-06"), now=NOW
        )
        self.assertEqual(due, [])
        self.assertEqual(
            state["signin_state"][retry_key(_target())], {"date": "2026-10-07"}
        )

    def test_zero_interval_disables_retry(self) -> None:
        """间隔 0 = 关闭重试。"""
        due, _ = evaluate_retry(
            [_target()], _failed_state(48 * 3600), now=NOW, retry_interval_hours=0
        )
        self.assertEqual(due, [])

    def test_disabled_target_skipped(self) -> None:
        """停用目标不参与重试。"""
        due, _ = evaluate_retry(
            [_target(enabled=False)], _failed_state(7 * 3600), now=NOW
        )
        self.assertEqual(due, [])

    def test_only_due_target_returned(self) -> None:
        """多个目标时只返回到期的那一个。"""
        due_target = _target("@okemby_bot")
        fresh_target = _target("@other_bot")
        state = _failed_state(7 * 3600)
        state["signin_state"][retry_key(fresh_target)] = {
            "date": "2026-10-07",
            "ok": False,
            "attempts": 1,
            "last_attempt_at": (NOW - timedelta(minutes=5)).timestamp(),
        }
        due, _ = evaluate_retry([due_target, fresh_target], state, now=NOW)
        self.assertEqual([item.bot_username for item in due], ["@okemby_bot"])


class TestRecordAttempts(unittest.TestCase):
    """结果写回状态。"""

    def test_accumulates_attempts_and_flags(self) -> None:
        """失败后再成功：尝试次数累计，ok 反映最近一次。"""
        target = _target()
        state = record_attempts(
            {},
            [
                {
                    "account": target.account_key,
                    "bot": target.bot_username,
                    "ok": False,
                    "status": "失败",
                }
            ],
            now=NOW,
        )
        state = record_attempts(
            state,
            [
                {
                    "account": target.account_key,
                    "bot": target.bot_username,
                    "ok": True,
                    "status": "签到成功",
                }
            ],
            now=NOW,
        )
        record = state["signin_state"][retry_key(target)]
        self.assertEqual(record["attempts"], 2)
        self.assertTrue(record["ok"])
        self.assertEqual(record["date"], "2026-10-07")
        self.assertEqual(record["last_status"], "签到成功")

    def test_new_day_starts_new_record(self) -> None:
        """跨日写回时从 1 次重新计。"""
        target = _target()
        state = {
            "signin_state": {
                retry_key(target): {
                    "date": "2026-10-06",
                    "ok": True,
                    "attempts": 3,
                    "last_attempt_at": 0,
                }
            }
        }
        state = record_attempts(
            state,
            [
                {
                    "account": target.account_key,
                    "bot": target.bot_username,
                    "ok": False,
                    "status": "失败",
                }
            ],
            now=NOW,
        )
        record = state["signin_state"][retry_key(target)]
        self.assertEqual(record["attempts"], 1)
        self.assertFalse(record["ok"])
        self.assertEqual(record["date"], "2026-10-07")

    def test_today_text(self) -> None:
        """today_text 按北京时区取日期。"""
        self.assertEqual(today_text(NOW), "2026-10-07")
        self.assertEqual(
            today_text(datetime(2026, 10, 7, 0, 30, tzinfo=timezone.utc)),
            "2026-10-07",
        )

    def test_ok_today_is_sticky(self) -> None:
        """今天成功过一次后，后续失败不清掉 ok_today（供「只回菜单」分档）。"""
        target = _target()
        state = record_attempts(
            {},
            [
                {
                    "account": target.account_key,
                    "bot": target.bot_username,
                    "ok": True,
                    "status": "签到成功",
                }
            ],
            now=NOW,
        )
        state = record_attempts(
            state,
            [
                {
                    "account": target.account_key,
                    "bot": target.bot_username,
                    "ok": False,
                    "status": "失败",
                }
            ],
            now=NOW,
        )
        record = state["signin_state"][retry_key(target)]
        self.assertFalse(record["ok"])
        self.assertTrue(record["ok_today"])


class TestSignedToday(unittest.TestCase):
    """「今天此前是否已签到成功」判定。"""

    def test_false_when_never_succeeded(self) -> None:
        """没有记录 / 没成功过 → False。"""
        target = _target()
        self.assertFalse(signed_today({}, target, now=NOW))
        state = record_attempts(
            {},
            [
                {
                    "account": target.account_key,
                    "bot": target.bot_username,
                    "ok": False,
                    "status": "失败",
                }
            ],
            now=NOW,
        )
        self.assertFalse(signed_today(state, target, now=NOW))

    def test_true_after_success(self) -> None:
        """今天成功过一次 → True。"""
        target = _target()
        state = record_attempts(
            {},
            [
                {
                    "account": target.account_key,
                    "bot": target.bot_username,
                    "ok": True,
                    "status": "签到成功",
                }
            ],
            now=NOW,
        )
        self.assertTrue(signed_today(state, target, now=NOW))

    def test_false_after_day_rolls_over(self) -> None:
        """昨天的成功不算今天。"""
        target = _target()
        state = {
            "signin_state": {
                retry_key(target): {
                    "date": "2026-10-06",
                    "ok": True,
                    "ok_today": True,
                    "attempts": 1,
                }
            }
        }
        self.assertFalse(signed_today(state, target, now=NOW))


if __name__ == "__main__":
    unittest.main()
