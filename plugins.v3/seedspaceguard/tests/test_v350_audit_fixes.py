# -*- coding: utf-8 -*-
"""
v3.5.0 审计修复回归：等待口径、零增长判据、并发可见性、锁作用域、文案互斥。

背景（2026-11-05 审计）：累计口径改完后仍留着五处结构性问题——

1. 判定改用累计值后，**等待时长也还在用累计值**，尾轮只删几 GB 也要按最大
   窗口空等十几分钟
2. 「零增长」只看绝对值 1GB，删 100GB 只回收 1GB 与删 1GB 回收 1GB 得到
   同一结论
3. 并发触发只回一句「已有清理任务正在运行」，无日志、不说跑了多久
4. 互斥锁挂在类上，插件的多个分身会互相阻塞
5. 「已提前停止」与「已取消」两段文案会同时出现
"""

import os
import threading
import time
import unittest
from unittest import mock

import tests  # noqa: F401  触发宿主桩路径注入

from seedspaceguard import GIB, RELEASE_STALL_MIN_RATIO, SeedSpaceGuard
from tests.test_clean_flow_e2e import _E2EBase


class _NoGrowthBase(unittest.TestCase):
    """判定单元用例的公共夹具。"""

    def _plugin(self):
        plugin = SeedSpaceGuard()
        plugin._sync_wait_seconds = 90
        return plugin

    @staticmethod
    def _orphan_patch(plugin):
        return mock.patch.object(
            plugin, "_reap_orphan_seeds",
            return_value={"torrent": 0, "checked": 0, "alive": 0,
                          "failed": 0, "_lines": []},
        )

    @staticmethod
    def _sweep_patch(plugin):
        return mock.patch.object(
            plugin, "_sweep_empty_dirs", return_value={"dirs": 0, "_lines": []}
        )


class TestNoGrowthRatio(_NoGrowthBase):
    """「零增长」必须按本轮名义量的比例判定，而不是一个固定绝对值。"""

    def test_large_round_with_tiny_gain_is_zero_growth(self):
        p = self._plugin()
        verdict = p._release_verdict(
            95 * GIB, 200 * GIB, 94 * GIB, 0, 100 * GIB
        )
        self.assertTrue(verdict["stalled"])
        self.assertTrue(
            verdict["stop"],
            "删 100GB 只回收 1GB 属于零增长，应停手而不是继续删",
        )

    def test_small_round_with_same_gain_is_growth(self):
        p = self._plugin()
        verdict = p._release_verdict(
            95 * GIB, 200 * GIB, 94 * GIB, 0, 2 * GIB
        )
        self.assertTrue(verdict["stalled"])
        self.assertFalse(
            verdict["stop"],
            "同一 1GB 增益在小批量轮次里已超过本轮名义的 10%，不算零增长",
        )
        self.assertEqual(verdict["stall_rounds"], 1)

    def test_ratio_constant_is_meaningful(self):
        self.assertGreater(RELEASE_STALL_MIN_RATIO, 0.0)
        self.assertLess(RELEASE_STALL_MIN_RATIO, 1.0)


class TestSmallRoundExemption(_NoGrowthBase):
    """
    小批量豁免必须看「本轮」名义量。

    用累计量做豁免等于永不触发：累计值一轮就超过 1GB，这条保护形同死代码。
    """

    def test_small_round_is_exempt(self):
        self.assertTrue(
            self._plugin()._release_is_healthy(0, 200 * GIB, GIB // 2),
            "本轮只删了 0.5GB，测量噪声不可信，不得据此判停",
        )

    def test_normal_round_is_not_exempt(self):
        self.assertFalse(
            self._plugin()._release_is_healthy(0, 200 * GIB, 20 * GIB),
        )


class TestRoundScopedWait(_NoGrowthBase, _E2EBase):
    """等待时长与等待目标取「本轮新增名义量」，不取累计值。"""

    def test_later_round_waits_on_its_own_nominal(self):
        for index, age in enumerate((40, 39, 38, 37)):
            self.make_file(
                os.path.join(self.dl, f"w{index}.mkv"), 6 * 1024 * 1024, age
            )
        self.configure([self.dl], threshold=15, sync_wait=90)

        seen = []

        def fake_wait(_free_before, nominal):
            seen.append(int(nominal))
            # 第一轮回收 11GB，第二轮回收 5GB
            return 11 * GIB if len(seen) == 1 else 5 * GIB

        # 3GB → 第一轮后 14.5GB（仍低于阈值）→ 第二轮后 20GB（达标）
        with self.patch_free([3 * GIB, 3 * GIB, int(14.5 * GIB), 20 * GIB]), \
                mock.patch.object(self.plugin, "_wait_for_release",
                                  side_effect=fake_wait), \
                self._orphan_patch(self.plugin), \
                self._sweep_patch(self.plugin):
            self.plugin.check_and_clean(source="手动")

        self.assertEqual(len(seen), 2, "两轮都应等待一次")
        self.assertEqual(seen[0], 12 * GIB, "首轮名义量为 2 个 6GB 文件")
        self.assertEqual(
            seen[1], 6 * GIB,
            "次轮只删 1 个 6GB 文件，不得按累计 18GB 取等待窗口",
        )


class TestBusyIsVisible(unittest.TestCase):
    """并发触发必须说明「谁在跑、跑了多久」，而不是静默跳过。"""

    def test_lock_is_instance_scoped(self):
        first, second = SeedSpaceGuard(), SeedSpaceGuard()
        self.assertIsNot(
            first._lock, second._lock,
            "锁必须是实例级：插件分身之间不该互相阻塞",
        )
        first._lock.acquire()
        try:
            self.assertTrue(
                second._lock.acquire(blocking=False),
                "一个分身持锁不应挡住另一个分身",
            )
            second._lock.release()
        finally:
            first._lock.release()

    def test_busy_message_states_elapsed(self):
        plugin = SeedSpaceGuard()
        plugin._enabled = True
        plugin._lock.acquire()
        plugin._run_started_at = time.time() - 125
        try:
            text = plugin.check_and_clean(source="手动")
        finally:
            plugin._lock.release()
            plugin._run_started_at = 0.0

        self.assertIn("仍在运行", text)
        self.assertIn("2 分 5 秒", text, "要说出已运行时长，便于判断是否卡死")

    def test_started_at_is_cleared_after_run(self):
        plugin = SeedSpaceGuard()
        plugin._run_started_at = 12345.0
        plugin._enabled = False
        plugin.check_and_clean(source="手动")
        self.assertEqual(
            plugin._run_started_at, 12345.0,
            "未进入清理流程（插件未启用）时不得改动计时状态",
        )

    def test_started_at_cleared_after_real_run(self):
        """真正进了清理流程后必须清掉计时，否则下一次回执会报错的等待时长。"""
        plugin = SeedSpaceGuard()
        plugin._enabled = True
        plugin._notify = False
        plugin._threshold_gb = 0
        with mock.patch.object(plugin, "_disk_free_bytes",
                               return_value=10 * GIB):
            plugin.check_and_clean(source="手动")
        self.assertEqual(plugin._run_started_at, 0.0)
        self.assertFalse(plugin._running)


class TestCancelWording(_NoGrowthBase, _E2EBase):
    """「已取消」与「空间未按预期释放」互斥：前者是用户动作，不是故障。"""

    def test_both_flags_render_cancel_only(self):
        """
        两个标记同时为真时只报「已取消」。

        取消后停手判定会被跳过，现实中很少同时出现；但只要有人把两段文案
        写成并列判断就会同时渲染，用户看到「已取消 + 空间未按预期释放」
        会以为插件一边被自己停、一边又报故障。
        """
        plugin = SeedSpaceGuard()
        plugin._clean_stats = {"cancelled": True, "stalled": True}
        text = plugin._stop_reason_suffix()
        self.assertIn("已取消", text)
        self.assertNotIn("空间未按预期释放", text)

    def test_stall_only_reports_stall(self):
        plugin = SeedSpaceGuard()
        plugin._clean_stats = {"cancelled": False, "stalled": True}
        self.assertIn("空间未按预期释放", plugin._stop_reason_suffix())

    def test_no_flag_no_suffix(self):
        plugin = SeedSpaceGuard()
        plugin._clean_stats = {"cancelled": False, "stalled": False}
        self.assertEqual(plugin._stop_reason_suffix(), "")

    def test_cancel_does_not_render_stall_wording(self):
        for index, age in enumerate((40, 39)):
            self.make_file(
                os.path.join(self.dl, f"x{index}.mkv"), 6 * 1024 * 1024, age
            )
        self.configure([self.dl], threshold=15, sync_wait=1)

        def fake_wait(_free_before, _nominal):
            # 每轮清理启动时会新建取消事件，必须取当前这一个再置位
            self.plugin._cancel_signal().set()
            return 0

        with self.patch_free([3 * GIB]), \
                mock.patch.object(self.plugin, "_wait_for_release",
                                  side_effect=fake_wait), \
                self._orphan_patch(self.plugin), \
                self._sweep_patch(self.plugin):
            msg = self.plugin.check_and_clean(source="手动")

        self.assertIn("已取消", msg)
        self.assertNotIn(
            "空间未按预期释放", msg,
            "用户主动停止时不得叠加故障告警文案",
        )


class TestStopServiceInterruptsWait(unittest.TestCase):
    """停用插件必须立刻打断正在放大的等待窗口。"""

    def test_stop_service_sets_cancel_signal(self):
        plugin = SeedSpaceGuard()
        plugin._run_cancel_event = threading.Event()
        plugin.stop_service()
        self.assertTrue(plugin._cancelled())

    def test_wait_exits_after_stop_service(self):
        plugin = SeedSpaceGuard()
        # 基础值取 3 秒（放大后约 18 秒）：足够区分「响应取消」与「等满窗口」，
        # 又不会让变异测试在「没响应取消」的变异体上耗尽时间预算
        plugin._sync_wait_seconds = 3
        plugin._run_cancel_event = threading.Event()
        plugin._disk_free_bytes = lambda: 1 * GIB
        plugin.stop_service()

        started = time.monotonic()
        plugin._wait_for_release(1 * GIB, 200 * GIB)
        self.assertLess(
            time.monotonic() - started, 3.0,
            "停用后不得继续空等窗口结束",
        )


class TestEmptyEaDirFailureLogged(unittest.TestCase):
    """空的 @eaDir 删不掉时必须留痕，不能静默吞掉。"""

    def test_rmdir_failure_logs_warning(self):
        import tempfile

        from app.runtime.log import logger as plugin_logger

        base = tempfile.mkdtemp(prefix="ssg-ea-")
        try:
            ea_dir = os.path.join(base, "@eaDir")
            os.makedirs(ea_dir)
            plugin = SeedSpaceGuard()
            with mock.patch(
                "seedspaceguard.os.rmdir", side_effect=OSError("busy")
            ):
                with self.assertLogs(plugin_logger, level="WARNING") as captured:
                    removed = plugin._purge_syno_index(base)
            self.assertEqual(removed, 0)
            self.assertTrue(
                any("@eaDir" in line for line in captured.output),
                "失败原因必须进日志，否则「目录已空却删不掉」无从追查",
            )
        finally:
            import shutil

            shutil.rmtree(base, ignore_errors=True)


class TestApiReportsSkip(unittest.TestCase):
    """API 手动触发被跳过时必须回报 success=False，否则前端当成跑成功了。"""

    class _Request:
        query_params: dict = {}

        async def json(self):
            return {}

    def test_busy_api_call_is_not_reported_as_success(self):
        import asyncio

        plugin = SeedSpaceGuard()
        plugin._enabled = True
        plugin._lock.acquire()
        plugin._run_started_at = time.time() - 65
        try:
            payload = asyncio.run(plugin.api_run(self._Request()))
        finally:
            plugin._lock.release()
            plugin._run_started_at = 0.0

        self.assertFalse(payload["success"])
        self.assertIn("仍在运行", payload["message"])


if __name__ == "__main__":
    unittest.main()
