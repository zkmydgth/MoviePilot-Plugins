"""
账号与签到目标的文本解析、回显与校验测试。

覆盖：默认值解析、注释与空行容错、全角分隔符、标识规范化与去重、
方式别名词（按钮/命令）、等待秒数回落、逐行引用校验。
"""

import unittest

import tests  # noqa: F401  触发宿主桩与插件路径注入

from tgsignin.core.config import (
    DEFAULT_ACCOUNTS_TEXT,
    DEFAULT_API_HASH,
    DEFAULT_API_ID,
    DEFAULT_TARGETS_TEXT,
    LOGIN_ACTION_CONFIRM,
    LOGIN_ACTION_NONE,
    LOGIN_ACTION_SEND,
    SIGN_TYPE_BUTTON,
    SIGN_TYPE_COMMAND,
    account_login_fields,
    accounts_from_slots,
    accounts_to_text,
    coerce_scalar,
    default_slot_config,
    login_actions,
    normalize_key,
    parse_accounts,
    parse_targets,
    targets_from_slots,
    targets_to_text,
    validate_config,
)


class TestNormalizeKey(unittest.TestCase):
    """账号标识规范化。"""

    def test_lowercases_and_strips_illegal_chars(self) -> None:
        """大写与非法字符应被压成合法形态。"""
        self.assertEqual(normalize_key("Acc 1!"), "acc1")

    def test_keeps_dash_and_underscore(self) -> None:
        """短横线与下划线保留。"""
        self.assertEqual(normalize_key("my_acc-2"), "my_acc-2")

    def test_empty_input(self) -> None:
        """空输入返回空串。"""
        self.assertEqual(normalize_key("   "), "")


class TestCoerceScalar(unittest.TestCase):
    """表单取值归一化：VCombobox 从下拉选中会写入整项对象。"""

    def test_plain_values(self) -> None:
        """标量原样返回，None 转空串，布尔转文本。"""
        self.assertEqual(coerce_scalar("all"), "all")
        self.assertEqual(coerce_scalar(" 按钮 "), "按钮")
        self.assertEqual(coerce_scalar(None), "")
        self.assertEqual(coerce_scalar(True), "true")
        self.assertEqual(coerce_scalar(15), "15")

    def test_dict_item_uses_value_then_title(self) -> None:
        """对象型取值优先取 value，其次 title。"""
        self.assertEqual(
            coerce_scalar({"title": "成功与失败都通知", "value": "all"}), "all"
        )
        self.assertEqual(coerce_scalar({"title": "未确认"}), "未确认")

    def test_nested_and_list(self) -> None:
        """嵌套对象与数组按同样规则取第一个可用值。"""
        self.assertEqual(coerce_scalar({"value": {"value": "x"}}), "x")
        self.assertEqual(coerce_scalar([{"value": "a"}, "b"]), "a")
        self.assertEqual(coerce_scalar([]), "")
        self.assertEqual(coerce_scalar({}), "")


class TestParseAccounts(unittest.TestCase):
    """账号列表解析。"""

    def test_defaults_text(self) -> None:
        """默认账号文本解析出两个账号并带上全局凭据。"""
        accounts = parse_accounts(DEFAULT_ACCOUNTS_TEXT)
        self.assertEqual([a.key for a in accounts], ["acc1", "acc2"])
        self.assertEqual(accounts[0].phone, "+12025550101")
        self.assertEqual(accounts[0].api_id, DEFAULT_API_ID)
        self.assertEqual(accounts[0].api_hash, DEFAULT_API_HASH)

    def test_ignores_comments_and_blank_lines(self) -> None:
        """注释行与空行被忽略。"""
        text = "# 注释\n\nacc1 | 一号 | +8613800138000\n"
        accounts = parse_accounts(text)
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0].label, "一号")

    def test_full_width_separator_and_dedup(self) -> None:
        """全角竖线可用，重复标识只保留第一条。"""
        text = "acc1｜一号｜+8613800138000\nacc1 | 重复 | +8613800138001"
        accounts = parse_accounts(text)
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0].label, "一号")

    def test_per_account_credentials(self) -> None:
        """行尾可以覆盖 api_id/api_hash。"""
        text = "acc1 | 一号 | +8613800138000 | 12345 | deadbeef"
        accounts = parse_accounts(text)
        self.assertEqual(accounts[0].api_id, 12345)
        self.assertEqual(accounts[0].api_hash, "deadbeef")

    def test_label_falls_back_to_key(self) -> None:
        """没写显示名时用标识兜底。"""
        accounts = parse_accounts("acc9")
        self.assertEqual(accounts[0].label, "acc9")


class TestAccountsRoundTrip(unittest.TestCase):
    """账号文本回显。"""

    def test_roundtrip_keeps_global_credentials_implicit(self) -> None:
        """使用全局凭据时不写出后两段，回显保持简洁。"""
        accounts = parse_accounts(DEFAULT_ACCOUNTS_TEXT)
        text = accounts_to_text(accounts)
        self.assertNotIn(str(DEFAULT_API_ID), text)
        self.assertEqual([a.key for a in parse_accounts(text)], ["acc1", "acc2"])

    def test_roundtrip_keeps_overridden_credentials(self) -> None:
        """覆盖过的凭据在回显里保留。"""
        accounts = parse_accounts("acc1 | 一号 | +8613800138000 | 12345 | deadbeef")
        text = accounts_to_text(accounts)
        self.assertIn("12345", text)
        self.assertIn("deadbeef", text)


class TestParseTargets(unittest.TestCase):
    """签到目标解析。"""

    def test_defaults_text(self) -> None:
        """默认目标文本给出 5 条，含 4 条按钮式与 1 条命令式。"""
        targets = parse_targets(DEFAULT_TARGETS_TEXT)
        self.assertEqual(len(targets), 5)
        buttons = [t for t in targets if t.sign_type == SIGN_TYPE_BUTTON]
        commands = [t for t in targets if t.sign_type == SIGN_TYPE_COMMAND]
        self.assertEqual(len(buttons), 4)
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].action_text, "/checkin")
        self.assertEqual(commands[0].bot_username, "@HDHaven_Bot")

    def test_bot_username_gets_at_prefix(self) -> None:
        """bot 用户名缺 @ 时自动补上。"""
        targets = parse_targets("acc1 | okemby_bot | 按钮 | 签到")
        self.assertEqual(targets[0].bot_username, "@okemby_bot")

    def test_command_alias_and_wait(self) -> None:
        """「命令」别名识别为命令式，等待秒数生效。"""
        targets = parse_targets("acc1 | @b | 命令 | /sign | 30")
        self.assertEqual(targets[0].sign_type, SIGN_TYPE_COMMAND)
        self.assertEqual(targets[0].wait_seconds, 30)

    def test_wait_defaults_and_clamped(self) -> None:
        """等待秒数缺失回落 15，非法值同样回落。"""
        self.assertEqual(parse_targets("acc1 | @b | 按钮 | 签到").pop().wait_seconds, 15)
        self.assertEqual(
            parse_targets("acc1 | @b | 按钮 | 签到 | abc").pop().wait_seconds, 15
        )

    def test_incomplete_line_is_skipped(self) -> None:
        """字段不足 4 段的行被跳过。"""
        self.assertEqual(parse_targets("acc1 | @b | 按钮"), [])

    def test_empty_action_gets_default(self) -> None:
        """第 4 段为空时按方式给默认值。"""
        button = parse_targets("acc1 | @b | 按钮 |").pop()
        command = parse_targets("acc1 | @b | 命令 |").pop()
        self.assertEqual(button.action_text, "签到")
        self.assertEqual(command.action_text, "/checkin")

    def test_roundtrip(self) -> None:
        """目标列表回显后可再次解析成等价结构。"""
        targets = parse_targets(DEFAULT_TARGETS_TEXT)
        again = parse_targets(targets_to_text(targets))
        self.assertEqual(len(again), len(targets))
        self.assertEqual(
            [(t.account_key, t.bot_username, t.sign_type, t.action_text) for t in again],
            [(t.account_key, t.bot_username, t.sign_type, t.action_text) for t in targets],
        )


class TestValidateConfig(unittest.TestCase):
    """配置校验。"""

    def test_defaults_are_valid(self) -> None:
        """默认账号 + 默认目标没有配置问题。"""
        problems = validate_config(
            parse_accounts(DEFAULT_ACCOUNTS_TEXT), parse_targets(DEFAULT_TARGETS_TEXT)
        )
        self.assertEqual(problems, [])

    def test_unknown_account_reference(self) -> None:
        """目标引用了不存在的账号要报出来。"""
        problems = validate_config(
            parse_accounts("acc1 | 一号 | +8613800138000"),
            parse_targets("acc2 | @b | 按钮 | 签到"),
        )
        self.assertTrue(any("不存在的账号" in item for item in problems))

    def test_missing_phone_reported(self) -> None:
        """账号没填手机号要报出来。"""
        problems = validate_config(parse_accounts("acc1 | 一号"), [])
        self.assertTrue(any("没填手机号" in item for item in problems))

    def test_empty_sides_reported(self) -> None:
        """两侧都空时各报一条。"""
        problems = validate_config([], [])
        self.assertEqual(len(problems), 2)


class TestSlotConfig(unittest.TestCase):
    """槽位式配置（配置页的可视化表单）解析。"""

    def test_default_slots(self) -> None:
        """默认槽位解析出 2 个账号与 5 条目标，且标识自动生成。"""
        config = default_slot_config()
        accounts = accounts_from_slots(config)
        self.assertEqual([a.key for a in accounts], ["acc1", "acc2"])
        self.assertEqual(accounts[0].label, "账号1")
        self.assertEqual(accounts[0].phone, "+12025550101")
        targets = targets_from_slots(config, [a.key for a in accounts])
        self.assertEqual(len(targets), 5)
        self.assertEqual(targets[3].sign_type, SIGN_TYPE_COMMAND)
        self.assertEqual(targets[3].action_text, "/checkin")
        self.assertEqual(validate_config(accounts, targets), [])

    def test_disabled_slot_is_ignored(self) -> None:
        """关掉的槽位不参与解析（账号与目标都是）。"""
        config = default_slot_config()
        config["account_2_enabled"] = False
        self.assertEqual([a.key for a in accounts_from_slots(config)], ["acc1"])
        config["target_1_enabled"] = False
        targets = targets_from_slots(config, ["acc1"])
        self.assertEqual(len(targets), 4)

    def test_label_falls_back_and_phone_stripped(self) -> None:
        """显示名缺失时用标识兜底，手机号去空白。"""
        config = {
            "account_1_enabled": True,
            "account_1_label": "",
            "account_1_phone": "  +8613800138000  ",
        }
        accounts = accounts_from_slots(config)
        self.assertEqual(accounts[0].label, "acc1")
        self.assertEqual(accounts[0].phone, "+8613800138000")

    def test_target_wait_default_and_invalid(self) -> None:
        """等待秒数缺失/非法都回落 15，超范围被夹到 1..120。"""
        base = {
            "target_1_enabled": True,
            "target_1_account": "acc1",
            "target_1_bot": "@b",
            "target_1_method": "按钮",
            "target_1_action": "签到",
        }
        self.assertEqual(targets_from_slots(base, []).pop().wait_seconds, 15)
        self.assertEqual(
            targets_from_slots({**base, "target_1_wait": "abc"}, []).pop().wait_seconds, 15
        )
        self.assertEqual(
            targets_from_slots({**base, "target_1_wait": 999}, []).pop().wait_seconds, 120
        )

    def test_target_requires_account_and_bot(self) -> None:
        """账号或 bot 缺失的目标被跳过。"""
        config = {
            "target_1_enabled": True,
            "target_1_account": "acc1",
            "target_1_bot": "",
        }
        self.assertEqual(targets_from_slots(config, ["acc1"]), [])

    def test_login_fields_and_actions(self) -> None:
        """登录字段读取与「登录动作」筛选。"""
        config = default_slot_config()
        config["account_1_login_action"] = LOGIN_ACTION_SEND
        config["account_1_login_code"] = ""
        config["account_1_login_password"] = "pwd"
        config["account_2_login_action"] = LOGIN_ACTION_CONFIRM
        config["account_2_login_code"] = "12345"

        fields = account_login_fields(config, "acc1")
        self.assertEqual(fields["action"], LOGIN_ACTION_SEND)
        self.assertEqual(fields["password"], "pwd")
        self.assertEqual(account_login_fields(config, "acc9")["action"], LOGIN_ACTION_NONE)

        actions = login_actions(config)
        self.assertEqual(len(actions), 2)
        self.assertEqual(actions[0][0], "acc1")
        self.assertEqual(actions[1], ("acc2", LOGIN_ACTION_CONFIRM, "12345", ""))

    def test_login_action_none_not_dispatched(self) -> None:
        """默认配置（全是不操作）不派发任何登录动作。"""
        self.assertEqual(login_actions(default_slot_config()), [])


class TestParseKeywords(unittest.TestCase):
    """结果关键词解析：分隔符、大小写、留空回落内置默认。"""

    def test_blank_falls_back_to_default(self) -> None:
        """留空（空串 / None）用内置默认。"""
        from tgsignin.core.config import (  # pylint: disable=import-outside-toplevel
            DEFAULT_SUCCESS_KEYWORDS,
            parse_keywords,
        )

        self.assertEqual(
            parse_keywords("", DEFAULT_SUCCESS_KEYWORDS),
            list(DEFAULT_SUCCESS_KEYWORDS),
        )
        self.assertEqual(parse_keywords(None, ("x",)), ["x"])

    def test_splits_and_lowercases(self) -> None:
        """支持 | 、逗号、顿号、换行分隔，并统一小写。"""
        from tgsignin.core.config import (  # pylint: disable=import-outside-toplevel
            parse_keywords,
        )

        self.assertEqual(
            parse_keywords("A|b，C、D\nE", ["fallback"]),
            ["a", "b", "c", "d", "e"],
        )


if __name__ == "__main__":
    unittest.main()
