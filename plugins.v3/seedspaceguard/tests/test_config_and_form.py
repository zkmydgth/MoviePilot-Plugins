# -*- coding: utf-8 -*-
"""
配置层回归测试：多目录解析、旧配置迁移、表单结构、状态展示与 API。

这些用例针对「改造后新增/变更的配置契约」，防止后续改动破坏兼容性。
"""

import json
import re
import unittest

import tests  # noqa: F401  触发宿主桩路径注入

from seedspaceguard import DEFAULT_PROTECT_PATTERN, SeedSpaceGuard


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


class TestProtectPatternSemantics(unittest.TestCase):
    """「保护文件后缀」的取值语义：**留空 = 不保护任何文件**（v3.0.10）。

    回归背景：``init_plugin`` 曾把空值回落为默认后缀模板，于是用户清空该项后
    ``.part`` 等未完成文件仍被挡住——「清空」成了无效操作，且没有任何提示。
    这些用例锁定新语义：空串 / 纯空白 / 缺键 / 未传配置，一律不保护任何文件。
    """

    @staticmethod
    def _patterns(plugin):
        """按 ``_clean_by_file`` 的真实口径把配置串解析为后缀列表。"""
        return [
            p.strip()
            for p in re.split(r"[,|，]", plugin._protect_pattern)
            if p.strip()
        ]

    def test_empty_string_protects_nothing(self):
        """空串 = 不保护任何文件。"""
        plugin = SeedSpaceGuard()
        plugin.init_plugin({"enabled": True, "protect_pattern": ""})
        self.assertEqual(plugin._protect_pattern, "")
        self.assertEqual(self._patterns(plugin), [])

    def test_missing_key_protects_nothing(self):
        """配置缺该项时不回落模板（旧行为会回落，属本条回归点）。"""
        plugin = SeedSpaceGuard()
        plugin._protect_pattern = "*.part"
        plugin.init_plugin({"enabled": True})
        self.assertEqual(self._patterns(plugin), [])

    def test_whitespace_only_protects_nothing(self):
        """纯空白同样视为留空。"""
        plugin = SeedSpaceGuard()
        plugin.init_plugin({"enabled": True, "protect_pattern": "   "})
        self.assertEqual(self._patterns(plugin), [])

    def test_no_config_protects_nothing(self):
        """未传配置时复位为空（不留上一次的旧值）。"""
        plugin = SeedSpaceGuard()
        plugin._protect_pattern = "*.part"
        plugin.init_plugin(None)
        self.assertEqual(self._patterns(plugin), [])

    def test_configured_patterns_still_parsed(self):
        """显式配置的后缀依旧逐项生效（含 ``|`` 分隔）。"""
        plugin = SeedSpaceGuard()
        plugin.init_plugin({"enabled": True, "protect_pattern": "*.part|*.!qb"})
        self.assertEqual(self._patterns(plugin), ["*.part", "*.!qb"])


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

    def test_default_config_keeps_protect_template(self):
        """默认配置仍预填推荐后缀：新装即受保护，只有显式清空才不保护。

        ``_default_config`` 是**新装初始模板**，与「空值不回落」并不冲突：
        空值回落曾让「清空」失效，而模板只决定安装时的初始值。
        """
        conf = self.plugin._default_config()
        self.assertEqual(conf["protect_pattern"], DEFAULT_PROTECT_PATTERN)

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

    def test_form_top_notice_is_concise(self):
        """待办2：配置表单顶部说明必须精简（不得回退为 447 字长文）。

        原长文把「种子级/仅文件、联动清理、保护后缀、硬链接多目录」等
        已在各配置项 hint 中重复的内容全堆在首屏，占全表单文案量的 39%。
        现压缩到约 110 字，只保留核心行为、安全承诺与首次使用引导。
        """
        form, _default = self.plugin.get_form()
        texts = []
        for props in self._collect_props(form):
            if isinstance(props, dict) and props.get("text"):
                texts.append(str(props["text"]))
        notice = [t for t in texts if "使用说明" in t]
        self.assertTrue(notice, "表单应保留顶部使用说明")
        body = notice[0]
        self.assertLessEqual(
            len(body), 200,
            f"顶部说明应精简到 200 字以内，实际 {len(body)} 字",
        )
        # 三条必须保留的要素
        self.assertIn("保种最久", body, "应保留核心行为说明")
        self.assertIn("宁可空间不足", body, "应保留安全承诺")
        self.assertIn("试运行", body, "应保留首次使用引导")

    def test_form_top_notice_drops_duplicated_details(self):
        """待办2：已被各配置项 hint 覆盖的细节不应再堆在顶部说明里。"""
        form, _default = self.plugin.get_form()
        notice = ""
        for props in self._collect_props(form):
            if isinstance(props, dict) and "使用说明" in str(props.get("text") or ""):
                notice = str(props["text"])
                break
        self.assertTrue(notice, "表单应保留顶部使用说明")
        for detail in ("硬链接", "种子级", "@eaDir", "保护文件后缀"):
            self.assertNotIn(
                detail, notice,
                f"「{detail}」已在对应配置项 hint 中说明，不应重复出现在顶部",
            )

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
        """状态 API 应在 data 内返回多目录列表。"""
        import asyncio

        plugin = SeedSpaceGuard()
        plugin.init_plugin({"enabled": True, "target_dirs": "/vol/a\n/vol/b"})
        result = asyncio.run(plugin.api_status(request=None))
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["target_dirs"], ["/vol/a", "/vol/b"])

    def test_api_status_keeps_legacy_field(self):
        """状态 API 应在 data 内保留单目录字段（向后兼容老调用方）。"""
        import asyncio

        plugin = SeedSpaceGuard()
        plugin.init_plugin({"enabled": True, "target_dirs": "/vol/a\n/vol/b"})
        result = asyncio.run(plugin.api_status(request=None))
        self.assertEqual(result["data"]["target_dir"], "/vol/a")

    def test_api_status_satisfies_host_envelope(self):
        """
        状态 API 必须满足宿主 envelope 三键契约。

        业务字段若平铺在顶层，顶层键数会超过 3，前端 isApiResponse()
        判定失败并弹出「服务器返回了无效响应」。
        """
        import asyncio

        plugin = SeedSpaceGuard()
        plugin.init_plugin({"enabled": True, "target_dirs": "/vol/a"})
        result = asyncio.run(plugin.api_status(request=None))

        self.assertEqual(
            sorted(result.keys()), ["data", "message", "success"],
            f"顶层只能是 success/message/data 三键，实际：{sorted(result.keys())}",
        )
        self.assertIsInstance(result["success"], bool)
        self.assertIsInstance(result["message"], str)
        self.assertIsInstance(result["data"], dict)

    def test_api_run_satisfies_host_envelope(self):
        """手动触发 API 同样必须满足三键契约。"""
        import asyncio

        plugin = SeedSpaceGuard()
        plugin.init_plugin({"enabled": True, "target_dirs": "/vol/a"})

        class _Req:
            query_params = {}

            async def json(self):
                return {}

        result = asyncio.run(plugin.api_run(request=_Req()))

        self.assertEqual(
            sorted(result.keys()), ["data", "message", "success"],
            f"顶层只能是 success/message/data 三键，实际：{sorted(result.keys())}",
        )
        self.assertIsInstance(result["success"], bool)
        self.assertIsInstance(result["message"], str)

    def test_api_status_legacy_field_empty_when_no_dirs(self):
        """无目录时兼容字段应为空字符串。"""
        import asyncio

        plugin = SeedSpaceGuard()
        plugin.init_plugin({"enabled": True})
        result = asyncio.run(plugin.api_status(request=None))
        self.assertEqual(result["data"]["target_dir"], "")

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


class TestRestoreProtectPatternButton(unittest.TestCase):
    """配置页「恢复默认保护后缀」按钮（v3.0.10）。

    宿主配置表单渲染器（``FormRender.vue`` 的 ``parseProps``）只认
    ``props.on*`` 形式的字符串脚本，且在 ``with(model)`` 作用域内求值；
    ``events`` 在配置表单里不生效（只有详情页按钮支持）。故本按钮采用
    ``onClick`` + 原生 ``confirm`` 确认弹窗，且只改本地表单——
    **仍需用户点击「保存」才落库**，不会直接改配置。
    """

    def setUp(self):
        self.plugin = SeedSpaceGuard()
        self.form, self.default = self.plugin.get_form()

    def _walk(self, node):
        """深度遍历表单节点。"""
        if isinstance(node, dict):
            yield node
            if "content" in node:
                yield from self._walk(node["content"])
        elif isinstance(node, list):
            for item in node:
                yield from self._walk(item)

    def _button(self):
        """取「恢复默认保护后缀」按钮节点。"""
        for node in self._walk(self.form):
            if (node.get("component") == "VBtn"
                    and node.get("text") == "恢复默认保护后缀"):
                return node
        return None

    def _props_by_model(self, model):
        """按 model 取控件 props。"""
        for node in self._walk(self.form):
            props = node.get("props") or {}
            if props.get("model") == model:
                return props
        return {}

    def test_button_present_in_form(self):
        """按钮必须出现在配置表单里。"""
        self.assertIsNotNone(self._button(), "表单缺少「恢复默认保护后缀」按钮")

    def test_button_has_no_model_and_no_events(self):
        """按钮不得带 model（否则会污染表单字段校验），也不得用 events。

        ``events`` 只在详情页按钮生效，放进配置表单会静默失效。
        """
        button = self._button()
        self.assertNotIn("model", button["props"])
        self.assertNotIn("events", button)

    def test_button_follows_file_only_visibility(self):
        """按钮与输入框同属「仅文件」组，可见性必须一致。"""
        button = self._button()
        self.assertEqual(button["props"].get("show"), "mode === 'file'")
        self.assertEqual(
            self._props_by_model("protect_pattern").get("show"),
            button["props"].get("show"),
        )

    def test_button_js_confirms_and_fills_default(self):
        """脚本必须：先 ``confirm`` 确认，再把默认模板写回表单字段。"""
        script = self._button()["props"].get("onClick")
        self.assertIsInstance(script, str, "onClick 必须是字符串脚本")
        self.assertIn("confirm(", script, "缺少确认弹窗")
        self.assertIn("model.protect_pattern", script, "未写回表单字段")
        self.assertIn(
            json.dumps(DEFAULT_PROTECT_PATTERN, ensure_ascii=False),
            script,
            "脚本内联的模板与 DEFAULT_PROTECT_PATTERN 不一致",
        )

    def test_default_template_single_source(self):
        """默认模板只有一个真源：新装默认值 / 占位符 / 按钮脚本三者一致。"""
        self.assertEqual(self.default["protect_pattern"], DEFAULT_PROTECT_PATTERN)
        self.assertEqual(
            self._props_by_model("protect_pattern").get("placeholder"),
            DEFAULT_PROTECT_PATTERN,
        )


if __name__ == "__main__":
    unittest.main()
