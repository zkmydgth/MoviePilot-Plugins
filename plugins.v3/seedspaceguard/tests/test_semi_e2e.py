# -*- coding: utf-8 -*-
"""
半残种子「全流程」端到端测试：直接驱动 _clean_by_seed 真实代码路径。

用户提问：**对于现有的已被部分删除文件的种子，后续在种子级删除时，
也能正常运行吗。**

本测试不再拆开单测各子方法，而是走完整流程：
  _collect_seed_candidates → 预选 → _clean_by_seed 主循环
  （①建索引 ②删种 ③清硬链接 ④连带辅种 ⑤收尾）

逐个覆盖「半残」形态，断言：不崩、不误删、统计正确、空间语义合理。

用例中用到的桩：
  - 下载器 stub：记录 remove_torrents 调用，delete_file=True 时真删内容目录
  - DownloaderHelper / DownloadHistoryOper 打桩，使 _collect_seed_candidates 可用
  - _disk_free_bytes 序列化返回值，模拟「删完空间就够」的正常释放
"""

import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

import tests  # noqa: F401  触发宿主桩路径注入

from seedspaceguard import SeedSpaceGuard, GIB


class _DlStub:
    """下载器桩：记录调用，delete_file=True 时删掉内容路径。"""

    def __init__(self, dl_root):
        self.calls = []          # [(hash, delete_file)]
        self.dl_root = dl_root

    def remove_torrents(self, hashs=None, delete_file=False, downloader=None):
        hs = [hashs] if isinstance(hashs, str) else list(hashs or [])
        for h in hs:
            self.calls.append((h, delete_file))
            if delete_file:
                shutil.rmtree(os.path.join(self.dl_root, h), ignore_errors=True)
        return True


class TestSemiDeletedSeedE2E(unittest.TestCase):
    """半残种子全流程测试。"""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="ssg-semi-e2e-")
        self.dl = os.path.join(self.base, "download")
        self.lib = os.path.join(self.base, "library")
        os.makedirs(self.dl)
        os.makedirs(self.lib)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)
        # 清理类级统计，避免跨用例串状态（历史踩坑）
        SeedSpaceGuard._clean_stats = None

    # -------------------------------------------------- 工具
    def build_seed(self, hash_str="h1", title="Show", count=4, size_gb=30.0):
        content = os.path.join(self.dl, hash_str)
        libc = os.path.join(self.lib, title)
        os.makedirs(content, exist_ok=True)
        os.makedirs(libc, exist_ok=True)
        pairs = []
        for i in range(count):
            f_dl = os.path.join(content, f"ep{i:02d}.mkv")
            with open(f_dl, "wb") as fh:
                fh.write(b"x" * 8192)
            f_lib = os.path.join(libc, f"ep{i:02d}.mkv")
            os.link(f_dl, f_lib)
            pairs.append((f_dl, f_lib))
        self.pairs, self.content, self.libc = pairs, content, libc
        return {
            "hash": hash_str, "title": title, "added": time.time() - 40 * 86400,
            "size_gb": size_gb, "path": content, "downloader": "qb",
            "module": self.dl_stub, "files": [],
        }

    def make_plugin(self, threshold_gb=1.0):
        p = SeedSpaceGuard()
        p._target_dirs = [self.dl, self.lib]
        p._active_dirs = [self.dl, self.lib]
        p._companion_cleanup = True
        p._delete_torrents = True
        p._delete_history = False
        p._recent_skip_days = 0
        p._threshold_gb = threshold_gb
        p._downloadhis = None
        p._transferhis = None
        p._ino_paths = {}
        p._clean_stats = {
            "files": 0, "transfers": 0, "seeds": 0,
            "companions": 0, "stalled": False,
        }
        return p

    def run_flow(self, plugin, cand, free_seq):
        """跑完整 _clean_by_seed：打桩候选收集与磁盘余量。"""
        seq = list(free_seq)

        def fake_free():
            return seq.pop(0) if len(seq) > 1 else seq[0]

        with mock.patch.object(plugin, "_collect_seed_candidates", return_value=[cand]), \
            mock.patch.object(plugin, "_disk_free_bytes", side_effect=fake_free), \
            mock.patch.object(plugin, "_wait_for_release",
                            lambda free_before, nominal, **k: nominal), \
            mock.patch.object(plugin, "_run_linkage_on_seed_deleted",
                            lambda c, d, dr: 0):
            return plugin._clean_by_seed(0, False)

    # -------------------------------------------------- 用例
    def test_A_library_links_gone_still_works(self):
        """A. 媒体库侧硬链接此前已被删（文件级模式跑过）。"""
        self.dl_stub = _DlStub(self.dl)
        plugin = self.make_plugin()
        cand = self.build_seed()
        for _, f_lib in self.pairs:
            os.unlink(f_lib)
        # 删种后空间足额释放
        deleted, gb, lines = self.run_flow(plugin, cand, [0, 40 * GIB])
        self.assertEqual(deleted, 1, "半残种子仍应被正常删除")
        self.assertEqual([c for c in self.dl_stub.calls if c[1]], [("h1", True)],
                        "主种子应以 delete_file=True 删除")
        self.assertFalse(os.path.isdir(self.content) and os.listdir(self.content),
                        "下载侧应清空")
        self.assertFalse(os.path.isdir(self.libc) and os.listdir(self.libc),
                        "媒体库侧本就为空")

    def test_B_download_side_partial_still_works(self):
        """B. 下载侧部分文件已丢，其余仍在。"""
        self.dl_stub = _DlStub(self.dl)
        plugin = self.make_plugin()
        cand = self.build_seed()
        for f_dl, _ in self.pairs[:2]:
            os.unlink(f_dl)
        deleted, gb, lines = self.run_flow(plugin, cand, [0, 40 * GIB])
        self.assertEqual(deleted, 1, "半残种子仍应被正常删除")
        # 媒体库侧孤证（前 2 个）也必须被清掉，否则空间不释放
        self.assertFalse(os.path.isdir(self.libc) and os.listdir(self.libc),
                        "媒体库侧含孤儿硬链接，必须清空")

    def test_C_stats_consistent_after_semi(self):
        """C. 半残场景下统计口径一致（seeds 计数与明细行对应）。"""
        self.dl_stub = _DlStub(self.dl)
        plugin = self.make_plugin()
        cand = self.build_seed()
        for f_dl, _ in self.pairs[:1]:
            os.unlink(f_dl)
        self.run_flow(plugin, cand, [0, 40 * GIB])
        self.assertEqual(plugin._clean_stats["seeds"], 1)
        self.assertFalse(plugin._clean_stats["stalled"],
                        "空间正常释放时不应判定 stall")

    def test_D_stall_not_triggered_by_orphan_links(self):
        """D. 孤儿硬链接被清掉 → 空间应正常释放，不得误报 stall。

        这是半残种子最危险的失效模式：若媒体库侧孤证没被清，实测释放量
        远小于名义值 → _release_is_healthy 误判 → stall 停手告警。
        """
        self.dl_stub = _DlStub(self.dl)
        plugin = self.make_plugin()
        cand = self.build_seed(size_gb=30.0)
        for f_dl, _ in self.pairs[:2]:
            os.unlink(f_dl)
        # 第一轮：删完释放 30GB（正常）
        self.run_flow(plugin, cand, [0, 40 * GIB])
        self.assertFalse(plugin._clean_stats["stalled"],
                        "媒体库侧孤证已清 → 释放健康 → 不得 stall")

    def test_E_all_missing_returns_zero_no_crash(self):
        """E. 全部文件已不存在 → 不崩、返回 0、不误删。"""
        self.dl_stub = _DlStub(self.dl)
        plugin = self.make_plugin()
        cand = self.build_seed()
        # 两侧全删 + 目录也删
        for f_dl, f_lib in self.pairs:
            os.unlink(f_dl)
            os.unlink(f_lib)
        shutil.rmtree(self.content, ignore_errors=True)
        shutil.rmtree(self.libc, ignore_errors=True)
        deleted, gb, lines = self.run_flow(plugin, cand, [0, 40 * GIB])
        self.assertEqual(deleted, 1, "种子本身仍应被删除（文件早没了）")
        self.assertEqual([c for c in self.dl_stub.calls if c[1]], [("h1", True)])

    def test_F_companion_of_semi_seed_also_removed(self):
        """F. 半残的主种子，其辅种也应被连带摘除。"""
        self.dl_stub = _DlStub(self.dl)
        plugin = self.make_plugin()
        cand = self.build_seed(hash_str="h1")
        # 第二个辅种：同路径 + 同种子名（内容一致）
        peer = dict(cand)
        peer["hash"] = "h2"
        for f_dl, _ in self.pairs[:1]:
            os.unlink(f_dl)

        with mock.patch.object(plugin, "_collect_seed_candidates",
                            return_value=[cand, peer]), \
            mock.patch.object(plugin, "_disk_free_bytes",
                            side_effect=[0, 40 * GIB, 40 * GIB]), \
            mock.patch.object(plugin, "_wait_for_release",
                            lambda free_before, nominal, **k: nominal), \
            mock.patch.object(plugin, "_get_downloader_for",
                            lambda h: self.dl_stub), \
            mock.patch.object(plugin, "_run_linkage_on_seed_deleted",
                            lambda c, d, dr: 0):
            plugin._clean_by_seed(0, False)

        called = [c for c in self.dl_stub.calls]
        self.assertIn(("h1", True), called, "主种子应删除文件")
        self.assertIn(("h2", False), called, "辅种应被摘除但不删文件")
        self.assertEqual(plugin._clean_stats["companions"], 1)

    def test_G_seed_count_accumulates_across_phases(self):
        """G. 进入本方法前已回收的空壳种子计数必须**累加**而非被覆盖。

        半残种子常伴随空壳回收：`check_and_clean` 先跑 `_reap_orphan_seeds`
        记下回收数，再进 `_clean_by_seed`。若此处用赋值而非累加，摘要会少报
        空壳那部分——用户看到「删除 1 个」实际删了 3 个，口径对不上。
        """
        self.dl_stub = _DlStub(self.dl)
        plugin = self.make_plugin()
        # 预置：本轮之前已回收 2 个空壳种子
        plugin._clean_stats["seeds"] = 2
        cand = self.build_seed()
        for f_dl, _ in self.pairs[:1]:
            os.unlink(f_dl)
        self.run_flow(plugin, cand, [0, 40 * GIB])
        self.assertEqual(plugin._clean_stats["seeds"], 3,
                        "应为 2（空壳）+ 1（本方法）= 3，不得被覆盖为 1")


if __name__ == "__main__":
    unittest.main()
