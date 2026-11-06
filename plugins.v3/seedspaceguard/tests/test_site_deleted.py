# -*- coding: utf-8 -*-
"""
Transmission 文件清单预取修复 + 站点删种回收测试（v3.6.0）。

背景（用户 2026-11-06 报障）
--------------------------
tr 里有 26 个「No data found」错误种子：文件确实被空间清理删掉了，
但对应的种子一直留在下载器里没被回收。追查发现是一条静默失效链：

1. 批量预取用 ``getattr(item, "files", None)`` 取文件清单，而当前
   ``transmission_rpc`` 的 Torrent 对象把字段放在 ``fields`` 字典里、
   **没有** ``files`` 属性 → 每个种子都被写成空清单（日志里
   「命中 1552 个」其实是 1552 个空列表）；
2. 空清单让 ``_get_seed_files`` 直接返回空 → 空壳判定退回「扫整个内容
   目录」→ 同目录里兄弟种子的文件被当成本种子的残留 → 判「仍有文件」；
3. 该错误结论还会写进 24 小时有效的增量缓存，于是每轮都命中旧结论。

本文件守护四件事：

  A. 批量预取必须按 ``fields`` 字典取值，且**空清单绝不进缓存**；
  B. 修复后，「自己的文件已删 + 同目录仍有兄弟文件」的种子必须被回收；
  C. 站点删种（tracker 明确公告未注册/已删除）要被识别，且只摘种子、
     **绝不删文件**；
  D. 判定必须保守：任一 tracker 公告成功、下载器本地错误（error=3）、
     临时失败措辞、没有任何 tracker 公告，都不算站点删种。
"""

import os
import shutil
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import tests  # noqa: F401  触发宿主桩路径注入

import seedspaceguard as ssg
from app.core.module import ModuleManager
from app.schemas.types import DownloaderType
from seedspaceguard import SeedSpaceGuard


# ======================================================================
# 桩：transmission_rpc 原始客户端与条目
# ======================================================================
class _RpcTorrent:
    """模拟 ``transmission_rpc`` 的 Torrent：字段只在 ``fields`` 字典里。"""

    def __init__(self, fields):
        """
        保存远程字段字典。

        :param fields: transmission 返回的字段字典
        """
        self.fields = fields

    def __getattr__(self, name):
        """
        刻意不提供字段同名属性，复刻真实对象的行为（取 ``.files`` 会抛错）。

        :param name: 属性名
        :return: 永不返回
        """
        raise AttributeError(f"'Torrent' object has no attribute '{name}'")


class _RpcClient:
    """模拟 ``trc``：一次请求返回整批种子的文件清单。"""

    def __init__(self, files_map):
        """
        记录每个 hash 的文件清单。

        :param files_map: {hash: [{name: 相对路径}, ...]}
        """
        self.files_map = files_map
        self.calls = []

    def get_torrents(self, ids=None, arguments=None, timeout=None):
        """
        返回本批种子的 ``hashString`` + ``files`` 字段。

        :param ids: hash 列表
        :param arguments: 请求字段
        :param timeout: 超时（桩忽略）
        :return: ``_RpcTorrent`` 列表
        """
        self.calls.append((list(ids or []), list(arguments or [])))
        return [
            _RpcTorrent({
                "hashString": hash_str,
                "files": self.files_map.get(hash_str, []),
            })
            for hash_str in (ids or [])
        ]


class _Server:
    """下载器桩：枚举种子、提供（可失效的）逐种子清单、记录删种调用。"""

    def __init__(self, torrents, files_map=None, trc=None):
        """
        初始化桩。

        :param torrents: 种子原始条目列表
        :param files_map: 逐种子接口返回的清单（默认全空）
        :param trc: 可选的原始 transmission 客户端
        """
        self.torrents = torrents
        self.files_map = files_map or {}
        self.trc = trc
        self.removed = []

    def get_torrents(self, *args, **kwargs):
        """
        返回种子列表。

        :return: 种子原始条目列表
        """
        return self.torrents

    def get_files(self, tid=None, downloader=None, **kwargs):
        """
        返回逐种子文件清单（本桩默认给空，模拟宿主取不到清单的情形）。

        :param tid: 种子 hash
        :param downloader: 下载器名
        :return: 清单列表
        """
        return self.files_map.get(tid, [])

    def remove_torrents(self, hashs=None, delete_file=False,
                        downloader=None, **kwargs):
        """
        记录一次删种调用。

        :param hashs: 种子 hash 列表
        :param delete_file: 是否连带删文件
        :param downloader: 下载器名
        :return: 恒为 True
        """
        self.removed.append((list(hashs or []), delete_file))
        return True


def _install(server):
    """
    把下载器桩注册进宿主模块表。

    :param server: 下载器桩
    :return: 同一个桩
    """
    ModuleManager.reset()
    ModuleManager.register_downloader(
        DownloaderType.Transmission, "tr", server,
    )
    return server


def _tr_item(dl_dir, name, hash_str, error=0, error_string="",
             announce=("Success",)):
    """
    构造 Transmission 原始条目（含站点删种判定所需字段）。

    :param dl_dir: 下载目录
    :param name: 种子名（即内容目录名）
    :param hash_str: 种子 hash
    :param error: Transmission 的 error 字段
    :param error_string: Transmission 的 errorString
    :param announce: 各 tracker 的最后一次公告结果
    :return: 条目字典
    """
    return {
        "download_dir": dl_dir,
        "name": name,
        "hash_string": hash_str,
        "percent_done": 1.0,
        "total_size": 4 * 1024 ** 3,
        "done_date": int(time.time()) - 40 * 86400,
        "added_date": int(time.time()) - 40 * 86400,
        "error": error,
        "errorString": error_string,
        "trackerStats": [
            {"lastAnnounceResult": text} for text in announce
        ],
    }


class _Base(unittest.TestCase):
    """夹具：一个「同目录多种子」的真实目录结构。"""

    def setUp(self):
        """
        建立下载目录、媒体库目录与一个剧集子目录。
        """
        self.base = tempfile.mkdtemp(prefix="ssg-sitedel-")
        self.dl = os.path.join(self.base, "download")
        self.lib = os.path.join(self.base, "library")
        os.makedirs(self.dl)
        os.makedirs(self.lib)
        self.show = os.path.join(self.dl, "Show.S01")
        os.makedirs(self.show)

    def tearDown(self):
        """
        清理临时目录与插件全局统计。
        """
        shutil.rmtree(self.base, ignore_errors=True)
        SeedSpaceGuard._clean_stats = None

    def make_plugin(self, delete_torrents=True, reap_site_deleted=True):
        """
        构造一个只填必要运行态字段的插件实例。

        :param delete_torrents: 联动删种总闸
        :param reap_site_deleted: 站点删种回收开关
        :return: 插件实例
        """
        plugin = SeedSpaceGuard()
        plugin._target_dirs = [self.dl, self.lib]
        plugin._active_dirs = [self.dl, self.lib]
        plugin._volume_path = self.base
        plugin._protect_pattern = ""
        plugin._protect_dirs = []
        plugin._recent_skip_days = 0
        plugin._threshold_gb = 1.0
        plugin._companion_cleanup = False
        plugin._orphan_cleanup = False
        plugin._orphan_seed_scope = False
        plugin._delete_torrents = delete_torrents
        plugin._delete_history = False
        plugin._reap_site_deleted = reap_site_deleted
        plugin._downloadhis = None
        plugin._transferhis = None
        plugin._ino_paths = {}
        plugin._mode = "seed"
        plugin._clean_stats = {
            "files": 0, "transfers": 0, "seeds": 0,
            "companions": 0, "orphans": 0, "stalled": False,
        }
        return plugin

    def make_file(self, path, size_kb=64, age_days=30):
        """
        创建一个带历史修改时间的文件。

        :param path: 文件路径
        :param size_kb: 文件大小（KB）
        :param age_days: 相对今天的天数
        :return: 文件路径
        """
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(b"x" * size_kb)
        stamp = time.time() - age_days * 86400
        os.utime(path, (stamp, stamp))
        return path


# ======================================================================
# A. 批量预取：按 fields 字典取值，空清单不入缓存
# ======================================================================
class TestPrefetchFromFields(_Base):

    def test_1_prefetch_reads_fields_dict(self):
        """字段只在 ``fields`` 字典里时也要取到文件清单。"""
        plugin = self.make_plugin()
        plugin._seed_files_cache = {}
        client = _RpcClient({"h1": [{"name": "Show.S01/E13.mkv"}]})

        ok = plugin._fetch_seed_files_chunk("tr", client, ["h1"])

        self.assertTrue(ok, "请求本身应视为成功")
        self.assertEqual(
            plugin._seed_files_cache, {"h1": ["Show.S01/E13.mkv"]},
            "必须从 fields['files'] 读到清单，而不是取不到的 .files 属性",
        )

    def test_2_empty_file_list_not_cached(self):
        """空清单/缺字段的种子绝不写入缓存（交给逐种子查询兜底）。"""
        plugin = self.make_plugin()
        plugin._seed_files_cache = {}
        client = _RpcClient({"h1": [{"name": "Show.S01/E13.mkv"}]})

        plugin._fetch_seed_files_chunk("tr", client, ["h1", "h2"])

        self.assertIn("h1", plugin._seed_files_cache)
        self.assertNotIn(
            "h2", plugin._seed_files_cache,
            "空清单进缓存会让判定退回扫目录，同目录兄弟文件把空壳永久挡住",
        )

    def test_3_prefetch_result_joins_download_dir(self):
        """预取结果经 ``_get_seed_files`` 拼成绝对路径。"""
        plugin = self.make_plugin()
        plugin._seed_files_cache = {"h1": ["Show.S01/E13.mkv"]}
        cand = {"path": self.show, "dl_dir": self.dl, "module": object()}

        files = plugin._get_seed_files("h1", cand)

        self.assertEqual(
            files, [os.path.join(self.dl, "Show.S01", "E13.mkv")]
        )

    def test_4_e2e_own_file_gone_reaped_via_prefetch(self):
        """⭐ 回归：自己的文件已删 + 同目录有兄弟文件 → 必须回收。

        逐种子接口刻意返回空（模拟宿主取不到清单），只有批量预取能提供
        真实清单。修复前这里会把空清单写进缓存 → 退回扫目录 → 判「仍有
        文件」→ 26 个空壳种子永远回收不掉。
        """
        self.make_file(os.path.join(self.show, "E01.mkv"))
        self.make_file(os.path.join(self.show, "E02.mkv"))
        client = _RpcClient({"h1": [{"name": "Show.S01/E13.mkv"}]})
        server = _install(_Server(
            [_tr_item(self.dl, "Show.S01", "h1",
                      announce=("Success",))],
            trc=client,
        ))
        plugin = self.make_plugin()

        with patch.object(ssg, "ORPHAN_BATCH_MIN_CANDIDATES", 1):
            stats = plugin._reap_orphan_seeds(dry_run=False)

        self.assertTrue(client.calls, "必须真的走了批量预取")
        self.assertEqual(
            stats["torrent"], 1,
            "自己的文件已删完 → 必须回收，不能被同目录兄弟文件挡住",
        )
        self.assertEqual(server.removed, [(["h1"], False)])


# ======================================================================
# B. 站点删种：识别与回收
# ======================================================================
class TestSiteDeletedDetection(_Base):

    def test_5_tracker_deleted_detected(self):
        """tracker 公告「未注册/已删除」+ error=2 → 判为站点删种。"""
        plugin = self.make_plugin()
        cand = plugin._parse_torrent(
            DownloaderType.Transmission,
            _tr_item(self.dl, "Show.S01", "h1", error=2,
                     error_string="torrent not registered with this "
                                  "tracker (deleted)",
                     announce=("torrent not registered with this tracker "
                               "(deleted)",)),
        )
        self.assertTrue(cand["site_deleted"])

    def test_6_success_announce_keeps_seed(self):
        """任一 tracker 公告成功 → 不算站点删种（可能是多站辅种）。"""
        plugin = self.make_plugin()
        cand = plugin._parse_torrent(
            DownloaderType.Transmission,
            _tr_item(self.dl, "Show.S01", "h1", error=2,
                     error_string="torrent not registered with this tracker",
                     announce=("Success",
                               "torrent not registered with this tracker")),
        )
        self.assertEqual(cand["site_deleted"], "")

    def test_7_local_error_is_not_site_deleted(self):
        """error=3（找不到本地数据）属空壳链路，不能当成站点删种。"""
        plugin = self.make_plugin()
        cand = plugin._parse_torrent(
            DownloaderType.Transmission,
            _tr_item(self.dl, "Show.S01", "h1", error=3,
                     error_string="No data found!",
                     announce=("torrent not registered with this tracker",)),
        )
        self.assertEqual(cand["site_deleted"], "")

    def test_8_transient_message_ignored(self):
        """超时/连接失败等临时措辞不算站点删种。"""
        plugin = self.make_plugin()
        cand = plugin._parse_torrent(
            DownloaderType.Transmission,
            _tr_item(self.dl, "Show.S01", "h1", error=2,
                     error_string="Could not connect to tracker",
                     announce=("Timeout", "Could not connect to tracker")),
        )
        self.assertEqual(cand["site_deleted"], "")

    def test_9_no_announce_result_keeps_conservative(self):
        """没有 tracker 公告、错误码也不是警告时，光凭错误串不足以判定。"""
        plugin = self.make_plugin()
        cand = plugin._parse_torrent(
            DownloaderType.Transmission,
            _tr_item(self.dl, "Show.S01", "h1", error=0,
                     error_string="torrent not registered with this tracker",
                     announce=()),
        )
        self.assertEqual(cand["site_deleted"], "")


# ======================================================================
# C. 站点删种：回收行为（只摘种子、不删文件）
# ======================================================================
class TestSiteDeletedReap(_Base):

    def _run(self, plugin, server):
        """
        跑一轮空壳回收。

        :param plugin: 插件实例
        :param server: 下载器桩
        :return: 统计字典
        """
        with patch.object(ssg, "ORPHAN_BATCH_MIN_CANDIDATES", 10 ** 6):
            return plugin._reap_orphan_seeds(dry_run=False)

    def test_10_site_deleted_reaped_files_kept(self):
        """⭐ 站点删种 → 只摘种子，磁盘文件一个都不能动。"""
        target = self.make_file(os.path.join(self.show, "E13.mkv"))
        server = _install(_Server([
            _tr_item(self.dl, "Show.S01", "h1", error=2,
                     error_string="torrent not registered with this "
                                  "tracker (deleted)",
                     announce=("torrent not registered with this tracker "
                               "(deleted)",)),
        ]))
        plugin = self.make_plugin()

        stats = self._run(plugin, server)

        self.assertEqual(stats["site_deleted"], 1)
        self.assertEqual(stats["torrent"], 0, "不得混进空壳计数")
        self.assertEqual(server.removed, [(["h1"], False)])
        self.assertTrue(os.path.exists(target), "站点删种绝不能删除文件")

    def test_11_switch_off_keeps_seed(self):
        """关闭「回收站点已删种的种子」后不得摘种。"""
        self.make_file(os.path.join(self.show, "E13.mkv"))
        server = _install(_Server([
            _tr_item(self.dl, "Show.S01", "h1", error=2,
                     error_string="torrent not registered with this tracker",
                     announce=("torrent not registered with this tracker",)),
        ]))
        plugin = self.make_plugin(reap_site_deleted=False)

        stats = self._run(plugin, server)

        self.assertEqual(stats["site_deleted"], 0)
        self.assertEqual(server.removed, [])

    def test_12_dry_run_only_reports(self):
        """试运行只预告，不真删。"""
        self.make_file(os.path.join(self.show, "E13.mkv"))
        server = _install(_Server([
            _tr_item(self.dl, "Show.S01", "h1", error=2,
                     error_string="torrent not registered with this tracker",
                     announce=("torrent not registered with this tracker",)),
        ]))
        plugin = self.make_plugin()

        with patch.object(ssg, "ORPHAN_BATCH_MIN_CANDIDATES", 10 ** 6):
            stats = plugin._reap_orphan_seeds(dry_run=True)

        self.assertEqual(stats["site_deleted"], 1)
        self.assertEqual(server.removed, [], "试运行不得真删")
        self.assertTrue(any("试运行" in line for line in stats["_lines"]))

    def test_13_alive_cache_does_not_block_site_deleted(self):
        """「仍有文件」缓存只约束空壳结论，不能挡住站点删种。"""
        self.make_file(os.path.join(self.show, "E13.mkv"))
        server = _install(_Server([
            _tr_item(self.dl, "Show.S01", "h1", error=2,
                     error_string="torrent not registered with this tracker",
                     announce=("torrent not registered with this tracker",)),
        ]))
        plugin = self.make_plugin()
        plugin._orphan_alive_state = {
            "h1": {"ts": time.time(),
                   "sig": f"{self.show}|{self.dl}"},
        }

        stats = self._run(plugin, server)

        self.assertEqual(stats["site_deleted"], 1)
        self.assertEqual(server.removed, [(["h1"], False)])

    def test_14_delete_torrents_gate_still_applies(self):
        """总闸（联动删除种子）关闭时，站点删种回收同样不生效。"""
        self.make_file(os.path.join(self.show, "E13.mkv"))
        server = _install(_Server([
            _tr_item(self.dl, "Show.S01", "h1", error=2,
                     error_string="torrent not registered with this tracker",
                     announce=("torrent not registered with this tracker",)),
        ]))
        plugin = self.make_plugin(delete_torrents=False)

        stats = self._run(plugin, server)

        self.assertEqual(stats["site_deleted"], 0)
        self.assertEqual(server.removed, [])


if __name__ == "__main__":
    unittest.main()
