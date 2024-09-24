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
    SIGN_TYPE_BUTTON,
    SIGN_TYPE_COMMAND,
    accounts_to_text,
    normalize_key,
    parse_accounts,
    parse_targets,
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


if __name__ == "__main__":
    unittest.main()
