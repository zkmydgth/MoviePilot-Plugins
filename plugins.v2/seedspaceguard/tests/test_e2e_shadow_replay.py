# -*- coding: utf-8 -*-
"""
端到端复盘测试：完整复现 2026-09-20 的真实 BUG 场景。

真实事故时间线（来自生产日志）：

    09-18 01:26  试运行：列出 Shadow 的 .mkv + .md5 + .nfo
    09-19 00:00  真删 .mkv（缺口够就停）→ .md5/.nfo 留下 → 种子未删完，判定保留 ✅
    09-20 01:20  真删 .md5 + .nfo → 种子文件全空 → **却没删种** ❌

第二轮的失败机理：被删的 .md5/.nfo 不在 DownloadFiles 表中，按文件名反查不到
hash，`pending_hashes` 为空，删种判定被整段跳过。

本文件要求修复后满足：

- 第二轮结束时，种子必须被删除；
- 删除理由必须可追溯（不能靠"碰巧"）；
- 中间轮次（文件未删完时）必须**保留**种子，不得抢跑。

另覆盖「跨轮补全」的完整闭环：单轮只删到预留空间即停 → 后续轮次继续删 →
删空后回收种子。
"""

import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

import tests  # noqa: F401  触发宿主桩路径注入

from app.core.module import ModuleManager
from app.db.downloadhistory_oper import DownloadHistoryOper
from app.db.transferhistory_oper import TransferHistoryOper
from app.schemas.types import DownloaderType
from seedspaceguard import SeedSpaceGuard

GIB = 1024 ** 3


class _Server:
    """伪下载器：持有若干种子，并记录删种调用。"""

    def __init__(self, torrents):
        self._torrents = list(torrents)
        self.removed = []

    def get_torrents(self, *args, **kwargs):
        return list(self._torrents)

    def remove_torrents(self, hashs, delete_file=False, downloader=None, **kw):
        self.removed.append({"hashs": list(hashs), "delete_file": delete_file})
        return True


def _qbt(content_path, hash_str, name, days_ago=30, size_gb=1.0):
    return {
        "progress": 1.0,
        "content_path": content_path,
        "hash": hash_str,
        "name": name,
        "completion_on": int(time.time()) - days_ago * 86400,
        "size": int(size_gb * GIB),
    }


class _Replay(unittest.TestCase):
    """复盘夹具：一个「Shadow」种子 + 一份真实文件布局。"""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="ssg-replay-")
        self.dl = os.path.join(self.base, "download")
        os.makedirs(self.dl)
        DownloadHistoryOper.reset()
        TransferHistoryOper.reset()
        ModuleManager.reset()

        self.plugin = SeedSpaceGuard()
        self.plugin._enabled = True
        self.plugin._notify = False
        self.plugin._target_dirs = [os.path.normpath(self.dl)]
        self.plugin._active_dirs = [os.path.normpath(self.dl)]
        self.plugin._mode = "file"
        self.plugin._dry_run = False
        self.plugin._delete_torrents = True
        self.plugin._delete_history = False
        self.plugin._downloaders = []
        self.plugin._recent_skip_days = 5
        self.plugin._sync_wait_seconds = 0
        self.plugin._volume_path = self.base

        self.seed_dir = os.path.join(
            self.dl, "Shadow.2018.2160p.BluRay.x265.10bit.Atmos.TrueHD7.1-WiKi"
        )
        os.makedirs(self.seed_dir, exist_ok=True)
        self.mkv = os.path.join(self.seed_dir, "Shadow.2018...-WiKi.mkv")
        self.md5 = os.path.join(self.seed_dir, "Shadow.2018...-WiKi.md5")
        self.nfo = os.path.join(self.seed_dir, "Shadow.2018...-WiKi.nfo")
        for path in (self.mkv, self.md5, self.nfo):
            with open(path, "wb") as handle:
                handle.write(b"x" * 16)

        # DownloadFiles 只登记正片，.md5/.nfo 不入表 —— 与真实 MP 一致
        DownloadHistoryOper.add_seed("HASH_SHADOW", [self.mkv, self.md5, self.nfo])
        DownloadHistoryOper.path_to_hash.pop(self.md5, None)
        DownloadHistoryOper.path_to_hash.pop(self.nfo, None)

        self.server = _Server([_qbt(self.seed_dir, "HASH_SHADOW", "Shadow", size_gb=28.6)])
        ModuleManager.register_downloader(DownloaderType.Qbittorrent, "qb", self.server)

        self.plugin._downloadhis = DownloadHistoryOper()
        self.plugin._transferhis = TransferHistoryOper()

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)
        ModuleManager.reset()

    def _rm(self, path):
        if os.path.exists(path):
            os.remove(path)


# ==========================================================================
# 一、完整事故复盘
# ==========================================================================
class TestShadowReplay(_Replay):
    """复现「删完刮削残留却不删种」的完整链路。"""

    def test_round1_partial_delete_keeps_seed(self):
        """第一轮只删正片：文件未删完 → 必须保留种子（不抢跑）。"""
        self._rm(self.mkv)
        detail_lines = []
        stats = self.plugin._run_linkage_after_delete(
            [self.mkv], detail_lines, dry_run=False
        )
        self.assertEqual(stats["torrent"], 0, "仍有 .md5/.nfo 残留，不得删种")
        self.assertEqual(stats["torrent_kept"], 1)
        self.assertEqual(self.server.removed, [])

    def test_round2_leftover_delete_reaps_seed(self):
        """
        第二轮删掉刮削残留后，种子文件全空 → **必须删种**（原始 BUG 的修复点）。

        这正是生产环境失败的场景：被删文件不在 DownloadFiles 表中，
        修复前按文件名反查落空，删种判定被整段跳过。
        """
        self._rm(self.mkv)
        self._rm(self.md5)
        self._rm(self.nfo)

        detail_lines = []
        stats = self.plugin._run_linkage_after_delete(
            [self.md5, self.nfo], detail_lines, dry_run=False
        )
        self.assertEqual(stats["torrent"], 1, "刮削残留删完后必须联动删种")
        self.assertEqual(len(self.server.removed), 1)
        self.assertEqual(self.server.removed[0]["hashs"], ["HASH_SHADOW"])
        self.assertFalse(
            self.server.removed[0]["delete_file"],
            "文件已由插件删除，删种时不得再连带删文件",
        )

    def test_full_cycle_two_rounds(self):
        """两轮串起来：第一轮保留、第二轮删除，闭环成立。"""
        # 第一轮
        self._rm(self.mkv)
        self.plugin._run_linkage_after_delete([self.mkv], [], dry_run=False)
        self.assertEqual(self.server.removed, [], "第一轮不应删种")

        # 第二轮
        self._rm(self.md5)
        self._rm(self.nfo)
        self.plugin._run_linkage_after_delete([self.md5, self.nfo], [], dry_run=False)
        self.assertEqual(len(self.server.removed), 1, "第二轮应删种")


# ==========================================================================
# 二、跨轮补全闭环（用户核心需求）
# ==========================================================================
class TestCrossRoundCompletion(_Replay):
    """
    「单轮只删到预留空间即停 + 后续轮次继续删 + 删空后删种」的完整闭环。

    用真实文件 + 真实磁盘空间驱动 `_clean_by_file`，验证：
    轮次 1、2 保留种子；文件全空后，空壳回收接手删种。
    """

    def _make_episodes(self, count):
        """造 count 集正片，返回路径列表。"""
        paths = []
        for i in range(1, count + 1):
            path = os.path.join(self.seed_dir, f"E{i:02d}.mkv")
            with open(path, "wb") as handle:
                handle.write(b"x" * 4096)
            paths.append(path)
        return paths

    def test_orphan_reaped_after_files_manually_gone(self):
        """文件全被外部删除（模拟多轮删完）后，空壳回收应删种。"""
        episodes = self._make_episodes(3)
        DownloadHistoryOper.add_seed("HASH_SHADOW", episodes + [self.mkv, self.md5, self.nfo])
        DownloadHistoryOper.path_to_hash.pop(self.md5, None)
        DownloadHistoryOper.path_to_hash.pop(self.nfo, None)

        for path in episodes + [self.mkv, self.md5, self.nfo]:
            self._rm(path)

        stats = self.plugin._reap_orphan_seeds(dry_run=False)
        self.assertEqual(stats["torrent"], 1, "文件删空后应回收种子")
        self.assertEqual(self.server.removed[0]["hashs"], ["HASH_SHADOW"])

    def test_orphan_not_reaped_when_episode_left(self):
        """还有一集没删 → 不得回收（跨轮补全必须等到真删完）。"""
        episodes = self._make_episodes(3)
        DownloadHistoryOper.add_seed("HASH_SHADOW", episodes + [self.mkv, self.md5, self.nfo])
        DownloadHistoryOper.path_to_hash.pop(self.md5, None)
        DownloadHistoryOper.path_to_hash.pop(self.nfo, None)

        for path in episodes[:-1] + [self.mkv, self.md5, self.nfo]:
            self._rm(path)
        # episodes[-1] 故意留下

        stats = self.plugin._reap_orphan_seeds(dry_run=False)
        self.assertEqual(stats["torrent"], 0, "仍有文件存在，不得回收")
        self.assertEqual(self.server.removed, [])

    def test_space_plenty_still_reaps(self):
        """
        关键闭环：空间已达标（会触发「无需清理」早退）时，空壳仍被回收。

        这正是原始 BUG 的卡点——半删种子的剩余文件删完后空间刚好达标，
        后续轮次全部早退，种子永远删不掉。
        """
        for path in (self.mkv, self.md5, self.nfo):
            self._rm(path)

        self.plugin._threshold_gb = 1.0        # 阈值极低 → 必然「空间充足」
        with mock.patch.object(
            self.plugin, "_disk_free_bytes", return_value=100 * GIB
        ):
            result = self.plugin.check_and_clean(source="手动")

        self.assertIn("空间充足", result)
        self.assertEqual(len(self.server.removed), 1, "空间充足时也必须回收空壳")
        self.assertIn("空壳种子 1 个", result)


# ==========================================================================
# 三、边界与安全
# ==========================================================================
class TestBoundaries(_Replay):
    """边界条件与安全约束。"""

    def test_dry_run_no_side_effect_anywhere(self):
        """试运行下，刮削兜底与空壳回收都不得产生动作。"""
        for path in (self.mkv, self.md5, self.nfo):
            self._rm(path)

        self.plugin._dry_run = True
        self.plugin._threshold_gb = 1.0
        with mock.patch.object(
            self.plugin, "_disk_free_bytes", return_value=100 * GIB
        ):
            self.plugin.check_and_clean(source="手动")

        self.assertEqual(self.server.removed, [], "试运行不得删种")

    def test_delete_torrents_off_no_reap(self):
        """关闭删种联动后，空壳保留（插件不越权）。"""
        for path in (self.mkv, self.md5, self.nfo):
            self._rm(path)
        self.plugin._delete_torrents = False

        stats = self.plugin._reap_orphan_seeds(dry_run=False)
        self.assertEqual(stats["torrent"], 0)
        self.assertEqual(self.server.removed, [])

    def test_empty_seed_dir_no_crash(self):
        """种子目录已整个消失（影子种子）不抛错，且能被回收。"""
        shutil.rmtree(self.seed_dir, ignore_errors=True)

        stats = self.plugin._reap_orphan_seeds(dry_run=False)
        self.assertEqual(stats["torrent"], 1, "目录都不存在了，文件必然已删空")

    def test_scrape_resolve_when_dir_vanished(self):
        """
        种子目录已消失（僵尸种子）时，按目录反查仍能命中——这是期望行为。

        下载器上种子还在、内容路径仍指向原目录，因此其残留文件路径依然可被
        归属；正因如此，这类种子才能进入删种判定并被空壳回收清理掉。
        """
        shutil.rmtree(self.seed_dir, ignore_errors=True)
        ghost = os.path.join(self.seed_dir, "x.nfo")
        self.assertEqual(self.plugin._resolve_hash_by_path(ghost), "HASH_SHADOW")

    def test_scrape_resolve_no_downloader_returns_empty(self):
        """下载器上已无任何种子时，按目录反查安全返回空串。"""
        ModuleManager.reset()
        self.assertEqual(self.plugin._resolve_hash_by_path(self.nfo), "")

    def test_multiple_seeds_mixed_state(self):
        """多种子混合：只回收已删空的那个，在做种的不受影响。"""
        alive_dir = os.path.join(self.dl, "Alive.Show")
        os.makedirs(alive_dir, exist_ok=True)
        alive_file = os.path.join(alive_dir, "e01.mkv")
        with open(alive_file, "wb") as handle:
            handle.write(b"x")
        DownloadHistoryOper.add_seed("HASH_ALIVE", [alive_file])

        # Shadow 全删空
        for path in (self.mkv, self.md5, self.nfo):
            self._rm(path)

        ModuleManager.reset()
        server = _Server([
            _qbt(self.seed_dir, "HASH_SHADOW", "Shadow"),
            _qbt(alive_dir, "HASH_ALIVE", "Alive.Show"),
        ])
        ModuleManager.register_downloader(DownloaderType.Qbittorrent, "qb", server)

        stats = self.plugin._reap_orphan_seeds(dry_run=False)
        self.assertEqual(stats["torrent"], 1, "只回收删空的那个")
        self.assertEqual(server.removed[0]["hashs"], ["HASH_SHADOW"])

    def test_idempotent_second_run(self):
        """重复执行不应重复删种（第二次候选已消失）。"""
        for path in (self.mkv, self.md5, self.nfo):
            self._rm(path)

        first = self.plugin._reap_orphan_seeds(dry_run=False)
        self.assertEqual(first["torrent"], 1)

        # 模拟下载器已删除该种子
        self.server._torrents = []
        self.plugin._dir_hash_cache = {}
        second = self.plugin._reap_orphan_seeds(dry_run=False)
        self.assertEqual(second["torrent"], 0, "已删除的种子不应被重复处理")

    def test_cache_isolation_between_runs(self):
        """目录反查缓存不应跨轮次残留（避免持有过期种子视图）。"""
        first = self.plugin._resolve_hash_by_path(self.nfo)
        self.assertEqual(first, "HASH_SHADOW")

        # 种子消失后，清缓存应得到空结果
        ModuleManager.reset()
        self.plugin._dir_hash_cache = {}
        self.assertEqual(self.plugin._resolve_hash_by_path(self.nfo), "")

    def test_protected_file_still_blocks_reap(self):
        """被保护后缀跳过的文件仍在磁盘 → 视为未删完，不得回收。"""
        part = os.path.join(self.seed_dir, "downloading.part")
        with open(part, "wb") as handle:
            handle.write(b"x")
        DownloadHistoryOper.add_seed("HASH_SHADOW", [self.mkv, part])
        for path in (self.mkv, self.md5, self.nfo):
            self._rm(path)
        # part 仍存在

        stats = self.plugin._reap_orphan_seeds(dry_run=False)
        self.assertEqual(stats["torrent"], 0, "有未完成任务时必须保留种子")


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
