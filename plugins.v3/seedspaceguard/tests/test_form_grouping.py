# -*- coding: utf-8 -*-
"""
配置界面分组与条件显示回归测试（v3.0.6）。

背景
----
本插件配置项较多，且部分项只在某一清理模式下生效。改造前所有项平铺展示，
只能靠 label 前缀（如「种子级：」）口头标注归属 —— 用户反映「很容易引起误会」，
尤其 ``orphan_seed_scope``（实为**两模式通用**）被冠以「种子级：」前缀，
更是把不存在的模式约束强加给了用户。

v3.0.6 起按四组重排：
  ① 全局基础设置（常驻）
  ② 种子级模式设置（``show: mode === 'seed'``）
  ③ 仅文件模式设置（``show: mode === 'file'``）
  ④ 种子联动设置（两种模式通用，常驻）

``show`` 由 MoviePilot 前端 FormRender 求值（源码
``src/components/render/FormRender.vue`` 的 ``parseProps``）：
表达式为假时设 ``style.display='none'``。前端在旧版本不认该属性时会退化为
「全部显示」——**功能不受损，只是不够清爽**，故本改造是安全的增强。

这些用例守护四件事：
1. 四个组标题都在，且不会污染「顶部使用说明」的专项断言；
2. 模式专用项确实带上了正确的 ``show`` 条件；
3. 全局项与通用项**绝不**带 ``show``（防止误藏导致用户找不到设置）；
4. 凡带 ``hint`` 的控件都带 ``persistent-hint: True``，让说明文字常驻
   （否则桌面端会退化成「点开开关才显示」）。
"""

import unittest

import tests  # noqa: F401  触发宿主桩路径注入

from seedspaceguard import SeedSpaceGuard


# 种子级模式专属（应带 show: mode === 'seed'）
SEED_ONLY_MODELS = ("downloaders", "companion_cleanup", "orphan_cleanup")
# 仅文件模式专属（应带 show: mode === 'file'）
FILE_ONLY_MODELS = ("protect_pattern",)
# 两模式通用 / 全局（绝不能带 show，否则会被误藏）
ALWAYS_VISIBLE_MODELS = (
    "manual_action", "enabled", "mode", "target_dirs", "volume_path",
    "threshold_gb", "recent_skip_days", "cron", "sync_wait_seconds",
    "dry_run", "notify",
    "delete_torrents", "delete_history", "orphan_seed_scope",
)

GROUP_TITLES = (
    "全局基础设置",
    "种子级模式设置",
    "仅文件模式设置",
    "种子联动设置（两种模式通用）",
)


class _FormBase(unittest.TestCase):
    """提供表单遍历工具。"""

    def setUp(self):
        self.plugin = SeedSpaceGuard()
        self.form, self.default = self.plugin.get_form()

    # ------------------------------------------------------------------
    def _walk(self, node):
        """深度遍历表单节点（与 test_config_and_form 同构）。"""
        if isinstance(node, dict):
            yield node
            if "content" in node:
                yield from self._walk(node["content"])
        elif isinstance(node, list):
            for item in node:
                yield from self._walk(item)

    def _props_by_model(self):
        """{model: props} 映射。同名取首个。"""
        out = {}
        for n in self._walk(self.form):
            props = n.get("props") or {}
            model = props.get("model")
            if model and model not in out:
                out[model] = props
        return out

    def _group_headers(self):
        """收集全部「分组标题」节点的 props。

        分组标题的判据：VAlert 且 text 以 ``▼ `` 开头。
        用这个前缀而非「使用说明」字样，是为了不与顶部说明的专项断言
        相互干扰（见 test_group_headers_do_not_shadow_top_notice）。
        """
        out = []
        for n in self._walk(self.form):
            if n.get("component") != "VAlert":
                continue
            props = n.get("props") or {}
            if str(props.get("text") or "").startswith("▼ "):
                out.append(props)
        return out


class TestGroupHeaders(_FormBase):
    """分组标题的存在性与安全性。"""

    def test_four_group_headers_present(self):
        """四个分组标题都应存在。"""
        texts = " | ".join(str(p.get("text") or "") for p in self._group_headers())
        for title in GROUP_TITLES:
            self.assertIn(title, texts, f"缺少分组标题：{title}")

    def test_group_headers_do_not_contain_usage_marker(self):
        """组标题不得含「使用说明」四字。

        否则会被 test_config_and_form.test_form_top_notice_is_concise
        的「取第一条含『使用说明』的 text」逻辑误当成顶部说明，
        导致那条用例取错对象（长度/关键词断言随之失真）。
        """
        for props in self._group_headers():
            self.assertNotIn(
                "使用说明", str(props.get("text") or ""),
                "分组标题不得含『使用说明』，否则会污染顶部说明的专项断言",
            )

    def test_top_notice_still_unique_and_concise(self):
        """顶部使用说明仍应只有一条，且仍满足 ≤200 字。

        改造新增了多条 VAlert，容易顺带把顶部说明也改花 —— 这里做守恒校验。
        """
        notices = [
            p for p in self._walk(self.form)
            if "使用说明" in str((p.get("props") or {}).get("text") or "")
        ]
        self.assertEqual(len(notices), 1, "顶部使用说明应恰有一条")
        body = str(notices[0]["props"]["text"])
        self.assertLessEqual(len(body), 200, f"顶部说明应 ≤200 字，实际 {len(body)}")

    def test_group_headers_have_no_model(self):
        """分组标题不得带 model（否则会撞「字段须在默认配置中」的校验）。"""
        for props in self._group_headers():
            self.assertNotIn("model", props, "分组标题不应带 model")
            self.assertNotIn("model", props)


class TestConditionalVisibility(_FormBase):
    """模式专用项的 show 条件。"""

    def test_seed_only_fields_show_on_seed(self):
        """种子级专属项应带 show: mode === 'seed'。"""
        props_map = self._props_by_model()
        for model in SEED_ONLY_MODELS:
            self.assertIn(model, props_map, f"表单缺少 {model}")
            self.assertEqual(
                props_map[model].get("show"), "mode === 'seed'",
                f"{model} 应仅在种子级模式显示",
            )

    def test_file_only_fields_show_on_file(self):
        """仅文件专属项应带 show: mode === 'file'。"""
        props_map = self._props_by_model()
        for model in FILE_ONLY_MODELS:
            self.assertIn(model, props_map, f"表单缺少 {model}")
            self.assertEqual(
                props_map[model].get("show"), "mode === 'file'",
                f"{model} 应仅在仅文件模式显示",
            )

    def test_seed_group_header_shares_seed_condition(self):
        """「种子级模式设置」组标题应跟随模式显隐。"""
        hit = [p for p in self._group_headers()
               if "种子级模式设置" in str(p.get("text") or "")]
        self.assertTrue(hit, "缺少「种子级模式设置」组标题")
        self.assertEqual(hit[0].get("show"), "mode === 'seed'")

    def test_file_group_header_shares_file_condition(self):
        """「仅文件模式设置」组标题应跟随模式显隐。"""
        hit = [p for p in self._group_headers()
               if "仅文件模式设置" in str(p.get("text") or "")]
        self.assertTrue(hit, "缺少「仅文件模式设置」组标题")
        self.assertEqual(hit[0].get("show"), "mode === 'file'")

    def test_always_visible_fields_have_no_show(self):
        """全局项与通用项**绝不能**带 show。

        这是本改造最危险的方向：若误给通用项加了 show，用户在另一种模式下
        会完全看不到该设置，且界面无任何提示 —— 属于「功能性静默失效」。
        """
        props_map = self._props_by_model()
        for model in ALWAYS_VISIBLE_MODELS:
            self.assertIn(model, props_map, f"表单缺少 {model}")
            self.assertNotIn(
                "show", props_map[model],
                f"{model} 为两模式通用/全局项，不应带 show（否则会被误藏）",
            )

    def test_common_group_header_has_no_show(self):
        """「种子联动设置（两种模式通用）」组标题应常驻显示。"""
        hit = [p for p in self._group_headers()
               if "种子联动设置" in str(p.get("text") or "")]
        self.assertTrue(hit, "缺少「种子联动设置」组标题")
        self.assertNotIn("show", hit[0])

    def test_all_show_expressions_are_mode_conditions(self):
        """所有 show 表达式只应针对 mode，不得引用其它字段。

        限定为 ``mode === '...'`` 形式：一旦有人写了别的表达式（比如依赖
        delete_torrents），显隐逻辑会变得难以推理，且易与本用例的
        「通用项不得隐藏」约束冲突。此处守死这个口子。
        """
        for n in self._walk(self.form):
            props = n.get("props") or {}
            expr = props.get("show")
            if not expr:
                continue
            self.assertRegex(
                expr, r"^mode === '(seed|file)'$",
                f"show 表达式只允许 mode 判断，实际：{expr}",
            )


class TestLabelClarity(_FormBase):
    """label 文案：消除「模式归属」误会。"""

    def test_orphan_seed_scope_label_has_no_mode_prefix(self):
        """orphan_seed_scope 是两模式通用，label 不得带「种子级：」前缀。

        改造前它的 label 是「种子级：空壳回收覆盖监控目录外的种子」，
        但其实现由 ``_reap_orphan_seeds`` 执行，调用点在模式分派**之外**
        （check_and_clean 内、早退判定之前），两种模式都会跑 ——
        前缀纯属误导。这是用户所说「容易引起误会」的典型，必须守住。
        """
        props = self._props_by_model()["orphan_seed_scope"]
        label = str(props.get("label") or "")
        self.assertNotIn("种子级", label, f"label 不应含「种子级」：{label}")
        self.assertIn("空壳", label)

    def test_seed_group_labels_dropped_redundant_prefix(self):
        """种子级组内的 label 不应再带「种子级：」前缀（组标题已表达）。"""
        props_map = self._props_by_model()
        for model in SEED_ONLY_MODELS:
            label = str(props_map[model].get("label") or "")
            self.assertNotIn(
                "种子级", label,
                f"{model} 已在「种子级模式设置」组内，label 不应重复前缀：{label}",
            )

    def test_file_group_label_dropped_redundant_suffix(self):
        """仅文件组内的 protect_pattern label 不应再带「（仅文件模式）」。"""
        label = str(self._props_by_model()["protect_pattern"].get("label") or "")
        self.assertNotIn("仅文件", label, f"label 冗余：{label}")

    def test_common_switches_mention_both_modes(self):
        """通用开关的 hint 应明确「两种模式」，避免用户以为只对某模式有效。"""
        props_map = self._props_by_model()
        for model in ("delete_torrents", "delete_history", "orphan_seed_scope"):
            hint = str(props_map[model].get("hint") or "")
            self.assertIn(
                "两种模式", hint,
                f"{model} 为两模式通用，hint 应写明以免误会：{hint[:40]}",
            )


class TestHintPersistent(_FormBase):
    """说明文字常驻显示（persistent-hint）。

    背景
    ----
    Vuetify 的 ``VInput`` 只在 ``props.hint && (props.persistentHint ||
    props.focused)`` 时渲染 hint（源码 ``VInput.tsx`` 的 ``messages``
    computed），而 ``persistentHint`` 默认 ``false``。MoviePilot 的插件
    表单渲染器 ``FormRender.vue`` **未**给控件传 ``persistent-hint``，
    于是插件配置页的说明文字在桌面端要「点开/关闭开关」才出现（移动端
    因为不受 focus 语义约束反而直接可见）—— 这被用户当成 bug 报了过来。

    解法：插件侧显式传 ``"persistent-hint": True``。``parseProps`` 的
    ``else`` 分支会把未知 prop 原样透传给组件，故该属性能够生效。

    本类守护三件事：
    1. 每个带 ``hint`` 的控件都必须带 ``persistent-hint``（否则说明又会
       变成「要交互才出现」）；
    2. ``persistent-hint`` 的值必须是布尔 ``True`` —— 若写成字符串 ``"true"``
       会踩 ``parseProps`` 的坑：字符串值若恰好等于某个配置 key 名，会被
       当成「取配置值」而非字面量；
    3. 不带 ``hint`` 的控件不应画蛇添足地带 ``persistent-hint``。
    """

    def _controls_with_hint(self):
        """收集所有「带了非空 hint」的控件 props。"""
        out = []
        for n in self._walk(self.form):
            props = n.get("props") or {}
            if str(props.get("hint") or "").strip():
                out.append((props.get("model") or props.get("text") or "?", props))
        return out

    def test_every_hint_is_persistent(self):
        """凡有 hint 的控件，都必须带 persistent-hint: True。"""
        controls = self._controls_with_hint()
        self.assertTrue(controls, "表单里竟然没有一个带 hint 的控件？")
        missing = [name for name, p in controls if "persistent-hint" not in p]
        self.assertFalse(
            missing,
            "以下控件的说明文字会退化为「需交互才显示」，缺 persistent-hint："
            + ", ".join(missing),
        )

    def test_persistent_hint_value_is_bool_true(self):
        """persistent-hint 必须是布尔 True，不能是字符串。"""
        for name, p in self._controls_with_hint():
            if "persistent-hint" not in p:
                continue
            value = p["persistent-hint"]
            self.assertIs(
                value, True,
                f"{name} 的 persistent-hint 必须是布尔 True，实际类型 "
                f"{type(value).__name__}、值 {value!r}（字符串可能被 parseProps "
                f"当成配置 key 取值）",
            )

    def test_no_persistent_hint_without_hint(self):
        """没有 hint 的控件不应带 persistent-hint（无意义且易误导）。"""
        for n in self._walk(self.form):
            props = n.get("props") or {}
            if "persistent-hint" not in props:
                continue
            self.assertTrue(
                str(props.get("hint") or "").strip(),
                f"控件 {props.get('model')} 没有 hint，却带了 persistent-hint",
            )

    def test_persistent_hint_count(self):
        """persistent-hint 的出现次数应等于带 hint 的控件数（一一对应）。"""
        controls = self._controls_with_hint()
        total = sum(
            1 for n in self._walk(self.form)
            if "persistent-hint" in (n.get("props") or {})
        )
        self.assertEqual(
            total, len(controls),
            f"persistent-hint 出现 {total} 次，但有 {len(controls)} 个控件带 hint",
        )


class TestNoModelRegression(_FormBase):
    """改造不应增删任何 model。"""

    def test_models_unchanged(self):
        """表单 model 集合应与改造前一致（18 项），仅顺序与分组变化。"""
        models = set(self._props_by_model())
        expected = set(ALWAYS_VISIBLE_MODELS) | set(SEED_ONLY_MODELS) | set(FILE_ONLY_MODELS)
        self.assertEqual(models, expected, f"model 集合发生变化：{models ^ expected}")

    def test_every_model_in_default_config(self):
        """每个 model 都应能在默认配置中取到（复用既有契约）。"""
        missing = set(self._props_by_model()) - set(self.default)
        self.assertFalse(missing, f"表单字段在默认配置中缺失：{missing}")


if __name__ == "__main__":
    unittest.main()
