# -*- coding: utf-8 -*-
"""
TgOfflineBot 单元测试。

覆盖：链接解析、白名单解析、路径规范化、命令面为空、生命周期启动条件、
非白名单过滤、目录与白名单指令、离线接口提交载荷与失败分支、
以及「不发送任何 MoviePilot 通知」这一边界。
"""

import asyncio
import base64
import threading
import time
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

import tgofflinebot
from tgofflinebot import (
    AiRecognition,
    TgOfflineBot,
    parse_offline_links,
    parse_user_ids,
)
from app.db.oper.systemconfig import SystemConfigOper
from app.schemas.event import TransferInterceptEventData


def make_plugin(**overrides: Any) -> TgOfflineBot:
    """
    构造一个不启动长轮询的插件实例。

    :param overrides: 需要覆盖的实例属性
    :return: 插件实例
    """
    plugin = TgOfflineBot()
    plugin._enabled = True
    plugin._bot_token = "123:test"
    plugin._allowed_users = {"123456789"}
    plugin._target_path = ""
    plugin._api_token = "test-token"
    plugin._host_port = 3001
    # 实例级运行时容器，避免类属性在测试之间互相污染
    plugin._stop_event = threading.Event()
    plugin._submit_lock = threading.Lock()
    plugin._choice_lock = threading.RLock()
    plugin._pending_choices = {}
    plugin._pending_groups = {}
    plugin._skip_precheck_until = {}
    plugin._identifier_rules = {}
    plugin._failed_recent = {}
    plugin._notify_buffer = {}
    plugin._notify_timers = {}
    plugin._offline_delete_notified = {}
    plugin._ai_cache = {}
    plugin._exists_cache = {}
    plugin._residue_checked = {}
    plugin._residue_timers = {}
    plugin._residue_delay = 300
    plugin._misc_buffer = {}
    plugin._misc_timers = {}
    plugin._cleanup_empty_dirs = True
    plugin._notified_histories = set()
    # 默认把测试常用的路径/任务名标记为「本插件提交」，隔离逻辑另有专门用例
    owned_until = time.time() + 3600
    plugin._owned_names = {
        key: owned_until
        for key in (
            "待整理",
            "stuck",
            "running",
            "recent",
            "done",
            "half",
            "downloading",
        )
    }
    # 默认视为刚提交过任务，巡检会在有效期内工作
    plugin._offline_active_until = time.time() + 3600
    # 插件基类的整理链：库存在检查默认返回「不存在」
    plugin.chain = SimpleNamespace(media_exists=lambda mediainfo: None)
    for key, value in overrides.items():
        setattr(plugin, key, value)
    return plugin


def capture_replies(plugin: TgOfflineBot) -> List[str]:
    """
    把实例的 _reply 替换为记录器。

    :param plugin: 插件实例
    :return: 用于接收回复文本的列表
    """
    replies: List[str] = []

    def fake_reply(chat_id: Any, text: str, message_id: Any = None) -> None:
        """记录回复正文。"""
        replies.append(text)

    plugin._reply = fake_reply  # type: ignore[method-assign]
    return replies


def make_update(
    text: str,
    user_id: int = 123456789,
    chat_type: str = "private",
    username: str = "",
) -> Dict[str, Any]:
    """
    构造一条 Telegram 私聊消息更新。

    :param text: 消息正文
    :param user_id: 发送者用户 ID
    :param chat_type: 会话类型
    :param username: 发送者用户名
    :return: 更新字典
    """
    sender: Dict[str, Any] = {"id": user_id}
    if username:
        sender["username"] = username
    return {
        "update_id": 1001,
        "message": {
            "message_id": 7,
            "from": sender,
            "chat": {"id": user_id, "type": chat_type},
            "text": text,
        },
    }


# ============================ 链接解析 ============================


def test_parse_magnet_only() -> None:
    """单条磁力链接可被识别。"""
    text = "magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567&dn=Demo"
    assert parse_offline_links(text) == [text]


def test_parse_ed2k_and_torrent() -> None:
    """ed2k 与种子直链都能识别。"""
    ed2k = "ed2k://|file|demo.mkv|734003200|0123456789ABCDEF0123456789ABCDEF|/"
    torrent = "https://example.com/a.torrent"
    links = parse_offline_links(f"下载这两个\n{ed2k}\n{torrent}")
    assert links == [ed2k, torrent]


def test_parse_deduplicates_and_keeps_order() -> None:
    """重复链接去重且保持原文顺序。"""
    magnet = "magnet:?xt=urn:btih:AAAABBBBCCCCDDDDEEEEFFFF0000111122223333"
    links = parse_offline_links(f"{magnet} {magnet}")
    assert links == [magnet]


def test_parse_returns_empty_without_links() -> None:
    """没有链接时返回空列表。"""
    assert parse_offline_links("这是一段普通文字") == []
    assert parse_offline_links("") == []


# ============================ 白名单与路径 ============================


def test_parse_user_ids_mixed_separators() -> None:
    """白名单支持中英文逗号、分号、空格与换行。"""
    assert parse_user_ids("123456789, 123\n456；789") == {"123456789", "123", "456", "789"}


def test_parse_user_ids_drops_non_numeric() -> None:
    """非法项被丢弃，@用户名保留。"""
    assert parse_user_ids("123456789, abc, @name, @a") == {"123456789", "@name"}


def test_normalize_path() -> None:
    """目标目录补前导斜杠、去重复斜杠与尾部斜杠。"""
    assert TgOfflineBot._normalize_path("video/电影") == "/video/电影"
    assert TgOfflineBot._normalize_path("//video//电影//") == "/video/电影"
    assert TgOfflineBot._normalize_path("/") == ""
    assert TgOfflineBot._normalize_path("") == ""


# ============================ 命令面与生命周期 ============================


def test_no_moviepilot_commands() -> None:
    """插件不向 MoviePilot 注册任何远程命令。"""
    assert TgOfflineBot.get_command() == []


def test_disabled_plugin_does_not_start_polling() -> None:
    """未启用时不启动轮询线程。"""
    plugin = TgOfflineBot()
    plugin.init_plugin({"enabled": False, "bot_token": "123:test"})
    assert plugin.get_state() is False
    assert plugin._thread is None


def test_missing_token_reports_error() -> None:
    """启用但缺 token 时不启动并记录原因。"""
    plugin = TgOfflineBot()
    plugin.init_plugin({"enabled": True, "bot_token": ""})
    assert plugin._thread is None
    assert "token" in plugin._last_error


def test_no_builtin_default_whitelist() -> None:
    """仓库里不内置任何真实白名单：未配置时保持为空，由用户自行填写。"""
    plugin = TgOfflineBot()
    plugin.init_plugin({"enabled": False, "bot_token": "123:test", "allowed_users": ""})
    assert plugin._allowed_users == set()


def test_enabled_plugin_starts_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """启用且配置完整时启动轮询线程。"""
    started: List[int] = []

    def fake_poll_loop(self: TgOfflineBot, generation: int) -> None:
        """记录线程启动，不发起网络请求。"""
        started.append(generation)

    monkeypatch.setattr(TgOfflineBot, "_poll_loop", fake_poll_loop)
    plugin = TgOfflineBot()
    plugin.init_plugin({"enabled": True, "bot_token": "123:test", "allowed_users": "123456789"})
    plugin._thread.join(timeout=5)  # type: ignore[union-attr]
    assert started
    assert plugin.get_state() is True


# ============================ 消息处理 ============================


def test_ignores_non_whitelisted_user() -> None:
    """非白名单用户的消息被静默丢弃，不产生回复。"""
    plugin = make_plugin()
    replies = capture_replies(plugin)
    plugin._handle_update(make_update("magnet:?xt=urn:btih:DEADBEEF", user_id=111))
    assert replies == []
    assert plugin._history[-1]["kind"] == "已忽略"


def test_ignores_unauthorized_group_chat() -> None:
    """群组未授权且发送者不在白名单时，消息被忽略。"""
    plugin = make_plugin()
    replies = capture_replies(plugin)
    plugin._handle_update(
        make_update("/status", user_id=111, chat_type="group")
    )
    assert replies == []
    assert plugin._history[-1]["kind"] == "已忽略"


def test_dir_command_updates_target(monkeypatch: pytest.MonkeyPatch) -> None:
    """`/dir <路径>` 改目标目录并写回配置。"""
    plugin = make_plugin()
    replies = capture_replies(plugin)
    monkeypatch.setattr(
        plugin,
        "_persist_config_async",
        lambda **changes: plugin.update_config({**plugin.get_config(), **changes}),
    )
    plugin._handle_update(make_update("/dir video/电影"))
    assert plugin._target_path == "/video/电影"
    assert plugin.get_config()["target_path"] == "/video/电影"
    assert "已改为" in replies[-1]


def test_dir_command_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    """`/dir default` 恢复默认链路。"""
    plugin = make_plugin(_target_path="/video/电影")
    replies = capture_replies(plugin)
    monkeypatch.setattr(
        plugin,
        "_persist_config_async",
        lambda **changes: plugin.update_config({**plugin.get_config(), **changes}),
    )
    plugin._handle_update(make_update("/dir default"))
    assert plugin._target_path == ""
    assert "恢复默认" in replies[-1]


def test_allow_and_deny(monkeypatch: pytest.MonkeyPatch) -> None:
    """`/allow` 与 `/deny` 增删白名单。"""
    plugin = make_plugin()
    replies = capture_replies(plugin)
    monkeypatch.setattr(
        plugin,
        "_persist_config_async",
        lambda **changes: plugin.update_config({**plugin.get_config(), **changes}),
    )
    plugin._handle_update(make_update("/allow 123456"))
    assert "123456" in plugin._allowed_users
    plugin._handle_update(make_update("/deny 123456"))
    assert "123456" not in plugin._allowed_users
    assert any("已加入白名单" in text for text in replies)


def test_deny_cannot_empty_whitelist(monkeypatch: pytest.MonkeyPatch) -> None:
    """不允许通过 /deny 把白名单清空。"""
    plugin = make_plugin()
    replies = capture_replies(plugin)
    monkeypatch.setattr(plugin, "_persist_config_async", lambda **changes: None)
    plugin._handle_update(make_update("/deny 123456789"))
    assert plugin._allowed_users == {"123456789"}
    assert any("拒绝清空" in text for text in replies)


def test_plain_text_without_link_gets_hint() -> None:
    """普通文本没有链接时给出提示。"""
    plugin = make_plugin()
    replies = capture_replies(plugin)
    plugin._handle_update(make_update("随便说句话"))
    assert replies and "没有识别到" in replies[0]


def test_command_suffix_with_bot_username() -> None:
    """兼容 /status@bot 写法。"""
    plugin = make_plugin()
    replies = capture_replies(plugin)
    plugin._handle_update(make_update("/status@my_bot"))
    assert replies and "运行状态" in replies[0]


# ============================ 离线提交 ============================


class _FakeResponse:
    """requests 响应替身。"""

    def __init__(self, status_code: int, payload: Dict[str, Any]) -> None:
        """保存状态码与响应体。"""
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Dict[str, Any]:
        """返回响应体。"""
        return self._payload


def test_submit_uses_default_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """目标目录留空时 path 传 None，走插件默认链路。"""
    captured: Dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> _FakeResponse:
        """记录提交请求。"""
        captured["url"] = url
        captured.update(kwargs)
        return _FakeResponse(200, {"code": 0, "msg": "1 个新任务已成功添加，正在后台处理。"})

    monkeypatch.setattr(tgofflinebot.requests, "post", fake_post)
    plugin = make_plugin()
    ok, detail = plugin._submit_links(["magnet:?xt=urn:btih:AAAA"])
    assert ok is True
    assert captured["json"] == {"links": ["magnet:?xt=urn:btih:AAAA"], "path": None}
    assert captured["headers"]["X-API-KEY"] == "test-token"
    assert "add_offline_task" in captured["url"]
    assert "已成功添加" in detail


def test_submit_uses_explicit_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """填写目标目录时把该路径原样传给插件。"""
    captured: Dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> _FakeResponse:
        """记录提交请求。"""
        captured.update(kwargs)
        return _FakeResponse(200, {"code": 0, "msg": "ok"})

    monkeypatch.setattr(tgofflinebot.requests, "post", fake_post)
    plugin = make_plugin(_target_path="/video/电影")
    plugin._submit_links(["ed2k://|file|a|1|0123456789ABCDEF0123456789ABCDEF|/"])
    assert captured["json"]["path"] == "/video/电影"


def test_submit_reports_missing_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    """P115StrmHelper 路由不存在时给出可操作的提示。"""
    monkeypatch.setattr(
        tgofflinebot.requests, "post", lambda url, **kwargs: _FakeResponse(404, {})
    )
    ok, detail = make_plugin()._submit_links(["magnet:?xt=urn:btih:AAAA"])
    assert ok is False
    assert "P115StrmHelper" in detail


def test_submit_reports_auth_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """鉴权失败时提示检查 Token。"""
    monkeypatch.setattr(
        tgofflinebot.requests, "post", lambda url, **kwargs: _FakeResponse(403, {})
    )
    ok, detail = make_plugin()._submit_links(["magnet:?xt=urn:btih:AAAA"])
    assert ok is False
    assert "鉴权失败" in detail


def test_submit_reports_plugin_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """插件返回非 0 code 时按失败回执。"""
    monkeypatch.setattr(
        tgofflinebot.requests,
        "post",
        lambda url, **kwargs: _FakeResponse(200, {"code": -1, "msg": "添加失败"}),
    )
    ok, detail = make_plugin()._submit_links(["magnet:?xt=urn:btih:AAAA"])
    assert ok is False
    assert detail == "添加失败"


def test_submit_without_token_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺 Token 时不发请求，直接给出原因。"""
    called: List[str] = []

    def fake_post(url: str, **kwargs: Any) -> _FakeResponse:
        """记录调用（不应发生）。"""
        called.append(url)
        return _FakeResponse(200, {"code": 0})

    monkeypatch.setattr(tgofflinebot.requests, "post", fake_post)
    monkeypatch.setattr(TgOfflineBot, "_host_setting", lambda self, name, default=None: "")
    plugin = make_plugin(_api_token="")
    ok, detail = plugin._submit_links(["magnet:?xt=urn:btih:AAAA"])
    assert ok is False
    assert "API Token" in detail
    assert called == []


def test_full_flow_replies_and_records(monkeypatch: pytest.MonkeyPatch) -> None:
    """完整链路：白名单用户发磁力 → 提交 → 回执 → 记录。"""
    monkeypatch.setattr(
        tgofflinebot.requests,
        "post",
        lambda url, **kwargs: _FakeResponse(200, {"code": 0, "msg": "2 个新任务已成功添加"}),
    )
    plugin = make_plugin()
    replies = capture_replies(plugin)
    plugin._handle_update(
        make_update(
            "magnet:?xt=urn:btih:AAAA\ned2k://|file|a|1|0123456789ABCDEF0123456789ABCDEF|/"
        )
    )
    assert replies and "已提交 2 条" in replies[0]
    assert plugin._history[-1]["kind"] == "成功"


def test_plugin_sends_no_moviepilot_notification(monkeypatch: pytest.MonkeyPatch) -> None:
    """插件运行期间不调用 MoviePilot 通知（分批路径同样不发）。"""
    """插件运行期间不调用 MoviePilot 通知。"""
    monkeypatch.setattr(
        tgofflinebot.requests,
        "post",
        lambda url, **kwargs: _FakeResponse(200, {"code": 0, "msg": "ok"}),
    )
    plugin = make_plugin()
    capture_replies(plugin)
    plugin._handle_update(make_update("magnet:?xt=urn:btih:AAAA"))
    assert plugin.sent_messages == []


# ============================ 分批提交 ============================


def make_links(count: int) -> List[str]:
    """
    生成指定数量的假磁力链接。

    :param count: 数量
    :return: 链接列表
    """
    return [f"magnet:?xt=urn:btih:{index:040X}" for index in range(count)]


def test_clamp_int() -> None:
    """分批参数被夹取到允许区间。"""
    assert TgOfflineBot._clamp_int(None, 20, 1, 100) == 20
    assert TgOfflineBot._clamp_int("0", 20, 1, 100) == 1
    assert TgOfflineBot._clamp_int("500", 20, 1, 100) == 100
    assert TgOfflineBot._clamp_int("abc", 20, 1, 100) == 20


def test_small_batch_submits_synchronously(monkeypatch: pytest.MonkeyPatch) -> None:
    """链接数不超过每批条数时同步提交，只调用一次。"""
    calls: List[List[str]] = []
    plugin = make_plugin(_batch_size=20)
    replies = capture_replies(plugin)

    def submit(links: List[str]):
        """记录提交并返回成功。"""
        calls.append(list(links))
        return True, "ok"

    monkeypatch.setattr(plugin, "_submit_links", submit)
    thread = plugin._submit_and_reply(make_links(20), 1, 2)
    assert thread is None
    assert len(calls) == 1 and len(calls[0]) == 20
    assert replies and "已提交 20 条" in replies[0]


def test_large_batch_splits_and_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    """45 条按 20 条分批 → 3 批，批间按配置等待。"""
    calls: List[int] = []
    waits: List[float] = []
    plugin = make_plugin(_batch_size=20, _batch_interval=30)

    def submit(links: List[str]):
        """记录每批条数。"""
        calls.append(len(links))
        return True, "ok"

    monkeypatch.setattr(plugin, "_submit_links", submit)
    monkeypatch.setattr(plugin, "_wait_between_batches", lambda seconds: bool(waits.append(seconds)))
    monkeypatch.setattr(plugin, "_send_text", lambda chat_id, text, message_id=None: 555)
    monkeypatch.setattr(plugin, "_edit_text", lambda chat_id, message_id, text: None)

    thread = plugin._submit_and_reply(make_links(45), 1, 2)
    assert thread is not None
    thread.join(timeout=10)
    assert calls == [20, 20, 5]
    assert waits == [30, 30]
    assert plugin._history[-1]["kind"] == "成功"


def test_failed_batch_doubles_interval_and_continues(monkeypatch: pytest.MonkeyPatch) -> None:
    """某批失败后间隔翻倍，后续批次继续提交。"""
    calls: List[int] = []
    waits: List[float] = []

    def submit(links: List[str]):
        """前两批失败，之后成功。"""
        calls.append(len(links))
        return (len(calls) > 2), ("ok" if len(calls) > 2 else "boom")

    plugin = make_plugin(_batch_size=10, _batch_interval=30)
    monkeypatch.setattr(plugin, "_submit_links", submit)
    monkeypatch.setattr(plugin, "_wait_between_batches", lambda seconds: bool(waits.append(seconds)))
    monkeypatch.setattr(plugin, "_send_text", lambda chat_id, text, message_id=None: 555)
    monkeypatch.setattr(plugin, "_edit_text", lambda chat_id, message_id, text: None)

    plugin._submit_batches([make_links(10), make_links(10), make_links(10)], 1, 2)
    assert calls == [10, 10, 10]
    assert waits == [60, 60]
    assert plugin._history[-1]["kind"] == "部分完成"
    assert plugin._history[-1]["detail"] == "10/30 条"


def test_batch_stops_on_stop_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """停止信号到达后不再提交剩余批次。"""
    stop = threading.Event()
    calls: List[int] = []
    plugin = make_plugin(_batch_size=10, _batch_interval=0, _stop_event=stop)

    def submit(links: List[str]):
        """第一批提交后立刻触发停止。"""
        calls.append(len(links))
        stop.set()
        return True, "ok"

    monkeypatch.setattr(plugin, "_submit_links", submit)
    monkeypatch.setattr(plugin, "_wait_between_batches", lambda seconds: stop.is_set())
    monkeypatch.setattr(plugin, "_send_text", lambda chat_id, text, message_id=None: 555)
    monkeypatch.setattr(plugin, "_edit_text", lambda chat_id, message_id, text: None)

    plugin._submit_batches([make_links(10), make_links(10)], 1, 2)
    assert len(calls) == 1
    assert plugin._history[-1]["detail"] == "10/20 条"


def test_batch_submission_sends_no_notification(monkeypatch: pytest.MonkeyPatch) -> None:
    """分批路径同样不触发 MoviePilot 通知。"""
    plugin = make_plugin(_batch_size=10)
    monkeypatch.setattr(plugin, "_submit_links", lambda links: (True, "ok"))
    monkeypatch.setattr(plugin, "_wait_between_batches", lambda seconds: False)
    monkeypatch.setattr(plugin, "_send_text", lambda chat_id, text, message_id=None: 555)
    monkeypatch.setattr(plugin, "_edit_text", lambda chat_id, message_id, text: None)
    plugin._submit_batches([make_links(10), make_links(10)], 1, 2)
    assert plugin.sent_messages == []


# ============================ 群组支持 ============================


def test_parse_user_ids_accepts_group_ids() -> None:
    """白名单接受负数群组 ID。"""
    assert parse_user_ids("123456789,-1001234567890") == {"123456789", "-1001234567890"}


def make_group_update(
    text: str, chat_id: int = -1001234567890, user_id: int = 111
) -> Dict[str, Any]:
    """
    构造一条群组消息更新。

    :param text: 消息正文
    :param chat_id: 群组会话 ID
    :param user_id: 发送者用户 ID
    :return: 更新字典
    """
    return {
        "update_id": 2001,
        "message": {
            "message_id": 9,
            "from": {"id": user_id},
            "chat": {"id": chat_id, "type": "supergroup"},
            "text": text,
        },
    }


def test_group_whitelisted_member_can_submit(monkeypatch: pytest.MonkeyPatch) -> None:
    """群 ID 在白名单时，群成员可直接提交链接。"""
    submitted: List[int] = []
    plugin = make_plugin(_allowed_users={"123456789", "-1001234567890"})
    replies = capture_replies(plugin)

    def submit(links: List[str]):
        """记录提交并返回成功。"""
        submitted.append(len(links))
        return True, "ok"

    monkeypatch.setattr(plugin, "_submit_links", submit)
    plugin._handle_update(make_group_update("magnet:?xt=urn:btih:AAAA", user_id=111))
    assert submitted == [1]
    assert replies and "已提交 1 条" in replies[0]


def test_group_not_whitelisted_is_ignored() -> None:
    """群 ID 与发送者都不在白名单时忽略。"""
    plugin = make_plugin(_allowed_users={"123456789"})
    replies = capture_replies(plugin)
    plugin._handle_update(make_group_update("magnet:?xt=urn:btih:AAAA", user_id=111))
    assert replies == []
    assert plugin._history[-1]["kind"] == "已忽略"


def test_group_member_cannot_change_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    """群内非白名单成员不能修改目标目录。"""
    plugin = make_plugin(_allowed_users={"123456789", "-1001234567890"})
    replies = capture_replies(plugin)
    monkeypatch.setattr(plugin, "_persist_config_async", lambda **changes: None)
    plugin._handle_update(make_group_update("/dir /video/电影", user_id=111))
    assert plugin._target_path == ""
    assert any("白名单成员" in text for text in replies)


def test_group_admin_can_change_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    """群内白名单成员可以修改目标目录。"""
    plugin = make_plugin(_allowed_users={"123456789", "-1001234567890"})
    replies = capture_replies(plugin)
    monkeypatch.setattr(plugin, "_persist_config_async", lambda **changes: None)
    plugin._handle_update(make_group_update("/dir /video/电影", user_id=123456789))
    assert plugin._target_path == "/video/电影"
    assert any("已改为" in text for text in replies)


def test_channel_message_ignored() -> None:
    """频道消息不处理。"""
    plugin = make_plugin(_allowed_users={"-1001234567890"})
    replies = capture_replies(plugin)
    update = make_group_update("magnet:?xt=urn:btih:AAAA", chat_id=-1001234567890)
    update["message"]["chat"]["type"] = "channel"
    plugin._handle_update(update)
    assert replies == []


# ============================ 预整理：库检查与识别比对 ============================


def make_mediainfo(
    title: str = "示例电影",
    year: str = "2024",
    media_type: str = "电影",
    seasons: Optional[Dict[int, List[int]]] = None,
    media_id: str = "123",
) -> Any:
    """
    构造 MP 识别结果替身。

    :param title: 标题
    :param year: 年份
    :param media_type: 媒体类型
    :param seasons: 季集字典
    :param media_id: 媒体 ID
    :return: 替身对象
    """
    return SimpleNamespace(
        title=title,
        year=year,
        type=media_type,
        seasons=seasons or {},
        media_id=media_id,
        media_source="tmdb",
    )


def make_intercept_event(
    path: str = "/待整理/示例电影.2024.1080p.mkv",
    mediainfo: Any = None,
) -> Any:
    """
    构造整理拦截事件。

    :param path: 源文件路径
    :param mediainfo: MP 识别结果
    :return: 带 event_data 的事件替身
    """
    data = TransferInterceptEventData(
        fileitem=SimpleNamespace(path=path),
        mediainfo=mediainfo or make_mediainfo(),
    )
    return SimpleNamespace(event_data=data)


def test_normalize_title_ignores_punctuation() -> None:
    """标题归一化忽略空白、标点与大小写。"""
    plugin = make_plugin()
    assert plugin._normalize_title("The Matrix: Reloaded") == plugin._normalize_title(
        "the matrix reloaded"
    )


def test_same_media_matches() -> None:
    """类型、标题、年份一致时判为同一媒体。"""
    plugin = make_plugin()
    ai = AiRecognition(name="示例电影", year="2024", media_type="movie", confidence=0.9)
    assert plugin._same_media(make_mediainfo(), ai) is True


def test_same_media_detects_type_conflict() -> None:
    """电影与电视剧判为不一致。"""
    plugin = make_plugin()
    ai = AiRecognition(name="示例电影", year="2024", media_type="tv", confidence=0.9)
    assert plugin._same_media(make_mediainfo(), ai) is False


def test_same_media_detects_title_conflict() -> None:
    """标题不同判为不一致。"""
    plugin = make_plugin()
    ai = AiRecognition(name="另一部电影", year="2024", media_type="movie", confidence=0.9)
    assert plugin._same_media(make_mediainfo(), ai) is False


def test_same_media_detects_year_conflict() -> None:
    """年份冲突判为不一致。"""
    plugin = make_plugin()
    ai = AiRecognition(name="示例电影", year="2019", media_type="movie", confidence=0.9)
    assert plugin._same_media(make_mediainfo(), ai) is False


def test_precheck_scope_default() -> None:
    """未配置范围时默认只处理 /待整理。"""
    plugin = make_plugin()
    assert plugin._in_precheck_scope("/待整理/a.mkv") is True
    assert plugin._in_precheck_scope("/other/a.mkv") is False


def test_skip_once_expires() -> None:
    """跳过标记在有效期内生效，过期后自动失效。"""
    plugin = make_plugin()
    plugin._skip_once("/待整理/a.mkv", minutes=10)
    assert plugin._is_skipped("/待整理/a.mkv") is True
    plugin._skip_precheck_until["/待整理/a.mkv"] = time.time() - 1
    assert plugin._is_skipped("/待整理/a.mkv") is False


def test_library_exists_blocks_and_notifies(monkeypatch: pytest.MonkeyPatch) -> None:
    """库中已存在时取消整理并通知。"""
    plugin = make_plugin(
        _notify_chat_id="123", _precheck_enabled=True, _notify_window=0
    )
    notified: List[str] = []
    monkeypatch.setattr(plugin, "_send_text", lambda chat_id, text, message_id=None, keyboard=None: notified.append(text) or 1)
    monkeypatch.setattr(plugin.chain, "media_exists", lambda mediainfo: SimpleNamespace(server="Emby", seasons={}))
    event = make_intercept_event()
    plugin._on_transfer_intercept(event)
    assert event.event_data.cancel is True
    assert "媒体库已存在" in event.event_data.reason
    assert any("跳过整理" in text for text in notified)
    assert plugin._history[-1]["kind"] == "库中已存在"


def test_library_exists_allows_missing_episode(monkeypatch: pytest.MonkeyPatch) -> None:
    """剧集只缺集时放行，不拦截。"""
    plugin = make_plugin(_notify_chat_id="123")
    monkeypatch.setattr(plugin, "_send_text", lambda *args, **kwargs: 1)
    monkeypatch.setattr(
        plugin.chain,
        "media_exists",
        lambda mediainfo: SimpleNamespace(server="Emby", seasons={1: [2]}),
    )
    monkeypatch.setattr(plugin, "_ai_recognize", lambda path, mediainfo: None)
    event = make_intercept_event(
        path="/待整理/示例剧.S01E01.mkv",
        mediainfo=make_mediainfo("示例剧", "2024", "电视剧", {1: [1]}),
    )
    plugin._on_transfer_intercept(event)
    assert event.event_data.cancel is False


def test_precheck_matching_passes_without_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    """MP 与 AI 识别一致时直接放行。"""
    plugin = make_plugin()
    plugin._library_check_enabled = False
    monkeypatch.setattr(
        plugin,
        "_ai_recognize",
        lambda path, mediainfo: AiRecognition(
            name="示例电影", year="2024", media_type="movie", confidence=0.95
        ),
    )
    event = make_intercept_event()
    plugin._on_transfer_intercept(event)
    assert event.event_data.cancel is False
    assert plugin._history[-1]["kind"] == "识别一致"


def test_precheck_mismatch_sends_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    """识别不一致时取消整理并发送选择按钮。"""
    plugin = make_plugin(_notify_chat_id="123", _precheck_timeout=0)
    plugin._library_check_enabled = False
    sent: List[Dict[str, Any]] = []

    def fake_send(chat_id, text, message_id=None, keyboard=None):
        """记录消息与键盘。"""
        sent.append({"chat_id": chat_id, "text": text, "keyboard": keyboard})
        return 88

    monkeypatch.setattr(plugin, "_send_text", fake_send)
    monkeypatch.setattr(
        plugin,
        "_ai_recognize",
        lambda path, mediainfo: AiRecognition(
            name="另一部电影", year="2024", media_type="movie", confidence=0.9
        ),
    )
    event = make_intercept_event()
    plugin._on_transfer_intercept(event)
    assert event.event_data.cancel is True
    assert plugin._pending_choices
    keyboard = sent[-1]["keyboard"]["inline_keyboard"][0]
    assert keyboard[0]["text"] == "按 MP 识别整理"
    assert keyboard[1]["text"] == "按 AI 识别整理"


def test_apply_choice_ai_writes_identifier_and_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """选择 AI 时写入限定文件名的识别词并重新触发整理。"""
    plugin = make_plugin()
    plugin._skip_precheck_until = {}
    monkeypatch.setattr(plugin, "_trigger_manual_transfer", lambda path: (True, "ok"))
    monkeypatch.setattr(plugin, "_resolve_ai_media_id", lambda name, year, media_type: ("999", "tmdb"))
    SystemConfigOper._store["CustomIdentifiers"] = ["# 现有规则"]
    target = {
        "path": "/待整理/示例电影.2024.1080p.mkv",
        "ai": {
            "name": "示例电影",
            "year": "2024",
            "media_type": "movie",
            "season": 0,
            "episode": 0,
            "confidence": 0.9,
        },
    }
    text = plugin._apply_choice("/待整理/示例电影.2024.1080p.mkv", True, target)
    assert "已重新触发整理" in text
    assert plugin._is_skipped("/待整理/示例电影.2024.1080p.mkv") is True
    rules = SystemConfigOper._store["CustomIdentifiers"]
    assert any("tmdbid=999" in rule and "type=movie" in rule for rule in rules)
    assert plugin._identifier_rules


def test_identifier_rule_uses_enum_source_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """媒体源是枚举时取 value，生成合法的 tmdbid 字段。"""
    plugin = make_plugin()
    monkeypatch.setattr(
        plugin,
        "_resolve_ai_media_id",
        lambda name, year, media_type: ("12345", SimpleNamespace(value="tmdb")),
    )
    rule = plugin._make_identifier_rule(
        "/待整理/示例剧/示例剧.S01E01.mkv",
        {"name": "示例剧", "year": "2024", "media_type": "tv", "season": 1, "episode": 1},
    )
    assert rule is not None
    assert "tmdbid=12345" in rule
    assert "MediaSource" not in rule


def test_make_identifier_rule_falls_back_to_title(monkeypatch: pytest.MonkeyPatch) -> None:
    """拿不到媒体 ID 时退化为标题替换规则。"""
    plugin = make_plugin()
    monkeypatch.setattr(plugin, "_resolve_ai_media_id", lambda name, year, media_type: None)
    rule = plugin._make_identifier_rule(
        "/待整理/Old.Name.2024.mkv",
        {"name": "新名字", "year": "2024", "media_type": "movie"},
    )
    assert rule is not None
    assert "=> 新名字 2024" in rule


def test_strm_target_maps_pan_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """网盘目标路径能映射到本地 STRM 路径。"""
    plugin = make_plugin()
    plugin._strm_local_root = "/mnt/strm"
    plugin._strm_pan_root = "/video"
    target = plugin._strm_target("/video/电影/外语电影/示例电影 (2024)/示例电影.mkv")
    assert target is not None
    assert str(target) == "/mnt/strm/电影/外语电影/示例电影 (2024)/示例电影.strm"


def test_mask_secrets_removes_bot_token() -> None:
    """异常文本里的 bot token 会被脱敏，避免写进日志。"""
    plugin = make_plugin()
    masked = plugin._mask_secrets(
        "HTTPSConnectionPool: /bot123456:ABC-DEF_ghi/getUpdates?timeout=10"
    )
    assert "ABC-DEF_ghi" not in masked
    assert "/bot<redacted>/getUpdates" in masked


# ============================ 通知聚合与降噪 ============================


def test_complete_notifications_are_aggregated(monkeypatch: pytest.MonkeyPatch) -> None:
    """同一部剧的多集在窗口内合并成一条通知。"""
    plugin = make_plugin(
        _notify_window=60, _notify_only_from_bot=False, _notify_strm=False,
        _notify_chat_id="123",
    )
    sent: List[str] = []
    monkeypatch.setattr(
        plugin,
        "_send_text",
        lambda chat_id, text, message_id=None, keyboard=None: sent.append(text) or 1,
    )
    plugin._buffer_complete(
        "示例剧", "2024", 1, 1, "/video/电视剧/示例剧/示例剧.S01E01.mkv"
    )
    plugin._buffer_complete(
        "示例剧", "2024", 1, 2, "/video/电视剧/示例剧/示例剧.S01E02.mkv"
    )
    assert sent == []
    plugin._flush_notify(next(iter(plugin._notify_buffer)))
    assert len(sent) == 1
    assert "E01,E02" in sent[0]
    assert "第1季" in sent[0]
    # 入库通知不再带具体路径
    assert "/video" not in sent[0]
    assert "/mnt" not in sent[0]


def test_notify_keys_isolate_season_show_and_year() -> None:
    """不同剧、不同季、同名不同年份各自独立，不会合并进同一条通知。"""
    plugin = make_plugin(
        _notify_window=300,
        _notify_only_from_bot=False,
        _notify_strm=False,
        _notify_chat_id="123",
    )
    plugin._buffer_complete(
        "示例剧", "2024", 1, 1, "/video/电视剧/示例剧/示例剧.S01E01.mkv", "电视剧"
    )
    plugin._buffer_complete(
        "示例剧", "2024", 2, 1, "/video/电视剧/示例剧/示例剧.S02E01.mkv", "电视剧"
    )
    plugin._buffer_complete(
        "另一部剧", "2024", 1, 1, "/video/电视剧/另一部剧/另一部剧.S01E01.mkv", "电视剧"
    )
    plugin._buffer_complete(
        "同名电影", "2024", 0, 0, "/video/电影/同名电影 (2024)/同名电影.mkv", "电影"
    )
    plugin._buffer_complete(
        "同名电影", "1999", 0, 0, "/video/电影/同名电影 (1999)/同名电影.mkv", "电影"
    )
    keys = set(plugin._notify_buffer)
    assert len(keys) == 5
    assert "示例剧|2024|1|电视剧" in keys
    assert "示例剧|2024|2|电视剧" in keys
    assert "另一部剧|2024|1|电视剧" in keys
    assert "同名电影|2024|0|电影" in keys
    assert "同名电影|1999|0|电影" in keys
    # 取消定时器，避免窗口到点后真的推送测试消息
    for timer in list(plugin._notify_timers.values()):
        timer.cancel()
    plugin._notify_timers.clear()
    plugin._notify_buffer.clear()


def test_strm_target_requires_mapping() -> None:
    """未配置本地/网盘根目录时不猜路径，避免误报 STRM 缺失。"""
    plugin = make_plugin()
    plugin._strm_local_root = ""
    plugin._strm_pan_root = ""
    assert plugin._strm_target("/video/电影/示例电影.mkv") is None


def test_complete_ignores_non_bot_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """非 /待整理 来源的整理不发通知。"""
    plugin = make_plugin(_precheck_scope="/待整理", _notify_only_from_bot=True)
    sent: List[str] = []
    monkeypatch.setattr(
        plugin, "_send_text", lambda *args, **kwargs: sent.append("x") or 1
    )
    event = SimpleNamespace(
        event_data=SimpleNamespace(
            mediainfo=SimpleNamespace(title="示例剧", year="2024", seasons={1: [1]}),
            transferinfo=SimpleNamespace(
                target_item=SimpleNamespace(path="/mnt/media/示例剧.mkv"),
                target_diritem=None,
            ),
            fileitem=SimpleNamespace(path="/mnt/media/下载/示例剧.mkv"),
            meta=None,
        )
    )
    plugin._on_transfer_complete(event)
    assert sent == []
    assert plugin._notify_buffer == {}


def test_failed_notification_is_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    """整理失败不再推送通知（广告文件也会触发，属于噪音）。"""
    plugin = make_plugin(_notify_only_from_bot=False, _notify_chat_id="123")
    sent: List[str] = []
    monkeypatch.setattr(
        plugin,
        "_send_text",
        lambda chat_id, text, message_id=None, keyboard=None: sent.append(text) or 1,
    )
    monkeypatch.setattr(plugin, "_purge_stale_records", lambda path: None)
    event = SimpleNamespace(
        event_data=SimpleNamespace(
            mediainfo=SimpleNamespace(title="示例剧"),
            transferinfo=SimpleNamespace(message="boom", failure_stage="move"),
            fileitem=SimpleNamespace(path="/待整理/示例剧.S01E01.mkv"),
        )
    )
    plugin._on_transfer_failed(event)
    plugin._on_transfer_failed(event)
    assert sent == []


def test_mismatch_groups_by_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    """同一目录的多个不一致文件只发一条消息，后续更新同一条。"""
    plugin = make_plugin(
        _notify_chat_id="123",
        _precheck_timeout=0,
        _library_check_enabled=False,
    )
    sent: List[str] = []
    edits: List[str] = []
    monkeypatch.setattr(
        plugin,
        "_send_text",
        lambda chat_id, text, message_id=None, keyboard=None: sent.append(text) or 1,
    )
    monkeypatch.setattr(
        plugin, "_edit_text", lambda chat_id, message_id, text: edits.append(text)
    )
    monkeypatch.setattr(
        plugin,
        "_ai_recognize",
        lambda path, mediainfo: AiRecognition(
            name="另一部剧", year="2024", media_type="tv", confidence=0.9
        ),
    )
    for episode in (1, 2):
        plugin._on_transfer_intercept(
            make_intercept_event(
                path=f"/待整理/示例剧/示例剧.S01E0{episode}.mkv",
                mediainfo=make_mediainfo("示例剧", "2024", "电视剧", {1: [episode]}),
            )
        )
    assert len(sent) == 1
    assert len(edits) == 1
    assert len(plugin._pending_groups) == 1
    assert "2 个文件" in edits[0]


def test_transfer_failed_does_not_notify(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """整理失败只写日志，不再推送通知。"""
    plugin = make_plugin(_notify_chat_id="123", _notify_only_from_bot=False)
    sent: List[str] = []
    monkeypatch.setattr(
        plugin,
        "_send_text",
        lambda chat_id, text, message_id=None, keyboard=None: sent.append(text) or 1,
    )
    monkeypatch.setattr(plugin, "_purge_stale_records", lambda path: None)
    event = SimpleNamespace(
        event_data=SimpleNamespace(
            mediainfo=SimpleNamespace(title="示例剧"),
            transferinfo=SimpleNamespace(message="boom", failure_stage="move"),
            fileitem=SimpleNamespace(path="/待整理/示例剧/x.mkv"),
        )
    )
    plugin._on_transfer_failed(event)
    assert sent == []


def test_owned_histories_ignore_before_last_submit() -> None:
    """只补发最近一次提交之后的记录，重启后不会把历史记录重发。"""
    from datetime import datetime as _dt
    from datetime import timedelta

    plugin = make_plugin()
    plugin._record_owned_links(
        ["magnet:?xt=urn:btih:AAAA&dn=Some.Show.S01.1080p.WEB-DL"]
    )
    now = _dt.now()
    plugin._last_submit_ts = now.timestamp()
    old_text = (now - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
    new_text = now.strftime("%Y-%m-%d %H:%M:%S")
    records = [
        SimpleNamespace(
            src="/待整理/Some.Show.S01.1080p.WEB-DL/old.mkv",
            dest="/video/old.mkv",
            status=True,
            date=old_text,
        ),
        SimpleNamespace(
            src="/待整理/Some.Show.S01.1080p.WEB-DL/new.mkv",
            dest="/video/new.mkv",
            status=True,
            date=new_text,
        ),
    ]
    matched = plugin._owned_histories_from(records, within=3600)
    assert len(matched) == 1
    assert matched[0].dest == "/video/new.mkv"


def test_remember_notified_persists(monkeypatch: pytest.MonkeyPatch) -> None:
    """已通知记录会持久化，插件重载后仍能去重。"""
    plugin = make_plugin()
    saved: List[Any] = []
    monkeypatch.setattr(
        plugin, "save_data", lambda key, value: saved.append((key, value))
    )
    plugin._remember_notified("/video/a.mkv")
    assert any(key == "notified_histories" for key, _ in saved)
    assert "/video/a.mkv" in plugin._notified_histories


def test_owned_histories_skip_failed_records() -> None:
    """被拦截产生的失败历史不能当成入库记录补发通知。"""
    from datetime import datetime as _dt

    plugin = make_plugin()
    plugin._record_owned_links(
        ["magnet:?xt=urn:btih:AAAA&dn=Some.Show.S01.1080p.WEB-DL"]
    )
    now_text = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
    records = [
        SimpleNamespace(
            src="/待整理/Some.Show.S01.1080p.WEB-DL/a.mkv",
            dest="",
            status=False,
            date=now_text,
        ),
        SimpleNamespace(
            src="/待整理/Some.Show.S01.1080p.WEB-DL/b.mkv",
            dest="/video/x.mkv",
            status=True,
            date=now_text,
        ),
    ]
    matched = plugin._owned_histories_from(records, within=600)
    assert len(matched) == 1
    assert matched[0].status is True


def test_sweep_recent_histories_sends_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """巡检按历史补发完成通知，同一记录只发一次。"""
    from datetime import datetime as _dt

    from app.db.oper.transferhistory import TransferHistoryOper

    plugin = make_plugin(
        _notify_window=0, _notify_strm=False, _notify_chat_id="123"
    )
    plugin._record_owned_links(
        ["magnet:?xt=urn:btih:AAAA&dn=Some.Show.S01.1080p.WEB-DL"]
    )
    now = _dt.now()
    TransferHistoryOper.by_date = {
        now.strftime("%Y-%m-%d"): [
            SimpleNamespace(
                src="/待整理/Some.Show.S01.1080p.WEB-DL/Some.Show.S01E01.mkv",
                title="Some Show",
                year="2024",
                seasons="S01",
                episodes="E01",
                dest="/video/电视剧/Some Show/Some.Show.S01E01.mkv",
                date=now.strftime("%Y-%m-%d %H:%M:%S"),
            )
        ]
    }
    sent: List[str] = []
    monkeypatch.setattr(
        plugin,
        "_send_text",
        lambda chat_id, text, message_id=None, keyboard=None: sent.append(text) or 1,
    )
    plugin._sweep_recent_histories()
    plugin._sweep_recent_histories()
    assert len(sent) == 1
    assert "整理完成" in sent[0]


def test_notify_from_recent_history_sends_and_dedups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """按历史补发通知，同一条记录只发一次。"""
    from datetime import datetime as _dt

    from app.db.oper.transferhistory import TransferHistoryOper

    plugin = make_plugin(
        _notify_window=0, _notify_strm=False, _notify_chat_id="123"
    )
    plugin._record_owned_links(
        ["magnet:?xt=urn:btih:AAAA&dn=Some.Show.S01.1080p.WEB-DL"]
    )
    now = _dt.now()
    TransferHistoryOper.by_date = {
        now.strftime("%Y-%m-%d"): [
            SimpleNamespace(
                src="/待整理/Some.Show.S01.1080p.WEB-DL/Some.Show.S01E01.mkv",
                title="Some Show",
                year="2024",
                seasons="S01",
                episodes="E01",
                dest="/video/电视剧/Some Show/Some.Show.S01E01.mkv",
                date=now.strftime("%Y-%m-%d %H:%M:%S"),
            )
        ]
    }
    sent: List[str] = []
    monkeypatch.setattr(
        plugin,
        "_send_text",
        lambda chat_id, text, message_id=None, keyboard=None: sent.append(text) or 1,
    )
    plugin._notify_from_recent_history()
    plugin._notify_from_recent_history()
    assert len(sent) == 1
    assert "整理完成" in sent[0]


def test_complete_uses_recent_history_when_payload_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """事件完全没有路径时，按时间窗从整理历史反查并发送通知。"""
    from datetime import datetime as _dt

    from app.db.oper.transferhistory import TransferHistoryOper

    plugin = make_plugin(
        _notify_window=0,
        _notify_strm=False,
        _notify_only_from_bot=True,
        _notify_chat_id="123",
    )
    plugin._record_owned_links(
        ["magnet:?xt=urn:btih:AAAA&dn=Some.Show.S01.1080p.WEB-DL"]
    )
    now_text = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
    TransferHistoryOper.by_date = {
        _dt.now().strftime("%Y-%m-%d"): [
            SimpleNamespace(
                src="/待整理/Some.Show.S01.1080p.WEB-DL/Some.Show.S01E01.mkv",
                title="Some Show",
                year="2024",
                seasons="S01",
                episodes="E01",
                dest="/video/电视剧/Some Show/Some.Show.S01E01.mkv",
                date=now_text,
            )
        ]
    }
    sent: List[str] = []
    monkeypatch.setattr(
        plugin,
        "_send_text",
        lambda chat_id, text, message_id=None, keyboard=None: sent.append(text) or 1,
    )
    event = SimpleNamespace(
        event_data=SimpleNamespace(
            mediainfo=None, transferinfo=None, fileitem=None, meta=None
        )
    )
    plugin._on_transfer_complete(event)
    assert sent and "整理完成" in sent[0]
    assert "Some Show" in sent[0]


def test_complete_handles_dict_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """事件载荷是字典（outbox 重投）时也能解析并发送通知。"""
    plugin = make_plugin(
        _notify_window=0,
        _notify_strm=False,
        _notify_only_from_bot=True,
        _notify_chat_id="123",
    )
    plugin._record_owned_links(
        ["magnet:?xt=urn:btih:AAAA&dn=Some.Show.S01.1080p.WEB-DL"]
    )
    sent: List[str] = []
    monkeypatch.setattr(
        plugin,
        "_send_text",
        lambda chat_id, text, message_id=None, keyboard=None: sent.append(text) or 1,
    )
    event = SimpleNamespace(
        event_data={
            "mediainfo": {
                "title": "Some Show",
                "year": "2024",
                "seasons": {1: [1]},
            },
            "transferinfo": {"target_item": {"path": "/video/电视剧/Some Show/x.mkv"}},
            "fileitem": {
                "path": "/待整理/Some.Show.S01.1080p.WEB-DL/Some.Show.S01E01.mkv"
            },
            "meta": {"episode_list": [1]},
        }
    )
    plugin._on_transfer_complete(event)
    assert sent and "整理完成" in sent[0]


def test_history_source_fallback() -> None:
    """事件缺少路径时可按整理历史 ID 反查源路径。"""
    from app.db.oper.transferhistory import TransferHistoryOper

    TransferHistoryOper.records = {
        123: SimpleNamespace(src="/待整理/Some.Show.S01.1080p/x.mkv")
    }
    plugin = make_plugin()
    assert (
        plugin._history_source(SimpleNamespace(transfer_history_id=123))
        == "/待整理/Some.Show.S01.1080p/x.mkv"
    )
    assert plugin._history_source(SimpleNamespace(transfer_history_id=None)) == ""
    assert plugin._history_source(SimpleNamespace()) == ""


def test_complete_uses_history_id_when_items_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fileitem 与 transferinfo 都为空时，用历史 ID 反查并正常发通知。"""
    from app.db.oper.transferhistory import TransferHistoryOper

    plugin = make_plugin(
        _notify_window=0,
        _notify_strm=False,
        _notify_only_from_bot=True,
        _notify_chat_id="123",
    )
    plugin._record_owned_links(
        ["magnet:?xt=urn:btih:AAAA&dn=Some.Show.S01.1080p.WEB-DL"]
    )
    TransferHistoryOper.records = {
        456: SimpleNamespace(
            src="/待整理/Some.Show.S01.1080p.WEB-DL/Some.Show.S01E01.mkv"
        )
    }
    sent: List[str] = []
    monkeypatch.setattr(
        plugin,
        "_send_text",
        lambda chat_id, text, message_id=None, keyboard=None: sent.append(text) or 1,
    )
    event = SimpleNamespace(
        event_data=SimpleNamespace(
            mediainfo=SimpleNamespace(title="Some Show", year="2024", seasons={1: [1]}),
            transferinfo=None,
            fileitem=None,
            meta=SimpleNamespace(episode_list=[1]),
            transfer_history_id=456,
        )
    )
    plugin._on_transfer_complete(event)
    assert sent and "整理完成" in sent[0]


def test_owned_prefix_match_tolerates_ads_in_path() -> None:
    """115 落盘名插入广告词时，用前缀仍能认出本插件任务。"""
    plugin = make_plugin(_owned_names={})
    plugin._record_owned_links(
        [
            "ed2k://|file|致命录像带：混音带.1080p.HD中英双字.mp4|100|0123456789ABCDEF0123456789ABCDEF|/"
        ]
    )
    path = (
        "/待整理/致命录像带：混音带.6v电影 地址发布页 www.6v123.net 收藏不迷路/"
        "致命录像带：混音带.1080p.HD中英双字[最新电影www.dyg7.com].mp4"
    )
    assert plugin._is_owned_path(path) is True


def test_complete_uses_transferinfo_fileitem_when_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """整理完成事件 fileitem 为空时，用 transferinfo 里的源文件项判断。"""
    plugin = make_plugin(
        _notify_window=0,
        _notify_strm=False,
        _notify_only_from_bot=True,
        _notify_chat_id="123",
    )
    plugin._record_owned_links(
        ["magnet:?xt=urn:btih:AAAA&dn=Some.Show.S01.1080p.WEB-DL"]
    )
    sent: List[str] = []
    monkeypatch.setattr(
        plugin,
        "_send_text",
        lambda chat_id, text, message_id=None, keyboard=None: sent.append(text) or 1,
    )
    event = SimpleNamespace(
        event_data=SimpleNamespace(
            mediainfo=SimpleNamespace(title="Some Show", year="2024", seasons={1: [1]}),
            transferinfo=SimpleNamespace(
                fileitem=SimpleNamespace(
                    path="/待整理/Some.Show.S01.1080p.WEB-DL/Some.Show.S01E01.mkv"
                ),
                target_item=SimpleNamespace(
                    path="/video/电视剧/Some Show/Some.Show.S01E01.mkv"
                ),
                target_diritem=None,
            ),
            fileitem=None,
            meta=SimpleNamespace(episode_list=[1]),
        )
    )
    plugin._on_transfer_complete(event)
    assert sent and "整理完成" in sent[0]


def test_sweep_skips_without_owned_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没有任何本插件任务标记时不发起残留巡检请求。"""
    plugin = make_plugin(_owned_names={})
    called: List[str] = []
    monkeypatch.setattr(
        plugin, "_get_dir_item", lambda path: called.append(path) or None
    )
    plugin._sweep_pending_residue()
    assert called == []


def test_filter_existing_links_skips_known_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """离线列表里已有同名任务时直接跳过，不再提交给 115。"""
    plugin = make_plugin()
    monkeypatch.setattr(
        plugin,
        "_list_offline_tasks",
        lambda: [{"name": "Some.Show.S01.1080p.WEB-DL"}],
    )
    pending, skipped = plugin._filter_existing_links(
        [
            "magnet:?xt=urn:btih:AAAA&dn=Some.Show.S01.1080p.WEB-DL",
            "magnet:?xt=urn:btih:BBBB&dn=Another.Movie.2024",
        ]
    )
    assert len(pending) == 1
    assert len(skipped) == 1


def test_misc_notify_merges_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    """同类通知在窗口内合并成一条。"""
    plugin = make_plugin(_notify_chat_id="123", _notify_window=60)
    sent: List[str] = []
    monkeypatch.setattr(
        plugin,
        "_send_text",
        lambda chat_id, text, message_id=None, keyboard=None: sent.append(text) or 1,
    )
    plugin._buffer_misc_notify("residue|/待整理/示例剧", "a.txt")
    plugin._buffer_misc_notify("residue|/待整理/示例剧", "b.txt")
    assert sent == []
    plugin._flush_misc_notify("residue|/待整理/示例剧")
    assert len(sent) == 1
    assert "a.txt" in sent[0] and "b.txt" in sent[0]


def test_remove_if_empty_deletes_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """目录为空时删除它。"""
    from app.chain.storage import StorageChain

    plugin = make_plugin(_cleanup_empty_dirs=True)
    deleted: List[Any] = []
    notified: List[str] = []
    monkeypatch.setattr(
        StorageChain, "list_files", lambda self, item, recursion=False: []
    )
    monkeypatch.setattr(
        StorageChain, "delete_file", lambda self, item: (deleted.append(item) or True)
    )
    monkeypatch.setattr(plugin, "_get_dir_item", lambda path: {"path": path})
    monkeypatch.setattr(
        plugin, "_buffer_misc_notify", lambda key, line: notified.append(key)
    )
    plugin._remove_if_empty("/待整理/示例剧")
    assert deleted
    # 空目录清理不再发送通知
    assert notified == []


def test_remove_if_empty_skips_non_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """目录非空时不删除。"""
    from app.chain.storage import StorageChain

    plugin = make_plugin(_cleanup_empty_dirs=True)
    deleted: List[Any] = []
    monkeypatch.setattr(
        StorageChain, "list_files", lambda self, item, recursion=False: [{"name": "x"}]
    )
    monkeypatch.setattr(
        StorageChain, "delete_file", lambda self, item: (deleted.append(item) or True)
    )
    monkeypatch.setattr(plugin, "_get_dir_item", lambda path: {"path": path})
    plugin._remove_if_empty("/待整理/示例剧")
    assert deleted == []


def test_purge_stale_records_only_touches_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """只清理目标目录下、名称能对上本插件标记的队列项与失败历史。"""
    plugin = make_plugin(
        _api_token="token",
        _owned_names={"示例剧专属标记": time.time() + 3600},
    )

    class _Resp:
        def __init__(self, payload: Dict[str, Any]) -> None:
            self._payload = payload

        def json(self) -> Dict[str, Any]:
            """返回预设响应体。"""
            return self._payload

    def fake_get(url: str, **kwargs: Any) -> _Resp:
        """按端点返回队列与历史。"""
        if url.endswith("/transfer/queue"):
            return _Resp(
                {
                    "data": [
                        {
                            "path": "/待整理/示例剧/示例剧专属标记.S01E01.mkv",
                            "storage": "u115",
                        },
                        {"path": "/待整理/示例剧/Other.Source.mkv", "storage": "u115"},
                        {"path": "/其它目录/b.mkv", "storage": "u115"},
                    ]
                }
            )
        return _Resp(
            {
                "data": {
                    "list": [
                        {
                            "id": 1,
                            "src": "/待整理/示例剧/示例剧专属标记.S01E01.mkv",
                            "title": "示例剧",
                            "status": False,
                        },
                        {
                            "id": 3,
                            "src": "/待整理/示例剧/Other.Source.mkv",
                            "title": "其它来源",
                            "status": False,
                        },
                        {
                            "id": 2,
                            "src": "/其它目录/b.mkv",
                            "title": "其它",
                            "status": False,
                        },
                        {
                            "id": 4,
                            "src": "/待整理/示例剧/示例剧专属标记.S01E02.mkv",
                            "title": "示例剧",
                            "status": True,
                        },
                    ]
                }
            }
        )

    deleted: List[Dict[str, Any]] = []

    def fake_delete(url: str, **kwargs: Any) -> _Resp:
        """记录删除请求。"""
        deleted.append(kwargs.get("json") or {})
        return _Resp({})

    monkeypatch.setattr(tgofflinebot.requests, "get", fake_get)
    monkeypatch.setattr(tgofflinebot.requests, "delete", fake_delete)
    plugin._purge_stale_records("/待整理/示例剧")
    # 只删同目录且名称匹配本插件标记的那一条队列项与一条历史
    assert len(deleted) == 2
    assert all("示例剧专属标记" in str(item) for item in deleted)


def test_intercept_schedules_residue_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """库存在拦截时也要安排残留清理。"""
    plugin = make_plugin(
        _delete_when_exists=False, _notify_chat_id="123", _precheck_enabled=True
    )
    monkeypatch.setattr(plugin, "_send_text", lambda *args, **kwargs: 1)
    monkeypatch.setattr(
        plugin.chain,
        "media_exists",
        lambda mediainfo: SimpleNamespace(server="Emby", seasons={}),
    )
    scheduled: List[str] = []
    monkeypatch.setattr(
        plugin, "_schedule_residue_cleanup", lambda path, fi: scheduled.append(path)
    )
    event = make_intercept_event()
    plugin._on_transfer_intercept(event)
    assert scheduled


def test_choice_callback_allows_group_member(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """群里任何成员都能点击「按 MP / 按 AI 整理」。"""
    plugin = make_plugin(_allowed_users={"123456789"})
    plugin._pending_groups["/待整理/示例剧"] = {
        "token": "tok1",
        "dir": "/待整理/示例剧",
        "paths": ["/待整理/示例剧/a.mkv"],
        "message_id": 1,
    }
    plugin._pending_choices["/待整理/示例剧/a.mkv"] = {"ai": {}}
    applied: List[Any] = []
    monkeypatch.setattr(plugin, "_answer_callback", lambda cid: None)
    monkeypatch.setattr(
        plugin,
        "_apply_choice_group",
        lambda dir_key, use_ai: (applied.append((dir_key, use_ai)) or "ok"),
    )
    monkeypatch.setattr(
        plugin, "_edit_text", lambda chat_id, message_id, text: None
    )
    plugin._handle_choice_callback(
        {
            "id": "cb1",
            "data": "tgob:mp:tok1",
            "from": {"id": 111},
            "message": {"message_id": 9, "chat": {"id": -1001234567890, "type": "supergroup"}},
        }
    )
    assert applied == [("/待整理/示例剧", False)]


def test_choice_callback_rejects_private_stranger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """私聊里非白名单用户点击仍被拒绝。"""
    plugin = make_plugin(_allowed_users={"123456789"})
    plugin._pending_groups["/待整理/示例剧"] = {
        "token": "tok1",
        "dir": "/待整理/示例剧",
        "paths": ["/待整理/示例剧/a.mkv"],
        "message_id": 1,
    }
    replied: List[str] = []
    monkeypatch.setattr(plugin, "_answer_callback", lambda cid: None)
    monkeypatch.setattr(
        plugin, "_apply_choice_group", lambda dir_key, use_ai: "should-not-run"
    )
    monkeypatch.setattr(
        plugin,
        "_edit_text",
        lambda chat_id, message_id, text: replied.append(text),
    )
    plugin._handle_choice_callback(
        {
            "id": "cb2",
            "data": "tgob:mp:tok1",
            "from": {"id": 111},
            "message": {"message_id": 9, "chat": {"id": 111, "type": "private"}},
        }
    )
    assert replied and "白名单" in replied[0]


def test_pending_group_deduplicates_same_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一文件被重复拦截时，待决策组里只保留一条。"""
    plugin = make_plugin(_precheck_timeout=0, _notify_chat_id="123")
    monkeypatch.setattr(
        plugin,
        "_send_text",
        lambda chat_id, text, message_id=None, keyboard=None: 1,
    )
    monkeypatch.setattr(plugin, "_edit_text", lambda chat_id, message_id, text: None)
    snapshot = {
        "title": "示例剧",
        "year": "2024",
        "type": "tv",
        "season": "第1季",
        "episode": "S01E01",
        "media_id": "1",
        "source": "tmdb",
    }
    ai = AiRecognition(name="另一部剧", year="2024", media_type="tv", confidence=0.9)
    path = "/待整理/示例剧/示例剧.S01E01.mkv"
    plugin._ask_choice(path, snapshot, ai, None)
    plugin._ask_choice(path, snapshot, ai, None)
    assert plugin._pending_groups["/待整理/示例剧"]["paths"] == [path]


def test_apply_choice_group_writes_rules_and_retries_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """整组选择 AI 时写入多条识别词，并只按目录重跑一次。"""
    plugin = make_plugin()
    calls: List[str] = []
    monkeypatch.setattr(
        plugin,
        "_trigger_manual_transfer",
        lambda path: (calls.append(path), (True, "ok"))[1],
    )
    monkeypatch.setattr(
        plugin, "_resolve_ai_media_id", lambda name, year, media_type: ("999", "tmdb")
    )
    SystemConfigOper._store["CustomIdentifiers"] = []
    dir_key = "/待整理/示例剧"
    plugin._pending_groups[dir_key] = {
        "token": "t1",
        "dir": dir_key,
        "paths": [f"{dir_key}/a.mkv", f"{dir_key}/b.mkv"],
        "message_id": 1,
    }
    plugin._pending_choices[f"{dir_key}/a.mkv"] = {
        "ai": {"name": "示例剧", "year": "2024", "media_type": "tv", "season": 1, "episode": 1}
    }
    plugin._pending_choices[f"{dir_key}/b.mkv"] = {
        "ai": {"name": "示例剧", "year": "2024", "media_type": "tv", "season": 1, "episode": 2}
    }
    text = plugin._apply_choice_group(dir_key, True)
    assert "2 个文件" in text
    assert calls == [dir_key]
    rules = SystemConfigOper._store["CustomIdentifiers"]
    assert len(rules) == 2
    assert plugin._is_skipped(f"{dir_key}/a.mkv") is True


def test_library_exists_deletes_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """库中已存在且开启删除时，删除源文件并在通知中说明。"""
    plugin = make_plugin(
        _delete_when_exists=True,
        _notify_chat_id="123",
        _precheck_enabled=True,
        _notify_window=0,
    )
    notified: List[str] = []
    monkeypatch.setattr(
        plugin, "_send_text", lambda chat_id, text, message_id=None, keyboard=None: notified.append(text) or 1
    )
    monkeypatch.setattr(
        plugin.chain,
        "media_exists",
        lambda mediainfo: SimpleNamespace(server="Emby", seasons={}),
    )
    deleted: List[Any] = []
    monkeypatch.setattr(
        plugin, "_delete_source", lambda fileitem: (deleted.append(fileitem) or True, "")
    )
    event = make_intercept_event()
    plugin._on_transfer_intercept(event)
    assert event.event_data.cancel is True
    assert deleted
    assert any("已删源" in text for text in notified)


def test_cleanup_stuck_offline_filters_and_deletes(monkeypatch: pytest.MonkeyPatch) -> None:
    """只删除超时且 0% 的下载中任务。"""
    plugin = make_plugin(_offline_timeout=5, _notify_chat_id="123")
    now = time.time()
    tasks = [
        {"info_hash": "aaa", "name": "stuck", "status": 0, "percent": 0, "add_time": now - 600},
        {"info_hash": "bbb", "name": "running", "status": 0, "percent": 3.5, "add_time": now - 300},
        {"info_hash": "ccc", "name": "recent", "status": 0, "percent": 0, "add_time": now - 60},
        {"info_hash": "ddd", "name": "done", "status": 2, "percent": 100, "add_time": now - 600},
    ]
    deleted: List[str] = []
    notified: List[str] = []
    monkeypatch.setattr(plugin, "_list_offline_tasks", lambda: tasks)
    monkeypatch.setattr(
        plugin, "_delete_offline_task", lambda info_hash: deleted.append(info_hash) or True
    )
    monkeypatch.setattr(
        plugin, "_send_text", lambda chat_id, text, message_id=None, keyboard=None: notified.append(text) or 1
    )
    plugin._cleanup_stuck_offline()
    assert deleted == ["aaa"]
    assert any("离线任务卡住" in text for text in notified)


def test_cleanup_deletes_progressed_but_stalled(monkeypatch: pytest.MonkeyPatch) -> None:
    """有进度但超过阈值仍未完成的任务同样被删除。"""
    plugin = make_plugin(
        _offline_timeout=5, _offline_progress_timeout=10, _notify_chat_id="123"
    )
    now = time.time()
    tasks = [
        {
            "info_hash": "bbb",
            "name": "half",
            "status": 0,
            "percent": 42.0,
            "add_time": now - 700,
        }
    ]
    deleted: List[str] = []
    notified: List[str] = []
    monkeypatch.setattr(plugin, "_list_offline_tasks", lambda: tasks)
    monkeypatch.setattr(
        plugin, "_delete_offline_task", lambda info_hash: deleted.append(info_hash) or True
    )
    monkeypatch.setattr(
        plugin,
        "_send_text",
        lambda chat_id, text, message_id=None, keyboard=None: notified.append(text) or 1,
    )
    plugin._cleanup_stuck_offline()
    assert deleted == ["bbb"]
    assert "42%" in notified[0]


def test_progressed_task_within_window_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    """有进度但未超过阈值的任务保留。"""
    plugin = make_plugin(
        _offline_timeout=5, _offline_progress_timeout=10, _notify_chat_id="123"
    )
    now = time.time()
    tasks = [
        {
            "info_hash": "eee",
            "name": "downloading",
            "status": 0,
            "percent": 80.0,
            "add_time": now - 300,
        }
    ]
    deleted: List[str] = []
    monkeypatch.setattr(plugin, "_list_offline_tasks", lambda: tasks)
    monkeypatch.setattr(
        plugin, "_delete_offline_task", lambda info_hash: deleted.append(info_hash) or True
    )
    monkeypatch.setattr(plugin, "_send_text", lambda *args, **kwargs: 1)
    plugin._cleanup_stuck_offline()
    assert deleted == []


def test_cleanup_residue_keeps_media_and_deletes_ads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """正片/字幕保留，广告与无结论文件删除。"""
    plugin = make_plugin(
        _cleanup_residue_enabled=True, _cleanup_unrecognized=True, _notify_window=0
    )
    video = SimpleNamespace(
        type="file", path="/待整理/示例剧/示例剧.S01E01.mkv", name="示例剧.S01E01.mkv", extension="mkv"
    )
    subtitle = SimpleNamespace(
        type="file", path="/待整理/示例剧/示例剧.S01E01.ass", name="示例剧.S01E01.ass", extension="ass"
    )
    ad = SimpleNamespace(
        type="file", path="/待整理/示例剧/最新网址.txt", name="最新网址.txt", extension="txt"
    )
    unknown = SimpleNamespace(
        type="file", path="/待整理/示例剧/xyz.dat", name="xyz.dat", extension="dat"
    )
    monkeypatch.setattr(
        plugin,
        "_list_residue_items",
        lambda fileitem, parent="": [video, subtitle, ad, unknown],
    )
    seen: List[List[str]] = []

    def fake_verdict(names: List[str]) -> Dict[str, Optional[bool]]:
        """记录参与判定的文件，并按剧本返回结论。"""
        seen.append(list(names))
        verdict: Dict[str, Optional[bool]] = {}
        for name in names:
            if name == "最新网址.txt":
                verdict[name] = True
            elif name == "xyz.dat":
                verdict[name] = None
            else:
                verdict[name] = False
        return verdict

    monkeypatch.setattr(plugin, "_ai_residue_verdict", fake_verdict)
    deleted: List[str] = []
    monkeypatch.setattr(
        plugin, "_delete_residue_item", lambda item: deleted.append(item.name) or True
    )
    notified: List[str] = []
    monkeypatch.setattr(
        plugin, "_send_text", lambda chat_id, text, message_id=None, keyboard=None: notified.append(text) or 1
    )
    plugin._cleanup_residue(
        "/待整理/示例剧",
        SimpleNamespace(type="file", path="/待整理/示例剧/示例剧.S01E01.mkv"),
    )
    assert deleted == ["最新网址.txt", "xyz.dat"]
    # 不限制扩展名：正片与字幕也参与 AI 判定
    assert seen and "示例剧.S01E01.mkv" in seen[0]
    assert "示例剧.S01E01.ass" in seen[0]
    # 残留清理不再发送通知
    assert notified == []


def test_cleanup_residue_respects_unrecognized_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """关闭「AI 无法判断也删除」时，只删明确判定为广告的文件。"""
    plugin = make_plugin(_cleanup_unrecognized=False)
    ad = SimpleNamespace(type="file", path="/待整理/示例剧/a.txt", name="a.txt", extension="txt")
    unknown = SimpleNamespace(type="file", path="/待整理/示例剧/b.dat", name="b.dat", extension="dat")
    monkeypatch.setattr(
        plugin, "_list_residue_items", lambda fileitem, parent="": [ad, unknown]
    )
    monkeypatch.setattr(plugin, "_ai_residue_verdict", lambda names: {name: None for name in names})
    deleted: List[str] = []
    monkeypatch.setattr(
        plugin, "_delete_residue_item", lambda item: deleted.append(item.name) or True
    )
    monkeypatch.setattr(plugin, "_send_text", lambda *args, **kwargs: 1)
    plugin._cleanup_residue(
        "/待整理/示例剧",
        SimpleNamespace(type="file", path="/待整理/示例剧/示例剧.S01E01.mkv"),
    )
    assert deleted == []


def test_api_whitelist_refuses_empty() -> None:
    """API 删除最后一名白名单成员会被拒绝，且名单不被清空。"""
    plugin = make_plugin(_allowed_users={"123456789"})

    class _Request:
        query_params: Dict[str, Any] = {}

        @staticmethod
        async def json() -> Dict[str, Any]:
            """模拟请求体。"""
            return {"action": "remove", "user_id": "123456789"}

    result = asyncio.run(plugin.api_whitelist(_Request()))
    assert result["success"] is False
    assert plugin._allowed_users == {"123456789"}


def test_residue_skips_files_beyond_review_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """超过单次送审上限的文件不参与删除。"""
    plugin = make_plugin(_cleanup_unrecognized=True)
    items = [
        SimpleNamespace(
            type="file", path=f"/待整理/示例剧/f{index}.dat", name=f"f{index}.dat"
        )
        for index in range(60)
    ]
    monkeypatch.setattr(
        plugin, "_list_residue_items", lambda fileitem, parent="": items
    )
    reviewed: List[List[str]] = []

    def fake_verdict(names: List[str]) -> Dict[str, Optional[bool]]:
        """模拟真实实现：只送审前 50 个，其余不出现在结果里。"""
        reviewed.append(list(names))
        return {name: True for name in names[:50]}

    monkeypatch.setattr(plugin, "_ai_residue_verdict", fake_verdict)
    deleted: List[str] = []
    monkeypatch.setattr(
        plugin, "_delete_residue_item", lambda item: deleted.append(item.name) or True
    )
    monkeypatch.setattr(plugin, "_send_text", lambda *args, **kwargs: 1)
    plugin._cleanup_residue(
        "/待整理/示例剧", SimpleNamespace(type="file", path="/待整理/示例剧/a.mkv")
    )
    assert reviewed and len(reviewed[0]) == 60
    assert len(deleted) == 50


def test_identifier_rule_is_persisted(monkeypatch: pytest.MonkeyPatch) -> None:
    """写入识别词后把清单持久化，重启后仍能清理。"""
    plugin = make_plugin()
    monkeypatch.setattr(plugin, "_trigger_manual_transfer", lambda path: (True, "ok"))
    monkeypatch.setattr(
        plugin, "_resolve_ai_media_id", lambda name, year, media_type: ("1", "tmdb")
    )
    saved: List[str] = []
    monkeypatch.setattr(
        plugin, "save_data", lambda key, value: saved.append(key)
    )
    target = {
        "path": "/待整理/示例剧/a.mkv",
        "ai": {
            "name": "示例剧",
            "year": "2024",
            "media_type": "tv",
            "season": 1,
            "episode": 1,
        },
    }
    plugin._apply_choice("/待整理/示例剧/a.mkv", True, target)
    assert "identifier_rules" in saved


def test_residue_cleanup_debounces_by_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同目录连续完成只保留一个计时器，被替换的旧计时器不执行。"""
    plugin = make_plugin(_residue_delay=60)
    calls: List[str] = []
    monkeypatch.setattr(
        plugin, "_cleanup_residue", lambda parent, fileitem: calls.append(parent)
    )
    fileitem = SimpleNamespace(type="file", path="/待整理/示例剧/a.mkv")
    plugin._schedule_residue_cleanup("/待整理/示例剧/a.mkv", fileitem)
    first = plugin._residue_timers["/待整理/示例剧"]
    plugin._schedule_residue_cleanup("/待整理/示例剧/b.mkv", fileitem)
    second = plugin._residue_timers["/待整理/示例剧"]
    assert second is not first
    assert first.finished.is_set()
    # 旧计时器到点也不应执行
    plugin._run_residue_cleanup("/待整理/示例剧", fileitem)
    assert calls == []
    # 模拟自己就是当前计时器
    plugin._residue_timers["/待整理/示例剧"] = threading.current_thread()
    plugin._run_residue_cleanup("/待整理/示例剧", fileitem)
    assert calls == ["/待整理/示例剧"]
    second.cancel()


def test_cleanup_residue_skips_scope_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """源文件直接位于 /待整理 根时不做目录扫描。"""
    plugin = make_plugin(_precheck_scope="/待整理")
    calls: List[int] = []
    monkeypatch.setattr(
        plugin, "_list_residue_items", lambda fileitem, parent="": calls.append(1) or []
    )
    plugin._cleanup_residue(
        "/待整理",
        SimpleNamespace(type="file", path="/待整理/示例电影.mkv"),
    )
    assert calls == []


def test_cleanup_residue_throttles_same_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一目录在节流窗口内只扫描一次。"""
    plugin = make_plugin()
    calls: List[int] = []
    monkeypatch.setattr(
        plugin, "_list_residue_items", lambda fileitem, parent="": calls.append(1) or []
    )
    for _ in range(2):
        plugin._cleanup_residue(
            "/待整理/示例剧",
            SimpleNamespace(type="file", path="/待整理/示例剧/示例剧.S01E01.mkv"),
        )
    assert len(calls) == 1


def test_non_owned_task_skips_precheck(monkeypatch: pytest.MonkeyPatch) -> None:
    """非本插件提交的任务完全不触发预检（连媒体库都不查）。"""
    plugin = make_plugin(_owned_names={})
    called: List[int] = []
    monkeypatch.setattr(
        plugin, "_library_exists", lambda mediainfo, fileitem: called.append(1) or None
    )
    monkeypatch.setattr(plugin, "_ai_recognize", lambda path, mediainfo: None)
    event = make_intercept_event()
    plugin._on_transfer_intercept(event)
    assert event.event_data.cancel is False
    assert called == []


def test_owned_name_recorded_from_links(monkeypatch: pytest.MonkeyPatch) -> None:
    """提交链接后记录来源标记，磁力按 dn、ed2k 按文件名。"""
    plugin = make_plugin(_owned_names={})
    plugin._record_owned_links(
        [
            "magnet:?xt=urn:btih:AAAA&dn=Some.Show.S01.1080p.WEB-DL",
            "ed2k://|file|Another.Movie.2024.mkv|100|0123456789ABCDEF0123456789ABCDEF|/",
        ]
    )
    assert plugin._is_owned_path("/待整理/Some.Show.S01.1080p.WEB-DL/Some.Show.S01E01.mkv")
    assert plugin._is_owned_path("/待整理/Another.Movie.2024.mkv")
    assert plugin._is_owned_path("/待整理/Unrelated.Release.2024/x.mkv") is False


def test_link_info_hash_normalizes_both_forms() -> None:
    """磁力 btih 的十六进制与 base32 两种写法都要归一化成 40 位小写十六进制。"""
    plugin = make_plugin(_owned_names={})
    info_hash = "f7b6c5016b4c299ee42b70e6cc7d1b0938d7c37e"
    base32_hash = base64.b32encode(bytes.fromhex(info_hash)).decode()
    assert plugin._link_info_hash(f"magnet:?xt=urn:btih:{info_hash.upper()}") == info_hash
    assert plugin._link_info_hash(f"magnet:?xt=urn:btih:{base32_hash}") == info_hash
    assert plugin._link_info_hash("magnet:?xt=urn:btih:AAAA&dn=Some.Show") == ""
    assert plugin._link_info_hash("ed2k://|file|A.mkv|1|0123456789ABCDEF0123456789ABCDEF|/") == ""


def test_owned_name_recorded_from_offline_task(monkeypatch: pytest.MonkeyPatch) -> None:
    """磁力不带 dn 时，按 info_hash 从 115 离线任务名补记来源，预检不再误判。"""
    plugin = make_plugin(_owned_names={})
    monkeypatch.setattr(plugin, "_start_owned_sync", lambda hashes: None)
    info_hash = "f7b6c5016b4c299ee42b70e6cc7d1b0938d7c37e"
    plugin._record_owned_links([f"magnet:?xt=urn:btih:{info_hash.upper()}"])
    # 没有 dn 时拿不到名称，只能先记住 info_hash
    assert plugin._owned_names == {}
    assert info_hash in plugin._pending_owned_hashes
    name = (
        "[ 高清剧集网 www.BTHDTV.com ]是，首相  第二季[全8集][中文字幕]."
        "首相.Yes.Prime.Minister.S02.1987.2160p.WEB-DL.H265.AAC-SeeWEB"
    )
    tasks = [
        {
            "info_hash": info_hash,
            "name": name,
            "status": 2,
            "percent": 100.0,
            "add_time": time.time(),
        }
    ]
    monkeypatch.setattr(plugin, "_list_offline_tasks", lambda: tasks)
    plugin._sync_owned_task_names(attempts=1)
    assert plugin._is_owned_path(f"/待整理/{name}/01.mkv") is True
    assert plugin._pending_owned_hashes == {}


def test_owned_sync_ignores_other_offline_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    """补记来源只认本次提交的 info_hash，其它渠道的任务不能算成本插件任务。"""
    plugin = make_plugin(_owned_names={})
    monkeypatch.setattr(plugin, "_start_owned_sync", lambda hashes: None)
    plugin._record_owned_links(["magnet:?xt=urn:btih:" + "A" * 40])
    tasks = [
        {
            "info_hash": "b" * 40,
            "name": "Other.Release.2024",
            "status": 2,
            "percent": 100.0,
            "add_time": time.time(),
        }
    ]
    monkeypatch.setattr(plugin, "_list_offline_tasks", lambda: tasks)
    plugin._sync_owned_task_names(attempts=1)
    assert plugin._is_owned_path("/待整理/Other.Release.2024/x.mkv") is False


def test_owned_sync_is_single_flight(monkeypatch: pytest.MonkeyPatch) -> None:
    """短时间内连续提交只启动一个补记线程，避免并发请求 115 接口。"""
    plugin = make_plugin(_owned_names={})
    plugin._owned_sync_lock = threading.Lock()
    started: List[str] = []

    class FakeThread:
        """只记录启动次数、不真正执行 target 的线程替身。"""

        def __init__(
            self,
            target: Any = None,
            args: Any = (),
            name: str = "",
            daemon: bool = False,
        ) -> None:
            self.name = name

        def start(self) -> None:
            """记录线程名，不执行任务。"""
            started.append(self.name)

    monkeypatch.setattr(tgofflinebot.threading, "Thread", FakeThread)
    plugin._start_owned_sync(["a" * 40])
    plugin._start_owned_sync(["b" * 40])
    assert started == ["TgOfflineBot-OwnedSync"]
    plugin._owned_sync_lock.release()


def test_cleanup_skips_non_owned_offline_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """只清理本插件提交的离线任务，其它渠道的任务不动。"""
    plugin = make_plugin(_owned_names={"ownedname": time.time() + 3600})
    now = time.time()
    tasks = [
        {
            "info_hash": "foreign",
            "name": "Other.Channel.Task",
            "status": 0,
            "percent": 0,
            "add_time": now - 900,
        },
        {
            "info_hash": "mine",
            "name": "OwnedName.Release",
            "status": 0,
            "percent": 0,
            "add_time": now - 900,
        },
    ]
    deleted: List[str] = []
    monkeypatch.setattr(plugin, "_list_offline_tasks", lambda: tasks)
    monkeypatch.setattr(
        plugin, "_delete_offline_task", lambda info_hash: deleted.append(info_hash) or True
    )
    monkeypatch.setattr(plugin, "_send_text", lambda *args, **kwargs: 1)
    plugin._cleanup_stuck_offline()
    assert deleted == ["mine"]


def test_allow_upgrade_passes_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    """开启洗版后，库里已有的媒体不再拦截，也不删源。"""
    plugin = make_plugin(
        _allow_upgrade=True, _notify_chat_id="123", _precheck_enabled=True
    )
    monkeypatch.setattr(plugin, "_send_text", lambda *args, **kwargs: 1)
    monkeypatch.setattr(
        plugin.chain,
        "media_exists",
        lambda mediainfo: SimpleNamespace(server="Emby", seasons={}),
    )
    deleted: List[Any] = []
    monkeypatch.setattr(
        plugin, "_delete_source", lambda fileitem: (deleted.append(fileitem) or True, "")
    )
    event = make_intercept_event()
    plugin._on_transfer_intercept(event)
    assert event.event_data.cancel is False
    assert deleted == []
    assert plugin._history[-1]["kind"] == "洗版放行"


def test_whitelist_username_matches() -> None:
    """白名单支持 @用户名，大小写不敏感。"""
    plugin = make_plugin(_allowed_users={"@alice"})
    replies = capture_replies(plugin)
    plugin._handle_update(
        make_update("/status", user_id=999, username="Alice")
    )
    assert replies and "运行状态" in replies[0]


def test_offline_tasks_paginates(monkeypatch: pytest.MonkeyPatch) -> None:
    """离线任务列表按页拉取，超过一页也能取全。"""
    plugin = make_plugin()
    pages = {
        1: [{"info_hash": f"h{index}"} for index in range(100)],
        2: [{"info_hash": "last1"}, {"info_hash": "last2"}],
    }
    monkeypatch.setattr(
        plugin,
        "_request_offline_tasks",
        lambda token, page, limit: pages.get(page, []),
    )
    tasks = plugin._list_offline_tasks()
    assert tasks is not None
    assert len(tasks) == 102


def test_offline_list_failure_keeps_active_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """列表接口失败时不能当成没有任务，保留在途标记。"""
    plugin = make_plugin(_offline_timeout=5)
    monkeypatch.setattr(plugin, "_list_offline_tasks", lambda: None)
    before = plugin._offline_active_until
    plugin._cleanup_stuck_offline()
    assert plugin._offline_active_until == before


def test_directory_source_is_not_deleted(monkeypatch: pytest.MonkeyPatch) -> None:
    """目录型源不自动删除，避免误删整季包。"""
    plugin = make_plugin()
    ok, detail = plugin._delete_source(SimpleNamespace(type="dir", path="/待整理/某剧"))
    assert ok is False
    assert "目录" in detail


def test_notify_forces_flush_after_long_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """持续有新集时，超过 30 分钟强制发一次通知。"""
    plugin = make_plugin(
        _notify_window=60, _notify_only_from_bot=False, _notify_strm=False,
        _notify_chat_id="123",
    )
    sent: List[str] = []
    monkeypatch.setattr(
        plugin,
        "_send_text",
        lambda chat_id, text, message_id=None, keyboard=None: sent.append(text) or 1,
    )
    key = "示例剧|2024|1|"
    plugin._notify_buffer[key] = {
        "title": "示例剧",
        "year": "2024",
        "season": 1,
        "episodes": {1},
        "dests": {"/video/电视剧/示例剧/示例剧.S01E01.mkv"},
        "first_ts": time.time() - 1900,
    }
    plugin._buffer_complete("示例剧", "2024", 1, 2, "/video/电视剧/示例剧/示例剧.S01E02.mkv")
    assert sent
    assert "E01,E02" in sent[0]


def test_notify_defaults_merge_quietly_and_skip_strm() -> None:
    """默认静默窗口 300 秒、STRM 通知默认关闭。"""
    plugin = make_plugin()
    _, defaults = plugin.get_form()
    assert defaults["notify_window"] == 300
    assert defaults["notify_strm"] is False
    assert tgofflinebot.NOTIFY_MAX_HOLD_SECONDS == 1800


def test_flush_notify_skips_strm_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """关闭 STRM 通知后，整理完成只发入库汇总，不启动 STRM 核对。"""
    plugin = make_plugin(
        _notify_window=0,
        _notify_strm=False,
        _notify_only_from_bot=False,
        _notify_chat_id="123",
    )
    sent: List[str] = []
    checks: List[str] = []
    monkeypatch.setattr(
        plugin,
        "_send_text",
        lambda chat_id, text, message_id=None, keyboard=None: sent.append(text) or 1,
    )
    monkeypatch.setattr(
        plugin, "_verify_strm_batch", lambda dests, label: checks.append(label)
    )
    plugin._buffer_complete(
        "示例剧", "2024", 1, 1, "/video/电视剧/示例剧/示例剧.S01E01.mkv"
    )
    assert len(sent) == 1
    assert checks == []


def test_scope_root_directory_is_not_batch_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """源目录就是比对范围根时，逐个文件重跑而不是整目录重跑。"""
    plugin = make_plugin(_precheck_scope="/待整理")
    dir_calls: List[str] = []
    file_calls: List[str] = []
    monkeypatch.setattr(
        plugin, "_trigger_manual_transfer", lambda path: (dir_calls.append(path), (True, "ok"))[1]
    )
    monkeypatch.setattr(
        plugin, "_retry_transfer", lambda path, fileitem: (file_calls.append(path), (True, "ok"))[1]
    )
    plugin._pending_groups["/待整理"] = {
        "token": "t",
        "dir": "/待整理",
        "paths": ["/待整理/a.mkv", "/待整理/b.mkv"],
        "message_id": 1,
    }
    plugin._pending_choices["/待整理/a.mkv"] = {"ai": {}}
    plugin._pending_choices["/待整理/b.mkv"] = {"ai": {}}
    plugin._apply_choice_group("/待整理", False)
    assert dir_calls == []
    assert len(file_calls) == 2


def test_prompts_only_expose_expected_variables() -> None:
    """提示词里的 JSON 示例必须转义，否则会被当成模板变量导致调用失败。"""
    plugin = make_plugin()
    assert set(plugin._ai_prompt().input_variables) == {"title", "path", "meta_hint"}
    assert plugin._residue_prompt().input_variables == ["names"]


def test_parse_json_object_tolerates_fences_and_text() -> None:
    """模型输出带代码围栏或前后说明时仍能解析出 JSON。"""
    plugin = make_plugin()
    assert plugin._parse_json_object('{"name": "示例", "year": "2024"}') == {
        "name": "示例",
        "year": "2024",
    }
    fenced = '结果如下：\n```json\n{"name": "示例", "year": "2024"}\n```\n以上。'
    assert plugin._parse_json_object(fenced) == {"name": "示例", "year": "2024"}
    assert plugin._parse_json_object("标题：示例\n年份：2024") is None


def test_ai_recognize_parses_plain_text_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """模型返回纯文本 JSON 时能解析为结构化识别结果。"""
    plugin = make_plugin()
    monkeypatch.setattr(
        plugin,
        "_invoke_llm_text",
        lambda prompt, payload, timeout: '{"name": "极限审判", "year": "2026", '
        '"media_type": "movie", "season": 0, "episode": 0, '
        '"confidence": 0.95, "reason": "ok"}',
    )
    result = plugin._ai_recognize(
        "/待整理/极限审判/Mercy.2026.mkv", make_mediainfo("极限审判", "2026")
    )
    assert result is not None
    assert result.name == "极限审判"
    assert result.confidence == 0.95


def test_ai_recognize_normalizes_numeric_year(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """模型把年份返回成数字时也能解析（真实模型的实际返回形态）。"""
    plugin = make_plugin()
    monkeypatch.setattr(
        plugin,
        "_invoke_llm_text",
        lambda prompt, payload, timeout: '{"name":"Mercy","year":2026,'
        '"media_type":"movie","season":0,"episode":0,"confidence":0.98,'
        '"reason":"ok"}',
    )
    result = plugin._ai_recognize(
        "/待整理/Mercy/Mercy.2026.mkv", make_mediainfo("Mercy", "2026")
    )
    assert result is not None
    assert result.name == "Mercy"
    assert result.year == "2026"
    assert result.confidence == 0.98


def test_residue_verdict_ignores_non_list_delete_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """delete_files 不是列表时不删除任何文件。"""
    plugin = make_plugin()
    monkeypatch.setattr(
        plugin,
        "_invoke_llm_text",
        lambda prompt, payload, timeout: '{"delete_files": "a.txt"}',
    )
    verdict = plugin._ai_residue_verdict(["a.txt"])
    assert verdict.get("a.txt") is False


def test_ai_recognize_reports_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """模型返回自然语言时记录为 AI 不可用，而不是静默放行。"""
    plugin = make_plugin(_notify_chat_id="123")
    monkeypatch.setattr(
        plugin,
        "_invoke_llm_text",
        lambda prompt, payload, timeout: "标题：Mercy\n年份：2026\n置信度：0.95",
    )
    notified: List[str] = []
    monkeypatch.setattr(
        plugin,
        "_send_text",
        lambda chat_id, text, message_id=None, keyboard=None: notified.append(text) or 1,
    )
    result = plugin._ai_recognize(
        "/待整理/极限审判/Mercy.2026.mkv", make_mediainfo("极限审判", "2026")
    )
    assert result is None
    assert notified and "AI 识别不可用" in notified[0]
    assert plugin._history[-1]["kind"] == "AI不可用"


def test_residue_verdict_returns_empty_on_unparseable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """残留判定无法解析时返回空结果，调用方不会删除任何文件。"""
    plugin = make_plugin()
    monkeypatch.setattr(
        plugin,
        "_invoke_llm_text",
        lambda prompt, payload, timeout: "这些文件都可以保留",
    )
    monkeypatch.setattr(plugin, "_send_text", lambda *args, **kwargs: 1)
    assert plugin._ai_residue_verdict(["a.txt", "b.dat"]) == {}


def test_residue_verdict_parses_delete_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """残留判定能解析 JSON 中的删除清单。"""
    plugin = make_plugin()
    monkeypatch.setattr(
        plugin,
        "_invoke_llm_text",
        lambda prompt, payload, timeout: '{"delete_files": ["a.txt"], "reason": "广告"}',
    )
    verdict = plugin._ai_residue_verdict(["a.txt", "b.dat"])
    assert verdict == {"a.txt": True, "b.dat": False}


def test_library_name_matches_media_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """按目标路径匹配到 Emby 库名。"""
    plugin = make_plugin()
    plugin._strm_local_root = "/mnt/strm"
    plugin._strm_pan_root = "/video"
    monkeypatch.setattr(
        plugin,
        "_media_library_map",
        lambda: [("日番", ["/mnt/strm/电视剧/日番"])],
    )
    name = plugin._library_name_for(
        "/video/电视剧/日番/全职猎人.HUNTER×HUNTER (2011)/Season 1/全职猎人.S01E52.mkv"
    )
    assert name == "日番"


def test_complete_notification_includes_library(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """入库通知里带上媒体库名。"""
    plugin = make_plugin(
        _notify_window=0,
        _notify_only_from_bot=False,
        _notify_strm=False,
        _notify_chat_id="123",
    )
    plugin._strm_local_root = "/mnt/strm"
    plugin._strm_pan_root = "/video"
    monkeypatch.setattr(
        plugin,
        "_media_library_map",
        lambda: [("日番", ["/mnt/strm/电视剧/日番"])],
    )
    sent: List[str] = []
    monkeypatch.setattr(
        plugin,
        "_send_text",
        lambda chat_id, text, message_id=None, keyboard=None: sent.append(text) or 1,
    )
    plugin._buffer_complete(
        "全职猎人",
        "2011",
        1,
        52,
        "/video/电视剧/日番/全职猎人 (2011)/Season 1/全职猎人.S01E52.mkv",
    )
    assert sent
    assert "媒体库：日番" in sent[0]


def test_updates_subscribe_callback_query(monkeypatch: pytest.MonkeyPatch) -> None:
    """长轮询同时订阅普通消息与按钮回调。"""
    captured: Dict[str, Any] = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def json() -> Dict[str, Any]:
            """返回空结果。"""
            return {"ok": True, "result": []}

    def fake_get(url: str, **kwargs: Any) -> _Resp:
        """记录请求参数。"""
        captured.update(kwargs)
        return _Resp()

    monkeypatch.setattr(tgofflinebot.requests, "get", fake_get)
    plugin = make_plugin()
    assert plugin._get_updates() == []
    assert "callback_query" in captured["params"]["allowed_updates"]


def test_cleanup_skips_when_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有在途任务时完全不请求离线列表。"""
    plugin = make_plugin(_offline_timeout=5)
    plugin._offline_active_until = 0.0
    calls: List[int] = []
    monkeypatch.setattr(
        plugin, "_list_offline_tasks", lambda: calls.append(1) or []
    )
    plugin._cleanup_stuck_offline()
    assert calls == []


def test_cleanup_clears_active_flag_when_no_downloading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """列表里没有下载中的任务时清掉在途标记。"""
    plugin = make_plugin(_offline_timeout=5)
    tasks = [
        {"info_hash": "ddd", "name": "done", "status": 2, "percent": 100, "add_time": time.time() - 900}
    ]
    monkeypatch.setattr(plugin, "_list_offline_tasks", lambda: tasks)
    plugin._cleanup_stuck_offline()
    assert plugin._offline_active_until == 0.0


def test_cleanup_stuck_offline_reports_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """删除失败时也发通知，同一任务不重复刷。"""
    plugin = make_plugin(_offline_timeout=5, _notify_chat_id="123")
    now = time.time()
    tasks = [
        {"info_hash": "aaa", "name": "stuck", "status": 0, "percent": 0, "add_time": now - 600}
    ]
    notified: List[str] = []
    monkeypatch.setattr(plugin, "_list_offline_tasks", lambda: tasks)
    monkeypatch.setattr(plugin, "_delete_offline_task", lambda info_hash: False)
    monkeypatch.setattr(
        plugin, "_send_text", lambda chat_id, text, message_id=None, keyboard=None: notified.append(text) or 1
    )
    plugin._cleanup_stuck_offline()
    plugin._cleanup_stuck_offline()
    assert len(notified) == 1
    assert "删除失败" in notified[0]


def test_manual_transfer_uses_parent_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    """重跑整理时把文件路径换成父目录，避免 115 上被当成目录创建。"""
    captured: Dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> _FakeResponse:
        """记录请求体。"""
        captured.update(kwargs)
        return _FakeResponse(200, {"code": 0, "msg": "ok"})

    monkeypatch.setattr(tgofflinebot.requests, "post", fake_post)
    plugin = make_plugin()
    ok, _ = plugin._trigger_manual_transfer("/待整理/剧集/示例.S01E01.mkv")
    assert ok is True
    assert captured["json"] == {"path": "/待整理/剧集"}


# ============================ 代理解析 ============================


def test_proxies_from_host_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    """宿主 PROXY 是字典时原样展开为 requests 代理。"""
    plugin = make_plugin(_proxy="")
    monkeypatch.setattr(
        TgOfflineBot,
        "_host_setting",
        lambda self, name, default=None: {"http": "http://1.2.3.4:7890", "https": "http://1.2.3.4:7890"}
        if name == "PROXY"
        else default,
    )
    assert plugin._telegram_proxies() == {
        "http": "http://1.2.3.4:7890",
        "https": "http://1.2.3.4:7890",
    }


def test_proxies_from_plugin_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """插件配置的单地址字符串同时用于 http 与 https。"""
    plugin = make_plugin(_proxy="socks5://1.2.3.4:1080")
    monkeypatch.setattr(TgOfflineBot, "_host_setting", lambda self, name, default=None: default)
    assert plugin._telegram_proxies() == {
        "http": "socks5://1.2.3.4:1080",
        "https": "socks5://1.2.3.4:1080",
    }


def test_proxies_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """两边都没配置时不设置代理。"""
    plugin = make_plugin(_proxy="")
    monkeypatch.setattr(TgOfflineBot, "_host_setting", lambda self, name, default=None: "")
    assert plugin._telegram_proxies() is None
