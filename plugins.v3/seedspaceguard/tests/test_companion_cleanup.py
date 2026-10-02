# -*- coding: utf-8 -*-
"""
种子级「连带清理」测试：硬链接补齐、辅种连带、合集保护、dry_run 预告。

覆盖 v3.0.2 引入的能力——种子级模式删除种子后：
  ① 连带删除媒体库侧同 inode 硬链接
  ② 连带摘除同内容（同路径 + 同种子名）的所有辅种
  ③ **不得**误伤「同目录不同种子名」的合集多集
  ④ 监控目录外的同名种子不受牵连
  ⑤ 索引必须在删种前建立（顺序正确性）

这些用例直接驱动插件真实方法（非等价复刻），夹具在临时目录中构造真实硬链接。
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
from seedspaceguard import SeedSpaceGuard, GIB


class _SeedFixtureBase(unittest.TestCase):
    """种子级夹具：下载目录 + 媒体库目录（互为硬链接）。"""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="ssg-companion-")
        self.dl = os.path.join(self.base, "download")
        self.lib = os.path.join(self.base, "library")
        self.outside = os.path.join(self.base, "outside")
        for path in (self.dl, self.lib, self.outside):
            os.makedirs(path)
        self.plugin = SeedSpaceGuard()
        self.plugin._volume_path = self.base

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    # ------------------------------------------------------------------
    def make_file(self, path, size_kb=1024, age_days=10):
        """创建稀疏文件并回拨 mtime（绕过保护期）。"""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.truncate(size_kb * 1024)
        stamp = time.time() - age_days * 86400
        os.utime(path, (stamp, stamp))
        return path

    def make_hardlink(self, src, dst):
        """在 dst 处创建指向 src 的硬链接（同 inode）。"""
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        os.link(src, dst)
        return dst

    def configure(self, dirs, threshold=10, mode="seed", sync_wait=0,
                  companion=True, delete_torrents=True):
        """配置插件内部状态（绕过 init_plugin）。"""
        self.plugin._enabled = True
        self.plugin._target_dirs = [os.path.normpath(d) for d in dirs]
        self.plugin._active_dirs = [
            d for d in self.plugin._target_dirs if os.path.isdir(d)
        ]
        self.plugin._threshold_gb = threshold
        self.plugin._mode = mode
        self.plugin._sync_wait_seconds = sync_wait
        self.plugin._recent_skip_days = 1
        self.plugin._notify = False
        self.plugin._dry_run = False
        self.plugin._companion_cleanup = companion
        self.plugin._delete_torrents = delete_torrents
        self.plugin._downloaders = []
        # _clean_stats 是类级可变属性，实例间共享。不显式重置会让上一个
        # 用例的计数（如 companions）串到本用例，造成随机失败。
        self.plugin._clean_stats = {
            "files": 0, "transfers": 0, "seeds": 0, "companions": 0,
            "stalled": False, "dry_run": False,
        }

    def patch_free(self, values):
        """注入磁盘剩余空间返回值序列（字节）。"""
        seq = list(values)

        def fake_free():
            return seq.pop(0) if len(seq) > 1 else seq[0]

        return mock.patch.object(
            SeedSpaceGuard, "_disk_free_bytes", side_effect=lambda: fake_free()
        )


def _qb_torrent(content_path, hash_str, name, size_gb=4, age_days=30):
    """构造单个 qBittorrent 种子条目（已完成的 dict 形态）。"""
    return {
        "progress": 1.0,
        "content_path": content_path,
        "hash": hash_str,
        "name": name,
        "completion_on": int(time.time()) - age_days * 86400,
        "size": int(size_gb * 1024 ** 3),
    }


# 阈值 10GB、剩余 1GB → 缺口 9GB。
# 每个种子名义 4GB，但 _clean_by_seed 的预选是按「累计达到缺口即止」，
# 故 4GB 的种子会被选到第 3 个。为了让「只删 1 个」可控，用大种子：
# 单个 20GB > 9GB 缺口 → 预选仅命中 1 个。这样辅种是否被删，
# 完全取决于辅种连带逻辑而非主链路的缺口补齐。
_BIG_SEED_GB = 20


def _install_downloader(torrents, removed_hook=None):
    """注册一个可控的 qBittorrent 下载器桩。

    :param torrents: get_torrents() 返回的种子列表
    :param removed_hook: 接收 (hashs, delete_file) 的回调，用于断言删种行为
    """

    class _Server:
        def get_torrents(self, *args, **kwargs):
            return torrents

        def remove_torrents(self, hashs=None, delete_file=False,
                            downloader=None, **kwargs):
            if removed_hook:
                # 主链路传的是单个字符串 hash，辅种链路传的是 [hash] 列表，
                # 统一归一化为列表，避免 extend 把字符串拆成字符
                if isinstance(hashs, str):
                    normalized = [hashs]
                else:
                    normalized = list(hashs or [])
                removed_hook(normalized, delete_file)
            return True

    ModuleManager.reset()
    ModuleManager.register_downloader(
        DownloaderType.Qbittorrent, "qb", _Server()
    )


class TestCompanionCleanup(_SeedFixtureBase):
    """种子级连带清理：硬链接 + 辅种。"""

    def test_hardlinks_removed_after_seed_deleted(self):
        """删种后必须连带删除媒体库侧的同 inode 硬链接。"""
        # 下载侧 + 媒体库侧互为硬链接
        f1 = self.make_file(os.path.join(self.dl, "Show", "Show.S01E01.mkv"))
        l1 = self.make_hardlink(f1, os.path.join(self.lib, "Show", "Show.S01E01.mkv"))
        self.assertTrue(os.path.exists(l1))

        seed_path = os.path.join(self.dl, "Show")
        _install_downloader([_qb_torrent(seed_path, "hash_main", "Show")])
        self.configure([self.dl, self.lib], threshold=10)

        with self.patch_free([1 * GIB]):
            self.plugin._clean_by_seed(1 * GIB, dry_run=False)

        self.assertFalse(
            os.path.exists(l1),
            "媒体库侧硬链接应被连带删除（否则空间不释放）",
        )

    def test_companion_seeds_removed(self):
        """同内容（同路径 + 同种子名）的其它 hash 应被连带摘除。"""
        f1 = self.make_file(os.path.join(self.dl, "Show", "Show.S01E01.mkv"))
        self.make_hardlink(f1, os.path.join(self.lib, "Show", "Show.S01E01.mkv"))

        seed_path = os.path.join(self.dl, "Show")
        main_deletes, companion_deletes = [], []

        def hook(hashs, delete_file):
            for h in hashs:
                (main_deletes if delete_file else companion_deletes).append(h)

        # 同一内容：两条种子（主 + 辅种），路径与种子名都相同。
        # 用大种子使主链路只选中 1 个，辅种是否被删完全取决于连带逻辑。
        _install_downloader(
            [
                _qb_torrent(seed_path, "hash_a", "Show",
                            size_gb=_BIG_SEED_GB, age_days=40),
                _qb_torrent(seed_path, "hash_b", "Show",
                            size_gb=_BIG_SEED_GB, age_days=30),
            ],
            removed_hook=hook,
        )
        self.configure([self.dl, self.lib], threshold=10)

        with self.patch_free([1 * GIB, 100 * GIB]):
            self.plugin._clean_by_seed(1 * GIB, dry_run=False)

        self.assertEqual(main_deletes, ["hash_a"], "主链路应只删最旧的那条")
        self.assertIn(
            "hash_b", companion_deletes,
            "辅种应走「连带摘除」链路删除（delete_file=False，文件已不存在）",
        )
        self.assertEqual(self.plugin._clean_stats.get("companions"), 1,
                         "辅种数应计入 companions 统计")

    def test_multi_episode_torrents_not_treated_as_companions(self):
        """合集多集（同目录不同种子名）绝不能被当作辅种连带删除。

        实测生产环境有 121 组这类目录（如一部剧 33 集各自成种共用一个目录），
        仅凭路径判定会把它们全部误删——这是本次改造最高危的点。

        断言方式：合集的多条种子**可以被主链路按缺口正常删掉**（那是正当清理），
        但**不得**被「辅种连带」逻辑删除。故检查 companions 计数恒为 0，
        且每次删种都必须是主链路发起（delete_file=True）。
        """
        d = os.path.join(self.dl, "Collection")
        f1 = self.make_file(os.path.join(d, "E01.mkv"))
        f2 = self.make_file(os.path.join(d, "E02.mkv"))
        self.make_hardlink(f1, os.path.join(self.lib, "Collection", "E01.mkv"))
        self.make_hardlink(f2, os.path.join(self.lib, "Collection", "E02.mkv"))
        self.assertTrue(os.path.exists(f2))

        removed = []          # [(hash, delete_file)]
        companion_deletes = []  # 仅辅种链路（delete_file=False）

        def hook(hashs, delete_file):
            for h in hashs:
                removed.append((h, delete_file))
                if not delete_file:
                    companion_deletes.append(h)

        # 同一目录，但种子名不同 → 各自独立的种，不是辅种
        _install_downloader(
            [
                _qb_torrent(d, "hash_ep1", "Collection.E01",
                            size_gb=_BIG_SEED_GB, age_days=40),
                _qb_torrent(d, "hash_ep2", "Collection.E02",
                            size_gb=_BIG_SEED_GB, age_days=30),
            ],
            removed_hook=hook,
        )
        self.configure([self.dl, self.lib], threshold=10)

        with self.patch_free([1 * GIB, 100 * GIB]):
            self.plugin._clean_by_seed(1 * GIB, dry_run=False)

        self.assertEqual(
            companion_deletes, [],
            "同目录不同种子名的合集集数不得走辅种链路删除",
        )
        self.assertFalse(
            self.plugin._clean_stats.get("companions"),
            "合集不应产生辅种计数",
        )

    def test_companion_outside_scope_not_touched(self):
        """监控目录外的同名种子不在候选内，不得被连带删除。"""
        f1 = self.make_file(os.path.join(self.dl, "Show", "Show.mkv"))
        self.make_hardlink(f1, os.path.join(self.lib, "Show", "Show.mkv"))
        # 范围外的同内容种子
        self.make_file(os.path.join(self.outside, "Show", "Show.mkv"))

        removed = []

        def hook(hashs, delete_file):
            removed.extend(hashs)

        _install_downloader(
            [
                _qb_torrent(os.path.join(self.dl, "Show"), "hash_in", "Show",
                            size_gb=_BIG_SEED_GB, age_days=40),
                _qb_torrent(os.path.join(self.outside, "Show"), "hash_out", "Show",
                            size_gb=_BIG_SEED_GB, age_days=30),
            ],
            removed_hook=hook,
        )
        # 只监控 dl 与 lib，不含 outside
        self.configure([self.dl, self.lib], threshold=10)

        with self.patch_free([1 * GIB, 100 * GIB]):
            self.plugin._clean_by_seed(1 * GIB, dry_run=False)

        self.assertIn("hash_in", removed)
        self.assertNotIn(
            "hash_out", removed,
            "监控目录外的种子不应被牵连（它压根不在候选里）",
        )

    def test_dry_run_previews_companions_without_deleting(self):
        """试运行应预告将连带删除的辅种数，但不实际删除任何东西。"""
        f1 = self.make_file(os.path.join(self.dl, "Show", "Show.mkv"))
        l1 = self.make_hardlink(f1, os.path.join(self.lib, "Show", "Show.mkv"))

        seed_path = os.path.join(self.dl, "Show")
        removed = []

        _install_downloader(
            [
                _qb_torrent(seed_path, "hash_a", "Show",
                            size_gb=_BIG_SEED_GB, age_days=40),
                _qb_torrent(seed_path, "hash_b", "Show",
                            size_gb=_BIG_SEED_GB, age_days=30),
            ],
            removed_hook=lambda h, d: removed.extend(h),
        )
        self.configure([self.dl, self.lib], threshold=10)

        with self.patch_free([1 * GIB]):
            _, _, lines = self.plugin._clean_by_seed(1 * GIB, dry_run=True)

        body = "\n".join(lines)
        self.assertIn("将连带删除辅种 1 个", body,
                      "试运行应如实预告辅种数量")
        self.assertEqual(removed, [], "试运行不得实际删种")
        self.assertTrue(os.path.exists(l1), "试运行不得删除硬链接")

    def test_companion_cleanup_disabled_restores_old_behavior(self):
        """关闭开关后恢复旧行为：只删主种子，硬链接与辅种都不动。"""
        f1 = self.make_file(os.path.join(self.dl, "Show", "Show.mkv"))
        l1 = self.make_hardlink(f1, os.path.join(self.lib, "Show", "Show.mkv"))

        seed_path = os.path.join(self.dl, "Show")
        removed = []

        _install_downloader(
            [
                _qb_torrent(seed_path, "hash_a", "Show",
                            size_gb=_BIG_SEED_GB, age_days=40),
                _qb_torrent(seed_path, "hash_b", "Show",
                            size_gb=_BIG_SEED_GB, age_days=30),
            ],
            removed_hook=lambda h, d: removed.extend(h),
        )
        self.configure([self.dl, self.lib], threshold=10, companion=False)

        with self.patch_free([1 * GIB, 100 * GIB]):
            self.plugin._clean_by_seed(1 * GIB, dry_run=False)

        self.assertEqual(removed, ["hash_a"], "关闭开关后只应删主种子")
        self.assertTrue(os.path.exists(l1), "关闭开关后硬链接不应被动")
        self.assertFalse(self.plugin._clean_stats.get("companions"),
                         "关闭开关后不应产生辅种计数")


class TestCompanionConfig(_SeedFixtureBase):
    """连带清理开关的配置读取语义（走真实 init_plugin 路径）。"""

    def test_default_on_when_key_absent(self):
        """配置里没有该键时，必须默认「开」。

        这是最容易写错的地方：若用 bool(config.get(...))，
        未配置会得到 False（默认关），与需求「默认开」完全相反。
        """
        self.plugin.init_plugin({"enabled": True, "target_dirs": self.dl})
        self.assertTrue(
            self.plugin._companion_cleanup,
            "未配置 companion_cleanup 时应默认开启",
        )

    def test_explicit_true_keeps_on(self):
        """显式 True 时开启。"""
        self.plugin.init_plugin({
            "enabled": True, "target_dirs": self.dl, "companion_cleanup": True,
        })
        self.assertTrue(self.plugin._companion_cleanup)

    def test_explicit_false_turns_off(self):
        """显式 False 时才关闭。"""
        self.plugin.init_plugin({
            "enabled": True, "target_dirs": self.dl, "companion_cleanup": False,
        })
        self.assertFalse(self.plugin._companion_cleanup)

    def test_default_config_declares_on(self):
        """默认配置字典里该键应为 True。"""
        self.assertIs(self.plugin._default_config().get("companion_cleanup"), True)

    def test_form_exposes_companion_switch(self):
        """配置表单应暴露该开关。"""
        form, _ = self.plugin.get_form()
        models = [
            item.get("props", {}).get("model")
            for group in (form or [])
            for item in group.get("content", [])
        ]
        self.assertIn("companion_cleanup", models,
                      "配置表单必须暴露「连带清理硬链接与辅种」开关")


class TestCompanionHelpers(_SeedFixtureBase):
    """连带清理的辅助方法与顺序约束。"""

    def test_content_key_requires_both_path_and_title(self):
        """内容键必须同时包含路径与种子名，任一不同即为不同内容。"""
        a = {"path": "/v/download/Show", "title": "Show"}
        b = {"path": "/v/download/Show", "title": "show"}      # 大小写不同 → 同内容
        c = {"path": "/v/download/Show", "title": "Other"}     # 名字不同 → 不同内容
        d = {"path": "/v/download/Other", "title": "Show"}     # 路径不同 → 不同内容
        self.assertEqual(self.plugin._content_key(a), self.plugin._content_key(b))
        self.assertNotEqual(self.plugin._content_key(a),
                            self.plugin._content_key(c))
        self.assertNotEqual(self.plugin._content_key(a),
                            self.plugin._content_key(d))

    def test_build_companion_map_groups_same_content(self):
        """同一内容的多个 hash 应被归入同一键。"""
        cands = [
            {"path": "/v/download/Show", "title": "Show", "hash": "h1"},
            {"path": "/v/download/Show", "title": "Show", "hash": "h2"},
            {"path": "/v/download/Show", "title": "Other", "hash": "h3"},
        ]
        mapping = self.plugin._build_companion_map(cands)
        self.assertEqual(sorted(mapping[("/v/download/Show", "show")]),
                         ["h1", "h2"])
        self.assertEqual(mapping[("/v/download/Show", "other")], ["h3"])

    def test_seed_related_paths_covers_both_sides(self):
        """关联路径必须同时覆盖下载侧记录与媒体库侧（按种子名推断）。"""
        self.configure([self.dl, self.lib], threshold=10)
        cand = {
            "path": os.path.join(self.dl, "Show"),
            "title": "Show",
            "hash": "h1",
            "files": [],
        }
        paths = self.plugin._seed_related_paths(cand)
        self.assertIn(os.path.join(self.dl, "Show"), paths)
        self.assertIn(
            os.path.join(self.lib, "Show"), paths,
            "媒体库侧路径必须由「配置目录 + 种子名」推断出来，否则硬链接找不到",
        )

    def test_inode_index_resolves_hardlink_pairs(self):
        """inode 索引应把互为硬链接的下载侧/媒体库侧路径归到一起。"""
        f1 = self.make_file(os.path.join(self.dl, "Show", "a.mkv"))
        l1 = self.make_hardlink(f1, os.path.join(self.lib, "Show", "a.mkv"))
        self.configure([self.dl, self.lib], threshold=10)

        index = self.plugin._build_inode_index(
            [os.path.join(self.dl, "Show"), os.path.join(self.lib, "Show")]
        )
        key = next(iter(index))
        self.assertEqual(sorted(index[key]), sorted([f1, l1]),
                         "同 inode 的下载侧与媒体库侧路径应归入同一键")

    def test_inode_index_skips_outside_paths(self):
        """索引不得纳入配置目录外的路径。"""
        f_out = self.make_file(os.path.join(self.outside, "x.mkv"))
        self.configure([self.dl, self.lib], threshold=10)
        index = self.plugin._build_inode_index([f_out])
        self.assertEqual(index, {}, "配置目录外的路径不应进入索引")

    def test_index_built_before_torrent_removal(self):
        """索引必须在删种之前建立（顺序约束）。

        做法：在 remove_torrents 被调用时检查「索引文件是否仍存在」。
        若实现把建索引挪到删种之后，删种后下载侧文件已消失，此处会捕获到
        索引为空（或文件已不在）从而失败。
        """
        f1 = self.make_file(os.path.join(self.dl, "Show", "a.mkv"))
        l1 = self.make_hardlink(f1, os.path.join(self.lib, "Show", "a.mkv"))
        seed_path = os.path.join(self.dl, "Show")
        seen = {}

        class _Server:
            def get_torrents(self, *args, **kwargs):
                return [_qb_torrent(seed_path, "hash_x", "Show")]

            def remove_torrents(self, hashs=None, delete_file=False,
                                downloader=None, **kwargs):
                # 删种这一刻，两侧文件都应仍然存在（索引已建好）
                seen["dl_exists"] = os.path.exists(f1)
                seen["lib_exists"] = os.path.exists(l1)
                # 模拟下载器删掉下载侧文件
                try:
                    os.remove(f1)
                except OSError:
                    pass
                return True

        ModuleManager.reset()
        ModuleManager.register_downloader(
            DownloaderType.Qbittorrent, "qb", _Server()
        )
        self.configure([self.dl, self.lib], threshold=10)

        with self.patch_free([1 * GIB, 100 * GIB]):
            self.plugin._clean_by_seed(1 * GIB, dry_run=False)

        self.assertTrue(seen.get("dl_exists"),
                        "删种时下载侧文件应仍在（索引必须已建好）")
        self.assertFalse(os.path.exists(l1),
                         "删种后媒体库侧硬链接应被清理")


if __name__ == "__main__":
    unittest.main()
