"""
插件契约测试：版本一致性、配置表单、详情页按钮、API/命令/定时服务结构。

对应六层测试里的「回归 + 边界 + 版本一致性」，同时也守住几条硬约束：
详情页按钮必须带 ``apikey``（不是 ``token``）、配置表单里不出现「使用说明」四字、
``persistent-hint`` 必须是布尔 True。
"""

import asyncio
import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import tests  # noqa: F401  触发宿主桩与插件路径注入

from tgsignin import TgSignin
from tgsignin.version import VERSION

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PLUGIN_ROOT.parent.parent


def _walk(node):
    """
    深度遍历 Vuetify JSON 结构里的所有节点。

    :param node: 任意节点（dict / list / 标量）
    :return list: 节点列表
    """
    found = []
    if isinstance(node, dict):
        found.append(node)
        for value in node.values():
            found.extend(_walk(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_walk(item))
    return found


class TestVersionConsistency(unittest.TestCase):
    """版本号四处一致。"""

    def test_version_matches_package_entry(self) -> None:
        """version.py 与 package.v3.json 的主版本、history 首键一致。"""
        package = json.loads((REPO_ROOT / "package.v3.json").read_text(encoding="utf-8"))
        entry = package["TgSignin"]
        self.assertEqual(entry["version"], VERSION)
        self.assertEqual(list(entry["history"].keys())[0], f"v{VERSION}")

    def test_class_version_uses_version_file(self) -> None:
        """插件类的 plugin_version 直接来自 version.py。"""
        self.assertEqual(TgSignin.plugin_version, VERSION)

    def test_plugin_metadata_matches_package(self) -> None:
        """类上的名称/作者/图标与市场元数据一致。"""
        package = json.loads((REPO_ROOT / "package.v3.json").read_text(encoding="utf-8"))
        entry = package["TgSignin"]
        self.assertEqual(entry["name"], TgSignin.plugin_name)
        self.assertEqual(entry["author"], TgSignin.plugin_author)
        self.assertEqual(entry["icon"], TgSignin.plugin_icon)
        self.assertTrue((PLUGIN_ROOT / TgSignin.plugin_icon).is_file())


class TestConfigForm(unittest.TestCase):
    """配置表单结构。"""

    def setUp(self) -> None:
        """构造已初始化的插件实例。"""
        self.plugin = TgSignin()
        self.plugin.init_plugin({"enabled": True})

    def test_form_is_json_serializable(self) -> None:
        """表单结构必须可 JSON 序列化（前端要能解析）。"""
        form, defaults = self.plugin.get_form()
        json.dumps(form)
        json.dumps(defaults)
        self.assertIsInstance(defaults["cron"], str)

    def test_defaults_cover_every_model_field(self) -> None:
        """表单里出现的 model 必须在默认值里有键。"""
        form, defaults = self.plugin.get_form()
        models = [
            node["props"]["model"]
            for node in _walk(form)
            if isinstance(node.get("props"), dict) and node["props"].get("model")
        ]
        self.assertTrue(models)
        missing = [model for model in models if model not in defaults]
        self.assertEqual(missing, [])

    def test_persistent_hint_is_bool(self) -> None:
        """persistent-hint 必须是布尔 True（写字符串会失效）。"""
        form, _ = self.plugin.get_form()
        hints = [
            node["props"]["persistent-hint"]
            for node in _walk(form)
            if isinstance(node.get("props"), dict) and "persistent-hint" in node["props"]
        ]
        self.assertTrue(hints)
        for value in hints:
            self.assertIs(value, True)

    def test_no_forbidden_group_title(self) -> None:
        """组标题不得出现「使用说明」四字。"""
        form, _ = self.plugin.get_form()
        self.assertNotIn("使用说明", json.dumps(form, ensure_ascii=False))


class TestPage(unittest.TestCase):
    """详情页结构与按钮契约。"""

    def test_disabled_shows_hint(self) -> None:
        """未启用时只给一条提示。"""
        plugin = TgSignin()
        plugin.init_plugin({})
        page = plugin.get_page()
        self.assertEqual(len(page), 1)
        self.assertIn("未启用", page[0]["props"]["text"])

    def test_enabled_page_has_buttons_with_apikey(self) -> None:
        """启用后按钮齐全，且 params 里带的是 apikey。"""
        plugin = TgSignin()
        plugin.init_plugin({"enabled": True})
        page = plugin.get_page()
        buttons = [node for node in _walk(page) if node.get("component") == "VBtn"]
        self.assertGreaterEqual(len(buttons), 4)
        for button in buttons:
            params = button["events"]["click"]["params"]
            self.assertIn("apikey", params)
            self.assertNotIn("token", params)
            self.assertTrue(button["events"]["click"]["api"].startswith("plugin/TgSignin/"))

    def test_page_contains_tables(self) -> None:
        """详情页包含账号状态表与结果表。"""
        plugin = TgSignin()
        plugin.init_plugin({"enabled": True})
        tables = [node for node in _walk(plugin.get_page()) if node.get("component") == "VTable"]
        self.assertEqual(len(tables), 2)


class TestApiCommandService(unittest.TestCase):
    """API / 命令 / 定时服务契约。"""

    def setUp(self) -> None:
        """构造已初始化的插件实例。"""
        self.plugin = TgSignin()
        self.plugin.init_plugin({"enabled": True, "cron": "0 9 * * *"})

    def test_api_routes(self) -> None:
        """API 路由齐全且都可调用。"""
        paths = {item["path"] for item in self.plugin.get_api()}
        self.assertIn("/login/send_code", paths)
        self.assertIn("/login/confirm", paths)
        self.assertIn("/signin/run", paths)
        self.assertIn("/selftest", paths)
        self.assertIn("/logout", paths)
        for item in self.plugin.get_api():
            self.assertTrue(callable(item["endpoint"]))
            self.assertTrue(item["methods"])

    def test_commands(self) -> None:
        """三条命令都指向本插件的 action。"""
        commands = self.plugin.get_command()
        self.assertEqual(len(commands), 3)
        for command in commands:
            self.assertTrue(command["cmd"].startswith("/tg"))
            self.assertTrue(command["data"]["action"].startswith("tgsignin_"))

    def test_service_with_valid_cron(self) -> None:
        """合法 cron 注册出一个定时服务。"""
        services = self.plugin.get_service()
        self.assertEqual(len(services), 1)
        self.assertEqual(services[0]["id"], "tgsignin_daily")

    def test_service_with_invalid_cron(self) -> None:
        """非法 cron 不注册服务（避免调度器抛错）。"""
        plugin = TgSignin()
        plugin.init_plugin({"enabled": True, "cron": "not a cron"})
        self.assertEqual(plugin.get_service(), [])

    def test_api_status(self) -> None:
        """状态接口返回配置摘要。"""
        request = MagicMock()
        result = asyncio.run(self.plugin.api_status(request))
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["cron"], "0 9 * * *")
        self.assertEqual(len(result["data"]["accounts"]), 2)

    def test_api_logout_two_phase(self) -> None:
        """退出登录必须两阶段：先提示，带 confirm 才删 session。"""
        session_dir = self.plugin.get_data_path() / "sessions"
        session_dir.mkdir(parents=True, exist_ok=True)
        session_file = session_dir / "acc_acc1.session"
        session_file.write_text("stub", encoding="utf-8")

        request = MagicMock()
        request.query_params = {"account": "acc1"}
        request.json = AsyncMock(return_value={})
        first = asyncio.run(self.plugin.api_logout(request))
        self.assertFalse(first["success"])
        self.assertTrue(first["data"]["need_confirm"])
        self.assertTrue(session_file.exists())

        request.query_params = {"account": "acc1", "confirm": "true"}
        second = asyncio.run(self.plugin.api_logout(request))
        self.assertTrue(second["success"])
        self.assertFalse(session_file.exists())

    def test_api_signin_rejected_when_disabled(self) -> None:
        """未启用时手动签到被拒绝。"""
        plugin = TgSignin()
        plugin.init_plugin({})
        request = MagicMock()
        request.query_params = {}
        request.json = AsyncMock(return_value={})
        result = asyncio.run(plugin.api_signin(request))
        self.assertFalse(result["success"])
        self.assertIn("未启用", result["message"])


class TestConfigRoundTrip(unittest.TestCase):
    """配置解析与回显。"""

    def test_default_config_parse(self) -> None:
        """默认配置解析出 2 个账号与 5 条目标。"""
        plugin = TgSignin()
        plugin.init_plugin({"enabled": True})
        self.assertEqual([a.key for a in plugin._accounts], ["acc1", "acc2"])
        self.assertEqual(len(plugin._targets), 5)
        self.assertEqual(plugin._config_problems, [])

    def test_bad_reference_reported(self) -> None:
        """目标引用不存在的账号时，问题列表里有记录。"""
        plugin = TgSignin()
        plugin.init_plugin(
            {
                "enabled": True,
                "accounts_text": "acc1 | 一号 | +8613800138000",
                "targets_text": "acc9 | @b | 按钮 | 签到",
            }
        )
        self.assertTrue(any("不存在的账号" in item for item in plugin._config_problems))


if __name__ == "__main__":
    unittest.main()
