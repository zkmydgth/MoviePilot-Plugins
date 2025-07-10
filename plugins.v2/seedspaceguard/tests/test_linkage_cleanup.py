# -*- coding: utf-8 -*-
"""
联动清理回归测试：种子 / 转移记录 / 刮削产物。

对应「源文件联动清理」插件的三项能力在「保种空间守护」中的等价实现。
核心约束（用户明确要求）：

    仅文件模式下，**必须某个种子下的所有文件都删除后，才删除该种子**。

因此本文件的重点是围绕 ``_seed_fully_removed`` 的判定边界展开：
只要该种子还有任一文件在磁盘上存在（含被保护后缀跳过的），就必须保留种子。
"""

import os
import shutil
import tempfile
import unittest
from unittest import mock

import tests  # noqa: F401  触发宿主桩路径注入

from app.db.downloadhistory_oper import DownloadHistoryOper
from app.db.transferhistory_oper import TransferHistoryOper
from seedspaceguard import SeedSpaceGuard, SCRAP_EXTENSIONS


class _LinkageBase(unittest.TestCase):
    """联动清理夹具：真实临时目录 + 内存版数据层。"""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="ssg-link-")
        self.dl = os.path.join(self.base, "download")
        self.lib = os.path.join(self.base, "library")
        for path in (self.dl, self.lib):
            os.makedirs(path)
        DownloadHistoryOper.reset()
        TransferHistoryOper.reset()
        self.plugin = SeedSpaceGuard()
        self.plugin._target_dirs = [os.path.normpath(self.dl)]
        self.plugin._active_dirs = [os.path.normpath(self.dl)]
        self.plugin._mode = "file"
        self.plugin._dry_run = False
        self.plugin._delete_torrents = False
        self.plugin._delete_history = False
        self.plugin._delete_scrap_infos = False
        self.plugin._downloadhis = DownloadHistoryOper()
        self.plugin._transferhis = TransferHistoryOper()

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    # ------------------------------------------------------------------
    def make(self, name, content=b"x", sub=""):
        """在下载目录下创建文件，返回绝对路径。"""
        parent = os.path.join(self.dl, sub) if sub else self.dl
        os.makedirs(parent, exist_ok=True)
        path = os.path.join(parent, name)
        with open(path, "wb") as handle:
            handle.write(content)
        return path


# ==========================================================================
# 一、刮削产物清理
# ==========================================================================
class TestScrapCleanup(_LinkageBase):
    """刮削文件清理的匹配规则与防误删边界。"""

    def setUp(self):
        super().setUp()
        self.plugin._delete_scrap_infos = True

    def test_same_stem_scrap_removed(self):
        """同词干的 nfo / 图片 / 字幕应被一并清理。"""
        media = self.make("阿凡达.mkv")
        for name in ("阿凡达.nfo", "阿凡达-poster.jpg", "阿凡达.zh.srt",
                     "阿凡达.fanart.png", "阿凡达.ass"):
            self.make(name)
        cleaned = self.plugin._clean_scrap_infos(media)
        self.assertEqual(len(cleaned), 5)
        for name in ("阿凡达.nfo", "阿凡达-poster.jpg", "阿凡达.zh.srt",
                     "阿凡达.fanart.png", "阿凡达.ass"):
            self.assertFalse(os.path.exists(os.path.join(self.dl, name)))

    def test_sequel_scrap_preserved(self):
        """续集刮削不得被误删：阿凡达2.nfo 与 阿凡达 词干不一致。"""
        media = self.make("阿凡达.mkv")
        sequel = self.make("阿凡达2.nfo")
        other = self.make("泰坦尼克号.nfo")
        cleaned = self.plugin._clean_scrap_infos(media)
        self.assertEqual(cleaned, [])
        self.assertTrue(os.path.exists(sequel))
        self.assertTrue(os.path.exists(other))

    def test_non_scrap_extension_preserved(self):
        """非刮削后缀（如另一个视频）不得被清理。"""
        media = self.make("阿凡达.mkv")
        video = self.make("阿凡达-cd2.mkv")
        cleaned = self.plugin._clean_scrap_infos(media)
        self.assertEqual(cleaned, [])
        self.assertTrue(os.path.exists(video))

    def test_trickplay_dir_removed(self):
        """同名 .trickplay 目录应被整体清理。"""
        media = self.make("阿凡达.mkv")
        trick = os.path.join(self.dl, "阿凡达.trickplay")
        os.makedirs(trick)
        with open(os.path.join(trick, "thumb.jpg"), "wb") as handle:
            handle.write(b"t")
        cleaned = self.plugin._clean_scrap_infos(media)
        self.assertEqual(cleaned, [trick])
        self.assertFalse(os.path.exists(trick))

    def test_other_media_trickplay_preserved(self):
        """其它媒体的 .trickplay 目录不得被清理。"""
        media = self.make("阿凡达.mkv")
        trick = os.path.join(self.dl, "泰坦尼克号.trickplay")
        os.makedirs(trick)
        cleaned = self.plugin._clean_scrap_infos(media)
        self.assertEqual(cleaned, [])
        self.assertTrue(os.path.exists(trick))

    def test_scrap_in_subdir_isolated(self):
        """
        刮削清理不跨目录：子目录里的同名 nfo 不受影响。

        注意：同时放一个**同目录**的同名 nfo 作为「阳性对照」，确保测试
        确实走到了匹配逻辑——否则变异体（如改用固定父目录）会同样返回空，
        导致测试因错误原因而通过。
        """
        media = self.make("阿凡达.mkv")
        sibling = self.make("阿凡达.nfo")
        nested = self.make("阿凡达.nfo", sub="sub")
        cleaned = self.plugin._clean_scrap_infos(media)
        self.assertEqual(cleaned, [sibling])
        self.assertFalse(os.path.exists(sibling))
        self.assertTrue(os.path.exists(nested))

    def test_scrap_outside_target_dir_skipped(self):
        """
        配置目录外的刮削路径不得被删除（安全边界）。

        分两层验证：

        1. 扫描层隔离：越界目录不在扫描范围，天然不会被清理
        2. 删除层兜底：直接构造越界路径调用 ``_remove_scrap_path``，
           验证即便上游误传，安全边界仍会拦住——这是真正的纵深防御
        """
        media = self.make("阿凡达.mkv")
        inside = self.make("阿凡达.nfo")
        outside = os.path.join(self.lib, "阿凡达.nfo")
        with open(outside, "wb") as handle:
            handle.write(b"o")
        cleaned = self.plugin._clean_scrap_infos(media)
        self.assertEqual(cleaned, [inside])
        self.assertFalse(os.path.exists(inside))
        self.assertTrue(os.path.exists(outside))

        # 删除层兜底：模拟上游误传越界路径
        direct: list = []
        self.plugin._remove_scrap_path(outside, direct)
        self.assertEqual(direct, [])
        self.assertTrue(os.path.exists(outside),
                        "越界路径被删除，安全边界失效")

    def test_remove_scrap_path_refuses_target_dir_itself(self):
        """删除层不得删除配置目录本身。"""
        target = self.plugin._target_dirs[0]
        direct: list = []
        self.plugin._remove_scrap_path(target, direct)
        self.assertEqual(direct, [])
        self.assertTrue(os.path.isdir(target), "配置目录本身被删除")

    def test_all_whitelist_extensions_covered(self):
        """白名单内各后缀均应被识别并清理（回归防护，避免遗漏后缀）。"""
        media = self.make("movie.mkv")
        for ext in SCRAP_EXTENSIONS:
            self.make(f"movie{ext}")
        cleaned = self.plugin._clean_scrap_infos(media)
        self.assertEqual(len(cleaned), len(SCRAP_EXTENSIONS))


# ==========================================================================
# 一之二、刮削产物不得进入清理候选池
# ==========================================================================
class TestScrapExcludedFromCandidates(_LinkageBase):
    """
    刮削产物不应被当作可清理的「空间占用文件」。

    理由：nfo/图片/字幕体积通常在 KB 级，删除它们对释放空间几乎无贡献，
    却会破坏媒体库元数据。它们只应随所属媒体文件一并清理。
    """

    def _index(self, patterns=None):
        return self.plugin._index_files(patterns or ["*.part"], 0)

    def test_scrap_not_in_candidates(self):
        """刮削文件不得出现在清理候选中。"""
        self.make("movie.mkv")
        self.make("movie.nfo")
        self.make("movie.srt")
        self.make("movie-poster.jpg")
        files, _ino = self._index()
        names = [os.path.basename(f[1]) for f in files]
        self.assertEqual(names, ["movie.mkv"])

    def test_media_still_in_candidates(self):
        """媒体文件本身仍需正常纳入候选（阳性对照）。"""
        self.make("a.mkv")
        self.make("b.mp4")
        self.make("c.ts")
        files, _ino = self._index()
        names = sorted(os.path.basename(f[1]) for f in files)
        self.assertEqual(names, ["a.mkv", "b.mp4", "c.ts"])

    def test_scrap_extension_case_insensitive(self):
        """后缀大小写不敏感，避免 .NFO 漏网。"""
        self.make("movie.mkv")
        self.make("movie.NFO")
        self.make("movie.Srt")
        files, _ino = self._index()
        names = [os.path.basename(f[1]) for f in files]
        self.assertEqual(names, ["movie.mkv"])


# ==========================================================================
# 二、转移记录删除
# ==========================================================================
class TestTransferHistory(_LinkageBase):
    """转移历史记录的删除与回退逻辑。"""

    def setUp(self):
        super().setUp()
        self.plugin._delete_history = True

    def test_delete_by_dest(self):
        """优先按目标路径命中并删除。"""
        path = self.make("阿凡达.mkv")
        TransferHistoryOper.add_record(1, "/src/阿凡达.mkv", path)
        self.assertTrue(self.plugin._delete_transfer_history(path))
        self.assertEqual(TransferHistoryOper.deleted_ids, [1])

    def test_fallback_to_src(self):
        """目标路径未命中时回退按源路径匹配。"""
        src = self.make("阿凡达.mkv")
        TransferHistoryOper.add_record(7, src, "/library/阿凡达.mkv")
        self.assertTrue(self.plugin._delete_transfer_history(src))
        self.assertEqual(TransferHistoryOper.deleted_ids, [7])

    def test_no_record_returns_false(self):
        """无关联记录时返回 False 且不产生删除。"""
        path = self.make("无关.mkv")
        self.assertFalse(self.plugin._delete_transfer_history(path))
        self.assertEqual(TransferHistoryOper.deleted_ids, [])

    def test_disabled_switch_noop(self):
        """开关关闭时不得触碰记录。"""
        path = self.make("阿凡达.mkv")
        TransferHistoryOper.add_record(3, "/src", path)
        self.plugin._delete_history = False
        self.assertFalse(self.plugin._delete_transfer_history(path))
        self.assertEqual(TransferHistoryOper.deleted_ids, [])

    def test_query_exception_degrades(self):
        """查询抛异常时降级返回 False，不向调用方抛错。"""
        path = self.make("阿凡达.mkv")
        TransferHistoryOper.raise_on_query = True
        self.assertFalse(self.plugin._delete_transfer_history(path))

    def test_delete_exception_degrades(self):
        """删除抛异常时降级返回 False。"""
        path = self.make("阿凡达.mkv")
        TransferHistoryOper.add_record(9, "/src", path)
        with mock.patch.object(TransferHistoryOper, "delete",
                               side_effect=RuntimeError("boom")):
            self.assertFalse(self.plugin._delete_transfer_history(path))


# ==========================================================================
# 三、删种判定：核心约束「所有文件都删完才删种」
# ==========================================================================
class TestSeedFullyRemoved(_LinkageBase):
    """_seed_fully_removed 的存在性判定边界。"""

    def test_all_files_gone_returns_true(self):
        """种子全部文件已从磁盘消失 → True。"""
        gone = [os.path.join(self.dl, f"f{i}.mkv") for i in range(3)]
        DownloadHistoryOper.add_seed("HASH1", gone)
        self.assertTrue(self.plugin._seed_fully_removed("HASH1"))

    def test_one_file_remains_returns_false(self):
        """仍有任一文件存在 → False（不得删种）。"""
        alive = self.make("f0.mkv")
        gone = os.path.join(self.dl, "f1.mkv")
        DownloadHistoryOper.add_seed("HASH2", [alive, gone])
        self.assertFalse(self.plugin._seed_fully_removed("HASH2"))

    def test_state_field_ignored_uses_disk(self):
        """
        记录 state 仍为 1（正常）但磁盘上文件已消失时，仍应判定为「已删完」。

        这是关键回归：我们不信任 DownloadFiles.state，因为它由 MoviePilot
        维护，不会因我们用 os.unlink 删文件而同步置 0。
        """
        gone = os.path.join(self.dl, "f.mkv")
        DownloadHistoryOper.add_seed("HASH3", [gone], state=1)
        self.assertTrue(self.plugin._seed_fully_removed("HASH3"))

    def test_empty_records_returns_false(self):
        """无任何文件记录时保守返回 False（无从判定，不删种）。"""
        self.assertFalse(self.plugin._seed_fully_removed("UNKNOWN"))

    def test_empty_hash_returns_false(self):
        """空 hash 直接返回 False。"""
        self.assertFalse(self.plugin._seed_fully_removed(""))

    def test_query_exception_returns_false(self):
        """数据层异常时保守返回 False。"""
        with mock.patch.object(DownloadHistoryOper, "get_files_by_hash",
                               side_effect=RuntimeError("db down")):
            self.assertFalse(self.plugin._seed_fully_removed("HASH4"))

    def test_no_oper_returns_false(self):
        """操作器缺失时保守返回 False。"""
        self.plugin._downloadhis = None
        self.assertFalse(self.plugin._seed_fully_removed("HASH5"))

    def test_blank_fullpath_skipped(self):
        """记录中 fullpath 为空的行应被跳过，不影响判定。"""
        alive = self.make("real.mkv")
        DownloadHistoryOper.add_seed("HASH6", ["", alive])
        self.assertFalse(self.plugin._seed_fully_removed("HASH6"))


# ==========================================================================
# 四、联动编排：_run_linkage_after_delete
# ==========================================================================
class TestLinkageOrchestration(_LinkageBase):
    """删除后的联动编排顺序与开关组合。"""

    def setUp(self):
        super().setUp()
        self.detail: list = []

    def test_all_switches_off_noop(self):
        """全关时不产生任何联动动作。"""
        path = self.make("阿凡达.mkv")
        stats = self.plugin._run_linkage_after_delete([path], self.detail, False)
        self.assertEqual(stats, {"scrap": 0, "history": 0,
                                 "torrent": 0, "torrent_kept": 0})

    def test_dry_run_no_side_effect(self):
        """试运行即使开关全开也不产生副作用。"""
        path = self.make("阿凡达.mkv")
        self.make("阿凡达.nfo")
        TransferHistoryOper.add_record(1, "/src", path)
        DownloadHistoryOper.add_seed("H", [path])
        self.plugin._delete_scrap_infos = True
        self.plugin._delete_history = True
        self.plugin._delete_torrents = True
        stats = self.plugin._run_linkage_after_delete([path], self.detail, True)
        self.assertEqual(stats["scrap"], 0)
        self.assertEqual(TransferHistoryOper.deleted_ids, [])
        self.assertTrue(os.path.exists(os.path.join(self.dl, "阿凡达.nfo")))

    def test_torrent_deleted_only_when_all_files_gone(self):
        """该种子文件全删完 → 删种；仍有文件 → 保留种子。"""
        # 场景 A：种子下两个文件都已删除
        full_gone = [os.path.join(self.dl, "a.mkv"), os.path.join(self.dl, "b.mkv")]
        DownloadHistoryOper.add_seed("FULL", full_gone)
        # 场景 B：种子下还有一个文件在磁盘上
        alive = self.make("c.mkv")
        remain = [alive, os.path.join(self.dl, "d.mkv")]
        DownloadHistoryOper.add_seed("PART", remain)

        self.plugin._delete_torrents = True
        with mock.patch.object(SeedSpaceGuard, "_delete_torrent_by_hash",
                               return_value=True) as patched:
            stats = self.plugin._run_linkage_after_delete(
                [full_gone[0], remain[0]], self.detail, False
            )
        called = {c.args[0] for c in patched.call_args_list}
        self.assertIn("FULL", called)
        self.assertNotIn("PART", called)
        self.assertEqual(stats["torrent"], 1)
        self.assertEqual(stats["torrent_kept"], 1)

    def test_seed_mode_skips_delete_judgement(self):
        """
        种子级模式下不做「文件全删才删种」判定（种子已被下载器删除）。

        关键：必须让该种子下的文件**全部消失**，否则「未调用」可能只是因为
        ``_seed_fully_removed`` 返回了 False，测试便因错误原因通过，
        无法真正区分「模式跳过」与「判定拦截」两条路径。
        """
        path = self.make("x.mkv")
        DownloadHistoryOper.add_seed("H", [path])
        os.unlink(path)  # 制造「所有文件已删完」这一在 file 模式下会触发删种的状态
        self.plugin._delete_torrents = True
        self.plugin._mode = "seed"
        with mock.patch.object(SeedSpaceGuard, "_delete_torrent_by_hash",
                               return_value=True) as patched:
            stats = self.plugin._run_linkage_after_delete([path], self.detail, False)
        patched.assert_not_called()
        self.assertEqual(stats["torrent"], 0)

        # 反向对照：同一状态下若改为 file 模式，则必须触发删种
        self.plugin._mode = "file"
        with mock.patch.object(SeedSpaceGuard, "_delete_torrent_by_hash",
                               return_value=True) as patched2:
            self.plugin._run_linkage_after_delete([path], self.detail, False)
        patched2.assert_called_once()

    def test_deduplicate_same_hash(self):
        """同一 hash 的多个文件只判定一次。"""
        paths = [os.path.join(self.dl, "1.mkv"), os.path.join(self.dl, "2.mkv")]
        DownloadHistoryOper.add_seed("DUP", paths)
        self.plugin._delete_torrents = True
        with mock.patch.object(SeedSpaceGuard, "_delete_torrent_by_hash",
                               return_value=True) as patched:
            self.plugin._run_linkage_after_delete(paths, self.detail, False)
        self.assertEqual(patched.call_count, 1)

    def test_unknown_hash_skipped(self):
        """反查不到 hash 的文件不触发删种。"""
        path = self.make("orphan.mkv")
        self.plugin._delete_torrents = True
        with mock.patch.object(SeedSpaceGuard, "_delete_torrent_by_hash",
                               return_value=True) as patched:
            self.plugin._run_linkage_after_delete([path], self.detail, False)
        patched.assert_not_called()

    def test_all_three_features_together(self):
        """三项功能同时开启的完整编排。"""
        path = self.make("阿凡达.mkv")
        self.make("阿凡达.nfo")
        self.make("阿凡达-poster.jpg")
        TransferHistoryOper.add_record(5, "/src", path)
        DownloadHistoryOper.add_seed("ALL", [path])
        # 模拟文件已被清理（联动的输入前提是本轮确实删除了文件）
        os.unlink(path)

        self.plugin._delete_scrap_infos = True
        self.plugin._delete_history = True
        self.plugin._delete_torrents = True
        with mock.patch.object(SeedSpaceGuard, "_delete_torrent_by_hash",
                               return_value=True):
            stats = self.plugin._run_linkage_after_delete([path], self.detail, False)
        self.assertEqual(stats["scrap"], 2)
        self.assertEqual(stats["history"], 1)
        self.assertEqual(stats["torrent"], 1)
        self.assertEqual(TransferHistoryOper.deleted_ids, [5])
        self.assertFalse(os.path.exists(os.path.join(self.dl, "阿凡达.nfo")))


# ==========================================================================
# 五、_delete_torrent_by_hash / _get_downloader_for
# ==========================================================================
class TestDeleteTorrentByHash(_LinkageBase):
    """种子删除的下载器定位与调用。"""

    def test_empty_hash_returns_false(self):
        """空 hash 不触发下载器调用。"""
        self.assertFalse(self.plugin._delete_torrent_by_hash(""))

    def test_no_downloader_returns_false(self):
        """找不到持有该种子的下载器时返回 False。"""
        with mock.patch.object(SeedSpaceGuard, "_get_downloader_for",
                               return_value=None):
            self.assertFalse(self.plugin._delete_torrent_by_hash("H"))

    def test_remove_torrents_called_without_file_deletion(self):
        """调用下载器时必须 delete_file=False（文件已由插件删除）。"""
        fake = mock.MagicMock()
        fake.remove_torrents.return_value = True
        with mock.patch.object(SeedSpaceGuard, "_get_downloader_for",
                               return_value=fake):
            self.assertTrue(self.plugin._delete_torrent_by_hash("H", "标题"))
        fake.remove_torrents.assert_called_once_with(
            hashs=["H"], delete_file=False
        )

    def test_downloader_error_degrades(self):
        """下载器抛异常时降级返回 False。"""
        fake = mock.MagicMock()
        fake.remove_torrents.side_effect = RuntimeError("qb down")
        with mock.patch.object(SeedSpaceGuard, "_get_downloader_for",
                               return_value=fake):
            self.assertFalse(self.plugin._delete_torrent_by_hash("H"))

    def test_target_downloader_filter(self):
        """配置了目标下载器范围时，非范围内的下载器应被排除。"""
        in_scope = mock.MagicMock()
        in_scope.name = "qb-main"
        in_scope.get_torrents.return_value = [{"hash": "H"}]
        out_scope = mock.MagicMock()
        out_scope.name = "tr-backup"
        out_scope.get_torrents.return_value = [{"hash": "H"}]

        fake_mgr = mock.MagicMock()
        fake_mgr.get_modules.return_value = [out_scope, in_scope]
        self.plugin._downloaders = ["qb-main"]
        with mock.patch("seedspaceguard.ModuleManager", return_value=fake_mgr):
            found = self.plugin._get_downloader_for("H")
        self.assertIs(found, in_scope)

    def test_no_scope_returns_first_match(self):
        """未配置范围时返回第一个持有该种子的下载器。"""
        module = mock.MagicMock()
        module.name = "any"
        module.get_torrents.return_value = [{"hash": "H"}]
        fake_mgr = mock.MagicMock()
        fake_mgr.get_modules.return_value = [module]
        self.plugin._downloaders = []
        with mock.patch("seedspaceguard.ModuleManager", return_value=fake_mgr):
            self.assertIs(self.plugin._get_downloader_for("H"), module)


if __name__ == "__main__":
    unittest.main(verbosity=2)
