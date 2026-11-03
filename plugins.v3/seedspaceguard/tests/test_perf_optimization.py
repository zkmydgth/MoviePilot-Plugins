# -*- coding: utf-8 -*-
"""
空壳扫描性能治理测试（v3.2.0）。

背景：实测单轮空壳扫描在 1652 个候选上耗时 50 分钟，80% 以上时间消耗在
「逐种子串行远程查询文件清单」与「同轮重复枚举种子列表」上。本测试锁定
以下新增行为，防止性能改造被后续修改无声回退：

  A. 批量预取：transmission 一次请求取回整批文件清单；qBittorrent 走有界并发，
     预取命中后单种子判定不再发起远程调用。
  B. 熔断：远程调用连续慢失败后停止逐种子判定，剩余候选保守保留。
  C. 预算：单轮逐种子判定有时间上限，超时即停止，不计为「已判定」。
  D. 空间充足降频：未到间隔时跳过深度空壳扫描。
  E. 增量缓存：已确认仍有文件的种子在有效期内不复测；试运行不读写缓存。
  F. 轮内快照：删种定位直接复用本轮枚举结果，不再逐个下载器探测。
  G. 删种复核：按下载器汇总后一次查询，替代逐 hash 复核。
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
from seedspaceguard import SeedSpaceGuard


class _FileEntry:
    """transmission_rpc.File 的最小替身：只有 name 属性。"""

    def __init__(self, name):
        self.name = name


class _TorrentItem:
    """transmission Torrent 的最小替身：hashString + files。"""

    def __init__(self, hash_str, names):
        self.hashString = hash_str
        self.files = [_FileEntry(name) for name in names]


class _TRClient:
    """transmission 客户端桩：记录批量请求参数并返回整批文件清单。"""

    def __init__(self, files_map):
        self.files_map = files_map
        self.calls = []

    def get_torrents(self, ids=None, arguments=None, timeout=None):
        self.calls.append({
            "ids": list(ids or []),
            "arguments": list(arguments or []),
            "timeout": timeout,
        })
        result = []
        for hash_str in ids or []:
            if hash_str in self.files_map:
                result.append(_TorrentItem(hash_str, self.files_map[hash_str]))
        return result


class _Server:
    """下载器实例桩：枚举种子、按需/预取文件清单、记录删种。"""

    def __init__(self, torrents, files_map=None, trc=None,
                 files_return_none=False):
        self.torrents = torrents
        self.files_map = files_map or {}
        self.trc = trc
        self.files_return_none = files_return_none
        self.torrent_files_calls = []
        self.removed = []
        self.get_torrents_calls = 0

    def get_torrents(self, *args, **kwargs):
        self.get_torrents_calls += 1
        return self.torrents

    def torrent_files(self, tid=None, downloader=None, **kwargs):
        self.torrent_files_calls.append(tid)
        if self.files_return_none:
            return None
        return [{"name": name} for name in self.files_map.get(tid, [])]

    def remove_torrents(self, hashs=None, delete_file=False,
                        downloader=None, **kwargs):
        normalized = [hashs] if isinstance(hashs, str) else list(hashs or [])
        self.removed.append((normalized, delete_file, downloader))
        return True


def _register(server, tr=False):
    """注册一个下载器桩（qBittorrent 或 Transmission）。"""
    ModuleManager.reset()
    ModuleManager.register_downloader(
        DownloaderType.Transmission if tr else DownloaderType.Qbittorrent,
        "tr" if tr else "qb",
        server,
    )


def _qb_item(content_path, hash_str, name):
    """构造 qBittorrent 原始条目（camelCase）。"""
    return {
        "content_path": content_path,
        "save_path": os.path.dirname(content_path),
        "hash": hash_str,
        "name": name,
        "progress": 1.0,
        "completion_on": int(time.time()) - 40 * 86400,
        "size": 4 * 1024 ** 3,
        "added_on": int(time.time()) - 40 * 86400,
    }


def _tr_item(download_dir, hash_str, name):
    """构造 Transmission 原始条目（snake_case）。"""
    return {
        "download_dir": download_dir,
        "name": name,
        "hash_string": hash_str,
        "percent_done": 1.0,
        "total_size": 4 * 1024 ** 3,
        "done_date": int(time.time()) - 40 * 86400,
        "added_date": int(time.time()) - 40 * 86400,
    }


class _Base(unittest.TestCase):
    """夹具：一个监控目录与可配置下载器。"""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="ssg-perf-")
        self.dl = os.path.join(self.base, "download")
        os.makedirs(self.dl)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)
        ModuleManager.reset()

    def make_plugin(self, **overrides):
        """构造插件实例并写入测试所需的最小运行态。"""
        plugin = SeedSpaceGuard()
        plugin._target_dirs = [self.dl]
        plugin._active_dirs = [self.dl]
        plugin._volume_path = self.base
        plugin._protect_pattern = ""
        plugin._recent_skip_days = 0
        plugin._threshold_gb = 1.0
        plugin._companion_cleanup = False
        plugin._orphan_cleanup = False
        plugin._orphan_seed_scope = False
        plugin._delete_torrents = True
        plugin._delete_history = False
        plugin._downloadhis = None
        plugin._transferhis = None
        plugin._ino_paths = {}
        plugin._clean_stats = {
            "files": 0, "transfers": 0, "seeds": 0,
            "companions": 0, "orphans": 0, "stalled": False,
        }
        for key, value in overrides.items():
            setattr(plugin, key, value)
        return plugin

    def make_empty_dir(self, name):
        """创建一个空的种子目录（触发空壳判定的廉价检查）。"""
        path = os.path.join(self.dl, name)
        os.makedirs(path, exist_ok=True)
        return path


class TestPrefetch(_Base):
    """A. 批量预取文件清单。"""

    def test_transmission_batch_single_request(self):
        """25 个候选只发一次 transmission 请求，且不再逐种子取清单。"""
        hashes = [f"H{i}" for i in range(25)]
        torrents = []
        files_map = {}
        for index, hash_str in enumerate(hashes):
            content = self.make_empty_dir(f"Show.{index}")
            torrents.append(_tr_item(self.dl, hash_str, f"Show.{index}"))
            files_map[hash_str] = [f"Show.{index}/e01.mkv"]
        client = _TRClient(files_map)
        server = _Server(torrents, files_map=files_map, trc=client)
        _register(server, tr=True)
        plugin = self.make_plugin()

        stats = plugin._reap_orphan_seeds(dry_run=False)

        self.assertEqual(len(client.calls), 1, "整批种子只应发一次请求")
        self.assertEqual(client.calls[0]["arguments"], ["hashString", "files"])
        self.assertEqual(len(client.calls[0]["ids"]), 25)
        self.assertEqual(server.torrent_files_calls, [], "预取命中后不得逐种子再查")
        self.assertEqual(stats["torrent"], 25)

    def test_qbittorrent_concurrent_prefetch(self):
        """无批量接口时并发预取，每个种子只请求一次。"""
        torrents = []
        files_map = {}
        for index in range(25):
            name = f"Show.{index}"
            content = self.make_empty_dir(name)
            hash_str = f"H{index}"
            torrents.append(_qb_item(content, hash_str, name))
            files_map[hash_str] = [f"{name}/e01.mkv"]
        server = _Server(torrents, files_map=files_map)
        _register(server)
        plugin = self.make_plugin()

        stats = plugin._reap_orphan_seeds(dry_run=False)

        self.assertEqual(len(server.torrent_files_calls), 25)
        self.assertEqual(stats["torrent"], 25)


class TestCircuitBreaker(_Base):
    """B. 远程调用熔断。"""

    def test_breaker_opens_after_threshold(self):
        """连续失败达到阈值后打开熔断。"""
        plugin = self.make_plugin()
        plugin._rpc_failures = 1

        plugin._note_rpc_failure()

        self.assertTrue(plugin._rpc_open)

    def test_open_breaker_skips_remote_call(self):
        """熔断打开后不再发起远程查询。"""
        content = self.make_empty_dir("Show")
        server = _Server([_qb_item(content, "H1", "Show")])
        _register(server)
        plugin = self.make_plugin()
        plugin._rpc_open = True

        files = plugin._get_seed_files(
            "H1", {"module": server, "hash": "H1", "dl_dir": self.dl}
        )

        self.assertEqual(files, [])
        self.assertEqual(server.torrent_files_calls, [])

    def test_breaker_breaks_reap_loop(self):
        """熔断打开后本轮停止判定，剩余候选计入跳过。"""
        torrents = [
            _qb_item(self.make_empty_dir(f"S{i}"), f"H{i}", f"S{i}")
            for i in range(3)
        ]
        server = _Server(torrents)
        _register(server)
        plugin = self.make_plugin()
        plugin._rpc_open = True

        stats = plugin._reap_orphan_seeds(dry_run=False)

        self.assertEqual(stats["torrent"], 0)
        self.assertEqual(stats["skipped"], 3)


class TestBudget(_Base):
    """C. 单轮判定预算。"""

    def test_expired_deadline_skips_remaining(self):
        """预算到期后不再判定任何候选。"""
        torrents = [
            _qb_item(self.make_empty_dir(f"S{i}"), f"H{i}", f"S{i}")
            for i in range(2)
        ]
        _register(_Server(torrents))
        plugin = self.make_plugin()

        # 预算为负数：本轮一开始就已过期，所有候选都应被跳过
        with mock.patch(
            "seedspaceguard.ORPHAN_SCAN_BUDGET_SECONDS", -1
        ):
            stats = plugin._reap_orphan_seeds(dry_run=False)

        self.assertEqual(stats["checked"], 0)
        self.assertEqual(stats["skipped"], 2)


class TestQuietSkip(_Base):
    """D. 空间充足时降频。"""

    def test_recent_scan_skipped_when_space_enough(self):
        """距上次扫描不足间隔且空间充足时不做深度扫描。"""
        server = _Server([_qb_item(self.make_empty_dir("S"), "H", "S")])
        _register(server)
        plugin = self.make_plugin()
        plugin.save_data("orphan_scan_at", time.time())

        stats = plugin._reap_orphan_seeds(dry_run=False, space_tight=False)

        self.assertEqual(stats["checked"], 0)
        self.assertEqual(server.get_torrents_calls, 0, "不应枚举种子")

    def test_scan_runs_when_space_tight(self):
        """空间不足时即使刚扫过也必须扫描。"""
        server = _Server([_qb_item(self.make_empty_dir("S"), "H", "S")])
        _register(server)
        plugin = self.make_plugin()
        plugin.save_data("orphan_scan_at", time.time())

        stats = plugin._reap_orphan_seeds(dry_run=False, space_tight=True)

        self.assertEqual(stats["checked"], 1)
        self.assertEqual(server.get_torrents_calls, 1)

    def test_protected_period_does_not_block_shell(self):
        """空壳回收不受保护期限制：文件已删光，继续保种没有意义。"""
        item = _qb_item(self.make_empty_dir("New"), "H", "New")
        item["added_on"] = int(time.time())
        item["completion_on"] = int(time.time())
        _register(_Server([item]))
        plugin = self.make_plugin()
        plugin._recent_skip_days = 5

        stats = plugin._reap_orphan_seeds(dry_run=False)

        self.assertEqual(stats["torrent"], 1)

    def test_dry_run_ignores_quiet_gate(self):
        """试运行不受降频约束：用户点按钮必须看到本轮会回收什么。"""
        server = _Server([_qb_item(self.make_empty_dir("S"), "H", "S")])
        _register(server)
        plugin = self.make_plugin()
        plugin.save_data("orphan_scan_at", time.time())

        stats = plugin._reap_orphan_seeds(dry_run=True, space_tight=False)

        self.assertEqual(stats["checked"], 1, "试运行必须照常扫描")
        self.assertEqual(stats["torrent"], 1)
        self.assertEqual(server.removed, [], "试运行不得真删")


class TestAliveCache(_Base):
    """E. 增量缓存。"""

    @staticmethod
    def _reset_scan_mark(plugin):
        """清掉降频时间戳，确保本轮真的执行深度扫描（否则会被跳过）。"""
        plugin.save_data("orphan_scan_at", 0)

    def _alive_setup(self):
        """构造一个「目录存在且自身文件仍在」的种子（判定为 alive）。"""
        name = "Alive"
        content = os.path.join(self.dl, name)
        os.makedirs(content, exist_ok=True)
        alive_file = os.path.join(content, "e01.mkv")
        with open(alive_file, "wb") as handle:
            handle.write(b"x")
        server = _Server(
            [_qb_item(content, "H1", name)],
            files_map={"H1": [f"{name}/e01.mkv"]},
        )
        _register(server)
        return server

    def test_second_run_hits_cache(self):
        """空间充足时第二轮命中增量缓存，不重复远程查询。"""
        server = self._alive_setup()
        plugin = self.make_plugin()

        self._reset_scan_mark(plugin)
        first = plugin._reap_orphan_seeds(dry_run=False, space_tight=False)
        calls_after_first = len(server.torrent_files_calls)
        self._reset_scan_mark(plugin)
        second = plugin._reap_orphan_seeds(dry_run=False, space_tight=False)

        self.assertEqual(first["alive"], 1)
        self.assertEqual(second["cached"], 1)
        self.assertEqual(len(server.torrent_files_calls), calls_after_first)

    def test_dry_run_does_not_write_cache(self):
        """试运行不落缓存，保证预告与实跑同口径。"""
        self._alive_setup()
        plugin = self.make_plugin()
        # 预置一个内存态条目：试运行若刷新缓存，它就会被持久化
        plugin._orphan_alive_note({"hash": "H0", "path": "/x", "dl_dir": "/"})

        plugin._reap_orphan_seeds(dry_run=True)

        self.assertIsNone(plugin.get_data("orphan_alive"))

    def test_directory_change_invalidates_cache(self):
        """目录内容变化（mtime 前移）后旧结论失效，必须复测。"""
        self._alive_setup()
        plugin = self.make_plugin()
        content = os.path.join(self.dl, "Alive")

        self._reset_scan_mark(plugin)
        first = plugin._reap_orphan_seeds(dry_run=False, space_tight=False)
        self._reset_scan_mark(plugin)
        second = plugin._reap_orphan_seeds(dry_run=False, space_tight=False)
        self.assertEqual(first["alive"], 1)
        self.assertEqual(second["cached"], 1)

        # 目录 mtime 前移 = 内容变了 → 缓存必须失效
        future = time.time() + 120
        os.utime(content, (future, future))
        self._reset_scan_mark(plugin)
        third = plugin._reap_orphan_seeds(dry_run=False, space_tight=False)

        self.assertEqual(third["cached"], 0, "目录变化后不得命中缓存")
        self.assertEqual(third["alive"], 1)

    def test_space_tight_bypasses_cache(self):
        """空间不足时不得复用缓存：这正是必须立刻拿到真实结论的场景。"""
        self._alive_setup()
        plugin = self.make_plugin()
        plugin._reap_orphan_seeds(dry_run=False, space_tight=False)

        tight = plugin._reap_orphan_seeds(dry_run=False, space_tight=True)

        self.assertEqual(tight["cached"], 0, "空间紧张时不得命中缓存")
        self.assertEqual(tight["alive"], 1)

    def test_missing_directory_invalidates_cache(self):
        """目录消失后旧结论失效：这是空壳最典型的形态。"""
        self._alive_setup()
        plugin = self.make_plugin()
        self._reset_scan_mark(plugin)
        plugin._reap_orphan_seeds(dry_run=False, space_tight=False)
        # 文件与目录一起被删掉（联动删种后的实际形态）
        shutil.rmtree(os.path.join(self.dl, "Alive"))

        self._reset_scan_mark(plugin)
        stats = plugin._reap_orphan_seeds(dry_run=False, space_tight=False)

        self.assertEqual(stats["cached"], 0, "目录消失后不得命中缓存")
        self.assertEqual(stats["torrent"], 1, "已删空的种子应被回收")


class TestSnapshot(_Base):
    """F. 轮内快照。"""

    def test_snapshot_avoids_downloader_probe(self):
        """已枚举过的 hash 直接由快照定位下载器。"""
        content = self.make_empty_dir("Show")
        server = _Server([_qb_item(content, "H1", "Show")])
        _register(server)
        plugin = self.make_plugin()

        plugin._collect_seed_candidates()
        calls_after_collect = server.get_torrents_calls
        module = plugin._get_downloader_for("H1")

        self.assertIsNotNone(module)
        self.assertEqual(plugin._resolved_downloader_name, "qb")
        self.assertEqual(
            server.get_torrents_calls, calls_after_collect,
            "快照命中时不应再向下载器探测",
        )


class TestBatchVerify(_Base):
    """G. 删种后批量复核。"""

    def test_single_batch_query(self):
        """同一轮多个 hash 只发一次复核查询。"""
        calls = []

        class _Module:
            def list_torrents(self, hashs=None, include_all_tags=False):
                calls.append(sorted(hashs or []))
                return []

        plugin = self.make_plugin()
        plugin._deleted_pending = {"qb": {"H1", "H2"}}
        plugin._downloader_modules = {"qb": _Module()}

        plugin._verify_deleted_hashes()

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], ["H1", "H2"])


class TestPrefetchBudget(_Base):
    """预取同样受预算约束。"""

    def test_expired_budget_blocks_prefetch(self):
        """预算已过期时预取不得再发起远程调用。"""
        torrents = []
        files_map = {}
        for index in range(25):
            name = f"S{index}"
            content = self.make_empty_dir(name)
            hash_str = f"H{index}"
            item = _qb_item(content, hash_str, name)
            item["downloader"] = "qb"
            torrents.append(item)
            files_map[hash_str] = [f"{name}/e01.mkv"]
        server = _Server(torrents, files_map=files_map)
        _register(server)
        plugin = self.make_plugin()
        # 预取按下载器模块取清单，直接注入一个转发到桩实例的模块
        class _ModuleStub:
            def torrent_files(self, tid=None, downloader=None):
                return server.torrent_files(tid=tid, downloader=downloader)

        plugin._downloader_modules = {"qb": _ModuleStub()}
        plugin._scan_deadline = time.monotonic() - 1
        plugin._prefetch_seed_files(torrents[:25])

        self.assertEqual(server.torrent_files_calls, [])


class TestBatchChunk(_Base):
    """H. TR 批量取清单：按上限分批，整批失败时对半拆一次。"""

    def _make_trc(self, fail_sizes=()):
        """构造记录每批 hash 数量的 trc 桩。"""

        class _Trc:
            def __init__(self):
                self.sizes = []
                self.fail_sizes = set(fail_sizes)

            def get_torrents(self, ids=None, arguments=None, timeout=None):
                ids = list(ids or [])
                self.sizes.append(len(ids))
                if len(ids) in self.fail_sizes:
                    raise RuntimeError("batch failed")
                return []

        return _Trc()

    def test_batches_respect_limit(self):
        """超过单批上限时拆成多次请求，而不是一次带上千个 hash。"""
        trc = self._make_trc()
        plugin = self.make_plugin()
        plugin._scan_deadline = time.monotonic() + 60
        items = [{"hash": f"H{i:04d}"} for i in range(950)]

        with mock.patch("seedspaceguard.ORPHAN_TR_BATCH_SIZE", 400):
            plugin._prefetch_seed_files_batch("tr", trc, items)

        self.assertEqual(trc.sizes, [400, 400, 150])

    def test_failed_batch_is_split_once(self):
        """整批失败时对半拆再试，避免优化整轮失效。"""
        trc = self._make_trc(fail_sizes={400})
        plugin = self.make_plugin()
        plugin._scan_deadline = time.monotonic() + 60
        items = [{"hash": f"H{i:04d}"} for i in range(400)]

        with mock.patch("seedspaceguard.ORPHAN_TR_BATCH_SIZE", 400):
            plugin._prefetch_seed_files_batch("tr", trc, items)

        self.assertEqual(trc.sizes, [400, 200, 200])

    def test_budget_stops_next_batch(self):
        """预算用尽后不再发起下一批请求。"""
        trc = self._make_trc()
        plugin = self.make_plugin()
        plugin._scan_deadline = time.monotonic() - 1
        items = [{"hash": f"H{i:04d}"} for i in range(900)]

        plugin._prefetch_seed_files_batch("tr", trc, items)

        self.assertEqual(trc.sizes, [])


class TestPrefetchBreaker(_Base):
    """I. 预取阶段的慢失败同样计入熔断。"""

    def test_prefetch_slow_failure_opens_breaker(self):
        """并发预取连续慢失败达到阈值后熔断，后续任务不再发请求。"""
        server = _Server([], files_return_none=True)
        plugin = self.make_plugin()
        plugin._downloader_modules = {"qb": server}
        plugin._seed_files_cache = {}
        plugin._rpc_failures = 0
        plugin._rpc_open = False
        plugin._scan_deadline = 0.0

        with mock.patch("seedspaceguard.ORPHAN_SLOW_CALL_SECONDS", 0.0), \
                mock.patch("seedspaceguard.ORPHAN_RPC_FAILURE_LIMIT", 1):
            plugin._prefetch_seed_files_concurrent(
                "qb", [{"hash": "H1"}, {"hash": "H2"}], parallel=False,
            )

        self.assertTrue(plugin._rpc_open, "预取慢失败应触发熔断")
        self.assertEqual(len(server.torrent_files_calls), 1, "熔断后应停止请求")

    def test_success_resets_consecutive_count(self):
        """一次成功即复位计数：熔断语义是「连续」失败。"""
        plugin = self.make_plugin()
        plugin._rpc_failures = 1
        plugin._clear_rpc_failure()
        self.assertEqual(plugin._rpc_failures, 0)


class TestBudgetConfig(_Base):
    """J. 预算可配置并夹在允许范围内。"""

    def test_default_and_instance_value(self):
        plugin = self.make_plugin()
        self.assertEqual(plugin._orphan_budget(), 300, "未配置时回退默认值")
        plugin._orphan_scan_budget = 900
        self.assertEqual(plugin._orphan_budget(), 900)

    def test_config_value_is_clamped(self):
        for raw, expected in ((5000, 3600), (1, 30), (600, 600)):
            plugin = SeedSpaceGuard()
            plugin.init_plugin({"enabled": True, "orphan_scan_budget": raw})
            self.assertEqual(plugin._orphan_budget(), expected, f"raw={raw}")

    def test_invalid_config_falls_back(self):
        plugin = SeedSpaceGuard()
        plugin.init_plugin({"enabled": True, "orphan_scan_budget": "abc"})
        self.assertEqual(plugin._orphan_budget(), 300)


class TestScanCursor(_Base):
    """K. 断点续扫：预算截断时记断点，完整跑完清断点。"""

    def test_cursor_rotates_candidates(self):
        plugin = self.make_plugin()
        plugin.save_data("orphan_scan_cursor", "H2")
        rotated = plugin._rotate_orphan_candidates(
            [{"hash": "H1"}, {"hash": "H2"}, {"hash": "H3"}]
        )
        self.assertEqual([c["hash"] for c in rotated], ["H2", "H3", "H1"])

    def test_cursor_cleared_when_full_scan_finishes(self):
        plugin = self.make_plugin()
        plugin.save_data("orphan_scan_cursor", "H1")
        plugin._orphan_cursor_clear()
        self.assertEqual(plugin.get_data("orphan_scan_cursor"), "")

    def test_budget_exhaustion_records_cursor(self):
        """预算耗尽时把未处理的那个种子记为下轮起点。"""
        for index in range(3):
            self.make_empty_dir(f"S{index}")
        server = _Server([
            _qb_item(os.path.join(self.dl, f"S{i}"), f"H{i}", f"S{i}")
            for i in range(3)
        ])
        _register(server)
        plugin = self.make_plugin()
        plugin._orphan_scan_budget = -1

        stats = plugin._reap_orphan_seeds(dry_run=False)

        self.assertEqual(stats["checked"], 0)
        self.assertEqual(plugin.get_data("orphan_scan_cursor"), "H0")

    def test_elapsed_reported(self):
        """统计里带耗时：试运行可能是分钟级，必须可观测。"""
        self.make_empty_dir("S")
        _register(_Server([_qb_item(os.path.join(self.dl, "S"), "H", "S")]))
        plugin = self.make_plugin()

        stats = plugin._reap_orphan_seeds(dry_run=True)

        self.assertIn("elapsed", stats)
        self.assertGreaterEqual(stats["elapsed"], 0.0)
        # 必须是本轮真实耗时：起点被写错（例如置 0）会得到天文数字
        self.assertLess(stats["elapsed"], 60.0, "耗时应为本轮真实耗时")


class TestCacheDrop(_Base):
    """L. 回收后清缓存条目：未载入时也要真正删掉。"""

    def test_drop_without_prior_load(self):
        plugin = self.make_plugin()
        plugin.save_data(
            "orphan_alive",
            {"H1": {"ts": time.time(), "sig": "/x|/y"}},
        )
        self.assertNotIn("_orphan_alive_state", plugin.__dict__)

        plugin._orphan_alive_drop("H1")
        plugin._orphan_alive_flush()

        self.assertEqual(plugin.get_data("orphan_alive"), {})


class TestVerifyInFinally(_Base):
    """M. 删种复核放进收尾：删除链路抛异常也要执行。"""

    def test_verify_runs_when_clean_raises(self):
        plugin = self.make_plugin()
        plugin._enabled = True
        plugin._mode = "seed"
        plugin._dry_run = False
        plugin._notify = False
        plugin._volume_path = self.base
        plugin._disk_free_gb = lambda: 0.0
        plugin._threshold_gb = 9999.0
        calls = []
        plugin._verify_deleted_hashes = lambda: calls.append(1)

        def _boom(*args, **kwargs):
            raise RuntimeError("boom")

        plugin._clean_by_seed = _boom
        plugin._clean_orphan_files = lambda *a, **k: (0, 0.0, [])

        with self.assertRaises(RuntimeError):
            plugin.check_and_clean()

        self.assertEqual(calls, [1], "异常路径也必须执行删种复核")


class TestPrefetchWorkerType(_Base):
    """N. 并发只在短连接下载器上启用。"""

    def _workers_used(self, dl_type, parallel):
        plugin = self.make_plugin()
        plugin._downloader_modules = {"x": _Server([])}
        plugin._downloader_types = {"x": dl_type}
        seen = []
        real = __import__(
            "concurrent.futures", fromlist=["ThreadPoolExecutor"]
        ).ThreadPoolExecutor

        def _fake(*args, **kwargs):
            seen.append(kwargs.get("max_workers"))
            return real(*args, **kwargs)

        with mock.patch("seedspaceguard.ThreadPoolExecutor", _fake):
            plugin._prefetch_seed_files_concurrent(
                "x", [{"hash": "H1"}], parallel,
            )
        return seen

    def test_qbittorrent_uses_parallel_workers(self):
        self.assertEqual(
            self._workers_used(DownloaderType.Qbittorrent, True), [8]
        )

    def test_transmission_uses_single_worker(self):
        self.assertEqual(
            self._workers_used(DownloaderType.Transmission, False), [1]
        )

    def test_prefetch_end_to_end_honours_type(self):
        """整条预取链路按下载器类型决定并发度（TR 无 trc 时须串行）。"""
        plugin = self.make_plugin()
        plugin._downloader_instances = {"tr": _Server([])}
        plugin._downloader_modules = {"tr": _Server([])}
        plugin._downloader_types = {"tr": DownloaderType.Transmission}
        plugin._seed_files_cache = {}
        seen = []
        real = __import__(
            "concurrent.futures", fromlist=["ThreadPoolExecutor"]
        ).ThreadPoolExecutor

        def _fake(*args, **kwargs):
            seen.append(kwargs.get("max_workers"))
            return real(*args, **kwargs)

        items = [{"hash": f"H{i}", "downloader": "tr"} for i in range(25)]
        with mock.patch("seedspaceguard.ThreadPoolExecutor", _fake):
            plugin._prefetch_seed_files(items)

        self.assertEqual(seen, [1], "Transmission 不应并发")


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
