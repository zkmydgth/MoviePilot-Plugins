"""
签到执行测试：按钮式与命令式两条路径。

用假的 TelegramClient 复现交接单里的真实形态：
- 按钮式：发 ``/start`` 后返回带 inline 按钮的消息，点击后返回签到结果；
- 命令式：发命令后返回文本回复；
并覆盖「找不到按钮」「bot 无回复」「找不到 bot」三类失败分支。
"""

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import tests  # noqa: F401  触发宿主桩与插件路径注入

from tgsignin.core import signin as signin_mod
from tgsignin.core.retry import TZ
from tgsignin.core.config import (
    NOTIFY_MODE_ALL,
    NOTIFY_MODE_FAILURE,
    NOTIFY_MODE_NONE,
    NOTIFY_MODE_SUCCESS,
    SIGN_TYPE_BUTTON,
    SIGN_TYPE_COMMAND,
    AccountConfig,
    BotTarget,
)
from tgsignin.core.signin import (
    STATUS_FAILED,
    STATUS_REPEATED,
    STATUS_SUCCESS,
    STATUS_UNCONFIRMED,
    build_notify_text,
    classify_result,
    run_account,
    signin_one,
    summarize_results,
)


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

    def __init__(self, text: str, sink: list, alert: str = "") -> None:
        """
        构造按钮。

        :param text: 按钮文字
        :param sink: 点击记录列表
        :param alert: 点击后 Telegram 返回的弹窗文本（callback 应答）
        """
        self.text = text
        self._sink = sink
        self._alert = alert

    async def click(self):
        """
        点击并返回 callback 应答（含弹窗文本）。

        :return _FakeCallbackAnswer: 应答替身
        """
        self._sink.append(self.text)
        return _FakeCallbackAnswer(self._alert)


class _FakeCallbackAnswer:
    """BotCallbackAnswer 替身：只带 message/alert。"""

    def __init__(self, message: str = "") -> None:
        """
        构造应答。

        :param message: 弹窗文本
        """
        self.message = message
        self.alert = bool(message)


class _FakeMessage:
    """消息替身。"""

    def __init__(
        self, text: str = "", out: bool = False, buttons=None, date=None
    ) -> None:
        """
        构造消息。

        :param text: 文本
        :param out: 是否为自己发出
        :param buttons: 按钮矩阵
        :param date: 消息时间（带时区）；None 表示不参与时间过滤
        """
        self.text = text
        self.out = out
        self.buttons = buttons
        self.date = date


class _FakeClient:
    """TelegramClient 替身：按脚本依次返回消息。"""

    def __init__(self, batches, entity_error: bool = False, sent_date=None) -> None:
        """
        构造客户端。

        :param batches: ``get_messages`` 依次返回的消息批次
        :param entity_error: 是否让 get_entity 抛错
        :param sent_date: ``send_message`` 返回消息的时间（本次发送时刻）
        """
        self._batches = list(batches)
        self._entity_error = entity_error
        self._sent_date = sent_date
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

    def make_buttons_with_alert(self, texts, alert: str) -> list:
        """
        生成带弹窗提示的按钮行。

        :param texts: 按钮文字列表
        :param alert: 点击后弹窗文本
        :return list: 按钮矩阵
        """
        return [[_FakeButton(text, self.clicks, alert) for text in texts]]

    async def get_entity(self, username: str):
        """
        返回 bot 实体。

        :param username: bot 用户名
        :return object: 实体
        """
        if self._entity_error:
            raise ValueError(f"cannot find any entity corresponding to {username}")
        return {"username": username}

    async def send_message(self, entity, text: str):
        """
        记录发出的消息，并返回带时间戳的「自己发出」消息。

        :param entity: 目标实体
        :param text: 文本
        :return _FakeMessage: 已发送消息（带 date，供时间归属过滤）
        """
        self.sent.append(text)
        return _FakeMessage(text=text, out=True, date=self._sent_date)

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

    def test_button_alert_is_captured(self) -> None:
        """点击按钮时 bot 的弹窗提示（callback 应答）要被记录，并据此分档。"""
        client = _FakeClient(batches=[])
        client._batches = [
            [
                _FakeMessage(
                    "请选择功能",
                    buttons=client.make_buttons_with_alert(
                        ["🎯 签到"], "您今天已经签到过了"
                    ),
                )
            ],
            [_FakeMessage("🍉 你好鸭 请选择功能")],
        ]
        target = BotTarget(
            account_key="acc1",
            bot_username="@okemby_bot",
            sign_type=SIGN_TYPE_BUTTON,
            action_text="签到",
        )
        result = asyncio.run(signin_one(client, target))
        self.assertTrue(result["ok"])
        self.assertEqual(result["alert"], "您今天已经签到过了")
        self.assertEqual(result["status"], STATUS_REPEATED)


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


class TestClassifyResult(unittest.TestCase):
    """结果分类：签到成功 / 今日已签到 / 未确认 / 失败。"""

    def test_failed_when_not_ok(self) -> None:
        """失败优先。"""
        self.assertEqual(classify_result("任意", False, "点按钮「签到」"), STATUS_FAILED)

    def test_success_marker(self) -> None:
        """回复含「签到成功」判为签到成功。"""
        self.assertEqual(
            classify_result("✅ 签到成功！获得 5 积分、5 经验", True, "发命令「/checkin」"),
            STATUS_SUCCESS,
        )

    def test_repeated_marker(self) -> None:
        """回复含「已签到」判为今日已签到。"""
        self.assertEqual(
            classify_result("✅ 今日已签到，明天再来。", True, "发命令「/checkin」"),
            STATUS_REPEATED,
        )

    def test_button_menu_reply_with_prior_success_is_repeated(self) -> None:
        """按钮式只回菜单 + 今天此前已成功过：判为今日已签到。"""
        self.assertEqual(
            classify_result(
                "🍉 你好鸭 请选择功能",
                True,
                "点按钮「签到」",
                already_signed_today=True,
            ),
            STATUS_REPEATED,
        )

    def test_button_menu_reply_without_prior_success_fails(self) -> None:
        """按钮式只回菜单 + 今天此前没成功过：判为失败（bot 没给出签到结果）。"""
        self.assertEqual(
            classify_result("🍉 你好鸭 请选择功能", True, "点按钮「签到」"),
            STATUS_FAILED,
        )

    def test_alert_repeated(self) -> None:
        """bot 用弹窗提示已签到时（回复只有菜单）同样判为今日已签到。"""
        self.assertEqual(
            classify_result(
                "🍉 你好鸭 请选择功能",
                True,
                "点按钮「签到」",
                "您今天已经签到过了",
            ),
            STATUS_REPEATED,
        )

    def test_alert_success(self) -> None:
        """弹窗里出现「签到成功」时判为签到成功。"""
        self.assertEqual(
            classify_result("", True, "点按钮「签到」", "签到成功，获得 5 积分"),
            STATUS_SUCCESS,
        )

    def test_command_reply_without_marker_is_unconfirmed(self) -> None:
        """命令式回复里没有成功标记时判为未确认（不硬说成功）。"""
        self.assertEqual(
            classify_result("我不知道你在说什么", True, "发命令「/checkin」"),
            STATUS_UNCONFIRMED,
        )


class TestBuildNotifyText(unittest.TestCase):
    """通知正文生成：四档通知方式 + 明细条数上限 + 摘要截断。"""

    @staticmethod
    def _results(ok_count: int, fail_count: int) -> list:
        """
        构造指定成功/失败条数的结果列表。

        :param ok_count: 成功条数
        :param fail_count: 失败条数
        :return list: 结果列表
        """

        results = []
        for index in range(ok_count):
            results.append(
                {
                    "account": "acc1",
                    "bot": f"@ok{index}",
                    "ok": True,
                    "reply": "🎉 签到成功 | 10 子弹 💴 当前持有 | 5 子弹 ⏳ 签到日期 | 2026-10-06",
                    "error": "",
                    "time": "2026-10-06 09:00:01",
                }
            )
        for index in range(fail_count):
            results.append(
                {
                    "account": "acc2",
                    "bot": f"@bad{index}",
                    "ok": False,
                    "reply": "",
                    "error": "最近 5 条消息里没找到含「签到」的按钮",
                    "time": "2026-10-06 09:00:02",
                }
            )
        return results

    def test_failure_mode_only_on_failure(self) -> None:
        """仅失败时：全绿不发；有失败项才发，且带失败明细。"""
        self.assertIsNone(
            build_notify_text(self._results(2, 0), "定时", NOTIFY_MODE_FAILURE)
        )
        text = build_notify_text(self._results(1, 1), "定时", NOTIFY_MODE_FAILURE)
        self.assertIsNotNone(text)
        self.assertIn("1/2 成功，1 项失败", text)
        self.assertIn("失败明细：", text)
        self.assertIn("@bad0", text)
        # 头部时间取本次结果的首条时间
        self.assertIn("2026-10-06 09:00:01", text)

    def test_success_mode_requires_all_ok(self) -> None:
        """仅成功时：只有全部成功才发，正文逐条列 bot 回复。"""
        self.assertIsNone(
            build_notify_text(self._results(1, 1), "定时", NOTIFY_MODE_SUCCESS)
        )
        text = build_notify_text(self._results(2, 0), "定时", NOTIFY_MODE_SUCCESS)
        self.assertIsNotNone(text)
        self.assertIn("2/2 全部成功", text)
        self.assertIn("@ok0", text)
        self.assertNotIn("失败明细：", text)

    def test_all_mode_covers_both(self) -> None:
        """都通知：成功与失败场景都发。"""
        self.assertIsNotNone(
            build_notify_text(self._results(1, 1), "手动", NOTIFY_MODE_ALL)
        )
        self.assertIsNotNone(
            build_notify_text(self._results(2, 0), "手动", NOTIFY_MODE_ALL)
        )

    def test_none_mode_and_empty_results(self) -> None:
        """不通知模式与空结果都不发。"""
        self.assertIsNone(
            build_notify_text(self._results(0, 2), "定时", NOTIFY_MODE_NONE)
        )
        self.assertIsNone(build_notify_text([], "定时", NOTIFY_MODE_ALL))

    def test_detail_cap_and_line_length(self) -> None:
        """失败明细最多 10 条 + 省略提示；每行摘要被截断。"""
        text = build_notify_text(self._results(0, 12), "定时", NOTIFY_MODE_FAILURE)
        self.assertIsNotNone(text)
        self.assertIn("…等 12 项", text)
        detail_lines = [line for line in text.splitlines() if line.startswith("- ")]
        self.assertEqual(len(detail_lines), 10)
        for line in detail_lines:
            self.assertLessEqual(len(line), 90)

    def test_prefers_account_label(self) -> None:
        """有显示名时通知里显示「账号1(acc1)」而不是纯标识。"""
        results = self._results(0, 2)
        results[0]["account_label"] = "账号1(acc1)"
        results[1]["account_label"] = "账号2(acc2)"
        text = build_notify_text(results, "定时", NOTIFY_MODE_FAILURE)
        self.assertIsNotNone(text)
        self.assertIn("账号1(acc1) → @bad0", text)
        self.assertIn("账号2(acc2) → @bad1", text)

    def test_repeated_status_in_success_text(self) -> None:
        """全绿但有重复签到时：首行标注「其中 N 项为今日已签到」，行内标明原因。"""
        results = self._results(2, 0)
        results[0]["status"] = STATUS_SUCCESS
        results[1]["status"] = STATUS_REPEATED
        results[1]["reply"] = "🍉 你好鸭 请选择功能"
        text = build_notify_text(results, "手动", NOTIFY_MODE_SUCCESS)
        self.assertIsNotNone(text)
        self.assertIn("2/2 全部成功（其中 1 项为今日已签到）", text)
        self.assertIn("今日已签到（按钮式：已点击，bot 未返回签到结果）", text)

    def test_status_notes_distinguish_cases(self) -> None:
        """三种「已签到」情形与「未确认」在通知里要能区分开。"""

        def item(bot: str, status: str, reply: str = "", alert: str = "") -> dict:
            """
            构造一条结果。

            :param bot: bot 用户名
            :param status: 状态
            :param reply: 回复文本
            :param alert: 弹窗文本
            :return dict: 结果字典
            """

            return {
                "account": "acc1",
                "account_label": "账号1(acc1)",
                "bot": bot,
                "ok": True,
                "reply": reply,
                "alert": alert,
                "error": "",
                "time": "2026-10-06 09:00:03",
                "status": status,
            }

        results = [
            item("@bb_emby_bot", STATUS_SUCCESS, "🎉 签到成功 | 10 子弹 💴 当前持有"),
            item("@HG_Emby_bot", STATUS_REPEATED, "🍉 你好鸭 请选择功能", "您今天已经签到过了"),
            item("@HDHaven_Bot", STATUS_REPEATED, "✅ 今日已签到，明天再来。"),
            item("@okemby_bot", STATUS_REPEATED, "🍉 你好鸭 请选择功能"),
            item("@other_bot", STATUS_UNCONFIRMED, "我不知道你在说什么"),
        ]
        text = build_notify_text(results, "手动", NOTIFY_MODE_SUCCESS)
        self.assertIsNotNone(text)
        self.assertIn("签到成功｜🎉 签到成功", text)
        self.assertIn("今日已签到（bot 弹窗：您今天已经签到过了）", text)
        self.assertIn("今日已签到｜✅ 今日已签到，明天再来。", text)
        self.assertIn("今日已签到（按钮式：已点击，bot 未返回签到结果）", text)
        self.assertIn("未确认（未在回复里看到签到结果）", text)

    def test_success_line_keeps_reply_snippet(self) -> None:
        """签到成功那行会带上 bot 回复摘要。"""
        results = self._results(1, 0)
        results[0]["status"] = STATUS_SUCCESS
        text = build_notify_text(results, "手动", NOTIFY_MODE_SUCCESS)
        self.assertIsNotNone(text)
        self.assertIn("签到成功｜🎉 签到成功", text)


class TestRunAccountAddsLabel(unittest.TestCase):
    """run_account 给每条结果补上账号显示名（供通知正文使用）。"""

    def setUp(self) -> None:
        """替换 sleep，避免命令式签到真的等待 15 秒。"""
        patcher = mock.patch.object(signin_mod, "asyncio", _FakeAsyncio)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_results_carry_account_label(self) -> None:
        """结果里带 account_label，且没有账号级错误。"""
        client = _FakeClient(batches=[[_FakeMessage("✅ 签到成功！获得 5 积分")]])
        account = AccountConfig(key="acc1", label="账号1", phone="+8613800138000")
        target = BotTarget(
            account_key="acc1",
            bot_username="@HDHaven_Bot",
            sign_type=SIGN_TYPE_COMMAND,
            action_text="/checkin",
        )
        with mock.patch.object(signin_mod, "build_client", return_value=client):
            results, error = asyncio.run(
                run_account(account, [target], Path("/tmp"), None)
            )
        self.assertEqual(error, "")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["account_label"], "账号1(acc1)")


class TestSigninStaleGuard(unittest.TestCase):
    """时间归属：只有「本次发送之后」的消息才算数（2026-10-07 假成功修复）。"""

    # 旧消息与本次发送时刻
    OLD = datetime(2026, 10, 7, 9, 0, tzinfo=timezone(timedelta(hours=8)))
    SENT = datetime(2026, 10, 7, 15, 0, tzinfo=timezone(timedelta(hours=8)))
    FRESH = datetime(2026, 10, 7, 15, 0, 5, tzinfo=timezone(timedelta(hours=8)))
    LATER = datetime(2026, 10, 7, 15, 0, 20, tzinfo=timezone(timedelta(hours=8)))

    def setUp(self) -> None:
        """替换 sleep，避免测试真的等待。"""
        patcher = mock.patch.object(signin_mod, "asyncio", _FakeAsyncio)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _target(self, sign_type: str = SIGN_TYPE_BUTTON) -> BotTarget:
        """
        构造一个签到目标。

        :param sign_type: 签到方式
        :return BotTarget: 目标
        """
        return BotTarget(
            account_key="acc1",
            bot_username="@okemby_bot",
            sign_type=sign_type,
            action_text="签到" if sign_type == SIGN_TYPE_BUTTON else "/checkin",
        )

    def test_stale_menu_not_clicked(self) -> None:
        """bot 离线：只剩上次的旧菜单 → 本次无新消息 → 判失败。"""
        client = _FakeClient(batches=[], sent_date=self.SENT)
        client._batches = [
            [
                _FakeMessage(
                    "请选择功能",
                    buttons=client.make_buttons(["🎯 签到"]),
                    date=self.OLD,
                )
            ]
        ]
        result = asyncio.run(signin_one(client, self._target()))
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], STATUS_FAILED)
        self.assertIn("没有新消息", result["error"])

    def test_stale_reply_not_success(self) -> None:
        """命令式：旧消息里的「签到成功」不能顶本次结果。"""
        client = _FakeClient(batches=[], sent_date=self.SENT)
        client._batches = [
            [_FakeMessage("🎉 签到成功 | 10 子弹", date=self.OLD)]
        ]
        result = asyncio.run(signin_one(client, self._target(SIGN_TYPE_COMMAND)))
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], STATUS_FAILED)
        self.assertIn("没等到 bot 回复", result["error"])

    def test_click_without_any_return_fails(self) -> None:
        """点到本次菜单按钮，但点击后 bot 零返回 → 失败（不再假成功）。"""
        client = _FakeClient(batches=[], sent_date=self.SENT)
        client._batches = [
            [
                _FakeMessage(
                    "请选择功能",
                    buttons=client.make_buttons(["🎯 签到"]),
                    date=self.FRESH,
                )
            ],
            [],  # 点击后没有任何新消息
        ]
        result = asyncio.run(signin_one(client, self._target()))
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], STATUS_FAILED)
        self.assertIn("无任何返回", result["error"])

    def test_menu_only_with_prior_success_repeated(self) -> None:
        """点击后 bot 又发了一条菜单 + 今天此前已成功过 → 今日已签到。"""
        client = _FakeClient(batches=[], sent_date=self.SENT)
        client._batches = [
            [
                _FakeMessage(
                    "请选择功能",
                    buttons=client.make_buttons(["🎯 签到"]),
                    date=self.FRESH,
                )
            ],
            [_FakeMessage("请选择功能", date=self.LATER)],
        ]
        result = asyncio.run(
            signin_one(client, self._target(), already_signed_today=True)
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], STATUS_REPEATED)

    def test_menu_only_without_prior_success_fails(self) -> None:
        """点击后 bot 只重发菜单、今天此前也没成功过 → 判失败（不再假成功）。"""
        client = _FakeClient(batches=[], sent_date=self.SENT)
        client._batches = [
            [
                _FakeMessage(
                    "请选择功能",
                    buttons=client.make_buttons(["🎯 签到"]),
                    date=self.FRESH,
                )
            ],
            [_FakeMessage("请选择功能", date=self.LATER)],
        ]
        result = asyncio.run(signin_one(client, self._target()))
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], STATUS_FAILED)
        self.assertIn("只回了菜单", result["error"])

    def test_menu_reply_not_renewed_fails(self) -> None:
        """点击后 bot 没有新消息（读回的还是那条菜单）→ 判失败，不算已签到。"""
        client = _FakeClient(batches=[], sent_date=self.SENT)
        menu = _FakeMessage(
            "请选择功能",
            buttons=client.make_buttons(["🎯 签到"]),
            date=self.FRESH,
        )
        client._batches = [[menu], [menu]]
        result = asyncio.run(signin_one(client, self._target()))
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], STATUS_FAILED)
        self.assertIn("无任何返回", result["error"])

    def test_fresh_alert_repeated(self) -> None:
        """弹窗说「已签到」也计入判据（修复 signin_one 漏传 alert）。"""
        client = _FakeClient(batches=[], sent_date=self.SENT)
        client._batches = [
            [
                _FakeMessage(
                    "请选择功能",
                    buttons=client.make_buttons_with_alert(
                        ["🎯 签到"], "您今天已经签到过了"
                    ),
                    date=self.FRESH,
                )
            ],
        ]
        result = asyncio.run(signin_one(client, self._target()))
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], STATUS_REPEATED)
        self.assertIn("签到", result["alert"])

    def test_fresh_success_reply(self) -> None:
        """本次回复命中「签到成功」→ 成功。"""
        client = _FakeClient(batches=[], sent_date=self.SENT)
        client._batches = [
            [
                _FakeMessage(
                    "请选择功能",
                    buttons=client.make_buttons(["🎯 签到"]),
                    date=self.FRESH,
                )
            ],
            [_FakeMessage("🎉 签到成功 | 12 子弹", date=self.LATER)],
        ]
        result = asyncio.run(signin_one(client, self._target()))
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], STATUS_SUCCESS)


if __name__ == "__main__":
    unittest.main()
