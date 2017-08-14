# -*- coding: utf-8 -*-
"""
端到端回归测试：驱动完整的 check_and_clean 流程。

覆盖「空间不足触发清理 → 删除 → 复核」的主链路，以及「空间未释放即停止」
这一新增的安全兜底。通过 monkeypatch 注入可控的磁盘剩余空间，使测试无需
真的把盘写满。
"""

import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

import tests  # noqa: F401  触发宿主桩路径注入

from seedspaceguard import SeedSpaceGuard, GIB

PROTECT_PATTERN = "*.part|*.!qb|*.download|*.aria2|*.tmp|*.crdownload"


class _E2EBase(unittest.TestCase):
    """端到端夹具：真实目录 + 可控的剩余空间。"""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="ssg-e2e-")
        self.dl = os.path.join(self.base, "download")
        self.lib = os.path.join(self.base, "library")
        for path in (self.dl, self.lib):
            os.makedirs(path)
        self.plugin = SeedSpaceGuard()
        # 用真实临时目录作为卷路径，避免依赖宿主环境
        self.plugin._volume_path = self.base

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    # ------------------------------------------------------------------
    def make_file(self, path, size_kb=64, age_days=10):
        """
        创建指定大小的文件并回拨 mtime。

        使用 ``truncate``（稀疏文件）而非真实写入：测试需要 GB 级「名义大小」
        来越过释放判定的硬停止下限（默认 1GB），但不必真的占用磁盘空间。

        :param path: 文件路径
        :param size_kb: 名义大小（KB）
        :param age_days: 距今的天数（用于绕过保护期）
        """
        with open(path, "wb") as handle:
            handle.truncate(size_kb * 1024)
        stamp = time.time() - age_days * 86400
        os.utime(path, (stamp, stamp))
        return path

    def configure(self, dirs, threshold=500, mode="file", sync_wait=0):
        """配置插件（绕过 init_plugin 以便直接控制内部状态）。"""
        self.plugin._enabled = True
        self.plugin._target_dirs = [os.path.normpath(d) for d in dirs]
        self.plugin._active_dirs = [
            d for d in self.plugin._target_dirs if os.path.isdir(d)
        ]
        self.plugin._threshold_gb = threshold
        self.plugin._mode = mode
        self.plugin._sync_wait_seconds = sync_wait
        self.plugin._recent_skip_days = 1
        self.plugin._notify = False
        self.plugin._dry_run = False

    def patch_free(self, values):
        """
        注入磁盘剩余空间的返回值序列（字节）。

        :param values: 依次返回的值，耗尽后重复最后一个
        """
        seq = list(values)

        def fake_free():
            return seq.pop(0) if len(seq) > 1 else seq[0]

        return mock.patch.object(
            SeedSpaceGuard, "_disk_free_bytes", side_effect=lambda: fake_free()
        )


class TestCleanByFile(_E2EBase):
    """仅文件模式的端到端清理。"""

    def test_dry_run_deletes_nothing(self):
        """试运行应只列出清单，不实际删除。"""
        path = self.make_file(os.path.join(self.dl, "movie.mkv"), 1024, 30)
        self.configure([self.dl], threshold=500)

        with self.patch_free([1 * GIB]):
            result = self.plugin._clean_by_file(1 * GIB, dry_run=True)

        count, _released, details = result
        self.assertEqual(count, 1)
        self.assertTrue(details[0].startswith("[试运行]"))
        self.assertTrue(os.path.exists(path), "试运行不应删除文件")

    def test_deletes_hardlink_pair(self):
        """真实删除应移除硬链接两侧。"""
        src = self.make_file(os.path.join(self.dl, "movie.mkv"), 512, 30)
        os.link(src, os.path.join(self.lib, "movie.mkv"))
        self.configure([self.dl, self.lib], threshold=5000, sync_wait=0)

        # 释放量充足（模拟成功回收），使循环在下一轮因达标而退出
        with self.patch_free([1 * GIB, 10 * GIB, 10 * GIB]):
            result = self.plugin._clean_by_file(1 * GIB, dry_run=False)

        count, _released, _details = result
        self.assertEqual(count, 1)
        self.assertFalse(os.path.exists(os.path.join(self.dl, "movie.mkv")))
        self.assertFalse(os.path.exists(os.path.join(self.lib, "movie.mkv")))

    def test_stops_when_space_not_released(self):
        """
        空间未释放时应立即停止并给出可操作告警。

        夹具使用 GB 级稀疏文件：释放判定的硬停止下限为 1GB，名义释放量需
        越过该下限才会启用「未释放即停止」保护（小体量文件被静默容忍，避免
        测量噪声误伤）。
        """
        # 每个 2GB，共 4 个；缺口 = 5000 - 1024 = 3976GB 时一轮即满足预选
        for name in ("a.mkv", "b.mkv", "c.mkv", "d.mkv"):
            self.make_file(os.path.join(self.dl, name), 2 * 1024 * 1024, 30)
        self.configure([self.dl], threshold=5000, sync_wait=0)

        # 剩余空间始终不变（完全未释放）
        with self.patch_free([1 * GIB]):
            result = self.plugin._clean_by_file(1 * GIB, dry_run=False)

        count, _released, details = result
        self.assertEqual(count, 4, "应只执行一轮，不再继续补删")
        self.assertTrue(
            any("空间未按预期释放" in line for line in details),
            f"应给出未释放告警，实际明细: {details}",
        )
        self.assertTrue(
            any("硬链接" in line or "快照" in line for line in details),
            "告警应说明可能原因",
        )

    def test_warning_mentions_dir_configuration(self):
        """未释放告警应提示检查目录配置。"""
        self.make_file(os.path.join(self.dl, "x.mkv"), 4 * 1024 * 1024, 30)
        self.configure([self.dl], threshold=5000, sync_wait=0)

        with self.patch_free([1 * GIB]):
            _count, _released, details = self.plugin._clean_by_file(
                1 * GIB, dry_run=False
            )

        joined = "\n".join(details)
        self.assertIn("目录", joined, "告警应引导用户检查目录配置")

    def test_small_file_group_tolerated(self):
        """
        小文件组（名义释放量低于硬停止下限）不应触发未释放告警。

        这是刻意的设计：小体量的空间波动容易被后台写入淹没，若据此停止会
        误伤正常清理。
        """
        self.make_file(os.path.join(self.dl, "small.mkv"), 2048, 30)
        self.configure([self.dl], threshold=5000, sync_wait=0)

        with self.patch_free([1 * GIB]):
            _count, _released, details = self.plugin._clean_by_file(
                1 * GIB, dry_run=False
            )

        self.assertFalse(
            any("空间未按预期释放" in line for line in details),
            "小文件组不应触发硬停止告警",
        )

    def test_exits_early_when_threshold_met(self):
        """空间已达标时应直接返回，不删除任何文件。"""
        path = self.make_file(os.path.join(self.dl, "keep.mkv"), 1024, 30)
        self.configure([self.dl], threshold=5, sync_wait=0)

        with self.patch_free([10 * GIB]):
            result = self.plugin._clean_by_file(10 * GIB, dry_run=False)

        count, _released, _details = result
        self.assertEqual(count, 0)
        self.assertTrue(os.path.exists(path), "空间充足时不应删除")

    def test_protect_pattern_respected(self):
        """保护后缀的文件不应被删除。"""
        part = self.make_file(os.path.join(self.dl, "x.part"), 4096, 30)
        self.configure([self.dl], threshold=5000, sync_wait=0)

        with self.patch_free([1 * GIB]):
            self.plugin._clean_by_file(1 * GIB, dry_run=False)

        self.assertTrue(os.path.exists(part), "保护后缀文件必须保留")

    def test_recent_file_respected(self):
        """保护期内的文件不应被删除。"""
        fresh = self.make_file(os.path.join(self.dl, "fresh.mkv"), 4096, 0.01)
        self.configure([self.dl], threshold=5000, sync_wait=0)

        with self.patch_free([1 * GIB]):
            self.plugin._clean_by_file(1 * GIB, dry_run=False)

        self.assertTrue(os.path.exists(fresh), "保护期内文件必须保留")


class TestCheckAndClean(_E2EBase):
    """check_and_clean 主流程。"""

    def test_disabled_plugin_skipped(self):
        """未启用时定时触发应直接返回。"""
        self.plugin._enabled = False
        self.assertEqual(self.plugin.check_and_clean(source="定时"), "插件未启用")

    def test_no_dirs_configured(self):
        """未配置目录时应给出明确提示。"""
        self.plugin._enabled = True
        self.plugin._target_dirs = []
        self.plugin._notify = False

        result = self.plugin.check_and_clean(source="手动")

        self.assertIn("未配置清理目录", result)

    def test_all_dirs_missing(self):
        """所有目录都不存在时应中止并提示。"""
        self.plugin._enabled = True
        self.plugin._target_dirs = ["/nonexistent/aaa", "/nonexistent/bbb"]
        self.plugin._notify = False

        result = self.plugin.check_and_clean(source="手动")

        self.assertIn("不存在", result)

    def test_partial_dirs_missing_continues(self):
        """部分目录缺失时应跳过无效目录、继续处理有效目录。"""
        self.make_file(os.path.join(self.dl, "a.mkv"), 1024, 30)
        self.plugin._enabled = True
        self.plugin._target_dirs = ["/nonexistent/aaa", self.dl]
        self.plugin._threshold_gb = 500
        self.plugin._sync_wait_seconds = 0
        self.plugin._notify = False
        self.plugin._dry_run = True

        with self.patch_free([1 * GIB]):
            result = self.plugin.check_and_clean(source="手动")

        self.assertIn("/nonexistent", result, "应提示跳过无效目录")
        self.assertEqual(self.plugin._active_dirs, [self.dl])

    def test_plenty_of_space_skips(self):
        """空间充足时应跳过清理。"""
        self.plugin._enabled = True
        self.plugin._target_dirs = [self.dl]
        self.plugin._threshold_gb = 10
        self.plugin._notify = False

        with self.patch_free([100 * GIB]):
            result = self.plugin.check_and_clean(source="手动")

        self.assertIn("空间充足", result)
        self.assertIn("无需清理", result)

    def test_active_dirs_filtered(self):
        """生效目录应为配置目录中真实存在的那部分。"""
        self.make_file(os.path.join(self.dl, "a.mkv"), 1024, 30)
        self.plugin._enabled = True
        self.plugin._target_dirs = [self.dl, self.lib, "/nonexistent/ccc"]
        self.plugin._threshold_gb = 500
        self.plugin._sync_wait_seconds = 0
        self.plugin._notify = False
        self.plugin._dry_run = True

        with self.patch_free([1 * GIB]):
            self.plugin.check_and_clean(source="手动")

        self.assertEqual(set(self.plugin._active_dirs), {self.dl, self.lib})

    def test_unavailable_disk_info(self):
        """无法获取剩余空间时应中止并提示。"""
        self.plugin._enabled = True
        self.plugin._target_dirs = [self.dl]
        self.plugin._notify = False

        with mock.patch.object(SeedSpaceGuard, "_disk_free_bytes", return_value=None):
            result = self.plugin.check_and_clean(source="手动")

        self.assertIn("无法获取", result)

    def test_threshold_bytes_comparison(self):
        """阈值比较应使用字节精度，避免 GB 取整造成边界误判。"""
        self.plugin._enabled = True
        self.plugin._target_dirs = [self.dl]
        self.plugin._threshold_gb = 500
        self.plugin._notify = False
        # 略低于阈值 1 字节：应触发清理流程（而非被判为充足）
        just_below = 500 * GIB - 1
        self.make_file(os.path.join(self.dl, "a.mkv"), 1024, 30)
        self.plugin._dry_run = True
        self.plugin._sync_wait_seconds = 0

        with self.patch_free([just_below]):
            result = self.plugin.check_and_clean(source="手动")

        self.assertNotIn("空间充足", result, "略低于阈值不应判为充足")

    def test_lock_prevents_concurrent_run(self):
        """已持锁时再次触发应被跳过。"""
        self.plugin._enabled = True
        self.plugin._notify = False
        self.plugin._lock.acquire()
        try:
            self.assertEqual(
                self.plugin.check_and_clean(source="手动"),
                "已有清理任务正在运行，本次跳过",
            )
        finally:
            self.plugin._lock.release()

    def test_notification_sent(self):
        """按配置应发送通知。"""
        self.plugin._enabled = True
        self.plugin._target_dirs = [self.dl]
        self.plugin._threshold_gb = 10
        self.plugin._notify = True

        with self.patch_free([100 * GIB]):
            self.plugin.check_and_clean(source="手动")

        # 空间充足时按设计不发通知
        self.assertEqual(len(self.plugin.sent_messages), 0)

    def test_notification_on_clean(self):
        """实际清理后应发送通知。"""
        self.make_file(os.path.join(self.dl, "a.mkv"), 4096, 30)
        self.plugin._enabled = True
        self.plugin._target_dirs = [self.dl]
        self.plugin._threshold_gb = 5000
        self.plugin._sync_wait_seconds = 0
        self.plugin._notify = True

        with self.patch_free([1 * GIB, 10 * GIB, 10 * GIB]):
            self.plugin.check_and_clean(source="手动")

        self.assertGreaterEqual(len(self.plugin.sent_messages), 1)
        self.assertEqual(self.plugin.sent_messages[0]["title"], "保种空间守护")


class TestSeedCandidates(_E2EBase):
    """种子候选的多目录筛选。"""

    def test_candidates_filtered_by_multiple_dirs(self):
        """落在任一配置目录下的种子都应纳入候选。"""
        from app.core.module import ModuleManager
        from app.schemas.types import DownloaderType

        class _Server:
            """伪下载器：返回两个已完成种子，分别位于不同目录。"""

            def get_torrents(self, *args, **kwargs):
                return [
                    {
                        "progress": 1.0,
                        "content_path": os.path.join(self_dl, "a"),
                        "hash": "hash_a",
                        "name": "A",
                        "completion_on": int(time.time()) - 30 * 86400,
                        "size": 1024 ** 3,
                    },
                    {
                        "progress": 1.0,
                        "content_path": os.path.join(self_lib, "b"),
                        "hash": "hash_b",
                        "name": "B",
                        "completion_on": int(time.time()) - 30 * 86400,
                        "size": 1024 ** 3,
                    },
                ]

        self_dl = self.dl
        self_lib = self.lib
        ModuleManager.reset()
        ModuleManager.register_downloader(
            DownloaderType.Qbittorrent, "qb", _Server()
        )
        self.plugin._target_dirs = [self.dl, self.lib]
        self.plugin._active_dirs = [self.dl, self.lib]
        self.plugin._downloaders = []

        candidates = self.plugin._collect_seed_candidates()

        self.assertEqual(len(candidates), 2, "两个目录下的种子都应被收集")
        ModuleManager.reset()

    def test_candidates_exclude_outside(self):
        """配置目录之外的种子不应纳入候选。"""
        from app.core.module import ModuleManager
        from app.schemas.types import DownloaderType

        class _Server:
            """伪下载器：种子位于配置外目录。"""

            def get_torrents(self, *args, **kwargs):
                return [{
                    "progress": 1.0,
                    "content_path": "/somewhere/else/c",
                    "hash": "hash_c",
                    "name": "C",
                    "completion_on": int(time.time()) - 30 * 86400,
                    "size": 1024 ** 3,
                }]

        ModuleManager.reset()
        ModuleManager.register_downloader(
            DownloaderType.Qbittorrent, "qb", _Server()
        )
        self.plugin._target_dirs = [self.dl]
        self.plugin._active_dirs = [self.dl]
        self.plugin._downloaders = []

        candidates = self.plugin._collect_seed_candidates()

        self.assertEqual(candidates, [], "配置外种子不应被收集")
        ModuleManager.reset()

    def test_downloader_filter(self):
        """配置了目标下载器时应只处理选定的。"""
        from app.core.module import ModuleManager
        from app.schemas.types import DownloaderType

        class _Server:
            """伪下载器。"""

            def __init__(self, path):
                self._path = path

            def get_torrents(self, *args, **kwargs):
                return [{
                    "progress": 1.0,
                    "content_path": self._path,
                    "hash": "h",
                    "name": "n",
                    "completion_on": int(time.time()) - 30 * 86400,
                    "size": 1024 ** 3,
                }]

        ModuleManager.reset()
        ModuleManager.register_downloader(
            DownloaderType.Qbittorrent, "qb1", _Server(os.path.join(self.dl, "a"))
        )
        ModuleManager.register_downloader(
            DownloaderType.Qbittorrent, "qb2", _Server(os.path.join(self.dl, "b"))
        )
        self.plugin._target_dirs = [self.dl]
        self.plugin._active_dirs = [self.dl]
        self.plugin._downloaders = ["qb1"]

        candidates = self.plugin._collect_seed_candidates()

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["downloader"], "qb1")
        ModuleManager.reset()


if __name__ == "__main__":
    unittest.main()
