# -*- coding: utf-8 -*-
"""
宿主 envelope 契约测试。

背景（真实线上事故）
--------------------
MoviePilot V3 前端在 ``client.ts`` 里用 ``isApiResponse()`` 校验每个 HTTP 响应：

    keys.length === 3
    && keys.every(k => k === 'success' || k === 'message' || k === 'data')
    && typeof success === 'boolean'
    && typeof message === 'string'
    && 'data' in record

校验失败且走的是**普通 data 客户端**时，拦截器会 ``notifyFailure('invalid-envelope')``，
前端弹出「服务器返回了无效响应」。而插件页的按钮事件由 ``PageRender.vue`` 的
``commonAction()`` 发起，它 ``import api from '@/api'`` —— 用的正是 data 客户端。

因此：**只要插件 API 返回的不是恰好三键，用户就会看到"无效响应"弹窗，
即便后端逻辑已经成功执行**。本模块把这条契约固化成测试。

注意：本模块只校验"响应形状"，不校验业务语义；业务分支由 test_boundary /
test_restore_flow 覆盖。两者的交集才是"用户不会看到报错"的充分条件。
"""

import json
import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import tests  # noqa: F401  # 自举 sys.path
from configbackup import ConfigBackup


# ----------------------------------------------------------------------
# 前端 isApiResponse() 的 Python 等价实现
# ----------------------------------------------------------------------
def is_api_response(value) -> bool:
    """
    逐字复刻 MoviePilot V3 前端的 envelope 校验。

    :param value: 待校验的响应载荷
    :return: 是否为合法宿主 envelope
    """
    if not isinstance(value, dict):
        return False
    keys = list(value.keys())
    if len(keys) != 3:
        return False
    if not all(k in ("success", "message", "data") for k in keys):
        return False
    if not isinstance(value["success"], bool):
        return False
    if not isinstance(value["message"], str):
        return False
    return "data" in value


def assert_envelope(testcase: unittest.TestCase, payload, label: str = "") -> None:
    """断言 payload 能通过前端 envelope 校验，并给出可读的失败信息。"""
    if is_api_response(payload):
        return
    testcase.fail(
        f"响应不满足宿主 envelope 三键契约{('（' + label + '）') if label else ''}：\n"
        f"  实际载荷 : {payload!r}\n"
        f"  键集合   : {sorted(payload.keys()) if isinstance(payload, dict) else type(payload).__name__}\n"
        f"  → 前端会弹出「服务器返回了无效响应」"
    )


class _EnvelopeBase(unittest.TestCase):
    """夹具：临时备份目录 + 启用状态的插件实例。"""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="cb-envelope-")
        self.backup_dir = os.path.join(self.base, "backups")
        os.makedirs(self.backup_dir)

        ConfigBackup._stub_data_path = os.path.join(self.base, "data")

        self.plugin = ConfigBackup()
        self.plugin._enabled = True
        self.plugin._backup_dir = self.backup_dir
        self.plugin._keep_count = 10
        self.plugin._notify = False
        self._prefix = ConfigBackup._prefix

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)
        ConfigBackup._stub_data_path = None

    def make_backup(self, name: str) -> Path:
        path = Path(self.backup_dir) / f"{self._prefix}{name}.zip"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("manifest.json", "{}")
        return path


# ======================================================================
# 1. isApiResponse 自身的行为（防止"测试抄错"）
# ======================================================================
class TestIsApiResponseSpec(unittest.TestCase):
    """确认这个 Python 复刻版和后端契约一致，否则后面的断言毫无意义。"""

    def test_accepts_canonical_envelope(self):
        self.assertTrue(is_api_response({"success": True, "message": "ok", "data": None}))
        self.assertTrue(is_api_response({"success": False, "message": "bad", "data": {"a": 1}}))

    def test_rejects_two_keys(self):
        """两键响应（本次事故的元凶形状）。"""
        self.assertFalse(is_api_response({"success": True, "message": "ok"}))
        self.assertFalse(is_api_response({"success": True, "data": None}))
        self.assertFalse(is_api_response({"success": False, "message": "x"}))

    def test_rejects_extra_keys(self):
        """四键同样非法——前端要求"恰好"三键。"""
        self.assertFalse(
            is_api_response({"success": True, "message": "ok", "data": None, "code": 0})
        )

    def test_rejects_wrong_key_names(self):
        self.assertFalse(is_api_response({"code": 0, "msg": "ok", "data": None}))

    def test_rejects_wrong_types(self):
        self.assertFalse(is_api_response({"success": "true", "message": "ok", "data": None}))
        self.assertFalse(is_api_response({"success": True, "message": 1, "data": None}))

    def test_rejects_non_dict(self):
        self.assertFalse(is_api_response(None))
        self.assertFalse(is_api_response([]))
        self.assertFalse(is_api_response("ok"))


# ======================================================================
# 2. 四个 API 的所有可达分支都要返回合法 envelope
# ======================================================================
class TestApiEnvelopeContract(_EnvelopeBase):
    """
    枚举每个 API 的每条 return 路径，逐一校验 envelope 形状。

    这是本次修复的核心回归：修复前 api_backup / api_delete / api_restore
    全部返回两键，api_list 缺 message——5 类弹窗由此而来。
    """

    # ------------------------------------------------------------------
    # /backup
    # ------------------------------------------------------------------
    def test_backup_success_envelope(self):
        with mock.patch.object(
            ConfigBackup, "_ConfigBackup__backup", return_value=(True, "备份成功")
        ):
            result = self.plugin.api_backup()
        assert_envelope(self, result, "api_backup 成功")

    def test_backup_failure_envelope(self):
        with mock.patch.object(
            ConfigBackup, "_ConfigBackup__backup", return_value=(False, "磁盘满")
        ):
            result = self.plugin.api_backup()
        assert_envelope(self, result, "api_backup 失败")
        self.assertFalse(result["success"])

    # ------------------------------------------------------------------
    # /list
    # ------------------------------------------------------------------
    def test_list_envelope(self):
        self.make_backup("20260918_090000")
        result = self.plugin.api_list()
        assert_envelope(self, result, "api_list")
        self.assertIsInstance(result["data"], list)

    def test_list_empty_envelope(self):
        result = self.plugin.api_list()
        assert_envelope(self, result, "api_list 空列表")
        self.assertEqual(result["data"], [])

    # ------------------------------------------------------------------
    # /delete —— 两阶段，每条分支都要合规
    # ------------------------------------------------------------------
    def test_delete_select_envelope(self):
        target = self.make_backup("20260918_090001")
        assert_envelope(self, self.plugin.api_delete(filename=target.name), "删除-选中")

    def test_delete_confirm_envelope(self):
        target = self.make_backup("20260918_090002")
        self.plugin.api_delete(filename=target.name)
        assert_envelope(self, self.plugin.api_delete(filename="", confirm="1"), "删除-确认")

    def test_delete_cancel_envelope(self):
        assert_envelope(self, self.plugin.api_delete(filename="", confirm="cancel"), "删除-取消")

    def test_delete_missing_filename_envelope(self):
        assert_envelope(self, self.plugin.api_delete(filename=""), "删除-缺文件名")

    def test_delete_illegal_name_envelope(self):
        assert_envelope(
            self, self.plugin.api_delete(filename="evil.zip"), "删除-非法名"
        )

    def test_delete_missing_file_envelope(self):
        assert_envelope(
            self,
            self.plugin.api_delete(filename=f"{self._prefix}not_exist.zip"),
            "删除-文件不存在",
        )

    def test_delete_no_pending_confirm_envelope(self):
        assert_envelope(
            self, self.plugin.api_delete(filename="", confirm="1"), "删除-无待删除"
        )

    def test_delete_pending_file_gone_envelope(self):
        """选中后文件被外部删除，确认时走"不存在"分支。"""
        target = self.make_backup("20260918_090003")
        self.plugin.api_delete(filename=target.name)
        target.unlink()

        assert_envelope(
            self, self.plugin.api_delete(filename="", confirm="1"), "删除-待删除文件丢失"
        )

    def test_delete_unlink_failure_envelope(self):
        """unlink 抛异常时也不能漏掉 envelope。"""
        target = self.make_backup("20260918_090004")
        self.plugin.api_delete(filename=target.name)

        with mock.patch.object(Path, "unlink", side_effect=PermissionError("只读")):
            result = self.plugin.api_delete(filename="", confirm="1")

        assert_envelope(self, result, "删除-落盘失败")
        self.assertFalse(result["success"])

    # ------------------------------------------------------------------
    # /restore —— 两阶段 + 校验分支，逐个覆盖
    # ------------------------------------------------------------------
    def test_restore_select_envelope(self):
        target = self.make_backup("20260918_091000")
        assert_envelope(self, self.plugin.api_restore(filename=target.name), "还原-选中")

    def test_restore_cancel_envelope(self):
        assert_envelope(self, self.plugin.api_restore(confirm="cancel"), "还原-取消")

    def test_restore_confirm_success_envelope(self):
        target = self.make_backup("20260918_091001")
        self.plugin.api_restore(filename=target.name)
        with mock.patch.object(
            ConfigBackup, "_ConfigBackup__backup", return_value=(True, "ok")
        ), mock.patch.object(
            ConfigBackup, "_ConfigBackup__restore", return_value=(True, "还原成功")
        ):
            result = self.plugin.api_restore(filename="", confirm="1")
        assert_envelope(self, result, "还原-确认成功")

    def test_restore_confirm_failure_envelope(self):
        target = self.make_backup("20260918_091002")
        self.plugin.api_restore(filename=target.name)
        with mock.patch.object(
            ConfigBackup, "_ConfigBackup__backup", return_value=(True, "ok")
        ), mock.patch.object(
            ConfigBackup, "_ConfigBackup__restore", return_value=(False, "还原炸了")
        ):
            result = self.plugin.api_restore(filename="", confirm="1")
        assert_envelope(self, result, "还原-确认失败")

    def test_restore_lock_busy_envelope(self):
        self.plugin._restore_lock.acquire()
        try:
            result = self.plugin.api_restore(filename="", confirm="1")
        finally:
            self.plugin._restore_lock.release()
        assert_envelope(self, result, "还原-锁占用")

    def test_restore_no_pending_envelope(self):
        assert_envelope(
            self, self.plugin.api_restore(filename="", confirm="1"), "还原-无待还原"
        )

    def test_restore_pending_file_gone_envelope(self):
        target = self.make_backup("20260918_091003")
        self.plugin.api_restore(filename=target.name)
        target.unlink()
        assert_envelope(
            self, self.plugin.api_restore(filename="", confirm="1"), "还原-待还原文件丢失"
        )

    def test_restore_missing_filename_envelope(self):
        assert_envelope(self, self.plugin.api_restore(filename=""), "还原-缺文件名")

    def test_restore_illegal_name_envelope(self):
        assert_envelope(
            self, self.plugin.api_restore(filename="evil.zip"), "还原-非法名"
        )

    def test_restore_missing_file_envelope(self):
        assert_envelope(
            self,
            self.plugin.api_restore(filename=f"{self._prefix}not_exist.zip"),
            "还原-文件不存在",
        )

    def test_restore_corrupt_zip_envelope(self):
        """zip 损坏分支。"""
        bad = Path(self.backup_dir) / f"{self._prefix}20260918_091004.zip"
        bad.write_bytes(b"definitely not a zip")

        result = self.plugin.api_restore(filename=bad.name)
        assert_envelope(self, result, "还原-zip损坏")
        self.assertFalse(result["success"])

    def test_restore_unreadable_zip_envelope(self):
        """读 zip 抛异常分支。"""
        target = self.make_backup("20260918_091005")
        with mock.patch(
            "configbackup.zipfile.ZipFile", side_effect=OSError("无法读取")
        ):
            result = self.plugin.api_restore(filename=target.name)
        assert_envelope(self, result, "还原-zip不可读")
        self.assertFalse(result["success"])

    def test_restore_unexpected_exception_envelope(self):
        """兜底 except 分支也必须返回合法 envelope。"""
        with mock.patch.object(
            Path, "name", new_callable=lambda: property(lambda self: 1 / 0)
        ):
            result = self.plugin.api_restore(filename="whatever")
        assert_envelope(self, result, "还原-兜底异常")
        self.assertFalse(result["success"])


# ======================================================================
# 3. get_page 渲染树里的按钮参数
# ======================================================================
class TestPageButtons(_EnvelopeBase):
    """页面渲染树中的事件参数必须能被后端正确接收。"""

    def _collect_buttons(self, nodes, acc=None):
        """递归收集渲染树里的所有按钮节点。"""
        if acc is None:
            acc = []
        if isinstance(nodes, dict):
            if nodes.get("component") == "VBtn":
                acc.append(nodes)
            for value in nodes.values():
                self._collect_buttons(value, acc)
        elif isinstance(nodes, list):
            for item in nodes:
                self._collect_buttons(item, acc)
        return acc

    def test_delete_buttons_use_two_stage(self):
        """列表里的【删除】只传 filename；顶部【确认删除】才传 confirm=1。"""
        self.make_backup("20260918_092000")
        self.plugin._ConfigBackup__set_pending_delete({"filename": f"{self._prefix}20260918_092000.zip"})

        buttons = self._collect_buttons(self.plugin.get_page())
        delete_btns = [
            b for b in buttons
            if b.get("events", {}).get("click", {}).get("api", "").endswith("/delete")
        ]
        self.assertTrue(delete_btns, "页面里应存在删除相关按钮")

        confirm_btns, cancel_btns, select_btns = [], [], []
        for btn in delete_btns:
            params = btn["events"]["click"].get("params", {})
            if params.get("confirm") == "1":
                confirm_btns.append(btn)
            elif params.get("confirm") == "cancel":
                cancel_btns.append(btn)
            elif "confirm" not in params:
                select_btns.append(btn)
            else:
                self.fail(f"出现了未知的 confirm 取值：{params}")

        self.assertEqual(len(confirm_btns), 1, "待删除状态下应有且仅有一个确认删除按钮")
        self.assertEqual(len(cancel_btns), 1, "待删除状态下应有且仅有一个取消删除按钮")
        self.assertEqual(len(select_btns), 1, "列表每行应有一个选择式删除按钮")
        for btn in select_btns:
            self.assertNotIn("confirm", btn["events"]["click"].get("params", {}))

    def test_no_confirm_delete_button_without_pending(self):
        """未选中任何备份时，不应出现确认删除按钮。"""
        self.make_backup("20260918_092001")

        buttons = self._collect_buttons(self.plugin.get_page())
        confirm_btns = [
            b for b in buttons
            if b.get("events", {}).get("click", {}).get("params", {}).get("confirm") == "1"
            and b.get("events", {}).get("click", {}).get("api", "").endswith("/delete")
        ]
        self.assertEqual(confirm_btns, [], "没有待删除项时不得渲染确认删除按钮")


if __name__ == "__main__":
    unittest.main(verbosity=2)
