# -*- coding: utf-8 -*-
"""
核心清理逻辑回归测试：硬链接索引、双侧删除、安全边界、释放判定。

这些用例直接驱动插件的真实方法（非等价复刻），夹具在临时目录中构造真实
硬链接、符号链接与文件，覆盖改造引入的关键行为。
"""

import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

import tests  # noqa: F401  触发宿主桩路径注入

from seedspaceguard import SeedSpaceGuard, GIB

PROTECT_PATTERN = "*.part|*.!qb|*.download|*.aria2|*.tmp|*.crdownload"


class _FixtureBase(unittest.TestCase):
    """构造带硬链接的临时目录夹具。"""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="ssg-test-")
        # 下载目录 / 媒体库目录（互为硬链接）/ 独立目录 / 配置外目录
        self.dl = os.path.join(self.base, "download")
        self.lib = os.path.join(self.base, "library")
        self.other = os.path.join(self.base, "other")
        self.outside = os.path.join(self.base, "outside")
        for path in (self.dl, self.lib, self.other, self.outside):
            os.makedirs(path)
        self.plugin = SeedSpaceGuard()
        self.patterns = [p.strip() for p in PROTECT_PATTERN.split("|") if p.strip()]

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    # ------------------------------------------------------------------
    def make_file(self, path, size_kb=64, age_days=10):
        """
        创建指定大小的文件，并把 mtime 回拨指定天数。

        :param path: 文件路径
        :param size_kb: 大小（KB）
        :param age_days: 距今的天数（用于绕过保护期）
        """
        with open(path, "wb") as handle:
            handle.write(b"x" * (size_kb * 1024))
        stamp = time.time() - age_days * 86400
        os.utime(path, (stamp, stamp))
        return path

    def activate(self, dirs):
        """设置生效目录。"""
        self.plugin._target_dirs = [os.path.normpath(d) for d in dirs]
        self.plugin._active_dirs = [d for d in self.plugin._target_dirs if os.path.isdir(d)]


class TestIndexFiles(_FixtureBase):
    """_index_files 的 inode 索引与候选筛选。"""

    def test_hardlink_grouped_as_one_candidate(self):
        """成对硬链接应只产生一个候选，且 size 只计一份。"""
        src = self.make_file(os.path.join(self.dl, "movie.mkv"), 1024, 30)
        os.link(src, os.path.join(self.lib, "movie.mkv"))
        self.activate([self.dl, self.lib])

        files, ino_paths = self.plugin._index_files(self.patterns, 86400)

        self.assertEqual(len(files), 1, "硬链接两处应为 1 个候选")
        _mtime, _path, size, key = files[0]
        self.assertEqual(size, 1024 * 1024, "size 应只计一份")
        self.assertEqual(len(ino_paths[key]), 2, "应记录两侧路径")

    def test_no_size_double_counting(self):
        """同一 inode 不应被重复计入，否则预计释放量虚高。"""
        src = self.make_file(os.path.join(self.dl, "a.mkv"), 2048, 30)
        os.link(src, os.path.join(self.lib, "a.mkv"))
        self.activate([self.dl, self.lib])

        files, _ = self.plugin._index_files(self.patterns, 86400)
        total = sum(item[2] for item in files)

        self.assertEqual(total, 2048 * 1024, "合计应为单份大小")

    def test_symlink_excluded(self):
        """符号链接不应被视为普通文件（否则会误删目标）。"""
        target = self.make_file(os.path.join(self.outside, "real.mkv"), 512, 40)
        os.symlink(target, os.path.join(self.dl, "link.mkv"))
        self.activate([self.dl])

        files, _ = self.plugin._index_files(self.patterns, 86400)
        names = {os.path.basename(item[1]) for item in files}

        self.assertNotIn("link.mkv", names, "符号链接必须被排除")

    def test_only_regular_files_indexed(self):
        """
        只有普通文件应进入索引：命名管道等特殊文件要被 S_ISREG 过滤掉。

        这层过滤独立于「是否跟随链接」：即便 lstat 已正确返回类型，若缺少
        S_ISREG 判断，特殊文件仍会混入候选并触发删除尝试。

        注意：fifo 的 mtime 也必须回拨——否则会先被保护期过滤拦下，
        导致该用例无法真正验证 S_ISREG 这层。
        """
        # 一个命名管道（非普通文件），mtime 回拨以绕过保护期
        fifo = os.path.join(self.dl, "pipe.mkv")
        try:
            os.mkfifo(fifo)
        except (AttributeError, OSError):  # pragma: no cover - 平台不支持
            self.skipTest("当前平台不支持 mkfifo")
        stamp = time.time() - 30 * 86400
        os.utime(fifo, (stamp, stamp))
        # 一个正常的普通文件作为对照
        self.make_file(os.path.join(self.dl, "real.mkv"), 128, 30)
        self.activate([self.dl])

        files, _ = self.plugin._index_files(self.patterns, 86400)
        names = {os.path.basename(item[1]) for item in files}

        self.assertNotIn("pipe.mkv", names, "命名管道不应进入候选")
        self.assertIn("real.mkv", names, "普通文件应进入候选")

    def test_symlink_target_untouched(self):
        """符号链接指向的外部文件不应被纳入索引。"""
        target = self.make_file(os.path.join(self.outside, "real.mkv"), 512, 40)
        os.symlink(target, os.path.join(self.dl, "link.mkv"))
        self.activate([self.dl])

        files, _ = self.plugin._index_files(self.patterns, 86400)
        paths = {item[1] for item in files}

        self.assertNotIn(target, paths)

    def test_protect_pattern_excluded(self):
        """保护后缀的文件不应纳入候选。"""
        self.make_file(os.path.join(self.other, "downloading.part"), 100, 10)
        self.make_file(os.path.join(self.other, "normal.mkv"), 100, 10)
        self.activate([self.other])

        files, _ = self.plugin._index_files(self.patterns, 86400)
        names = {os.path.basename(item[1]) for item in files}

        self.assertNotIn("downloading.part", names)
        self.assertIn("normal.mkv", names)

    def test_recent_file_excluded(self):
        """保护期内（mtime 很新）的文件不应纳入候选。"""
        self.make_file(os.path.join(self.other, "fresh.mkv"), 100, 0.01)
        self.activate([self.other])

        files, _ = self.plugin._index_files(self.patterns, 86400)
        names = {os.path.basename(item[1]) for item in files}

        self.assertNotIn("fresh.mkv", names)

    def test_recent_exclusion_covers_all_hardlinks(self):
        """
        保护期内文件的全部硬链接都不应成为候选。

        注意：索引会先登记 inode→路径映射（不受保护期影响），再过滤 mtime，
        因此 ino_paths 里可能存在已登记但未成为候选的条目。这是刻意设计：
        先登记才能完整识别双侧，避免「一侧新、一侧旧」导致去重失效。
        关键保证是**候选为空**，即不会删到保护期内的文件。
        """
        src = self.make_file(os.path.join(self.dl, "fresh.mkv"), 100, 0.01)
        os.link(src, os.path.join(self.lib, "fresh.mkv"))
        self.activate([self.dl, self.lib])

        files, _ino_paths = self.plugin._index_files(self.patterns, 86400)

        self.assertEqual(files, [], "保护期内不应产生任何候选")

    def test_recent_file_never_deleted_via_index(self):
        """保护期内文件即使被登记，也不应出现在候选并可被删除。"""
        src = self.make_file(os.path.join(self.dl, "fresh.mkv"), 100, 0.01)
        os.link(src, os.path.join(self.lib, "fresh.mkv"))
        self.activate([self.dl, self.lib])

        files, ino_paths = self.plugin._index_files(self.patterns, 86400)
        self.plugin._ino_paths = ino_paths
        candidate_keys = {item[3] for item in files}

        self.assertEqual(candidate_keys, set(), "保护期内不应有候选 inode")

    def test_eadir_excluded(self):
        """@eaDir 下的 DSM 索引残片不应纳入候选。"""
        ea = os.path.join(self.other, "@eaDir", "sub")
        os.makedirs(ea)
        self.make_file(os.path.join(ea, "SYNOINDEX_MEDIA_INFO"), 1, 50)
        self.activate([self.other])

        files, _ = self.plugin._index_files(self.patterns, 86400)

        self.assertEqual(files, [], "@eaDir 内容必须被排除")

    def test_recycle_excluded(self):
        """#recycle 回收站不应纳入候选。"""
        recycle = os.path.join(self.other, "#recycle")
        os.makedirs(recycle)
        self.make_file(os.path.join(recycle, "old.mkv"), 100, 50)
        self.activate([self.other])

        files, _ = self.plugin._index_files(self.patterns, 86400)

        self.assertEqual(files, [], "回收站必须被排除")

    def test_sorted_by_mtime_ascending(self):
        """候选应按 mtime 升序（最旧的优先删除）。"""
        self.make_file(os.path.join(self.other, "new.mkv"), 100, 5)
        self.make_file(os.path.join(self.other, "old.mkv"), 100, 50)
        self.make_file(os.path.join(self.other, "mid.mkv"), 100, 20)
        self.activate([self.other])

        files, _ = self.plugin._index_files(self.patterns, 86400)
        names = [os.path.basename(item[1]) for item in files]

        self.assertEqual(names, ["old.mkv", "mid.mkv", "new.mkv"])

    def test_multiple_dirs_merged(self):
        """多个目录的候选应合并到一次索引中。"""
        self.make_file(os.path.join(self.dl, "a.mkv"), 100, 10)
        self.make_file(os.path.join(self.other, "b.mkv"), 100, 20)
        self.activate([self.dl, self.other])

        files, _ = self.plugin._index_files(self.patterns, 86400)
        names = {os.path.basename(item[1]) for item in files}

        self.assertEqual(names, {"a.mkv", "b.mkv"})

    def test_three_way_hardlink(self):
        """三处硬链接应记录全部三处路径、size 仍只计一份。"""
        third = os.path.join(self.other, "third")
        os.makedirs(third)
        src = self.make_file(os.path.join(self.dl, "tri.mkv"), 512, 30)
        os.link(src, os.path.join(self.lib, "tri.mkv"))
        os.link(src, os.path.join(third, "tri.mkv"))
        self.activate([self.dl, self.lib, third])

        files, ino_paths = self.plugin._index_files(self.patterns, 86400)

        self.assertEqual(len(files), 1)
        self.assertEqual(sum(item[2] for item in files), 512 * 1024)
        self.assertEqual(len(ino_paths[files[0][3]]), 3)


class TestDeleteOne(_FixtureBase):
    """_delete_one 的双侧删除与安全边界。"""

    def test_deletes_both_hardlink_sides(self):
        """删除应同时移除硬链接的两侧。"""
        src = self.make_file(os.path.join(self.dl, "movie.mkv"), 512, 30)
        os.link(src, os.path.join(self.lib, "movie.mkv"))
        self.activate([self.dl, self.lib])
        _files, ino_paths = self.plugin._index_files(self.patterns, 86400)
        self.plugin._ino_paths = ino_paths
        key = list(ino_paths)[0]

        removed = self.plugin._delete_one(os.path.join(self.dl, "movie.mkv"), key)

        self.assertEqual(removed, 2, "应删除两处")
        self.assertFalse(os.path.exists(os.path.join(self.dl, "movie.mkv")))
        self.assertFalse(os.path.exists(os.path.join(self.lib, "movie.mkv")))

    def test_single_link_deleted(self):
        """无硬链接的普通文件应正常删除。"""
        self.make_file(os.path.join(self.other, "single.mkv"), 128, 30)
        self.activate([self.other])
        _files, ino_paths = self.plugin._index_files(self.patterns, 86400)
        self.plugin._ino_paths = ino_paths
        key = list(ino_paths)[0]

        removed = self.plugin._delete_one(os.path.join(self.other, "single.mkv"), key)

        self.assertEqual(removed, 1)
        self.assertFalse(os.path.exists(os.path.join(self.other, "single.mkv")))

    def test_outside_path_never_deleted(self):
        """配置目录外的路径绝不应被删除。"""
        outside_file = self.make_file(os.path.join(self.outside, "keep.mkv"), 128, 30)
        # 人为把外部路径塞进索引，模拟索引被污染
        key = (os.stat(outside_file).st_dev, os.stat(outside_file).st_ino)
        self.plugin._ino_paths = {key: [outside_file]}
        self.activate([self.other])

        removed = self.plugin._delete_one(outside_file, key)

        self.assertEqual(removed, 0, "配置外文件不应被删除")
        self.assertTrue(os.path.exists(outside_file), "配置外文件必须保留")

    def test_outside_path_blocked_by_guards(self):
        """
        配置目录外的普通文件应被安全边界拦住。

        说明：对「普通文件、非符号链接」的路径，边界①（词法校验）与
        边界③（realpath 校验）的作用重叠——此时 ``realpath(path) == path``。
        本用例验证的是**整体行为**（外部文件不被删），不针对单层；
        边界③ 的独立价值由 ``test_realpath_guard_blocks_symlinked_parent``
        覆盖（那种场景下词法校验看不出问题）。
        """
        outside_file = self.make_file(os.path.join(self.outside, "victim.mkv"), 256, 30)
        real_key = (os.stat(outside_file).st_dev, os.stat(outside_file).st_ino)
        self.plugin._ino_paths = {real_key: [outside_file]}
        self.activate([self.other])

        removed = self.plugin._delete_one(outside_file, real_key)

        self.assertEqual(removed, 0, "配置外文件不应被删除")
        self.assertTrue(os.path.exists(outside_file), "配置外文件必须保留")

    def test_lexical_guard_blocks_sibling_prefix_dir(self):
        """与配置目录同前缀但非其子目录的路径应被拦截。"""
        sibling = os.path.join(self.base, "other-sibling")
        os.makedirs(sibling)
        victim = self.make_file(os.path.join(sibling, "v.mkv"), 256, 30)
        key = (os.stat(victim).st_dev, os.stat(victim).st_ino)
        self.plugin._ino_paths = {key: [victim]}
        self.activate([self.other])

        removed = self.plugin._delete_one(victim, key)

        self.assertEqual(removed, 0)
        self.assertTrue(os.path.exists(victim))

    def test_realpath_guard_blocks_symlinked_parent(self):
        """
        realpath 校验应拦住「配置目录内、但解析后指向外部」的路径。

        symlink 目录不会被 os.walk 跟随，但若索引被污染，realpath 这层必须
        能独立兜住。
        """
        # 在配置目录内建一个指向外部的 symlink 目录
        link_dir = os.path.join(self.other, "escape")
        os.symlink(self.outside, link_dir)
        victim = self.make_file(os.path.join(self.outside, "target.mkv"), 256, 30)
        # 构造一条「词法上在配置目录内」的路径：/other/escape/target.mkv
        disguised = os.path.join(link_dir, "target.mkv")
        key = (os.stat(victim).st_dev, os.stat(victim).st_ino)
        self.plugin._ino_paths = {key: [disguised]}
        self.activate([self.other])

        removed = self.plugin._delete_one(disguised, key)

        self.assertEqual(removed, 0, "realpath 越界应被拦截")
        self.assertTrue(os.path.exists(victim), "真实目标文件必须保留")

    def test_inode_changed_skipped(self):
        """inode 已变化的文件应被跳过（防止误删重建的同名文件）。"""
        path = self.make_file(os.path.join(self.other, "recreated.mkv"), 128, 30)
        stale_key = (os.stat(path).st_dev, os.stat(path).st_ino)
        # 删除后重建同名文件，inode 通常不同
        os.remove(path)
        self.make_file(os.path.join(self.other, "recreated.mkv"), 999, 30)
        new_key = (os.stat(path).st_dev, os.stat(path).st_ino)
        if stale_key == new_key:  # pragma: no cover - 文件系统复用了 inode
            self.skipTest("文件系统复用了同一 inode，无法验证该分支")
        self.plugin._ino_paths = {stale_key: [path]}
        self.activate([self.other])

        removed = self.plugin._delete_one(path, stale_key)

        self.assertEqual(removed, 0, "inode 不匹配应跳过")
        self.assertTrue(os.path.exists(path), "新文件必须保留")

    def test_missing_file_counts_as_removed(self):
        """文件已不存在时应视为已处理（幂等）。"""
        path = os.path.join(self.other, "gone.mkv")
        key = (1, 1)
        self.plugin._ino_paths = {key: [path]}
        self.activate([self.other])

        removed = self.plugin._delete_one(path, key)

        self.assertEqual(removed, 1)

    def test_symlink_not_deleted(self):
        """符号链接不应被当作普通文件删除。"""
        target = self.make_file(os.path.join(self.outside, "real.mkv"), 128, 30)
        link = os.path.join(self.dl, "link.mkv")
        os.symlink(target, link)
        key = (os.lstat(link).st_dev, os.lstat(link).st_ino)
        self.plugin._ino_paths = {key: [link]}
        self.activate([self.dl])

        removed = self.plugin._delete_one(link, key)

        self.assertEqual(removed, 0, "符号链接不应被删除")
        self.assertTrue(os.path.lexists(link), "符号链接应保留")
        self.assertTrue(os.path.exists(target), "目标文件应保留")

    def test_partial_hardlink_set_still_deletes_visible(self):
        """只配置了一侧时，仍应删除配置内的那一侧（另一侧留给用户处理）。"""
        src = self.make_file(os.path.join(self.dl, "half.mkv"), 256, 30)
        os.link(src, os.path.join(self.lib, "half.mkv"))
        self.activate([self.dl])  # 只配置下载目录
        _files, ino_paths = self.plugin._index_files(self.patterns, 86400)
        self.plugin._ino_paths = ino_paths
        key = list(ino_paths)[0]

        removed = self.plugin._delete_one(os.path.join(self.dl, "half.mkv"), key)

        self.assertEqual(removed, 1)
        self.assertFalse(os.path.exists(os.path.join(self.dl, "half.mkv")))
        self.assertTrue(
            os.path.exists(os.path.join(self.lib, "half.mkv")),
            "配置外的一侧不应被删除",
        )


class TestReleaseHealth(unittest.TestCase):
    """_release_is_healthy 释放判定。"""

    def setUp(self):
        self.plugin = SeedSpaceGuard()

    def test_full_release_healthy(self):
        """接近名义量的释放应判定为正常。"""
        self.assertTrue(self.plugin._release_is_healthy(2 * GIB, 2 * GIB))

    def test_at_tolerance_boundary(self):
        """恰好达到容差下限应判定为正常。"""
        nominal = 2 * GIB
        self.assertTrue(self.plugin._release_is_healthy(int(nominal * 0.9), nominal))

    def test_partial_release_unhealthy(self):
        """释放不足容差应判定为异常。"""
        self.assertFalse(self.plugin._release_is_healthy(int(2 * GIB * 0.5), 2 * GIB))

    def test_no_release_unhealthy(self):
        """完全未释放应判定为异常。"""
        self.assertFalse(self.plugin._release_is_healthy(0, 2 * GIB))

    def test_space_shrunk_unhealthy(self):
        """空间反而减少应判定为异常。"""
        self.assertFalse(self.plugin._release_is_healthy(-GIB, 2 * GIB))

    def test_small_nominal_always_healthy(self):
        """名义量低于阈值时应跳过硬判定，避免测量噪声误伤。"""
        self.assertTrue(self.plugin._release_is_healthy(0, GIB // 2))


class TestWaitForRelease(unittest.TestCase):
    """_wait_for_release 轮询等待。"""

    def test_zero_wait_returns_immediately(self):
        """等待秒数为 0 时应立即返回，不阻塞。"""
        plugin = SeedSpaceGuard()
        plugin._sync_wait_seconds = 0
        plugin._volume_path = tempfile.gettempdir()

        started = time.monotonic()
        result = plugin._wait_for_release(0, 1024)
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 1.0, "不应等待")
        self.assertIsInstance(result, int)

    def test_returns_int_delta(self):
        """返回值应为整数（字节差值）。"""
        plugin = SeedSpaceGuard()
        plugin._sync_wait_seconds = 0
        plugin._volume_path = tempfile.gettempdir()

        baseline = plugin._disk_free_bytes()
        result = plugin._wait_for_release(baseline, 0)

        self.assertIsInstance(result, int)


class TestOwnerDirOf(unittest.TestCase):
    """_owner_dir_of 目录归属。"""

    def setUp(self):
        self.plugin = SeedSpaceGuard()
        self.plugin._target_dirs = ["/vol/a", "/vol/b"]
        self.plugin._active_dirs = ["/vol/a", "/vol/b"]

    def test_finds_owner(self):
        """应找到路径所属的配置目录。"""
        self.assertEqual(self.plugin._owner_dir_of("/vol/a/x/y.mkv"), "/vol/a")
        self.assertEqual(self.plugin._owner_dir_of("/vol/b/y.mkv"), "/vol/b")

    def test_none_when_outside(self):
        """不属于任何目录时返回 None。"""
        self.assertIsNone(self.plugin._owner_dir_of("/vol/c/y.mkv"))

    def test_longest_match_wins(self):
        """存在嵌套时应取最长匹配。"""
        self.plugin._target_dirs = ["/vol/a", "/vol/a/b"]
        self.plugin._active_dirs = ["/vol/a", "/vol/a/b"]
        self.assertEqual(self.plugin._owner_dir_of("/vol/a/b/c.mkv"), "/vol/a/b")


class TestPruneEmptyDirs(_FixtureBase):
    """_prune_empty_dirs 空目录清理与边界保护。"""

    def test_prunes_empty_parents(self):
        """删除文件后应清理遗留的空目录。"""
        nested = os.path.join(self.other, "x", "y")
        os.makedirs(nested)
        path = self.make_file(os.path.join(nested, "a.mkv"), 64, 10)
        self.activate([self.other])

        os.remove(path)
        self.plugin._prune_empty_dirs(path)

        self.assertFalse(os.path.exists(nested), "空目录应被清理")
        self.assertTrue(os.path.isdir(self.other), "配置目录本身不应被删除")

    def test_keeps_non_empty_parents(self):
        """非空目录不应被清理。"""
        nested = os.path.join(self.other, "x")
        os.makedirs(nested)
        path = self.make_file(os.path.join(nested, "a.mkv"), 64, 10)
        self.make_file(os.path.join(nested, "b.mkv"), 64, 10)
        self.activate([self.other])

        os.remove(path)
        self.plugin._prune_empty_dirs(path)

        self.assertTrue(os.path.exists(nested), "非空目录必须保留")

    def test_outside_path_ignored(self):
        """配置目录外的路径不应触发任何清理。"""
        nested = os.path.join(self.outside, "x")
        os.makedirs(nested)
        self.activate([self.other])

        self.plugin._prune_empty_dirs(os.path.join(nested, "a.mkv"))

        self.assertTrue(os.path.exists(nested), "配置外目录不应被动")


class TestHardlinkNoticeWording(_FixtureBase):
    """
    硬链接提示文案：必须说明「路径条数」而非「硬链接处数」。

    背景：用户看到「含 2 处硬链接」时无法判断是"共 2 个文件"还是"3 个文件
    其中 2 个是硬链接"。根因是文案用路径计数（``len(ino_paths[(dev, ino)])``）
    却措辞成"处硬链接"——而术语上"硬链接数"通常指额外引用数。

    因此这里锁定两点：
    1. 必须出现「条路径」字样，且不得再出现「处硬链接」
    2. 路径条数必须真实反映同一 inode 在配置目录内的路径总数
    """

    def _clean(self, dry_run, dirs, free_seq):
        """驱动 ``_clean_by_file``，返回结果消息。"""
        self.activate(dirs)
        self.plugin._threshold_gb = 5000
        self.plugin._mode = "file"
        self.plugin._sync_wait_seconds = 0
        self.plugin._recent_skip_days = 1
        self.plugin._dry_run = False
        self.plugin._volume_path = self.base

        with mock.patch.object(
            SeedSpaceGuard, "_disk_free_bytes", side_effect=free_seq
        ):
            return self.plugin._clean_by_file(1 * GIB, dry_run=dry_run)

    def make_big_file(self, path, size_gb=1.0, age_days=30):
        """构造 GB 级稀疏文件（占位不占盘），用于让提示里的占用显示为非零。"""
        with open(path, "wb") as handle:
            handle.truncate(int(size_gb * GIB))
        stamp = time.time() - age_days * 86400
        os.utime(path, (stamp, stamp))
        return path

    # ------------------------------------------------------------------
    def test_dry_run_wording_two_paths(self):
        """试运行：两条路径时应提示「2 条路径」并给出占用空间。"""
        src = self.make_big_file(os.path.join(self.dl, "movie.mkv"), 1.0)
        os.link(src, os.path.join(self.lib, "movie.mkv"))
        self.plugin._protect_pattern = ""

        _count, _released, details = self._clean(
            True, [self.dl, self.lib], [1 * GIB]
        )

        line = details[0]
        self.assertIn("2 条路径", line, f"应说明路径条数，实际：{line}")
        self.assertNotIn("处硬链接", line, f"旧措辞「处硬链接」必须消失：{line}")
        # 同一 inode 只占一份空间，提示里给出的占用不应翻倍
        self.assertIn("共占 1.0GB", line, f"应给出实际占用（不翻倍），实际：{line}")

    def test_real_delete_wording_other_paths(self):
        """真实删除：应提示「连同其余 N 条路径一并删除」。"""
        src = self.make_big_file(os.path.join(self.dl, "movie.mkv"), 1.0)
        os.link(src, os.path.join(self.lib, "movie.mkv"))
        self.plugin._protect_pattern = ""

        _count, _released, details = self._clean(
            False, [self.dl, self.lib], [1 * GIB, 10 * GIB, 10 * GIB]
        )

        line = details[0]
        self.assertIn("已删除文件", line)
        expect = "连同其余 1 条路径一并删除"
        self.assertIn(expect, line, f"应说明其余路径条数，实际：{line}")
        self.assertNotIn("处硬链接", line, f"旧措辞「处硬链接」必须消失：{line}")
        self.assertFalse(os.path.exists(src), "硬链接两侧都应被删除")

    def test_single_path_has_no_notice(self):
        """仅一条路径时不应附加任何硬链接提示（避免噪音）。"""
        self.make_big_file(os.path.join(self.dl, "solo.mkv"), 1.0)
        self.plugin._protect_pattern = ""

        _count, _released, details = self._clean(True, [self.dl], [1 * GIB])

        line = details[0]
        self.assertNotIn("条路径", line, f"单路径不应提示，实际：{line}")
        self.assertNotIn("硬链接", line, f"单路径不应提示，实际：{line}")

    def test_three_paths_counted_correctly(self):
        """三条路径（三目录）时应提示「3 条路径」。"""
        src = self.make_big_file(os.path.join(self.dl, "tri.mkv"), 2.0)
        os.link(src, os.path.join(self.lib, "tri.mkv"))
        os.link(src, os.path.join(self.other, "tri.mkv"))
        self.plugin._protect_pattern = ""

        _count, _released, details = self._clean(
            True, [self.dl, self.lib, self.other], [1 * GIB]
        )

        line = details[0]
        self.assertIn("3 条路径", line, f"应说明 3 条路径，实际：{line}")
        self.assertIn("共占 2.0GB", line, f"占用仍为单份，实际：{line}")

    def test_no_legacy_wording_anywhere(self):
        """全量扫描：整个试运行清单都不得再出现旧措辞。"""
        src = self.make_big_file(os.path.join(self.dl, "a.mkv"), 1.0)
        os.link(src, os.path.join(self.lib, "a.mkv"))
        self.make_big_file(os.path.join(self.dl, "b.mkv"), 1.0)
        self.plugin._protect_pattern = ""

        _count, _released, details = self._clean(
            True, [self.dl, self.lib], [1 * GIB]
        )

        joined = "\n".join(details)
        msg = f"清单中残留旧措辞：\n{joined}"
        self.assertNotIn("处硬链接", joined, msg)


if __name__ == "__main__":
    unittest.main()
