# -*- coding: utf-8 -*-
"""
「保护目录」回归测试（v3.1.0）。

需求（用户 2026-10-29 提出）：
  在「保护目录」中填写的目录不自动删除，以应对该目录位于「保种/清理目录」
  之内、却被自动清理的情况。

保护必须覆盖**所有**清理链路，任何一条漏判都会删掉用户明确要求保留的内容：

  - 种子级：内容位于保护目录内、或内容目录包含保护目录 → 整条种子跳过
  - 仅文件：保护目录内的文件不进入候选；同 inode 的其它硬链接整组跳过
  - 硬链接清理 / 单文件删除：即使索引来自外部注入，也在删除前拦截
  - 空目录收尾 / 空壳目录巡检：保护目录及其内部不参与，含保护目录的父目录
    也不会被当成空壳删掉
"""

import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

import tests  # noqa: F401  触发宿主桩路径注入

from app.schemas.system import DownloaderConf, ServiceInfo
from seedspaceguard import SeedSpaceGuard, GIB


class _ProtectBase(unittest.TestCase):
    """保护目录夹具：真实临时目录 + 直接注入运行状态。"""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="ssg-protect-")
        self.dl = os.path.join(self.base, "download")
        self.lib = os.path.join(self.base, "library")
        os.makedirs(self.dl)
        os.makedirs(self.lib)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)
        SeedSpaceGuard._clean_stats = None

    def make_plugin(self, targets=None, protect=None):
        """构造插件实例并注入目录 / 保护配置。"""
        plugin = SeedSpaceGuard()
        plugin._target_dirs = [os.path.normpath(d) for d in (targets or [self.dl])]
        plugin._active_dirs = [d for d in plugin._target_dirs if os.path.isdir(d)]
        plugin._protect_dirs = [os.path.normpath(d) for d in (protect or [])]
        plugin._volume_path = self.base
        plugin._threshold_gb = 1
        plugin._recent_skip_days = 0
        plugin._protect_pattern = ""
        plugin._delete_torrents = False
        plugin._delete_history = False
        plugin._dry_run = False
        plugin._notify = False
        plugin._empty_dir_sweep = True
        plugin._ino_paths = {}
        plugin._clean_stats = {
            "files": 0, "transfers": 0, "seeds": 0,
            "companions": 0, "orphans": 0, "dirs": 0,
            "stalled": False, "cancelled": False, "dry_run": False,
        }
        return plugin

    @staticmethod
    def make_file(path, content=b"x", mtime_offset=0):
        """创建文件，可指定相对当前时间的 mtime 偏移（秒，负数=更旧）。"""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(content)
        if mtime_offset:
            stamp = time.time() + mtime_offset
            os.utime(path, (stamp, stamp))
        return path

    @staticmethod
    def make_shell(path):
        """构造「只剩 DSM 索引残片」的空壳目录。"""
        os.makedirs(path, exist_ok=True)
        meta = os.path.join(path, "@eaDir", "E01.mkv")
        os.makedirs(meta, exist_ok=True)
        with open(os.path.join(meta, "SYNOINDEX_MEDIA_INFO"), "w",
                  encoding="utf-8") as handle:
            handle.write("meta")
        return path


class TestProtectConfig(_ProtectBase):
    """配置解析与表单契约。"""

    def test_parse_protect_dirs(self):
        """多行配置：去重、跳过注释、拒绝相对路径与根目录。"""
        plugin = SeedSpaceGuard()
        plugin.init_plugin({
            "enabled": True,
            "target_dirs": "/vol/dl",
            "protect_dirs": (
                "/vol/dl/keep\n"
                "# /vol/dl/disabled\n"
                "\n"
                "relative/path\n"
                "/\n"
                "/vol/dl/keep\n"
            ),
        })
        self.assertEqual(plugin._protect_dirs, ["/vol/dl/keep"])

    def test_protect_dir_equal_target_warns_but_kept(self):
        """保护目录与清理目录相同：保留配置（会等于整目录不清理）。"""
        plugin = SeedSpaceGuard()
        plugin.init_plugin({
            "enabled": True,
            "target_dirs": "/vol/dl",
            "protect_dirs": "/vol/dl",
        })
        self.assertEqual(plugin._protect_dirs, ["/vol/dl"])

    def test_default_config_has_empty_protect_dirs(self):
        """新装默认不做目录级保护。"""
        self.assertEqual(SeedSpaceGuard()._default_config()["protect_dirs"], "")

    def test_form_has_protect_dirs_field(self):
        """配置表单必须提供「保护目录」多行输入。"""
        plugin = SeedSpaceGuard()
        form, default = plugin.get_form()
        found = []

        def walk(nodes):
            for node in nodes or []:
                props = node.get("props") or {}
                if props.get("model") == "protect_dirs":
                    found.append(props)
                walk(node.get("content"))

        walk(form)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].get("label"), "保护目录（每行一个）")
        self.assertIn("即使该目录位于", str(found[0].get("hint") or ""))
        self.assertIn("protect_dirs", default)
        self.assertEqual(default["protect_dirs"], "")


class TestProtectPathJudgement(_ProtectBase):
    """路径级判定：自身、子路径、前缀误导、反方向包含。"""

    def test_is_protected(self):
        """保护目录本身与其中内容都受保护，同名前缀不误判。"""
        keep = os.path.join(self.dl, "keep")
        plugin = self.make_plugin(protect=[keep])
        self.assertTrue(plugin._is_protected(keep))
        self.assertTrue(plugin._is_protected(os.path.join(keep, "a.mkv")))
        self.assertFalse(plugin._is_protected(os.path.join(self.dl, "keep2")))
        self.assertFalse(plugin._is_protected(os.path.join(self.dl, "other")))

    def test_contains_protected(self):
        """内容目录包含保护目录时也算受保护（删整目录会连带删掉它）。"""
        keep = os.path.join(self.dl, "show", "keep")
        plugin = self.make_plugin(protect=[keep])
        self.assertTrue(plugin._contains_protected(os.path.join(self.dl, "show")))
        self.assertFalse(plugin._contains_protected(os.path.join(self.dl, "other")))

    def test_is_protected_seed(self):
        """种子内容在保护目录内、或内容目录包含保护目录，都判受保护。"""
        show = os.path.join(self.dl, "show")
        plugin = self.make_plugin(protect=[os.path.join(show, "keep")])
        self.assertTrue(plugin._is_protected_seed(
            {"path": os.path.join(show, "keep", "A")}
        ))
        self.assertTrue(plugin._is_protected_seed({"path": show}))
        self.assertFalse(plugin._is_protected_seed(
            {"path": os.path.join(self.dl, "other")}
        ))
        self.assertFalse(plugin._is_protected_seed({"path": ""}))


class TestProtectFileMode(_ProtectBase):
    """仅文件链路：候选索引、单文件删除、硬链接整组跳过。"""

    def test_protected_dir_not_indexed(self):
        """保护目录内的文件不进入清理候选。"""
        plugin = self.make_plugin(protect=[os.path.join(self.dl, "keep")])
        self.make_file(os.path.join(self.dl, "old.mkv"), mtime_offset=-86400)
        self.make_file(os.path.join(self.dl, "keep", "protected.mkv"),
                       mtime_offset=-86400)
        files, _ino = plugin._index_files([], 0)
        self.assertEqual(
            [os.path.basename(item[1]) for item in files], ["old.mkv"]
        )

    def test_protected_dir_as_target_root_not_indexed(self):
        """保护目录本身就是清理目录（根）时，其中文件同样不纳入候选。"""
        keep = os.path.join(self.dl, "keep")
        plugin = self.make_plugin(targets=[keep], protect=[keep])
        self.make_file(os.path.join(keep, "movie.mkv"), mtime_offset=-86400)
        files, _ino = plugin._index_files([], 0)
        self.assertEqual(files, [])

    def test_protected_dir_skipped_without_inode_index(self):
        """即使 inode 索引不可用，遍历层也必须跳过保护目录内的文件。"""
        keep = os.path.join(self.dl, "keep")
        plugin = self.make_plugin(targets=[keep], protect=[keep])
        self.make_file(os.path.join(keep, "movie.mkv"), mtime_offset=-86400)
        with mock.patch.object(plugin, "_protected_inodes", return_value=set()):
            files, _ino = plugin._index_files([], 0)
        self.assertEqual(files, [])

    def test_hardlink_group_skipped_when_any_link_protected(self):
        """同 inode 有一处落在保护目录 → 整组不进入候选。"""
        keep = os.path.join(self.lib, "keep")
        plugin = self.make_plugin(
            targets=[self.dl], protect=[keep]
        )
        protected = self.make_file(os.path.join(keep, "movie.mkv"))
        os.link(protected, os.path.join(self.dl, "movie.mkv"))
        files, _ino = plugin._index_files([], 0)
        self.assertEqual(files, [], "整组跳过，避免删一侧不释放空间")

    def test_delete_one_refuses_protected_path(self):
        """即使索引被注入保护路径，删除前也必须拦住。"""
        keep = os.path.join(self.dl, "keep")
        plugin = self.make_plugin(protect=[keep])
        target = self.make_file(os.path.join(keep, "movie.mkv"))
        st = os.lstat(target)
        key = (st.st_dev, st.st_ino)
        plugin._ino_paths = {key: [target]}
        self.assertEqual(plugin._delete_one(target, key), 0)
        self.assertTrue(os.path.exists(target))

    def test_clean_by_file_keeps_protected(self):
        """完整仅文件清理：删除普通文件、保留保护目录内的文件。"""
        keep = os.path.join(self.dl, "keep")
        plugin = self.make_plugin(protect=[keep])
        old = self.make_file(os.path.join(self.dl, "old.mkv"), mtime_offset=-86400)
        protected = self.make_file(os.path.join(keep, "protected.mkv"),
                                   mtime_offset=-86400)
        plugin._linkage_enabled = lambda: False
        with mock.patch.object(plugin, "_disk_free_bytes",
                               side_effect=[0, 10 * GIB]), \
            mock.patch.object(plugin, "_wait_for_release",
                              lambda free_before, nominal: nominal):
            deleted, _gb, _lines = plugin._clean_by_file(0, False)
        self.assertEqual(deleted, 1)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(protected), "保护目录内的文件不得删除")


class TestProtectHardlinks(_ProtectBase):
    """种子级硬链接清理：索引不收录保护路径，删除时再兜一层。"""

    def test_build_inode_index_skips_protected(self):
        """保护目录内的路径不进入 inode 索引（索引即待删清单）。"""
        keep = os.path.join(self.lib, "keep")
        plugin = self.make_plugin(targets=[self.dl, self.lib], protect=[keep])
        f_dl = self.make_file(os.path.join(self.dl, "seed", "ep01.mkv"))
        os.makedirs(keep, exist_ok=True)
        f_lib = os.path.join(keep, "ep01.mkv")
        os.link(f_dl, f_lib)
        index = plugin._build_inode_index([os.path.join(self.dl, "seed"), keep])
        paths = [p for plist in index.values() for p in plist]
        self.assertIn(f_dl, paths)
        self.assertNotIn(f_lib, paths)

    def test_clean_hardlinks_refuses_protected(self):
        """外部注入含保护路径的索引：保护路径保留，其它路径正常删除。"""
        keep = os.path.join(self.dl, "keep")
        plugin = self.make_plugin(protect=[keep])
        protected = self.make_file(os.path.join(keep, "movie.mkv"))
        free_file = self.make_file(os.path.join(self.dl, "other.mkv"))
        st = os.lstat(protected)
        key = (st.st_dev, st.st_ino)
        st2 = os.lstat(free_file)
        key2 = (st2.st_dev, st2.st_ino)
        removed = plugin._clean_hardlinks_for(
            {key: [protected], key2: [free_file]}, "Show"
        )
        self.assertEqual(removed, 1)
        self.assertTrue(os.path.exists(protected))
        self.assertFalse(os.path.exists(free_file))

    def test_build_inode_index_skips_nested_protected(self):
        """保护目录嵌在索引根之下时，其内部文件也不得进入待删清单。"""
        keep = os.path.join(self.lib, "keep")
        plugin = self.make_plugin(targets=[self.lib], protect=[keep])
        target = self.make_file(os.path.join(keep, "movie.mkv"))
        index = plugin._build_inode_index([self.lib])
        paths = [p for plist in index.values() for p in plist]
        self.assertNotIn(target, paths)


class TestProtectSeedCandidates(_ProtectBase):
    """种子级候选：保护目录命中的种子整条跳过。"""

    @staticmethod
    def _service(name, instance):
        """构造 V3 下载器服务信息桩。"""
        conf = DownloaderConf(name=name, type="qbittorrent", enabled=True)
        return ServiceInfo(name=name, module=instance, instance=instance,
                           type="qbittorrent", config=conf)

    def _collect(self, plugin):
        """打桩下载器后收集候选。"""
        torrents = [
            {
                "hash": "A" * 40, "name": "Protected.Show", "progress": 1.0,
                "content_path": os.path.join(self.dl, "keep", "Protected.Show"),
                "completion_on": int(time.time()) - 3600, "size": 4096,
            },
            {
                "hash": "B" * 40, "name": "Normal.Show", "progress": 1.0,
                "content_path": os.path.join(self.dl, "normal", "Normal.Show"),
                "completion_on": int(time.time()) - 3600, "size": 4096,
            },
        ]
        instance = mock.MagicMock()
        instance.get_torrents.return_value = torrents
        helper = mock.MagicMock()
        helper.get_services.return_value = {"qb": self._service("qb", instance)}
        with mock.patch("seedspaceguard.DownloaderHelper", return_value=helper):
            return plugin._collect_candidates(scope_only=True), \
                plugin._collect_candidates(scope_only=False)

    def test_protected_seed_excluded_from_both_scopes(self):
        """两种范围（主链路 / 空壳回收）都必须排除保护目录内的种子。"""
        plugin = self.make_plugin(protect=[os.path.join(self.dl, "keep")])
        scoped, all_scope = self._collect(plugin)
        self.assertEqual([c["title"] for c in scoped], ["Normal.Show"])
        self.assertEqual([c["title"] for c in all_scope], ["Normal.Show"])

    def test_seed_with_protected_subdir_excluded(self):
        """内容目录包含保护子目录的种子同样跳过（删种会连带删掉它）。"""
        plugin = self.make_plugin(
            protect=[os.path.join(self.dl, "normal", "Normal.Show", "keep")]
        )
        scoped, _all_scope = self._collect(plugin)
        self.assertEqual([c["title"] for c in scoped], ["Protected.Show"])


class TestProtectEmptyDirs(_ProtectBase):
    """目录收尾与空壳巡检：保护目录永不被删。"""

    def test_prune_stops_at_protected_dir(self):
        """向上收尾遇到保护目录即停，不清理其索引残片也不 rmdir。"""
        keep = os.path.join(self.dl, "show", "keep")
        plugin = self.make_plugin(protect=[keep])
        shell = self.make_shell(keep)
        self.make_file(os.path.join(self.dl, "show", "E01.mkv"))
        os.unlink(os.path.join(self.dl, "show", "E01.mkv"))
        plugin._prune_empty_dirs(os.path.join(self.dl, "show", "deleted.mkv"))
        self.assertTrue(os.path.isdir(os.path.join(self.dl, "show")))
        self.assertTrue(os.path.isdir(shell), "保护目录不得删除")

    def test_prune_returns_for_protected_start(self):
        """起点本身就在保护目录内时，直接在入口返回。"""
        keep = os.path.join(self.dl, "keep")
        plugin = self.make_plugin(protect=[keep])
        os.makedirs(keep)
        plugin._prune_empty_dirs(os.path.join(keep, "deleted.mkv"))
        self.assertTrue(os.path.isdir(keep))

    def test_sweep_keeps_protected_shell_and_parent(self):
        """空壳巡检不清理保护目录，也不清理含保护目录的父目录。"""
        keep = os.path.join(self.dl, "show", "keep")
        plugin = self.make_plugin(protect=[keep])
        protected_shell = self.make_shell(keep)
        parent = os.path.dirname(keep)
        nested_shell = self.make_shell(os.path.join(keep, "nested"))
        normal_shell = self.make_shell(os.path.join(self.dl, "normal"))
        stats = plugin._sweep_empty_dirs(False)
        self.assertEqual(stats["dirs"], 1, "只应清理未受保护的空壳目录")
        self.assertTrue(os.path.isdir(protected_shell))
        self.assertTrue(os.path.isdir(nested_shell))
        self.assertTrue(os.path.isdir(parent))
        self.assertFalse(os.path.exists(normal_shell))


if __name__ == "__main__":
    unittest.main()
