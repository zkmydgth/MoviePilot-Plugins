"""
两阶段登录测试（不联网，用假的 TelegramClient 与假 telethon 模块）。

覆盖：发码成功/已登录/缺手机号、确认登录成功、需要两步验证、密码补齐后成功、
待登录状态过期，以及「未先发码就确认」的提示。
"""

import asyncio
import json
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import tests  # noqa: F401  触发宿主桩与插件路径注入

from tgsignin.core import login as login_mod
from tgsignin.core.config import AccountConfig
from tgsignin.core.login import confirm_login, load_pending, pending_path, send_code


class _SessionPasswordNeededError(Exception):
    """两步验证所需异常的替身。"""


class _FakeErrors:
    """telethon.errors 替身。"""

    SessionPasswordNeededError = _SessionPasswordNeededError


class _FakeTelethon:
    """telethon 模块替身。"""

    errors = _FakeErrors


class _SentCode:
    """send_code_request 返回值替身。"""

    def __init__(self, phone_code_hash: str) -> None:
        """
        构造返回值。

        :param phone_code_hash: 假的 hash
        """
        self.phone_code_hash = phone_code_hash


class _Me:
    """get_me 返回值替身。"""

    first_name = "测试用户"
    last_name = ""
    username = "testuser"
    id = 100000001
    phone = "+12025550101"


class _FakeLoginClient:
    """登录用客户端替身。"""

    def __init__(
        self,
        authorized: bool = False,
        need_password: bool = False,
        code_error: Exception = None,
    ) -> None:
        """
        构造客户端。

        :param authorized: 是否已登录
        :param need_password: sign_in 时是否抛两步验证异常
        :param code_error: send_code_request 要抛的异常
        """
        self.authorized = authorized
        self.need_password = need_password
        self.code_error = code_error
        self.sent_phones: list = []
        self.signin_calls: list = []
        self.connected = False

    async def connect(self) -> None:
        """模拟连接。"""
        self.connected = True

    async def disconnect(self) -> None:
        """模拟断开。"""
        self.connected = False

    async def is_user_authorized(self) -> bool:
        """
        返回登录态。

        :return bool: 是否已登录
        """
        return self.authorized

    async def send_code_request(self, phone: str) -> _SentCode:
        """
        记录发码请求并返回假 hash。

        :param phone: 手机号
        :return _SentCode: 假返回值
        """
        if self.code_error:
            raise self.code_error
        self.sent_phones.append(phone)
        return _SentCode("hash-abc")

    async def get_me(self) -> _Me:
        """
        返回当前登录用户。

        :return _Me: 假用户
        """
        return _Me()

    async def sign_in(self, **kwargs) -> None:
        """
        记录登录调用；首次调用可抛两步验证异常。

        :param kwargs: 登录参数
        :return None
        """
        self.signin_calls.append(kwargs)
        if self.need_password and "password" not in kwargs:
            raise _SessionPasswordNeededError("password needed")


class TestSendCode(unittest.TestCase):
    """阶段一：发送验证码。"""

    def setUp(self) -> None:
        """准备临时数据目录。"""
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        self.account = AccountConfig(key="acc1", label="一号", phone="+12025550101")
        self.addCleanup(self._tmp.cleanup)

    def test_send_code_success(self) -> None:
        """发码成功并写入待登录状态。"""
        client = _FakeLoginClient()
        with mock.patch.object(login_mod, "build_client", return_value=client):
            result = asyncio.run(send_code(self.data_dir, self.account, None))
        self.assertTrue(result["ok"])
        self.assertIn("验证码已发往", result["message"])
        self.assertEqual(client.sent_phones, ["+12025550101"])
        pending = load_pending(self.data_dir)
        self.assertEqual(pending["acc1"]["phone_code_hash"], "hash-abc")

    def test_send_code_when_already_authorized(self) -> None:
        """已登录时不再发码，直接告知身份。"""
        client = _FakeLoginClient(authorized=True)
        with mock.patch.object(login_mod, "build_client", return_value=client):
            result = asyncio.run(send_code(self.data_dir, self.account, None))
        self.assertTrue(result["ok"])
        self.assertTrue(result["already"])
        self.assertIn("testuser", result["message"])
        self.assertEqual(client.sent_phones, [])

    def test_send_code_without_phone(self) -> None:
        """没填手机号时直接失败。"""
        account = AccountConfig(key="acc1")
        result = asyncio.run(send_code(self.data_dir, account, None))
        self.assertFalse(result["ok"])
        self.assertIn("没填手机号", result["message"])

    def test_send_code_error_is_reported(self) -> None:
        """发码异常被翻译成可读消息。"""
        client = _FakeLoginClient(code_error=ValueError("PHONE_NUMBER_INVALID"))
        with mock.patch.object(login_mod, "build_client", return_value=client):
            result = asyncio.run(send_code(self.data_dir, self.account, None))
        self.assertFalse(result["ok"])
        self.assertIn("PHONE_NUMBER_INVALID", result["message"])


class TestConfirmLogin(unittest.TestCase):
    """阶段二：确认登录。"""

    def setUp(self) -> None:
        """准备临时数据目录与待登录状态。"""
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        self.account = AccountConfig(key="acc1", label="一号", phone="+12025550101")
        self.addCleanup(self._tmp.cleanup)
        pending_path(self.data_dir).write_text(
            json.dumps(
                {
                    "acc1": {
                        "phone": self.account.phone,
                        "phone_code_hash": "hash-abc",
                        "ts": time.time(),
                    }
                }
            ),
            encoding="utf-8",
        )

    def test_confirm_success(self) -> None:
        """验证码正确时登录成功并清理待登录状态。"""
        client = _FakeLoginClient()
        with mock.patch.object(login_mod, "build_client", return_value=client), \
                mock.patch.object(login_mod, "import_telethon", return_value=_FakeTelethon):
            result = asyncio.run(confirm_login(self.data_dir, self.account, "12345", "", None))
        self.assertTrue(result["ok"])
        self.assertEqual(result["me"]["username"], "testuser")
        self.assertEqual(client.signin_calls[0]["phone_code_hash"], "hash-abc")
        self.assertNotIn("acc1", load_pending(self.data_dir))

    def test_confirm_requires_password(self) -> None:
        """启用两步验证但没给密码时给出明确提示。"""
        client = _FakeLoginClient(need_password=True)
        with mock.patch.object(login_mod, "build_client", return_value=client), \
                mock.patch.object(login_mod, "import_telethon", return_value=_FakeTelethon):
            result = asyncio.run(confirm_login(self.data_dir, self.account, "12345", "", None))
        self.assertFalse(result["ok"])
        self.assertTrue(result["need_password"])

    def test_confirm_with_password(self) -> None:
        """补上两步验证密码后登录成功。"""
        client = _FakeLoginClient(need_password=True)
        with mock.patch.object(login_mod, "build_client", return_value=client), \
                mock.patch.object(login_mod, "import_telethon", return_value=_FakeTelethon):
            result = asyncio.run(
                confirm_login(self.data_dir, self.account, "12345", "my-pass", None)
            )
        self.assertTrue(result["ok"])
        self.assertIn("password", client.signin_calls[-1])

    def test_confirm_without_pending(self) -> None:
        """没先发码就确认，提示先发送验证码。"""
        login_mod.clear_pending(self.data_dir, "acc1")
        result = asyncio.run(confirm_login(self.data_dir, self.account, "12345", "", None))
        self.assertFalse(result["ok"])
        self.assertIn("请先点「发送验证码」", result["message"])

    def test_confirm_expired(self) -> None:
        """待登录状态过期后要求重新发码。"""
        pending_path(self.data_dir).write_text(
            json.dumps(
                {
                    "acc1": {
                        "phone": self.account.phone,
                        "phone_code_hash": "hash-abc",
                        "ts": time.time() - login_mod.PENDING_TTL_SECONDS - 60,
                    }
                }
            ),
            encoding="utf-8",
        )
        result = asyncio.run(confirm_login(self.data_dir, self.account, "12345", "", None))
        self.assertFalse(result["ok"])
        self.assertIn("过期", result["message"])

    def test_empty_code(self) -> None:
        """验证码为空直接失败。"""
        result = asyncio.run(confirm_login(self.data_dir, self.account, "", "", None))
        self.assertFalse(result["ok"])
        self.assertIn("验证码为空", result["message"])


class TestPhoneMasking(unittest.TestCase):
    """手机号打码：日志与状态文件里不再出现完整号码（2026-10-11 加）。"""

    def test_send_code_message_is_masked(self) -> None:
        """发码提示里的手机号是打码形态。"""
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            account = AccountConfig(key="acc1", label="一号", phone="+12025550101")
            client = _FakeLoginClient()
            with mock.patch.object(login_mod, "build_client", return_value=client):
                result = asyncio.run(send_code(data_dir, account, None))
            self.assertTrue(result["ok"])
            self.assertIn("+1******0101", result["message"])
            self.assertNotIn("+12025550101", result["message"])

    def test_describe_me_masks_phone(self) -> None:
        """describe_me 只回打码手机号（写进 state.json 的也是打码值）。"""
        info = login_mod.describe_me(_Me())
        self.assertEqual(info["phone"], "+1******0101")
        self.assertNotIn("+12025550101", json.dumps(info, ensure_ascii=False))

    def test_pending_file_is_private(self) -> None:
        """待登录状态文件权限 0600（含 phone_code_hash，属敏感数据）。"""
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            login_mod.save_pending(
                data_dir,
                {"acc1": {"phone": "+12025550101", "phone_code_hash": "h", "ts": 1}},
            )
            mode = stat.S_IMODE(pending_path(data_dir).stat().st_mode)
            self.assertEqual(mode, 0o600)


if __name__ == "__main__":
    unittest.main()
