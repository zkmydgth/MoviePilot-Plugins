# -*- coding: utf-8 -*-
"""
鉴权方式与令牌下发回归（2026-11-28）。

背景（与 TgSignin / ConfigBackup 同批修复）
-------------------------------------------
插件接口未声明 ``auth`` 时，MoviePilot 按默认 ``apikey`` 校验，而它只从 query /
请求头读取；前端点详情页按钮却把参数放进**请求体** → 一律 401「apikey 校验不通过」
→ 前端清会话跳登录页，动作根本没执行。

同时旧实现把 ``settings.API_TOKEN``（管理员 API Token）塞进按钮 params，令牌明文
出现在插件页面 JSON、浏览器缓存与开发者工具里。鉴权改走 Bearer 后该参数既无用又
是暴露面，故一并移除。
"""

import json
import unittest

import tests  # noqa: F401  触发宿主桩与插件路径注入

from app.runtime.config import settings
from doubanscorewatch import DoubanScoreWatch


class TestAuthAndTokenExposure(unittest.TestCase):
    """详情页按钮必须走 Bearer 鉴权，且页面不得下发管理员令牌。"""

    def setUp(self) -> None:
        """构造启用状态的插件实例。"""
        self.plugin = DoubanScoreWatch()
        self.plugin.init_plugin({"enabled": True})

    def test_check_now_uses_bear_auth(self) -> None:
        """「立即检查」按钮背后的路由必须声明 Bearer 鉴权。"""
        routes = {item["path"]: item for item in self.plugin.get_api()}
        self.assertIn("/check_now", routes)
        self.assertEqual(routes["/check_now"].get("auth"), "bear")

    def test_page_has_no_apikey(self) -> None:
        """页面 JSON 里不得出现 apikey，也不得出现真实 API Token。"""
        plain = json.dumps(self.plugin.get_page(), ensure_ascii=False)
        self.assertNotIn("apikey", plain)
        token = str(getattr(settings, "API_TOKEN", "") or "")
        if token:
            self.assertNotIn(token, plain, "页面 JSON 里不得出现真实 API Token")


if __name__ == "__main__":
    unittest.main(verbosity=2)
