# -*- coding: utf-8 -*-
"""
空壳回收范围扩展测试（v3.0.4）：回收「监控目录外」的空壳种子。

背景（用户 2026-10-02 追问「空壳种子搜集完全吗？有没有漏过的？」）：
  实测发现 `_reap_orphan_seeds` 的候选来自 `_collect_seed_candidates`，
  后者会把 `content_path` 不在监控目录内的种子标为 `out_of_scope` 丢弃。
  于是出现一个讽刺的结果：

    **一个种子越是彻底地变成空壳，它越进不了空壳回收的候选。**

  因为空壳的典型形态是「文件全没了、目录也没了」，此时 `content_path`
  指向已不存在的路径，`_path_under_any` 的前缀匹配必然失败。
  实测三个空壳只回收了一个。

本测试覆盖两部分：

  A. **功能**：`_collect_all_seed_candidates`（不判范围）与开关 `orphan_seed_scope`
     的择路行为 —— 开关关时保持原行为，开时纳入范围外空壳。

  B. **安全**（重点）：扩大范围**绝不能**变成「误删还在做种的种子」。
     空壳回收只调 `remove_torrents(delete_file=False)`，不碰文件；真正的
     风险是误删活跃种子，因此大量用例围绕「什么情况下绝不能回收」展开：
       - 范围外但**仍有文件**的种子 → 绝不回收（最高危）
       - 判定依据失效（无记录、无路径、查询异常）→ 保守不回收
       - 目录扫描超上限（疑似扫不完）→ 判「有文件」，不回收
       - 未完成种子 → 本版**仍被排除**（防越界改动）
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


def _install_downloader(torrents, removed_hook=None, raise_on_get=False):
    """注册一个可控的 qBittorrent 下载器桩。

    :param torrents: get_torrents() 返回的原始条目列表
    :param removed_hook: 接收 (hashs, delete_file) 的回调
    :param raise_on_get: 为真时 get_torrents() 抛异常（模拟下载器故障）
    """

    class _Server:
        def get_torrents(self, *args, **kwargs):
            if raise_on_get:
                raise RuntimeError("downloader boom")
            return torrents

        def remove_torrents(self, hashs=None, delete_file=False,
                            downloader=None, **kwargs):
            if removed_hook:
                normalized = [hashs] if isinstance(hashs, str) else list(hashs or [])
                removed_hook(normalized, delete_file)
            return True

    ModuleManager.reset()
    ModuleManager.register_downloader(
        DownloaderType.Qbittorrent, "qb", _Server()
    )


def _qb_item(content_path, hash_str, name, progress=1.0):
    """构造一个 qBittorrent 原始种子条目（camelCase 字段）。"""
    return {
        "content_path": content_path,
        "save_path": os.path.dirname(content_path),
        "hash": hash_str,
        "name": name,
        "progress": progress,
        "completion_on": int(time.time()) - 40 * 86400,
        "size": 4 * 1024 ** 3,
        "added_on": int(time.time()) - 40 * 86400,
    }


class _ScopeBase(unittest.TestCase):
    """夹具：监控目录（dl + lib）与监控范围外目录（away）。"""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="ssg-scope-")
        self.dl = os.path.join(self.base, "download")
        self.lib = os.path.join(self.base, "library")
        self.away = os.path.join(self.base, "away")     # 监控范围之外
        for path in (self.dl, self.lib, self.away):
            os.makedirs(path)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)
        # _clean_stats 是类属性，跨用例共享 → 必须显式复位
        SeedSpaceGuard._clean_stats = None

    # ------------------------------------------------------------------
    def make_plugin(self, scope=False, delete_torrents=True):
        p = SeedSpaceGuard()
        p._target_dirs = [self.dl, self.lib]
        p._active_dirs = [self.dl, self.lib]
        p._volume_path = self.base
        p._protect_pattern = ""
        p._recent_skip_days = 0
        p._threshold_gb = 1.0
        p._companion_cleanup = True
        p._orphan_cleanup = False
        p._orphan_seed_scope = scope
        p._delete_torrents = delete_torrents
        p._delete_history = False
        p._downloadhis = None
        p._transferhis = None
        p._ino_paths = {}
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

    def names(self, plugin):
        return {c.get("title") for c in plugin._collect_all_seed_candidates()}


# ======================================================================
# A. 候选收集：范围外的空壳必须能进候选
# ======================================================================
class TestScopeCandidateCollection(_ScopeBase):

    def test_1_outside_empty_shell_is_collected(self):
        """场景 B：目录在监控外、已被删空 → 必须能进候选。"""
        empty = os.path.join(self.away, "ShellB")
        os.makedirs(empty)
        _install_downloader([_qb_item(empty, "b1", "ShellB")])
        plugin = self.make_plugin()

        self.assertIn(
            "ShellB", self.names(plugin),
            "范围外的空壳必须进候选，否则永远回收不了",
        )

    def test_2_outside_vanished_dir_is_collected(self):
        """场景 C（最典型）：目录整个消失 → 必须能进候选。

        这是实测中漏扫最严重的一类：qB 仍记录原 content_path，
        而该路径已不存在，前缀匹配必然失败。
        """
        gone = os.path.join(self.away, "ShellC")     # 故意不创建
        _install_downloader([_qb_item(gone, "c1", "ShellC")])
        plugin = self.make_plugin()

        self.assertIn(
            "ShellC", self.names(plugin),
            "目录已消失的空壳必须能进候选——它正是最该回收的形态",
        )

    def test_3_in_scope_seed_still_collected(self):
        """监控目录内的种子照常进候选（不回归）。"""
        inside = os.path.join(self.dl, "Inside")
        os.makedirs(inside)
        _install_downloader([_qb_item(inside, "i1", "Inside")])
        plugin = self.make_plugin()

        self.assertIn("Inside", self.names(plugin))

    def test_4_unfinished_seed_still_excluded(self):
        """⚠️ 防越界：未完成种子在本版**仍被排除**。

        决策点 2 已定「暂不处理未完成种子」。此用例防止本次改动
        （取消范围过滤）顺手把 progress 过滤也放开了。
        """
        gone = os.path.join(self.away, "HalfDone")
        _install_downloader([_qb_item(gone, "h1", "HalfDone", progress=0.5)])
        plugin = self.make_plugin()

        self.assertNotIn(
            "HalfDone", self.names(plugin),
            "未完成种子必须仍被排除——本版不覆盖该场景",
        )


# ======================================================================
# B. 开关行为：关时保持原样，开时纳入范围外
# ======================================================================
class TestScopeSwitch(_ScopeBase):

    def test_5_switch_off_keeps_original_behavior(self):
        """开关关闭 → 范围外空壳不进候选（原行为不变）。"""
        empty = os.path.join(self.away, "ShellB")
        os.makedirs(empty)
        _install_downloader([_qb_item(empty, "b1", "ShellB")])
        plugin = self.make_plugin(scope=False)

        got = {c.get("title") for c in plugin._collect_seed_candidates()}
        self.assertNotIn("ShellB", got, "开关关闭时不得改变原有范围语义")

    def test_6_switch_on_reaps_outside_shell(self):
        """开关开启 → 范围外空壳被回收（端到端）。"""
        empty = os.path.join(self.away, "ShellB")
        os.makedirs(empty)
        removed = []
        _install_downloader(
            [_qb_item(empty, "b1", "ShellB")],
            removed_hook=lambda h, d: removed.append((h, d)),
        )
        plugin = self.make_plugin(scope=True)

        res = plugin._reap_orphan_seeds(dry_run=False)
        self.assertEqual(res["torrent"], 1, "范围外空壳应被回收")
        self.assertEqual(
            removed, [(["b1"], False)],
            "回收必须只摘种子、不删文件（delete_file=False）",
        )

    def test_7_switch_off_does_not_reap_outside_shell(self):
        """开关关闭 → 范围外空壳**不**被回收（对照用例）。"""
        empty = os.path.join(self.away, "ShellB")
        os.makedirs(empty)
        removed = []
        _install_downloader(
            [_qb_item(empty, "b1", "ShellB")],
            removed_hook=lambda h, d: removed.append((h, d)),
        )
        plugin = self.make_plugin(scope=False)

        res = plugin._reap_orphan_seeds(dry_run=False)
        self.assertEqual(res["torrent"], 0, "开关关闭时不得回收范围外空壳")
        self.assertEqual(removed, [])

    def test_8_switch_on_in_scope_still_reaped(self):
        """开关开启时，范围内空壳仍正常回收（不回归）。"""
        empty = os.path.join(self.dl, "ShellA")
        os.makedirs(empty)
        removed = []
        _install_downloader(
            [_qb_item(empty, "a1", "ShellA")],
            removed_hook=lambda h, d: removed.append((h, d)),
        )
        plugin = self.make_plugin(scope=True)

        res = plugin._reap_orphan_seeds(dry_run=False)
        self.assertEqual(res["torrent"], 1)
        self.assertEqual(removed, [(["a1"], False)])

    def test_9_dry_run_previews_outside_shell(self):
        """试运行照样预告范围外空壳（不因 dry_run 整段跳过扫描）。"""
        empty = os.path.join(self.away, "ShellB")
        os.makedirs(empty)
        removed = []
        _install_downloader(
            [_qb_item(empty, "b1", "ShellB")],
            removed_hook=lambda h, d: removed.append((h, d)),
        )
        plugin = self.make_plugin(scope=True)

        res = plugin._reap_orphan_seeds(dry_run=True)
        self.assertEqual(res["torrent"], 1, "试运行应如实预告将回收的种子")
        self.assertEqual(removed, [], "试运行不得真的删种")
        self.assertTrue(any("ShellB" in line for line in res["_lines"]))


# ======================================================================
# B2. 安全：绝不能误删还在做种的种子（最高危）
# ======================================================================
class TestScopeSafety(_ScopeBase):

    def test_10_outside_seed_with_files_is_kept(self):
        """⚠️ 最高危：范围外但**仍有文件**的种子，绝不能回收。

        这是扩大范围后的核心风险——范围外种子同样在做种，
        若因「范围外」就跳过物理复核，会把完好种子删掉。
        """
        content = os.path.join(self.away, "AliveSeed")
        self.make_file(os.path.join(content, "movie.mkv"), size_kb=1024)
        removed = []
        _install_downloader(
            [_qb_item(content, "alive1", "AliveSeed")],
            removed_hook=lambda h, d: removed.append((h, d)),
        )
        plugin = self.make_plugin(scope=True)

        res = plugin._reap_orphan_seeds(dry_run=False)
        self.assertEqual(
            res["torrent"], 0,
            "仍有文件的种子绝不能被当空壳回收（数据丢失级）",
        )
        self.assertEqual(res["alive"], 1)
        self.assertEqual(removed, [])

    def test_11_outside_seed_missing_path_conservative(self):
        """无记录且 content_path 为空 → 无从判定，保守不回收。"""
        item = _qb_item("", "nopath1", "NoPath")
        item["content_path"] = ""
        item["save_path"] = ""
        removed = []
        _install_downloader(
            [item], removed_hook=lambda h, d: removed.append((h, d)),
        )
        plugin = self.make_plugin(scope=True)

        res = plugin._reap_orphan_seeds(dry_run=False)
        self.assertEqual(
            res["torrent"], 0,
            "无任何可复核依据时必须保守保留，不得凭猜测删种",
        )
        self.assertEqual(removed, [])

    def test_12_enumeration_failure_aborts(self):
        """下载器读取失败 → 候选为空，不回收任何种子。"""
        _install_downloader([], raise_on_get=True)
        plugin = self.make_plugin(scope=True)

        res = plugin._reap_orphan_seeds(dry_run=False)
        self.assertEqual(res["torrent"], 0)
        self.assertEqual(res["checked"], 0)

    def test_13_dir_scan_limit_keeps_seed(self):
        """目录扫描超上限（疑似扫不完）→ 判「有文件」，不回收。

        用 mock.patch.object 替换静态方法，避免手工备份/还原时把
        staticmethod 描述符还原成绑定方法（那会让 self 被当成 path）。
        """
        content = os.path.join(self.away, "HugeDir")
        self.make_file(os.path.join(content, "a.mkv"), size_kb=64)
        removed = []
        _install_downloader(
            [_qb_item(content, "huge1", "HugeDir")],
            removed_hook=lambda h, d: removed.append((h, d)),
        )
        plugin = self.make_plugin(scope=True)

        original = SeedSpaceGuard._dir_has_any_file

        def tiny_limit(path, max_scan=None, _depth=0):
            # 强制 max_scan=0，模拟「一眼就知道扫不完」
            return original(path, max_scan=0, _depth=_depth)

        patcher = mock.patch.object(
            SeedSpaceGuard, "_dir_has_any_file", staticmethod(tiny_limit)
        )
        with patcher:
            res = plugin._reap_orphan_seeds(dry_run=False)

        self.assertEqual(
            res["torrent"], 0,
            "扫不完时必须保守判为「有文件」，绝不能回收",
        )
        self.assertEqual(removed, [])
        # 还原校验：确保 mock 退出后静态方法已恢复原状
        self.assertIs(
            SeedSpaceGuard._dir_has_any_file, original,
            "静态方法必须被完整还原，否则会污染后续用例",
        )

    def test_14_switch_requires_delete_torrents(self):
        """总闸（联动删除种子）未开 → 本开关无效，不做任何回收。"""
        empty = os.path.join(self.away, "ShellB")
        os.makedirs(empty)
        removed = []
        _install_downloader(
            [_qb_item(empty, "b1", "ShellB")],
            removed_hook=lambda h, d: removed.append((h, d)),
        )
        plugin = self.make_plugin(scope=True, delete_torrents=False)

        res = plugin._reap_orphan_seeds(dry_run=False)
        self.assertEqual(
            res["torrent"], 0,
            "未开启「联动删除种子」时空壳回收整段不应执行",
        )
        self.assertEqual(removed, [])


# ======================================================================
# C. 配置：走真实 init_plugin / default_config / get_form
# ======================================================================
class TestScopeConfig(_ScopeBase):

    def test_15_default_off_when_key_absent(self):
        plugin = SeedSpaceGuard()
        plugin.init_plugin({"enabled": True,
                            "target_dirs": self.dl + "\n" + self.lib})
        self.assertFalse(
            plugin._orphan_seed_scope,
            "未配置时必须默认关闭——它把手伸到配置目录之外",
        )

    def test_16_explicit_false_keeps_off(self):
        plugin = SeedSpaceGuard()
        plugin.init_plugin({"enabled": True,
                            "target_dirs": self.dl + "\n" + self.lib,
                            "orphan_seed_scope": False})
        self.assertFalse(plugin._orphan_seed_scope)

    def test_17_explicit_true_turns_on(self):
        plugin = SeedSpaceGuard()
        plugin.init_plugin({"enabled": True,
                            "target_dirs": self.dl + "\n" + self.lib,
                            "orphan_seed_scope": True})
        self.assertTrue(plugin._orphan_seed_scope)

    def test_18_default_config_declares_off(self):
        plugin = SeedSpaceGuard()
        self.assertFalse(
            plugin._default_config()["orphan_seed_scope"],
            "_default_config 必须声明为关闭",
        )

    def test_19_form_exposes_scope_switch(self):
        """表单必须暴露该开关，且 hint 需说明对「联动删除种子」的依赖。"""
        plugin = SeedSpaceGuard()
        found = {}

        def walk(node):
            if isinstance(node, dict):
                props = node.get("props")
                if isinstance(props, dict) \
                        and props.get("model") == "orphan_seed_scope":
                    found["props"] = props
                for value in node.values():
                    walk(value)
            elif isinstance(node, (list, tuple)):
                for value in node:
                    walk(value)

        walk(plugin.get_form())
        self.assertIn("props", found, "表单必须暴露空壳回收范围开关")
        hint = str(found["props"].get("hint") or "")
        self.assertIn(
            "联动删除种子", hint,
            "hint 必须说明依赖关系，否则用户开了以为没效果",
        )


# ======================================================================
# D. 端到端：范围内外混合，只回收该回收的
# ======================================================================
class TestScopeEndToEnd(_ScopeBase):

    def test_20_mixed_shells_only_reap_the_empty_ones(self):
        """混合场景：范围内空壳 + 范围外空壳 + 范围外活跃种子。"""
        in_shell = os.path.join(self.dl, "InShell")
        os.makedirs(in_shell)
        out_shell = os.path.join(self.away, "OutShell")
        os.makedirs(out_shell)
        out_alive = os.path.join(self.away, "OutAlive")
        self.make_file(os.path.join(out_alive, "keep.mkv"), size_kb=512)

        removed = []
        _install_downloader(
            [
                _qb_item(in_shell, "k1", "InShell"),
                _qb_item(out_shell, "k2", "OutShell"),
                _qb_item(out_alive, "k3", "OutAlive"),
            ],
            removed_hook=lambda h, d: removed.append((h, d)),
        )
        plugin = self.make_plugin(scope=True)

        res = plugin._reap_orphan_seeds(dry_run=False)
        self.assertEqual(res["torrent"], 2, "两个空壳应被回收")
        self.assertEqual(res["alive"], 1, "活跃种子应被识别为「仍有文件」")
        hashes = [h for pair in removed for h in pair[0]]
        self.assertCountEqual(
            hashes, ["k1", "k2"],
            "只应回收两个空壳，绝不碰活跃种子 k3",
        )
        self.assertTrue(all(d is False for _h, d in removed),
                        "回收必须 delete_file=False，不删任何文件")
        self.assertTrue(os.path.exists(os.path.join(out_alive, "keep.mkv")),
                        "活跃种子的文件必须原封不动")


if __name__ == "__main__":
    unittest.main()
