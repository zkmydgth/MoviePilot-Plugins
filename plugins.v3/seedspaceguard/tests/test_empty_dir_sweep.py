# -*- coding: utf-8 -*-
"""
「监控目录空壳扫描」测试（v3.0.12）：清理只剩 @eaDir 残片的空目录。

背景（用户 2026-10-19 实测提出）：
  `/volume1/video/下载/上海滩.The.Bund...` 里的视频文件早已被删掉，
  但群晖的 `@eaDir/*.mkv/SYNOINDEX_MEDIA_INFO` 索引残片让目录无法 rmdir，
  于是长期留下一个「看起来是空的」空壳目录。

  旧逻辑只有 `_prune_empty_dirs`，且**只在插件自己删文件 / 删种之后**沿路径
  向上收尾；这类「文件不是插件删的」目录没有任何触发点，永远不会被处理。

本测试的重点同样是**安全**：清理会删目录，误判一次就会删掉用户目录。

  - 目录内有任一真实文件 → 整棵保留
  - 符号链接 → 视为内容，保留
  - @eaDir 内有无法识别的条目（可能是用户数据）→ 保留
  - 配置目录本身（监控根）→ 永不删除
  - 开关关闭 / 试运行 → 不删任何东西
"""

import os
import shutil
import tempfile
import unittest
from unittest import mock

import tests  # noqa: F401  触发宿主桩路径注入

from seedspaceguard import SeedSpaceGuard, GIB


class _SweepBase(unittest.TestCase):
    """空壳扫描夹具：真实临时目录 + 直接注入内部状态。"""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="ssg-sweep-")
        self.dl = os.path.join(self.base, "download")
        self.lib = os.path.join(self.base, "library")
        os.makedirs(self.dl)
        os.makedirs(self.lib)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def make_plugin(self, dirs=None, sweep=True):
        """构造插件实例并直接注入目录 / 开关状态。"""
        plugin = SeedSpaceGuard()
        plugin._target_dirs = [os.path.normpath(d) for d in (dirs or [self.dl, self.lib])]
        plugin._active_dirs = [d for d in plugin._target_dirs if os.path.isdir(d)]
        plugin._volume_path = self.base
        plugin._threshold_gb = 500
        plugin._empty_dir_sweep = sweep
        plugin._recent_skip_days = 0
        plugin._protect_pattern = ""
        plugin._delete_torrents = False
        plugin._delete_history = False
        plugin._dry_run = False
        plugin._notify = False
        plugin._clean_stats = {
            "files": 0, "transfers": 0, "seeds": 0,
            "companions": 0, "orphans": 0, "dirs": 0,
            "stalled": False, "dry_run": False,
        }
        return plugin

    @staticmethod
    def make_shell(path, episodes=("E01", "E02")):
        """构造「只剩 DSM 索引残片」的空壳目录。"""
        os.makedirs(path, exist_ok=True)
        for episode in episodes:
            meta = os.path.join(path, "@eaDir", f"{episode}.mkv")
            os.makedirs(meta, exist_ok=True)
            with open(os.path.join(meta, "SYNOINDEX_MEDIA_INFO"), "w",
                      encoding="utf-8") as handle:
                handle.write("meta")
        return path

    @staticmethod
    def make_file(path, content=b"x"):
        """创建真实文件。"""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(content)
        return path


class TestSweepEmptyDirs(_SweepBase):
    """空壳目录的判定与清理。"""

    def test_delete_shell_with_only_eadir(self):
        """只剩 @eaDir 残片的目录应被整体删除（含 @eaDir）。"""
        shell = self.make_shell(os.path.join(self.dl, "Show.S01"))
        stats = self.make_plugin()._sweep_empty_dirs(False)
        self.assertEqual(stats["dirs"], 1)
        self.assertFalse(os.path.exists(shell))
        self.assertEqual(len(stats["_lines"]), 1)
        self.assertIn("已删除空壳目录", stats["_lines"][0])

    def test_keep_dir_with_real_file(self):
        """目录内有真实文件时绝不删除。"""
        target = os.path.join(self.dl, "Show.S01")
        self.make_shell(target)
        self.make_file(os.path.join(target, "E01.mkv"))
        stats = self.make_plugin()._sweep_empty_dirs(False)
        self.assertEqual(stats["dirs"], 0)
        self.assertTrue(os.path.exists(os.path.join(target, "E01.mkv")))
        # 真实文件对应的索引必须保留（不是残片）
        self.assertTrue(
            os.path.exists(os.path.join(target, "@eaDir", "E01.mkv",
                                        "SYNOINDEX_MEDIA_INFO"))
        )

    def test_keep_user_data_inside_eadir(self):
        """@eaDir 内有无法识别的条目（可能是用户数据）时保留整个目录。"""
        target = self.make_shell(os.path.join(self.dl, "Show.S01"))
        with open(os.path.join(target, "@eaDir", "notes.txt"), "w",
                  encoding="utf-8") as handle:
            handle.write("user data")
        stats = self.make_plugin()._sweep_empty_dirs(False)
        self.assertEqual(stats["dirs"], 0)
        self.assertTrue(os.path.exists(os.path.join(target, "@eaDir", "notes.txt")))

    def test_keep_symlink(self):
        """符号链接一律视为内容：不跟随、不删除目录。"""
        target = os.path.join(self.dl, "Show.S01")
        os.makedirs(target)
        link = os.path.join(target, "E01.mkv")
        os.symlink(os.path.join(self.base, "elsewhere.mkv"), link)
        stats = self.make_plugin()._sweep_empty_dirs(False)
        self.assertEqual(stats["dirs"], 0)
        self.assertTrue(os.path.islink(link))

    def test_nested_shell_removed_parent_kept(self):
        """深层空壳应删除，但其仍有文件的父目录必须保留。"""
        show = os.path.join(self.dl, "Show.S01")
        season = os.path.join(show, "Season 1")
        self.make_shell(season, episodes=("E01",))
        self.make_file(os.path.join(show, "poster.jpg"))
        stats = self.make_plugin()._sweep_empty_dirs(False)
        self.assertEqual(stats["dirs"], 1)
        self.assertFalse(os.path.exists(season))
        self.assertTrue(os.path.exists(os.path.join(show, "poster.jpg")))

    def test_root_dir_never_deleted(self):
        """监控根目录本身即使为空也不删。"""
        stats = self.make_plugin(dirs=[self.dl])._sweep_empty_dirs(False)
        self.assertEqual(stats["dirs"], 0)
        self.assertTrue(os.path.isdir(self.dl))

    def test_switch_off_skips_scan(self):
        """开关关闭时不扫描、不删除。"""
        shell = self.make_shell(os.path.join(self.dl, "Show.S01"))
        stats = self.make_plugin(sweep=False)._sweep_empty_dirs(False)
        self.assertEqual(stats["dirs"], 0)
        self.assertEqual(stats["checked"], 0)
        self.assertTrue(os.path.isdir(shell))

    def test_dry_run_reports_without_deleting(self):
        """试运行只统计与记录，不做任何删除。"""
        shell = self.make_shell(os.path.join(self.dl, "Show.S01"))
        stats = self.make_plugin()._sweep_empty_dirs(True)
        self.assertEqual(stats["dirs"], 1)
        self.assertTrue(os.path.isdir(shell))
        self.assertIn("将删除空壳目录", stats["_lines"][0])

    def test_outside_scope_untouched(self):
        """监控目录之外的空壳目录不得触碰。"""
        outside = self.make_shell(os.path.join(self.base, "outside", "Show.S01"))
        self.make_plugin(dirs=[self.dl])._sweep_empty_dirs(False)
        self.assertTrue(os.path.isdir(outside))


class TestSweepIntegration(_SweepBase):
    """与主流程 / 通知计数的集成。"""

    def _run(self, dry_run=False):
        """跑一次完整 check_and_clean，桩掉下载器与磁盘余量。"""
        plugin = self.make_plugin()
        plugin._enabled = True
        plugin._dry_run = dry_run
        orphan_stats = {"torrent": 0, "checked": 0, "alive": 0, "failed": 0}
        with mock.patch.object(SeedSpaceGuard, "_reap_orphan_seeds",
                               return_value=orphan_stats), \
                mock.patch.object(SeedSpaceGuard, "_disk_free_bytes",
                                  return_value=900 * GIB), \
                mock.patch.object(SeedSpaceGuard, "_disk_free_gb",
                                  return_value=900):
            msg = plugin.check_and_clean(source="手动")
        return plugin, msg

    def test_space_sufficient_still_sweeps(self):
        """空间充足时仍执行空壳扫描，并在摘要中体现。"""
        shell = self.make_shell(os.path.join(self.dl, "Show.S01"))
        plugin, msg = self._run()
        self.assertIn("另清理空壳目录 1 个", msg)
        self.assertEqual(plugin._clean_stats["dirs"], 1)
        self.assertFalse(os.path.exists(shell))

    def test_dry_run_note_and_no_delete(self):
        """试运行摘要用「预计清理」，且不动目录。"""
        shell = self.make_shell(os.path.join(self.dl, "Show.S01"))
        plugin, msg = self._run(dry_run=True)
        self.assertIn("另预计清理空壳目录 1 个", msg)
        self.assertEqual(plugin._clean_stats["dirs"], 1)
        self.assertTrue(os.path.isdir(shell))

    def test_notify_text_contains_dir_line(self):
        """通知正文单独列出空壳目录数量。"""
        plugin = self.make_plugin()
        plugin._clean_stats = {
            "files": 0, "transfers": 0, "seeds": 0, "companions": 0,
            "orphans": 0, "dirs": 3, "stalled": False, "dry_run": False,
        }
        text = plugin._build_notify_text("摘要")
        self.assertIn("共清理空壳目录：3 个", text)

    def test_notify_text_dry_run_prefix(self):
        """试运行通知使用「预计」前缀。"""
        plugin = self.make_plugin()
        plugin._clean_stats = {
            "files": 0, "transfers": 0, "seeds": 0, "companions": 0,
            "orphans": 0, "dirs": 2, "stalled": False, "dry_run": True,
        }
        text = plugin._build_notify_text("摘要")
        self.assertIn("预计清理空壳目录：2 个", text)


if __name__ == "__main__":
    unittest.main()
