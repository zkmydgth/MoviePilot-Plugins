# -*- coding: utf-8 -*-
"""
综艺正片守卫回归测试。

覆盖四类风险（每条都对应一处可能写错的判定，变异脚本会逐条攻击）：

  A. 作用域：只在「电视剧 + 分类含综艺」生效，其它媒体一律放行
  B. 判定：排除词命中才跳过；白名单优先；坏正则不得崩
  C. 静默语义：全为非正片时改写成宿主的「无候选」跳过（不产生失败记录）
  D. 安全边界：试运行不过滤、异常放行、补丁可装可卸、失配不抛
  E. 词表同步：镜像写入目标规则、已一致时不写、缺配置/缺规则/词表异常/写入失败都不得动规则
"""

import asyncio
import re
import unittest

import tests  # noqa: F401  触发宿主桩路径注入

from app.application import configuration as stub_configuration
from app.application import rules as stub_rules
from app.modules.filemanager import transhandler as stub_transhandler
from app.schemas.types import MediaType, SystemConfigKey
from app.sdk import scheduler as stub_scheduler
from varietyguard import VarietyGuard


class FakeMedia:
    """媒体信息替身：只提供插件判定用到的字段。"""

    def __init__(self, title="现在就出发", year="2023", mtype=MediaType.TV,
                 library_category="综艺", category="综艺"):
        self.title = title
        self.year = year
        self.type = mtype
        self.library_category = library_category
        self.category = category
        self.metadata_category = ""


class VarietyGuardTestCase(unittest.TestCase):
    """插件核心行为的公共夹具。"""

    def setUp(self) -> None:
        """为每个用例准备全新插件实例，避免状态串味。"""
        self.plugin = VarietyGuard()

    def tearDown(self) -> None:
        """卸载补丁，避免污染其它用例。"""
        self.plugin.stop_service()

    def _enable(self, **overrides):
        """按配置启用插件。"""
        config = {
            "enabled": True,
            "dry_run": False,
            "exclude_keywords": "先导\n花絮\n加更\n纯享\nPrologue",
            "allow_keywords": "正片",
            "scope_media_types": "电视剧",
            "scope_categories": "综艺",
        }
        config.update(overrides)
        self.plugin.init_plugin(config)
        return self.plugin

    def _plan(self, mediainfo, items):
        """调用宿主（已被包装）的整理计划方法。"""
        stub_transhandler.set_pending_items(items)
        return stub_transhandler.TransHandler().plan_transfer(
            planning_input=None, meta=None, mediainfo=mediainfo
        )

    # ---------------- A. 作用域 ----------------

    def test_variety_skips_non_main_episode(self):
        """综艺整季包：先导片被剔除，正片保留。"""
        self._enable()
        checkpoint = self._plan(
            FakeMedia(),
            [
                ("现在就出发.正片.S04E01.mkv", "/dl/现在就出发.S04/现在就出发.正片.S04E01.mkv"),
                ("现在就出发.先导片.S04E01.Prologue.mkv",
                 "/dl/现在就出发.S04/现在就出发.先导片.S04E01.Prologue.mkv"),
            ],
        )
        names = [item.source_fileitem["name"] for item in checkpoint.items]
        self.assertEqual(names, ["现在就出发.正片.S04E01.mkv"])
        self.assertEqual([item.sequence for item in checkpoint.items], [0])
        self.assertIsNone(checkpoint.skip_reason)

    def test_other_media_types_untouched(self):
        """非综艺内容（分类=国产剧）命中同名词也不得被过滤。"""
        self._enable()
        checkpoint = self._plan(
            FakeMedia(title="某剧", library_category="国产剧", category="国产剧"),
            [
                ("某剧.花絮.S01E01.mkv", "/dl/某剧.S01/某剧.花絮.S01E01.mkv"),
                ("某剧.S01E02.mkv", "/dl/某剧.S01/某剧.S01E02.mkv"),
            ],
        )
        self.assertEqual(len(checkpoint.items), 2)

    def test_movie_media_type_untouched(self):
        """电影类型不在作用域内。"""
        self._enable()
        checkpoint = self._plan(
            FakeMedia(title="某电影", mtype=MediaType.MOVIE,
                      library_category="外语电影", category="外语电影"),
            [("某电影.预告.mkv", "/dl/某电影.预告.mkv")],
        )
        self.assertEqual(len(checkpoint.items), 1)

    def test_scope_categories_empty_means_all(self):
        """分类作用域留空时只按媒体类型判定。"""
        self._enable(scope_categories="")
        checkpoint = self._plan(
            FakeMedia(title="某剧", library_category="国产剧", category="国产剧"),
            [("某剧.花絮.S01E01.mkv", "/dl/某剧.花絮.S01E01.mkv")],
        )
        self.assertEqual(len(checkpoint.items), 0)
        self.assertTrue(checkpoint.skip_reason)

    # ---------------- B. 判定 ----------------

    def test_allowlist_wins_over_exclude(self):
        """白名单优先：同时含「正片」与「花絮」时保留。"""
        self._enable()
        checkpoint = self._plan(
            FakeMedia(),
            [("现在就出发.S04E01.正片花絮.mkv", "/dl/现在就出发.S04/现在就出发.S04E01.正片花絮.mkv")],
        )
        self.assertEqual(len(checkpoint.items), 1)

    def test_invalid_regex_is_ignored(self):
        """非法正则不得抛错，也不得误判为命中。"""
        self._enable(exclude_keywords="[\n先导")
        checkpoint = self._plan(
            FakeMedia(),
            [
                ("现在就出发.S04E01.mkv", "/dl/现在就出发.S04/现在就出发.S04E01.mkv"),
                ("现在就出发.先导片.S04E01.mkv", "/dl/现在就出发.S04/现在就出发.先导片.S04E01.mkv"),
            ],
        )
        self.assertEqual(len(checkpoint.items), 1)

    def test_match_full_path_mode(self):
        """开启路径匹配时，目录名含关键词会被一并命中（已知风险行为）。"""
        self._enable(match_full_path=True)
        checkpoint = self._plan(
            FakeMedia(),
            [
                ("现在就出发.S04E01.mkv", "/dl/现在就出发.先导片合集/现在就出发.S04E01.mkv"),
                ("现在就出发.S04E02.mkv", "/dl/现在就出发/现在就出发.S04E02.mkv"),
            ],
        )
        self.assertEqual(len(checkpoint.items), 1)
        self.assertEqual(checkpoint.items[0].source_fileitem["name"], "现在就出发.S04E02.mkv")

    # ---------------- C. 静默语义 ----------------

    def test_all_non_main_rewrites_to_skip_reason(self):
        """候选全为非正片时改写成宿主的无候选静默跳过（不产生失败）。"""
        self._enable()
        checkpoint = self._plan(
            FakeMedia(),
            [
                ("现在就出发.先导片.S04E01.mkv", "/dl/现在就出发.S04/现在就出发.先导片.S04E01.mkv"),
                ("现在就出发.纯享.S04E01.mkv", "/dl/现在就出发.S04/现在就出发.纯享.S04E01.mkv"),
            ],
        )
        self.assertEqual(checkpoint.items, ())
        self.assertIn("非正片", checkpoint.skip_reason or "")

    def test_no_hit_keeps_checkpoint_identity(self):
        """没有命中时不做任何改写。"""
        self._enable()
        checkpoint = self._plan(
            FakeMedia(),
            [("现在就出发.S04E01.mkv", "/dl/现在就出发.S04/现在就出发.S04E01.mkv")],
        )
        self.assertEqual(len(checkpoint.items), 1)
        self.assertIsNone(checkpoint.skip_reason)

    def test_single_file_plan_untouched(self):
        """单片计划没有逐文件候选，插件不介入。"""
        self._enable()
        checkpoint = self._plan(FakeMedia(), [])
        self.assertEqual(checkpoint.items, ())
        self.assertEqual(checkpoint.skip_reason, "源目录中没有可整理文件")

    def test_stats_and_records_written(self):
        """过滤后写入统计与记录。"""
        self._enable()
        self._plan(
            FakeMedia(),
            [("现在就出发.先导片.S04E01.mkv", "/dl/x/现在就出发.先导片.S04E01.mkv")],
        )
        stats = self.plugin.get_data("stats")
        self.assertEqual(stats["total_skipped"], 1)
        self.assertEqual(stats["by_keyword"]["先导"], 1)
        records = self.plugin.get_data("records")
        self.assertEqual(records[0]["keyword"], "先导")
        self.assertTrue(records[0]["applied"])

    def test_notify_only_when_disabled(self):
        """通知开关关闭时不发送消息。"""
        self._enable()
        self._plan(FakeMedia(), [("现在就出发.先导片.S04E01.mkv", "/dl/x/先导片.mkv")])
        self.assertEqual(self.plugin.sent_messages, [])

    def test_notify_when_enabled(self):
        """通知开关打开时发送一条跳过消息。"""
        self._enable(notify=True)
        self._plan(FakeMedia(), [("现在就出发.先导片.S04E01.mkv", "/dl/x/先导片.mkv")])
        self.assertEqual(len(self.plugin.sent_messages), 1)
        self.assertIn("跳过", self.plugin.sent_messages[0]["title"])

    # ---------------- D. 安全边界 ----------------

    def test_dry_run_records_but_keeps_items(self):
        """试运行只记录，不执行过滤。"""
        self._enable(dry_run=True)
        checkpoint = self._plan(
            FakeMedia(),
            [("现在就出发.先导片.S04E01.mkv", "/dl/x/现在就出发.先导片.S04E01.mkv")],
        )
        self.assertEqual(len(checkpoint.items), 1)
        records = self.plugin.get_data("records")
        self.assertFalse(records[0]["applied"])

    def test_fail_open_on_internal_error(self):
        """内部异常必须放行原始计划。"""
        self._enable()
        original_text = VarietyGuard._item_text
        VarietyGuard._item_text = lambda self, item: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            checkpoint = self._plan(
                FakeMedia(),
                [("现在就出发.先导片.S04E01.mkv", "/dl/x/现在就出发.先导片.S04E01.mkv")],
            )
        finally:
            VarietyGuard._item_text = original_text
        self.assertEqual(len(checkpoint.items), 1)

    def test_missing_mediainfo_fail_open(self):
        """取不到媒体信息时不得过滤。"""
        self._enable()
        checkpoint = self._plan(None, [("现在就出发.先导片.mkv", "/dl/x/先导片.mkv")])
        self.assertEqual(len(checkpoint.items), 1)

    def test_patch_install_and_restore(self):
        """启用时装补丁、停用时还原原方法。"""
        original = stub_transhandler.TransHandler.plan_transfer
        self._enable()
        patched = stub_transhandler.TransHandler.plan_transfer
        self.assertIsNot(patched, original)
        self.plugin.stop_service()
        self.assertIs(stub_transhandler.TransHandler.plan_transfer, original)

    def test_reinit_does_not_stack_patches(self):
        """重复初始化不得叠加包装（原方法只被调用一次）。"""
        self._enable()
        self._enable()
        stub_transhandler.ORIGINAL_PLAN_CALLS = 0
        self._plan(FakeMedia(library_category="国产剧"), [("x.mkv", "/dl/x.mkv")])
        self.assertEqual(stub_transhandler.ORIGINAL_PLAN_CALLS, 1)

    def test_seam_missing_disables_without_raise(self):
        """宿主方法缺失时只标记失败，不抛异常。"""
        original = stub_transhandler.TransHandler.plan_transfer
        del stub_transhandler.TransHandler.plan_transfer
        try:
            self._enable()
        finally:
            stub_transhandler.TransHandler.plan_transfer = original
        self.assertEqual(self.plugin._patch_state, "failed")
        self.assertIn("plan_transfer", self.plugin._patch_message)

    def test_disabled_plugin_does_not_filter(self):
        """未启用时不装补丁，也不过滤。"""
        self.plugin.init_plugin({"enabled": False})
        checkpoint = self._plan(FakeMedia(), [("现在就出发.先导片.mkv", "/dl/x/先导片.mkv")])
        self.assertEqual(len(checkpoint.items), 1)

    def test_original_checkpoint_not_mutated(self):
        """过滤走 dataclasses.replace，不就地修改宿主计划对象。"""
        plugin = self._enable()
        original = plugin._original_plan_transfer
        self.assertIsNotNone(original)
        stub_transhandler.set_pending_items(
            [("现在就出发.先导片.S04E01.mkv", "/dl/x/先导片.mkv")]
        )
        handler = stub_transhandler.TransHandler()
        raw = original(handler, None, meta=None, mediainfo=FakeMedia())
        filtered = self._plan(FakeMedia(), [("现在就出发.先导片.S04E01.mkv", "/dl/x/先导片.mkv")])
        self.assertEqual(len(raw.items), 1)
        self.assertEqual(filtered.items, ())

    def test_form_and_page_shapes(self):
        """配置表单与详情页结构可渲染（hint 布尔、无「使用说明」组标题）。"""
        form, defaults = VarietyGuard().get_form()
        self.assertTrue(form and isinstance(defaults, dict))
        blob = repr(form)
        self.assertNotIn("使用说明", blob)
        self.assertIn("'persistent-hint': True", blob)
        self.assertTrue(self._enable().get_page())

    def test_command_definitions(self):
        """远程命令定义完整。"""
        commands = VarietyGuard.get_command()
        self.assertEqual(
            {item["data"]["action"] for item in commands},
            {"varietyguard_status", "varietyguard_reset"},
        )

    def test_reset_command_clears_stats(self):
        """重置命令清空统计。"""
        plugin = self._enable()
        self._plan(FakeMedia(), [("现在就出发.先导片.mkv", "/dl/x/先导片.mkv")])

        class _Event:
            event_data = {"action": "varietyguard_reset"}

        plugin.on_plugin_action(_Event())
        self.assertEqual(plugin.get_data("stats"), {})
        self.assertEqual(plugin.get_data("records"), [])


    # ---------------- E. 边界 / 异常 ----------------

    def test_corrupted_stats_does_not_break_filtering(self):
        """统计/记录数据损坏时不影响过滤，也不抛异常。"""
        self._enable()
        self.plugin.save_data("stats", "not-a-dict")
        self.plugin.save_data("records", {"bad": "shape"})
        checkpoint = self._plan(
            FakeMedia(), [("现在就出发.先导片.S04E01.mkv", "/dl/x/先导片.mkv")]
        )
        self.assertEqual(checkpoint.items, ())
        self.assertEqual(self.plugin.get_data("stats")["total_skipped"], 1)

    def test_keep_records_zero_keeps_stats_only(self):
        """保留记录为 0 时只累计统计、不写记录。"""
        self._enable(keep_records=0)
        self._plan(FakeMedia(), [("现在就出发.先导片.S04E01.mkv", "/dl/x/先导片.mkv")])
        self.assertIsNone(self.plugin.get_data("records"))
        self.assertEqual(self.plugin.get_data("stats")["total_skipped"], 1)

    def test_case_insensitive_match(self):
        """关键词匹配忽略大小写。"""
        self._enable(exclude_keywords="prologue")
        checkpoint = self._plan(
            FakeMedia(), [("Show.S04E01.PROLOGUE.mkv", "/dl/Show/PROLOGUE.mkv")]
        )
        self.assertEqual(checkpoint.items, ())

    def test_keyword_value_accepts_form_list_shape(self):
        """关键词配置兼容表单可能回传的列表/对象形态。"""
        self._enable(exclude_keywords=["先导", {"value": "纯享"}], allow_keywords=[])
        checkpoint = self._plan(
            FakeMedia(),
            [
                ("a.先导片.mkv", "/dl/a.先导片.mkv"),
                ("b.纯享.mkv", "/dl/b.纯享.mkv"),
                ("c.S04E01.mkv", "/dl/c.S04E01.mkv"),
            ],
        )
        self.assertEqual([item.source_fileitem["name"] for item in checkpoint.items],
                         ["c.S04E01.mkv"])

    def test_item_without_source_identity_is_kept(self):
        """计划项缺少源文件身份时按放行处理，不抛异常。"""
        from app.application.transfer.models import (
            TransferPlanCheckpoint,
            TransferPlanItem,
        )

        self._enable()
        raw = TransferPlanCheckpoint(
            items=(
                TransferPlanItem(
                    sequence=0,
                    source_fileitem={},
                    target_path="/library/现在就出发.先导片.S04E01.mkv",
                ),
            )
        )
        filtered = self.plugin._filter_checkpoint(raw, (), {"mediainfo": FakeMedia()})
        self.assertEqual(len(filtered.items), 1)

    def test_scope_matches_metadata_category(self):
        """分类作用域同时匹配 metadata_category。"""
        media = FakeMedia(title="某综艺", library_category="", category="")
        media.metadata_category = "综艺"
        self._enable()
        checkpoint = self._plan(
            media, [("某综艺.先导片.S01E01.mkv", "/dl/某综艺.先导片.S01E01.mkv")]
        )
        self.assertEqual(checkpoint.items, ())

    def test_default_keyword_list_integrity(self):
        """默认词表：61 项、无重复、全部合法正则；ASCII 项带词边界（E00/EP00 例外）、中文项不带。"""
        from varietyguard import DEFAULT_EXCLUDE_KEYWORDS

        self.assertEqual(len(DEFAULT_EXCLUDE_KEYWORDS), 61)
        self.assertEqual(len(set(DEFAULT_EXCLUDE_KEYWORDS)), 61)
        for pattern in DEFAULT_EXCLUDE_KEYWORDS:
            re.compile(pattern)  # 非法正则会直接抛错
        chinese_items = [w for w in DEFAULT_EXCLUDE_KEYWORDS if not w.isascii()]
        ascii_items = [w for w in DEFAULT_EXCLUDE_KEYWORDS if w.isascii()]
        self.assertEqual(len(chinese_items) + len(ascii_items), 61)
        # v1.0.7：与「中文 35 + 英文/数字 26」分组一致，且并入 Before
        self.assertEqual(len(chinese_items), 35)
        self.assertEqual(len(ascii_items), 26)
        self.assertIn("(?<![A-Za-z])Before(?![A-Za-z])", DEFAULT_EXCLUDE_KEYWORDS)
        self.assertIn("独家", DEFAULT_EXCLUDE_KEYWORDS)
        for item in ascii_items:
            if item in {"E00", "EP00"}:
                continue  # 这两条按设计不加词边界（见 test_bare_episode_zero_keywords）
            self.assertTrue(item.startswith("(?<!"), f"{item} 缺少左词边界")
        for item in chinese_items:
            self.assertFalse(item.startswith("(?<!"), f"{item} 中文词不应加词边界")

    def test_bare_episode_zero_keywords(self):
        """E00 / EP00 刻意不加词边界：能命中 S01E00、S01E0012、EP00 这类串。"""
        from varietyguard import DEFAULT_EXCLUDE_KEYWORDS

        plugin = VarietyGuard()
        self.assertIn("E00", DEFAULT_EXCLUDE_KEYWORDS)
        self.assertIn("EP00", DEFAULT_EXCLUDE_KEYWORDS)
        for name in ("Show.S01E00.mkv", "Show.S01E0012.mkv", "Show.EP00.mkv"):
            self.assertIsNotNone(
                plugin._match_first(DEFAULT_EXCLUDE_KEYWORDS, name), f"{name} 应命中"
            )

    def test_added_markers_hit_real_non_main(self):
        """v1.0.4 新增词：Extra / EX / 尝鲜篇 / 森林体验篇 必须命中对应命名。"""
        from varietyguard import DEFAULT_EXCLUDE_KEYWORDS

        plugin = VarietyGuard()
        for name in (
            "[现在就出发].Natural.High.2023.S01E05.Extra.2160p.WEB-DL.HEVC.DDP2Audios-QHstudio.mp4",
            "Natural.High.S01E05.EX1.20230816.2160p.WEB-DL.H265.AAC-CHDWEB.mp4",
            "[20230806][现在就出发 第一季 尝鲜篇].Natural.High.Appetizer.2023.S01E01.2160p.WEB-DL.H265.AAC-UBWEB.mp4",
            "[20230730][现在就出发 第一季 森林体验篇].Natural.High.Forest.Experience.2023.S01E01.2160p.WEB-DL.H265.AAC-UBWEB.mp4",
        ):
            self.assertIsNotNone(
                plugin._match_first(DEFAULT_EXCLUDE_KEYWORDS, name), f"{name} 应命中"
            )

    def test_added_markers_hit_real_non_main_v105(self):
        """v1.0.5 新增词：超前 / 尊享版 / 陪看 / 直拍 / 探班 / 直播 / Live / Pilot / Fancam 必须命中对应命名。"""
        from varietyguard import DEFAULT_EXCLUDE_KEYWORDS

        plugin = VarietyGuard()
        for name in (
            # S02 观众站 ADWeb（描述含「加更|特辑|超前」）
            "现在就出发.超前.Natural.High.S02E01.2024.2160p.TX.WEB-DL.H265.DDP2.0-ADWeb.mp4",
            # S03 观众站 ADWeb 五类命名
            "现在就出发.直播.Natural.High.S03E03.Live.2025-ADWeb.mp4",
            "现在就出发.直拍.Natural.High.S03E28.Fancam.2025-ADWeb.mp4",
            # 只写英文标签、无中文词的变体（原词表会漏）
            "现在就出发.Natural.High.S03E01.Live.2025-ADWeb.mp4",
            "现在就出发.Natural.High.S03E01.Pilot.2025-ADWeb.mp4",
            "现在就出发.Natural.High.S03E01.Fancam.2025-ADWeb.mp4",
            # 其余中文衍生词
            "现在就出发.尊享版.Natural.High.S02E02.2024-ADWeb.mp4",
            "现在就出发.陪看.Natural.High.S02E03.2024-ADWeb.mp4",
            "现在就出发.探班.Natural.High.S02E04.2024-ADWeb.mp4",
        ):
            self.assertIsNotNone(
                plugin._match_first(DEFAULT_EXCLUDE_KEYWORDS, name), f"{name} 应命中"
            )

    def test_added_markers_do_not_hit_word_containing_strings(self):
        """v1.0.5 新增英文词的词边界生效：LiveHouse / Pilotlight / FancamX 与纯正片名不得被误伤。"""
        from varietyguard import DEFAULT_EXCLUDE_KEYWORDS

        plugin = VarietyGuard()
        for name in (
            "现在就出发.Natural.High.S03E01.LiveHouse.2025-ADWeb.mp4",
            "现在就出发.Natural.High.S03E01.Pilotlight.2025-ADWeb.mp4",
            "现在就出发.Natural.High.S03E01.FancamX.2025-ADWeb.mp4",
            "现在就出发.Natural.High.S03E15.2025-ADWeb.mp4",
            "现在就出发.Natural.High.S03E15.正片.2025-ADWeb.mp4",
        ):
            self.assertIsNone(
                plugin._match_first(DEFAULT_EXCLUDE_KEYWORDS, name), f"{name} 不应命中"
            )

    def test_added_markers_do_not_hit_main_episodes(self):
        """v1.0.4 新增词不得误伤正片：Part1/Part2/VIP1/VIP2/第X期 上/下，及含词串（Extraction/Extension）。"""
        from varietyguard import DEFAULT_EXCLUDE_KEYWORDS

        plugin = VarietyGuard()
        for name in (
            "现在就出发.Natural.High.S01E01.Part1.2023.2160p.WEB-DL.H265.AAC-ADWeb.mp4",
            "[现在就出发].Natural.High.2023.S01E01.Part1.2160p.WEB-DL.HEVC.DDP2Audios-QHstudio.mp4",
            "Natural.High.S01E01.VIP1.20230813.2160p.WEB-DL.H265.AAC-CHDWEB.mp4",
            "[20230813][现在就出发 第一季 第01期 上].Natural.High.2023.S01E01.Part01.2160p.WEB-DL.H265.AAC-UBWEB.mp4",
            "Xian Zai Jiu Chu Fa 2023 S01E01.Part1 2160p WEB-DL H265 AAC-PTerWEB.mp4",
            "Extraction.2020.1080p.WEB-DL.mkv",
            "Extension.2024.S01E01.1080p.mkv",
        ):
            self.assertIsNone(
                plugin._match_first(DEFAULT_EXCLUDE_KEYWORDS, name), f"{name} 不应命中"
            )

    def test_default_boundary_does_not_hit_word_containing_strings(self):
        """词边界：含词串（MAXPLUS / StartUp / Clubhouse / Reactionary）不得命中。"""
        from varietyguard import DEFAULT_EXCLUDE_KEYWORDS

        plugin = VarietyGuard()
        for name in (
            "[风声].The.Message.S01E01.2160p.60Fps.MAXPLUS.H265.mp4",
            "StartUp.S01E03.1080p.mkv",
            "Clubhouse.S01E01.mkv",
            "Reactionary.S01E02.mkv",
        ):
            self.assertIsNone(
                plugin._match_first(DEFAULT_EXCLUDE_KEYWORDS, name), f"{name} 不应命中"
            )

    def test_default_boundary_hits_real_markers(self):
        """词边界：真标记仍必须命中（含多词短语与集号 E00/EP00）。"""
        from varietyguard import DEFAULT_EXCLUDE_KEYWORDS

        plugin = VarietyGuard()
        for name in (
            "现在就出发.S04E01.Plus.2160p.mkv",
            "Show.Rapid.Case.1080p.mkv",
            "Show.Rapid Case.1080p.mkv",
            "Show.Detective.Club.S01E01.mkv",
            "Show.S01E00.mkv",
            "Show.EP00.mkv",
            "现在就出发.S04E01.Pure.mkv",
            "现在就出发.S04E01.Prologue.mkv",
        ):
            self.assertIsNotNone(
                plugin._match_first(DEFAULT_EXCLUDE_KEYWORDS, name), f"{name} 应命中"
            )

    def test_default_keywords_still_catch_real_non_main(self):
        """默认词表：中文非正片词（含本次新增）仍必须命中。"""
        from varietyguard import DEFAULT_EXCLUDE_KEYWORDS

        plugin = VarietyGuard()
        for name in ("现在就出发.先导片.S04E01.mkv", "现在就出发.花絮.S04E01.mkv",
                     "现在就出发.纯享版.S04E01.mkv", "现在就出发.巅峰.S04E01.mkv",
                     "现在就出发.盛典.S04E01.mkv", "现在就出发.独家.S04E01.mkv",
                     "现在就出发.未播.S04E01.mkv", "现在就出发.加更.S04E01.mkv"):
            self.assertIsNotNone(
                plugin._match_first(DEFAULT_EXCLUDE_KEYWORDS, name), f"{name} 应命中"
            )

    def test_checkpoint_items_not_iterator(self):
        """items 为空元组时原样返回（不误判成需要过滤）。"""
        from app.application.transfer.models import TransferPlanCheckpoint

        self._enable()
        raw = TransferPlanCheckpoint(items=())
        self.assertIs(
            self.plugin._filter_checkpoint(raw, (), {"mediainfo": FakeMedia()}), raw
        )

    # ---------------- E. 词表同步（可选能力） ----------------

    def test_rule_sync_mirrors_keywords_into_rule(self):
        """填了 sync_rule_id：词表被整条镜像写入目标规则，并留档同步记录。"""
        stub_rules.RULES[:] = [{"id": "RULE4", "name": "排除综艺非正片", "exclude": "旧词|Plus"}]
        stub_configuration.SERVICE.writes.clear()
        stub_scheduler.JOBS.clear()
        self._enable(enabled=False, sync_rule_id="RULE4", exclude_keywords=None)
        # 未启用也应安排同步：词表一致性与「是否过滤」无关
        self.assertEqual(len(stub_scheduler.JOBS), 1)
        self.assertEqual(stub_scheduler.JOBS[0]["func"].__name__, "_sync_rule_keywords")
        asyncio.run(self.plugin._sync_rule_keywords())
        self.assertEqual(len(stub_configuration.SERVICE.writes), 1)
        key, value = stub_configuration.SERVICE.writes[0]
        self.assertEqual(key, SystemConfigKey.CustomFilterRules)
        mirror = next(rule for rule in value if rule["id"] == "RULE4")
        self.assertEqual(mirror["exclude"], "|".join(self.plugin._exclude_keywords))
        record = self.plugin.get_data("rule_sync")
        self.assertEqual(record["rule_id"], "RULE4")
        self.assertEqual(record["count"], len(self.plugin._exclude_keywords))
        self.assertIn("直播", record["added"])
        self.assertIn("旧词", record["removed"])

    def test_rule_sync_noop_when_rule_already_matches(self):
        """规则文本已与词表一致时不写入（不产生无谓的配置变更事件）。"""
        self._enable(enabled=False, sync_rule_id="RULE4", exclude_keywords=None)
        mirror = "|".join(self.plugin._exclude_keywords)
        stub_rules.RULES[:] = [{"id": "RULE4", "name": "排除综艺非正片", "exclude": mirror}]
        stub_configuration.SERVICE.writes.clear()
        asyncio.run(self.plugin._sync_rule_keywords())
        self.assertEqual(stub_configuration.SERVICE.writes, [])

    def test_rule_sync_disabled_without_rule_id(self):
        """未填写 rule_id：不安排任务、也不写入任何配置。"""
        stub_scheduler.JOBS.clear()
        stub_configuration.SERVICE.writes.clear()
        self._enable(enabled=False)
        self.assertEqual(stub_scheduler.JOBS, [])
        asyncio.run(self.plugin._sync_rule_keywords())
        self.assertEqual(stub_configuration.SERVICE.writes, [])

    def test_rule_sync_ignores_missing_target_rule(self):
        """目标规则不存在时只跳过：不新建规则、不写入。"""
        stub_rules.RULES[:] = [{"id": "RULE1", "name": "!HQ", "exclude": "HQ"}]
        stub_configuration.SERVICE.writes.clear()
        self._enable(enabled=False, sync_rule_id="RULE9", exclude_keywords=None)
        asyncio.run(self.plugin._sync_rule_keywords())
        self.assertEqual(stub_configuration.SERVICE.writes, [])

    def test_rule_sync_guard_rejects_abnormal_keywords(self):
        """词表项数过少时触发安全闸：绝不把异常词表写进目标规则。"""
        stub_rules.RULES[:] = [{"id": "RULE4", "name": "排除综艺非正片", "exclude": "旧词"}]
        stub_configuration.SERVICE.writes.clear()
        self._enable(enabled=False, sync_rule_id="RULE4", exclude_keywords="先导\n花絮")
        asyncio.run(self.plugin._sync_rule_keywords())
        self.assertEqual(stub_configuration.SERVICE.writes, [])

    def test_rule_sync_failure_is_fail_open(self):
        """写入抛错时不得向上抛（fail-open），且不改变插件自身状态。"""
        stub_rules.RULES[:] = [{"id": "RULE4", "name": "排除综艺非正片", "exclude": "旧词"}]
        self._enable(enabled=False, sync_rule_id="RULE4", exclude_keywords=None)
        stub_configuration.SERVICE.fail_on_write = True
        try:
            asyncio.run(self.plugin._sync_rule_keywords())
        finally:
            stub_configuration.SERVICE.fail_on_write = False

    def test_form_exposes_sync_rule_id(self):
        """配置表单与默认模型都暴露 sync_rule_id（留空=关闭）。"""
        form, model = self.plugin.get_form()
        self.assertTrue(form)
        self.assertIn("sync_rule_id", model)
        self.assertEqual(model["sync_rule_id"], "")


class RestoreDefaultKeywordsTestCase(unittest.TestCase):
    """F. 恢复默认关键词：配置表单按钮、详情页按钮与 API 三条入口。"""

    def setUp(self) -> None:
        """为每个用例准备全新插件实例。"""
        self.plugin = VarietyGuard()

    def tearDown(self) -> None:
        """卸载补丁，避免污染其它用例。"""
        self.plugin.stop_service()

    def test_form_button_uses_client_script(self):
        """配置表单按钮：走 props.onClick 字符串脚本（含 confirm 守卫），且不得出现表单渲染器不支持的 events。"""
        form, _defaults = VarietyGuard().get_form()
        blob = repr(form)
        self.assertIn("恢复默认关键词", blob)
        self.assertIn("model.exclude_keywords", blob)
        self.assertIn("confirm(", blob)
        self.assertNotIn("'events'", blob)

    def test_form_button_payload_equals_defaults(self):
        """按钮脚本内联的词表必须与内置默认逐项一致，否则「恢复默认」会填错内容。"""
        import json as _json

        from varietyguard import DEFAULT_ALLOW_KEYWORDS, DEFAULT_EXCLUDE_KEYWORDS

        script = VarietyGuard._restore_keywords_js()
        exclude_raw = script.split("model.exclude_keywords = ", 1)[1].split(".join(", 1)[0]
        allow_raw = script.split("model.allow_keywords = ", 1)[1].split(".join(", 1)[0]
        self.assertEqual(_json.loads(exclude_raw), list(DEFAULT_EXCLUDE_KEYWORDS))
        self.assertEqual(_json.loads(allow_raw), list(DEFAULT_ALLOW_KEYWORDS))

    def test_default_keywords_text_shape(self):
        """默认词表文本形态：与常量逐项一致（供配置文本框与 API 写回共用）。"""
        from varietyguard import DEFAULT_ALLOW_KEYWORDS, DEFAULT_EXCLUDE_KEYWORDS

        payload = VarietyGuard.default_keywords()
        self.assertEqual(payload["exclude_keywords"].splitlines(), list(DEFAULT_EXCLUDE_KEYWORDS))
        self.assertEqual(payload["allow_keywords"].splitlines(), list(DEFAULT_ALLOW_KEYWORDS))

    def test_api_requires_confirmation(self):
        """两阶段：第一次调用只写待确认，配置与运行期词表都不得变化。"""
        self.plugin.init_plugin({"enabled": True, "exclude_keywords": "只留这一个"})

        first = self.plugin.api_restore_default_keywords()

        self.assertTrue(first["success"])
        self.assertIn("确认", first["message"])
        self.assertIsNotNone(self.plugin._restore_pending())
        self.assertEqual(self.plugin._exclude_keywords, ["只留这一个"])

    def test_api_confirm_writes_defaults_and_keeps_other_fields(self):
        """确认阶段：写回默认词表、保留其它配置项，并让运行期词表立即生效。"""
        from varietyguard import DEFAULT_ALLOW_KEYWORDS, DEFAULT_EXCLUDE_KEYWORDS

        self.plugin.update_config({"enabled": True, "notify": True, "sync_rule_id": "RULE4"})
        self.plugin.init_plugin({"enabled": True, "exclude_keywords": "只留这一个"})
        self.plugin.api_restore_default_keywords()

        result = self.plugin.api_restore_default_keywords(confirm="1")

        self.assertTrue(result["success"])
        saved = self.plugin.get_config()
        self.assertEqual(saved["exclude_keywords"].splitlines(), list(DEFAULT_EXCLUDE_KEYWORDS))
        self.assertEqual(saved["allow_keywords"].splitlines(), list(DEFAULT_ALLOW_KEYWORDS))
        self.assertTrue(saved["notify"])
        self.assertEqual(saved["sync_rule_id"], "RULE4")
        self.assertEqual(self.plugin._exclude_keywords, list(DEFAULT_EXCLUDE_KEYWORDS))
        self.assertEqual(self.plugin._allow_keywords, list(DEFAULT_ALLOW_KEYWORDS))
        self.assertIsNone(self.plugin._restore_pending())

    def test_api_confirm_without_pending_fails(self):
        """没先选中就直接确认：必须拒绝，且不得改动配置。"""
        self.plugin.init_plugin({"enabled": True, "exclude_keywords": "只留这一个"})

        result = self.plugin.api_restore_default_keywords(confirm="1")

        self.assertFalse(result["success"])
        self.assertEqual(self.plugin._exclude_keywords, ["只留这一个"])

    def test_api_cancel_clears_pending(self):
        """取消：清除待确认状态，且不得改动配置。"""
        self.plugin.init_plugin({"enabled": True, "exclude_keywords": "只留这一个"})
        self.plugin.api_restore_default_keywords()

        result = self.plugin.api_restore_default_keywords(confirm="cancel")

        self.assertTrue(result["success"])
        self.assertIsNone(self.plugin._restore_pending())
        self.assertEqual(self.plugin._exclude_keywords, ["只留这一个"])

    def test_pending_expires(self):
        """待确认超过 TTL 自动作废：过期后确认必须失败。"""
        import time as _time

        self.plugin.init_plugin({"enabled": True, "exclude_keywords": "只留这一个"})
        self.plugin.save_data("restore_pending", {"count": 61, "time": _time.time() - 601})

        self.assertIsNone(self.plugin._restore_pending())
        self.assertFalse(self.plugin.api_restore_default_keywords(confirm="1")["success"])
        self.assertEqual(self.plugin._exclude_keywords, ["只留这一个"])

    def test_api_reports_save_failure(self):
        """确认阶段保存异常必须回告失败，不得静默当成功。"""
        self.plugin.init_plugin({"enabled": True, "exclude_keywords": "只留这一个"})
        self.plugin.api_restore_default_keywords()

        def boom(config, plugin_id=None):
            raise RuntimeError("磁盘满了")

        self.plugin.update_config = boom  # type: ignore[assignment]
        result = self.plugin.api_restore_default_keywords(confirm="1")

        self.assertFalse(result["success"])
        self.assertIn("磁盘满了", result["message"])

    def test_page_shows_two_phase_buttons(self):
        """详情页两阶段：未选中时单按钮；选中后出现「确认恢复 / 取消」并带 confirm 参数。"""
        idle = repr(self.plugin.get_page() or [])
        self.assertIn("恢复默认关键词", idle)
        self.assertIn("plugin/VarietyGuard/restore_default_keywords", idle)
        self.assertIn("'method': 'get'", idle)
        self.assertIn("apikey", idle)
        self.assertNotIn("确认恢复", idle)

        self.plugin.api_restore_default_keywords()

        armed = repr(self.plugin.get_page() or [])
        self.assertIn("确认恢复", armed)
        self.assertIn("取消", armed)
        self.assertIn("'confirm': '1'", armed)
        self.assertIn("'confirm': 'cancel'", armed)

    def test_api_registered(self):
        """插件 API 列表暴露恢复端点（详情页按钮依赖它）。"""
        apis = VarietyGuard().get_api()
        self.assertEqual({item["path"] for item in apis}, {"/restore_default_keywords"})
        self.assertEqual(apis[0]["methods"], ["GET"])


if __name__ == "__main__":
    unittest.main()
