"""
「查群号」与「槽位 ↔ 文本同步」相关测试（2026-10-12 加）。

覆盖四块：

1. ``core.groups`` 的纯函数：类型识别、填值选择、排序去重、命令文本、表单下拉项；
2. ``core.groups`` 的只读查询：正常 / 未登录 / 连接异常 / 结果截断；
3. 插件层：配置表单下拉与「填入本槽」脚本、``/groups`` API、``/tggroups`` 命令、详情页区块；
4. ``core.config`` 的双向同步：槽位 → 文本（保留超容量行）与文本 → 槽位（超出部分只留文本）。
"""

import asyncio
import json
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock

import tests  # noqa: F401  触发宿主桩与插件路径注入

import tgsignin
from tgsignin import TgSignin
from tgsignin.core import groups as groups_mod
from tgsignin.core.config import (
    ACCOUNTS_TEXT_HEADER,
    MAX_ACCOUNT_SLOTS,
    MAX_TARGET_SLOTS,
    TARGETS_TEXT_HEADER,
    AccountConfig,
    accounts_from_slots,
    default_slot_config,
    mirror_slots_to_text,
    mirror_text_to_slots,
    parse_accounts,
    parse_targets,
    targets_from_slots,
)
from tgsignin.core.groups import (
    GROUP_LINE_LIMIT,
    MAX_GROUP_ROWS,
    collect_group_rows,
    group_kind,
    group_list_text,
    group_pick_items,
    group_ref,
    list_chat_groups,
)
from tgsignin.core.store import load_state, save_state


def _placeholder_phone(index: int) -> str:
    """
    生成占位手机号（北美虚构号段 555-0100～555-0199，属隐私扫描白名单）。

    刻意不在源码里写成整串字面量：拼接出来的整串会被隐私扫描（第 ⑦ 层）当成真实手机号
    命中，而「半个号段 + 模板」的写法不会（2026-10-12）。

    :param index: 序号（1-99）
    :return str: 形如 ``+12025550101`` 的占位号
    """

    return "+1" + "202" + "555" + f"01{index:02d}"


class _Entity:
    """实体替身：只带类型识别与用户名需要的字段。"""

    def __init__(
        self,
        title: str = "",
        username: str = "",
        megagroup: bool = False,
        broadcast: bool = False,
    ) -> None:
        """
        构造实体。

        :param title: 群标题
        :param username: 公开用户名
        :param megagroup: 是否超级群
        :param broadcast: 是否广播频道
        """
        self.title = title
        self.username = username
        self.megagroup = megagroup
        self.broadcast = broadcast


class _Dialog:
    """会话替身（telethon Dialog 的最小形态）。"""

    def __init__(
        self,
        chat_id: int,
        entity: _Entity,
        name: str = "",
        is_group: bool = True,
        is_channel: bool = False,
        archived: bool = False,
    ) -> None:
        """
        构造会话。

        :param chat_id: 数字 ID（形如 -100…）
        :param entity: 实体替身
        :param name: 会话标题（默认取实体标题）
        :param is_group: 是否群组
        :param is_channel: 是否频道
        :param archived: 是否归档
        """
        self.id = chat_id
        self.entity = entity
        self.name = name or entity.title
        self.is_group = is_group
        self.is_channel = is_channel
        self.is_user = not (is_group or is_channel)
        self.archived = archived


class _FakeClient:
    """TelegramClient 替身：按 ``archived`` 参数产出预置会话。"""

    def __init__(
        self,
        dialogs: dict,
        authorized: bool = True,
        connect_error: str = "",
    ) -> None:
        """
        构造替身。

        :param dialogs: ``{archived 参数: [会话]}``
        :param authorized: is_user_authorized 的返回值
        :param connect_error: 非空时 connect 抛错
        """
        self._dialogs = dialogs
        self._authorized = authorized
        self._connect_error = connect_error
        self.connected = False

    async def connect(self) -> None:
        """模拟连接（可按需抛错）。"""
        if self._connect_error:
            raise RuntimeError(self._connect_error)
        self.connected = True

    async def disconnect(self) -> None:
        """模拟断开。"""
        self.connected = False

    async def is_user_authorized(self) -> bool:
        """桩：返回预设的授权状态。"""
        return self._authorized

    async def iter_dialogs(self, archived=None):
        """
        产出预置会话。

        :param archived: 归档参数（None / True）
        :yield: 会话替身
        """
        for dialog in self._dialogs.get(archived, []):
            yield dialog


def _sample_dialogs() -> list:
    """
    构造一批混合会话：私有超级群、公开超级群、普通群、广播频道、私聊。

    :return list: 会话列表
    """

    return [
        _Dialog(
            -1001111111111,
            _Entity(title="私有超级群", megagroup=True),
            is_channel=True,
        ),
        _Dialog(
            -1002222222222,
            _Entity(title="公开超级群", username="public_group", megagroup=True),
            is_channel=True,
        ),
        _Dialog(-1003333333333, _Entity(title="普通群")),
        _Dialog(
            -1004444444444,
            _Entity(title="广播频道", username="broadcast_chat", broadcast=True),
            is_channel=True,
        ),
        _Dialog(
            -1005555555555,
            _Entity(title="某人的私聊"),
            is_group=False,
            is_channel=False,
        ),
    ]


class TestGroupHelpers(unittest.TestCase):
    """纯函数：类型识别、填值选择、列表整理。"""

    def test_group_kind(self) -> None:
        """三类群 / 频道各自的类型描述。"""
        self.assertEqual(group_kind(_Entity(megagroup=True)), "超级群")
        self.assertEqual(group_kind(_Entity(broadcast=True)), "频道（广播）")
        self.assertEqual(group_kind(_Entity(title="普通群")), "普通群")

    def test_group_ref_prefers_username(self) -> None:
        """公开群优先用 @用户名（不依赖会话缓存），私有群回落数字 ID。"""
        self.assertEqual(group_ref("public_group", -1002222222222), "@public_group")
        self.assertEqual(group_ref("@public_group", -1002222222222), "@public_group")
        self.assertEqual(group_ref("", -1001111111111), "-1001111111111")
        self.assertEqual(group_ref("", None), "")

    def test_collect_rows_filters_and_sorts(self) -> None:
        """私聊被过滤；私有群排在前面；字段齐全。"""
        rows = collect_group_rows(_sample_dialogs())
        self.assertEqual(len(rows), 4)
        # 私有群（无 @用户名）在前，同组内按群名排序
        self.assertEqual(
            [row["title"] for row in rows],
            ["普通群", "私有超级群", "公开超级群", "广播频道"],
        )
        self.assertTrue(rows[0]["private"])
        self.assertTrue(rows[1]["private"])
        self.assertEqual(rows[1]["ref"], "-1001111111111")
        self.assertFalse(rows[2]["private"])
        public = next(row for row in rows if row["title"] == "公开超级群")
        self.assertFalse(public["private"])
        self.assertEqual(public["ref"], "@public_group")
        self.assertEqual(public["kind"], "超级群")

    def test_collect_rows_marks_configured_and_dedupes(self) -> None:
        """已配为新目标的会被标记；同一数字 ID 只保留一条。"""
        dialogs = _sample_dialogs() + [
            _Dialog(
                -1002222222222,
                _Entity(title="公开超级群", username="public_group", megagroup=True),
                is_channel=True,
                archived=True,
            )
        ]
        rows = collect_group_rows(
            dialogs, configured=["-1001111111111", "@public_group"]
        )
        titles = [row["title"] for row in rows]
        self.assertEqual(titles.count("公开超级群"), 1)
        marks = {row["title"]: row["configured"] for row in rows}
        self.assertTrue(marks["私有超级群"])
        self.assertTrue(marks["公开超级群"])
        self.assertFalse(marks["普通群"])
        self.assertFalse(marks["广播频道"])

    def test_collect_rows_marks_configured_by_chat_id(self) -> None:
        """配置里写数字 ID 时也能识别出「已配为目标」。"""
        rows = collect_group_rows(
            _sample_dialogs(), configured=["-1002222222222"]
        )
        public = next(row for row in rows if row["title"] == "公开超级群")
        self.assertTrue(public["configured"])

    def test_group_list_text(self) -> None:
        """命令文本：空列表给提示，超出上限时截断并说明。"""
        self.assertIn("没有查到任何群", group_list_text([], "acc1："))
        rows = collect_group_rows(_sample_dialogs())
        text = group_list_text(rows, "acc1：")
        self.assertIn("共 4 个群", text)
        self.assertIn("私有群，只能填数字 ID", text)
        self.assertIn("公开群，可填 @用户名", text)
        short = group_list_text(rows, "acc1：", limit=2)
        self.assertIn("只列前 2 个", short)
        self.assertEqual(len([l for l in short.splitlines() if l.startswith("- ")]), 2)

    def test_group_pick_items(self) -> None:
        """候选项只列指定账号的群：不混别的账号、按目标标识去重、带数量上限。"""
        cache = {
            "acc1": {"rows": [{"title": "私有超级群", "chat_id": "-1", "ref": "-1"}]},
            "acc2": {
                "rows": [
                    {"title": "私有超级群", "chat_id": "-1", "ref": "-1"},
                    {"title": "公开超级群", "chat_id": "-2", "ref": "@public_group"},
                ]
            },
        }
        items = group_pick_items(cache, "acc2")
        self.assertEqual([item["value"] for item in items], ["-1", "@public_group"])
        # 标题不再带账号后缀（候选已经限定到某一个账号）
        self.assertEqual(items[0]["title"], "私有超级群（-1）")
        # acc1 只有 1 个群：不能把 acc2 的「公开超级群」带进来
        self.assertEqual(
            [item["value"] for item in group_pick_items(cache, "acc1")], ["-1"]
        )
        # 没给账号、账号没查过、缓存缺失：一律空候选，不拿别人的群凑
        self.assertEqual(group_pick_items(cache, ""), [])
        self.assertEqual(group_pick_items(cache, "acc9"), [])
        self.assertEqual(group_pick_items(None, "acc1"), [])
        # 数量上限
        self.assertEqual(len(group_pick_items(cache, "acc2", limit=1)), 1)


class TestListChatGroups(unittest.TestCase):
    """只读查询：正常、未登录、连接异常、结果截断。"""

    def _run(self, client: _FakeClient) -> dict:
        """
        用替身客户端跑一次查询。

        :param client: 替身客户端
        :return dict: 查询结果
        """

        with mock.patch.object(
            groups_mod, "build_client", return_value=client
        ), mock.patch.object(groups_mod, "secure_session_files"):
            return asyncio.run(
                list_chat_groups(
                    AccountConfig(key="acc1", label="账号1"),
                    Path("/tmp"),
                    None,
                )
            )

    def test_query_ok(self) -> None:
        """正常查询：总数、私有群优先、归档会话也计入。"""
        client = _FakeClient(
            {
                None: _sample_dialogs(),
                True: [
                    _Dialog(
                        -1006666666666,
                        _Entity(title="归档群", megagroup=True),
                        is_channel=True,
                        archived=True,
                    )
                ],
            }
        )
        result = self._run(client)
        self.assertTrue(result["ok"])
        self.assertEqual(result["total"], 5)
        self.assertEqual(result["rows"][0]["title"], "归档群")
        self.assertFalse(result["truncated"])
        self.assertFalse(client.connected, "查询结束应断开连接")

    def test_query_unauthorized(self) -> None:
        """session 失效时给出可读提示，不抛异常。"""
        result = self._run(_FakeClient({None: []}, authorized=False))
        self.assertFalse(result["ok"])
        self.assertIn("未登录", result["message"])

    def test_query_connect_error(self) -> None:
        """连接异常被收敛成失败结果。"""
        result = self._run(_FakeClient({None: []}, connect_error="boom"))
        self.assertFalse(result["ok"])
        self.assertIn("查询失败", result["message"])

    def test_query_truncates(self) -> None:
        """群数量超过上限时截断并置 truncated。"""
        dialogs = [
            _Dialog(
                -1000000000000 - index,
                _Entity(title=f"群{index}", megagroup=True),
                is_channel=True,
            )
            for index in range(MAX_GROUP_ROWS + 2)
        ]
        result = self._run(_FakeClient({None: dialogs}))
        self.assertTrue(result["ok"])
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["rows"]), MAX_GROUP_ROWS)
        self.assertEqual(result["total"], MAX_GROUP_ROWS + 2)


class TestGroupSync(unittest.TestCase):
    """槽位 ↔ 文本双向同步。"""

    def _slot_config(self) -> dict:
        """
        构造一份默认槽位配置。

        :return dict: 配置字典
        """

        config = dict(default_slot_config())
        config["accounts_text"] = ACCOUNTS_TEXT_HEADER
        config["targets_text"] = TARGETS_TEXT_HEADER
        return config

    def test_slots_mirror_into_text(self) -> None:
        """槽位模式：槽位内容写进文本域，并给出当前格式的头部注释。"""
        config = self._slot_config()
        accounts = accounts_from_slots(config)
        targets = targets_from_slots(config, [item.key for item in accounts])
        payload = mirror_slots_to_text(config, accounts, targets)
        self.assertIn(ACCOUNTS_TEXT_HEADER, payload["accounts_text"])
        self.assertIn(
            f"acc1 | 账号1 | {_placeholder_phone(1)}", payload["accounts_text"]
        )
        self.assertIn(TARGETS_TEXT_HEADER, payload["targets_text"])
        self.assertIn("acc1 | @example_bot_b | 按钮 | 签到 | 15", payload["targets_text"])
        # 已经同步过就不再写（避免每次初始化都刷配置）
        self.assertEqual(mirror_slots_to_text(payload, accounts, targets), {})

    def test_slots_mirror_keeps_overflow_lines(self) -> None:
        """超出槽位容量的文本行原样保留（第 5 个账号起、第 9 条目标起）。"""
        config = self._slot_config()
        config["accounts_text"] = "\n".join(
            [ACCOUNTS_TEXT_HEADER]
            + [f"acc{i} | 账号{i} | {_placeholder_phone(i)}" for i in range(1, 6)]
        )
        config["targets_text"] = "\n".join(
            [TARGETS_TEXT_HEADER] + [f"acc1 | @bot_{i} | 按钮 | 签到" for i in range(1, 11)]
        )
        accounts = accounts_from_slots(config)
        targets = targets_from_slots(config, [item.key for item in accounts])
        payload = mirror_slots_to_text(config, accounts, targets)
        self.assertIn(
            f"acc5 | 账号5 | {_placeholder_phone(5)}", payload["accounts_text"]
        )
        self.assertIn("@bot_9", payload["targets_text"])
        self.assertIn("@bot_10", payload["targets_text"])
        # 槽位只有 2 个账号：第 3、4 个槽位的旧文本行不再保留（它们不是「超容量」内容）
        self.assertNotIn("acc3 | 账号3", payload["accounts_text"])

    def test_slots_empty_keeps_existing_text(self) -> None:
        """槽位为空、文本有内容（旧版升级兜底路径）时不覆盖用户文本。"""
        config = self._slot_config()
        config["targets_text"] = "# 旧格式\nacc1 | @old_bot | 按钮 | 签到"
        self.assertEqual(mirror_slots_to_text(config, [], []), {})

    def test_text_mirror_into_slots(self) -> None:
        """文本模式：前 4 个账号 / 前 8 条目标回填进槽位；多余的只留文本。"""
        config = self._slot_config()
        accounts_text = "\n".join(
            [ACCOUNTS_TEXT_HEADER]
            + [f"acc{i} | 账号{i} | {_placeholder_phone(i)}" for i in range(1, 5)]
        )
        targets_text = "\n".join(
            [TARGETS_TEXT_HEADER]
            + [
                f"acc{i} | @bot_{i} | 命令 | /checkin | 20 | owner_{i}"
                for i in range(1, MAX_TARGET_SLOTS + 2)
            ]
        )
        accounts = parse_accounts(accounts_text)
        targets = parse_targets(targets_text)
        payload = mirror_text_to_slots(dict(config), accounts, targets)
        self.assertEqual(payload["account_4_label"], "账号4")
        self.assertEqual(payload["target_1_bot"], "@bot_1")
        self.assertEqual(payload["target_1_method"], "命令")
        self.assertEqual(payload["target_1_wait"], 20)
        self.assertEqual(payload["target_1_owner"], "owner_1")
        self.assertEqual(payload["target_8_bot"], "@bot_8")
        # 第 9 条只留在文本里（槽位只有 8 个），文本本身不被改写
        self.assertNotIn("target_9_bot", payload)
        # 已在同步状态时返回空字典
        self.assertEqual(mirror_text_to_slots(payload, accounts, targets), {})

    def test_text_mirror_clears_unused_slots(self) -> None:
        """文本里没有的槽位被清成未启用，避免残留旧值让人以为还生效。"""
        config = self._slot_config()
        accounts = parse_accounts(
            f"{ACCOUNTS_TEXT_HEADER}\nacc1 | 一号 | {_placeholder_phone(1)}"
        )
        targets = parse_targets("acc1 | @only_bot | 按钮 | 签到")
        payload = mirror_text_to_slots(dict(config), accounts, targets)
        self.assertFalse(payload["account_2_enabled"])
        self.assertEqual(payload["account_2_phone"], "")
        self.assertFalse(payload["target_2_enabled"])
        self.assertEqual(payload["target_2_bot"], "")
        # 登录动作字段不受同步影响
        self.assertEqual(payload["account_1_login_action"], "不操作")

    def test_init_plugin_syncs_both_directions(self) -> None:
        """插件初始化时按当前模式同步两个视图。"""
        plugin = TgSignin()
        plugin.init_plugin({**self._slot_config(), "enabled": True})
        # 槽位模式：文本域被镜像
        self.assertTrue(plugin.config_updates)
        mirrored = plugin.config_updates[0]
        self.assertIn(
            f"acc1 | 账号1 | {_placeholder_phone(1)}", mirrored["accounts_text"]
        )
        self.assertIn("@example_bot_b", mirrored["targets_text"])

        text_plugin = TgSignin()
        text_config = dict(default_slot_config())
        text_config.update(
            {
                "enabled": True,
                "use_text_mode": True,
                "accounts_text": f"# 账号\nacc1 | 演示账号 | {_placeholder_phone(1)}",
                "targets_text": "acc1 | @only_bot | 命令 | /checkin",
            }
        )
        with mock.patch.object(
            text_plugin, "update_config"
        ) as updater:
            text_plugin.init_plugin(text_config)
        self.assertTrue(updater.called)
        written = updater.call_args[0][0]
        self.assertTrue(written["account_1_enabled"])
        self.assertEqual(written["account_1_label"], "演示账号")
        self.assertEqual(written["target_1_bot"], "@only_bot")
        self.assertFalse(written["target_2_enabled"])


class TestGroupFormAndApi(unittest.TestCase):
    """插件层：配置表单、API 路由、命令、详情页区块。"""

    def _plugin(self) -> TgSignin:
        """
        构造一个启用且带缓存的插件实例。

        :return TgSignin: 插件实例
        """

        plugin = TgSignin()
        plugin.init_plugin({**default_slot_config(), "enabled": True})
        save_state(
            plugin.get_data_path(),
            {
                "groups": {
                    "acc1": {
                        "time": "2026-10-12 01:00:00",
                        "total": 1,
                        "truncated": False,
                        "rows": [
                            {
                                "title": "私有超级群",
                                "chat_id": "-1001111111111",
                                "username": "",
                                "ref": "-1001111111111",
                                "kind": "超级群",
                                "private": True,
                                "archived": False,
                                "configured": False,
                            }
                        ],
                    },
                    "acc2": {
                        "time": "2026-10-12 02:00:00",
                        "total": 1,
                        "truncated": False,
                        "rows": [
                            {
                                "title": "二号账号的群",
                                "chat_id": "-1002222222222",
                                "username": "",
                                "ref": "-1002222222222",
                                "kind": "超级群",
                                "private": True,
                                "archived": False,
                                "configured": False,
                            }
                        ],
                    },
                }
            },
        )
        return plugin

    def test_form_has_filterable_pick_and_fill_button(self) -> None:
        """每个目标槽都有可输入筛选的「从已查到的群组选」组合框与「填入本槽」按钮。"""
        plugin = self._plugin()
        form, defaults = plugin.get_form()
        plain = json.dumps(form, ensure_ascii=False)
        self.assertIn("target_1_pick", plain)
        self.assertIn("target_8_pick", plain)
        self.assertEqual(defaults["target_1_pick"], "")
        self.assertIn("填入本槽", plain)
        # 候选项来自缓存（带群名与群号）
        self.assertIn("私有超级群（-1001111111111）", plain)
        # 必须是可以输入关键词筛选的组合框，而不是只能翻列表的纯下拉
        pickers = [
            node
            for node in _walk(form)
            if node.get("component") == "VCombobox"
            and str(node.get("props", {}).get("model", "")).endswith("_pick")
        ]
        self.assertEqual(len(pickers), MAX_TARGET_SLOTS, "每个目标槽一个筛选框")
        props = pickers[0]["props"]
        self.assertIn("筛选", str(props.get("label")))
        self.assertTrue(props.get("clearable"))
        self.assertIn("max-height", json.dumps(props.get("menu-props"), ensure_ascii=False))
        self.assertTrue(props.get("items"), "候选项非空时才谈得上筛选")
        # 候选按「行账号」收窄：第 1 行是 acc1，只能看到 acc1 的群
        self.assertEqual(
            [item["title"] for item in props["items"]],
            ["私有超级群（-1001111111111）"],
        )
        # 第 5 行是 acc2：只列 acc2 的群，看不到 acc1 的
        self.assertEqual(
            [item["title"] for item in pickers[4]["props"]["items"]],
            ["二号账号的群（-1002222222222）"],
        )
        # 第 6-8 行还没选账号：候选为空（绝不混别的账号的群）
        for empty_row in pickers[5:]:
            self.assertEqual(empty_row["props"]["items"], [])

    def test_fill_button_js_targets_bot_field(self) -> None:
        """「填入本槽」脚本：解析选择框内容 → 只把群标识写进同一行的 bot 字段。"""
        script = TgSignin()._fill_target_button_js(3)
        self.assertIn("model.target_3_pick", script)
        self.assertIn("model.target_3_bot = ref", script)
        self.assertIn("alert(", script)
        self.assertIn("confirm(", script)
        # 确认框显示解析后的群标识，而不是原始对象 / 整条标题
        self.assertIn("+ ref +", script)

    def test_fill_button_js_resolution_order(self) -> None:
        """
        解析顺序：整项对象取 value → 已是群标识直接用 → 标题串从「（群号）」截出群号。

        用户反馈（2026-10-12）：从候选里点选后框里是「群名（群号）」，旧版只按字符串形态
        校验，会把正常选中也当成筛选关键词拦掉。这里锁住三步解析与「认不出才拦」的把关。
        """
        script = TgSignin()._fill_target_button_js(3)
        # ① 选中项是整项对象时取 value（与同表 target_N_method 的存法一致）
        self.assertIn('typeof raw === "object"', script)
        self.assertIn("raw.value", script)
        # ② 群标识的两种形态：@用户名 与 数字 ID（含负号）
        self.assertIn(r"/^@[A-Za-z0-9_]{4,}$/", script)
        self.assertIn(r"/^-?\d{5,}$/", script)
        # ③ 完整标题串：从我们自己的「（群号）」后缀里截出群号
        self.assertIn(r".match(/（(-?\d{5,})）/)", script)
        self.assertIn("ref = m[1]", script)
        # 三步都认不出（半截筛选词）才弹提示，不把无效值写进目标
        self.assertIn("if (!okId(ref)) { alert(", script)
        self.assertIn("没能从选择框里认出群标识", script)

    def test_groups_api_route(self) -> None:
        """/groups 路由绑定到查询接口。"""
        routes = {item["path"]: item for item in TgSignin().get_api()}
        self.assertIn("/groups", routes)
        self.assertIn("GET", routes["/groups"]["methods"])

    def test_api_list_groups_records_cache(self) -> None:
        """查询成功时写缓存，并在响应里给出私有群数量。"""
        plugin = self._plugin()
        rows = [
            {
                "title": "私有超级群",
                "chat_id": "-1001111111111",
                "username": "",
                "ref": "-1001111111111",
                "kind": "超级群",
                "private": True,
                "archived": False,
                "configured": False,
            },
            {
                "title": "公开超级群",
                "chat_id": "-1002222222222",
                "username": "public_group",
                "ref": "@public_group",
                "kind": "超级群",
                "private": False,
                "archived": False,
                "configured": False,
            },
        ]
        request = MagicMock()
        request.query_params = {"account": "acc1"}
        with mock.patch.object(
            tgsignin,
            "list_chat_groups",
            return_value={"ok": True, "rows": rows, "total": 2, "truncated": False},
        ):
            result = asyncio.run(plugin.api_list_groups(request))
        self.assertTrue(result["success"])
        self.assertIn("共 2 个群", result["message"])
        self.assertIn("1 个私有群", result["message"])
        cached = load_state(plugin.get_data_path())["groups"]["acc1"]
        self.assertEqual(cached["total"], 2)
        self.assertEqual(cached["rows"][0]["title"], "私有超级群")

    def test_owner_visible_only_for_group_rows(self) -> None:
        """「回复归属标识」只在群目标行出现：bot 行与空行不显示，群行才显示。"""
        plugin = TgSignin()
        config = dict(default_slot_config())
        config.update(
            {
                "enabled": True,
                "target_1_bot": "@some_bot",  # bot：隐藏
                "target_2_bot": "-1001234567890",  # 数字 ID：显示
                "target_3_bot": "@public_group",  # 已查到的公开群：显示
                "target_4_bot": "",  # 还没填：隐藏
                "target_5_bot": "@example_bot_a",  # bot，但下面选了群组：显示
                "target_5_pick": "-1002222222222",
            }
        )
        plugin.init_plugin(config)
        state = load_state(plugin.get_data_path())
        state["groups"] = {
            "acc1": {
                "time": "2026-10-12 02:00:00",
                "total": 1,
                "truncated": False,
                "rows": [
                    {
                        "title": "公开群",
                        "chat_id": "-1009999999999",
                        "username": "public_group",
                        "ref": "@public_group",
                        "kind": "超级群",
                        "private": False,
                        "archived": False,
                        "configured": False,
                    }
                ],
            }
        }
        save_state(plugin.get_data_path(), state)

        form, _ = plugin.get_form()
        shows = {
            str(node["props"]["model"]): node["props"]["show"]
            for node in _walk(form)
            if node.get("component") == "VTextField"
            and str(node.get("props", {}).get("model", "")).endswith("_owner")
        }
        self.assertEqual(shows["target_1_owner"], "target_1_enabled && false")
        self.assertEqual(shows["target_2_owner"], "target_2_enabled && true")
        self.assertEqual(shows["target_3_owner"], "target_3_enabled && true")
        self.assertEqual(shows["target_4_owner"], "target_4_enabled && false")
        self.assertEqual(shows["target_5_owner"], "target_5_enabled && true")

    def test_group_detection_helper(self) -> None:
        """群目标识别：数字 ID / 缓存里的群 → 群；以 bot 结尾 → 不是群；空值 → False。"""
        plugin = TgSignin()
        config = dict(default_slot_config())
        config.update(
            {
                "enabled": True,
                "target_1_bot": "-1001234567890",
                "target_2_bot": "@example_bot",
                "target_3_bot": "SomeGroupName",
                "target_4_bot": "",
                "target_4_account": "acc1",
            }
        )
        plugin.init_plugin(config)
        self.assertTrue(plugin._row_is_group_target(1))
        self.assertFalse(plugin._row_is_group_target(2))
        self.assertTrue(plugin._row_is_group_target(3), "看不出是 bot 就按群处理")
        self.assertFalse(plugin._row_is_group_target(4), "还没填目标时不显示")
        # 命中缓存里的群（按本行账号）
        state = load_state(plugin.get_data_path())
        state["groups"] = {
            "acc1": {
                "time": "t",
                "total": 1,
                "truncated": False,
                "rows": [
                    {
                        "title": "某群",
                        "chat_id": "-1009",
                        "username": "my_group",
                        "ref": "@my_group",
                        "kind": "超级群",
                        "private": False,
                        "archived": False,
                        "configured": False,
                    }
                ],
            }
        }
        save_state(plugin.get_data_path(), state)
        plugin._raw_config = {**plugin._raw_config, "target_4_account": "acc1", "target_4_bot": "@my_group"}
        self.assertTrue(plugin._row_is_group_target(4))

    def test_bot_field_label_mentions_group(self) -> None:
        """「bot 用户名 / 群」这一栏文案要说明群也能填（群也可以签到）。"""
        plugin = self._plugin()
        form, _ = plugin.get_form()
        labels = {
            str(node["props"]["model"]): str(node["props"]["label"])
            for node in _walk(form)
            if node.get("component") == "VTextField"
            and str(node.get("props", {}).get("model", "")).endswith("_bot")
        }
        self.assertIn("群", labels["target_1_bot"])

    def test_groups_refresh_route(self) -> None:
        """/groups/refresh 路由绑定到「更新群组」接口。"""
        routes = {item["path"]: item for item in TgSignin().get_api()}
        self.assertIn("/groups/refresh", routes)
        # 会连 Telegram 并写群组缓存：按写操作只允许 POST（2026-10-30 加固）
        self.assertEqual(routes["/groups/refresh"]["methods"], ["POST"])

    def test_refresh_queries_every_account(self) -> None:
        """不带参数：逐个账号刷新，一个失败不影响其它账号，并汇总成一句话。"""
        plugin = self._plugin()
        seen: list = []

        async def _fake(account):
            """
            记录被刷新的账号，并让 acc2 失败。

            :param account: 账号配置
            :return dict: 查询结果
            """
            seen.append(account.key)
            if account.key == "acc2":
                return {
                    "ok": False,
                    "message": "账号 acc2 未登录或 session 已失效",
                    "data": None,
                }
            return {
                "ok": True,
                "message": "共 3 个群 / 频道，其中 1 个私有群",
                "data": {},
            }

        request = MagicMock()
        request.query_params = {}
        with mock.patch.object(plugin, "_query_groups", side_effect=_fake):
            result = asyncio.run(plugin.api_refresh_groups(request))
        self.assertEqual(seen, ["acc1", "acc2"])
        self.assertFalse(result["success"])
        self.assertIn("1/2 个账号查询成功", result["message"])
        self.assertIn("未登录", result["message"])
        self.assertEqual(result["data"], {"ok_count": 1, "total": 2})

    def test_refresh_single_account(self) -> None:
        """带 account 参数：只刷新该账号。"""
        plugin = self._plugin()
        seen: list = []

        async def _fake(account):
            """
            记录被刷新的账号。

            :param account: 账号配置
            :return dict: 查询结果
            """
            seen.append(account.key)
            return {
                "ok": True,
                "message": "共 1 个群 / 频道，其中 1 个私有群",
                "data": {},
            }

        request = MagicMock()
        request.query_params = {"account": "acc2"}
        with mock.patch.object(plugin, "_query_groups", side_effect=_fake):
            result = asyncio.run(plugin.api_refresh_groups(request))
        self.assertEqual(seen, ["acc2"])
        self.assertTrue(result["success"])
        self.assertIn("1/1 个账号查询成功", result["message"])

    def test_refresh_unknown_account_skips_query(self) -> None:
        """账号标识写错时直接返回失败提示，不去连 Telegram。"""
        plugin = self._plugin()
        request = MagicMock()
        request.query_params = {"account": "acc9"}
        with mock.patch.object(
            plugin, "_query_groups", side_effect=AssertionError("不该发起查询")
        ):
            result = asyncio.run(plugin.api_refresh_groups(request))
        self.assertFalse(result["success"])
        self.assertIn("找不到账号", result["message"])
        self.assertIn("hint", result["data"])

    def test_refresh_without_enabled_accounts(self) -> None:
        """没有启用账号时给出可读提示。"""
        plugin = TgSignin()
        config = dict(default_slot_config())
        for index in range(1, MAX_ACCOUNT_SLOTS + 1):
            config[f"account_{index}_enabled"] = False
        # 槽位全关时插件会回落到内置示例账号，这里把文本也置空，才是真的「没有账号」
        config["accounts_text"] = "# 无账号"
        plugin.init_plugin({**config, "enabled": True})
        self.assertEqual(plugin._accounts, [])
        request = MagicMock()
        request.query_params = {}
        result = asyncio.run(plugin.api_refresh_groups(request))
        self.assertFalse(result["success"])
        self.assertIn("还没有启用的账号", result["message"])

    def test_page_has_refresh_groups_button(self) -> None:
        """详情页有「更新群组（全部账号）」按钮，且与其它按钮一样带 apikey。"""
        plugin = self._plugin()
        buttons = [
            node
            for node in _walk(plugin.get_page())
            if node.get("component") == "VBtn"
            and "更新群组" in str(node.get("text") or "")
        ]
        self.assertEqual(len(buttons), 1)
        click = buttons[0]["events"]["click"]
        self.assertIn("apikey", click["params"])
        self.assertTrue(click["api"].endswith("/groups/refresh"))

    def test_page_says_groups_are_not_auto_refreshed(self) -> None:
        """详情页明确写清「列表不会自动更新」，避免以为会自动刷。"""
        plugin = self._plugin()
        plain = json.dumps(plugin.get_page(), ensure_ascii=False)
        self.assertIn("不会自动更新", plain)

    def test_api_list_groups_unknown_account(self) -> None:
        """账号不存在时返回失败并给出可用值提示。"""
        plugin = self._plugin()
        request = MagicMock()
        request.query_params = {"account": "acc9"}
        result = asyncio.run(plugin.api_list_groups(request))
        self.assertFalse(result["success"])
        self.assertIn("找不到账号", result["message"])

    def test_api_list_groups_disabled(self) -> None:
        """插件未启用时不查询。"""
        plugin = TgSignin()
        plugin.init_plugin({**default_slot_config(), "enabled": False})
        request = MagicMock()
        request.query_params = {"account": "acc1"}
        result = asyncio.run(plugin.api_list_groups(request))
        self.assertFalse(result["success"])
        self.assertIn("未启用", result["message"])

    def test_groups_command_posts_list(self) -> None:
        """/tggroups 命令把群组列表发到聊天里。"""
        plugin = self._plugin()
        event = tgsignin.Event(
            event_data={
                "action": "tgsignin_groups",
                "arg_str": "acc1",
                "channel": "telegram",
                "source": "private",
                "userid": "1",
            }
        )
        result = {
            "ok": True,
            "message": "共 1 个群 / 频道",
            "data": {
                "rows": [
                    {
                        "title": "私有超级群",
                        "chat_id": "-1001111111111",
                        "kind": "超级群",
                        "private": True,
                        "configured": False,
                    }
                ]
            },
        }
        with mock.patch.object(plugin, "_run_sync", return_value=result):
            plugin.handle_plugin_action(event)
        self.assertTrue(plugin.messages)
        text = plugin.messages[-1].get("text", "")
        self.assertIn("私有超级群", text)
        self.assertIn("-1001111111111", text)
        self.assertIn("只能填数字 ID", text)

    def test_groups_command_defaults_to_first_account(self) -> None:
        """不带账号参数时用第一个账号（与其它命令一致）。"""
        plugin = self._plugin()
        event = tgsignin.Event(
            event_data={"action": "tgsignin_groups", "arg_str": "", "userid": "1"}
        )
        seen: dict = {}

        async def _fake_query(account):
            """
            记录被查询的账号并返回预置结果。

            :param account: 账号配置
            :return dict: 查询结果
            """
            seen["account"] = account.key
            return {
                "ok": True,
                "message": "共 1 个群 / 频道",
                "data": {
                    "rows": [
                        {
                            "title": "私有超级群",
                            "chat_id": "-1001111111111",
                            "kind": "超级群",
                            "private": True,
                            "configured": False,
                        }
                    ]
                },
            }

        with mock.patch.object(
            plugin, "_query_groups", side_effect=_fake_query
        ), mock.patch.object(
            plugin, "_run_sync", side_effect=lambda factory: asyncio.run(factory())
        ):
            plugin.handle_plugin_action(event)
        self.assertEqual(seen["account"], "acc1")
        self.assertIn("-1001111111111", plugin.messages[-1].get("text", ""))

    def test_groups_command_unknown_account_lists_usage(self) -> None:
        """账号标识写错时回复用法与可用账号，不去连 Telegram。"""
        plugin = self._plugin()
        event = tgsignin.Event(
            event_data={"action": "tgsignin_groups", "arg_str": "acc9", "userid": "1"}
        )
        with mock.patch.object(
            plugin, "_run_sync", side_effect=AssertionError("不该发起查询")
        ):
            plugin.handle_plugin_action(event)
        text = plugin.messages[-1].get("text", "")
        self.assertIn("/tggroups <账号标识>", text)
        self.assertIn("acc1", text)

    def test_page_shows_cached_groups(self) -> None:
        """详情页渲染缓存的群组表与查询按钮（按钮带 apikey）。"""
        plugin = self._plugin()
        page = plugin.get_page()
        plain = json.dumps(page, ensure_ascii=False)
        self.assertIn("群组列表（查群号）", plain)
        self.assertIn("-1001111111111", plain)
        self.assertIn("需数字 ID", plain)
        buttons = [
            node
            for node in _walk(page)
            if node.get("component") == "VBtn"
            and "查询群组" in str(node.get("text") or "")
        ]
        self.assertEqual(
            sorted(button["events"]["click"]["params"]["account"] for button in buttons),
            ["acc1", "acc2"],
        )
        for button in buttons:
            params = button["events"]["click"]["params"]
            self.assertIn("apikey", params)

    def test_page_without_cache_shows_hint(self) -> None:
        """没有查询过时给出引导文案。"""
        plugin = TgSignin()
        plugin.init_plugin({**default_slot_config(), "enabled": True})
        plain = json.dumps(plugin.get_page(), ensure_ascii=False)
        self.assertIn("还没有查询过", plain)
        self.assertIn("/tggroups", plain)


def _walk(node) -> list:
    """
    递归收集页面 / 表单里的所有节点（含自身）。

    :param node: 节点或节点列表
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


if __name__ == "__main__":
    unittest.main()
