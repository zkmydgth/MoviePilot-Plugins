# -*- coding: utf-8 -*-
"""
配置层回归测试：多目录解析、旧配置迁移、表单结构、状态展示与 API。

这些用例针对「改造后新增/变更的配置契约」，防止后续改动破坏兼容性。
"""

import unittest

import tests  # noqa: F401  触发宿主桩路径注入

from seedspaceguard import SeedSpaceGuard


class TestParseDirs(unittest.TestCase):
    """_parse_dirs 多行目录解析。"""

    def test_multiline_basic(self):
        """多行输入应逐行解析为路径列表。"""
        raw = "/vol/a\n/vol/b\n/vol/c"
        self.assertEqual(
            SeedSpaceGuard._parse_dirs(raw), ["/vol/a", "/vol/b", "/vol/c"]
        )

    def test_skip_blank_and_whitespace(self):
        """空行与首尾空白应被忽略。"""
        raw = "\n  /vol/a  \n\n\t/vol/b\t\n\n"
        self.assertEqual(SeedSpaceGuard._parse_dirs(raw), ["/vol/a", "/vol/b"])

    def test_skip_comment_lines(self):
        """# 开头的行应被忽略（用于临时停用目录）。"""
        raw = "/vol/a\n# /vol/b\n   # /vol/c\n/vol/d"
        self.assertEqual(SeedSpaceGuard._parse_dirs(raw), ["/vol/a", "/vol/d"])

    def test_dedup(self):
        """重复路径应去重，且保持首次出现顺序。"""
        raw = "/vol/a\n/vol/b\n/vol/a"
        self.assertEqual(SeedSpaceGuard._parse_dirs(raw), ["/vol/a", "/vol/b"])

    def test_nested_removed(self):
        """嵌套目录应被剔除（父目录已覆盖子目录）。"""
        raw = "/vol/a\n/vol/a/b\n/vol/a/b/c"
        self.assertEqual(SeedSpaceGuard._parse_dirs(raw), ["/vol/a"])

    def test_nested_reverse_order(self):
        """子目录先出现时，仍应保留父目录、剔除子目录。"""
        raw = "/vol/a/b\n/vol/a"
        self.assertEqual(SeedSpaceGuard._parse_dirs(raw), ["/vol/a"])

    def test_normalize_trailing_slash(self):
        """末尾斜杠应被规范化。"""
        raw = "/vol/a/\n/vol/b//"
        self.assertEqual(SeedSpaceGuard._parse_dirs(raw), ["/vol/a", "/vol/b"])

    def test_similar_prefix_not_nested(self):
        """/vol/ab 不是 /vol/a 的子目录，不应被误剔除。"""
        raw = "/vol/a\n/vol/ab"
        self.assertEqual(SeedSpaceGuard._parse_dirs(raw), ["/vol/a", "/vol/ab"])

    def test_empty_and_none(self):
        """空值应返回空列表。"""
        self.assertEqual(SeedSpaceGuard._parse_dirs(""), [])
        self.assertEqual(SeedSpaceGuard._parse_dirs(None), [])
        self.assertEqual(SeedSpaceGuard._parse_dirs("   \n  \n"), [])

    def test_single_dir_compat(self):
        """单行输入应返回单元素列表（兼容旧配置）。"""
        self.assertEqual(SeedSpaceGuard._parse_dirs("/vol/only"), ["/vol/only"])


class TestPathUnderAny(unittest.TestCase):
    """_path_under_any 多目录归属判断与安全边界。"""

    def setUp(self):
        self.plugin = SeedSpaceGuard()
        self.plugin._target_dirs = ["/vol/dl", "/vol/lib"]
        self.plugin._active_dirs = ["/vol/dl", "/vol/lib"]

    def test_inside_either(self):
        """落在任一目录内即返回 True。"""
        self.assertTrue(self.plugin._path_under_any("/vol/dl/a.mkv"))
        self.assertTrue(self.plugin._path_under_any("/vol/lib/a.mkv"))
        self.assertTrue(self.plugin._path_under_any("/vol/lib/sub/a.mkv"))

    def test_outside_all(self):
        """不在任何目录内应返回 False。"""
        self.assertFalse(self.plugin._path_under_any("/vol/other/a.mkv"))
        self.assertFalse(self.plugin._path_under_any("/vol/dlx/a.mkv"))
        self.assertFalse(self.plugin._path_under_any(""))

    def test_prefix_attack_blocked(self):
        """前缀相似但非子目录的路径不应被判定为在内。"""
        # /vol/dl-evil 以 /vol/dl 开头但不是其子目录
        self.assertFalse(self.plugin._path_under_any("/vol/dl-evil/a.mkv"))

    def test_dir_itself(self):
        """目录自身应判定为在内。"""
        self.assertTrue(self.plugin._path_under_any("/vol/dl"))

    def test_falls_back_to_target_dirs(self):
        """_active_dirs 为空时应回退到 _target_dirs。"""
        self.plugin._active_dirs = []
        self.assertTrue(self.plugin._path_under_any("/vol/dl/a.mkv"))

    def test_explicit_dirs_used_when_no_active(self):
        """
        无生效目录时，显式传入的目录列表应生效。

        注意：_active_dirs 非空时优先于显式参数——这是安全边界所需，
        删除判定必须基于「本次实际生效的目录」，不能被外部参数放宽。
        """
        self.plugin._active_dirs = []
        self.plugin._target_dirs = []
        self.assertTrue(self.plugin._path_under_any("/vol/x/a.mkv", ["/vol/x"]))
        self.assertFalse(self.plugin._path_under_any("/vol/x/a.mkv", ["/vol/y"]))

    def test_active_dirs_take_priority_over_explicit(self):
        """生效目录非空时应优先，避免外部参数绕过安全边界。"""
        self.assertTrue(self.plugin._path_under_any("/vol/dl/a.mkv", ["/vol/other"]))
        self.assertFalse(self.plugin._path_under_any("/vol/other/a.mkv", ["/vol/dl"]))


class TestConfigMigration(unittest.TestCase):
    """旧版单目录配置向多目录迁移。"""

    def test_new_field_wins(self):
        """已配置 target_dirs 时应直接使用，不做迁移。"""
        plugin = SeedSpaceGuard()
        plugin.init_plugin({"enabled": True, "target_dirs": "/vol/new/a\n/vol/new/b"})
        self.assertEqual(plugin._target_dirs, ["/vol/new/a", "/vol/new/b"])

    def test_migrate_from_legacy(self):
        """仅有旧字段 target_dir 时应迁移为多目录。"""
        plugin = SeedSpaceGuard()
        plugin.init_plugin({"enabled": True, "target_dir": "/vol/legacy"})
        self.assertEqual(plugin._target_dirs, ["/vol/legacy"])

    def test_migration_writes_back(self):
        """迁移应把结果回写配置，避免每次启动重复迁移。"""
        plugin = SeedSpaceGuard()
        plugin.update_config({"enabled": True, "target_dir": "/vol/legacy"})
        plugin.init_plugin({"enabled": True, "target_dir": "/vol/legacy"})
        saved = plugin.get_config()
        self.assertEqual(saved.get("target_dirs"), "/vol/legacy")
        self.assertEqual(saved.get("target_dir"), "")

    def test_new_field_takes_priority_over_legacy(self):
        """两字段同时存在时应以新字段为准。"""
        plugin = SeedSpaceGuard()
        plugin.init_plugin({
            "enabled": True,
            "target_dirs": "/vol/new",
            "target_dir": "/vol/old",
        })
        self.assertEqual(plugin._target_dirs, ["/vol/new"])

    def test_empty_dirs_string_treated_as_missing(self):
        """target_dirs 为空白字符串时应视作未配置并尝试迁移。"""
        plugin = SeedSpaceGuard()
        plugin.init_plugin({
            "enabled": True,
            "target_dirs": "   ",
            "target_dir": "/vol/legacy",
        })
        self.assertEqual(plugin._target_dirs, ["/vol/legacy"])

    def test_no_config_resets_state(self):
        """未传配置时应复位内部状态。"""
        plugin = SeedSpaceGuard()
        plugin._target_dirs = ["/stale"]
        plugin.init_plugin(None)
        self.assertEqual(plugin._target_dirs, [])


class TestDefaultConfigAndForm(unittest.TestCase):
    """默认配置与表单结构一致性。"""

    def setUp(self):
        self.plugin = SeedSpaceGuard()

    def test_default_config_has_new_field(self):
        """默认配置应包含多目录字段。"""
        conf = self.plugin._default_config()
        self.assertIn("target_dirs", conf)
        self.assertIn("target_dir", conf)

    def test_default_config_target_dir_empty(self):
        """兼容字段 target_dir 默认为空，避免与新字段冲突。"""
        conf = self.plugin._default_config()
        self.assertEqual(conf["target_dir"], "")

    def test_form_uses_textarea_for_dirs(self):
        """目录输入应使用多行文本框。"""
        form, _default = self.plugin.get_form()
        models = self._collect_models(form)
        self.assertIn("target_dirs", models)
        self.assertNotIn("target_dir", models)

    def test_form_has_no_stale_target_dir_field(self):
        """表单不应再出现旧的单目录字段。"""
        form, _default = self.plugin.get_form()
        props = self._collect_props(form)
        self.assertFalse(
            any(self._model_of(p) == "target_dir" for p in props),
            "表单仍残留 target_dir 输入项",
        )

    def test_form_models_match_default_config(self):
        """表单引用的字段都应存在于默认配置中（防止读不到配置）。"""
        form, default = self.plugin.get_form()
        models = self._collect_models(form)
        missing = models - set(default)
        self.assertFalse(missing, f"表单字段在默认配置中缺失: {missing}")

    def test_form_returns_default_config(self):
        """get_form 应同时返回默认配置。"""
        form, default = self.plugin.get_form()
        self.assertIsInstance(form, list)
        self.assertIsInstance(default, dict)

    # ------------------------------------------------------------------
    def _walk(self, node):
        """深度遍历表单节点。"""
        if isinstance(node, dict):
            yield node
            if "content" in node:
                yield from self._walk(node["content"])
        elif isinstance(node, list):
            for item in node:
                yield from self._walk(item)

    def _collect_props(self, form):
        """收集全部 props。"""
        return [n.get("props") or {} for n in self._walk(form)]

    @staticmethod
    def _model_of(props):
        """取 props 中的 model。"""
        return props.get("model") if isinstance(props, dict) else None

    def _collect_models(self, form):
        """收集全部 model 名。"""
        return {
            self._model_of(p)
            for p in self._collect_props(form)
            if self._model_of(p)
        }


class TestStatusAndPage(unittest.TestCase):
    """状态展示与 API 返回。"""

    def test_api_status_includes_dirs(self):
        """状态 API 应返回多目录列表。"""
        import asyncio

        plugin = SeedSpaceGuard()
        plugin.init_plugin({"enabled": True, "target_dirs": "/vol/a\n/vol/b"})
        result = asyncio.run(plugin.api_status(request=None))
        self.assertTrue(result["success"])
        self.assertEqual(result["target_dirs"], ["/vol/a", "/vol/b"])

    def test_api_status_keeps_legacy_field(self):
        """状态 API 应保留单目录字段（向后兼容老调用方）。"""
        import asyncio

        plugin = SeedSpaceGuard()
        plugin.init_plugin({"enabled": True, "target_dirs": "/vol/a\n/vol/b"})
        result = asyncio.run(plugin.api_status(request=None))
        self.assertEqual(result["target_dir"], "/vol/a")

    def test_api_status_legacy_field_empty_when_no_dirs(self):
        """无目录时兼容字段应为空字符串。"""
        import asyncio

        plugin = SeedSpaceGuard()
        plugin.init_plugin({"enabled": True})
        result = asyncio.run(plugin.api_status(request=None))
        self.assertEqual(result["target_dir"], "")

    def test_page_none_when_disabled(self):
        """插件未启用时详情页应返回 None。"""
        plugin = SeedSpaceGuard()
        plugin.init_plugin({"enabled": False, "target_dirs": "/vol/a"})
        self.assertIsNone(plugin.get_page())

    def test_page_renders_with_many_dirs(self):
        """目录较多时详情页应折叠显示且不报错。"""
        plugin = SeedSpaceGuard()
        plugin.init_plugin({
            "enabled": True,
            "target_dirs": "\n".join(f"/vol/d{i}" for i in range(6)),
        })
        page = plugin.get_page()
        self.assertIsNotNone(page)
        text = str(page)
        self.assertIn("6 个目录", text)

    def test_page_renders_with_few_dirs(self):
        """目录较少时应完整列出。"""
        plugin = SeedSpaceGuard()
        plugin.init_plugin({"enabled": True, "target_dirs": "/vol/a\n/vol/b"})
        page = plugin.get_page()
        text = str(page)
        self.assertIn("/vol/a", text)
        self.assertIn("/vol/b", text)
        self.assertNotIn("个目录", text)


if __name__ == "__main__":
    unittest.main()
