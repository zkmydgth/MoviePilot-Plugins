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

from app.db.downloadhistory_oper import DownloadHistoryOper
from app.db.transferhistory_oper import TransferHistoryOper
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


class TestLinkageIntegration(_E2EBase):
    """
    联动清理的端到端集成：验证在真实删除流程中确实被触发。

    单元测试已覆盖各联动方法的边界，这里补的是「接线是否接上」——
    避免出现「方法都对，但 _clean_by_file 忘了调用」这类集成缺口。
    """

    def setUp(self):
        super().setUp()
        DownloadHistoryOper.reset()
        TransferHistoryOper.reset()
        self.plugin._downloadhis = DownloadHistoryOper()
        self.plugin._transferhis = TransferHistoryOper()
        self.plugin._delete_torrents = False
        self.plugin._delete_history = False

    def test_linkage_triggered_in_real_clean(self):
        """真实删除流程结束后应自动执行联动清理。"""
        media = self.make_file(os.path.join(self.dl, "阿凡达.mkv"), 2 * 1024 * 1024, 30)
        TransferHistoryOper.add_record(11, "/src/阿凡达.mkv", media)
        DownloadHistoryOper.add_seed("HASH-E2E", [media])

        self.configure([self.dl], threshold=5000, sync_wait=0)
        self.plugin._delete_history = True
        self.plugin._delete_torrents = True

        with self.patch_free([1 * GIB, 10 * GIB, 10 * GIB]), \
                mock.patch.object(SeedSpaceGuard, "_delete_torrent_by_hash",
                                  return_value=True) as patched:
            _count, _released, details = self.plugin._clean_by_file(
                1 * GIB, dry_run=False
            )

        # 文件被清理
        self.assertFalse(os.path.exists(media))
        # 转移记录被删除
        self.assertEqual(TransferHistoryOper.deleted_ids, [11])
        # 该种子文件已全部删除 → 触发了删种
        patched.assert_called_once()
        self.assertEqual(patched.call_args.args[0], "HASH-E2E")
        # 明细中应出现联动条目
        self.assertIn("转移记录", "\n".join(details))

    def test_linkage_off_keeps_everything(self):
        """开关全关时，删除文件不得产生任何联动副作用。"""
        media = self.make_file(os.path.join(self.dl, "阿凡达.mkv"), 2 * 1024 * 1024, 30)
        TransferHistoryOper.add_record(12, "/src/阿凡达.mkv", media)
        DownloadHistoryOper.add_seed("HASH-OFF", [media])

        self.configure([self.dl], threshold=5000, sync_wait=0)
        with self.patch_free([1 * GIB, 10 * GIB, 10 * GIB]), \
                mock.patch.object(SeedSpaceGuard, "_delete_torrent_by_hash",
                                  return_value=True) as patched:
            _count, _released, _details = self.plugin._clean_by_file(
                1 * GIB, dry_run=False
            )

        self.assertFalse(os.path.exists(media), "媒体文件应被删除")
        # 联动开关全关 → 不删记录、不删种
        self.assertEqual(TransferHistoryOper.deleted_ids, [])
        patched.assert_not_called()

    def test_linkage_skipped_in_dry_run(self):
        """试运行不得触发任何联动（真删才联动）。"""
        media = self.make_file(os.path.join(self.dl, "阿凡达.mkv"), 2 * 1024 * 1024, 30)
        TransferHistoryOper.add_record(13, "/src/阿凡达.mkv", media)
        DownloadHistoryOper.add_seed("HASH-DRY", [media])

        self.configure([self.dl], threshold=5000, sync_wait=0)
        self.plugin._delete_history = True
        self.plugin._delete_torrents = True

        with self.patch_free([1 * GIB]), \
                mock.patch.object(SeedSpaceGuard, "_delete_torrent_by_hash",
                                  return_value=True) as patched:
            self.plugin._clean_by_file(1 * GIB, dry_run=True)

        self.assertTrue(os.path.exists(media))
        self.assertEqual(TransferHistoryOper.deleted_ids, [])
        patched.assert_not_called()

    def test_partial_deletion_keeps_torrent(self):
        """
        种子尚有文件未删 → 保留种子（核心约束的集成验证）。

        构造一个含两个文件的种子，但只有其中一个够旧、会被清理；
        另一个因在保护期内被跳过。此时**不得**删除该种子。
        """
        old = self.make_file(os.path.join(self.dl, "old.mkv"), 2 * 1024 * 1024, 30)
        recent = self.make_file(os.path.join(self.dl, "recent.mkv"), 10, 0)
        DownloadHistoryOper.add_seed("HASH-PART", [old, recent])

        self.configure([self.dl], threshold=5000, sync_wait=0)
        self.plugin._recent_skip_days = 1  # recent 在保护期内，不会被选入
        self.plugin._delete_torrents = True

        with self.patch_free([1 * GIB, 10 * GIB, 10 * GIB]), \
                mock.patch.object(SeedSpaceGuard, "_delete_torrent_by_hash",
                                  return_value=True) as patched:
            self.plugin._clean_by_file(1 * GIB, dry_run=False)

        self.assertFalse(os.path.exists(old), "旧文件应被删除")
        self.assertTrue(os.path.exists(recent), "保护期内文件应保留")
        patched.assert_not_called()


class TestProtectPatternIntegration(_E2EBase):
    """
    「保护文件后缀」的端到端验证。

    走 ``_clean_by_file`` 的真实解析路径（``_protect_pattern`` 字符串 →
    pattern 列表），确保**每个**后缀都生效。若解析只取第一个 pattern，
    后续后缀会静默失效，导致下载中的临时文件被误删。
    """

    def test_all_protect_patterns_effective(self):
        """配置字符串中的每个后缀都必须真正挡住文件。"""
        # 视频文件（可删）与各类受保护的临时文件
        media = self.make_file(os.path.join(self.dl, "movie.mkv"),
                               2 * 1024 * 1024, 30)
        guarded = [
            self.make_file(os.path.join(self.dl, name), 1, 30)
            for name in ("a.part", "b.!qb", "c.download",
                         "d.aria2", "e.tmp", "f.crdownload")
        ]
        self.configure([self.dl], threshold=5000, sync_wait=0)
        self.plugin._protect_pattern = (
            "*.part|*.!qb|*.download|*.aria2|*.tmp|*.crdownload"
        )

        with self.patch_free([1 * GIB, 10 * GIB, 10 * GIB]):
            _count, _released, _details = self.plugin._clean_by_file(
                1 * GIB, dry_run=False
            )

        self.assertFalse(os.path.exists(media), "普通视频应被删除")
        for path in guarded:
            self.assertTrue(
                os.path.exists(path),
                f"受保护文件被误删：{os.path.basename(path)}"
                f"（保护后缀未全部生效）",
            )

    def test_comma_separated_protect_pattern(self):
        """逗号分隔的保护后缀同样必须全部生效。"""
        media = self.make_file(os.path.join(self.dl, "movie.mkv"),
                               2 * 1024 * 1024, 30)
        guard = self.make_file(os.path.join(self.dl, "x.tmp"), 1, 30)
        self.configure([self.dl], threshold=5000, sync_wait=0)
        self.plugin._protect_pattern = "*.part,*.tmp"

        with self.patch_free([1 * GIB, 10 * GIB, 10 * GIB]):
            self.plugin._clean_by_file(1 * GIB, dry_run=False)

        self.assertFalse(os.path.exists(media))
        self.assertTrue(os.path.exists(guard), "逗号分隔的第二个后缀未生效")


class TestDryRunFullPreview(_E2EBase):
    """
    试运行的「如实预告」必须覆盖全部三类动作，缺一不可。

    v1.3.5 的空壳回收在试运行下整段跳过，导致用户拿到的预演「只有文件、
    没有种子」，与实际执行存在落差。这里验证：空间不足时，试运行应当同时
    预告「将删除的文件」与「将回收的空壳种子」。
    """

    class _SeedServer:
        """伪下载器：持有指定种子，记录删种调用。"""

        def __init__(self):
            self.torrents = []
            self.removed = []

        def add(self, path, hash_str, name):
            self.torrents.append({
                "progress": 1.0,
                "content_path": path,
                "hash": hash_str,
                "name": name,
                "completion_on": int(time.time()) - 30 * 86400,
                "size": 1024 ** 3,
            })

        def get_torrents(self, *args, **kwargs):
            return list(self.torrents)

        def list_torrents(self, hashs=None, *args, **kwargs):
            want = set(hashs or [])
            return [t for t in self.torrents if not want or t["hash"] in want]

        def remove_torrents(self, hashs=None, delete_file=False, downloader=None):
            self.removed.append((list(hashs or []), delete_file, downloader))
            return True

    def _setup(self):
        """造一个空壳种子，并放一个大文件制造「空间不足需删文件」。"""
        from app.core.module import ModuleManager
        from app.schemas.types import DownloaderType

        ModuleManager.reset()
        DownloadHistoryOper.reset()
        server = self._SeedServer()
        seed_dir = os.path.join(self.dl, "OrphanCell")
        os.makedirs(seed_dir, exist_ok=True)
        gone = os.path.join(seed_dir, "a.mkv")
        DownloadHistoryOper.add_seed("HASH_ORPHAN_E2E", [gone])  # 记录在、文件不在
        server.add(seed_dir, "HASH_ORPHAN_E2E", "OrphanCell")
        ModuleManager.register_downloader(
            DownloaderType.Qbittorrent, "qb", server
        )
        self.plugin._downloadhis = DownloadHistoryOper()
        big = self.make_file(os.path.join(self.dl, "big.mkv"),
                             3 * 1024 * 1024, 30)
        self.configure([self.dl], threshold=500, mode="file", sync_wait=0)
        self.plugin._delete_torrents = True
        return server, big

    def tearDown(self):
        from app.core.module import ModuleManager
        ModuleManager.reset()
        super().tearDown()

    def test_dry_run_previews_both_file_and_orphan(self):
        """试运行应同时预告待删文件与待回收空壳，且两者都不真做。"""
        server, big = self._setup()

        with self.patch_free([1 * GIB]):
            msg = self.plugin.check_and_clean(
                source="手动", dry_run_override=True
            )

        self.assertTrue(os.path.exists(big), "试运行绝不允许删文件")
        self.assertEqual(server.removed, [], "试运行绝不允许删种子")
        self.assertTrue(msg.startswith("[试运行]"), f"缺前缀：{msg}")
        self.assertIn("预计回收空壳种子 1 个", msg, "试运行应预告空壳回收")

    def test_dry_run_and_real_report_same_orphan_count(self):
        """同场景下，试运行预告的空壳数与正式跑的实删数必须一致。"""
        server, _big = self._setup()

        with self.patch_free([1 * GIB]):
            preview = self.plugin.check_and_clean(
                source="手动", dry_run_override=True
            )
        self.assertIn("预计回收空壳种子 1 个", preview)
        self.assertEqual(server.removed, [], "试运行不得留下删种痕迹")

        with self.patch_free([1 * GIB, 10 * GIB, 10 * GIB]):
            self.plugin.check_and_clean(source="手动", dry_run_override=False)
        self.assertEqual(
            [h for h, _, _ in server.removed], [["HASH_ORPHAN_E2E"]],
            "正式跑实删数应与试运行预告一致",
        )

    def test_real_run_message_uses_clean_prefix(self):
        """正式清理的消息应带 [清理] 前缀，与试运行可辨。"""
        server, _big = self._setup()
        with self.patch_free([1 * GIB, 10 * GIB, 10 * GIB]):
            msg = self.plugin.check_and_clean(
                source="手动", dry_run_override=False
            )
        self.assertTrue(msg.startswith("[清理]"), f"缺前缀：{msg}")


if __name__ == "__main__":
    unittest.main()
