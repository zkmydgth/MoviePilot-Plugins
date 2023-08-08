# -*- coding: utf-8 -*-
"""
还原按钮两阶段交互的回归测试。

背景（用户反馈）
----------------
用户点击顶部的「确认还原」按钮无反应/报错，并指出正确流程应为：

    "点击「还原」按钮 → 选中需要还原的备份文件 → 点「确认还原」"

根因（非功能 bug，是 UI 状态管理缺陷）
--------------------------------------
1. 「确认还原」按钮**无条件显示**，但在未选中备份时点击必然失败 ——
   后端 ``api_restore(confirm="1")`` 走到 ``if not pending`` 分支，
   返回「没有待还原的备份，请先在列表中选择备份文件」。
2. 列表内的行按钮只有 ``mdi-restore`` **图标、无文字**，用户难以识别这就是"还原"按钮。

修复目标（本测试锁定）
----------------------
- 未选中备份时：**不出现**「确认还原」按钮；出现引导说明
- 选中备份后：**出现**「确认还原」按钮 + 「取消还原」按钮；提示待还原文件名
- 列表行按钮：具备「还原」**文字**（不再是纯图标）
"""

import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import tests  # noqa: F401  触发宿主桩路径注入

from configbackup import ConfigBackup


def _flatten(components) -> list:
    """把 MP 返回的组件树递归展开为扁平列表（便于查找按钮）。"""
    out = []
    if isinstance(components, dict):
        out.append(components)
        for value in components.values():
            out.extend(_flatten(value))
    elif isinstance(components, (list, tuple)):
        for item in components:
            out.extend(_flatten(item))
    return out


def _find_buttons(components) -> list:
    """找出所有 VBtn 组件。"""
    return [c for c in _flatten(components) if c.get("component") == "VBtn"]


def _texts(components) -> list:
    """收集所有组件的 text 字段。"""
    return [c.get("text") for c in _flatten(components) if c.get("text")]


class _PageBase(unittest.TestCase):
    """构造一个启用状态、带若干备份文件的插件实例。"""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="cb-test-")
        self.backup_dir = os.path.join(self.base, "backups")
        os.makedirs(self.backup_dir)

        # 基类桩要求：数据目录（放 pending_restore.json）
        ConfigBackup._stub_data_path = os.path.join(self.base, "data")

        self.plugin = ConfigBackup()
        self.plugin._enabled = True
        self.plugin._backup_dir = self.backup_dir
        self.plugin._keep_count = 10
        # 真实前缀来自类属性 _prefix = "bk_"（无下划线结尾）
        self._prefix = ConfigBackup._prefix

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)
        ConfigBackup._stub_data_path = None

    # ------------------------------------------------------------------
    def make_backup(self, name: str, content: str = "dummy") -> Path:
        """
        造一个"看起来像备份"的 zip。

        文件名需匹配插件的 ``_prefix`` 与 ``.zip`` 后缀，
        否则 ``__list_backups`` 会过滤掉。
        """
        path = Path(self.backup_dir) / f"{self._prefix}{name}.zip"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("manifest.json", content)
        return path

    def page(self):
        return self.plugin.get_page()


class TestRestoreButtonVisibility(_PageBase):
    """「确认还原」按钮的显示条件 —— 本次修复的核心。"""

    def test_no_confirm_button_without_selection(self):
        """
        未选中备份时，不得出现「确认还原」按钮。

        这正是用户遇到"点了无效"的原因：按钮存在但必然失败。
        """
        self.make_backup("20260918_030000")
        self.assertIsNone(
            self.plugin._ConfigBackup__get_pending_restore(),
            "前置条件：不应有 pending 状态",
        )

        page = self.page()
        buttons = _find_buttons(page)
        texts = [b.get("text") for b in buttons]

        self.assertNotIn(
            "确认还原", texts,
            f"未选中备份时不应显示「确认还原」按钮，实际按钮：{texts}",
        )
        self.assertNotIn(
            "取消还原", texts,
            f"未选中备份时不应显示「取消还原」按钮，实际按钮：{texts}",
        )

    def test_confirm_button_appears_after_selection(self):
        """选中备份后，必须出现「确认还原」与「取消还原」按钮。"""
        self.make_backup("20260918_030001")
        # 直接写入 pending 状态，模拟用户已点过行内【还原】
        self.plugin._ConfigBackup__set_pending_restore({
            "filename": f"{self._prefix}20260918_030001.zip",
            "time": "2026-09-18 03:00:01",
        })

        page = self.page()
        texts = [b.get("text") for b in _find_buttons(page)]

        self.assertIn("确认还原", texts, f"选中后应出现「确认还原」，实际：{texts}")
        self.assertIn("取消还原", texts, f"选中后应出现「取消还原」，实际：{texts}")

    def test_confirm_button_params_unchanged(self):
        """「确认还原」的 API 调用参数必须仍为 confirm=1（行为不变）。"""
        self.make_backup("20260918_030002")
        self.plugin._ConfigBackup__set_pending_restore({
            "filename": f"{self._prefix}20260918_030002.zip",
            "time": "2026-09-18 03:00:02",
        })

        page = self.page()
        btn = next(
            b for b in _find_buttons(page) if b.get("text") == "确认还原"
        )
        click = btn["events"]["click"]
        self.assertEqual(click["api"], "plugin/ConfigBackup/restore")
        self.assertEqual(click["params"]["confirm"], "1")


class TestRowButtonWording(_PageBase):
    """列表行按钮必须具备「还原」文字，不能只是图标。"""

    def test_row_restore_button_has_text(self):
        self.make_backup("20260918_031000")
        page = self.page()
        texts = [b.get("text") for b in _find_buttons(page)]

        self.assertIn(
            "还原", texts,
            f"行内应有带文字的「还原」按钮，实际：{texts}",
        )

    def test_row_restore_button_selects_file(self):
        """行内「还原」按钮点击后应传 filename（选择语义，非执行）。"""
        name = f"{self._prefix}20260918_031001.zip"
        self.make_backup("20260918_031001")
        page = self.page()

        btn = next(b for b in _find_buttons(page) if b.get("text") == "还原")
        click = btn["events"]["click"]
        self.assertEqual(click["api"], "plugin/ConfigBackup/restore")
        self.assertEqual(click["params"]["filename"], name)
        self.assertNotIn(
            "confirm", click["params"],
            "行内按钮应只做选择，不应带 confirm 参数",
        )

    def test_row_delete_button_has_text(self):
        """删除按钮同样补齐文字，保持一致。"""
        self.make_backup("20260918_031002")
        page = self.page()
        texts = [b.get("text") for b in _find_buttons(page)]
        self.assertIn("删除", texts, f"行内应有带文字的「删除」按钮，实际：{texts}")


class TestGuidanceText(_PageBase):
    """引导文案：未选中时告知两步流程，选中后告知待还原对象。"""

    def test_guidance_when_nothing_selected(self):
        self.make_backup("20260918_032000")
        page = self.page()
        joined = " ".join(t for t in _texts(page) if t)

        self.assertIn("先在", joined, f"应提示先选择备份，实际文案：{joined[:200]}")
        self.assertIn("确认还原", joined, f"应提示使用哪个按钮，实际：{joined[:200]}")

    def test_guidance_shows_selected_filename(self):
        """选中提示必须落在同一条文案里，且含完整文件名。

        注意：不能用"全页面搜索文件名"来断言——时间戳 ``20260918_032001``
        在别处也会出现，模糊搜索会让本用例变成永远为真的绿色装饰
        （变异测试实测过：删掉文件名后全页搜索仍能命中）。
        """
        name = f"{self._prefix}20260918_032001.zip"
        self.make_backup("20260918_032001")
        self.plugin._ConfigBackup__set_pending_restore({
            "filename": name,
            "time": "2026-09-18 03:20:01",
        })
        page = self.page()
        # 只取"已选中待还原备份"那条提示，要求它在同一条文案内带上文件名
        notices = [t for t in _texts(page) if t and "已选中待还原备份" in t]
        self.assertTrue(notices, "选中后应出现「已选中待还原备份」提示")
        self.assertIn(
            name, notices[0],
            f"选中提示里应带完整文件名，实际：{notices[0][:200]}",
        )

    def test_selection_notice_not_confusable_with_other_lines(self):
        """反向校验：别处出现的时间戳不能替提示行"顶包"。

        把时间戳换成与文件名不同的值，若实现里忘了带文件名，
        就再也没有其它行能满足断言 —— 从而暴露缺陷。
        """
        name = f"{self._prefix}20260918_032001.zip"
        self.make_backup("20260918_032001")
        self.plugin._ConfigBackup__set_pending_restore({
            "filename": name,
            # 故意与文件名里的时间戳不同
            "time": "2026-09-18 07:59:59",
        })
        page = self.page()
        notices = [t for t in _texts(page) if t and "已选中待还原备份" in t]
        self.assertTrue(notices, "选中后应出现「已选中待还原备份」提示")
        self.assertIn(name, notices[0], "提示行必须自带文件名")


class TestApiRestoreContract(_PageBase):
    """
    后端接口契约未变（防回归）。

    本次只改 UI 展示条件，后端两阶段语义必须保持不变。
    """

    def test_confirm_without_pending_returns_error(self):
        """未选中就 confirm=1 时，后端仍应明确报错（而非静默成功）。"""
        result = self.plugin.api_restore(filename="", confirm="1")
        self.assertFalse(result.get("success"))
        self.assertIn("没有待还原", result.get("message", ""))

    def test_select_then_cancel(self):
        """选择后再取消，应清除 pending 状态。"""
        name = f"{self._prefix}20260918_033000.zip"
        self.make_backup("20260918_033000")

        selected = self.plugin.api_restore(filename=name, confirm="")
        self.assertTrue(selected.get("success"), selected)
        self.assertIsNotNone(self.plugin._ConfigBackup__get_pending_restore())

        cancelled = self.plugin.api_restore(filename="", confirm="cancel")
        self.assertTrue(cancelled.get("success"), cancelled)
        self.assertIsNone(
            self.plugin._ConfigBackup__get_pending_restore(),
            "取消后 pending 应被清除",
        )

    def test_select_missing_file_rejected(self):
        """选择不存在的备份应被拒绝。"""
        result = self.plugin.api_restore(filename="不存在.zip", confirm="")
        self.assertFalse(result.get("success"))


if __name__ == "__main__":
    unittest.main()
