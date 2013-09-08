# -*- coding: utf-8 -*-
"""
「无主文件」清扫测试：种子级模式下清理不被任何种子引用的孤儿硬链接。

背景（用户 2026-10-02 实测提出）：
  种子 A 的 10 个文件在媒体库目录生成硬链接后，把 A 的内容与种子**都移到
  监控目录之外**。此时残留的硬链接既无种子可依（种子级候选要求种子在监控
  目录内），又不被文件级链路触及（模式二选一），于是**永久残留**。

本测试的重点是**安全**而非功能：清理动作会删用户文件，判定错一次就是数据
丢失。因此下列用例大量围绕「什么情况下**绝不能删**」展开：

  - 范围外活跃种子引用的文件 → 绝不删（最高危）
  - 未完成种子占用的文件 → 绝不删
  - 拿不到种子清单时 → 整轮放弃，一个都不删
  - 同 inode 任一路径有主 → 整组保留
  - 保护期内 / 命中保护后缀 → 保留
"""

import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

import tests  # noqa: F401  触发宿主桩路径注入

from app.core.module import ModuleManager
from app.schemas.types import DownloaderType
from seedspaceguard import SeedSpaceGuard, GIB


def _install_downloader(torrents, removed_hook=None):
    """注册一个可控的 qBittorrent 下载器桩。

    :param torrents: get_torrents() 返回的种子列表（原始条目 dict）
    :param removed_hook: 接收 (hashs, delete_file) 的回调
    """

    class _Server:
        def get_torrents(self, *args, **kwargs):
            return torrents

        def remove_torrents(self, hashs=None, delete_file=False,
                            downloader=None, **kwargs):
            if removed_hook:
                if isinstance(hashs, str):
                    normalized = [hashs]
                else:
                    normalized = list(hashs or [])
                removed_hook(normalized, delete_file)
            return True

    ModuleManager.reset()
    ModuleManager.register_downloader(
        DownloaderType.Qbittorrent, "qb", _Server()
    )


def _qb_item(content_path, hash_str, name, progress=1.0):
    """构造一个 qBittorrent 原始种子条目（camleCase 字段）。"""
    return {
        "content_path": content_path,
        "save_path": os.path.dirname(content_path),
        "hash": hash_str,
        "name": name,
        "progress": progress,
        "completion_on": int(time.time()) - 40 * 86400,
        "size": 4 * 1024 ** 3,
        "added_on": int(time.time()) - 40 * 86400,
    }


class _OrphanBase(unittest.TestCase):
    """夹具：下载目录 + 媒体库目录（清理目录 = 两者）。"""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="ssg-orphanscan-")
        self.dl = os.path.join(self.base, "download")
        self.lib = os.path.join(self.base, "library")
        self.away = os.path.join(self.base, "away")   # 监控范围之外
        for path in (self.dl, self.lib, self.away):
            os.makedirs(path)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)
        # _clean_stats 是类属性，跨用例共享 → 必须显式复位
        SeedSpaceGuard._clean_stats = None

    # ------------------------------------------------------------------
    def make_plugin(self, orphan_cleanup=True, threshold_gb=1.0,
                    protect_pattern="", recent_skip_days=0):
        p = SeedSpaceGuard()
        p._target_dirs = [self.dl, self.lib]
        p._active_dirs = [self.dl, self.lib]
        p._volume_path = self.base
        p._protect_pattern = protect_pattern
        p._recent_skip_days = recent_skip_days
        p._threshold_gb = threshold_gb
        p._companion_cleanup = True
        p._orphan_cleanup = orphan_cleanup
        p._delete_torrents = True
        p._delete_history = False
        p._downloadhis = None
        p._transferhis = None
        p._ino_paths = {}
        p._clean_stats = {
            "files": 0, "transfers": 0, "seeds": 0,
            "companions": 0, "orphans": 0, "stalled": False,
        }
        return p

    def make_file(self, path, size_kb=4096, age_days=30):
        """创建文件并回拨 mtime（默认已过保护期）。"""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(b"x" * size_kb)
        stamp = time.time() - age_days * 86400
        os.utime(path, (stamp, stamp))
        return path

    def run_scan(self, plugin, free_seq=None):
        """跑 _clean_orphan_files，磁盘余量可指定序列。"""
        if free_seq is None:
            free_seq = [0]
        seq = list(free_seq)

        def fake_free():
            return seq.pop(0) if len(seq) > 1 else seq[0]

        patcher = mock.patch.object(plugin, "_disk_free_bytes",
                                    side_effect=fake_free)
        with patcher:
            return plugin._clean_orphan_files(False)


class TestOrphanCleanup(_OrphanBase):
    """基础功能：孤儿文件应被清理。"""

    def test_pure_orphan_is_removed(self):
        """纯孤儿：只在媒体库有一份、无任何种子引用 → 删除。"""
        orphan = self.make_file(os.path.join(self.lib, "Show", "ep01.mkv"))
        # 下载器里没有任何种子
        _install_downloader([])
        plugin = self.make_plugin()
        deleted, gb, lines = self.run_scan(plugin)
        self.assertEqual(deleted, 1, "纯孤儿应被清理")
        self.assertFalse(os.path.exists(orphan), "文件应已删除")
        self.assertEqual(plugin._clean_stats["orphans"], 1)

    def test_orphan_pair_both_sides_removed(self):
        """同 inode 双侧硬链接、种子已移走 → 两条路径一起清。"""
        src = self.make_file(os.path.join(self.dl, "Show", "ep01.mkv"))
        link = os.path.join(self.lib, "Show", "ep01.mkv")
        os.makedirs(os.path.dirname(link), exist_ok=True)
        os.link(src, link)
        _install_downloader([])
        plugin = self.make_plugin()
        deleted, gb, lines = self.run_scan(plugin)
        self.assertEqual(deleted, 1, "按 inode 计一个文件")
        self.assertFalse(os.path.exists(src), "下载侧应一并删除")
        self.assertFalse(os.path.exists(link), "媒体库侧应一并删除")

    def test_dry_run_previews_without_deleting(self):
        """试运行：如实预告，文件仍在。"""
        orphan = self.make_file(os.path.join(self.lib, "Show", "ep01.mkv"))
        _install_downloader([])
        plugin = self.make_plugin()
        with mock.patch.object(plugin, "_disk_free_bytes", return_value=0):
            deleted, gb, lines = plugin._clean_orphan_files(True)
        self.assertEqual(deleted, 1, "试运行也应预告数量")
        self.assertTrue(os.path.exists(orphan), "试运行时不得真删")
        self.assertTrue(any("将删除无主文件" in x for x in lines),
                        "应给出试运行预告行")

    def test_space_sufficient_skips(self):
        """空间充足：不应删除任何东西（删完即停）。"""
        orphan = self.make_file(os.path.join(self.lib, "Show", "ep01.mkv"))
        _install_downloader([])
        plugin = self.make_plugin(threshold_gb=1.0)
        # 一开始空间就充足 → 循环首轮即 break
        self.run_scan(plugin, free_seq=[50 * GIB])
        self.assertTrue(os.path.exists(orphan),
                        "空间充足时不应执行删除")


class TestOrphanSafety(_OrphanBase):
    """安全边界：这些情况下**绝不能删**。"""

    def test_1_outsider_active_seed_protects(self):
        """最高危：种子仍在下载器、content_path 在监控范围外。

        其文件在媒体库有硬链接 —— 绝不能当孤儿删掉，否则破坏做种。
        """
        # 活跃种子：内容在 away（监控范围外），媒体库有硬链接
        away_content = os.path.join(self.away, "Show")
        src = self.make_file(os.path.join(away_content, "ep01.mkv"))
        link = os.path.join(self.lib, "Show", "ep01.mkv")
        os.makedirs(os.path.dirname(link), exist_ok=True)
        os.link(src, link)

        _install_downloader([
            _qb_item(away_content, "hash_out", "Show", progress=1.0)
        ])
        plugin = self.make_plugin()
        deleted, gb, lines = self.run_scan(plugin)
        self.assertEqual(deleted, 0, "范围外活跃种子引用的文件绝不能被删")
        self.assertTrue(os.path.exists(link), "媒体库侧链接必须保留")
        self.assertTrue(os.path.exists(src), "种子内容必须保留")

    def test_2_incomplete_seed_protects(self):
        """未完成种子（progress<1）仍占磁盘文件 → 绝不能删。"""
        content = os.path.join(self.dl, "Show")
        self.make_file(os.path.join(content, "ep01.mkv"))
        # progress=0.5 → _parse_torrent 会丢弃，但仍占文件
        _install_downloader([
            _qb_item(content, "hash_part", "Show", progress=0.5)
        ])
        plugin = self.make_plugin()
        deleted, gb, lines = self.run_scan(plugin)
        self.assertEqual(deleted, 0, "未完成种子的文件绝不能被删")

    def test_3_enumeration_failure_aborts(self):
        """拿不到种子清单 → 整轮放弃，一个都不删。"""
        orphan = self.make_file(os.path.join(self.lib, "Show", "ep01.mkv"))
        # 无任何下载器 → get_services 为空 → 返回 None
        ModuleManager.reset()
        plugin = self.make_plugin()
        deleted, gb, lines = self.run_scan(plugin)
        self.assertEqual(deleted, 0, "无法确证时必须放弃")
        self.assertTrue(os.path.exists(orphan), "文件必须保留")
        self.assertTrue(any("无法获取种子清单" in x for x in lines))

    def test_4_inode_group_partially_owned_is_kept(self):
        """同 inode 一侧有主 → 整组保留。"""
        # 下载侧是活跃种子的内容（有主），媒体库侧是它的硬链接
        content = os.path.join(self.dl, "Show")
        src = self.make_file(os.path.join(content, "ep01.mkv"))
        link = os.path.join(self.lib, "Show", "ep01.mkv")
        os.makedirs(os.path.dirname(link), exist_ok=True)
        os.link(src, link)
        _install_downloader([
            _qb_item(content, "hash_live", "Show", progress=1.0)
        ])
        plugin = self.make_plugin()
        deleted, gb, lines = self.run_scan(plugin)
        self.assertEqual(deleted, 0, "一侧有主则整组保留")
        self.assertTrue(os.path.exists(link))
        self.assertTrue(os.path.exists(src))

    def test_5_protection_window_keeps(self):
        """保护期内的孤儿文件 → 保留。"""
        self.make_file(os.path.join(self.lib, "Show", "ep01.mkv"), age_days=0)
        _install_downloader([])
        # 保护期 7 天，文件 mtime 是「刚刚」
        plugin = self.make_plugin(recent_skip_days=7)
        deleted, gb, lines = self.run_scan(plugin)
        self.assertEqual(deleted, 0, "保护期内不得删除")

    def test_6_protect_pattern_keeps(self):
        """命中保护后缀的文件 → 保留。"""
        keep = self.make_file(
            os.path.join(self.lib, "Show", "ep01.mkv.part"))
        _install_downloader([])
        plugin = self.make_plugin(protect_pattern="*.part")
        deleted, gb, lines = self.run_scan(plugin)
        self.assertEqual(deleted, 0, "保护后缀不得删除")
        self.assertTrue(os.path.exists(keep))


class TestOrphanConfig(_OrphanBase):
    """配置：默认关、开关生效。"""

    def test_default_off_when_key_absent(self):
        """走真实 init_plugin：未配置该键时默认**关闭**。"""
        plugin = SeedSpaceGuard()
        plugin.init_plugin({"enabled": True,
                            "target_dirs": self.dl + "\n" + self.lib})
        self.assertFalse(plugin._orphan_cleanup,
                        "未配置时必须默认关闭（涉及删用户文件）")

    def test_explicit_false_keeps_off(self):
        plugin = SeedSpaceGuard()
        plugin.init_plugin({"enabled": True,
                            "target_dirs": self.dl + "\n" + self.lib,
                            "orphan_cleanup": False})
        self.assertFalse(plugin._orphan_cleanup)

    def test_explicit_true_turns_on(self):
        plugin = SeedSpaceGuard()
        plugin.init_plugin({"enabled": True,
                            "target_dirs": self.dl + "\n" + self.lib,
                            "orphan_cleanup": True})
        self.assertTrue(plugin._orphan_cleanup)

    def test_default_config_declares_off(self):
        plugin = SeedSpaceGuard()
        self.assertFalse(plugin._default_config()["orphan_cleanup"],
                        "_default_config 必须声明为关闭")

    def test_form_exposes_orphan_switch(self):
        plugin = SeedSpaceGuard()
        form = plugin.get_form()
        found = []

        def walk(node):
            if isinstance(node, dict):
                if node.get("model") == "orphan_cleanup":
                    found.append(node)
                for value in node.values():
                    walk(value)
            # get_form() 返回 tuple，必须一并展开（只认 dict/list 会漏掉）
            elif isinstance(node, (list, tuple)):
                for value in node:
                    walk(value)

        walk(form)
        self.assertEqual(len(found), 1, "表单应有一个 orphan_cleanup 开关")


if __name__ == "__main__":
    unittest.main()
