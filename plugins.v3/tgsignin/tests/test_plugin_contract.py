"""
插件契约测试：版本一致性、配置表单、详情页按钮、API/命令/定时服务结构。

对应六层测试里的「回归 + 边界 + 版本一致性」，同时也守住几条硬约束：
详情页按钮必须带 ``apikey``（不是 ``token``）、配置表单里不出现「使用说明」四字、
``persistent-hint`` 必须是布尔 True。
"""

import asyncio
import json
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

import tests  # noqa: F401  触发宿主桩与插件路径注入

from tgsignin import TgSignin
from tgsignin.core.config import (
    LOGIN_ACTION_LOGOUT,
    LOGIN_ACTION_NONE,
    LOGIN_ACTION_SEND,
    MAX_ACCOUNT_SLOTS,
    NOTIFY_MODE_ALL,
    NOTIFY_MODE_FAILURE,
    NOTIFY_MODE_NONE,
    NOTIFY_MODE_SUCCESS,
    default_slot_config,
)
from tgsignin.core.store import (
    load_state,
    record_ai_keywords,
    record_login,
    record_run,
    save_state,
)
from tgsignin.core.retry import TZ, today_text
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

    def test_execute_cycle_uses_cron_field(self) -> None:
        """执行周期用 VCronField（点开可选），不是纯文本输入。"""
        form, defaults = self.plugin.get_form()
        components = {node.get("component") for node in _walk(form)}
        self.assertIn("VCronField", components)
        cron_fields = [
            node
            for node in _walk(form)
            if isinstance(node.get("props"), dict) and node["props"].get("model") == "cron"
        ]
        self.assertEqual(len(cron_fields), 1)
        self.assertIn("cron", defaults)

    def test_notify_mode_options(self) -> None:
        """通知方式是选项（四档），默认仅失败时。"""
        form, defaults = self.plugin.get_form()
        notify_nodes = [
            node
            for node in _walk(form)
            if isinstance(node.get("props"), dict)
            and node["props"].get("model") == "notify_mode"
        ]
        self.assertEqual(len(notify_nodes), 1)
        values = [item["value"] for item in notify_nodes[0]["props"]["items"]]
        self.assertEqual(
            values,
            [NOTIFY_MODE_FAILURE, NOTIFY_MODE_SUCCESS, NOTIFY_MODE_ALL, NOTIFY_MODE_NONE],
        )
        self.assertEqual(defaults["notify_mode"], NOTIFY_MODE_FAILURE)


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
        self.assertGreaterEqual(len(buttons), 7)
        accounts = [
            button["events"]["click"]["params"].get("account") for button in buttons
        ]
        self.assertIn("acc1", accounts)
        self.assertIn("acc2", accounts)
        for button in buttons:
            params = button["events"]["click"]["params"]
            self.assertIn("apikey", params)
            self.assertNotIn("token", params)
            self.assertTrue(button["events"]["click"]["api"].startswith("plugin/TgSignin/"))

    def test_page_contains_tables(self) -> None:
        """详情页包含账号状态表、今日状态表与结果表。"""
        plugin = TgSignin()
        plugin.init_plugin({"enabled": True})
        tables = [node for node in _walk(plugin.get_page()) if node.get("component") == "VTable"]
        self.assertEqual(len(tables), 3)

    def test_buttons_are_wrapped_in_flex_container(self) -> None:
        """按钮必须包在 flex 容器里（回归：直接平铺时移动端会与上方色块重叠）。"""
        plugin = TgSignin()
        plugin.init_plugin({"enabled": True})
        wrappers = [
            node
            for node in _walk(plugin.get_page())
            if isinstance(node.get("props"), dict)
            and "d-flex" in str(node["props"].get("class", ""))
            and any(
                child.get("component") == "VBtn" for child in (node.get("content") or [])
            )
        ]
        self.assertEqual(len(wrappers), 1, "应恰有一个承载操作按钮的 flex 容器")
        top_buttons = [
            child
            for child in wrappers[0]["content"]
            if child.get("component") == "VBtn"
        ]
        self.assertGreaterEqual(len(top_buttons), 5, "顶部操作按钮齐全")
        # 页面内所有按钮（含表格行内的「重试 / 测试 / 退出」）都要带 apikey
        for button in [n for n in _walk(plugin.get_page()) if n.get("component") == "VBtn"]:
            params = button["events"]["click"]["params"]
            self.assertIn("apikey", params)
            self.assertTrue(
                button["events"]["click"]["api"].startswith("plugin/TgSignin/")
            )

    def test_top_alerts_have_bottom_margin(self) -> None:
        """顶部信息块带 mb-3，避免与下一块贴在一起。"""
        plugin = TgSignin()
        plugin.init_plugin({"enabled": True})
        alerts = [n for n in _walk(plugin.get_page()) if n.get("component") == "VAlert"]
        top_alerts = [
            node
            for node in alerts
            if "▼" not in str(node.get("props", {}).get("text", ""))
        ]
        self.assertTrue(top_alerts)
        for node in top_alerts:
            self.assertIn("mb-3", str(node["props"].get("class", "")))

    def test_page_with_ai_keyword_log(self) -> None:
        """
        回归：AI 归纳关键词有记录时详情页仍能渲染。

        修复前 ``_ai_keyword_block`` 跨作用域引用 ``get_page`` 的局部函数
        ``table``，只要 ``ai_keyword_log`` 非空就抛
        ``NameError: name 'table' is not defined``（2026-10-11 线上故障）。
        """
        plugin = TgSignin()
        plugin.init_plugin({"enabled": True})
        record_ai_keywords(
            plugin.get_data_path(),
            [
                {
                    "time": "2026-10-11 00:02:26",
                    "account": "acc1",
                    "bot": "@HDHaven_Bot",
                    "verdict": "success",
                    "keyword": "连续签到",
                }
            ],
        )
        page = plugin.get_page()
        tables = [node for node in _walk(page) if node.get("component") == "VTable"]
        self.assertEqual(len(tables), 4, "账号状态表 + 今日状态表 + 结果表 + AI 归纳关键词表")
        blob = json.dumps(page, ensure_ascii=False)
        self.assertIn("AI 归纳关键词", blob)
        self.assertIn("连续签到", blob)


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
        """合法 cron 注册出「定时签到 + 失败重试」两个服务。"""
        services = self.plugin.get_service()
        self.assertEqual(
            [item["id"] for item in services], ["tgsignin_daily", "tgsignin_retry"]
        )

    def test_retry_service_skipped_when_disabled(self) -> None:
        """重试间隔 0 = 不注册重试服务。"""
        plugin = TgSignin()
        plugin.init_plugin(
            {"enabled": True, "cron": "0 9 * * *", "retry_interval_hours": 0}
        )
        services = plugin.get_service()
        self.assertEqual([item["id"] for item in services], ["tgsignin_daily"])

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

    def test_notify_mode_migration_from_legacy_bool(self) -> None:
        """旧版只有布尔「失败时通知」：关掉映射为不通知，开/缺省映射为仅失败时。"""
        off = TgSignin()
        off.init_plugin({"enabled": True, "notify_on_failure": False})
        self.assertEqual(off._notify_mode, NOTIFY_MODE_NONE)

        default = TgSignin()
        default.init_plugin({"enabled": True})
        self.assertEqual(default._notify_mode, NOTIFY_MODE_FAILURE)

        on = TgSignin()
        on.init_plugin({"enabled": True, "notify_on_failure": True})
        self.assertEqual(on._notify_mode, NOTIFY_MODE_FAILURE)

    def test_new_notify_mode_wins_over_legacy_bool(self) -> None:
        """同时存在时以新的通知方式为准；非法值回落仅失败时。"""
        plugin = TgSignin()
        plugin.init_plugin(
            {"enabled": True, "notify_mode": NOTIFY_MODE_ALL, "notify_on_failure": False}
        )
        self.assertEqual(plugin._notify_mode, NOTIFY_MODE_ALL)

        bad = TgSignin()
        bad.init_plugin({"enabled": True, "notify_mode": "weird"})
        self.assertEqual(bad._notify_mode, NOTIFY_MODE_FAILURE)

    def test_notify_label(self) -> None:
        """通知方式中文标签（页面摘要用）。"""
        plugin = TgSignin()
        plugin.init_plugin({"enabled": True, "notify_mode": NOTIFY_MODE_ALL})
        self.assertEqual(plugin._notify_label(), "成功与失败都通知")

    def test_notify_mode_object_from_frontend(self) -> None:
        """回归：前端把下拉选中写成整项对象时，通知方式仍要生效。

        实测（2026-10-06）：用户在页面上选了「成功与失败都通知」，配置里存的是
        ``{"title": "成功与失败都通知", "value": "all"}``；若不归一化，
        会被判成非法值并回落「仅失败时」，导致跑完不通知。
        """

        plugin = TgSignin()
        plugin.init_plugin(
            {
                "enabled": True,
                "notify_mode": {"title": "成功与失败都通知", "value": NOTIFY_MODE_ALL},
            }
        )
        self.assertEqual(plugin._notify_mode, NOTIFY_MODE_ALL)
        self.assertEqual(plugin._notify_label(), "成功与失败都通知")

    def test_slot_fields_as_objects(self) -> None:
        """槽位字段同样是对象形态时也要能解析（账号/方式/登录动作）。"""
        config = dict(default_slot_config())
        config["enabled"] = True
        config["target_1_method"] = {"title": "命令式（直接发命令）", "value": "命令"}
        config["target_1_account"] = {"title": "账号1(acc1)", "value": "acc1"}
        config["account_1_login_action"] = {"title": "发送验证码", "value": "发送验证码"}
        plugin = TgSignin()
        spawned: list = []
        with mock.patch.object(
            TgSignin,
            "_spawn_background",
            staticmethod(lambda target, name: spawned.append(name)),
        ):
            plugin.init_plugin(config)
        # 对象形态的「登录动作」被正确识别成待执行动作并派发
        self.assertEqual(spawned, ["login-actions"])
        # 派发后动作复位为不操作
        self.assertEqual(plugin._slot_login("acc1")["action"], LOGIN_ACTION_NONE)
        first = plugin._targets[0]
        self.assertEqual(first.sign_type, "command")
        self.assertEqual(first.account_key, "acc1")

    def test_legacy_config_keys_are_dropped(self) -> None:
        """升级后清掉 v1.0.1 遗留的全局登录字段（避免两步密码长期留存）。"""
        config = dict(default_slot_config())
        config.update(
            {
                "enabled": True,
                "notify_on_failure": True,
                "login_account": "acc2",
                "login_code": "85249",
                "login_password": "secret-2fa",
                "notify_mode": {"title": "成功与失败都通知", "value": NOTIFY_MODE_ALL},
            }
        )
        plugin = TgSignin()
        plugin.init_plugin(config)
        self.assertTrue(plugin.config_updates)
        saved = plugin.config_updates[-1]
        for key in ("login_account", "login_code", "login_password", "notify_on_failure"):
            self.assertNotIn(key, saved)
        self.assertEqual(saved["notify_mode"], NOTIFY_MODE_ALL)


class TestLoginDispatch(unittest.TestCase):
    """保存配置时的「登录动作」派发：后台执行一次，并立刻复位为不操作。"""

    @staticmethod
    def _config(**overrides) -> dict:
        """
        构造一份启用状态的默认槽位配置。

        :param overrides: 需要覆盖的字段
        :return dict: 配置字典
        """

        config = dict(default_slot_config())
        config["enabled"] = True
        config.update(overrides)
        return config

    def test_dispatch_spawns_and_resets(self) -> None:
        """选了「发送验证码」：派发一次后台任务，并把动作复位。"""
        plugin = TgSignin()
        spawned: list = []
        with mock.patch.object(
            TgSignin,
            "_spawn_background",
            staticmethod(lambda target, name: spawned.append(name)),
        ):
            plugin.init_plugin(self._config(account_1_login_action=LOGIN_ACTION_SEND))
        self.assertEqual(spawned, ["login-actions"])
        self.assertTrue(plugin.config_updates)
        self.assertEqual(
            plugin.config_updates[-1]["account_1_login_action"], LOGIN_ACTION_NONE
        )

    def test_no_dispatch_without_action(self) -> None:
        """默认动作「不操作」时不派发后台任务、也不写配置。"""
        plugin = TgSignin()
        with mock.patch.object(
            TgSignin,
            "_spawn_background",
            staticmethod(lambda target, name: self.fail("不该派发后台任务")),
        ):
            plugin.init_plugin(self._config())
        self.assertEqual(plugin.config_updates, [])


class TestBackgroundApi(unittest.TestCase):
    """发送验证码 / 确认登录 / 立即签到都改为后台执行（前台立即返回）。"""

    def setUp(self) -> None:
        """构造启用状态的插件，并拦截后台派发。"""
        self.spawned: list = []
        def _fake_spawn(target, name) -> bool:
            """
            记录派发并声明已启动。

            :param target: 忽略
            :param name: 线程名（同时是互斥键）
            :return bool: 恒为 True
            """
            del target
            self.spawned.append(name)
            return True

        patcher = mock.patch.object(
            TgSignin,
            "_spawn_background",
            staticmethod(_fake_spawn),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        config = dict(default_slot_config())
        config["enabled"] = True
        self.plugin = TgSignin()
        self.plugin.init_plugin(config)

    @staticmethod
    def _request(**params) -> MagicMock:
        """
        构造带查询参数的请求替身。

        :param params: 查询参数
        :return MagicMock: 请求替身
        """

        request = MagicMock()
        request.query_params = dict(params)
        request.json = AsyncMock(return_value={})
        return request

    def test_send_code_is_background(self) -> None:
        """点「发送验证码」立刻返回，动作在后台线程里跑。"""
        result = asyncio.run(self.plugin.api_send_code(self._request(account="acc1")))
        self.assertTrue(result["success"])
        self.assertTrue(result["data"]["background"])
        self.assertEqual(self.spawned, ["send-code-acc1"])

    def test_confirm_without_code_is_rejected(self) -> None:
        """验证码没填时不派发后台任务，直接提示。"""
        result = asyncio.run(self.plugin.api_confirm_login(self._request(account="acc1")))
        self.assertFalse(result["success"])
        self.assertIn("验证码为空", result["message"])
        self.assertEqual(self.spawned, [])

    def test_confirm_with_slot_code_is_background(self) -> None:
        """验证码取自账号槽字段时后台派发确认登录。"""
        self.plugin._raw_config["account_1_login_code"] = "12345"
        result = asyncio.run(self.plugin.api_confirm_login(self._request(account="acc1")))
        self.assertTrue(result["success"])
        self.assertEqual(self.spawned, ["confirm-acc1"])

    def test_signin_is_background(self) -> None:
        """点「立即签到」立刻返回，签到在后台线程里跑。"""
        result = asyncio.run(self.plugin.api_signin(self._request()))
        self.assertTrue(result["success"])
        self.assertEqual(self.spawned, ["signin"])

    def test_login_reset_clears_slot_fields(self) -> None:
        """清空动作：三个登录字段都复位。"""
        self.plugin._raw_config["account_2_login_code"] = "99999"
        result = asyncio.run(self.plugin.api_login_reset(self._request(account="acc2")))
        self.assertTrue(result["success"])
        payload = self.plugin.config_updates[-1]
        self.assertEqual(payload["account_2_login_action"], LOGIN_ACTION_NONE)
        self.assertEqual(payload["account_2_login_code"], "")
        self.assertEqual(payload["account_2_login_password"], "")


class TestPageExtras(unittest.TestCase):
    """2026-10-11 新增：详情页信息与按钮、落盘字段、仪表盘、配置项形态。"""

    @staticmethod
    def _request(**params) -> MagicMock:
        """
        构造带查询参数的请求替身。

        :param params: 查询参数
        :return MagicMock: 请求替身
        """

        request = MagicMock()
        request.query_params = dict(params)
        request.json = AsyncMock(return_value={})
        return request

    @staticmethod
    def _plugin() -> TgSignin:
        """
        构造启用状态的插件。

        :return TgSignin: 插件实例
        """

        plugin = TgSignin()
        plugin.init_plugin({"enabled": True})
        return plugin

    def test_today_table_and_probe_buttons(self) -> None:
        """详情页含「今日签到状态」表，行内「测试」按钮带 apikey 与 account/bot。"""
        page = self._plugin().get_page()
        blob = json.dumps(page, ensure_ascii=False)
        self.assertIn("今日签到状态", blob)
        probes = [
            node
            for node in _walk(page)
            if node.get("component") == "VBtn" and node.get("text") == "测试"
        ]
        self.assertTrue(probes, "目标行应有「测试」按钮")
        for node in probes:
            params = node["events"]["click"]["params"]
            self.assertIn("apikey", params)
            self.assertIn("account", params)
            self.assertIn("bot", params)

    def test_today_table_with_today_records(self) -> None:
        """
        回归：state 里「今天已有记录」时今日状态表必须能渲染。

        修复前 ``_today_rows`` 的 ``next_text`` 只在「需要重试」分支赋值，
        今天已有成败记录（但无需重试）时会抛 UnboundLocalError，
        整个详情页再次变成「数据加载失败」（2026-10-11 端到端实测发现）。
        """
        plugin = self._plugin()
        data_dir = plugin.get_data_path()
        state = load_state(data_dir)
        stamp = time.time()
        today = today_text()
        state["signin_state"] = {
            "acc1|@bb_emby_bot": {
                "date": today,
                "ok": True,
                "ok_today": True,
                "attempts": 1,
                "last_attempt_at": stamp,
            },
            "acc1|@HG_Emby_bot": {
                "date": today,
                "ok": False,
                "ok_today": False,
                "attempts": 2,
                "last_attempt_at": stamp,
            },
            "acc1|@okemby_bot": {"date": "2000-01-01"},
        }
        save_state(data_dir, state)
        blob = json.dumps(plugin.get_page(), ensure_ascii=False)
        self.assertIn("✅ 已成功", blob)
        self.assertIn("❌ 上次失败", blob)
        self.assertIn("—（今天还没跑）", blob)
        # 「最后尝试」列填的是本地时间文本，不是空值
        expected = datetime.fromtimestamp(stamp, TZ).strftime("%Y-%m-%d %H:%M:%S")
        self.assertIn(expected, blob)

    def test_logout_moved_to_config_form(self) -> None:
        """
        退出登录挪到配置表单：按钮带原生确认脚本，详情页不再有退出按钮。

        配置表单渲染器只认 ``props.on*`` 字符串脚本（``events`` 不生效），
        所以用 ``confirm`` 弹窗 + 回填「登录动作」的方式实现（2026-10-11 用户定案）。
        """
        plugin = self._plugin()
        page_texts = {
            node.get("text")
            for node in _walk(plugin.get_page())
            if node.get("component") == "VBtn"
        }
        self.assertNotIn("退出", page_texts)
        self.assertNotIn("确认退出", page_texts)
        form, _ = plugin.get_form()
        buttons = [
            node
            for node in _walk(form)
            if node.get("component") == "VBtn" and node.get("text") == "退出登录"
        ]
        self.assertEqual(len(buttons), MAX_ACCOUNT_SLOTS, "每个账号槽一枚「退出登录」")
        script = buttons[0]["props"]["onClick"]
        self.assertTrue(script.startswith("function ()"))
        self.assertIn("confirm(", script)
        self.assertIn("model.account_1_login_action", script)
        self.assertIn(f'"{LOGIN_ACTION_LOGOUT}"', script)
        self.assertIn("show", buttons[0]["props"])

    def test_logout_action_deletes_session(self) -> None:
        """配置页选「退出登录」保存后：session 与登录记录都被清掉。"""
        plugin = self._plugin()
        data_dir = plugin.get_data_path()
        sessions = data_dir / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        (sessions / "acc_acc1.session").write_bytes(b"x")
        record_login(data_dir, "acc1", {"name": "t", "username": "u"})
        asyncio.run(
            plugin._execute_login_actions([("acc1", LOGIN_ACTION_LOGOUT, "", "")])
        )
        self.assertFalse((sessions / "acc_acc1.session").exists())
        self.assertNotIn("acc1", load_state(data_dir).get("accounts") or {})

    def test_result_rows_have_retry_buttons(self) -> None:
        """结果表行内「重试」按钮带 apikey 与 account/bot。"""
        plugin = self._plugin()
        record_run(
            plugin.get_data_path(),
            [
                {
                    "time": "2026-10-11 00:01:00",
                    "account": "acc1",
                    "bot": "@okemby_bot",
                    "method": "按钮式",
                    "ok": False,
                    "reply": "",
                    "error": "失败",
                    "status": "失败",
                }
            ],
            "定时",
            "0/1 成功，1 项失败",
        )
        retries = [
            node
            for node in _walk(plugin.get_page())
            if node.get("component") == "VBtn" and node.get("text") == "重试"
        ]
        self.assertEqual(len(retries), 1)
        params = retries[0]["events"]["click"]["params"]
        self.assertEqual(params["account"], "acc1")
        self.assertEqual(params["bot"], "@okemby_bot")

    def test_history_keeps_status_and_alert(self) -> None:
        """history 落盘保留 status/alert/ai_*（详情页 AI 复核列不再恒空的前提）。"""
        plugin = self._plugin()
        record_run(
            plugin.get_data_path(),
            [
                {
                    "time": "t",
                    "account": "acc1",
                    "bot": "@a",
                    "ok": True,
                    "status": "今日已签到",
                    "alert": "您今天已经签到过了",
                    "ai_state": "judged",
                    "ai_verdict": "repeated",
                    "ai_keywords": ["赌狗签到"],
                }
            ],
            "定时",
        )
        item = load_state(plugin.get_data_path())["history"][-1]
        self.assertEqual(item["status"], "今日已签到")
        self.assertEqual(item["alert"], "您今天已经签到过了")
        self.assertEqual(item["ai_verdict"], "repeated")
        self.assertEqual(item["ai_keywords"], ["赌狗签到"])

    def test_dashboard_returns_cards(self) -> None:
        """仪表盘返回 col 配置 / 全局配置 / 页面元素三段结构。"""
        plugin = self._plugin()
        meta = TgSignin.get_dashboard_meta()
        self.assertEqual(meta[0]["key"], "main")
        cols, conf, elements = plugin.get_dashboard("main")
        self.assertIn("cols", cols)
        self.assertIn("title", conf)
        self.assertTrue(elements)

    def test_reset_keeps_raw_ai_confirm_flag(self) -> None:
        """「清空验证码/密码」不会顺手打开「自动使用 AI 确认」（只开 AI 归纳时）。"""
        plugin = TgSignin()
        config = dict(default_slot_config())
        config.update(
            {
                "enabled": True,
                "ai_confirm_enabled": False,
                "ai_keyword_autofill": True,
            }
        )
        plugin.init_plugin(config)
        self.assertTrue(plugin._ai_judge.enabled)
        asyncio.run(plugin.api_login_reset(self._request()))
        self.assertFalse(plugin._raw_config["ai_confirm_enabled"])

    def test_dedupe_returns_busy_message(self) -> None:
        """同名任务在跑时接口回「已有任务在运行」，不重复启动。"""
        plugin = self._plugin()
        with mock.patch.object(
            TgSignin, "_spawn_background", staticmethod(lambda target, name: False)
        ):
            result = asyncio.run(plugin.api_signin(self._request()))
        self.assertFalse(result["success"])
        self.assertIn("已有签到任务在运行", result["message"])

    def test_keyword_fields_are_textareas(self) -> None:
        """关键词三栏用多行文本（AI 自动加词后单行看不清）。"""
        form, _ = self._plugin().get_form()
        for model in ("success_keywords", "repeated_keywords", "failure_keywords"):
            node = next(
                item
                for item in _walk(form)
                if isinstance(item.get("props"), dict)
                and item["props"].get("model") == model
            )
            self.assertEqual(node["component"], "VTextarea")

    def test_number_fields_declare_range(self) -> None:
        """并发上限 / 重试间隔声明取值范围（与后端 clamp 口径一致）。"""
        form, _ = self._plugin().get_form()
        for model, low in (("concurrency", 1), ("retry_interval_hours", 0)):
            node = next(
                item
                for item in _walk(form)
                if isinstance(item.get("props"), dict)
                and item["props"].get("model") == model
            )
            self.assertEqual(node["props"].get("min"), low)
            self.assertIn("max", node["props"])


if __name__ == "__main__":
    unittest.main()
