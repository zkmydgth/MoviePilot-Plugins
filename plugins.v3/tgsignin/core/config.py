"""
TgSignin 配置模型与文本解析。

MoviePilot 的 JSON 配置表单没有按钮组件，做不出「增删行」的列表控件，
因此本插件沿用本仓库既有的「一行一条、竖线分隔」约定（与 SeedSpaceGuard
的目标目录写法同源），把「账号」与「签到目标」都做成多行文本配置。
本模块负责：

1. 文本 → 数据类（容错解析：忽略空行、``#`` 注释行与多余空白）；
2. 数据类 → 文本（用于配置表单回显，保持用户可读性）；
3. 基本校验（账号标识重复、目标引用了不存在的账号、内容缺失等）。

文本格式
--------
账号行（标识必填，其余可省）::

    <标识> | <显示名> | <手机号> [| <api_id> | <api_hash>]

签到目标行（等待秒数可省，默认 15）::

    <账号标识> | <bot 用户名> | <按钮|命令> | <按钮文字 或 命令> [| <等待秒数>]
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence

__all__ = [
    "AccountConfig",
    "BotTarget",
    "SIGN_TYPE_BUTTON",
    "SIGN_TYPE_COMMAND",
    "MAX_ACCOUNT_SLOTS",
    "MAX_TARGET_SLOTS",
    "PROXY_MODE_MP",
    "PROXY_MODE_CUSTOM",
    "PROXY_MODE_DIRECT",
    "LOGIN_ACTION_NONE",
    "LOGIN_ACTION_SEND",
    "LOGIN_ACTION_CONFIRM",
    "DEFAULT_API_ID",
    "DEFAULT_API_HASH",
    "DEFAULT_ACCOUNTS_TEXT",
    "DEFAULT_TARGETS_TEXT",
    "parse_accounts",
    "accounts_to_text",
    "parse_targets",
    "targets_to_text",
    "accounts_from_slots",
    "account_login_fields",
    "login_actions",
    "targets_from_slots",
    "default_slot_config",
    "normalize_key",
    "validate_config",
]

# 签到方式：按钮式（先 /start 再点按钮）与命令式（直接发命令）
SIGN_TYPE_BUTTON = "button"
SIGN_TYPE_COMMAND = "command"

# 方式字段的中英文别名（大小写不敏感）
_BUTTON_ALIASES = {"按钮", "点按钮", "button", "click", "inline", "btn"}
_COMMAND_ALIASES = {"命令", "发命令", "command", "cmd", "text", "msg"}

# 允许的半角/全角竖线分隔符
_SEPARATORS = ("|", "｜")

# 账号标识只允许小写字母数字与下划线/短横线：它会被拼进 session 文件名
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,15}$")

# Telegram Desktop 官方公开应用凭据（应用级，可登录任意多个账号）
DEFAULT_API_ID = 2040
DEFAULT_API_HASH = "b18441a1ff607e10a989891a5462e627"

# 表单槽位数量：账号与签到目标各给这么多「一行一组」的可视化槽位
MAX_ACCOUNT_SLOTS = 4
MAX_TARGET_SLOTS = 8

# 代理模式：跟随 MoviePilot / 自定义 / 直连
PROXY_MODE_MP = "mp"
PROXY_MODE_CUSTOM = "custom"
PROXY_MODE_DIRECT = "direct"

# 账号槽里的「登录动作」：保存配置时按它执行，执行完自动复位
LOGIN_ACTION_NONE = "不操作"
LOGIN_ACTION_SEND = "发送验证码"
LOGIN_ACTION_CONFIRM = "确认登录"

# 交接单里已实测确认的默认账号与签到目标（用户可随意增删改）
DEFAULT_ACCOUNTS_TEXT = """# 一行一个账号：标识 | 显示名 | 手机号
acc1 | 账号1 | +12025550101
acc2 | 账号2 | +12025550102"""

DEFAULT_TARGETS_TEXT = """# 一行一个签到目标：账号标识 | bot | 按钮/命令 | 按钮文字或命令 | 等待秒数(可省)
acc1 | @bb_emby_bot | 按钮 | 签到
acc1 | @HG_Emby_bot | 按钮 | 签到
acc1 | @okemby_bot | 按钮 | 签到
acc1 | @HDHaven_Bot | 命令 | /checkin
acc2 | @okemby_bot | 按钮 | 签到"""


@dataclass
class AccountConfig:
    """一个 Telegram 账号的登录与显示配置。"""

    # 账号标识：用户自定，作为 session 文件名与目标行的引用键
    key: str
    # 显示名（仅用于界面展示）
    label: str = ""
    # 手机号（含国际区号）；留空则在发送验证码时需要用户补充
    phone: str = ""
    # 应用级凭据，缺省用全局默认值
    api_id: int = DEFAULT_API_ID
    api_hash: str = DEFAULT_API_HASH

    def display(self) -> str:
        """
        返回界面用的账号展示名。

        :return str: ``显示名(标识)``；显示名缺失时只返回标识
        """

        return f"{self.label}({self.key})" if self.label else self.key


@dataclass
class BotTarget:
    """一条签到目标：某账号对某个 bot 的签到方式。"""

    # 引用 AccountConfig.key
    account_key: str
    # bot 用户名（可带或不带 @）
    bot_username: str
    # 签到方式：SIGN_TYPE_BUTTON 或 SIGN_TYPE_COMMAND
    sign_type: str = SIGN_TYPE_BUTTON
    # 按钮式=按钮文字（如「签到」）；命令式=命令（如 /checkin）
    action_text: str = "签到"
    # 发送后等待 bot 回复的秒数
    wait_seconds: int = 15
    # 该条是否启用
    enabled: bool = True

    def method_desc(self) -> str:
        """
        返回签到方式的中文描述（写进结果与日志）。

        :return str: 如 ``点按钮「签到」`` 或 ``发命令「/checkin」``
        """

        if self.sign_type == SIGN_TYPE_BUTTON:
            return f"点按钮「{self.action_text}」"
        return f"发命令「{self.action_text}」"


def _split_line(line: str) -> List[str]:
    """
    按半角/全角竖线切分一行并去掉每段空白。

    :param line: 原始配置行
    :return List[str]: 切分后的字段列表（已 strip）
    """

    parts = [line]
    for sep in _SEPARATORS:
        next_parts: List[str] = []
        for piece in parts:
            next_parts.extend(piece.split(sep))
        parts = next_parts
    return [piece.strip() for piece in parts]


def normalize_key(raw: str) -> str:
    """
    规范化账号标识：转小写并只保留合法字符。

    :param raw: 用户填写的原始标识
    :return str: 规范化后的标识（可能为空字符串，调用方需判空）
    """

    cleaned = re.sub(r"[^0-9a-zA-Z_-]", "", (raw or "").strip().lower())
    return cleaned


def parse_accounts(
    text: Optional[str],
    default_api_id: int = DEFAULT_API_ID,
    default_api_hash: str = DEFAULT_API_HASH,
) -> List[AccountConfig]:
    """
    解析账号多行文本。

    :param text: 多行文本，每行 ``标识 | 显示名 | 手机号 [| api_id | api_hash]``
    :param default_api_id: 行内未给 api_id 时使用的默认值
    :param default_api_hash: 行内未给 api_hash 时使用的默认值
    :return List[AccountConfig]: 账号列表（按出现顺序，标识去重）
    """

    accounts: List[AccountConfig] = []
    seen: set[str] = set()
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = _split_line(line)
        key = normalize_key(fields[0] if fields else "")
        if not key or key in seen:
            continue
        label = fields[1] if len(fields) > 1 else ""
        phone = fields[2] if len(fields) > 2 else ""
        api_id = default_api_id
        api_hash = default_api_hash
        if len(fields) > 3 and fields[3]:
            try:
                api_id = int(fields[3])
            except ValueError:
                api_id = default_api_id
        if len(fields) > 4 and fields[4]:
            api_hash = fields[4]
        seen.add(key)
        accounts.append(
            AccountConfig(
                key=key,
                label=label or key,
                phone=phone,
                api_id=api_id,
                api_hash=api_hash,
            )
        )
    return accounts


def accounts_to_text(
    accounts: Sequence[AccountConfig],
    default_api_id: int = DEFAULT_API_ID,
    default_api_hash: str = DEFAULT_API_HASH,
) -> str:
    """
    把账号列表还原成多行文本（用于配置表单回显）。

    :param accounts: 账号列表
    :param default_api_id: 全局默认 api_id（与之相同则不写出，保持行干净）
    :param default_api_hash: 全局默认 api_hash（同上）
    :return str: 多行文本
    """

    lines: List[str] = []
    for account in accounts:
        fields = [account.key, account.label or "", account.phone or ""]
        if account.api_id != default_api_id or account.api_hash != default_api_hash:
            fields.extend([str(account.api_id), account.api_hash])
        lines.append(" | ".join(fields).rstrip(" |"))
    return "\n".join(lines)


def _normalize_sign_type(raw: str) -> str:
    """
    把方式字段归一化为 button / command。

    :param raw: 用户填写的原始方式（中英文均可）
    :return str: SIGN_TYPE_BUTTON 或 SIGN_TYPE_COMMAND（无法识别时按按钮处理）
    """

    value = (raw or "").strip().lower()
    if value in _COMMAND_ALIASES:
        return SIGN_TYPE_COMMAND
    if value in _BUTTON_ALIASES:
        return SIGN_TYPE_BUTTON
    # 含「命令」字样也按命令处理，其余（含空）按按钮处理
    return SIGN_TYPE_COMMAND if "命令" in value else SIGN_TYPE_BUTTON


def parse_targets(text: Optional[str]) -> List[BotTarget]:
    """
    解析签到目标多行文本。

    :param text: 多行文本，每行
        ``账号标识 | bot | 按钮/命令 | 内容 [| 等待秒数]``
    :return List[BotTarget]: 目标列表（忽略空行与注释行，等待秒数非法时回落 15）
    """

    targets: List[BotTarget] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = _split_line(line)
        if len(fields) < 4:
            continue
        account_key = normalize_key(fields[0])
        bot_username = fields[1].strip()
        if not account_key or not bot_username:
            continue
        if not bot_username.startswith("@"):
            bot_username = f"@{bot_username}"
        sign_type = _normalize_sign_type(fields[2])
        action_text = fields[3].strip()
        if not action_text:
            action_text = "/checkin" if sign_type == SIGN_TYPE_COMMAND else "签到"
        wait_seconds = 15
        if len(fields) > 4 and fields[4]:
            try:
                wait_seconds = max(1, min(120, int(float(fields[4]))))
            except ValueError:
                wait_seconds = 15
        targets.append(
            BotTarget(
                account_key=account_key,
                bot_username=bot_username,
                sign_type=sign_type,
                action_text=action_text,
                wait_seconds=wait_seconds,
            )
        )
    return targets


def targets_to_text(targets: Sequence[BotTarget]) -> str:
    """
    把签到目标列表还原成多行文本（用于配置表单回显）。

    :param targets: 目标列表
    :return str: 多行文本
    """

    type_names = {SIGN_TYPE_BUTTON: "按钮", SIGN_TYPE_COMMAND: "命令"}
    lines: List[str] = []
    for target in targets:
        lines.append(
            " | ".join(
                [
                    target.account_key,
                    target.bot_username,
                    type_names.get(target.sign_type, "按钮"),
                    target.action_text,
                    str(target.wait_seconds),
                ]
            )
        )
    return "\n".join(lines)


def validate_config(
    accounts: Sequence[AccountConfig],
    targets: Sequence[BotTarget],
) -> List[str]:
    """
    校验账号与目标配置，返回人类可读的问题列表。

    :param accounts: 账号列表
    :param targets: 目标列表
    :return List[str]: 问题描述列表；为空表示没有发现问题
    """

    problems: List[str] = []
    if not accounts:
        problems.append("未配置任何 Telegram 账号")
    keys = {account.key for account in accounts}
    for account in accounts:
        if not account.phone:
            problems.append(f"账号 {account.key} 没填手机号（发验证码时会失败）")
        if not _KEY_RE.match(account.key):
            problems.append(f"账号标识「{account.key}」不合法（只允许小写字母、数字、_ 与 -）")
    if not targets:
        problems.append("未配置任何签到目标")
    for target in targets:
        if target.account_key not in keys:
            problems.append(
                f"签到目标 {target.bot_username} 引用了不存在的账号「{target.account_key}」"
            )
    return problems


def accounts_from_slots(config: Mapping[str, Any]) -> List[AccountConfig]:
    """
    从表单槽位字段解析账号列表。

    槽位字段命名：``account_<n>_enabled`` / ``_label`` / ``_phone``。
    账号标识由槽位序号自动生成（``acc1``…``acc4``），不需要用户填写——
    它只用于 session 文件名与「签到目标」的账号关联。

    :param config: 插件配置字典
    :return List[AccountConfig]: 账号列表
    """

    accounts: List[AccountConfig] = []
    for index in range(1, MAX_ACCOUNT_SLOTS + 1):
        if not config.get(f"account_{index}_enabled"):
            continue
        key = f"acc{index}"
        accounts.append(
            AccountConfig(
                key=key,
                label=str(config.get(f"account_{index}_label") or "") or key,
                phone=str(config.get(f"account_{index}_phone") or "").strip(),
            )
        )
    return accounts


def _slot_of(account_key: str) -> int:
    """
    由账号标识反推槽位序号（``acc3`` → 3）。

    :param account_key: 账号标识
    :return int: 槽位序号；无法解析时返回 0
    """

    text = (account_key or "").strip().lower()
    if not text.startswith("acc"):
        return 0
    try:
        index = int(text[3:])
    except ValueError:
        return 0
    return index if 1 <= index <= MAX_ACCOUNT_SLOTS else 0


def account_login_fields(config: Mapping[str, Any], account_key: str) -> Dict[str, str]:
    """
    读取某个账号槽的三个登录字段。

    :param config: 插件配置字典
    :param account_key: 账号标识（``acc1``…）
    :return Dict[str, str]: ``{action, code, password}``
    """

    index = _slot_of(account_key)
    if not index:
        return {"action": LOGIN_ACTION_NONE, "code": "", "password": ""}
    return {
        "action": str(config.get(f"account_{index}_login_action") or LOGIN_ACTION_NONE),
        "code": str(config.get(f"account_{index}_login_code") or ""),
        "password": str(config.get(f"account_{index}_login_password") or ""),
    }


def login_actions(
    config: Mapping[str, Any],
) -> List[Sequence[str]]:
    """
    收集需要执行的登录动作（保存配置时由插件入口派发）。

    只返回「槽位启用且动作是发送验证码/确认登录」的条目；每个条目形如
    ``(账号标识, 动作, 验证码, 两步验证密码)``。

    :param config: 插件配置字典
    :return List[Sequence[str]]: 待执行动作列表
    """

    actions: List[Sequence[str]] = []
    for index in range(1, MAX_ACCOUNT_SLOTS + 1):
        if not config.get(f"account_{index}_enabled"):
            continue
        action = str(config.get(f"account_{index}_login_action") or "").strip()
        if action not in (LOGIN_ACTION_SEND, LOGIN_ACTION_CONFIRM):
            continue
        actions.append(
            (
                f"acc{index}",
                action,
                str(config.get(f"account_{index}_login_code") or "").strip(),
                str(config.get(f"account_{index}_login_password") or ""),
            )
        )
    return actions


def targets_from_slots(
    config: Mapping[str, Any],
    account_keys: Sequence[str],
) -> List[BotTarget]:
    """
    从表单槽位字段解析签到目标列表。

    槽位字段命名：``target_<n>_enabled`` / ``_account`` / ``_bot`` / ``_method``
    / ``_action`` / ``_wait``。只取「启用且 bot 与账号都填了」的槽位；
    引用了不存在的账号也会保留，交给 ``validate_config`` 提示。

    :param config: 插件配置字典
    :param account_keys: 已配置的账号标识（仅作文档说明，不用于过滤）
    :return List[BotTarget]: 目标列表
    """

    del account_keys  # 仅保留签名语义，过滤交由校验函数
    targets: List[BotTarget] = []
    for index in range(1, MAX_TARGET_SLOTS + 1):
        if not config.get(f"target_{index}_enabled"):
            continue
        account_key = normalize_key(str(config.get(f"target_{index}_account") or ""))
        bot_username = str(config.get(f"target_{index}_bot") or "").strip()
        if not account_key or not bot_username:
            continue
        if not bot_username.startswith("@"):
            bot_username = f"@{bot_username}"
        sign_type = _normalize_sign_type(str(config.get(f"target_{index}_method") or ""))
        action_text = str(config.get(f"target_{index}_action") or "").strip()
        if not action_text:
            action_text = "/checkin" if sign_type == SIGN_TYPE_COMMAND else "签到"
        wait_seconds = 15
        raw_wait = config.get(f"target_{index}_wait")
        if raw_wait not in (None, ""):
            try:
                wait_seconds = max(1, min(120, int(float(raw_wait))))
            except (TypeError, ValueError):
                wait_seconds = 15
        targets.append(
            BotTarget(
                account_key=account_key,
                bot_username=bot_username,
                sign_type=sign_type,
                action_text=action_text,
                wait_seconds=wait_seconds,
            )
        )
    return targets


def default_slot_config() -> Dict[str, Any]:
    """
    生成默认的槽位配置（预填交接单里那 2 个账号与 5 条签到目标）。

    :return Dict[str, Any]: 槽位字段字典（可直接并入插件默认配置）
    """

    config: Dict[str, Any] = {}
    accounts = [
        ("账号1", "+12025550101"),
        ("账号2", "+12025550102"),
        ("", ""),
        ("", ""),
    ]
    for index, (label, phone) in enumerate(accounts, start=1):
        config[f"account_{index}_enabled"] = bool(phone)
        config[f"account_{index}_label"] = label
        config[f"account_{index}_phone"] = phone
        config[f"account_{index}_login_action"] = LOGIN_ACTION_NONE
        config[f"account_{index}_login_code"] = ""
        config[f"account_{index}_login_password"] = ""

    targets = [
        ("acc1", "@bb_emby_bot", SIGN_TYPE_BUTTON, "签到", 15),
        ("acc1", "@HG_Emby_bot", SIGN_TYPE_BUTTON, "签到", 15),
        ("acc1", "@okemby_bot", SIGN_TYPE_BUTTON, "签到", 15),
        ("acc1", "@HDHaven_Bot", SIGN_TYPE_COMMAND, "/checkin", 15),
        ("acc2", "@okemby_bot", SIGN_TYPE_BUTTON, "签到", 15),
    ]
    for index in range(1, MAX_TARGET_SLOTS + 1):
        if index <= len(targets):
            account_key, bot, sign_type, action, wait = targets[index - 1]
            config[f"target_{index}_enabled"] = True
        else:
            account_key, bot, sign_type, action, wait = ("", "", SIGN_TYPE_BUTTON, "", 15)
            config[f"target_{index}_enabled"] = False
        config[f"target_{index}_account"] = account_key
        config[f"target_{index}_bot"] = bot
        config[f"target_{index}_method"] = (
            "命令" if sign_type == SIGN_TYPE_COMMAND else "按钮"
        )
        config[f"target_{index}_action"] = action
        config[f"target_{index}_wait"] = wait
    return config
