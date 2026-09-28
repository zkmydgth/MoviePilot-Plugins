# -*- coding: utf-8 -*-
"""
端到端测试：真实 HTTP 请求 → 插件 API → envelope 契约。

与 test_envelope_contract.py 的区别
-----------------------------------
- 契约测试直接调用 Python 方法，验证"返回值形状"；
- 本模块把插件端点挂到一个真实的 FastAPI 应用上，通过 ``httpx`` 发真实
  HTTP 请求，再**在响应侧复刻前端拦截器**，判定"用户是否会被弹窗"。

这是最接近线上的一层：它同时覆盖了 FastAPI 的 JSON 序列化、
路由参数解析（query string）以及响应形状。

判定口径
--------
一次请求被视为"用户无感（不报错）"，当且仅当：

1. HTTP 状态码为 2xx（非 2xx 会走 axios 的 reject 分支 → 弹错）；且
2. 响应体通过 ``isApiResponse()`` 三键校验（否则 data 客户端弹"无效响应"）。

用户的原始现象「操作生效但弹无效响应」正是"① 通过、② 失败"的组合，
本模块把这个组合固化为失败用例。
"""

import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import tests  # noqa: F401
from configbackup import ConfigBackup

try:
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    _HTTP_OK = True
except Exception:  # pragma: no cover - 环境缺依赖时跳过
    _HTTP_OK = False


# ----------------------------------------------------------------------
# 前端拦截器的等价判定
# ----------------------------------------------------------------------
def frontend_accepts(status_code: int, body) -> tuple[bool, str]:
    """
    模拟前端 axios 拦截器对一次响应的处置。

    :param status_code: HTTP 状态码
    :param body: 已解析的 JSON 响应体
    :return: (用户是否无感, 若不无感则给出原因)
    """
    if not (200 <= status_code < 300):
        return False, f"HTTP {status_code} 触发 axios reject → 弹出错误"

    if not isinstance(body, dict):
        return False, "响应体不是对象 → invalid-envelope"

    keys = list(body.keys())
    if len(keys) != 3 or not all(k in ("success", "message", "data") for k in keys):
        return False, f"键集合 {sorted(keys)} 不满足三键契约 → 弹「服务器返回了无效响应」"
    if not isinstance(body["success"], bool):
        return False, "success 不是布尔 → 弹「服务器返回了无效响应」"
    if not isinstance(body["message"], str):
        return False, "message 不是字符串 → 弹「服务器返回了无效响应」"
    if "data" not in body:
        return False, "缺少 data 键 → 弹「服务器返回了无效响应」"

    return True, ""


@unittest.skipUnless(_HTTP_OK, "未安装 fastapi/httpx，跳过端到端测试")
class _E2EBase(unittest.TestCase):
    """把插件端点挂到真实 FastAPI 应用上，用真实 HTTP 调用。"""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="cb-e2e-")
        self.backup_dir = os.path.join(self.base, "backups")
        os.makedirs(self.backup_dir)

        ConfigBackup._stub_data_path = os.path.join(self.base, "data")

        self.plugin = ConfigBackup()
        self.plugin._enabled = True
        self.plugin._backup_dir = self.backup_dir
        self.plugin._keep_count = 10
        self.plugin._notify = False
        self._prefix = ConfigBackup._prefix

        self.app = FastAPI()
        self._mount_endpoints()
        self.client = TestClient(self.app)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)
        ConfigBackup._stub_data_path = None

    def _mount_endpoints(self):
        """
        按 MoviePilot 的方式挂载插件动态 API。

        MP 把 endpoint 从 FastAPI 的依赖注入签名里取出参数，
        这里用 ``request.query_params`` 显式还原同样的取值口径。
        """
        plugin = self.plugin

        @self.app.get("/api/v1/plugin/ConfigBackup/backup")
        def _backup():
            return plugin.api_backup()

        @self.app.get("/api/v1/plugin/ConfigBackup/list")
        def _list():
            return plugin.api_list()

        @self.app.get("/api/v1/plugin/ConfigBackup/delete")
        def _delete(request: Request):
            qp = request.query_params
            return plugin.api_delete(
                filename=qp.get("filename", ""),
                confirm=qp.get("confirm", ""),
            )

        @self.app.get("/api/v1/plugin/ConfigBackup/restore")
        def _restore(request: Request):
            qp = request.query_params
            return plugin.api_restore(
                filename=qp.get("filename", ""),
                confirm=qp.get("confirm", ""),
            )

    def make_backup(self, name: str) -> Path:
        path = Path(self.backup_dir) / f"{self._prefix}{name}.zip"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("manifest.json", "{}")
        return path

    def call(self, path: str, **params):
        """发一次真实 HTTP 请求，并断言前端不会报错。"""
        resp = self.client.get(path, params=params)
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        ok, why = frontend_accepts(resp.status_code, body)
        self.assertTrue(
            ok,
            f"用户会看到报错：GET {path} params={params}\n  原因：{why}\n  响应：{body}",
        )
        return body


# ======================================================================
# 1. 四个端点经由真实 HTTP 均不得触发前端弹窗
# ======================================================================
class TestEndpointsOverHttp(_E2EBase):
    """逐个端点走真实 HTTP，确认响应形状对前端友好。"""

    def test_backup(self):
        with mock.patch.object(
            ConfigBackup, "_ConfigBackup__backup", return_value=(True, "备份完成")
        ):
            body = self.call("/api/v1/plugin/ConfigBackup/backup")
        self.assertTrue(body["success"])
        self.assertEqual(body["message"], "备份完成")

    def test_list(self):
        self.make_backup("20260918_100000")
        body = self.call("/api/v1/plugin/ConfigBackup/list")
        self.assertTrue(body["success"])
        self.assertEqual(len(body["data"]), 1)

    def test_delete_select_then_confirm_over_http(self):
        """删除完整两阶段：HTTP 选中 → 文件仍在 → HTTP 确认 → 文件消失。"""
        target = self.make_backup("20260918_100001")
        name = target.name

        select = self.call("/api/v1/plugin/ConfigBackup/delete", filename=name)
        self.assertTrue(select["success"], select)
        self.assertTrue(target.exists(), "选中阶段不得删文件")

        confirm = self.call("/api/v1/plugin/ConfigBackup/delete", confirm="1")
        self.assertTrue(confirm["success"], confirm)
        self.assertFalse(target.exists(), "确认后文件应被删除")

    def test_delete_cancel_over_http(self):
        target = self.make_backup("20260918_100002")

        self.call("/api/v1/plugin/ConfigBackup/delete", filename=target.name)
        cancel = self.call("/api/v1/plugin/ConfigBackup/delete", confirm="cancel")

        self.assertTrue(cancel["success"], cancel)
        self.assertTrue(target.exists(), "取消删除不得动文件")

    def test_delete_failure_branches_over_http(self):
        """各失败分支在 HTTP 层同样不得弹"无效响应"。"""
        cases = [
            ("缺文件名", {}),
            ("非法名", {"filename": "evil.zip"}),
            ("不存在", {"filename": f"{self._prefix}nope.zip"}),
            ("无待删除", {"confirm": "1"}),
        ]
        for label, params in cases:
            with self.subTest(case=label):
                body = self.call("/api/v1/plugin/ConfigBackup/delete", **params)
                self.assertFalse(body["success"], f"【{label}】应失败：{body}")

    def test_restore_select_over_http(self):
        target = self.make_backup("20260918_100003")
        body = self.call("/api/v1/plugin/ConfigBackup/restore", filename=target.name)
        self.assertTrue(body["success"], body)

    def test_restore_failure_branches_over_http(self):
        cases = [
            ("缺文件名", {}),
            ("非法名", {"filename": "../evil.zip"}),
            ("无待还原", {"confirm": "1"}),
        ]
        for label, params in cases:
            with self.subTest(case=label):
                body = self.call("/api/v1/plugin/ConfigBackup/restore", **params)
                self.assertFalse(body["success"], f"【{label}】应失败：{body}")


# ======================================================================
# 2. 前端拦截器等价判定自身的正确性
# ======================================================================
class TestFrontendJudgementSpec(unittest.TestCase):
    """防止"端到端断言永远通过"——先证明判定器能识别坏响应。"""

    def test_rejects_original_bug_shape(self):
        """修复前的响应形状必须被判为"会弹窗"。"""
        ok, why = frontend_accepts(200, {"success": True, "message": "删除成功"})
        self.assertFalse(ok, "两键响应必须被判为会弹窗")
        self.assertIn("无效响应", why)

    def test_rejects_non_2xx(self):
        ok, why = frontend_accepts(500, {"success": True, "message": "x", "data": None})
        self.assertFalse(ok)
        self.assertIn("500", why)

    def test_accepts_fixed_shape(self):
        ok, _ = frontend_accepts(200, {"success": True, "message": "ok", "data": None})
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
