# -*- coding: utf-8 -*-
"""
备份数量兜底（v3.4.0）的回归用例。

背景
----
定时表达式被设成高频形式（如 ``* 3 * * *`` = 3 点这一小时每分钟）时，一天会
产生 60 份 31M 的包；``keep_days`` 保护又让「保留 N 份」失效，文件只增不减。
兜底策略是「停 + 问」：超过 ``max(keep_count × 3, 30)`` 就暂停自动备份并通知
用户确认，确认「正常」抬升上限继续，确认「异常」保持暂停等用户改定时。

覆盖清单
--------
1. 状态机：阈值、触发幂等、确认抬升、定时变更自动解除、份数回落自愈、
   24 小时提醒节流、状态文件损坏降级
2. 运行期：暂停拦截定时备份（不发失败通知）、手动备份放行、超量触发并带按钮
3. 交互：按钮回调解析与去插件串扰、API 决策、详情页告警块
4. 初始化：改过定时自动恢复、份数已回落自动恢复
5. 清理：硬上限忽略保留天数保护（止血）
"""

import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import tests  # noqa: F401  触发宿主桩路径注入

from configbackup import ConfigBackup
from configbackup.guard import (
    ACK_GROWTH,
    CEILING_FLOOR,
    REMIND_INTERVAL,
    BackupGuard,
    count_daily_runs,
)
from tests.test_boundary import _BoundaryBase


class TestGuardStateMachine(unittest.TestCase):
    """兜底状态机：阈值、触发、确认与自愈。"""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="cb-guard-")
        self.path = Path(self.base) / "backup_guard.json"
        self.guard = BackupGuard(self.path, keep_count=10)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def test_ceiling_uses_factor_and_floor(self):
        """上限 = max(保留个数 × 3, 30)。"""
        self.assertEqual(self.guard.base_ceiling(), 30)
        self.assertEqual(BackupGuard(self.path, keep_count=20).base_ceiling(), 60)
        self.assertEqual(BackupGuard(self.path, keep_count=1).base_ceiling(), CEILING_FLOOR)

    def test_exceeded_boundary(self):
        """正好等于上限不触发，多一份才触发。"""
        self.assertFalse(self.guard.exceeded(30))
        self.assertTrue(self.guard.exceeded(31))

    def test_trip_persists_and_is_idempotent(self):
        """触发写盘，重复触发不再改写（避免每次运行都刷通知时间）。"""
        self.assertTrue(self.guard.trip(31, "* 3 * * *", now=1000.0))
        self.assertFalse(self.guard.trip(99, "* 3 * * *", now=1001.0))

        reloaded = BackupGuard(self.path, keep_count=10)
        reloaded.load()
        self.assertTrue(reloaded.state.paused)
        self.assertEqual(reloaded.state.count, 31)
        self.assertEqual(reloaded.state.cron, "* 3 * * *")
        self.assertEqual(reloaded.state.notified_at, 1000.0)

    def test_acknowledge_raises_ceiling_and_resumes(self):
        """确认「正常」：解除暂停并把上限抬到当前份数之上。"""
        self.guard.trip(63, "* 3 * * *")
        ceiling = self.guard.acknowledge_ok()

        self.assertFalse(self.guard.state.paused)
        self.assertGreaterEqual(ceiling, int(63 * ACK_GROWTH))
        self.assertEqual(self.guard.ceiling(), ceiling)
        self.assertFalse(self.guard.exceeded(63), "抬升后同一份数不应再次触发")

    def test_mark_schedule_fix_keeps_paused(self):
        """确认「异常」：保持暂停并等待改定时。"""
        self.guard.trip(31, "* 3 * * *")
        self.guard.mark_schedule_fix()

        self.assertTrue(self.guard.state.paused)
        self.assertTrue(self.guard.state.needs_schedule_fix)

    def test_sync_schedule_resumes_on_change(self):
        """定时表达式一变就自动解除；未变不解除。"""
        self.guard.trip(31, "* 3 * * *")

        self.assertFalse(self.guard.sync_schedule("* 3 * * *"))
        self.assertTrue(self.guard.state.paused)
        self.assertTrue(self.guard.sync_schedule("0 3 * * *"))
        self.assertFalse(self.guard.state.paused)

    def test_self_heal_only_within_ceiling(self):
        """份数仍在上限之上时不自愈，回落后才自愈。"""
        self.guard.trip(63, "* 3 * * *")

        self.assertFalse(self.guard.self_heal(40))
        self.assertTrue(self.guard.state.paused)
        self.assertTrue(self.guard.self_heal(20))
        self.assertFalse(self.guard.state.paused)

    def test_remind_interval_is_24h(self):
        """提醒按 24 小时节流。"""
        self.guard.trip(31, "* 3 * * *", now=1000.0)

        self.assertFalse(self.guard.should_remind(now=1000.0 + REMIND_INTERVAL - 1))
        self.assertTrue(self.guard.should_remind(now=1000.0 + REMIND_INTERVAL))
        self.guard.note_reminded(now=1000.0 + REMIND_INTERVAL)
        self.assertEqual(self.guard.state.remind_count, 1)
        self.assertFalse(self.guard.should_remind(now=1000.0 + REMIND_INTERVAL + 10))

    def test_corrupt_state_falls_back(self):
        """状态文件损坏时退化为未暂停，不影响备份。"""
        self.path.write_text("{not json", encoding="utf-8")
        self.guard.load()

        self.assertFalse(self.guard.state.paused)
        self.assertEqual(self.guard.ceiling(), 30)

    def test_save_uses_atomic_replace(self):
        """写状态不留 .tmp 残file，且文件可被重新解析。"""
        self.guard.trip(31, "0 3 * * *")

        self.assertTrue(self.path.exists())
        self.assertFalse(self.path.with_name(self.path.name + ".tmp").exists())
        self.assertTrue(json.loads(self.path.read_text(encoding="utf-8"))["paused"])

    def test_count_daily_runs(self):
        """cron 频率换算供通知文案使用。"""
        self.assertEqual(count_daily_runs("0 3 * * *"), 1)
        self.assertEqual(count_daily_runs("* 3 * * *"), 60)
        self.assertEqual(count_daily_runs("* * * * *"), 200, "高频表达式命中迭代上限")
        self.assertEqual(count_daily_runs("bad cron"), 0)


class _PluginGuardBase(_BoundaryBase):
    """插件级夹具：挂一个已加载的兜底状态机。"""

    def setUp(self):
        super().setUp()
        self.plugin._keep_count = 10
        self.plugin._keep_days = 7
        self.plugin._cron = "0 3 * * *"
        self.guard = BackupGuard(self.data_path / "backup_guard.json", keep_count=10)
        self.guard.load()
        self.plugin._guard = self.guard

    @property
    def data_path(self) -> Path:
        """插件数据目录（与 ``_stub_data_path`` 一致）。"""
        return Path(self.base) / "data"

    def add_backups(self, count: int, offset: int = 0):
        """在备份目录里造 count 份合法备份包。"""
        for index in range(offset, offset + count):
            self.make_backup(f"20261106{index:06d}")

    def make_plugin_config(self, **overrides):
        """构造一份完整插件配置，便于 init_plugin 走真实解析。"""
        config = {
            "enabled": True,
            "cron": "0 3 * * *",
            "backup_dir": self.backup_dir,
            "keep_count": 10,
            "keep_days": 7,
            "backup_parts": ["system"],
        }
        config.update(overrides)
        return config


class TestPluginGuardRuntime(_PluginGuardBase):
    """运行期行为：拦截、放行、触发与通知。"""

    def test_paused_blocks_scheduled_backup(self):
        """暂停后定时备份被拦住，且不再发「备份失败」通知。"""
        self.guard.trip(31, self.plugin._cron)

        with mock.patch.object(
            ConfigBackup, "_ConfigBackup__run_backup", return_value=(True, "ok")
        ) as run_backup:
            ok, msg = self.plugin._ConfigBackup__backup()

        self.assertFalse(ok)
        self.assertIn("已暂停", msg)
        run_backup.assert_not_called()
        self.assertEqual(getattr(self.plugin, "_stub_messages", []), [])

    def test_paused_reminds_after_24h(self):
        """暂停满 24 小时后补一条提醒。"""
        self.guard.trip(31, self.plugin._cron, now=time.time() - REMIND_INTERVAL - 1)

        with mock.patch.object(ConfigBackup, "_ConfigBackup__run_backup", return_value=(True, "ok")):
            self.plugin._ConfigBackup__backup()

        messages = self.plugin._stub_messages
        self.assertEqual(len(messages), 1)
        self.assertIn("仍已暂停", messages[0][1]["title"])
        self.assertEqual(self.guard.state.remind_count, 1)

    def test_overflow_trips_and_notifies_with_buttons(self):
        """份数超过上限时暂停并发出带两个按钮的通知。"""
        self.add_backups(31)

        tripped = self.plugin._ConfigBackup__check_backup_overflow()

        self.assertTrue(tripped)
        self.assertTrue(self.guard.state.paused)
        kwargs = self.plugin._stub_messages[-1][1]
        self.assertIn("已暂停", kwargs["title"])
        callbacks = [button["callback_data"] for button in kwargs["buttons"][0]]
        self.assertEqual(
            callbacks,
            ["[PLUGIN]ConfigBackup|cbg|ok", "[PLUGIN]ConfigBackup|cbg|fix"],
        )

    def test_overflow_not_tripped_before_threshold(self):
        """正好 30 份不触发。"""
        self.add_backups(30)

        self.assertFalse(self.plugin._ConfigBackup__check_backup_overflow())
        self.assertFalse(self.guard.state.paused)

    def test_overflow_silent_when_already_paused(self):
        """已暂停时不重复推送。"""
        self.add_backups(31)
        self.guard.trip(31, self.plugin._cron)
        self.plugin._stub_messages = []

        self.assertFalse(self.plugin._ConfigBackup__check_backup_overflow())
        self.assertEqual(self.plugin._stub_messages, [])

    def test_manual_backup_bypasses_pause(self):
        """手动备份不受闸门限制。"""
        self.guard.trip(31, self.plugin._cron)

        with mock.patch.object(
            ConfigBackup, "_ConfigBackup__run_backup", return_value=(True, "ok")
        ) as run_backup:
            result = self.plugin.api_backup()

        self.assertTrue(result["success"], result["message"])
        run_backup.assert_called_once()
        self.assertTrue(self.guard.state.paused, "手动备份不应自动解除暂停")

    def test_successful_scheduled_backup_checks_overflow(self):
        """定时备份成功后要跑一次数量判定。"""
        self.add_backups(31)

        with mock.patch.object(ConfigBackup, "_ConfigBackup__run_backup", return_value=(True, "ok")):
            self.plugin._ConfigBackup__backup()

        self.assertTrue(self.guard.state.paused)


class TestPluginGuardInteraction(_PluginGuardBase):
    """交互：按钮回调、API 与详情页。"""

    def test_parse_callback_variants(self):
        """只认本插件的 cbg 载荷。"""
        parse = ConfigBackup._ConfigBackup__parse_guard_callback

        self.assertEqual(parse("cbg|ok"), "ok")
        self.assertEqual(parse("[PLUGIN]ConfigBackup|cbg|fix"), "fix")
        self.assertEqual(parse("CALLBACK:cbg|resume"), "resume")
        self.assertIsNone(parse("cbg|nope"))
        self.assertIsNone(parse("other|ok"))
        self.assertIsNone(parse(""))

    def test_message_action_applies_decision(self):
        """按钮点「数量正常」后恢复并回执。"""
        self.guard.trip(31, self.plugin._cron)
        event = SimpleNamespace(
            event_data={"plugin_id": "configbackup", "text": "cbg|ok", "userid": "1"}
        )

        self.plugin.message_action(event)

        self.assertFalse(self.guard.state.paused)
        self.assertIn("已恢复", self.plugin._stub_messages[-1][1]["text"])

    def test_message_action_ignores_other_plugin(self):
        """别的插件的回调不能被吃掉。"""
        self.guard.trip(31, self.plugin._cron)
        event = SimpleNamespace(event_data={"plugin_id": "other", "text": "cbg|ok"})

        self.plugin.message_action(event)

        self.assertTrue(self.guard.state.paused)

    def test_api_guard_status_and_decisions(self):
        """API 查询与三种决策。"""
        status = self.plugin.api_guard()
        self.assertTrue(status["success"])
        self.assertFalse(status["data"]["paused"])
        self.assertEqual(status["data"]["ceiling"], 30)

        self.guard.trip(31, self.plugin._cron)
        ok = self.plugin.api_guard("ok")
        self.assertTrue(ok["success"])
        self.assertFalse(self.guard.state.paused)
        self.assertGreater(self.guard.ceiling(), 31)

        self.guard.trip(31, self.plugin._cron)
        fix = self.plugin.api_guard("fix")
        self.assertTrue(fix["success"])
        self.assertTrue(self.guard.state.paused)
        self.assertTrue(self.guard.state.needs_schedule_fix)

        resume = self.plugin.api_guard("resume")
        self.assertTrue(resume["success"])
        self.assertFalse(self.guard.state.paused)

    def test_api_guard_rejects_unknown_decision(self):
        """未知决策不改变状态。"""
        self.guard.trip(31, self.plugin._cron)

        result = self.plugin.api_guard("boom")

        self.assertFalse(result["success"])
        self.assertTrue(self.guard.state.paused)

    def test_page_shows_pause_alert_and_buttons(self):
        """详情页在暂停时给出告警与两个操作入口。"""
        self.guard.trip(31, "* 3 * * *")

        page_text = json.dumps(self.plugin.get_page(), ensure_ascii=False)

        self.assertIn("自动备份已暂停", page_text)
        self.assertIn('"decision": "ok"', page_text)
        self.assertIn('"decision": "fix"', page_text)


class TestPluginGuardInit(_PluginGuardBase):
    """初始化：改过定时或份数回落时自动恢复。"""

    def test_init_plugin_resumes_on_schedule_change(self):
        """用户把 cron 改了 → 自动解除暂停。"""
        # 份数仍超限，只有「定时变更」这一条路径能解除（自愈分支不会命中）
        self.add_backups(31)
        self.guard.trip(31, "* 3 * * *")

        self.plugin.init_plugin(self.make_plugin_config(cron="0 3 * * *"))

        self.assertIsNotNone(self.plugin._guard)
        self.assertFalse(self.plugin._guard.state.paused)

    def test_init_plugin_self_heals_when_within_ceiling(self):
        """份数已回落到上限以内 → 自动解除。"""
        self.add_backups(3)
        self.guard.trip(31, self.plugin._cron)

        self.plugin.init_plugin(self.make_plugin_config())

        self.assertFalse(self.plugin._guard.state.paused)

    def test_init_plugin_keeps_pause_when_still_over(self):
        """份数仍超限且定时未改 → 保持暂停。"""
        self.add_backups(31)
        self.guard.trip(31, self.plugin._cron)

        self.plugin.init_plugin(self.make_plugin_config())

        self.assertTrue(self.plugin._guard.state.paused)

    def test_init_plugin_survives_broken_state_file(self):
        """状态文件损坏时插件照常初始化，只是不启用兜底。"""
        self.data_path.mkdir(parents=True, exist_ok=True)
        (self.data_path / "backup_guard.json").write_text("{bad", encoding="utf-8")

        self.plugin.init_plugin(self.make_plugin_config())

        self.assertIsNotNone(self.plugin._guard)
        self.assertFalse(self.plugin._guard.state.paused)


class TestCleanupHardCap(_PluginGuardBase):
    """清理硬上限：天数保护不能把份数推到无上限。"""

    def test_hard_cap_ignores_keep_days(self):
        """份数超过硬上限时忽略天数保护，按数量清理。"""
        self.plugin._keep_count = 10
        self.plugin._keep_days = 36500
        self.add_backups(45)

        deleted = self.plugin._ConfigBackup__clean_old_backups(Path(self.backup_dir))
        left = self.plugin._ConfigBackup__iter_local_backups(Path(self.backup_dir))

        self.assertEqual(deleted, 5)
        self.assertEqual(len(left), 40)

    def test_below_hard_cap_still_respects_keep_days(self):
        """未超过硬上限时仍按「7 天内不删」。"""
        self.plugin._keep_count = 10
        self.plugin._keep_days = 36500
        self.add_backups(12)

        deleted = self.plugin._ConfigBackup__clean_old_backups(Path(self.backup_dir))

        self.assertEqual(deleted, 0)


if __name__ == "__main__":
    unittest.main()
