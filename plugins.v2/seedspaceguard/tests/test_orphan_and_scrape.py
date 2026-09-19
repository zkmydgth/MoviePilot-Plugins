# -*- coding: utf-8 -*-
"""
v1.3.5 专项测试：刮削残留删后不判种 + 空壳种子跨轮回收。

对应两个真实 BUG（2026-09-20 定位）：

BUG-1「刮削残留删完不判种」
    仅文件模式下删掉 ``.md5``/``.nfo`` 这类刮削残留后，插件按文件名去
    ``DownloadFiles`` 反查 hash。该表**只登记正片文件**，残留文件查不到，
    于是 ``if hash_str:`` 判断为假，整段删种判定被静默跳过——即便此刻该种子的
    文件已经**全部删除**，种子依然留在下载器里做种。

BUG-2「空壳种子无人回收」
    半删种子（单轮只删到预留空间即停）的剩余文件要等后续轮次才被删完。
    可一旦删完，空间往往刚好越过阈值，后续轮次以「空间充足」直接早退，
    删种判定再无入口，种子永久卡在下载器里。

修复验证点：
    1. ``_resolve_hash_by_path`` 精确匹配落空时，按「种子目录」前缀兜底归属；
    2. 反查彻底失败时必须有 WARN 日志（不再静默）；
    3. ``_reap_orphan_seeds`` 回收文件已删空的种子，且**不删任何文件**；
    4. 空壳回收在「空间充足」时也要执行（早退之前）；
    5. 保护期内的空壳种子同样回收（文件都没了，无保种价值）。
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


class _FakeServer:
    """伪下载器：可注入种子列表，并记录 remove_torrents 调用。"""

    def __init__(self, torrents):
        self._torrents = list(torrents)
        self.removed = []

    def get_torrents(self, *args, **kwargs):
        return list(self._torrents)

    def remove_torrents(self, hashs, delete_file=False, downloader=None):
        self.removed.append((list(hashs), delete_file, downloader))
        return True


def _torrent(content_path, hash_str, name, done_days_ago=30, size_gb=1):
    """构造一个 qB 已完成种子条目。"""
    return {
        "progress": 1.0,
        "content_path": content_path,
        "hash": hash_str,
        "name": name,
        "completion_on": int(time.time()) - done_days_ago * 86400,
        "size": int(size_gb * 1024 ** 3),
    }


class _Base(unittest.TestCase):
    """公共夹具：真实临时目录 + 内存数据层 + 可注入下载器。"""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="ssg-v135-")
        self.dl = os.path.join(self.base, "download")
        os.makedirs(self.dl)
        DownloadHistoryOper.reset()
        TransferHistoryOper.reset()
        ModuleManager.reset()

        self.plugin = SeedSpaceGuard()
        self.plugin._target_dirs = [os.path.normpath(self.dl)]
        self.plugin._active_dirs = [os.path.normpath(self.dl)]
        self.plugin._mode = "file"
        self.plugin._dry_run = False
        self.plugin._delete_torrents = True
        self.plugin._delete_history = False
        self.plugin._downloaders = []
        self.plugin._recent_skip_days = 5
        self.plugin._threshold_gb = 500.0
        self.plugin._volume_path = self.base
        self.plugin._downloadhis = DownloadHistoryOper()
        self.plugin._transferhis = TransferHistoryOper()

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)
        ModuleManager.reset()

    def make(self, relpath, content=b"x"):
        """在下载目录下创建文件，返回绝对路径。"""
        path = os.path.join(self.dl, relpath)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(content)
        return path


# ==========================================================================
# 一、刮削残留按目录反查 hash（BUG-1）
# ==========================================================================
class TestResolveByParentDir(_Base):
    """精确匹配落空时，用种子目录做前缀兜底归属。"""

    def _register_seed(self, seed_name, hash_str):
        """注册一个种子，并在下载器上挂它。"""
        seed_dir = os.path.join(self.dl, seed_name)
        os.makedirs(seed_dir, exist_ok=True)
        server = _FakeServer([_torrent(seed_dir, hash_str, seed_name)])
        ModuleManager.register_downloader(DownloaderType.Qbittorrent, "qb", server)
        return seed_dir

    def test_scrape_file_resolved_via_parent_dir(self):
        """刮削残留（不在 DownloadFiles 表中）应能按种子目录归属到 hash。"""
        seed_dir = self._register_seed("Shadow.2018", "HASH_SHADOW")
        leftover = os.path.join(seed_dir, "Shadow.2018.nfo")
        # 刻意不登记进 DownloadHistoryOper，模拟「表中无此文件」

        self.assertEqual(
            self.plugin._resolve_hash_by_path(leftover), "HASH_SHADOW",
            "查不到精确记录时应退化为按目录前缀归属",
        )

    def test_md5_leftover_resolved(self):
        """``.md5`` 残留同样应能归属（实测 BUG 的原始文件类型）。"""
        seed_dir = self._register_seed("Movie.WiKi", "HASH_MD5")
        leftover = os.path.join(seed_dir, "Movie.WiKi.md5")
        self.assertEqual(self.plugin._resolve_hash_by_path(leftover), "HASH_MD5")

    def test_exact_match_takes_priority(self):
        """精确命中时不应走目录兜底（正片文件路径优先）。"""
        seed_dir = self._register_seed("S", "HASH_SEED")
        movie = self.make("S/S.mkv")
        # 故意让目录前缀指向另一个 hash，验证精确匹配优先生效
        DownloadHistoryOper.add_seed("HASH_EXACT", [movie])
        self.assertEqual(self.plugin._resolve_hash_by_path(movie), "HASH_EXACT")

    def test_unrelated_path_returns_empty(self):
        """与任何种子目录都不匹配的路径返回空串。"""
        self._register_seed("S", "HASH_SEED")
        stray = os.path.join(self.dl, "别处", "x.nfo")
        self.assertEqual(self.plugin._resolve_hash_by_path(stray), "")

    def test_longest_prefix_wins(self):
        """嵌套目录下取最长（最贴近）的种子目录。"""
        parent_dir = os.path.join(self.dl, "Outer")
        child_dir = os.path.join(parent_dir, "Season 2")
        os.makedirs(child_dir, exist_ok=True)
        server = _FakeServer([
            _torrent(parent_dir, "HASH_OUTER", "Outer"),
            _torrent(child_dir, "HASH_CHILD", "Outer/Season 2"),
        ])
        ModuleManager.register_downloader(DownloaderType.Qbittorrent, "qb", server)

        leftover = os.path.join(child_dir, "e01.nfo")
        self.assertEqual(
            self.plugin._resolve_hash_by_path(leftover), "HASH_CHILD",
            "应命中嵌套最深的那个种子目录",
        )

    def test_deep_subdir_resolved_by_prefix(self):
        """
        种子的残留文件位于其**子目录**时，必须按前缀（而非相等）归属。

        实测场景：``种子名/Season 2/e01.nfo`` 与种子目录 ``种子名`` 并不相等，
        只有前缀匹配才能救回；相等匹配会让这类残留永久失去删种机会。
        """
        seed_dir = os.path.join(self.dl, "Show.S01")
        deep_dir = os.path.join(seed_dir, "Season 1", "Extras")
        os.makedirs(deep_dir, exist_ok=True)
        server = _FakeServer([_torrent(seed_dir, "HASH_DEEP", "Show.S01")])
        ModuleManager.register_downloader(DownloaderType.Qbittorrent, "qb", server)

        leftover = os.path.join(deep_dir, "behind.nfo")
        self.assertEqual(
            self.plugin._resolve_hash_by_path(leftover), "HASH_DEEP",
            "子目录（非同级）下的残留也必须能归属到种子",
        )

    def test_cache_avoids_repeat_collect(self):
        """同一目录二次反查应命中缓存，不再重复收集候选。"""
        seed_dir = self._register_seed("S", "HASH_SEED")
        leftover = os.path.join(seed_dir, "a.nfo")
        self.plugin._resolve_hash_by_path(leftover)
        with mock.patch.object(
            self.plugin, "_collect_seed_candidates",
            side_effect=AssertionError("不应重复收集"),
        ):
            self.assertEqual(
                self.plugin._resolve_hash_by_path(leftover), "HASH_SEED"
            )


# ==========================================================================
# 二、反查失败必须留痕（不再静默跳过）
# ==========================================================================
class TestResolveFailureLogged(_Base):
    """反查彻底失败时需要 WARN 日志，暴露「删了文件但没判种」的情况。"""

    def test_unresolvable_path_logs_warning(self):
        """无法归属的文件应产生 WARN，而非静默丢弃。"""
        orphan = self.make("Unknown/x.nfo")
        deleted_paths = [orphan]
        detail_lines = []

        with mock.patch("seedspaceguard.logger") as log:
            self.plugin._run_linkage_after_delete(
                deleted_paths, detail_lines, dry_run=False
            )
            warned = [
                call for call in log.warning.call_args_list
                if "无法按路径归属种子" in str(call)
            ]
        self.assertTrue(warned, "反查失败必须打 WARN 日志")

    def test_leftover_uses_dir_fallback_in_linkage(self):
        """联动入口处的刮削残留应被目录兜底救回，正常进入删种判定。"""
        seed_dir = os.path.join(self.dl, "Shadow.2018")
        os.makedirs(seed_dir, exist_ok=True)
        server = _FakeServer([_torrent(seed_dir, "HASH_SHADOW", "Shadow.2018")])
        ModuleManager.register_downloader(DownloaderType.Qbittorrent, "qb", server)

        # 种子只剩一个 nfo，且该 nfo 刚被删除 → 文件已全空 → 应删种
        leftover = os.path.join(seed_dir, "Shadow.2018.nfo")
        with open(leftover, "wb") as handle:
            handle.write(b"x")
        DownloadHistoryOper.add_seed("HASH_SHADOW", [leftover])
        os.remove(leftover)

        detail_lines = []
        stats = self.plugin._run_linkage_after_delete(
            [leftover], detail_lines, dry_run=False
        )
        self.assertEqual(stats["torrent"], 1, "刮削残留删完后应联动删种")
        self.assertEqual(server.removed[0][0], ["HASH_SHADOW"])


# ==========================================================================
# 三、空壳种子回收（BUG-2）
# ==========================================================================
class TestReapOrphanSeeds(_Base):
    """文件已全部删除但种子仍在的做种任务应被回收。"""

    def _seed_with_files(self, name, hash_str, files, done_days_ago=30,
                         create=True):
        """在下载器上注册一个种子，并（可选）在磁盘创建其文件。"""
        seed_dir = os.path.join(self.dl, name)
        os.makedirs(seed_dir, exist_ok=True)
        paths = []
        for fn in files:
            path = os.path.join(seed_dir, fn)
            if create:
                with open(path, "wb") as handle:
                    handle.write(b"x")
            paths.append(path)
        if create:
            DownloadHistoryOper.add_seed(hash_str, paths)
        else:
            # 记录存在但文件已不在：模拟「已删空」
            DownloadHistoryOper.add_seed(hash_str, paths)
            for path in paths:
                if os.path.exists(path):
                    os.remove(path)
        server = _FakeServer([_torrent(seed_dir, hash_str, name, done_days_ago)])
        ModuleManager.register_downloader(DownloaderType.Qbittorrent, "qb", server)
        return server, seed_dir

    def test_orphan_seed_reaped(self):
        """文件全空的种子应被回收。"""
        server, _ = self._seed_with_files(
            "Shadow.2018", "HASH_ORPHAN", ["a.mkv", "b.nfo"], create=False
        )
        stats = self.plugin._reap_orphan_seeds(dry_run=False)
        self.assertEqual(stats["torrent"], 1)
        self.assertEqual(server.removed[0][0], ["HASH_ORPHAN"])
        self.assertFalse(server.removed[0][1], "回收种子不得连带删文件")

    def test_seed_with_files_kept(self):
        """仍有文件存在的种子不得被回收。"""
        server, _ = self._seed_with_files(
            "Alive", "HASH_ALIVE", ["a.mkv"], create=True
        )
        stats = self.plugin._reap_orphan_seeds(dry_run=False)
        self.assertEqual(stats["torrent"], 0)
        self.assertEqual(server.removed, [])

    def test_half_deleted_seed_kept(self):
        """半删种子（部分文件仍在）不得被回收。"""
        seed_dir = os.path.join(self.dl, "Half")
        os.makedirs(seed_dir, exist_ok=True)
        alive = os.path.join(seed_dir, "kept.mkv")
        gone = os.path.join(seed_dir, "gone.mkv")
        with open(alive, "wb") as handle:
            handle.write(b"x")
        DownloadHistoryOper.add_seed("HASH_HALF", [alive, gone])
        server = _FakeServer([_torrent(seed_dir, "HASH_HALF", "Half")])
        ModuleManager.register_downloader(DownloaderType.Qbittorrent, "qb", server)

        stats = self.plugin._reap_orphan_seeds(dry_run=False)
        self.assertEqual(stats["torrent"], 0, "半删种子必须保留，等待后续轮次删完")
        self.assertEqual(server.removed, [])

    def test_dry_run_does_not_remove(self):
        """试运行照常扫描并预告将回收的种子，但绝不真删。"""
        server, _ = self._seed_with_files(
            "Shadow.Dry", "HASH_DRY", ["a.mkv"], create=False
        )
        stats = self.plugin._reap_orphan_seeds(dry_run=True)
        # 关键：试运行必须如实预告「将会回收 1 个」，而非跳过扫描报 0
        self.assertEqual(stats["torrent"], 1, "试运行应如实预告将回收的种子数")
        self.assertEqual(stats["checked"], 1, "试运行必须真的扫描过候选")
        self.assertEqual(server.removed, [], "试运行绝不允许真删种子")
        self.assertTrue(
            any("[试运行]" in line for line in stats["_lines"]),
            "试运行明细行需带 [试运行] 前缀以便区分",
        )

    def test_dry_run_predicts_same_as_real(self):
        """试运行预告的数量必须与正式跑一致（试运行的价值所在）。"""
        server, _ = self._seed_with_files(
            "Shadow.Same", "HASH_SAME", ["a.mkv"], create=False
        )
        predict = self.plugin._reap_orphan_seeds(dry_run=True)
        self.assertEqual(server.removed, [], "试运行不得真删")
        real = self.plugin._reap_orphan_seeds(dry_run=False)
        self.assertEqual(
            predict["torrent"], real["torrent"],
            "试运行的预告数必须等于正式跑的实删数",
        )
        self.assertEqual(server.removed[0][0], ["HASH_SAME"], "正式跑应真删该种子")

    def test_dry_run_keeps_alive_zero(self):
        """试运行同样不得把「仍有文件」的种子算进预告。"""
        server, _ = self._seed_with_files(
            "Alive.Dry", "HASH_ALIVE_DRY", ["a.mkv"], create=True
        )
        stats = self.plugin._reap_orphan_seeds(dry_run=True)
        self.assertEqual(stats["torrent"], 0)
        self.assertEqual(stats["alive"], 1)
        self.assertEqual(server.removed, [])

    def test_switch_off_noop(self):
        """未开启删种联动时整体不执行。"""
        server, _ = self._seed_with_files(
            "Shadow.Off", "HASH_OFF", ["a.mkv"], create=False
        )
        self.plugin._delete_torrents = False
        stats = self.plugin._reap_orphan_seeds(dry_run=False)
        self.assertEqual(stats["torrent"], 0)
        self.assertEqual(server.removed, [])

    def test_recent_orphan_also_reaped(self):
        """保护期内的空壳种子同样回收：文件都没了，保种已无意义。"""
        server, _ = self._seed_with_files(
            "Fresh.Orphan", "HASH_FRESH", ["a.mkv"],
            done_days_ago=1, create=False,
        )
        self.plugin._recent_skip_days = 30   # 远大于 1 天，本应处于保护期
        stats = self.plugin._reap_orphan_seeds(dry_run=False)
        self.assertEqual(stats["torrent"], 1, "空壳回收不受保护期限制")

    def test_no_downloader_returns_empty(self):
        """无可用下载器时安全返回空统计。"""
        stats = self.plugin._reap_orphan_seeds(dry_run=False)
        self.assertEqual(stats["torrent"], 0)
        self.assertEqual(stats["checked"], 0)

    def test_query_exception_degrades(self):
        """候选收集抛异常时降级，不向调用方抛错。"""
        with mock.patch.object(
            self.plugin, "_collect_seed_candidates",
            side_effect=RuntimeError("boom"),
        ):
            stats = self.plugin._reap_orphan_seeds(dry_run=False)
        self.assertEqual(stats["torrent"], 0)

    def test_remove_exception_degrades(self):
        """删种抛异常时降级，不影响其它种子处理。"""
        server, _ = self._seed_with_files(
            "Boom", "HASH_BOOM", ["a.mkv"], create=False
        )
        server.remove_torrents = mock.Mock(side_effect=RuntimeError("boom"))
        stats = self.plugin._reap_orphan_seeds(dry_run=False)
        self.assertEqual(stats["torrent"], 0)
        self.assertEqual(stats["checked"], 1)

    def test_orphan_outside_target_dirs_ignored(self):
        """不在配置目录内的种子不参与回收。"""
        other = tempfile.mkdtemp(prefix="ssg-other-")
        try:
            seed_dir = os.path.join(other, "Outside")
            os.makedirs(seed_dir, exist_ok=True)
            outside = os.path.join(seed_dir, "a.mkv")
            DownloadHistoryOper.add_seed("HASH_OUT", [outside])
            server = _FakeServer([_torrent(seed_dir, "HASH_OUT", "Outside")])
            ModuleManager.register_downloader(
                DownloaderType.Qbittorrent, "qb", server
            )
            stats = self.plugin._reap_orphan_seeds(dry_run=False)
            self.assertEqual(stats["torrent"], 0)
            self.assertEqual(server.removed, [])
        finally:
            shutil.rmtree(other, ignore_errors=True)

    def test_multiple_orphans_all_reaped(self):
        """多个空壳种子应全部回收。"""
        srv_a, _ = self._seed_with_files(
            "OrphanA", "HASH_A", ["a.mkv"], create=False
        )
        srv_b, _ = self._seed_with_files(
            "OrphanB", "HASH_B", ["b.mkv"], create=False
        )
        # 后注册的覆盖前者，重新注册两份以便都可见
        class _Both:
            def get_torrents(self, *a, **kw):
                return srv_a.get_torrents() + srv_b.get_torrents()

            def __init__(self):
                self.removed = []

            def remove_torrents(self, hashs, delete_file=False, downloader=None):
                self.removed.append(list(hashs))
                return True

        both = _Both()
        ModuleManager.reset()
        ModuleManager.register_downloader(DownloaderType.Qbittorrent, "qb", both)

        stats = self.plugin._reap_orphan_seeds(dry_run=False)
        self.assertEqual(stats["torrent"], 2)
        self.assertEqual(sorted(both.removed[0] + both.removed[1]),
                         ["HASH_A", "HASH_B"])


# ==========================================================================
# 四、空壳回收必须早于「空间充足」早退
# ==========================================================================
class TestReapBeforeEarlyReturn(_Base):
    """空间充足时也要回收空壳，否则永远轮不到删种。"""

    def _setup_orphan(self):
        seed_dir = os.path.join(self.dl, "Orphan")
        os.makedirs(seed_dir, exist_ok=True)
        path = os.path.join(seed_dir, "a.mkv")
        DownloadHistoryOper.add_seed("HASH_ORPHAN", [path])   # 记录在、文件不存在
        server = _FakeServer([_torrent(seed_dir, "HASH_ORPHAN", "Orphan")])
        ModuleManager.register_downloader(DownloaderType.Qbittorrent, "qb", server)
        return server

    def _run(self):
        """以「手动触发 + 空间充足」运行一次主流程。"""
        self.plugin._enabled = True
        self.plugin._notify = False
        self.plugin._threshold_gb = 1.0     # 阈值极低，磁盘必然「充足」
        return self.plugin.check_and_clean(source="手动")

    def test_reap_runs_when_space_plenty(self):
        """空间充足早退前，空壳种子仍被回收。"""
        server = self._setup_orphan()
        with mock.patch.object(
            self.plugin, "_disk_free_bytes", return_value=10 * 1024 ** 3
        ):
            self._run()

        self.assertEqual(
            [h for h, _, _ in server.removed], [["HASH_ORPHAN"]],
            "空间充足时也必须回收空壳种子",
        )

    def test_reap_result_in_message(self):
        """回收数量应出现在结果消息里，便于用户确认。"""
        self._setup_orphan()
        self.plugin._threshold_gb = 1.0
        with mock.patch.object(
            self.plugin, "_disk_free_bytes", return_value=10 * 1024 ** 3
        ):
            self._run()
        self.assertIn("空壳种子 1 个", self.plugin._last_result)

    def test_no_orphan_no_noise(self):
        """没有空壳时不产生额外文案，保持原有「空间充足」提示。"""
        seed_dir = os.path.join(self.dl, "Alive")
        os.makedirs(seed_dir, exist_ok=True)
        alive = os.path.join(seed_dir, "a.mkv")
        with open(alive, "wb") as handle:
            handle.write(b"x")
        DownloadHistoryOper.add_seed("HASH_ALIVE", [alive])
        server = _FakeServer([_torrent(seed_dir, "HASH_ALIVE", "Alive")])
        ModuleManager.register_downloader(DownloaderType.Qbittorrent, "qb", server)

        self.plugin._threshold_gb = 1.0
        with mock.patch.object(
            self.plugin, "_disk_free_bytes", return_value=10 * 1024 ** 3
        ):
            self._run()

        self.assertIn("空间充足", self.plugin._last_result)
        self.assertNotIn("空壳种子", self.plugin._last_result)
        self.assertEqual(server.removed, [])

    def test_orphan_reaped_in_shortage_too(self):
        """空间不足时同样回收空壳，并计入本轮种子数。"""
        server = self._setup_orphan()
        self.plugin._enabled = True
        self.plugin._notify = False
        self.plugin._threshold_gb = 10 ** 6      # 阈值极高，必然「不足」
        self.plugin._sync_wait_seconds = 0
        with mock.patch.object(
            self.plugin, "_disk_free_bytes", return_value=1 * 1024 ** 3
        ):
            self.plugin.check_and_clean(source="手动")

        self.assertEqual(
            [h for h, _, _ in server.removed], [["HASH_ORPHAN"]],
            "空间不足时也要回收空壳种子",
        )


# ==========================================================================
# 五、试运行必须如实预告（v1.3.6 修复：试运行不再跳过空壳扫描）
# ==========================================================================
class TestDryRunPreviewsOrphans(_Base):
    """
    试运行若整段跳过空壳扫描，用户看到的「回收 0 个」便是假象。

    这里从主流程（check_and_clean）层面验证：试运行时既不能真删，又必须
    如实预告将回收的种子，且结果消息要能被一眼认出是试运行。
    """

    def _setup_orphan(self, name="DryRun", hash_str="HASH_DRYRUN"):
        seed_dir = os.path.join(self.dl, name)
        os.makedirs(seed_dir, exist_ok=True)
        path = os.path.join(seed_dir, "a.mkv")
        DownloadHistoryOper.add_seed(hash_str, [path])   # 记录在、文件不存在
        server = _FakeServer([_torrent(seed_dir, hash_str, name)])
        ModuleManager.register_downloader(DownloaderType.Qbittorrent, "qb", server)
        return server

    def _run(self, dry_run):
        self.plugin._enabled = True
        self.plugin._notify = False
        self.plugin._threshold_gb = 1.0        # 阈值极低，磁盘必然「充足」
        with mock.patch.object(
            self.plugin, "_disk_free_bytes", return_value=10 * 1024 ** 3
        ):
            return self.plugin.check_and_clean(
                source="手动", dry_run_override=dry_run
            )

    def test_dry_run_previews_orphan(self):
        """试运行必须预报将回收的空壳种子，而不是报告 0 个。"""
        server = self._setup_orphan()
        msg = self._run(dry_run=True)
        self.assertEqual(server.removed, [], "试运行绝不能真删种子")
        self.assertIn("预计回收空壳种子 1 个", msg,
                      "试运行必须如实预告将回收的空壳种子数")

    def test_dry_run_message_has_prefix(self):
        """试运行的结果消息需带 [试运行] 前缀，与正式清理可辨。"""
        self._setup_orphan()
        msg = self._run(dry_run=True)
        self.assertTrue(msg.startswith("[试运行]"),
                        f"试运行消息应以 [试运行] 开头，实际：{msg}")

    def test_real_run_message_has_prefix(self):
        """正式清理的结果消息需带 [清理] 前缀。"""
        self._setup_orphan()
        msg = self._run(dry_run=False)
        self.assertTrue(msg.startswith("[清理]"),
                        f"正式清理消息应以 [清理] 开头，实际：{msg}")

    def test_dry_run_count_matches_real(self):
        """试运行预告值与正式跑实删值必须一致。"""
        server = self._setup_orphan()
        self._run(dry_run=True)
        self.assertEqual(server.removed, [], "试运行过后不得有删种痕迹")

        self._run(dry_run=False)
        self.assertEqual(
            [h for h, _, _ in server.removed], [["HASH_DRYRUN"]],
            "正式跑应真删该种子，与试运行预告一致",
        )

    def test_dry_run_no_false_alarm_when_alive(self):
        """试运行时「仍有文件」的种子不得计入预告数。"""
        seed_dir = os.path.join(self.dl, "StillAlive")
        os.makedirs(seed_dir, exist_ok=True)
        alive = os.path.join(seed_dir, "a.mkv")
        with open(alive, "wb") as handle:
            handle.write(b"x")
        DownloadHistoryOper.add_seed("HASH_STILL", [alive])
        server = _FakeServer([_torrent(seed_dir, "HASH_STILL", "StillAlive")])
        ModuleManager.register_downloader(DownloaderType.Qbittorrent, "qb", server)

        msg = self._run(dry_run=True)
        self.assertNotIn("空壳种子", msg, "有文件在的种子不应被预报回收")
        self.assertEqual(server.removed, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
