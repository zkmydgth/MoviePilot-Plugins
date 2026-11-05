# -*- coding: utf-8 -*-
"""
v3.5.1 回归：停手前宽限一轮 + 摘要数字以最终一次真实读取为准。

现场背景（2026-11-05 18:06）：一次清理删掉 29 个文件、名义 130.7GB，通知报
「实测已回收 119.0GB，当前剩余 503GB」；二十分钟后磁盘实际 669GB。原因是
Btrfs 的空间回收滞后于删除，窗口内的实测值系统性偏低，用户拿通知里的数字
与稍后的 df 对比必然对不上。

本文件锁定两件事：

1. 摘要里的「实测已回收」与「停手时剩余」必须来自同一次最终读取，
   「实测已回收 + 起点剩余 = 停手时剩余」恒成立
2. 判定「未按预期释放」时不立刻停手，先不删文件、宽限一轮再复测；
   宽限期内确有回收就继续，仍为零才停
"""

import os
import unittest
from unittest import mock

import tests  # noqa: F401  触发宿主桩路径注入

from seedspaceguard import GIB, SeedSpaceGuard
from tests.test_clean_flow_e2e import _E2EBase


class _GraceBase(_E2EBase):
    """共用夹具：屏蔽与释放判定无关的空壳链路。"""

    def _orphan_patch(self):
        return mock.patch.object(
            self.plugin,
            "_reap_orphan_seeds",
            return_value={
                "torrent": 0, "checked": 0, "alive": 0, "failed": 0, "_lines": [],
            },
        )

    def _sweep_patch(self):
        return mock.patch.object(
            self.plugin, "_sweep_empty_dirs",
            return_value={"dirs": 0, "_lines": []},
        )


class TestFinalSnapshotNumbers(_GraceBase):
    """摘要数字必须来自同一次最终读取，不能靠窗口内推演值。"""

    def test_summary_uses_final_read(self):
        """
        窗口内只观察到 1.8GB 释放，停手后实际已到 6GB。

        旧实现把窗口推演值写进「实测已回收」（1.8GB），却另写一个
        「当前剩余 6GB」——两个数相加不等于起点剩余，用户无法对账。
        """
        for index, age in enumerate((40, 39, 38, 37)):
            self.make_file(
                os.path.join(self.dl, f"f{index}.mkv"), 6 * 1024 * 1024, age
            )
        self.configure([self.dl], threshold=4, sync_wait=1)

        # 读取顺序：初始判断 3GB → 首轮 3GB → 次轮 4.5GB（达阈值即止）→ 最终 6GB
        with self.patch_free(
            [3 * GIB, 3 * GIB, int(4.5 * GIB), 6 * GIB]
        ), mock.patch.object(
            self.plugin, "_wait_for_release", return_value=1.8 * GIB
        ), self._orphan_patch(), self._sweep_patch():
            msg = self.plugin.check_and_clean(source="手动")

        self.assertIn("实测已回收 3.0GB", msg,
                      "实测必须取最终读取（6-3），不能用窗口推演值 1.8GB")
        self.assertIn("停手时剩余 6GB", msg,
                      "剩余必须点明是停手瞬间的快照")
        self.assertNotIn("当前剩余", msg,
                         "旧措辞会被用户当成与事实不符的实时值")


class TestStopGraceRound(_GraceBase):
    """判定未释放时先宽限一轮，不再立刻停手。"""

    def _prepare(self, count=1):
        # 候选只放 count 个：本轮预选即耗尽，宽限期结束后不会再有第二轮删除，
        # 这样等待调用次数就只反映「本轮等待 + 宽限等待」。
        for index in range(count):
            self.make_file(
                os.path.join(self.dl, f"g{index}.mkv"), 6 * 1024 * 1024,
                40 - index,
            )
        self.configure([self.dl], threshold=15, sync_wait=1)

    def test_grace_release_keeps_cleaning(self):
        """首轮零回收被判停手，宽限窗口内回收 5GB → 必须继续而不是停。"""
        self._prepare()

        waiter = mock.Mock(side_effect=[0, 5 * GIB])
        with self.patch_free([3 * GIB]), mock.patch.object(
            self.plugin, "_wait_for_release", waiter
        ), self._orphan_patch(), self._sweep_patch():
            msg = self.plugin.check_and_clean(source="手动")

        self.assertEqual(waiter.call_count, 2,
                         "判定停手后必须再等一轮（共两次等待）")
        self.assertFalse(self.plugin._clean_stats["stalled"],
                         "宽限期内确有回收，不得判成空间未释放")
        self.assertNotIn("已提前停止", msg)
        self.assertEqual(self.plugin._clean_stats["files"], 1)

    def test_grace_still_zero_stops(self):
        """宽限一轮后仍为零回收 → 停手，且不额外删除文件。"""
        self._prepare()

        waiter = mock.Mock(side_effect=[0, 0])
        with self.patch_free([3 * GIB]), mock.patch.object(
            self.plugin, "_wait_for_release", waiter
        ), self._orphan_patch(), self._sweep_patch():
            msg = self.plugin.check_and_clean(source="手动")

        self.assertEqual(waiter.call_count, 2, "停手前必须真的等过一轮")
        self.assertTrue(self.plugin._clean_stats["stalled"])
        self.assertIn("已提前停止", msg)
        self.assertEqual(self.plugin._clean_stats["files"], 1)


class TestGraceNotDeleteExtra(_GraceBase):
    """宽限期间只等待，不能借机扩大删除范围。"""

    def test_grace_does_not_delete_more_candidates(self):
        # 缺口 12GB、单文件 6GB：本轮预选 2 个恰好填满缺口，宽限期不得再删。
        for index, age in enumerate((40, 39)):
            self.make_file(
                os.path.join(self.dl, f"h{index}.mkv"), 6 * 1024 * 1024, age
            )
        self.configure([self.dl], threshold=15, sync_wait=1)

        waiter = mock.Mock(side_effect=[0, 0])
        with self.patch_free([3 * GIB]), mock.patch.object(
            self.plugin, "_wait_for_release", waiter
        ), self._orphan_patch(), self._sweep_patch():
            self.plugin.check_and_clean(source="手动")

        self.assertEqual(self.plugin._clean_stats["files"], 2)
        remaining = os.listdir(self.dl)
        self.assertEqual(remaining, [], "宽限期不得留下未处理的额外删除")


if __name__ == "__main__":
    unittest.main()
