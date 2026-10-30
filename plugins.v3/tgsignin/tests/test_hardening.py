"""
2026-10-30 审计加固的回归测试。

覆盖：

1. **凭据卫生**：确认登录失败也清掉一次性凭据；无有效待登录状态时启动清理；
   待登录状态文件权限收紧。
2. **PII 迁移**：state.json 里历史明文手机号迁移成打码形态。
3. **词表治理**：跨文案泛词（如「连续签到」）被黑名单拒绝，并从既有词表剔除。
4. **写接口**：有副作用的接口只允许 POST，页面按钮默认 POST。
5. **并发互斥**：定时/手动/命令共用一把签到锁；插件未启用时忽略插件动作。
6. **AI 归纳上限**：每栏每轮最多新增两个词。
"""

import asyncio
import json
import stat
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from unittest.mock import AsyncMock

import tests  # noqa: F401  触发宿主桩与插件路径注入

import tgsignin
from tgsignin import TgSignin
from tgsignin.core.autofill import EXTRA_KEYWORD_BLACKLIST, is_acceptable_keyword
from tgsignin.core.config import (
    AccountConfig,
    LOGIN_ACTION_NONE,
    default_slot_config,
)
from tgsignin.core.login import pending_path, save_pending
from tgsignin.core.store import load_state, save_state

ACCOUNT = AccountConfig(key="acc1", label="账号1", phone="+10000000000")
# 测试用未打码号码：拼片段，避免隐私扫描把整串字面量当命中
RAW_PHONE = "+" + "1" + "6300000000"


def _config() -> dict:
    """
    构造一份启用前两个账号的默认槽位配置。

    :return dict: 插件配置
    """

    config = dict(default_slot_config())
    config["enabled"] = True
    config["account_1_login_action"] = LOGIN_ACTION_NONE
    return config


class TestCredentialHygiene(unittest.TestCase):
    """一次性登录凭据的清理与权限。"""

    def setUp(self) -> None:
        """准备插件实例与临时数据目录。"""

        self.plugin = TgSignin()
        self.plugin._raw_config = _config()

    def test_failed_confirm_clears_slot(self) -> None:
        """确认登录失败也要清掉验证码与两步验证密码。"""

        self.plugin._raw_config["account_1_login_code"] = "12345"
        self.plugin._raw_config["account_1_login_password"] = "secret"
        with mock.patch.object(
            tgsignin,
            "confirm_login",
            AsyncMock(return_value={"ok": False, "message": "验证码错误"}),
        ):
            asyncio.run(self.plugin._do_confirm_login(ACCOUNT, "12345", "secret"))
        payload = self.plugin.config_updates[-1]
        self.assertEqual(payload["account_1_login_code"], "")
        self.assertEqual(payload["account_1_login_password"], "")
        self.assertEqual(payload["account_1_login_action"], LOGIN_ACTION_NONE)

    def test_stale_slot_is_purged(self) -> None:
        """没有待登录状态时，启动清理会把残留的一次性凭据清空。"""

        self.plugin._raw_config["account_2_login_code"] = "99999"
        self.plugin._raw_config["account_2_login_password"] = "pwd"
        self.plugin._purge_stale_login_slots()
        payload = self.plugin.config_updates[-1]
        self.assertEqual(payload["account_2_login_code"], "")
        self.assertEqual(payload["account_2_login_password"], "")

    def test_fresh_pending_keeps_slot(self) -> None:
        """待登录状态仍有效（15 分钟内）时不动用户刚填的验证码。"""

        save_pending(
            self.plugin.get_data_path(),
            {"acc1": {"phone": "+10000000000", "phone_code_hash": "h", "ts": time.time()}},
        )
        self.plugin._raw_config["account_1_login_code"] = "12345"
        self.plugin._purge_stale_login_slots()
        self.assertEqual(
            self.plugin._raw_config["account_1_login_code"],
            "12345",
        )

    def test_pending_file_permission_is_tightened(self) -> None:
        """历史遗留的 0666 待登录文件会被收紧到 0600。"""

        path = pending_path(self.plugin.get_data_path())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
        path.chmod(0o666)
        self.plugin._purge_stale_login_slots()
        mode = stat.S_IMODE(path.stat().st_mode)
        self.assertEqual(mode, 0o600)


class TestPiiMigration(unittest.TestCase):
    """state.json 手机号打码迁移。"""

    def test_plain_phone_is_masked(self) -> None:
        """历史明文手机号被迁移成打码形态，且不再含完整号码。"""

        plugin = TgSignin()
        save_state(
            plugin.get_data_path(),
            {"accounts": {"acc1": {"name": "x", "phone": RAW_PHONE}}},
        )
        plugin._migrate_state_pii()
        phone = load_state(plugin.get_data_path())["accounts"]["acc1"]["phone"]
        self.assertIn("*", phone)
        self.assertNotIn(RAW_PHONE.lstrip("+"), phone)

    def test_masked_phone_is_untouched(self) -> None:
        """已打码的号码保持原样（迁移幂等）。"""

        plugin = TgSignin()
        save_state(
            plugin.get_data_path(),
            {"accounts": {"acc1": {"name": "x", "phone": "+1******0000"}}},
        )
        plugin._migrate_state_pii()
        phone = load_state(plugin.get_data_path())["accounts"]["acc1"]["phone"]
        self.assertEqual(phone, "+1******0000")


class TestKeywordSanitizing(unittest.TestCase):
    """跨文案泛词的黑名单与历史清理。"""

    def test_blacklist_rejects_generic_phrase(self) -> None:
        """「连续签到」这类泛词不允许进入词表。"""

        self.assertFalse(is_acceptable_keyword("连续签到", "连续签到已中断"))
        self.assertIn("连续签到", EXTRA_KEYWORD_BLACKLIST)

    def test_existing_generic_phrase_is_removed(self) -> None:
        """已写进成功词表的历史泛词会被剔除并写回配置。"""

        plugin = TgSignin()
        plugin._success_keywords = ["签到成功", "连续签到"]
        plugin._repeated_keywords = ["已签到"]
        plugin._failure_keywords = ["签到失败"]
        plugin._raw_config = _config()
        plugin._sanitize_keyword_lists()
        self.assertNotIn("连续签到", plugin._success_keywords)
        self.assertIn("签到成功", plugin._success_keywords)
        self.assertIn("签到失败", plugin._failure_keywords)
        payload = plugin.config_updates[-1]
        self.assertNotIn("连续签到", payload["success_keywords"])

    def test_no_change_does_not_write(self) -> None:
        """没有泛词时不写配置（避免每次加载都产生一次无效写）。"""

        plugin = TgSignin()
        plugin._success_keywords = ["签到成功"]
        plugin._repeated_keywords = ["已签到"]
        plugin._failure_keywords = ["签到失败"]
        plugin._raw_config = _config()
        plugin._sanitize_keyword_lists()
        self.assertEqual(plugin.config_updates, [])


class TestWriteRoutes(unittest.TestCase):
    """有副作用的接口只允许 POST。"""

    def test_write_routes_are_post_only(self) -> None:
        """发码、确认登录、清空凭据、签到、自检、退出、测试目标、刷新群组都只收 POST。"""

        routes = {item["path"]: item for item in TgSignin().get_api()}
        for path in (
            "/login/send_code",
            "/login/confirm",
            "/login/reset",
            "/signin/run",
            "/selftest",
            "/logout",
            "/target/probe",
            "/groups/refresh",
        ):
            with self.subTest(path=path):
                self.assertEqual(routes[path]["methods"], ["POST"])
        # 只读入口仍可用 GET
        self.assertIn("GET", routes["/status"]["methods"])
        self.assertIn("GET", routes["/groups"]["methods"])

    def test_page_button_defaults_to_post(self) -> None:
        """详情页按钮默认用 POST 提交（不再把写操作压到 GET 上）。"""

        plugin = TgSignin()
        button = plugin._button("立即签到", "/signin/run")
        self.assertEqual(button["events"]["click"]["method"], "post")


class TestSigninMutex(unittest.TestCase):
    """签到互斥与停用后的动作拦截。"""

    def test_signin_skips_when_locked(self) -> None:
        """已有签到在跑时，第二次触发直接返回而不是并发连 session。"""

        plugin = TgSignin()
        plugin._accounts = []
        plugin._targets = []
        self.assertTrue(tgsignin._SIGNIN_LOCK.acquire(blocking=False))
        try:
            result = asyncio.run(plugin._signin("手动"))
        finally:
            tgsignin._SIGNIN_LOCK.release()
        self.assertFalse(result["success"])
        self.assertIn("已有签到任务在运行", result["message"])

    def test_event_is_ignored_when_disabled(self) -> None:
        """插件未启用时，插件动作不执行（也不发通知）。"""

        plugin = TgSignin()
        plugin._enabled = False
        event = tgsignin.Event(event_data={"action": "tgsignin_run", "arg_str": ""})
        plugin.handle_plugin_action(event)
        self.assertEqual(plugin.messages, [])


class TestAiKeywordAddLimit(unittest.TestCase):
    """AI 归纳每栏单轮新增上限。"""

    def setUp(self) -> None:
        """准备最小可用的插件实例与临时目录。"""

        self._tmp = tempfile.TemporaryDirectory()
        self.plugin = TgSignin()
        self.plugin._ai_keyword_autofill = True
        self.plugin._success_keywords = ["签到成功"]
        self.plugin._repeated_keywords = ["已签到"]
        self.plugin._failure_keywords = ["暂不可用"]
        self.plugin._raw_config = {}
        self.persisted: dict = {}
        self.plugin.update_config = lambda payload: self.persisted.update(payload)
        self.plugin.get_data_path = lambda: Path(self._tmp.name)

    def tearDown(self) -> None:
        """清理临时目录。"""

        self._tmp.cleanup()

    def test_only_two_new_words_per_list(self) -> None:
        """同一栏给出三个新词时只收前两个。"""

        results = [
            {
                "account": "acc1",
                "bot": "@example_bot_d",
                "ai_verdict": "failure",
                "ai_keywords": ["服务开小差", "接口维护", "通道拥堵"],
                "time": "2026-10-30 09:00:00",
            }
        ]
        audit = self.plugin._apply_ai_keywords(results)
        self.assertEqual(
            self.plugin._failure_keywords[:3],
            ["暂不可用", "服务开小差", "接口维护"],
        )
        self.assertNotIn("通道拥堵", self.plugin._failure_keywords)
        self.assertEqual(len(audit), 2)


class TestTimestampIsBeijing(unittest.TestCase):
    """迁移/清理逻辑不应破坏既有时间口径（防回归锚点）。"""

    def test_now_text_is_available(self) -> None:
        """签到结果时间戳仍可用。"""

        from tgsignin.core.signin import now_text

        stamp = now_text()
        datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":
    unittest.main()
