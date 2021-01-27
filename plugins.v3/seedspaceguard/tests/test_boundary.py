# -*- coding: utf-8 -*-
"""
边界 / 安全测试：SeedSpaceGuard 的"不可逆操作"防护。

为什么这个插件需要单独的边界测试
--------------------------------
它是**唯一会真删用户媒体文件的插件**，且删除不可逆。正常路径再绿，
也不能证明"遇到越界路径、文件被替换、符号链接、目录配置畸形"时不会
删错东西。本文件专门攻击这些薄弱环节。

三层安全边界（``_delete_one`` 的顺序即防御深度）
------------------------------------------------
1. **词法校验** —— 路径必须落在配置目录内（挡 ``..``、绝对路径、外部路径）
2. **inode 复核** —— ``lstat`` 确认仍是普通文件且 inode 未变
   （挡住"遍历之后文件被下载器/其它进程重建"的竞态）
3. **realpath 校验** —— 解析符号链接后仍须在配置目录内（挡链接逃逸）

覆盖清单
--------
- 词法越界路径一律不删
- inode 变化（同名新文件）不删
- 普通文件被换成符号链接：不删链接、也**不得删除链接目标**
- 硬链接两侧全删；单侧存在时只删已有侧
- ``_path_under`` 前缀陷阱（``/data2`` 不应被 ``/data`` 认领）
- ``_parse_dirs`` 脏配置（空行、注释、重复、嵌套）
- ``_index_files`` 跳过 symlink 与非普通文件、跳过 DSM 系统目录
- ``_seed_fully_removed`` 记录异常时保守返回 False（不删种）
- ``_delete_torrent_by_hash`` / ``_get_downloader_for`` 异常降级
- ``_purge_syno_index`` 只清残片，不动真实文件与用户数据
"""

import os
import shutil
import stat
import tempfile
import time
import unittest
from unittest import mock

import tests  # noqa: F401  触发宿主桩路径注入

from seedspaceguard import SeedSpaceGuard

GIB = 1024 ** 3


class _Base(unittest.TestCase):
    """边界测试夹具：真实临时目录 + 独立插件实例。"""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="ssg-boundary-")
        self.dl = os.path.join(self.base, "download")
        self.lib = os.path.join(self.base, "library")
        # 配置目录的"外部"，用来验证越界访问确实没发生
        self.outside = os.path.join(self.base, "outside")
        for path in (self.dl, self.lib, self.outside):
            os.makedirs(path)

        self.plugin = SeedSpaceGuard()
        self.plugin._enabled = True
        self.plugin._target_dirs = [self.dl, self.lib]
        self.plugin._active_dirs = [self.dl, self.lib]
        self.plugin._recent_skip_days = 1
        self.plugin._sync_wait_seconds = 0
        self.plugin._notify = False
        self.plugin._dry_run = False
        self.plugin._ino_paths = {}

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    # ------------------------------------------------------------------
    def make_file(self, path, size_kb=64, age_days=10):
        """创建指定名义大小、并回拨 mtime 的稀疏文件。"""
        with open(path, "wb") as handle:
            handle.truncate(size_kb * 1024)
        stamp = time.time() - age_days * 86400
        os.utime(path, (stamp, stamp))
        return path

    def key_of(self, path):
        """取文件的 inode 身份 (st_dev, st_ino)。"""
        st = os.lstat(path)
        return (st.st_dev, st.st_ino)

    def delete_one(self, fpath, key=None):
        """调用 _delete_one，缺省用当前 inode。"""
        if key is None:
            key = self.key_of(fpath)
        return self.plugin._delete_one(fpath, key)


# ======================================================================
# 1. 边界①：词法校验
# ======================================================================
class TestLexicalBoundary(_Base):
    """配置目录之外的路径必须拒删，无论它看起来多"合理"。"""

    def test_outside_path_refused(self):
        """明确落在配置目录外的文件不得被删除。"""
        victim = self.make_file(os.path.join(self.outside, "important.mkv"), 4096, 30)

        removed = self.delete_one(victim)

        self.assertEqual(removed, 0, "越界路径应被拒删")
        self.assertTrue(os.path.exists(victim), "配置目录外的文件被删（严重）")

    def test_traversal_path_refused(self):
        """形如 ``../outside/x`` 的穿越路径不得被删除。"""
        victim = self.make_file(os.path.join(self.outside, "important.mkv"), 4096, 30)
        traversal = os.path.join(self.dl, "..", "outside", "important.mkv")

        removed = self.delete_one(traversal)

        self.assertEqual(removed, 0, "穿越路径应被拒删")
        self.assertTrue(os.path.exists(victim), "穿越路径删到了外部文件（严重）")

    def test_absolute_path_outside_refused(self):
        """绝对路径指向外部时同样拒删。"""
        victim = self.make_file(os.path.join(self.outside, "important.mkv"), 4096, 30)

        removed = self.delete_one(os.path.realpath(victim))

        self.assertEqual(removed, 0)
        self.assertTrue(os.path.exists(victim))

    def test_inside_path_accepted(self):
        """反向校验：配置目录内的路径必须能删（否则上面全是假绿）。"""
        victim = self.make_file(os.path.join(self.dl, "victim.mkv"), 4096, 30)

        removed = self.delete_one(victim)

        self.assertEqual(removed, 1, "配置目录内应正常删除")
        self.assertFalse(os.path.exists(victim))


class TestPathUnderTrap(_Base):
    """
    ``_path_under`` 的前缀比较不得产生"兄弟目录误认"。

    若实现用朴素的 ``startswith``，``/data2/x`` 会被 ``/data`` 认领，
    在配置常以同级目录并列时（``/volume1/data`` 与 ``/volume1/data-old``）
    足以造成越界删除。
    """

    def test_sibling_prefix_not_recognized(self):
        """``/a/data2`` 不属于 ``/a/data``。"""
        self.assertFalse(
            SeedSpaceGuard._path_under("/volume1/data2/x.mkv", "/volume1/data"),
            "前缀相同的兄弟目录不应被认领",
        )

    def test_same_name_prefix_not_recognized(self):
        """``/dl-old`` 不属于 ``/dl``。"""
        self.assertFalse(
            SeedSpaceGuard._path_under("/mnt/dl-old/x.mkv", "/mnt/dl")
        )

    def test_true_descendant_recognized(self):
        """真正的子路径必须被认领。"""
        self.assertTrue(
            SeedSpaceGuard._path_under("/volume1/data/sub/x.mkv", "/volume1/data")
        )

    def test_self_recognized(self):
        """目录自身应被认领（用于空目录清理的边界判断）。"""
        self.assertTrue(SeedSpaceGuard._path_under("/volume1/data", "/volume1/data"))

    def test_trailing_slash_normalized(self):
        """带尾斜杠的配置不应影响判断。"""
        self.assertTrue(
            SeedSpaceGuard._path_under("/volume1/data/x.mkv", "/volume1/data/")
        )

    def test_empty_path_refused(self):
        """空路径不得被认领。"""
        self.assertFalse(SeedSpaceGuard._path_under("", "/volume1/data"))

    def test_backslash_normalized(self):
        """
        Windows 风格反斜杠应被统一为 ``/``。

        这是一致性防护：混用分隔符会让同一目录出现两种写法，
        前缀比较随即失效。
        """
        self.assertTrue(
            SeedSpaceGuard._path_under("\\volume1\\data\\x.mkv", "/volume1/data"),
            "反斜杠路径应被规范化后认领",
        )


# ======================================================================
# 2. 边界②：inode 复核（竞态防护）
# ======================================================================
class TestInodeRecheck(_Base):
    """
    索引建立后、真正删除前，文件可能已被替换。

    真实场景：qb 删掉旧文件又立刻用同名文件重新下载。若不做 inode 复核，
    插件会删掉那个**刚下载的新文件**——用户视角就是"清理插件删了我正在下的东西"。
    """

    def test_recreated_same_name_not_deleted(self):
        """同名文件被重建（inode 已变）时不得删除。"""
        path = self.make_file(os.path.join(self.dl, "movie.mkv"), 4096, 30)
        old_key = self.key_of(path)

        # 模拟"删除 + 重建同名文件"
        os.unlink(path)
        self.make_file(path, 4096, 0)  # 新文件，mtime 是现在
        new_key = self.key_of(path)
        self.assertNotEqual(old_key, new_key, "前置条件：inode 应已变化")

        removed = self.delete_one(path, key=old_key)

        self.assertEqual(removed, 0, "inode 变化应跳过")
        self.assertTrue(os.path.exists(path), "刚重建的新文件被误删（严重）")

    def test_inode_unchanged_deleted(self):
        """反向校验：inode 未变时必须能删。"""
        path = self.make_file(os.path.join(self.dl, "movie.mkv"), 4096, 30)

        removed = self.delete_one(path)

        self.assertEqual(removed, 1)
        self.assertFalse(os.path.exists(path))

    def test_missing_file_counted_as_handled(self):
        """
        文件已不存在时应记为已处理（返回 1）。

        语义解释：外部（下载器）已经完成删除，插件无需再动手，
        但排产时应视为"本项已处置"，否则会反复重试同一条目。
        """
        path = self.make_file(os.path.join(self.dl, "gone.mkv"), 4096, 30)
        key = self.key_of(path)
        os.unlink(path)

        removed = self.delete_one(path, key=key)

        self.assertEqual(removed, 1, "已不存在应记为已处理")

    def test_non_regular_file_not_deleted(self):
        """路径变成目录后不得被删除（防止 rmtree 式误伤）。"""
        path = os.path.join(self.dl, "became-dir")
        os.makedirs(path)
        inner = self.make_file(os.path.join(path, "inner.mkv"), 1024, 30)
        key = self.key_of(os.path.join(path, "inner.mkv"))

        removed = self.delete_one(path, key=key)

        self.assertEqual(removed, 0, "非普通文件应跳过")
        self.assertTrue(os.path.isdir(path), "目录被删（严重）")
        self.assertTrue(os.path.exists(inner), "目录内容被删（严重）")


# ======================================================================
# 3. 边界③：符号链接 / realpath 逃逸
# ======================================================================
class TestSymlinkEscape(_Base):
    """
    符号链接是越界删除最隐蔽的入口。

    ``_index_files`` 用 ``lstat`` + ``S_ISREG`` 过滤 symlink，因此链接本身
    不会进入候选；但**删除前的最后一刻**仍要复核，否则"候选建立后文件被
    换成链接"这一竞态会让 ``os.unlink`` 作用在链接上——若实现改用 ``stat``
    跟随链接，删除甚至会打到目标文件上。
    """

    def test_symlink_target_outside_not_deleted(self):
        """配置目录内的符号链接：链接与目标都不得被删除。"""
        target = self.make_file(os.path.join(self.outside, "real-data.mkv"), 4096, 30)
        link = os.path.join(self.dl, "shortcut.mkv")
        os.symlink(target, link)

        # 传入链接自身的 lstat 身份（模拟"候选建立时它还是普通文件"）
        st = os.lstat(link)
        removed = self.plugin._delete_one(link, (st.st_dev, st.st_ino))

        self.assertTrue(
            os.path.islink(link) or os.path.exists(link),
            "符号链接本身不应被删（非普通文件）",
        )
        self.assertTrue(os.path.exists(target), "链接目标被删（严重）：realpath 边界失效")

    def test_symlink_to_dir_not_traversed(self):
        """指向外部的目录符号链接：其内容不得被遍历删除。"""
        victim = self.make_file(os.path.join(self.outside, "inside-link.mkv"), 4096, 30)
        link = os.path.join(self.dl, "linkdir")
        os.symlink(self.outside, link)

        files, _ino_paths = self.plugin._index_files([], 0)

        indexed = [f[1] for f in files]
        self.assertNotIn(
            os.path.join(link, "inside-link.mkv"), indexed,
            "不应跟随目录符号链接进入外部目录",
        )
        self.assertTrue(os.path.exists(victim))

    def test_index_skips_symlink_entries(self):
        """``_index_files`` 必须以 lstat 过滤，不把 symlink 当普通文件。"""
        target = self.make_file(os.path.join(self.outside, "real.mkv"), 4096, 30)
        link = os.path.join(self.dl, "aliased.mkv")
        os.symlink(target, link)
        real = self.make_file(os.path.join(self.dl, "normal.mkv"), 4096, 30)

        files, _ino_paths = self.plugin._index_files([], 0)
        indexed = [f[1] for f in files]

        self.assertIn(real, indexed, "普通文件应入候选")
        self.assertNotIn(link, indexed, "symlink 不应入候选")

    def test_realpath_inside_accepted(self):
        """反向校验：链接目标仍在配置目录内时应正常删除（防"一律拒删"假绿）。"""
        target = self.make_file(os.path.join(self.lib, "real-target.mkv"), 4096, 30)
        # 直接把 lib 侧的真实文件交给删除流程
        removed = self.delete_one(target)

        self.assertEqual(removed, 1)
        self.assertFalse(os.path.exists(target))


# ======================================================================
# 4. 硬链接身份与双侧删除
# ======================================================================
class TestHardlinkBoundary(_Base):
    """硬链接两侧应一并删除；不完整时不得误伤。"""

    def test_both_sides_deleted(self):
        """同一 inode 的两侧路径必须都删除（否则空间不释放）。"""
        src = self.make_file(os.path.join(self.dl, "movie.mkv"), 4096, 30)
        link = os.path.join(self.lib, "movie.mkv")
        os.link(src, link)

        files, ino_paths = self.plugin._index_files([], 0)
        self.plugin._ino_paths = ino_paths
        key = self.key_of(src)
        self.assertEqual(len(ino_paths.get(key, [])), 2, "应识别出两条路径")

        removed = self.plugin._delete_one(src, key)

        self.assertEqual(removed, 2, "两侧都应删除")
        self.assertFalse(os.path.exists(src))
        self.assertFalse(os.path.exists(link))

    def test_one_side_missing_still_deletes_other(self):
        """一侧已被外部删除时，另一侧仍应删除。"""
        src = self.make_file(os.path.join(self.dl, "movie.mkv"), 4096, 30)
        link = os.path.join(self.lib, "movie.mkv")
        os.link(src, link)

        _files, ino_paths = self.plugin._index_files([], 0)
        self.plugin._ino_paths = ino_paths
        key = self.key_of(src)

        os.unlink(link)  # 外部先删掉媒体库侧

        removed = self.plugin._delete_one(src, key)

        self.assertEqual(removed, 2, "缺失侧记为已处理 + 存在侧真删")
        self.assertFalse(os.path.exists(src))

    def test_size_counted_once_per_inode(self):
        """
        同一 inode 只累计一份大小。

        若两侧各计一次，预计释放量虚高一倍，会让插件误判为"达标"而提前
        停止清理，实际空间仍不足——这是静默的清理失效。
        """
        src = self.make_file(os.path.join(self.dl, "big.mkv"), 4096, 30)
        size = os.path.getsize(src)
        os.link(src, os.path.join(self.lib, "big.mkv"))

        files, _ino_paths = self.plugin._index_files([], 0)
        sizes = [f[2] for f in files if f[1] == src]

        self.assertEqual(len(sizes), 1, "同一 inode 只应有一条候选")
        self.assertEqual(sizes[0], size, "大小应只计一份")

    def test_index_reports_all_paths_for_key(self):
        """索引必须登记全部路径（供双侧删除使用）。"""
        src = self.make_file(os.path.join(self.dl, "multi.mkv"), 4096, 30)
        second = os.path.join(self.lib, "multi.mkv")
        os.link(src, second)

        _files, ino_paths = self.plugin._index_files([], 0)

        self.assertEqual(
            sorted(ino_paths[self.key_of(src)]), sorted([src, second])
        )


# ======================================================================
# 5. 保护期 / 保护后缀 的边界
# ======================================================================
class TestProtectionBoundary(_Base):
    """保护规则在边界值下的行为。"""

    def test_recent_skip_days_zero_disables_protection(self):
        """保护期设为 0 时，刚创建的文件也应进入候选。"""
        fresh = self.make_file(os.path.join(self.dl, "fresh.mkv"), 4096, 0)

        files, _paths = self.plugin._index_files([], 0)
        indexed = [f[1] for f in files]

        self.assertIn(fresh, indexed, "保护期 0 时不应有保护")

    def test_recent_skip_days_large_blocks_everything(self):
        """保护期设得极大时，所有文件都应被保护。"""
        self.make_file(os.path.join(self.dl, "a.mkv"), 4096, 30)

        files, _paths = self.plugin._index_files([], 3650 * 86400)

        self.assertEqual(files, [], "超长保护期内不应有候选")

    def test_protect_pattern_does_not_block_paths(self):
        """保护后缀只匹配文件名，不应因目录名相同而误保护。"""
        # 目录名含 .tmp 但文件名不含 —— 文件应仍可清理
        tmp_dir = os.path.join(self.dl, "release.tmp")
        os.makedirs(tmp_dir)
        media = self.make_file(os.path.join(tmp_dir, "movie.mkv"), 4096, 30)

        files, _paths = self.plugin._index_files(["*.tmp"], 0)
        indexed = [f[1] for f in files]

        self.assertIn(media, indexed, "目录名不应参与保护后缀匹配")

    def test_multiple_patterns_all_effective(self):
        """多个保护后缀必须全部生效（漏一个就会误删下载中文件）。"""
        guarded = [
            self.make_file(os.path.join(self.dl, name), 64, 30)
            for name in ("a.part", "b.!qb", "c.download", "d.aria2", "e.tmp")
        ]
        normal = self.make_file(os.path.join(self.dl, "movie.mkv"), 64, 30)

        files, _paths = self.plugin._index_files(
            ["*.part", "*.!qb", "*.download", "*.aria2", "*.tmp"], 0
        )
        indexed = [f[1] for f in files]

        self.assertIn(normal, indexed)
        for path in guarded:
            self.assertNotIn(path, indexed, f"保护后缀未生效：{os.path.basename(path)}")


class TestSystemDirsSkipped(_Base):
    """DSM 系统目录与回收站不得进入候选。"""

    def test_syno_dirs_skipped(self):
        """``@`` 前缀目录（@eaDir 等）整体跳过。"""
        for name in ("@eaDir", "@tmp", "@SynoFinder", "@Recently-Snapshot"):
            sub = os.path.join(self.dl, name)
            os.makedirs(sub)
            self.make_file(os.path.join(sub, "x.mkv"), 1024, 30)

        files, _paths = self.plugin._index_files([], 0)

        self.assertEqual(files, [], "DSM 系统目录不应被遍历")

    def test_recycle_skipped(self):
        """``#recycle`` 回收站跳过（其内容由 DSM 自行管理）。"""
        sub = os.path.join(self.dl, "#recycle")
        os.makedirs(sub)
        self.make_file(os.path.join(sub, "x.mkv"), 1024, 30)

        files, _paths = self.plugin._index_files([], 0)

        self.assertEqual(files, [], "回收站不应被遍历")

    def test_normal_subdir_included(self):
        """反向校验：普通子目录必须被遍历到。"""
        sub = os.path.join(self.dl, "电影")
        os.makedirs(sub)
        media = self.make_file(os.path.join(sub, "movie.mkv"), 1024, 30)

        files, _paths = self.plugin._index_files([], 0)

        self.assertIn(media, [f[1] for f in files], "普通子目录应被遍历")


# ======================================================================
# 6. 目录配置解析边界
# ======================================================================
class TestParseDirsBoundary(_Base):
    """脏目录配置不得导致越界或重复清理。"""

    def test_blank_and_comment_skipped(self):
        """空行与 ``#`` 注释行应被跳过。"""
        result = SeedSpaceGuard._parse_dirs(
            "/a/b\n\n   \n# 这是注释\n#/c/d\n/e/f"
        )

        self.assertEqual(result, ["/a/b", "/e/f"])

    def test_duplicates_removed(self):
        """重复目录应去重（否则同一文件会被两轮遍历、重复计数）。"""
        result = SeedSpaceGuard._parse_dirs("/a/b\n/a/b\n/a/b/")

        self.assertEqual(result, ["/a/b"])

    def test_nested_removed(self):
        """嵌套目录应保留父目录、丢弃子目录。"""
        result = SeedSpaceGuard._parse_dirs("/a\n/a/b\n/a/b/c")

        self.assertEqual(result, ["/a"], "嵌套子目录应被剔除")

    def test_nested_reverse_order(self):
        """顺序颠倒时同样要剔除子目录。"""
        result = SeedSpaceGuard._parse_dirs("/a/b/c\n/a/b\n/a")

        self.assertEqual(result, ["/a"])

    def test_sibling_not_treated_as_nested(self):
        """同级目录不得被误判为嵌套。"""
        result = SeedSpaceGuard._parse_dirs("/a\n/b")

        self.assertEqual(sorted(result), ["/a", "/b"])

    def test_sibling_prefix_not_nested(self):
        """``/a/b2`` 不是 ``/a/b`` 的子目录，不得被剔除。"""
        result = SeedSpaceGuard._parse_dirs("/a/b\n/a/b2")

        self.assertEqual(sorted(result), ["/a/b", "/a/b2"])

    def test_empty_input(self):
        """空输入应返回空列表。"""
        self.assertEqual(SeedSpaceGuard._parse_dirs(""), [])
        self.assertEqual(SeedSpaceGuard._parse_dirs(None), [])

    def test_relative_paths_normalized(self):
        """相对路径应被规范化（``a/../b`` → ``b``）。"""
        self.assertEqual(SeedSpaceGuard._parse_dirs("a/../b"), ["b"])


# ======================================================================
# 7. 联动清理的保守返回
# ======================================================================
class TestLinkageConservative(_Base):
    """
    联动失败必须"保守失败"：宁可留种子，不可删错。

    删种不可逆（用户会丢种，哪怕文件还能重新下载也要重新做种），
    因此任何判定不了的情况都必须返回"未删完/不删"。
    """

    def setUp(self):
        super().setUp()
        from app.db.downloadhistory_oper import DownloadHistoryOper
        from app.db.transferhistory_oper import TransferHistoryOper

        DownloadHistoryOper.reset()
        TransferHistoryOper.reset()
        self.plugin._downloadhis = DownloadHistoryOper()
        self.plugin._transferhis = TransferHistoryOper()
        self.plugin._delete_torrents = True
        self.plugin._delete_history = True

    def test_no_records_keeps_torrent(self):
        """查不到文件记录时不得删种（无从判定 → 保守保留）。"""
        self.assertFalse(
            self.plugin._seed_fully_removed("HASH-UNKNOWN"),
            "无记录时应保守返回 False（不删种）",
        )

    def test_empty_hash_keeps_torrent(self):
        """空 hash 直接返回 False。"""
        self.assertFalse(self.plugin._seed_fully_removed(""))

    def test_query_exception_keeps_torrent(self):
        """查询抛异常时不得删种。"""
        with mock.patch.object(
            type(self.plugin._downloadhis), "get_files_by_hash",
            side_effect=RuntimeError("db down"),
        ):
            self.assertFalse(
                self.plugin._seed_fully_removed("HASH-ERR"),
                "查询异常时应保守保留种子",
            )

    def test_partial_files_keep_torrent(self):
        """只要还有文件在磁盘上，就不删种。"""
        from app.db.downloadhistory_oper import DownloadHistoryOper

        exists = self.make_file(os.path.join(self.dl, "still-here.mkv"), 1024, 30)
        gone = os.path.join(self.dl, "already-gone.mkv")
        DownloadHistoryOper.add_seed("HASH-PARTIAL", [exists, gone])

        self.assertFalse(
            self.plugin._seed_fully_removed("HASH-PARTIAL"),
            "仍有文件存在时不得删种",
        )

    def test_all_files_gone_allows_torrent(self):
        """反向校验：文件全部消失时才允许删种。"""
        from app.db.downloadhistory_oper import DownloadHistoryOper

        gone = os.path.join(self.dl, "gone1.mkv")
        DownloadHistoryOper.add_seed("HASH-CLEAN", [gone])

        self.assertTrue(
            self.plugin._seed_fully_removed("HASH-CLEAN"),
            "全部文件不存在时应允许删种",
        )

    def test_blank_record_path_is_inconclusive_and_kept(self):
        """
        记录里路径全为空串时属于「无从判定」，必须保留种子。

        历史教训（v1.3.6 实测误删事故）：旧实现把「所有记录路径都失效」
        一律当成「已删空」并删种。而记录路径失效的成因很多——qBittorrent →
        Transmission 做种转移后旧路径消失、下载器被清空重建、目录迁移等，
        此时磁盘上文件可能完好无损。仅凭失效记录就删种，会误删正在保种的
        资源（实测一个 178GB、84 个文件的种子被误删）。

        安全方向必须选对：误删一个完好种子的代价，远大于一个空壳种子
        多留一轮。因此「判不了」一律保留。
        """
        from app.db.downloadhistory_oper import DownloadHistoryOper

        DownloadHistoryOper.add_seed("HASH-BLANK", ["", ""])

        self.assertFalse(
            self.plugin._seed_fully_removed("HASH-BLANK"),
            "空路径记录无从判定，必须保守保留种子",
        )

    def test_blank_record_but_real_path_alive_is_kept(self):
        """记录路径失效、但下载器报告的真实路径仍有文件 → 必须保留种子。

        这是误删事故的直接复现场景：判定不能被失效记录带偏，
        要以下载器当前报告的内容路径做物理复核。
        """
        from app.db.downloadhistory_oper import DownloadHistoryOper

        # 记录里的路径是「已不存在的旧路径」
        DownloadHistoryOper.add_seed("HASH-MOVED", [os.path.join(self.dl, "旧路径", "a.mkv")])
        # 但下载器报告的真实路径下有文件
        real_dir = os.path.join(self.dl, "真实目录")
        os.makedirs(real_dir, exist_ok=True)
        self.make_file(os.path.join(real_dir, "正片.mkv"))

        self.assertFalse(
            self.plugin._seed_fully_removed("HASH-MOVED", {"path": real_dir}),
            "真实路径仍有文件，必须保留种子（不得因记录失效而误删）",
        )

    def test_delete_torrent_empty_hash_refused(self):
        """空 hash 不得下发删种请求。"""
        self.assertFalse(self.plugin._delete_torrent_by_hash(""))

    def test_delete_torrent_no_downloader_refused(self):
        """找不到下载器时应返回 False 而非抛异常。"""
        with mock.patch.object(
            SeedSpaceGuard, "_get_downloader_for", return_value=None
        ):
            self.assertFalse(self.plugin._delete_torrent_by_hash("HASH-X", "标题"))

    def test_delete_torrent_exception_contained(self):
        """下载器抛异常时必须被吞掉并返回 False（不能影响主清理流程）。"""
        class _Bad:
            def remove_torrents(self, **kwargs):
                raise RuntimeError("qb 掉线了")

        with mock.patch.object(
            SeedSpaceGuard, "_get_downloader_for", return_value=_Bad()
        ):
            self.assertFalse(
                self.plugin._delete_torrent_by_hash("HASH-Y"),
                "下载器异常应被降级为 False",
            )

    def test_delete_torrent_passes_delete_file_false(self):
        """删种时必须显式``delete_file=False``（文件已由插件删除）。"""
        recorded = {}

        class _Fake:
            def remove_torrents(self, hashs=None, delete_file=None):
                recorded["hashs"] = hashs
                recorded["delete_file"] = delete_file
                return True

        with mock.patch.object(
            SeedSpaceGuard, "_get_downloader_for", return_value=_Fake()
        ):
            self.assertTrue(self.plugin._delete_torrent_by_hash("HASH-Z"))

        self.assertEqual(recorded["delete_file"], False, "不得让下载器再删一次文件")
        self.assertEqual(recorded["hashs"], ["HASH-Z"])

    def test_linkage_noop_when_all_switches_off(self):
        """开关全关时联动入口应直接返回，不做任何查询。"""
        self.plugin._delete_torrents = False
        self.plugin._delete_history = False
        lines = []

        stats = self.plugin._run_linkage_after_delete(
            [os.path.join(self.dl, "x.mkv")], lines, dry_run=False
        )

        self.assertEqual(stats, {"history": 0, "torrent": 0, "torrent_kept": 0})
        self.assertEqual(lines, [])

    def test_linkage_noop_on_dry_run(self):
        """试运行不得产生任何联动副作用。"""
        lines = []

        stats = self.plugin._run_linkage_after_delete(
            [os.path.join(self.dl, "x.mkv")], lines, dry_run=True
        )

        self.assertEqual(stats, {"history": 0, "torrent": 0, "torrent_kept": 0})
        self.assertEqual(lines, [])


# ======================================================================
# 8. DSM 索引残片清理
# ======================================================================
class TestSynoIndexPurge(_Base):
    """
    ``@eaDir`` 残片清理：只删"确定失去对应真实文件"的索引。

    Synology 不会自动回收索引残片，长期堆积会让父目录无法 rmdir。
    但这里有真正误删用户数据的风险：``@eaDir`` 下也可能有用户东西，
    因此判定必须严格（白名单之外的任何文件都保住整条）。
    """

    def _make_index(self, dir_path, name, files=("SYNOINDEX_MEDIA_INFO",)):
        """在 @eaDir 下造一条索引条目。"""
        entry = os.path.join(dir_path, "@eaDir", name)
        os.makedirs(entry, exist_ok=True)
        for fname in files:
            with open(os.path.join(entry, fname), "w", encoding="utf-8") as fh:
                fh.write("meta")
        return entry

    def test_orphan_index_removed(self):
        """真实文件已不存在 → 索引残片应被清理。"""
        entry = self._make_index(self.dl, "deleted.mkv")

        removed = self.plugin._purge_syno_index(self.dl)

        self.assertEqual(removed, 1)
        self.assertFalse(os.path.exists(entry), "孤立残片应被清理")

    def test_index_for_existing_file_kept(self):
        """真实文件仍存在 → 索引必须保留（否则媒体库元数据丢失）。"""
        self.make_file(os.path.join(self.dl, "alive.mkv"), 1024, 30)
        entry = self._make_index(self.dl, "alive.mkv")

        self.plugin._purge_syno_index(self.dl)

        self.assertTrue(os.path.exists(entry), "真实文件在，索引不得删")

    def test_user_data_in_index_kept(self):
        """索引条目里混入用户文件时，整条必须保留（白名单防护）。"""
        entry = self._make_index(
            self.dl, "deleted.mkv", files=("SYNOINDEX_MEDIA_INFO", "我的笔记.txt")
        )

        self.plugin._purge_syno_index(self.dl)

        self.assertTrue(os.path.exists(entry), "含非元数据文件时不得删除")
        self.assertTrue(os.path.exists(os.path.join(entry, "我的笔记.txt")))

    def test_unknown_entry_skipped(self):
        """白名单之外的文件名不得被当作残片删除。"""
        entry = self._make_index(self.dl, "deleted.mkv", files=("random.bin",))

        removed = self.plugin._purge_syno_index(self.dl)

        self.assertEqual(removed, 0)
        self.assertTrue(os.path.exists(entry), "非白名单条目不得删除")

    def test_no_index_dir_returns_zero(self):
        """没有 @eaDir 时应返回 0，不报错。"""
        self.assertEqual(self.plugin._purge_syno_index(self.dl), 0)

    def test_empty_dir_path_returns_zero(self):
        """空路径应直接返回 0。"""
        self.assertEqual(self.plugin._purge_syno_index(""), 0)

    def test_empty_ea_dir_removed(self):
        """残片清空后，空的 @eaDir 目录本身也应被移除。"""
        self._make_index(self.dl, "deleted.mkv")

        self.plugin._purge_syno_index(self.dl)

        self.assertFalse(
            os.path.exists(os.path.join(self.dl, "@eaDir")),
            "空索引目录应一并删除，否则父目录无法 rmdir",
        )


# ======================================================================
# 9. 空目录清理边界
# ======================================================================
class TestPruneEmptyDirs(_Base):
    """删除文件后的空目录清理：不得越界、不得删除配置目录本身。"""

    def test_prunes_up_to_but_not_config_dir(self):
        """应清理到配置目录为止，配置目录本身必须保留。"""
        nested = os.path.join(self.dl, "剧集", "S01")
        os.makedirs(nested)
        media = self.make_file(os.path.join(nested, "ep1.mkv"), 1024, 30)
        os.unlink(media)

        self.plugin._prune_empty_dirs(media)

        self.assertTrue(os.path.isdir(self.dl), "配置目录本身不得删除")
        self.assertFalse(os.path.exists(nested), "空子目录应被清理")
        self.assertFalse(os.path.exists(os.path.join(self.dl, "剧集")))

    def test_keeps_non_empty_dir(self):
        """目录仍有其它文件时必须保留。"""
        nested = os.path.join(self.dl, "剧集")
        os.makedirs(nested)
        self.make_file(os.path.join(nested, "ep2.mkv"), 1024, 30)

        self.plugin._prune_empty_dirs(os.path.join(nested, "ep1.mkv"))

        self.assertTrue(os.path.isdir(nested), "非空目录不得删除")

    def test_outside_path_untouched(self):
        """不属于任何配置目录的路径不得被清理。"""
        nested = os.path.join(self.outside, "other", "deep")
        os.makedirs(nested)

        self.plugin._prune_empty_dirs(nested)

        self.assertTrue(os.path.isdir(nested), "配置目录外的目录不得删除")


if __name__ == "__main__":
    unittest.main()
