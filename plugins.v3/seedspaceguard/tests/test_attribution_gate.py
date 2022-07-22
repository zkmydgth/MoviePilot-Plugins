# -*- coding: utf-8 -*-
"""
硬链接清理的「归属闸门」测试（2026-10-04，随 v3.0.9 引入）。

背景（代码审查发现的高危缺口）：

  `_build_inode_index` 是按**目录** walk 出来的索引 —— 目录里凡 `lstat`
  成功的普通文件都会被收录，**并不校验文件是否属于本种子**。而一个目录下
  常同时存放多个不同种子的文件（实测某剧集目录下 19 个种子各管一集）。
  若无闸门，「删硬链接」就等于「删索引里所有仍在盘上的文件」，会把同目录
  **其它种子**的文件（含其媒体库侧副本）一并 unlink → 红种 / H&R。

  注意：v3.0.5 修过同一个坑，但只修了 `_seed_fully_removed`（判定「空壳」用
  自身清单），**`_build_inode_index` 的目录 walk 一直没改** —— 属「漏改调用点」
  的同一模式。

本测试覆盖四部分：

  A. `_shared_content_dirs`：识别「与其它种子共用」的内容目录
  B. 归属闸门：只删有证据属于本种子的 inode（**不误删**）
  C. 继承路径：下载侧已消失的孤儿硬链接仍要被清理（**不放过**）
  D. 放宽候选集：取不到 probe 时先补取一次，避免第 2 级物理复核被整体跳过
"""

import os
import shutil
import tempfile
import unittest
from unittest import mock

import tests  # noqa: F401  触发宿主桩路径注入

from seedspaceguard import SeedSpaceGuard


class _FakeDownloadHis:
    """极简 DownloadFiles 替身：按完整路径精确反查 hash。"""

    def __init__(self, mapping=None):
        self.mapping = dict(mapping or {})

    def get_files_by_hash(self, hash_str):
        return []

    def get_hash_by_fullpath(self, path):
        return self.mapping.get(path, "")


class _Base(unittest.TestCase):
    """夹具：下载侧与媒体库侧互为硬链接，另有一个「兄弟种子」的文件。"""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="ssg-attr-")
        self.dl = os.path.join(self.base, "download")
        self.lib = os.path.join(self.base, "library")
        self.show = os.path.join(self.dl, "Show.S01")
        self.libshow = os.path.join(self.lib, "Show")
        os.makedirs(self.show)
        os.makedirs(self.libshow)
        # 本种子负责的文件（下载侧 + 媒体库侧硬链接）
        self.own_dl = os.path.join(self.show, "E13.mkv")
        self.own_lib = os.path.join(self.libshow, "E13.mkv")
        # 兄弟种子负责的文件（同目录，绝不允许被删）
        self.sib_dl = os.path.join(self.show, "E01.mkv")
        self.sib_lib = os.path.join(self.libshow, "E01.mkv")
        self._make_pair(self.own_dl, self.own_lib)
        self._make_pair(self.sib_dl, self.sib_lib)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)
        # ⚠️ 不能把类级 `_clean_stats` 置为 None：本文件在字母序上位于
        # test_notify_format / test_boundary 等之前，置 None 会让后续直调
        # `_clean_by_*` 的用例踩到「'seeds' not in None」而连带失败。
        # 这里恢复为一份干净的默认字典，既清状态又不污染他人。
        SeedSpaceGuard._clean_stats = {
            "files": 0,
            "transfers": 0,
            "seeds": 0,
            "companions": 0,
            "orphans": 0,
            "stalled": False,
            "dry_run": False,
        }

    @staticmethod
    def _make_pair(dl_path, lib_path):
        with open(dl_path, "wb") as handle:
            handle.write(b"x" * 4096)
        os.link(dl_path, lib_path)

    def make_plugin(self, mode="file"):
        plugin = SeedSpaceGuard()
        plugin._target_dirs = [self.dl, self.lib]
        plugin._active_dirs = [self.dl, self.lib]
        plugin._protect_pattern = ""
        plugin._recent_skip_days = 0
        plugin._threshold_gb = 1.0
        plugin._companion_cleanup = True
        plugin._orphan_cleanup = False
        plugin._orphan_seed_scope = False
        plugin._delete_torrents = True
        plugin._delete_history = False
        plugin._downloadhis = _FakeDownloadHis()
        plugin._transferhis = None
        plugin._ino_paths = {}
        plugin._dir_hash_cache = {}
        plugin._mode = mode
        plugin._clean_stats = {
            "files": 0, "transfers": 0, "seeds": 0,
            "companions": 0, "orphans": 0, "stalled": False,
        }
        return plugin


# ======================================================================
# A. 共用目录识别
# ======================================================================
class TestSharedContentDirs(_Base):

    def test_same_content_path_detected(self):
        """另一候选的内容路径与自身相同 → 判为共用。"""
        own = {"hash": "H1", "path": self.show, "title": "Show"}
        other = {"hash": "H2", "path": self.show, "title": "Show"}
        self.assertEqual(
            SeedSpaceGuard._shared_content_dirs(own, [own, other]),
            [self.show],
        )

    def test_nested_seed_detected(self):
        """另一候选落在自身内容目录**之内** → 同样判为共用。"""
        own = {"hash": "H1", "path": self.dl, "title": "Pack"}
        other = {"hash": "H2", "path": self.show, "title": "Show.S01"}
        self.assertEqual(
            SeedSpaceGuard._shared_content_dirs(own, [own, other]),
            [self.dl],
        )

    def test_no_sibling_no_exclusion(self):
        """无兄弟种子 → 不排除任何目录。"""
        own = {"hash": "H1", "path": self.show, "title": "Show"}
        other = {"hash": "H2", "path": os.path.join(self.lib, "Other")}
        self.assertEqual(
            SeedSpaceGuard._shared_content_dirs(own, [own, other]), [],
        )

    def test_self_excluded_from_comparison(self):
        """自己不能把自己判成兄弟（否则永远无法清理）。"""
        own = {"hash": "H1", "path": self.show}
        self.assertEqual(SeedSpaceGuard._shared_content_dirs(own, [own]), [])


# ======================================================================
# B. 归属闸门：不误删同目录其它种子的文件
# ======================================================================
class TestAttributionGate(_Base):

    def test_sibling_inode_blocked(self):
        """索引里属于兄弟种子的 inode 不得被删（最高危回归）。"""
        plugin = self.make_plugin()
        index = plugin._build_inode_index([
            self.own_dl, self.own_lib, self.sib_dl, self.sib_lib,
        ])
        self.assertEqual(len(index), 2, "两侧各 1 个 inode，共 2 个")

        own_inodes = plugin._inodes_of([self.own_dl])
        removed = plugin._clean_hardlinks_for(
            index, "own", own_inodes, [self.own_dl, self.own_lib],
        )

        self.assertEqual(removed, 2, "本种子的两侧（下载侧 + 媒体库侧）应被清理")
        self.assertFalse(os.path.exists(self.own_dl))
        self.assertFalse(os.path.exists(self.own_lib))
        # 关键断言：兄弟种子的文件必须完好
        self.assertTrue(os.path.exists(self.sib_dl), "兄弟种子下载侧文件不得被删")
        self.assertTrue(os.path.exists(self.sib_lib), "兄弟种子媒体库侧文件不得被删")

    def test_gate_disabled_when_owned_roots_none(self):
        """owned_roots=None 时不做闸门（保持既有调用签名与语义）。"""
        plugin = self.make_plugin()
        index = plugin._build_inode_index([self.own_dl, self.own_lib])
        removed = plugin._clean_hardlinks_for(index, "own")
        self.assertEqual(removed, 2)

    def test_no_evidence_at_all_refuses_to_delete(self):
        """既无归属基准、索引内又无路径消失 → 一律不删（不误删优先）。"""
        plugin = self.make_plugin()
        index = plugin._build_inode_index([self.own_dl, self.own_lib])
        removed = plugin._clean_hardlinks_for(index, "own", set(), [])
        self.assertEqual(removed, 0, "无任何归属依据时必须整体放弃清理")
        self.assertTrue(os.path.exists(self.own_dl))
        self.assertTrue(os.path.exists(self.own_lib))

    def test_paths_outside_owned_roots_blocked(self):
        """不在本次索引入口之下的路径，即使同 inode 也不删。"""
        plugin = self.make_plugin()
        index = plugin._build_inode_index([self.own_dl, self.own_lib])
        own_inodes = plugin._inodes_of([self.own_dl])
        # owned_roots 只给一个「无关」根：此时 own 的 inode 仍靠 attr_inodes 兜住，
        # 但若连 attr_inodes 也没有，则应整体拒绝
        removed = plugin._clean_hardlinks_for(
            index, "own", set(), [os.path.join(self.lib, "Unrelated")],
        )
        self.assertEqual(removed, 0)


# ======================================================================
# C. 不放过：下载侧已消失的孤儿硬链接必须被清理
# ======================================================================
class TestOrphanLinkStillCleaned(_Base):

    def test_missing_download_side_makes_inode_attributable(self):
        """建索引后下载侧被删 → 该 inode 有「消失证据」→ 媒体库侧仍要清掉。

        这是「不放过」的核心：删种后下载侧路径由下载器删除，若因取不到
        自身清单就放弃，媒体库侧会永久残留、空间永不释放。
        """
        plugin = self.make_plugin()
        index = plugin._build_inode_index([self.own_dl, self.own_lib])
        self.assertEqual(len(index), 1)
        os.unlink(self.own_dl)          # 模拟第 ② 步：下载器删掉下载侧

        removed = plugin._clean_hardlinks_for(index, "own", set(), [])
        # 计数含「已不存在」的那一侧（`_delete_one` 对 FileNotFoundError 记 1，
        # 该语义由 test_boundary「已不存在应记为已处理」锁定），故为 2；
        # 关键断言是媒体库侧文件被真正删除（空间释放）。
        self.assertEqual(removed, 2)
        self.assertFalse(os.path.exists(self.own_lib), "媒体库侧应被清理（空间释放）")

    def test_library_side_recorded_directly_is_cleaned(self):
        """下载侧早已不存在（只剩媒体库侧记录）时，仍能凭索引入口清理。"""
        os.unlink(self.own_dl)          # 建索引前下载侧就没了
        plugin = self.make_plugin()
        index = plugin._build_inode_index([self.own_dl, self.own_lib])
        self.assertEqual(len(index), 1, "只剩媒体库侧 1 个 inode")
        removed = plugin._clean_hardlinks_for(
            index, "own", set(), [self.own_lib],
        )
        self.assertEqual(removed, 1)
        self.assertFalse(os.path.exists(self.own_lib))


# ======================================================================
# D. 放宽候选集：probe 取不到时不得整体跳过第 2 级物理复核
# ======================================================================
class TestWidenedCandidateProbe(_Base):

    def test_probe_from_widened_candidates(self):
        """主候选集（判范围）取不到时，应改用放宽候选集拿到 probe。"""
        plugin = self.make_plugin()
        plugin._downloadhis = _FakeDownloadHis({self.own_dl: "H1"})

        wide_cand = {
            "hash": "H1", "module": mock.MagicMock(), "path": self.show,
            "title": "Show", "downloader": "qb",
        }
        seen = []

        def fake_fully_removed(hash_str, cand=None):
            seen.append(cand)
            return False

        with mock.patch.object(plugin, "_collect_seed_candidates",
                               return_value=[]), \
                mock.patch.object(plugin, "_collect_all_seed_candidates",
                                  return_value=[wide_cand]), \
                mock.patch.object(plugin, "_seed_fully_removed",
                                  side_effect=fake_fully_removed):
            stats = plugin._run_linkage_after_delete(
                [self.own_dl], [], False,
            )

        self.assertEqual(seen, [wide_cand], "应把放宽候选集里的完整候选交给物理复核")
        self.assertIsNotNone(seen[0], "probe 不得为 None（否则第 2 级被整体跳过）")
        self.assertEqual(stats["torrent_kept"], 1)
        self.assertEqual(stats["torrent"], 0)

    def test_widened_candidates_not_built_when_unneeded(self):
        """主候选集能取到 probe 时，不应触发放宽候选集的额外枚举（性能）。"""
        plugin = self.make_plugin()
        plugin._downloadhis = _FakeDownloadHis({self.own_dl: "H1"})
        cand = {
            "hash": "H1", "module": mock.MagicMock(), "path": self.show,
            "title": "Show", "downloader": "qb",
        }
        with mock.patch.object(plugin, "_collect_seed_candidates",
                               return_value=[cand]), \
                mock.patch.object(plugin, "_collect_all_seed_candidates",
                                  side_effect=AssertionError("不应被调用")), \
                mock.patch.object(plugin, "_seed_fully_removed",
                                  return_value=True), \
                mock.patch.object(plugin, "_delete_torrent_by_hash",
                                  return_value=True):
            stats = plugin._run_linkage_after_delete([self.own_dl], [], False)

        self.assertEqual(stats["torrent"], 1)


if __name__ == "__main__":
    unittest.main()
