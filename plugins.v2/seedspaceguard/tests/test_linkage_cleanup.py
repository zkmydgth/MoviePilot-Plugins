# -*- coding: utf-8 -*-
"""
联动清理回归测试：联动删除种子 / 删除转移记录。

核心约束（用户明确要求）：

    仅文件模式下，**必须某个种子下的所有文件都删除后，才删除该种子**。

因此本文件的重点是围绕 ``_seed_fully_removed`` 的判定边界展开：
只要该种子还有任一文件在磁盘上存在（含被保护后缀跳过的），就必须保留种子。

另覆盖清理范围规则：除「保护文件后缀」命中的外，**目录下所有文件均纳入候选**
（含 nfo/图片/字幕等刮削产物），不再按文件类型区分。
"""

import os
import shutil
import tempfile
import unittest
from unittest import mock

import tests  # noqa: F401  触发宿主桩路径注入

from app.db.downloadhistory_oper import DownloadHistoryOper
from app.db.transferhistory_oper import TransferHistoryOper
from seedspaceguard import SeedSpaceGuard


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
        self.assertEqual(stats, {"history": 0,
                                 "torrent": 0, "torrent_kept": 0})

    def test_dry_run_no_side_effect(self):
        """试运行即使开关全开也不产生副作用。"""
        path = self.make("阿凡达.mkv")
        self.make("阿凡达.nfo")
        TransferHistoryOper.add_record(1, "/src", path)
        DownloadHistoryOper.add_seed("H", [path])
        self.plugin._delete_history = True
        self.plugin._delete_torrents = True
        stats = self.plugin._run_linkage_after_delete([path], self.detail, True)
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

    def test_both_features_together(self):
        """两项功能同时开启的完整编排。"""
        path = self.make("阿凡达.mkv")
        TransferHistoryOper.add_record(5, "/src", path)
        DownloadHistoryOper.add_seed("ALL", [path])
        # 模拟文件已被清理（联动的输入前提是本轮确实删除了文件）
        os.unlink(path)

        self.plugin._delete_history = True
        self.plugin._delete_torrents = True
        with mock.patch.object(SeedSpaceGuard, "_delete_torrent_by_hash",
                               return_value=True):
            stats = self.plugin._run_linkage_after_delete([path], self.detail, False)
        self.assertEqual(stats["history"], 1)
        self.assertEqual(stats["torrent"], 1)
        self.assertEqual(TransferHistoryOper.deleted_ids, [5])


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


# ==========================================================================
# 六、清理范围：除保护后缀外，所有文件均纳入候选
# ==========================================================================
class TestCandidateScope(_LinkageBase):
    """
    候选池范围的回归测试。

    规则（用户明确要求）：除「保护文件后缀」命中的文件外，**目录下所有文件
    均纳入清理候选**，不区分文件类型——刮削产物（nfo/图片/字幕）同样计入，
    以便「多余文件一并删除」。
    """

    def _index(self, patterns=None):
        return self.plugin._index_files(patterns or [], 0)

    def test_scrap_files_included(self):
        """刮削产物必须进入候选池（与旧版行为相反）。"""
        self.make("movie.mkv")
        self.make("movie.nfo")
        self.make("movie.srt")
        self.make("movie-poster.jpg")
        files, _ino = self._index()
        names = sorted(os.path.basename(f[1]) for f in files)
        self.assertEqual(names, ["movie-poster.jpg", "movie.mkv",
                                 "movie.nfo", "movie.srt"])

    def test_arbitrary_extension_included(self):
        """任意后缀的文件都应纳入候选，不受类型限制。"""
        for name in ("a.mkv", "b.txt", "c.bak", "d.iso", "e.json", "f.bin"):
            self.make(name)
        files, _ino = self._index()
        self.assertEqual(len(files), 6)

    def test_protect_pattern_excludes(self):
        """保护后缀命中的文件不得进入候选。"""
        self.make("movie.mkv")
        self.make("downloading.part")
        self.make("downloading.!qb")
        self.make("temp.tmp")
        files, _ino = self._index(
            ["*.part", "*.!qb", "*.tmp"]
        )
        names = [os.path.basename(f[1]) for f in files]
        self.assertEqual(names, ["movie.mkv"])

    def test_protect_pattern_string_parsed_fully(self):
        """
        经 ``_protect_pattern`` 字符串解析时，**每个** pattern 都必须生效。

        回归防护：此处若只取第一个 pattern，后续后缀的保护会静默失效，
        导致下载中的临时文件被误删。上面那个用例直接传 patterns 列表，
        绕过了字符串解析，测不出这个问题，因此必须单独覆盖解析路径。
        """
        self.make("movie.mkv")
        self.make("a.part")
        self.make("b.!qb")
        self.make("c.download")
        self.make("d.aria2")
        self.make("e.tmp")
        self.make("f.crdownload")
        self.plugin._protect_pattern = (
            "*.part|*.!qb|*.download|*.aria2|*.tmp|*.crdownload"
        )
        # 走 _clean_by_file 的解析路径：空目录扫描，只关心候选集合
        patterns = [
            p.strip()
            for p in __import__("re").split(r"[,|，]", self.plugin._protect_pattern)
            if p.strip()
        ]
        self.assertEqual(
            len(patterns), 6,
            "解析后应得到 6 个 pattern，少于 6 说明解析把后缀漏掉了",
        )
        files, _ino = self._index(patterns)
        names = [os.path.basename(f[1]) for f in files]
        self.assertEqual(names, ["movie.mkv"],
                         f"保护后缀未全部生效，候选={names}")

    def test_protect_pattern_comma_separated(self):
        """支持逗号/中文逗号分隔（与 | 等效）。"""
        self.make("movie.mkv")
        self.make("a.part")
        self.make("b.tmp")
        patterns = [
            p.strip()
            for p in __import__("re").split(r"[,|，]", "*.part,*.tmp")
            if p.strip()
        ]
        files, _ino = self._index(patterns)
        names = [os.path.basename(f[1]) for f in files]
        self.assertEqual(names, ["movie.mkv"])

    def test_empty_protect_pattern_includes_all(self):
        """保护后缀为空时，所有文件均纳入（用户当前配置即此情形）。"""
        for name in ("a.mkv", "b.part", "c.nfo", "d.tmp"):
            self.make(name)
        files, _ino = self._index([])
        self.assertEqual(len(files), 4)

    def test_trickplay_dir_not_as_file(self):
        """.trickplay 等目录不作为文件纳入（遍历只取文件）。"""
        self.make("movie.mkv")
        os.makedirs(os.path.join(self.dl, "movie.trickplay"))
        files, _ino = self._index()
        self.assertEqual(len(files), 1)

    def test_system_dirs_still_excluded(self):
        """DSM 系统目录与回收站仍应排除（安全边界不因全盘扫描而放宽）。"""
        self.make("movie.mkv")
        self.make("SYNOINDEX_x", sub="@eaDir")
        self.make("recycled.mkv", sub="#recycle")
        files, _ino = self._index()
        names = [os.path.basename(f[1]) for f in files]
        self.assertEqual(names, ["movie.mkv"])

    def test_nested_dirs_scanned(self):
        """子目录中的文件同样纳入候选（全盘扫描）。"""
        self.make("top.mkv")
        self.make("nested.mkv", sub="sub/deep")
        files, _ino = self._index()
        self.assertEqual(len(files), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
