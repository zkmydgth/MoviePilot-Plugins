# -*- coding: utf-8 -*-
"""
v3.4.0 累计口径释放判定回归。

现场背景（2026-11-05）：一次清理删掉 238 个文件、名义 242.5GB。首轮 90 秒回收
约 197GB；第二轮 5 个文件 90 秒只回收 13.9GB，单轮口径判「未释放」而提前停手。
几分钟后剩余空间实际已到 522GB，与名义值基本一致——旧判定错在把上一轮尚未
回收完的尾部算进了本轮的账，并且固定 90 秒对上百 GB 的删除远远不够。

本文件锁定三件事：

1. 等待时长随名义删除规模放大（有上限）
2. 判定用整次清理的累计口径，单轮低释放不再直接停手
3. 摘要必须同时给出名义删除量与实测回收量
"""

import os
import threading
import time
import unittest

import tests  # noqa: F401  触发宿主桩路径注入

from seedspaceguard import (
    GIB,
    MAX_STALL_ROUNDS,
    RELEASE_WAIT_MAX_SECONDS,
    SeedSpaceGuard,
)
from tests.test_clean_flow_e2e import _E2EBase


class TestAdaptiveWait(unittest.TestCase):
    """等待时长必须随删除规模放大，同时保留上限。"""

    def _plugin(self, base):
        plugin = SeedSpaceGuard()
        plugin._sync_wait_seconds = base
        return plugin

    def test_zero_means_no_wait(self):
        self.assertEqual(
            self._plugin(0)._effective_wait_seconds(50 * GIB), 0,
            "配置为 0 表示不等待，不能自行放大",
        )

    def test_small_batch_keeps_base(self):
        self.assertEqual(
            self._plugin(90)._effective_wait_seconds(1 * GIB), 90,
            "小批量删除不应被放大等待，否则每轮都白等",
        )

    def test_scales_with_nominal(self):
        self.assertEqual(
            self._plugin(90)._effective_wait_seconds(int(22.5 * GIB)), 270,
            "22.5GB 折成两档，应放大到 90 × 3 秒",
        )
        self.assertEqual(
            self._plugin(90)._effective_wait_seconds(220 * GIB), 540,
            "220GB 应放大到倍数上限 6 倍",
        )

    def test_capped_by_max_seconds(self):
        self.assertEqual(
            self._plugin(300)._effective_wait_seconds(200 * GIB),
            RELEASE_WAIT_MAX_SECONDS,
            "放大后不得超过整体上限",
        )


class TestReleaseVerdict(unittest.TestCase):
    """累计口径判定：单轮低释放不停手，真不释放才停。"""

    def _plugin(self):
        plugin = SeedSpaceGuard()
        plugin._sync_wait_seconds = 90
        return plugin

    def test_healthy_resets_stalls(self):
        verdict = self._plugin()._release_verdict(
            95 * GIB, 100 * GIB, 90 * GIB, 3
        )
        self.assertFalse(verdict["stalled"])
        self.assertFalse(verdict["stop"])
        self.assertEqual(verdict["stall_rounds"], 0, "达标轮必须清零连续计数")

    def test_slow_first_round_keeps_going(self):
        """首轮只回收 65%（现场 197/220GB 的同类情形），且仍在增长 → 不停手。"""
        verdict = self._plugin()._release_verdict(
            65 * GIB, 100 * GIB, 0, 0
        )
        self.assertTrue(verdict["stalled"])
        self.assertFalse(verdict["stop"], "空间还在回收，不得判定为未释放")
        self.assertEqual(verdict["stall_rounds"], 1)

    def test_second_round_low_rate_still_keeps_going(self):
        """现场复现：第二轮单轮只回收 61%，但累计已 87% → 不停手。"""
        verdict = self._plugin()._release_verdict(
            211 * GIB, 242 * GIB, 197 * GIB, 1
        )
        self.assertTrue(verdict["stalled"])
        self.assertFalse(verdict["stop"], "累计仍在增长，停手就是误判")
        self.assertEqual(verdict["stall_rounds"], 2)

    def test_stop_when_zero_growth_twice(self):
        verdict = self._plugin()._release_verdict(
            211 * GIB, 242 * GIB, 211 * GIB, MAX_STALL_ROUNDS - 1
        )
        self.assertTrue(verdict["stalled"])
        self.assertTrue(verdict["stop"], "连续两轮零增长必须停手")

    def test_stop_when_nothing_released(self):
        verdict = self._plugin()._release_verdict(0, 100 * GIB, 0, 0)
        self.assertTrue(verdict["stop"], "整次清理完全没有释放，立即停")

    def test_stop_when_ratio_low_and_zero_growth(self):
        verdict = self._plugin()._release_verdict(
            50 * GIB, 100 * GIB, 50 * GIB, 0
        )
        self.assertTrue(verdict["stop"], "释放率过低且零增长必须停")

    def test_low_ratio_with_growth_keeps_going(self):
        verdict = self._plugin()._release_verdict(
            50 * GIB, 100 * GIB, 10 * GIB, 0
        )
        self.assertFalse(verdict["stop"], "本轮仍在大量回收，不得停手")


class TestCumulativeRoundsE2E(_E2EBase):
    """端到端：多轮累计口径不误停，且摘要分开报名义/实测。"""

    @staticmethod
    def _patch(plugin, name, value):
        from unittest import mock
        return mock.patch.object(plugin, name, return_value=value)

    @staticmethod
    def _patch_seq(plugin, name, func):
        from unittest import mock
        return mock.patch.object(plugin, name, side_effect=func)

    @staticmethod
    def _orphan_patch(plugin):
        from unittest import mock
        return mock.patch.object(
            plugin, "_reap_orphan_seeds",
            return_value={"torrent": 0, "checked": 0, "alive": 0,
                          "failed": 0, "_lines": []},
        )

    @staticmethod
    def _sweep_patch(plugin):
        from unittest import mock
        return mock.patch.object(
            plugin, "_sweep_empty_dirs", return_value={"dirs": 0, "_lines": []}
        )

    def test_multi_round_slow_reclaim_does_not_stop(self):
        """
        4 个 6GB 文件、阈值 15GB：每轮回收率都不足 90%，但累计仍在增长。

        旧口径会在第二轮（单轮 2.7/6GB）就判「未释放」而停手，只删 2 个文件；
        新口径必须把 4 个文件处理完，并把名义 24GB / 实测 14.5GB 分开报。
        """
        for index, age in enumerate((40, 39, 38, 37)):
            self.make_file(
                os.path.join(self.dl, f"f{index}.mkv"), 6 * 1024 * 1024, age
            )
        self.configure([self.dl], threshold=15, sync_wait=1)

        releases = iter([int(7.8 * GIB), int(2.7 * GIB), 4 * GIB])

        with self.patch_free(
            [3 * GIB, 3 * GIB, int(10.8 * GIB), int(13.5 * GIB),
             int(17.5 * GIB)]
        ), self._patch_seq(self.plugin, "_wait_for_release",
                           lambda *_: next(releases)), \
                self._orphan_patch(self.plugin), \
                self._sweep_patch(self.plugin):
            msg = self.plugin.check_and_clean(source="手动")

        self.assertEqual(
            self.plugin._clean_stats["files"], 4,
            "累计仍在回收时不得提前停手（旧口径会停在 2 个）",
        )
        self.assertFalse(self.plugin._clean_stats["stalled"])
        self.assertIn("名义删除约 24.0GB", msg)
        self.assertIn("实测已回收 14.5GB", msg)

    def test_true_stall_stops_and_reports_totals(self):
        """完全没释放：首轮即停，告警给出累计名义与累计实测。"""
        for index, age in enumerate((40, 39, 38, 37)):
            self.make_file(
                os.path.join(self.dl, f"s{index}.mkv"), 6 * 1024 * 1024, age
            )
        self.configure([self.dl], threshold=15, sync_wait=1)

        with self.patch_free([3 * GIB]), self._patch(
            self.plugin, "_wait_for_release", 0
        ), self._orphan_patch(self.plugin), \
                self._sweep_patch(self.plugin):
            msg = self.plugin.check_and_clean(source="手动")

        self.assertTrue(self.plugin._clean_stats["stalled"])
        self.assertIn("已提前停止", msg)
        self.assertIn("名义删除约 12.0GB", msg)
        self.assertIn("实测已回收 0.0GB", msg)

    def test_stale_free_snapshot_not_counted_as_release(self):
        """
        调用方传入的旧剩余快照不得被算成本次清理的释放量。

        现状风险：进入清理前还要跑空壳回收与目录扫描，可能已过去几分钟；
        若把那时读到的剩余当作基线，期间其它进程腾出的空间会被误算成
        「本次释放」，于是一个字节都没回收的清理反而看起来完全正常。
        """
        for index, age in enumerate((40, 39)):
            self.make_file(
                os.path.join(self.dl, f"b{index}.mkv"), 6 * 1024 * 1024, age
            )
        self.configure([self.dl], threshold=1000, sync_wait=1)

        # 首次读取（进入清理前的判断）3GB，之后实时剩余一直是 30GB
        with self.patch_free([3 * GIB, 30 * GIB]), self._patch(
            self.plugin, "_wait_for_release", 0
        ), self._orphan_patch(self.plugin), self._sweep_patch(self.plugin):
            msg = self.plugin.check_and_clean(source="手动")

        self.assertTrue(
            self.plugin._clean_stats["stalled"],
            "本次删除没有回收任何空间，不能被旧快照掩盖成「释放了 27GB」",
        )
        self.assertIn("实测已回收 0.0GB", msg)


class TestCancelDuringWait(_E2EBase):
    """等待窗口按规模放大到十几分钟后，「停止」必须仍然立刻生效。"""

    def test_wait_returns_early_when_cancelled(self):
        plugin = SeedSpaceGuard()
        # 基础值 3 秒（放大后约 18 秒）：足以区分「响应取消」与「等满窗口」
        plugin._sync_wait_seconds = 3
        plugin._run_cancel_event = threading.Event()
        plugin._run_cancel_event.set()
        plugin._disk_free_bytes = lambda: 1 * GIB

        started = time.monotonic()
        released = plugin._wait_for_release(1 * GIB, 200 * GIB)
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 3.0, "已取消时不得继续空等到窗口结束")
        self.assertEqual(released, 0)

    def test_cancel_while_waiting_does_not_mark_stall(self):
        """等待期间取消：既不能判成「空间未释放」，也要如实标注已取消。"""
        for index, age in enumerate((40, 39, 38, 37)):
            self.make_file(
                os.path.join(self.dl, f"c{index}.mkv"), 6 * 1024 * 1024, age
            )
        self.configure([self.dl], threshold=15, sync_wait=1)
        def fake_wait(_free_before, _nominal):
            # 每轮清理启动时会新建取消事件，必须取当前这一个再置位
            self.plugin._cancel_signal().set()
            return 0

        with self.patch_free([3 * GIB]), self._cancel_patches(fake_wait):
            msg = self.plugin.check_and_clean(source="手动")

        self.assertTrue(self.plugin._clean_stats["cancelled"])
        self.assertFalse(
            self.plugin._clean_stats["stalled"],
            "等待被打断不等于「删了没释放」，不得触发停手告警",
        )
        self.assertIn("已取消", msg)

    def _cancel_patches(self, fake_wait):
        from contextlib import ExitStack
        from unittest import mock

        stack = ExitStack()
        stack.enter_context(
            mock.patch.object(self.plugin, "_wait_for_release",
                              side_effect=fake_wait)
        )
        stack.enter_context(
            mock.patch.object(
                self.plugin, "_reap_orphan_seeds",
                return_value={"torrent": 0, "checked": 0, "alive": 0,
                              "failed": 0, "_lines": []},
            )
        )
        stack.enter_context(
            mock.patch.object(
                self.plugin, "_sweep_empty_dirs",
                return_value={"dirs": 0, "_lines": []},
            )
        )
        return stack


if __name__ == "__main__":
    unittest.main()
