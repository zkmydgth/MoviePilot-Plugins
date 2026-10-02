# -*- coding: utf-8 -*-
"""
空壳判定改用「种子自身文件清单」测试（v3.0.5）。

背景（用户 2026-10-02 报障）：

  > 我发现 tr 中有个 hash 为 6036bbc8... 的种子找不到文件但没有被回收

  排查后发现它**并非空壳**（磁盘上确实有 10 个文件），但顺着查出了真正的
  缺陷：该种子只负责其中一集（E13），一旦 E13 被删而兄弟种子的文件还在，
  `_seed_fully_removed` 的第 2 级会扫**整个目录** → 扫到别人的文件 →
  判定「未删空」→ **该种子永远回收不掉**。

  用户的原始要求：

  > 我的要求是该种子的所有文件被删除后，自动删除这个种子，而不动种子文件
  > 所在文件夹下的其它文件，直到这个文件夹确认清空，再删除文件夹

本测试覆盖三部分：

  A. **核心修复**：自己的文件删完 → 即便同目录还有兄弟种子的文件，也必须回收。
  B. **保守垫**：清单取不到 → 退回扫目录；兄弟文件绝不被删除。
  C. **覆盖两条链路**：空壳回收（`_reap_orphan_seeds`）与仅文件模式联动删种
     （`_run_linkage_after_delete`）——后者在 v3.0.5 补漏前是**半失效**的。
"""

import os
import shutil
import tempfile
import time
import unittest

import tests  # noqa: F401  触发宿主桩路径注入

from app.core.module import ModuleManager
from app.schemas.types import DownloaderType
from seedspaceguard import SeedSpaceGuard


class _Server:
    """可控的下载器桩：枚举种子 + 提供文件清单 + 记录删种调用。"""

    def __init__(self, torrents, files_map=None, removed_hook=None,
                 raise_on_get=False, raise_on_files=False,
                 files_return_none=False):
        self.torrents = torrents
        # {hash: [file_dict_or_obj, ...]}；缺失的 hash 视为「清单为空」
        self.files_map = files_map or {}
        self.removed_hook = removed_hook
        self.raise_on_get = raise_on_get
        self.raise_on_files = raise_on_files
        self.files_return_none = files_return_none

    def get_torrents(self, *args, **kwargs):
        if self.raise_on_get:
            raise RuntimeError("downloader boom")
        return self.torrents

    def get_files(self, tid=None, downloader=None, **kwargs):
        """qBittorrent 风格：返回 dict 列表（`f["name"]`）。"""
        if self.raise_on_files:
            raise RuntimeError("files boom")
        if self.files_return_none:
            return None
        return self.files_map.get(tid, [])

    def remove_torrents(self, hashs=None, delete_file=False,
                        downloader=None, **kwargs):
        if self.removed_hook:
            normalized = [hashs] if isinstance(hashs, str) else list(hashs or [])
            self.removed_hook(normalized, delete_file)
        return True


class _ServerTR(_Server):
    """Transmission 风格：只有 `torrent_files`，返回对象列表（`f.name`）。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.get_files = None          # 确保不命中 qB 的别名
        self.removed_file_calls = []

    def torrent_files(self, tid=None, downloader=None, **kwargs):
        if self.raise_on_files:
            raise RuntimeError("files boom")
        if self.files_return_none:
            return None
        return [_FileObj(name) for name in self.files_map.get(tid, [])]

    def remove_torrents(self, hashs=None, delete_file=False,
                        downloader=None, **kwargs):
        # 记录调用，同时保持与基类一致的 hook 行为
        self.removed_file_calls.append((hashs, delete_file))
        return super().remove_torrents(
            hashs=hashs, delete_file=delete_file, downloader=downloader,
        )


class _FileObj:
    """模拟 `transmission_rpc.File`：只有 `.name` 属性，取不到 `.get`。"""

    def __init__(self, name):
        self.name = name
        self.size = 1024

    def get(self, *_args, **_kwargs):     # pragma: no cover - 防误用
        raise AttributeError("transmission_rpc.File 没有 get()")


def _install_downloader(torrents, files_map=None, removed_hook=None,
                        raise_on_get=False, raise_on_files=False,
                        files_return_none=False, tr=False):
    """注册下载器桩（qB 或 TR）。"""
    cls = _ServerTR if tr else _Server
    server = cls(
        torrents,
        files_map=files_map,
        removed_hook=removed_hook,
        raise_on_get=raise_on_get,
        raise_on_files=raise_on_files,
        files_return_none=files_return_none,
    )
    ModuleManager.reset()
    ModuleManager.register_downloader(
        DownloaderType.Transmission if tr else DownloaderType.Qbittorrent,
        "tr" if tr else "qb",
        server,
    )
    return server


def _qb_item(content_path, hash_str, name, progress=1.0, save_path=None):
    """构造 qBittorrent 原始条目（camelCase 字段）。"""
    return {
        "content_path": content_path,
        "save_path": save_path if save_path is not None
        else os.path.dirname(content_path),
        "hash": hash_str,
        "name": name,
        "progress": progress,
        "completion_on": int(time.time()) - 40 * 86400,
        "size": 4 * 1024 ** 3,
        "added_on": int(time.time()) - 40 * 86400,
    }


def _tr_item(download_dir, name, hash_str, percent=1.0):
    """构造 Transmission 原始条目（snake_case 字段）。"""
    return {
        "download_dir": download_dir,
        "name": name,
        "hash_string": hash_str,
        "percent_done": percent,
        "total_size": 4 * 1024 ** 3,
        "done_date": int(time.time()) - 40 * 86400,
        "added_date": int(time.time()) - 40 * 86400,
    }


class _FakeDownloadHis:
    """极简 DownloadFiles 操作器替身：按完整路径精确反查 hash。

    真实实现查 PostgreSQL 的 ``downloadfiles`` 表；这里只需支撑
    ``_resolve_hash_by_path`` 的第 1 级精确匹配，足以驱动
    ``_run_linkage_after_delete`` 的判定链路。
    """

    def __init__(self, mapping=None):
        # {fullpath: hash}
        self.mapping = dict(mapping or {})

    def get_files_by_hash(self, hash_str):
        """按 hash 取记录（第 1 级记录复核用）；此处返回空，交由第 2 级判定。"""
        return []

    def get_hash_by_fullpath(self, path):
        return self.mapping.get(path, "")


class _Base(unittest.TestCase):
    """夹具：一个「同目录多种子」的真实场景。

    目录结构（复刻实测生产环境）::

        <dl>/Show.S01/
            E01.mkv      ← 兄弟种子 sib1 负责
            E02.mkv      ← 兄弟种子 sib1 负责
            E13.mkv      ← 本测试的主角 own1 负责
    """

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="ssg-perfile-")
        self.dl = os.path.join(self.base, "download")
        self.lib = os.path.join(self.base, "library")
        os.makedirs(self.dl)
        os.makedirs(self.lib)
        self.show = os.path.join(self.dl, "Show.S01")
        os.makedirs(self.show)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)
        SeedSpaceGuard._clean_stats = None

    # ------------------------------------------------------------------
    def make_plugin(self, mode="file", delete_torrents=True, scope=False,
                    downloadhis=None):
        p = SeedSpaceGuard()
        p._target_dirs = [self.dl, self.lib]
        p._active_dirs = [self.dl, self.lib]
        p._volume_path = self.base
        p._protect_pattern = ""
        p._recent_skip_days = 0
        p._threshold_gb = 1.0
        p._companion_cleanup = False
        p._orphan_cleanup = False
        p._orphan_seed_scope = scope
        p._delete_torrents = delete_torrents
        p._delete_history = False
        # 联动删种链路需要按路径反查 hash（DownloadFiles 记录）
        p._downloadhis = downloadhis if downloadhis is not None \
            else _FakeDownloadHis()
        p._transferhis = None
        p._ino_paths = {}
        p._mode = mode
        p._clean_stats = {
            "files": 0, "transfers": 0, "seeds": 0,
            "companions": 0, "orphans": 0, "stalled": False,
        }
        return p

    def make_file(self, path, size_kb=64, age_days=30):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(b"x" * size_kb)
        stamp = time.time() - age_days * 86400
        os.utime(path, (stamp, stamp))
        return path


# ======================================================================
# A. 核心修复：自己的文件删完 → 必须回收（即便同目录有兄弟文件）
# ======================================================================
class TestCoreFix(_Base):

    def test_1_own_files_gone_sibling_files_remain_reaped(self):
        """⭐ 核心修复：自己的文件已删 + 同目录有兄弟种子文件 → 必须回收。

        改造前：第 2 级扫整个目录 → 扫到 E01/E02（兄弟种子的）→ 判「未删空」
                → **永远回收不掉**（功能静默失效）。
        改造后：按自身清单（只有 E13）复核 → E13 已不在 → 回收。
        """
        self.make_file(os.path.join(self.show, "E01.mkv"))
        self.make_file(os.path.join(self.show, "E02.mkv"))
        # E13 故意不创建（已被删）
        removed = []
        _install_downloader(
            [_qb_item(self.show, "own1", "Show.S01",
                      save_path=self.dl)],
            files_map={"own1": [{"name": "Show.S01/E13.mkv", "id": 2}]},
            removed_hook=lambda h, d: removed.append((h, d)),
        )
        plugin = self.make_plugin()

        res = plugin._reap_orphan_seeds(dry_run=False)
        self.assertEqual(
            res["torrent"], 1,
            "自己的文件已删完 → 必须回收，不能被同目录的兄弟文件挡住",
        )
        self.assertEqual(removed, [(["own1"], False)])

    def test_2_own_files_remain_sibling_remain_kept(self):
        """自己的文件还在 + 同目录有兄弟文件 → 保留（不得误删）。"""
        self.make_file(os.path.join(self.show, "E01.mkv"))
        self.make_file(os.path.join(self.show, "E13.mkv"))
        removed = []
        _install_downloader(
            [_qb_item(self.show, "own1", "Show.S01", save_path=self.dl)],
            files_map={"own1": [{"name": "Show.S01/E13.mkv"}]},
            removed_hook=lambda h, d: removed.append((h, d)),
        )
        plugin = self.make_plugin()

        res = plugin._reap_orphan_seeds(dry_run=False)
        self.assertEqual(res["torrent"], 0, "自己的文件还在，绝不能回收")
        self.assertEqual(res["alive"], 1)
        self.assertEqual(removed, [])

    def test_3_own_files_gone_dir_empty_reaped(self):
        """自己的文件已删 + 同目录也空 → 回收（旧逻辑也满足，防回归）。"""
        removed = []
        _install_downloader(
            [_qb_item(self.show, "own1", "Show.S01", save_path=self.dl)],
            files_map={"own1": [{"name": "Show.S01/E13.mkv"}]},
            removed_hook=lambda h, d: removed.append((h, d)),
        )
        plugin = self.make_plugin()

        res = plugin._reap_orphan_seeds(dry_run=False)
        self.assertEqual(res["torrent"], 1)
        self.assertEqual(removed, [(["own1"], False)])

    def test_4_own_files_gone_dir_has_empty_subdir_reaped(self):
        """自己文件已删 + 同目录只剩空子目录 → 回收。

        空子目录不该算「有文件」——否则 DSM 的 @eaDir 之类残片会永久
        挡住回收。
        """
        os.makedirs(os.path.join(self.show, "Sample"))
        removed = []
        _install_downloader(
            [_qb_item(self.show, "own1", "Show.S01", save_path=self.dl)],
            files_map={"own1": [{"name": "Show.S01/E13.mkv"}]},
            removed_hook=lambda h, d: removed.append((h, d)),
        )
        plugin = self.make_plugin()

        res = plugin._reap_orphan_seeds(dry_run=False)
        self.assertEqual(res["torrent"], 1, "空子目录不算「有文件」")


# ======================================================================
# B. 保守垫：取不到清单 → 退回扫目录
# ======================================================================
class TestConservativeFallback(_Base):

    def test_5_no_filelist_sibling_files_remain_kept(self):
        """取不到清单 + 同目录有文件 → 保留（退回保守，不误删）。"""
        self.make_file(os.path.join(self.show, "E01.mkv"))
        removed = []
        _install_downloader(
            [_qb_item(self.show, "own1", "Show.S01", save_path=self.dl)],
            files_map={},                      # 清单为空 → 取不到
            removed_hook=lambda h, d: removed.append((h, d)),
        )
        plugin = self.make_plugin()

        res = plugin._reap_orphan_seeds(dry_run=False)
        self.assertEqual(
            res["torrent"], 0,
            "取不到清单时必须退回扫目录，保守保留（宁可漏回收不可误删）",
        )
        self.assertEqual(removed, [])

    def test_6_no_filelist_dir_empty_reaped(self):
        """取不到清单 + 同目录空 → 仍能回收（退回后不失效）。"""
        removed = []
        _install_downloader(
            [_qb_item(self.show, "own1", "Show.S01", save_path=self.dl)],
            files_map={},
            removed_hook=lambda h, d: removed.append((h, d)),
        )
        plugin = self.make_plugin()

        res = plugin._reap_orphan_seeds(dry_run=False)
        self.assertEqual(
            res["torrent"], 1,
            "退回扫目录后，同目录确实空时仍应回收（保守≠失效）",
        )

    def test_7_filelist_dict_form_qb(self):
        """下载器返回 dict 形态（qBittorrent）→ 正确解析。"""
        self.make_file(os.path.join(self.show, "E01.mkv"))
        removed = []
        _install_downloader(
            [_qb_item(self.show, "own1", "Show.S01", save_path=self.dl)],
            files_map={"own1": [{"name": "Show.S01/E13.mkv", "id": 2}]},
            removed_hook=lambda h, d: removed.append((h, d)),
        )
        plugin = self.make_plugin()

        res = plugin._reap_orphan_seeds(dry_run=False)
        self.assertEqual(res["torrent"], 1, "dict 形态（f['name']）必须被正确解析")

    def test_8_filelist_object_form_tr(self):
        """下载器返回对象形态（Transmission）→ 正确解析。"""
        self.make_file(os.path.join(self.show, "E01.mkv"))
        server = _install_downloader(
            [_tr_item(self.dl, "Show.S01", "own1")],
            files_map={"own1": ["Show.S01/E13.mkv"]},
            tr=True,
        )
        plugin = self.make_plugin()

        res = plugin._reap_orphan_seeds(dry_run=False)
        self.assertEqual(res["torrent"], 1, "对象形态（f.name）必须被正确解析")
        self.assertEqual(server.removed_file_calls, [(["own1"], False)])

    def test_9_filelist_raises_falls_back(self):
        """`torrent_files` 抛异常 → 退回扫目录，不崩。

        同目录有兄弟文件 → 保守保留。
        """
        self.make_file(os.path.join(self.show, "E01.mkv"))
        removed = []
        _install_downloader(
            [_qb_item(self.show, "own1", "Show.S01", save_path=self.dl)],
            raise_on_files=True,
            removed_hook=lambda h, d: removed.append((h, d)),
        )
        plugin = self.make_plugin()

        res = plugin._reap_orphan_seeds(dry_run=False)
        self.assertEqual(res["torrent"], 0, "取清单异常必须退回保守，不能崩")
        self.assertEqual(res["failed"], 0, "异常应被内部消化，不计入 failed")
        self.assertEqual(removed, [])

    def test_10_filelist_returns_none_falls_back(self):
        """`torrent_files` 返回 None → 退回扫目录。"""
        self.make_file(os.path.join(self.show, "E01.mkv"))
        removed = []
        _install_downloader(
            [_qb_item(self.show, "own1", "Show.S01", save_path=self.dl)],
            files_return_none=True,
            removed_hook=lambda h, d: removed.append((h, d)),
        )
        plugin = self.make_plugin()

        res = plugin._reap_orphan_seeds(dry_run=False)
        self.assertEqual(res["torrent"], 0, "返回 None 必须退回保守")
        self.assertEqual(removed, [])

    def test_11_filelist_paths_all_siblings_kept(self):
        """清单里的路径全是兄弟种子文件（异常数据）→ 保守保留。

        若清单被污染（混入了别人的文件），且那些文件还在磁盘上，判定会
        得出「自己的文件还在 → 保留」。这是**安全侧**的偏差（漏回收），
        绝不允许反向（误判空壳）。
        """
        self.make_file(os.path.join(self.show, "E01.mkv"))
        removed = []
        _install_downloader(
            [_qb_item(self.show, "own1", "Show.S01", save_path=self.dl)],
            # 异常数据：清单里塞了别人的文件
            files_map={"own1": [{"name": "Show.S01/E01.mkv"}]},
            removed_hook=lambda h, d: removed.append((h, d)),
        )
        plugin = self.make_plugin()

        res = plugin._reap_orphan_seeds(dry_run=False)
        self.assertEqual(
            res["torrent"], 0,
            "清单内容异常时必须落在「保留」这一侧（漏回收可接受，误删不可接受）",
        )

    def test_12_dry_run_previews_reap(self):
        """试运行：判定照做，计入预览，不真删。"""
        self.make_file(os.path.join(self.show, "E01.mkv"))
        removed = []
        _install_downloader(
            [_qb_item(self.show, "own1", "Show.S01", save_path=self.dl)],
            files_map={"own1": [{"name": "Show.S01/E13.mkv"}]},
            removed_hook=lambda h, d: removed.append((h, d)),
        )
        plugin = self.make_plugin()

        res = plugin._reap_orphan_seeds(dry_run=True)
        self.assertEqual(res["torrent"], 1, "试运行应如实预告将回收的种子")
        self.assertEqual(removed, [], "试运行绝不能真删")

    def test_13_reap_uses_delete_file_false(self):
        """⚠️ 空壳回收删种**必须** `delete_file=False`（数据安全底线）。"""
        removed = []
        _install_downloader(
            [_qb_item(self.show, "own1", "Show.S01", save_path=self.dl)],
            files_map={"own1": [{"name": "Show.S01/E13.mkv"}]},
            removed_hook=lambda h, d: removed.append((h, d)),
        )
        plugin = self.make_plugin()

        plugin._reap_orphan_seeds(dry_run=False)
        self.assertTrue(removed, "应有删种调用")
        self.assertTrue(
            all(d is False for _h, d in removed),
            "空壳回收只摘种子、绝不删文件——delete_file 必须为 False",
        )

    def test_14_sibling_files_not_deleted_e2e(self):
        """E2E：兄弟种子的文件在整轮回收后**原封不动**。"""
        own = os.path.join(self.show, "E13.mkv")
        sib1 = self.make_file(os.path.join(self.show, "E01.mkv"), size_kb=1024)
        sib2 = self.make_file(os.path.join(self.show, "E02.mkv"), size_kb=1024)
        removed = []
        _install_downloader(
            [_qb_item(self.show, "own1", "Show.S01", save_path=self.dl)],
            files_map={"own1": [{"name": "Show.S01/E13.mkv"}]},
            removed_hook=lambda h, d: removed.append((h, d)),
        )
        plugin = self.make_plugin()

        res = plugin._reap_orphan_seeds(dry_run=False)
        self.assertEqual(res["torrent"], 1)
        self.assertFalse(os.path.exists(own), "自己的文件早已不在")
        self.assertTrue(os.path.exists(sib1), "兄弟种子的文件绝不能被删除")
        self.assertTrue(os.path.exists(sib2), "兄弟种子的文件绝不能被删除")


# ======================================================================
# C. 覆盖两条链路（v3.0.5 补漏：仅文件模式联动删种原本半失效）
# ======================================================================
class TestBothLinkages(_Base):

    def test_15_out_of_scope_path_still_judged_correctly(self):
        """`cand["path"]` **不在监控目录内** + 清单可得 → 判定仍正确。

        实测发现 TR 的 `cand["path"]` = `os.path.join(download_dir, name)`，
        其 `download_dir` 常在用户配置的清理目录**之外**（例如 TR 配
        `/volume1/video/下载`，而清理目录配 `/volume1/video`）。

        两层含义，本用例同时锁住：
        1. **候选层**：范围外种子要靠 `orphan_seed_scope` 才进候选
           （v3.0.4 的能力，本用例顺带回归，防改造破坏它）；
        2. **判定层**：进了候选之后，判定只看文件存在性，**与路径是否在
           监控目录内无关**——清单能取到就必须能正确回收。
        """
        away_dl = os.path.join(self.base, "away-dl")
        away_show = os.path.join(away_dl, "Away.S01")
        os.makedirs(away_show)
        # 种子的清单文件已删（不创建）；同目录有兄弟文件
        self.make_file(os.path.join(away_show, "E01.mkv"))
        removed = []
        _install_downloader(
            [_tr_item(away_dl, "Away.S01", "away1")],
            files_map={"away1": ["Away.S01/E13.mkv"]},
            tr=True,
            removed_hook=lambda h, d: removed.append((h, d)),
        )
        plugin = self.make_plugin(mode="file", scope=True)

        res = plugin._reap_orphan_seeds(dry_run=False)
        self.assertEqual(
            res["torrent"], 1,
            "清单判定与「路径是否在监控目录内」无关，仍应正确回收",
        )
        self.assertEqual(removed, [(["away1"], False)])
        self.assertTrue(
            os.path.exists(os.path.join(away_show, "E01.mkv")),
            "兄弟种子的文件必须原封不动",
        )

    def test_16_file_mode_linkage_uses_precise_filelist(self):
        """⭐ 仅文件模式联动删种（`_run_linkage_after_delete`）走精确清单。

        v3.0.5 补漏前，该处 `probe` 只传 `{"path": ...}`（无 `module`），
        导致清单永远取不到 → 退回扫目录 → 同目录多种子时依旧误判
        「未删空」→ **该链路修复等于没做**。
        """
        # 自己的文件已删，兄弟文件还在
        self.make_file(os.path.join(self.show, "E01.mkv"))
        own_hash = "own1"
        own_path = os.path.join(self.show, "E13.mkv")

        removed = []
        _install_downloader(
            [_qb_item(self.show, own_hash, "Show.S01", save_path=self.dl)],
            files_map={own_hash: [{"name": "Show.S01/E13.mkv"}]},
            removed_hook=lambda h, d: removed.append((h, d)),
        )
        plugin = self.make_plugin(
            mode="file",
            downloadhis=_FakeDownloadHis({own_path: own_hash}),
        )
        # 直接走联动入口：模拟 E13 刚被本插件删除
        detail_lines = []
        stats = plugin._run_linkage_after_delete(
            [own_path], detail_lines, dry_run=False
        )
        self.assertEqual(
            stats["torrent"], 1,
            "仅文件模式下，自己的文件删完就该删种——不能被同目录兄弟文件挡住",
        )
        self.assertEqual(stats["torrent_kept"], 0)
        self.assertEqual(removed, [(["own1"], False)])

    def test_17_file_mode_linkage_no_cand_conservative(self):
        """仅文件模式 + 取不到候选（`probe=None`）→ 退回第 1 级，保守保留。

        `_collect_seed_candidates` 只收监控目录内的种子，因此「候选取不到」
        是常态。此时必须落在「保留」侧，不引入新的误删风险。
        """
        # 候选列表里没有这个 hash（模拟范围外/已消失）
        other = os.path.join(self.dl, "Other")
        os.makedirs(other)
        removed = []
        _install_downloader(
            [_qb_item(other, "other1", "Other", save_path=self.dl)],
            files_map={"other1": [{"name": "Other/x.mkv"}]},
            removed_hook=lambda h, d: removed.append((h, d)),
        )
        gone_hash = "ghost1"
        gone_path = os.path.join(self.show, "E13.mkv")   # 不存在
        plugin = self.make_plugin(
            mode="file",
            downloadhis=_FakeDownloadHis({gone_path: gone_hash}),
        )
        detail_lines = []

        stats = plugin._run_linkage_after_delete(
            [gone_path], detail_lines, dry_run=False
        )
        self.assertEqual(
            stats["torrent"], 0,
            "取不到候选时必须保守保留（无从判定不等于已删空）",
        )
        self.assertEqual(stats["torrent_kept"], 1)
        self.assertEqual(removed, [])


# ======================================================================
# D. `_get_seed_files` / `_seed_download_base` 单元测试
# ======================================================================
class TestGetSeedFilesUnit(_Base):

    def test_18_get_seed_files_qb_joins_save_path(self):
        """qB：相对路径 + save_path 拼接成绝对路径。"""
        _install_downloader(
            [_qb_item(self.show, "h1", "Show.S01", save_path=self.dl)],
            files_map={"h1": [{"name": "Show.S01/E13.mkv"},
                              {"name": "Show.S01/E14.mkv"}]},
        )
        plugin = self.make_plugin()
        cand = plugin._collect_seed_candidates()[0]

        files = plugin._get_seed_files("h1", cand)
        self.assertEqual(
            files,
            [os.path.join(self.dl, "Show.S01", "E13.mkv"),
             os.path.join(self.dl, "Show.S01", "E14.mkv")],
            "必须拼成绝对路径，顺序与清单一致",
        )

    def test_19_get_seed_files_tr_joins_download_dir(self):
        """TR：相对路径 + download_dir 拼接成绝对路径。"""
        _install_downloader(
            [_tr_item(self.dl, "Show.S01", "h1")],
            files_map={"h1": ["Show.S01/E13.mkv"]},
            tr=True,
        )
        plugin = self.make_plugin()
        cand = plugin._collect_seed_candidates()[0]

        files = plugin._get_seed_files("h1", cand)
        self.assertEqual(
            files, [os.path.join(self.dl, "Show.S01", "E13.mkv")]
        )

    def test_20_get_seed_files_no_module_returns_empty(self):
        """无 `module` → 空列表（调用方退回保守）。"""
        _install_downloader([], files_map={})
        plugin = self.make_plugin()
        self.assertEqual(plugin._get_seed_files("h1", {"path": self.show}), [])
        self.assertEqual(plugin._get_seed_files("h1", None), [])
        self.assertEqual(plugin._get_seed_files("", {"module": object()}), [])

    def test_21_get_seed_files_no_base_returns_empty(self):
        """清单是相对路径但基准目录取不到 → 整体放弃，绝不拿相对路径试探。

        相对路径拿 `os.path.exists` 去查会在**进程工作目录**下意外命中，
        必须整体返回空列表。
        """
        class _Mod:
            def get_files(self, tid=None, downloader=None, **kw):
                return [{"name": "Show.S01/E13.mkv"}]

        plugin = self.make_plugin()
        # 既无 dl_dir，也无 path → 基准取不到
        self.assertEqual(
            plugin._get_seed_files("h1", {"module": _Mod()}), [],
            "拼不出绝对路径时必须整体放弃，不得返回相对路径",
        )

    def test_22_seed_download_base_file_form_qb(self):
        """qB 单文件种子的 `content_path` 是文件 → 沿路径找到种子名层。"""
        plugin = self.make_plugin()
        file_path = os.path.join(self.show, "E13.mkv")
        self.make_file(file_path)
        base = plugin._seed_download_base(
            {"path": file_path, "title": "Show.S01"}
        )
        self.assertEqual(base, os.path.normpath(self.dl))

    def test_23_seed_download_base_dir_form_tr(self):
        """TR 的 `path` 是目录 → 上一级即下载目录。"""
        plugin = self.make_plugin()
        base = plugin._seed_download_base(
            {"path": self.show, "title": "Show.S01"}
        )
        self.assertEqual(base, os.path.normpath(self.dl))

    def test_24_seed_download_base_prefers_dl_dir(self):
        """有 `dl_dir` 时优先用它（解析种子时就地记下，最可靠）。"""
        plugin = self.make_plugin()
        base = plugin._seed_download_base(
            {"path": self.show, "title": "Show.S01", "dl_dir": "/custom/dir"}
        )
        self.assertEqual(base, "/custom/dir")

    def test_25_parse_torrent_records_dl_dir(self):
        """`_parse_torrent` 必须为两种下载器都记下 `dl_dir`。"""
        plugin = self.make_plugin()
        qb = plugin._parse_torrent(
            DownloaderType.Qbittorrent,
            _qb_item(self.show, "h1", "Show.S01", save_path=self.dl),
        )
        self.assertEqual(qb["dl_dir"], self.dl)
        tr = plugin._parse_torrent(
            DownloaderType.Transmission, _tr_item(self.dl, "Show.S01", "h2")
        )
        self.assertEqual(tr["dl_dir"], self.dl)

    def test_26_get_seed_files_type_error_retries_without_downloader(self):
        """模块签名不接受 `downloader` 关键字 → 退回只传 tid。"""
        class _ModStrict:
            def __init__(self):
                self.calls = []

            def get_files(self, tid=None, **kw):
                if kw:                       # 只容忍 tid
                    raise TypeError("unexpected keyword")
                self.calls.append(tid)
                return [{"name": "Show.S01/E13.mkv"}]

        mod = _ModStrict()
        plugin = self.make_plugin()
        files = plugin._get_seed_files(
            "h1", {"module": mod, "dl_dir": self.dl, "downloader": "qb"}
        )
        self.assertEqual(
            files, [os.path.join(self.dl, "Show.S01", "E13.mkv")],
            "签名不兼容时应重试成功，而不是直接放弃",
        )


if __name__ == "__main__":
    unittest.main()
