# -*- coding: utf-8 -*-
"""
孤儿文件场景测试：**无种子文件**（种子已移走，硬链接还留在媒体库）会如何处理。

背景（用户 2026-10-02 提问）：
  种子 A 有 10 个文件 → 在「电视剧」文件夹生成 10 条硬链接 →
  然后**把 A 的文件和种子都移到别的目录**（不在清理目录内）。
  这时「电视剧」文件夹里就有 10 个「无种子文件」（只剩媒体库侧一条链接）。

本测试回答两个问题：
  1. **种子级模式**会自动清理这些无种子文件吗？
  2. 如果不自动，那它们靠什么被清掉？会不会永远残留？

结论（见各用例断言）：
  - 种子级**不会**主动扫「无种子文件」：它的候选来源是**下载器里现有的种子**，
    种子一旦移出监控目录或从下载器移除，这些文件就失去了「种子入口」。
  - 但它们**不会永久残留**：只要「电视剧」目录自身在清理目录内，
    **文件级模式**（`_clean_by_file`）会把它们作为普通最旧文件纳入候选，
    按 mtime 从旧到新删，并靠 inode 索引把**同 inode 的全部路径一并删除**。
  - ⚠️ 唯一的永久残留风险：该目录**不在**清理目录内 → 两种模式都够不到。
"""

import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

import tests  # noqa: F401  触发宿主桩路径注入

from seedspaceguard import SeedSpaceGuard, GIB


class TestOrphanFiles(unittest.TestCase):
    """孤儿文件（无种子文件）处理行为。"""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="ssg-orphan-")
        # 清理目录 = 下载 + 媒体库
        self.dl = os.path.join(self.base, "download")
        self.lib = os.path.join(self.base, "library")
        # 「别的目录」——不在清理目录内（种子被移到这里）
        self.away = os.path.join(self.base, "away")
        for p in (self.dl, self.lib, self.away):
            os.makedirs(p)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)
        SeedSpaceGuard._clean_stats = None

    def build_orphans(self, title="Show", count=10, age_days=30):
        """造 10 个「无种子文件」：只有媒体库侧一条链接，无下载侧。"""
        libc = os.path.join(self.lib, title)
        os.makedirs(libc, exist_ok=True)
        files = []
        for i in range(count):
            f = os.path.join(libc, f"ep{i:02d}.mkv")
            with open(f, "wb") as fh:
                fh.write(b"x" * 8192)
            st = time.time() - age_days * 86400
            os.utime(f, (st, st))
            files.append(f)
        return libc, files

    def make_plugin(self):
        p = SeedSpaceGuard()
        p._target_dirs = [self.dl, self.lib]
        p._active_dirs = [self.dl, self.lib]
        p._volume_path = self.base
        p._protect_pattern = ""
        p._recent_skip_days = 0
        p._threshold_gb = 1.0
        p._companion_cleanup = True
        p._delete_torrents = True
        p._delete_history = False
        p._downloadhis = None
        p._transferhis = None
        p._ino_paths = {}
        p._clean_stats = {
            "files": 0, "transfers": 0, "seeds": 0,
            "companions": 0, "stalled": False,
        }
        return p

    # ------------------------------------------------------------------
    def test_1_seed_mode_does_not_see_orphan_files(self):
        """① 种子级模式的候选来自「下载器里的种子」，看不到无种子文件。

        无种子文件没有对应的种子条目 → 不进 `_collect_seed_candidates`
        → 种子级链路完全够不到它们。
        """
        libc, files = self.build_orphans()
        plugin = self.make_plugin()
        # 下载器中没有任何种子（模拟种子已被移走/删除）
        with mock.patch.object(plugin, "_collect_seed_candidates",
                            return_value=[]):
            deleted, gb, lines = plugin._clean_by_seed(0, False)
        # 候选为空 → 直接返回 -1，一个都不删
        self.assertEqual(deleted, -1, "无候选种子时种子级应返回 -1（未找到）")
        self.assertEqual(len(os.listdir(libc)), 10,
                        "种子级模式不会碰这些无种子文件")

    def test_2_file_mode_cleans_orphan_files(self):
        """② 文件级模式会把它们当普通最旧文件删掉（且凭 inode 清双侧）。

        这是它们**不会永久残留**的原因：只要目录在清理目录内，
        文件级链路按 mtime 从旧到新扫，无种子文件照样进候选。
        """
        libc, files = self.build_orphans()
        plugin = self.make_plugin()
        # 空间序列：先不足，删完释放充足
        with mock.patch.object(plugin, "_disk_free_bytes",
                            side_effect=[0, 40 * GIB, 40 * GIB]), \
            mock.patch.object(plugin, "_wait_for_release",
                            lambda before, nominal, **k: nominal), \
            mock.patch.object(plugin, "_run_linkage_after_delete",
                            lambda paths, lines, dr: {"history": 0, "torrent": 0, "torrent_kept": 0}):
            deleted, gb, lines = plugin._clean_by_file(0, False)
        self.assertGreater(deleted, 0, "文件级应删掉这些孤儿文件")
        left = os.listdir(libc) if os.path.isdir(libc) else []
        self.assertEqual(left, [], "孤儿文件应被文件级清理干净")

    def test_3_file_mode_index_covers_orphan(self):
        """③ 文件级建索引时，孤儿文件（同 inode 只有一条路径）也能被正确索引。

        断言索引里能找到它 —— 说明「只剩一条硬链接」不影响被清理。
        """
        libc, files = self.build_orphans(count=3)
        plugin = self.make_plugin()
        parsed = [p.strip() for p in
                __import__("re").split(r"[,|，]", plugin._protect_pattern)
                if p.strip()]
        _, ino_paths = plugin._index_files(parsed, 0)
        indexed_paths = {p for plist in ino_paths.values() for p in plist}
        for f in files:
            self.assertIn(f, indexed_paths,
                        f"孤儿文件应被文件级索引收录：{f}")

    def test_4_out_of_scope_dir_permanently_untouched(self):
        """④ ⚠️ 若目录**不在清理目录内**，两种模式都够不到 → 永久残留。

        这正是需要提醒用户的唯一风险点：把种子和文件都移到监控范围外后，
        留在媒体库里的那些硬链接，插件既看不到种子、也扫不到那个目录。
        """
        # 把孤儿文件放在「清理目录之外」的 away 目录
        awayc = os.path.join(self.away, "Show")
        os.makedirs(awayc, exist_ok=True)
        files = []
        for i in range(3):
            f = os.path.join(awayc, f"ep{i:02d}.mkv")
            with open(f, "wb") as fh:
                fh.write(b"x" * 4096)
            files.append(f)

        plugin = self.make_plugin()
        # 文件级索引
        _, ino_paths = plugin._index_files([], 0)
        indexed = {p for plist in ino_paths.values() for p in plist}
        for f in files:
            self.assertNotIn(f, indexed,
                            "清理目录外的文件不应进索引（这是设计，不是 bug）")
        # 种子级同样够不到
        with mock.patch.object(plugin, "_collect_seed_candidates",
                            return_value=[]):
            deleted, _, _ = plugin._clean_by_seed(0, False)
        self.assertEqual(deleted, -1)
        self.assertEqual(len(os.listdir(awayc)), 3,
                        "清理目录外的孤儿文件不会被触碰 —— 需用户自行清理")

    def test_5_orphan_with_peer_link_in_scope_partially_cleaned(self):
        """⑤ 若同 inode 还有一条链接**在**清理目录内，则两条都会被删。

        更贴近用户场景的变体：A 的原始文件被移到了清理目录内的其它位置，
        或下载侧仍在 —— 此时 inode 被索引到，「两侧」会一并清理。
        """
        # 原始文件在下载目录（在范围内），媒体库侧是它的硬链接
        content = os.path.join(self.dl, "Show")
        os.makedirs(content, exist_ok=True)
        libc = os.path.join(self.lib, "Show")
        os.makedirs(libc, exist_ok=True)
        for i in range(3):
            f_dl = os.path.join(content, f"ep{i:02d}.mkv")
            with open(f_dl, "wb") as fh:
                fh.write(b"x" * 4096)
            os.link(f_dl, os.path.join(libc, f"ep{i:02d}.mkv"))

        plugin = self.make_plugin()
        _, ino_paths = plugin._index_files([], 0)
        indexed = {p for plist in ino_paths.values() for p in plist}
        # 媒体库侧（孤儿侧）也应被收录 —— 因为同 inode 在范围内有链接
        for i in range(3):
            self.assertIn(os.path.join(libc, f"ep{i:02d}.mkv"), indexed,
                        "同 inode 在范围内有链接时，媒体库侧也应被索引")


if __name__ == "__main__":
    unittest.main()
