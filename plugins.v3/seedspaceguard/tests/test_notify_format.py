# -*- coding: utf-8 -*-
"""
通知格式与计数聚合回归测试。

锁定用户要求：清理完成后的站内通知只给「删除文件数 / 转移记录数 / 种子数」
三类汇总数字，不罗列每个被删文件的明细——明细全部写入插件日志，由用户自行查阅。

同时覆盖清理流程是否正确聚合 ``_clean_stats``（files / transfers / seeds）。
"""

import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

import tests  # noqa: F401  触发宿主桩路径注入

from app.db.downloadhistory_oper import DownloadHistoryOper
from app.db.transferhistory_oper import TransferHistoryOper
from seedspaceguard import SeedSpaceGuard, GIB


class _NotifyBase(unittest.TestCase):
    """与端到端夹具一致的轻量基座：真实目录 + 可控剩余空间。"""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="ssg-notify-")
        self.dl = os.path.join(self.base, "download")
        self.lib = os.path.join(self.base, "library")
        for path in (self.dl, self.lib):
            os.makedirs(path)
        self.plugin = SeedSpaceGuard()
        # 用真实临时目录作为卷路径，避免依赖宿主环境
        self.plugin._volume_path = self.base

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def make_file(self, path, size_kb=64, age_days=10):
        """创建稀疏文件并回拨 mtime（绕过保护期）。"""
        with open(path, "wb") as handle:
            handle.truncate(size_kb * 1024)
        stamp = time.time() - age_days * 86400
        os.utime(path, (stamp, stamp))
        return path

    def configure(self, dirs, threshold=500, mode="file", sync_wait=0, notify=False):
        """绕过 init_plugin 直接控制内部状态。"""
        self.plugin._enabled = True
        self.plugin._target_dirs = [os.path.normpath(d) for d in dirs]
        self.plugin._active_dirs = [
            d for d in self.plugin._target_dirs if os.path.isdir(d)
        ]
        self.plugin._threshold_gb = threshold
        self.plugin._mode = mode
        self.plugin._sync_wait_seconds = sync_wait
        self.plugin._recent_skip_days = 1
        self.plugin._notify = notify
        self.plugin._dry_run = False

    def patch_free(self, values):
        """注入磁盘剩余空间返回值序列（字节）。"""
        seq = list(values)

        def fake_free():
            return seq.pop(0) if len(seq) > 1 else seq[-1]

        return mock.patch.object(
            SeedSpaceGuard, "_disk_free_bytes", side_effect=lambda: fake_free()
        )


class TestBuildNotifyText(unittest.TestCase):
    """_build_notify_text 的格式契约（不依赖磁盘/下载器）。"""

    def _plugin(self):
        return SeedSpaceGuard()

    def test_real_mode_three_counts_no_detail(self):
        """真实删除：通知含三类计数，且不含任何文件明细路径。"""
        p = self._plugin()
        p._clean_stats = {
            "files": 12, "transfers": 8, "seeds": 3,
            "stalled": False, "dry_run": False,
        }
        text = p._build_notify_text("摘要行")
        self.assertIn("删除文件：12 个", text)
        self.assertIn("删除转移记录：8 条", text)
        self.assertIn("删除种子：3 个", text)
        self.assertNotIn("movie.mkv", text, "文件明细不应进入通知")
        self.assertNotIn("预计", text, "真实删除不加『预计』前缀")
        self.assertTrue(text.startswith("摘要行"))

    def test_dry_run_prefix(self):
        """试运行：三类计数均带『预计』前缀。"""
        p = self._plugin()
        p._clean_stats = {
            "files": 5, "transfers": 0, "seeds": 0,
            "stalled": False, "dry_run": True,
        }
        text = p._build_notify_text("[试运行] 摘要")
        self.assertIn("预计删除文件：5 个", text)
        self.assertIn("预计删除转移记录：0 条", text)
        self.assertIn("预计删除种子：0 个", text)

    def test_stalled_warning(self):
        """空间未如期释放：通知含告警行。"""
        p = self._plugin()
        p._clean_stats = {
            "files": 4, "transfers": 0, "seeds": 0,
            "stalled": True, "dry_run": False,
        }
        text = p._build_notify_text("摘要")
        self.assertIn("⚠️ 空间未如期释放", text)
        self.assertIn("删除文件：4 个", text)

    def test_no_count_returns_msg_only(self):
        """无任何删除计数时，通知正文就是摘要本身（不加空的三计数块）。"""
        p = self._plugin()
        p._clean_stats = {
            "files": 0, "transfers": 0, "seeds": 0,
            "stalled": False, "dry_run": False,
        }
        self.assertEqual(p._build_notify_text("无删除摘要"), "无删除摘要")


class TestCleanStatsPopulated(_NotifyBase):
    """清理流程正确聚合计数（files 等）。"""

    def test_file_mode_populates_files_count(self):
        self.make_file(os.path.join(self.dl, "movie.mkv"), 1024, 30)
        self.configure([self.dl], threshold=500)
        with self.patch_free([1 * GIB, 10 * GIB, 10 * GIB]):
            result = self.plugin._clean_by_file(1 * GIB, dry_run=False)
        deleted, _released, _details = result
        self.assertEqual(deleted, 1)
        self.assertEqual(
            self.plugin._clean_stats["files"], 1, "文件数应与删除数一致"
        )
        self.assertFalse(self.plugin._clean_stats["dry_run"])

    def test_dry_run_sets_flag(self):
        self.make_file(os.path.join(self.dl, "movie.mkv"), 1024, 30)
        self.configure([self.dl], threshold=500)
        with self.patch_free([1 * GIB]):
            result = self.plugin._clean_by_file(1 * GIB, dry_run=True)
        deleted, _released, _details = result
        self.assertEqual(deleted, 1)
        self.assertTrue(self.plugin._clean_stats["dry_run"])
        self.assertEqual(self.plugin._clean_stats["files"], 1)


class TestNotifyContentE2E(_NotifyBase):
    """端到端：check_and_clean 实际发出的通知只含计数，不含文件明细。"""

    def test_notification_has_counts_not_details(self):
        path = self.make_file(
            os.path.join(self.dl, "secret_movie.mkv"), 1024, 30
        )
        # 仅文件模式、不开联动，聚焦「删除文件」计数
        self.configure([self.dl], threshold=500, notify=True)
        captured = {}

        def fake_post(mtype=None, title=None, text=None):
            captured["text"] = text

        with self.patch_free([1 * GIB, 10 * GIB, 10 * GIB]):
            with mock.patch.object(
                SeedSpaceGuard, "post_message", side_effect=fake_post
            ):
                self.plugin.check_and_clean(source="手动")
        self.assertIn("删除文件：1 个", captured["text"])
        self.assertNotIn(
            "secret_movie.mkv", captured["text"], "被删文件名不应出现在通知里"
        )
        self.assertNotIn("/download/", captured["text"])


if __name__ == "__main__":
    unittest.main()
