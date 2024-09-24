"""
签到执行测试：按钮式与命令式两条路径。

用假的 TelegramClient 复现交接单里的真实形态：
- 按钮式：发 ``/start`` 后返回带 inline 按钮的消息，点击后返回签到结果；
- 命令式：发命令后返回文本回复；
并覆盖「找不到按钮」「bot 无回复」「找不到 bot」三类失败分支。
"""

import asyncio
import unittest
from unittest import mock

import tests  # noqa: F401  触发宿主桩与插件路径注入

from tgsignin.core import signin as signin_mod
from tgsignin.core.config import SIGN_TYPE_BUTTON, SIGN_TYPE_COMMAND, BotTarget
from tgsignin.core.signin import signin_one, summarize_results


class _FakeAsyncio:
    """asyncio 替身：把 sleep 变成空操作，让测试毫秒级完成。"""

    @staticmethod
    async def sleep(_seconds: float) -> None:
        """
        空操作睡眠。

        :param _seconds: 忽略的秒数
        :return None
        """
        return None


class _FakeButton:
    """inline 按钮替身：点击时把文字记到 sink。"""

    def __init__(self, text: str, sink: list) -> None:
        """
        构造按钮。

        :param text: 按钮文字
        :param sink: 点击记录列表
        """
        self.text = text
        self._sink = sink

    async def click(self) -> None:
        """点击并记录。"""
        self._sink.append(self.text)


class _FakeMessage:
    """消息替身。"""

    def __init__(self, text: str = "", out: bool = False, buttons=None) -> None:
        """
        构造消息。

        :param text: 文本
        :param out: 是否为自己发出
        :param buttons: 按钮矩阵
        """
        self.text = text
        self.out = out
        self.buttons = buttons


class _FakeClient:
    """TelegramClient 替身：按脚本依次返回消息。"""

    def __init__(self, batches, entity_error: bool = False) -> None:
        """
        构造客户端。

        :param batches: ``get_messages`` 依次返回的消息批次
        :param entity_error: 是否让 get_entity 抛错
        """
        self._batches = list(batches)
        self._entity_error = entity_error
        self.sent: list = []
        self.clicks: list = []
        self.connected = False

    def make_buttons(self, texts) -> list:
        """
        生成一批按钮行。

        :param texts: 按钮文字列表
        :return list: 按钮矩阵
        """
        return [[_FakeButton(text, self.clicks) for text in texts]]

    async def get_entity(self, username: str):
        """
        返回 bot 实体。

        :param username: bot 用户名
        :return object: 实体
        """
        if self._entity_error:
            raise ValueError(f"cannot find any entity corresponding to {username}")
        return {"username": username}

    async def send_message(self, entity, text: str) -> None:
        """
        记录发出的消息。

        :param entity: 目标实体
        :param text: 文本
        :return None
        """
        self.sent.append(text)

    async def get_messages(self, entity, limit: int = 5):
        """
        按脚本返回消息批次。

        :param entity: 目标实体
        :param limit: 条数上限（替身忽略）
        :return list: 消息列表
        """
        del entity, limit
        if self._batches:
            return self._batches.pop(0)
        return []

    async def connect(self) -> None:
        """模拟连接。"""
        self.connected = True

    async def disconnect(self) -> None:
        """模拟断开。"""
        self.connected = False

    async def is_user_authorized(self) -> bool:
        """桩：视为已授权。"""
        return True


class TestSigninButtonMode(unittest.TestCase):
    """按钮式签到。"""

    def setUp(self) -> None:
        """替换 sleep，避免测试真的等待。"""
        patcher = mock.patch.object(signin_mod, "asyncio", _FakeAsyncio)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_click_button_success(self) -> None:
        """找到含关键词的按钮并点击，成功读回回复。"""
        client = _FakeClient(batches=[])
        # 第一条消息带 inline 按钮，第二条是点击后的签到回复
        client._batches = [
            [_FakeMessage("请选择功能", buttons=client.make_buttons(["🎯 签到"]))],
            [_FakeMessage("🎉 签到成功 | 10 子弹")],
        ]
        target = BotTarget(
            account_key="acc1",
            bot_username="@bb_emby_bot",
            sign_type=SIGN_TYPE_BUTTON,
            action_text="签到",
        )
        result = asyncio.run(signin_one(client, target))
        self.assertTrue(result["ok"])
        self.assertIn("签到成功", result["reply"])
        self.assertEqual(client.sent, ["/start"])
        self.assertEqual(client.clicks, ["🎯 签到"])

    def test_button_not_found(self) -> None:
        """菜单里没有含关键词的按钮时给出可读错误。"""
        client = _FakeClient(
            batches=[[_FakeMessage("无按钮消息")]]
        )
        target = BotTarget(
            account_key="acc1", bot_username="@x", action_text="签到"
        )
        result = asyncio.run(signin_one(client, target))
        self.assertFalse(result["ok"])
        self.assertIn("没找到含「签到」的按钮", result["error"])

    def test_entity_error(self) -> None:
        """bot 不存在时不抛异常，改为返回失败结果。"""
        client = _FakeClient(batches=[], entity_error=True)
        target = BotTarget(account_key="acc1", bot_username="@nope")
        result = asyncio.run(signin_one(client, target))
        self.assertFalse(result["ok"])
        self.assertIn("找不到 bot", result["error"])


class TestSigninCommandMode(unittest.TestCase):
    """命令式签到。"""

    def setUp(self) -> None:
        """替换 sleep。"""
        patcher = mock.patch.object(signin_mod, "asyncio", _FakeAsyncio)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_command_success(self) -> None:
        """发命令后收到回复视为成功。"""
        client = _FakeClient(batches=[[_FakeMessage("✅ 签到成功！获得 5 积分")]])
        target = BotTarget(
            account_key="acc1",
            bot_username="@HDHaven_Bot",
            sign_type=SIGN_TYPE_COMMAND,
            action_text="/checkin",
        )
        result = asyncio.run(signin_one(client, target))
        self.assertTrue(result["ok"])
        self.assertEqual(client.sent, ["/checkin"])
        self.assertIn("签到成功", result["reply"])

    def test_command_no_reply(self) -> None:
        """没等到回复算失败。"""
        client = _FakeClient(batches=[[]])
        target = BotTarget(
            account_key="acc1",
            bot_username="@b",
            sign_type=SIGN_TYPE_COMMAND,
            action_text="/checkin",
        )
        result = asyncio.run(signin_one(client, target))
        self.assertFalse(result["ok"])
        self.assertIn("没等到 bot 回复", result["error"])

    def test_unknown_sign_type(self) -> None:
        """未知签到方式返回可读错误。"""
        client = _FakeClient(batches=[[]])
        target = BotTarget(account_key="acc1", bot_username="@b", sign_type="weird")
        result = asyncio.run(signin_one(client, target))
        self.assertFalse(result["ok"])
        self.assertIn("未知的签到方式", result["error"])


class TestSummarize(unittest.TestCase):
    """结果摘要。"""

    def test_counts(self) -> None:
        """统计成功条数。"""
        self.assertEqual(
            summarize_results([{"ok": True}, {"ok": False}, {"ok": True}]),
            "2/3 成功",
        )

    def test_empty(self) -> None:
        """无结果时给出说明。"""
        self.assertEqual(summarize_results([]), "没有可执行的目标")


if __name__ == "__main__":
    unittest.main()
