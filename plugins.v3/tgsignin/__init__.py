#!/usr/bin/env python3
"""
TgSignin —— Telegram 多账号自动签到（MoviePilot V3 插件）。

需求来源：用户交接单《MP插件开发需求交接单.md》（2026-10-05）。核心能力：

- **多账号**：账号数量不限，一行一个（``标识 | 显示名 | 手机号``）；
- **多 bot**：每个账号可配多个签到目标，支持两种签到方式——按钮式
  （先发 ``/start`` 再点文字含「签到」的按钮）与命令式（直接发命令）；
- **两阶段登录**：详情页按钮「① 发送验证码」→「② 确认登录」（兼容两步验证），
  session 落在插件数据目录 ``/config/plugins/TgSignin/sessions/``，不写进容器 /app；
- **定时签到**：cron 可配（默认每天 09:00），失败按需走 MoviePilot 通知；
- **结果可查**：详情页表格展示每个 bot 最近一次结果（状态/时间/回复摘要）。

配置以「多行文本」表达（MoviePilot 的 JSON 配置表单没有按钮组件，做不出
增删行的列表控件），解析与校验见 ``core/config.py``。
"""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from apscheduler.triggers.cron import CronTrigger
from fastapi import Request

from app.plugins import _PluginBase
from app.runtime.config import settings
from app.runtime.log import logger
from app.schemas.types import EventType, MessageType
from app.sdk.events import Event, eventmanager

from .core.config import (
    DEFAULT_ACCOUNTS_TEXT,
    DEFAULT_API_HASH,
    DEFAULT_API_ID,
    DEFAULT_TARGETS_TEXT,
    MAX_ACCOUNT_SLOTS,
    MAX_TARGET_SLOTS,
    LOGIN_ACTION_CONFIRM,
    LOGIN_ACTION_NONE,
    LOGIN_ACTION_SEND,
    LOGIN_ACTION_LOGOUT,
    NOTIFY_MODE_ALL,
    NOTIFY_MODE_FAILURE,
    NOTIFY_MODE_NONE,
    NOTIFY_MODE_SUCCESS,
    DEFAULT_RETRY_INTERVAL_HOURS,
    DEFAULT_REPEATED_KEYWORDS,
    DEFAULT_SUCCESS_KEYWORDS,
    MAX_RETRY_INTERVAL_HOURS,
    PROXY_MODE_CUSTOM,
    PROXY_MODE_DIRECT,
    PROXY_MODE_MP,
    AccountConfig,
    BotTarget,
    accounts_to_text,
    DEFAULT_AI_KEYWORD_AUTOFILL,
    DEFAULT_CONCURRENCY,
    DEFAULT_FAILURE_KEYWORDS,
    KEYWORD_LIST_LIMIT,
    MAX_CONCURRENCY,
    account_login_fields,
    accounts_from_slots,
    coerce_scalar,
    default_slot_config,
    login_actions,
    parse_accounts,
    parse_keywords,
    parse_targets,
    targets_from_slots,
    targets_to_text,
    validate_config,
)
from .core.login import clear_pending, confirm_login, load_pending, send_code
from .core.ai import AiSigninJudge
from .core.autofill import merge_keywords
from .core.session import (
    connection_selftest,
    delete_session,
    proxy_desc,
    resolve_proxy,
    session_files,
)
from .core.retry import TZ, evaluate_retry, today_text
from .core.signin import (
    build_notify_text,
    normalize_bot,
    now_text,
    probe_target,
    run_all,
    summarize_results,
)
from .core.store import (
    PAGE_RESULT_LIMIT,
    load_state,
    recent_results,
    record_login,
    record_login_event,
    record_run,
    save_state,
)
from .version import VERSION

__all__ = ["TgSignin"]


def _table(headers: List[str], rows: List[Dict[str, Any]]) -> dict:
    """
    构造一个紧凑表格节点。

    详情页自身与各区块（如「AI 归纳关键词」）共用。此前它是 ``get_page`` 的
    局部函数、却被 ``_ai_keyword_block`` 跨作用域引用，导致详情页 NameError
    （2026-10-11 修复）。

    :param headers: 表头文字
    :param rows: 数据行
    :return dict: 表格节点
    """

    return {
        "component": "VTable",
        "props": {"density": "compact", "hover": True},
        "content": [
            {
                "component": "thead",
                "content": [
                    {
                        "component": "tr",
                        "content": [
                            {
                                "component": "th",
                                "props": {"class": "text-left"},
                                "text": title,
                            }
                            for title in headers
                        ],
                    }
                ],
            },
            {"component": "tbody", "content": rows},
        ],
    }


# 后台任务互斥登记表（同名任务不重复启动，2026-10-11 加）
_RUNNING_JOBS: Dict[str, bool] = {}
_RUNNING_JOBS_LOCK = threading.Lock()


def _truthy(value: Any, default: bool = False) -> bool:
    """
    把配置值（布尔 / 字符串 / 数字 / 下拉对象）统一成布尔。

    :param value: 原始配置值
    :param default: 无法判断时的返回值
    :return bool: 布尔结果
    """

    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, dict):
        return _truthy(value.get("value"), default)
    text = str(value).strip().lower()
    if not text:
        return default
    return text in ("1", "true", "yes", "on", "是", "开")


class TgSignin(_PluginBase):
    """Telegram 多账号自动签到插件。"""

    # 插件元数据
    plugin_name = "Telegram 自动签到"
    plugin_desc = (
        "多个 Telegram 账号按 cron 定时到各自订阅的 bot 签到；"
        "支持按钮式与命令式两种签到、两阶段登录（含两步验证）、"
        "结果展示与失败通知。账号与签到目标均可在配置里自由增删。"
    )
    plugin_icon = "tgsignin.png"
    plugin_version = VERSION
    plugin_author = "zkmydgth"
    author_url = "https://github.com/zkmydgth"
    plugin_config_prefix = "tgsignin_"
    plugin_order = 100
    auth_level = 1

    # ---------- 运行状态 ----------
    _enabled: bool = False
    _cron: str = "0 9 * * *"
    # 通知方式：不通知 / 仅失败时 / 仅成功时 / 成功与失败都通知
    _notify_mode: str = NOTIFY_MODE_FAILURE
    # 文本模式：用两个多行文本域配置账号与目标（默认关，走槽位表单）
    _use_text_mode: bool = False
    _accounts_text: str = DEFAULT_ACCOUNTS_TEXT
    _targets_text: str = DEFAULT_TARGETS_TEXT
    # 代理：默认跟随 MoviePilot；自定义时用下面三个字段
    _proxy_mode: str = PROXY_MODE_MP
    _proxy_type: str = "socks5"
    _proxy_host: str = ""
    _proxy_port: int = 0
    _api_id: int = DEFAULT_API_ID
    _api_hash: str = DEFAULT_API_HASH
    # 解析后的配置（每次 init_plugin 刷新）
    _accounts: List[AccountConfig] = []
    _targets: List[BotTarget] = []
    _config_problems: List[str] = []
    # 原始配置（供表单回显与局部更新）
    _raw_config: Dict[str, Any] = {}

    # ==================== 生命周期 ====================

    def init_plugin(self, config: dict = None) -> None:
        """
        按保存的配置初始化插件运行状态。

        :param config: 插件配置字典（MoviePilot 传入）
        :return None
        """
        self.stop_service()

        self._enabled = False
        self._cron = "0 9 * * *"
        self._notify_mode = NOTIFY_MODE_FAILURE
        self._retry_interval_hours = DEFAULT_RETRY_INTERVAL_HOURS
        self._success_keywords = list(DEFAULT_SUCCESS_KEYWORDS)
        self._repeated_keywords = list(DEFAULT_REPEATED_KEYWORDS)
        self._failure_keywords = list(DEFAULT_FAILURE_KEYWORDS)
        # AI 复核器：默认关闭；开启后仅对「未确认」结果调用 MP 内置智能助手
        self._ai_judge = AiSigninJudge(enabled=False, logger=logger)
        # 并发上限（1 = 串行，默认）与 AI 自动归纳关键词（默认关闭）
        self._concurrency = DEFAULT_CONCURRENCY
        self._ai_keyword_autofill = DEFAULT_AI_KEYWORD_AUTOFILL
        self._use_text_mode = False
        self._accounts_text = DEFAULT_ACCOUNTS_TEXT
        self._targets_text = DEFAULT_TARGETS_TEXT
        self._proxy_mode = PROXY_MODE_MP
        self._proxy_type = "socks5"
        self._proxy_host = ""
        self._proxy_port = 0
        self._api_id = DEFAULT_API_ID
        self._api_hash = DEFAULT_API_HASH
        self._raw_config = {}

        if config:
            self._enabled = bool(config.get("enabled"))
            self._cron = str(config.get("cron") or "0 9 * * *")
            # 通知方式：新字段优先；旧版只有布尔「失败时通知」，按它兼容映射
            raw_mode = coerce_scalar(config.get("notify_mode"))
            if raw_mode:
                self._notify_mode = raw_mode
            else:
                self._notify_mode = (
                    NOTIFY_MODE_FAILURE
                    if bool(config.get("notify_on_failure", True))
                    else NOTIFY_MODE_NONE
                )
            if self._notify_mode not in (
                NOTIFY_MODE_NONE,
                NOTIFY_MODE_FAILURE,
                NOTIFY_MODE_SUCCESS,
                NOTIFY_MODE_ALL,
            ):
                self._notify_mode = NOTIFY_MODE_FAILURE
            # 失败重试间隔（小时）：0 = 不重试；窗口到次日 0 点自动重置
            self._retry_interval_hours = max(
                0,
                min(
                    MAX_RETRY_INTERVAL_HOURS,
                    self._safe_int(
                        coerce_scalar(config.get("retry_interval_hours")),
                        DEFAULT_RETRY_INTERVAL_HOURS,
                    ),
                ),
            )
            # 结果关键词：留空用内置默认（2026-10-07 用户定案，接新 bot 不必改代码）
            self._success_keywords = parse_keywords(
                config.get("success_keywords"), DEFAULT_SUCCESS_KEYWORDS
            )
            self._repeated_keywords = parse_keywords(
                config.get("repeated_keywords"), DEFAULT_REPEATED_KEYWORDS
            )
            # 失败关键词：明确失败文案（如「签到服务暂不可用，请稍后重试。」）判为失败并进入失败重试
            self._failure_keywords = parse_keywords(
                config.get("failure_keywords"), DEFAULT_FAILURE_KEYWORDS
            )
            # 并发上限：1 = 串行（默认，行为与 1.0.x 一致）；2-4 = 同账号内「每 bot 一路」并发
            self._concurrency = max(
                1,
                min(
                    MAX_CONCURRENCY,
                    self._safe_int(
                        coerce_scalar(config.get("concurrency")), DEFAULT_CONCURRENCY
                    ),
                ),
            )
            # AI 自动归纳关键词（默认关闭）：开启后与 AI 复核**复用同一次调用**
            self._ai_keyword_autofill = bool(config.get("ai_keyword_autofill"))
            # 两个 AI 开关任一开启都要启用复核器（归纳本身必须依赖 AI 调用）
            self._ai_judge.enabled = (
                bool(config.get("ai_confirm_enabled")) or self._ai_keyword_autofill
            )
            self._use_text_mode = bool(config.get("use_text_mode"))
            self._accounts_text = str(config.get("accounts_text") or DEFAULT_ACCOUNTS_TEXT)
            self._targets_text = str(config.get("targets_text") or DEFAULT_TARGETS_TEXT)
            self._proxy_mode = coerce_scalar(config.get("proxy_mode")) or PROXY_MODE_MP
            self._proxy_type = coerce_scalar(config.get("proxy_type")) or "socks5"
            self._proxy_host = coerce_scalar(config.get("proxy_host"))
            self._proxy_port = self._safe_int(coerce_scalar(config.get("proxy_port")), 0)
            try:
                self._api_id = int(config.get("api_id") or DEFAULT_API_ID)
            except (TypeError, ValueError):
                self._api_id = DEFAULT_API_ID
            self._api_hash = str(config.get("api_hash") or DEFAULT_API_HASH)
            # 槽位字段原样留存，供表单回显与局部更新
            self._raw_config = dict(config)

        self._drop_legacy_config_keys()

        self._refresh_parsed_config()
        # 保存配置时若选了「登录动作」，在后台派发（不阻塞保存请求）
        self._dispatch_login_actions()

    @staticmethod
    def _safe_int(value: Any, fallback: int) -> int:
        """
        宽松转整数（空值/非法值回落）。

        :param value: 原值
        :param fallback: 回落值
        :return int: 整数值
        """

        try:
            return int(float(value))
        except (TypeError, ValueError):
            return fallback

    def _drop_legacy_config_keys(self) -> None:
        """
        清掉旧版本遗留的配置键（含已废弃的验证码/两步密码字段）。

        v1.0.1 的登录字段是全局的 ``login_code`` / ``login_password``，v1.0.2 起
        改为每个账号槽各一份；这些旧键如果留着，会把**两步验证密码**长期保存在
        插件配置里，所以升级后主动删一次。

        :return None
        """

        legacy = ("login_account", "login_code", "login_password", "notify_on_failure")
        present = [key for key in legacy if key in self._raw_config]
        if not present:
            return
        cleaned = {
            key: value for key, value in self._raw_config.items() if key not in legacy
        }
        cleaned["notify_mode"] = self._notify_mode
        self._raw_config = cleaned
        logger.info("【TgSignin】已清理旧版遗留配置键：%s", "、".join(present))
        self.update_config(cleaned)

    def _refresh_parsed_config(self) -> None:
        """
        重新解析账号与签到目标配置，并刷新校验问题列表。

        :return None
        """
        if self._use_text_mode:
            # 文本模式：两个多行文本域（可无限扩展，适合批量粘贴）
            self._accounts = parse_accounts(
                self._accounts_text, self._api_id, self._api_hash
            )
            self._targets = parse_targets(self._targets_text)
        else:
            # 槽位模式（默认）：表单里一行一组，账号用下拉选择
            self._accounts = accounts_from_slots(self._raw_config)
            if not self._accounts and self._accounts_text:
                # 首次从旧版本升级：槽位为空时回退到历史文本配置
                self._accounts = parse_accounts(
                    self._accounts_text, self._api_id, self._api_hash
                )
            self._targets = targets_from_slots(
                self._raw_config, [account.key for account in self._accounts]
            )
            if not self._targets and self._targets_text:
                self._targets = parse_targets(self._targets_text)
        self._config_problems = validate_config(self._accounts, self._targets)
        for problem in self._config_problems:
            logger.warning("【TgSignin】配置问题：%s", problem)

    def _proxy_tuple(self) -> Optional[Tuple[str, str, int]]:
        """
        按代理模式解析出当前生效的代理（跟随 MP / 自定义 / 直连）。

        :return Optional[Tuple[str, str, int]]: 代理元组；None 表示直连
        """

        return resolve_proxy(
            self._proxy_mode,
            self._proxy_type,
            self._proxy_host,
            self._proxy_port,
            str(getattr(settings, "PROXY_HOST", "") or ""),
        )

    def _notify_label(self) -> str:
        """
        返回通知方式的中文标签（页面摘要与排障用）。

        :return str: 如 ``仅失败时通知``
        """

        mapping = {
            NOTIFY_MODE_NONE: "不通知",
            NOTIFY_MODE_FAILURE: "仅失败时通知",
            NOTIFY_MODE_SUCCESS: "仅成功时通知",
            NOTIFY_MODE_ALL: "成功与失败都通知",
        }
        return mapping.get(self._notify_mode, "仅失败时通知")

    def _slot_login(self, account_key: str) -> Dict[str, str]:
        """
        读取某个账号槽的登录字段（动作 / 验证码 / 两步验证密码）。

        :param account_key: 账号标识（``acc1``…）
        :return Dict[str, str]: ``{action, code, password}``
        """

        return account_login_fields(self._raw_config, account_key)

    @staticmethod
    def _spawn_background(target: Callable[[], None], name: str) -> bool:
        """
        在后台线程执行一个同步函数（前台请求立即返回，避免页面进度条）。

        **同名任务互斥**（2026-10-11 加）：同名的任务已在跑时直接跳过，
        避免连点「立即签到」叠成多份并发全量（同一 session 并发连接、
        重复签到、状态互相覆盖）。

        :param target: 无参可调用对象
        :param name: 线程名（同时作为互斥键，便于排障）
        :return bool: True = 已启动；False = 同名任务在运行，本次未启动
        """

        with _RUNNING_JOBS_LOCK:
            if _RUNNING_JOBS.get(name):
                logger.info(
                    "【TgSignin】已有同名后台任务在运行，跳过本次触发：%s", name
                )
                return False
            _RUNNING_JOBS[name] = True

        def runner() -> None:
            """线程体：执行目标，异常只记日志不外抛；结束务必释放互斥标记。"""
            try:
                target()
            except Exception as error:  # pylint: disable=broad-except
                logger.error("【TgSignin】后台任务 %s 失败：%s", name, error)
            finally:
                with _RUNNING_JOBS_LOCK:
                    _RUNNING_JOBS.pop(name, None)

        threading.Thread(target=runner, name=f"tgsignin-{name}", daemon=True).start()
        return True

    def _dispatch_login_actions(self) -> None:
        """
        把配置里选中的「登录动作」放后台执行，并立刻把动作复位为「不操作」。

        这样用户只需「选动作 + 保存」即可完成发码/确认登录，无需切页面；
        且保存请求立刻返回（动作在后台线程里跑）。

        :return None
        """

        actions = login_actions(self._raw_config)
        if not actions:
            return
        # 先复位动作，避免保存触发的重入把同一动作跑两遍
        reset_payload = dict(self._raw_config)
        for index in range(1, MAX_ACCOUNT_SLOTS + 1):
            reset_payload[f"account_{index}_login_action"] = LOGIN_ACTION_NONE
        self._raw_config = reset_payload
        self.update_config(reset_payload)
        if not self._spawn_background(
            lambda: self._run_sync(lambda: self._execute_login_actions(actions)),
            "login-actions",
        ):
            logger.info("【TgSignin】登录动作已在执行中，本次保存不再重复触发")

    async def _execute_login_actions(self, actions: List[Any]) -> None:
        """
        依次执行登录动作（发送验证码 / 确认登录），结果写状态并按需通知。

        :param actions: ``[(账号标识, 动作, 验证码, 两步验证密码)]``
        :return None
        """

        proxy = self._proxy_tuple()
        for account_key, action, code, password in actions:
            account = self._find_account(account_key)
            if account is None:
                logger.warning("【TgSignin】登录动作找不到账号：%s", account_key)
                continue
            if action == LOGIN_ACTION_SEND:
                result = await send_code(self.get_data_path(), account, proxy)
            elif action == LOGIN_ACTION_LOGOUT:
                # 退出登录：删除 session 与登录记录（配置页按钮带原生确认弹窗）
                result = self._logout_account(account)
            else:
                result = await confirm_login(
                    self.get_data_path(), account, code, password, proxy
                )
                if result.get("ok"):
                    record_login(
                        self.get_data_path(), account.key, result.get("me") or {}
                    )
            ok = bool(result.get("ok"))
            message = str(result.get("message") or "")
            record_login_event(
                self.get_data_path(), account.key, str(action), ok, message
            )
            logger.info(
                "【TgSignin】登录动作 %s %s → %s", account.key, action, message
            )
            if not ok:
                try:
                    self.post_message(
                        mtype=MessageType.Plugin,
                        title="【Telegram 登录】",
                        text=f"{account.display()} {action} 失败：{message}",
                    )
                except Exception as error:  # pylint: disable=broad-except
                    logger.error("【TgSignin】登录失败通知发送出错：%s", error)

    def get_state(self) -> bool:
        """
        获取插件启用状态。

        :return bool: 是否启用
        """
        return self._enabled

    def stop_service(self) -> None:
        """
        停止插件后台服务并释放资源（本插件无常驻线程，仅置位）。

        :return None
        """
        self._running = False

    # ==================== 配置表单 ====================

    @staticmethod
    def _group_header(title: str, desc: str = "") -> dict:
        """
        构造配置表单的分组标题节点（无 model，纯展示）。

        :param title: 组标题
        :param desc: 组说明
        :return dict: 表单节点
        """

        return {
            "component": "VAlert",
            "props": {
                "type": "info",
                "variant": "tonal",
                "class": "mt-6",
                "text": f"▼ {title}　—　{desc}" if desc else f"▼ {title}",
            },
        }

    def get_form(self) -> Tuple[Optional[List[dict]], Dict[str, Any]]:
        """
        返回插件配置表单与默认配置。

        :return Tuple[Optional[List[dict]], Dict[str, Any]]: ``(表单结构, 默认值)``
        """
        self._refresh_parsed_config()
        account_items = [
            {"title": account.display(), "value": account.key} for account in self._accounts
        ]
        method_items = [
            {"title": "按钮式（先 /start 再点按钮）", "value": "按钮"},
            {"title": "命令式（直接发命令）", "value": "命令"},
        ]
        login_action_items = [
            {"title": LOGIN_ACTION_NONE, "value": LOGIN_ACTION_NONE},
            {"title": LOGIN_ACTION_SEND, "value": LOGIN_ACTION_SEND},
            {"title": LOGIN_ACTION_CONFIRM, "value": LOGIN_ACTION_CONFIRM},
            {"title": LOGIN_ACTION_LOGOUT, "value": LOGIN_ACTION_LOGOUT},
        ]
        proxy_items = [
            {"title": "跟随 MoviePilot 代理", "value": PROXY_MODE_MP},
            {"title": "自定义代理", "value": PROXY_MODE_CUSTOM},
            {"title": "直连（不走代理）", "value": PROXY_MODE_DIRECT},
        ]
        notify_items = [
            {"title": "仅失败时通知", "value": NOTIFY_MODE_FAILURE},
            {"title": "仅成功时通知", "value": NOTIFY_MODE_SUCCESS},
            {"title": "成功与失败都通知", "value": NOTIFY_MODE_ALL},
            {"title": "不通知", "value": NOTIFY_MODE_NONE},
        ]
        mp_proxy = str(getattr(settings, "PROXY_HOST", "") or "").strip()

        content: List[dict] = [
            {
                "component": "VRow",
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 3},
                        "content": [
                            {
                                "component": "VSwitch",
                                "props": {"model": "enabled", "label": "启用插件"},
                            }
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 4},
                        "content": [
                            {
                                "component": "VCronField",
                                "props": {
                                    "model": "cron",
                                    "label": "执行周期",
                                    "placeholder": "5位cron表达式，默认 0 9 * * *",
                                    "persistent-hint": True,
                                    "hint": "点开可直接选「每天/每周/每月 + 时间」，也可手输五段式 cron",
                                },
                            }
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 4},
                        "content": [
                            {
                                "component": "VCombobox",
                                "props": {
                                    "model": "notify_mode",
                                    "label": "通知方式",
                                    "items": notify_items,
                                    "persistent-hint": True,
                                    "hint": "「仅成功时」= 只有全部成功才通知；登录动作失败始终通知",
                                },
                            }
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 4},
                        "content": [
                            {
                                "component": "VTextField",
                                "props": {
                                    "model": "retry_interval_hours",
                                    "label": "失败重试间隔（小时）",
                                    "type": "number",
                                    "min": 0,
                                    "max": MAX_RETRY_INTERVAL_HOURS,
                                    "placeholder": f"默认 {DEFAULT_RETRY_INTERVAL_HOURS}，0=不重试",
                                    "persistent-hint": True,
                                    "hint": "当天签到失败后每隔这么久重试一次；到次日 0 点自动重置重试窗口；"
                                    f"超出范围按 0-{MAX_RETRY_INTERVAL_HOURS} 截断",
                                },
                            }
                        ],
                    },
                ],
            },
            {
                "component": "VRow",
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 6},
                        "content": [
                            {
                                "component": "VTextarea",
                                "props": {
                                    "model": "success_keywords",
                                    "label": "签到成功关键词",
                                    "rows": 3,
                                    "persistent-hint": True,
                                    "hint": "回复/弹窗里出现任一关键词即判「签到成功」；多个用 | 或逗号分隔，留空用内置默认",
                                    "placeholder": "|".join(DEFAULT_SUCCESS_KEYWORDS),
                                },
                            }
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 6},
                        "content": [
                            {
                                "component": "VTextarea",
                                "props": {
                                    "model": "repeated_keywords",
                                    "label": "已签到关键词",
                                    "rows": 3,
                                    "persistent-hint": True,
                                    "hint": "回复/弹窗里出现任一关键词即判「今日已签到」；多个用 | 或逗号分隔，留空用内置默认",
                                    "placeholder": "|".join(DEFAULT_REPEATED_KEYWORDS),
                                },
                            }
                        ],
                    },
                ],
            },
            {
                "component": "VRow",
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 6},
                        "content": [
                            {
                                "component": "VTextarea",
                                "props": {
                                    "model": "failure_keywords",
                                    "label": "签到失败关键词",
                                    "rows": 3,
                                    "persistent-hint": True,
                                    "hint": "回复/弹窗里出现任一关键词即判「失败」，并进入失败重试"
                                    "（如 bot 回「签到服务暂不可用，请稍后重试。」）；"
                                    "多个用 | 或逗号分隔，留空用内置默认",
                                    "placeholder": "|".join(DEFAULT_FAILURE_KEYWORDS),
                                },
                            }
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 6},
                        "content": [
                            {
                                "component": "VSwitch",
                                "props": {
                                    "model": "ai_confirm_enabled",
                                    "label": "自动使用 AI 确认",
                                    "persistent-hint": True,
                                    "hint": "仅当结果落到「未确认」时，调用 MoviePilot 内置智能助手判定"
                                    "成功/失败；判定失败会进入失败重试，无法判定则保持「未确认」。"
                                    "需在 MP 里配置好智能助手（LLM）。默认关闭",
                                },
                            }
                        ],
                    },
                ],
            },
            {
                "component": "VRow",
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 6},
                        "content": [
                            {
                                "component": "VTextField",
                                "props": {
                                    "model": "concurrency",
                                    "label": "并发签到上限",
                                    "type": "number",
                                    "min": 1,
                                    "max": MAX_CONCURRENCY,
                                    "persistent-hint": True,
                                    "hint": "1 = 串行（默认，行为与旧版一致）；2-4 = 同一账号内「每个 bot 一路」"
                                    "同时触发（账号之间仍串行，避免同账号多连接风控）。建议 3；"
                                    f"超出范围按 1-{MAX_CONCURRENCY} 截断",
                                    "placeholder": f"默认 {DEFAULT_CONCURRENCY}",
                                },
                            }
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 6},
                        "content": [
                            {
                                "component": "VSwitch",
                                "props": {
                                    "model": "ai_keyword_autofill",
                                    "label": "AI 自动归纳关键词",
                                    "persistent-hint": True,
                                    "hint": "签到后用 AI 判定回复属于成功/已签到/失败，并把它逐字摘取的短语自动补进对应关键词栏。"
                                    "只增不删、写入前查重（已存在的词不会重复添加），每栏上限 "
                                    f"{KEYWORD_LIST_LIMIT} 条；新增记录可在详情页回看。需配置 MP 智能助手。默认关闭",
                                },
                            }
                        ],
                    },
                ],
            },
            self._group_header(
                "账号", f"最多 {MAX_ACCOUNT_SLOTS} 个；打开左侧开关即可编辑该账号"
            ),
        ]

        for index in range(1, MAX_ACCOUNT_SLOTS + 1):
            enabled_expr = f"account_{index}_enabled"
            phone_props: Dict[str, Any] = {
                "model": f"account_{index}_phone",
                "label": "手机号（含国际区号）",
                "placeholder": "+8613800138000",
                "show": enabled_expr,
            }
            if index == 1:
                phone_props["persistent-hint"] = True
                phone_props["hint"] = "账号内部标识由槽位自动生成（acc1…），无需填写"
            content.append(
                {
                    "component": "VRow",
                    "content": [
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 2},
                            "content": [
                                {
                                    "component": "VSwitch",
                                    "props": {
                                        "model": enabled_expr,
                                        "label": f"账号 {index}",
                                    },
                                }
                            ],
                        },
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 3},
                            "content": [
                                {
                                    "component": "VTextField",
                                    "props": {
                                        "model": f"account_{index}_label",
                                        "label": "显示名",
                                        "placeholder": f"账号{index}",
                                        "show": enabled_expr,
                                    },
                                }
                            ],
                        },
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 4},
                            "content": [
                                {
                                    "component": "VTextField",
                                    "props": phone_props,
                                }
                            ],
                        },
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 3},
                            "content": [
                                {
                                    "component": "VCombobox",
                                    "props": {
                                        "model": f"account_{index}_login_action",
                                        "label": "登录动作",
                                        "items": login_action_items,
                                        "show": enabled_expr,
                                    },
                                }
                            ],
                        },
                    ],
                }
            )
            content.append(
                {
                    "component": "VRow",
                    "content": [
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 4},
                            "content": [
                                {
                                    "component": "VTextField",
                                    "props": {
                                        "model": f"account_{index}_login_code",
                                        "label": "登录验证码",
                                        "show": enabled_expr,
                                    },
                                }
                            ],
                        },
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 4},
                            "content": [
                                {
                                    "component": "VTextField",
                                    "props": {
                                        "model": f"account_{index}_login_password",
                                        "label": "两步验证密码（可选）",
                                        "type": "password",
                                        "show": enabled_expr,
                                    },
                                }
                            ],
                        },
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 4},
                            "content": [
                                {
                                    "component": "VAlert",
                                    "props": {
                                        "type": "info",
                                        "variant": "tonal",
                                        "show": enabled_expr,
                                        "text": "① 选「发送验证码」保存 → ② 填验证码、选「确认登录」再保存"
                                                "；点下方按钮可直接退出该账号登录",
                                    },
                                },
                                {
                                    "component": "VBtn",
                                    "props": {
                                        "color": "warning",
                                        "variant": "tonal",
                                        "size": "small",
                                        "class": "mt-2",
                                        "prepend-icon": "mdi-logout",
                                        "show": enabled_expr,
                                        "onClick": self._logout_button_js(index),
                                    },
                                    "text": "退出登录",
                                }
                            ],
                        },
                    ],
                }
            )

        content.append(
            self._group_header(
                "签到目标",
                f"最多 {MAX_TARGET_SLOTS} 条；打开左侧开关后选账号（下拉）/填 bot/选方式",
            )
        )
        for index in range(1, MAX_TARGET_SLOTS + 1):
            enabled_expr = f"target_{index}_enabled"
            content.append(
                {
                    "component": "VRow",
                    "content": [
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 1},
                            "content": [
                                {
                                    "component": "VSwitch",
                                    "props": {"model": enabled_expr, "label": str(index)},
                                }
                            ],
                        },
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 2},
                            "content": [
                                {
                                    "component": "VCombobox",
                                    "props": {
                                        "model": f"target_{index}_account",
                                        "label": "账号",
                                        "items": account_items,
                                        "show": enabled_expr,
                                    },
                                }
                            ],
                        },
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 3},
                            "content": [
                                {
                                    "component": "VTextField",
                                    "props": {
                                        "model": f"target_{index}_bot",
                                        "label": "bot 用户名",
                                        "placeholder": "@example_bot_a",
                                        "show": enabled_expr,
                                    },
                                }
                            ],
                        },
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 2},
                            "content": [
                                {
                                    "component": "VCombobox",
                                    "props": {
                                        "model": f"target_{index}_method",
                                        "label": "方式",
                                        "items": method_items,
                                        "show": enabled_expr,
                                    },
                                }
                            ],
                        },
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 2},
                            "content": [
                                {
                                    "component": "VTextField",
                                    "props": {
                                        "model": f"target_{index}_action",
                                        "label": "按钮文字/命令",
                                        "placeholder": "签到 或 /checkin",
                                        "show": enabled_expr,
                                    },
                                }
                            ],
                        },
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 2},
                            "content": [
                                {
                                    "component": "VTextField",
                                    "props": {
                                        "model": f"target_{index}_wait",
                                        "label": "等待秒数",
                                        "placeholder": "15",
                                        "show": enabled_expr,
                                    },
                                }
                            ],
                        },
                    ],
                }
            )

        content.extend(
            [
                self._group_header(
                    "网络与凭据",
                    "默认跟随 MoviePilot 的代理设置；Telegram 流量需要能出去的节点",
                ),
                {
                    "component": "VRow",
                    "content": [
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 3},
                            "content": [
                                {
                                    "component": "VSelect",
                                    "props": {
                                        "model": "proxy_mode",
                                        "label": "代理模式",
                                        "items": proxy_items,
                                        "persistent-hint": True,
                                        "hint": f"跟随 MP 时用 MP 的 PROXY_HOST（当前：{mp_proxy or '未配置'}）",
                                    },
                                }
                            ],
                        },
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 3},
                            "content": [
                                {
                                    "component": "VCombobox",
                                    "props": {
                                        "model": "proxy_type",
                                        "label": "代理类型（自定义）",
                                        "items": [
                                            {"title": "socks5", "value": "socks5"},
                                            {"title": "http", "value": "http"},
                                        ],
                                        "show": "proxy_mode === 'custom'",
                                    },
                                }
                            ],
                        },
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 3},
                            "content": [
                                {
                                    "component": "VTextField",
                                    "props": {
                                        "model": "proxy_host",
                                        "label": "代理主机（自定义）",
                                        "show": "proxy_mode === 'custom'",
                                    },
                                }
                            ],
                        },
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 3},
                            "content": [
                                {
                                    "component": "VTextField",
                                    "props": {
                                        "model": "proxy_port",
                                        "label": "代理端口（自定义）",
                                        "show": "proxy_mode === 'custom'",
                                    },
                                }
                            ],
                        },
                    ],
                },
                {
                    "component": "VRow",
                    "content": [
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 4},
                            "content": [
                                {
                                    "component": "VTextField",
                                    "props": {
                                        "model": "api_id",
                                        "label": "api_id",
                                        "persistent-hint": True,
                                        "hint": "应用级凭据；默认用 Telegram Desktop 公开值，可整套替换",
                                    },
                                }
                            ],
                        },
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 8},
                            "content": [
                                {
                                    "component": "VTextField",
                                    "props": {"model": "api_hash", "label": "api_hash"},
                                }
                            ],
                        },
                    ],
                },
                self._group_header(
                    "高级：文本批量配置",
                    "默认关闭；开启后用多行文本代替上面的槽位（适合批量粘贴或超过槽位数）。"
                    "注意：两种模式是各自独立的数据源 —— 关掉文本模式后按上面的槽位解析，"
                    "槽位为空会回落到内置示例值；且账号标识（acc1…）决定 session 文件名，"
                    "切换模式后可能需要重新登录",
                ),
                {
                    "component": "VRow",
                    "content": [
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 4},
                            "content": [
                                {
                                    "component": "VSwitch",
                                    "props": {
                                        "model": "use_text_mode",
                                        "label": "启用文本模式",
                                    },
                                }
                            ],
                        },
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 8},
                            "content": [
                                {
                                    "component": "VTextarea",
                                    "props": {
                                        "model": "accounts_text",
                                        "label": "账号列表（文本模式）",
                                        "rows": 3,
                                        "show": "use_text_mode",
                                        "persistent-hint": True,
                                        "hint": "格式：标识 | 显示名 | 手机号（含国际区号）",
                                    },
                                }
                            ],
                        },
                        {
                            "component": "VCol",
                            "props": {"cols": 12},
                            "content": [
                                {
                                    "component": "VTextarea",
                                    "props": {
                                        "model": "targets_text",
                                        "label": "签到目标列表（文本模式）",
                                        "rows": 5,
                                        "show": "use_text_mode",
                                        "persistent-hint": True,
                                        "hint": "格式：账号标识 | bot用户名 | 按钮或命令 | 按钮文字或命令 [| 等待秒数]",
                                    },
                                }
                            ],
                        },
                    ],
                },
            ]
        )

        defaults: Dict[str, Any] = {
            "enabled": False,
            "cron": "0 9 * * *",
            "notify_mode": NOTIFY_MODE_FAILURE,
            "retry_interval_hours": DEFAULT_RETRY_INTERVAL_HOURS,
            "success_keywords": "|".join(DEFAULT_SUCCESS_KEYWORDS),
            "repeated_keywords": "|".join(DEFAULT_REPEATED_KEYWORDS),
            "failure_keywords": "|".join(DEFAULT_FAILURE_KEYWORDS),
            "ai_confirm_enabled": False,
            "concurrency": DEFAULT_CONCURRENCY,
            "ai_keyword_autofill": DEFAULT_AI_KEYWORD_AUTOFILL,
            "use_text_mode": False,
            "accounts_text": DEFAULT_ACCOUNTS_TEXT,
            "targets_text": DEFAULT_TARGETS_TEXT,
            "proxy_mode": PROXY_MODE_MP,
            "proxy_type": "socks5",
            "proxy_host": "",
            "proxy_port": 0,
            "api_id": DEFAULT_API_ID,
            "api_hash": DEFAULT_API_HASH,
        }
        defaults.update(default_slot_config())
        return [{"component": "VForm", "content": content}], defaults

    # ==================== 详情页 ====================

    def _login_status_rows(self) -> List[Dict[str, Any]]:
        """
        构造账号登录状态行（详情页表格用）。

        :return List[Dict[str, Any]]: 表格行
        """
        state = load_state(self.get_data_path())
        account_state: Dict[str, Any] = state.get("accounts") or {}
        files = session_files(self.get_data_path())
        rows: List[Dict[str, Any]] = []
        for account in self._accounts:
            info = account_state.get(account.key) or {}
            if not files.get(account.key):
                status = "未登录"
            elif info.get("logged_in"):
                status = f"已登录：{info.get('name', '')} @{info.get('username', '')}"
            else:
                status = "有 session（未校验）"
            rows.append(
                {
                    "component": "tr",
                    "content": [
                        {"component": "td", "text": account.display()},
                        {"component": "td", "text": account.phone or "-"},
                        {"component": "td", "text": status},
                    ],
                }
            )
        if not rows:
            rows.append(
                {
                    "component": "tr",
                    "content": [
                        {"component": "td", "props": {"colspan": 3}, "text": "尚未配置账号"},
                    ],
                }
            )
        return rows

    @staticmethod
    def _ai_note(item: Dict[str, Any]) -> str:
        """
        返回结果行里的「AI 复核」说明（含本次归纳出的词）。

        :param item: 一条签到结果（含 ai_state / ai_verdict / ai_keywords / ai_message）
        :return str: 中文短说明；AI 未参与时返回空串
        """

        state = str(item.get("ai_state") or "")
        verdict_names = {"success": "成功", "repeated": "已签到", "failure": "失败"}
        verdict = verdict_names.get(str(item.get("ai_verdict") or ""), "")
        keywords = item.get("ai_keywords") or []
        if state == "judged":
            text = f"AI 判定{verdict}"
        elif state == "unknown":
            text = "AI 无法判定"
        elif state == "unconfigured":
            text = "AI 未配置"
        elif state == "timeout":
            text = "AI 超时"
        elif state == "error":
            text = f"AI 异常：{str(item.get('ai_message') or '')[:40]}"
        else:
            return ""
        return f"{text}｜词={'/'.join(str(w) for w in keywords)}" if keywords else text

    def _row_retry_button(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """
        构造结果表行内的「重试」按钮（单目标手动重试，2026-10-11 加）。

        :param item: 一条历史结果（需含 account / bot）
        :return Dict[str, Any]: VBtn 节点；账号或 bot 缺失时返回占位文本
        """

        account = str(item.get("account") or "")
        bot = str(item.get("bot") or "")
        if not account or not bot or bot == "-":
            return {"component": "span", "text": "—"}
        return self._button(
            "重试",
            "/signin/run",
            color="secondary",
            params={"account": account, "bot": bot},
        )

    def _result_rows(self) -> List[Dict[str, Any]]:
        """
        构造最近签到结果行（详情页表格用）。

        :return List[Dict[str, Any]]: 表格行
        """
        state = load_state(self.get_data_path())
        results = recent_results(state, PAGE_RESULT_LIMIT)
        rows: List[Dict[str, Any]] = []
        for item in results:
            ok = bool(item.get("ok"))
            status = str(item.get("status") or ("签到成功" if ok else "失败"))
            detail = item.get("reply") or item.get("error") or ""
            alert = str(item.get("alert") or "")
            if alert:
                detail = f"弹窗：{alert}｜{detail}" if detail else f"弹窗：{alert}"
            ai_note = self._ai_note(item)
            rows.append(
                {
                    "component": "tr",
                    "content": [
                        {"component": "td", "text": item.get("time", "")},
                        {"component": "td", "text": item.get("account", "")},
                        {"component": "td", "text": item.get("bot", "")},
                        {
                            "component": "td",
                            "text": f"{'✅' if ok else '❌'} {status}",
                        },
                        {
                            "component": "td",
                            "props": {"class": "text-truncate", "style": "max-width: 320px"},
                            "text": str(detail)[:120],
                        },
                        {
                            "component": "td",
                            "props": {"class": "text-truncate", "style": "max-width: 220px"},
                            "text": ai_note,
                        },
                        {
                            "component": "td",
                            "content": [self._row_retry_button(item)],
                        },
                    ],
                }
            )
        if not rows:
            rows.append(
                {
                    "component": "tr",
                    "content": [
                        {
                            "component": "td",
                            "props": {"colspan": 7},
                            "text": "还没有签到记录",
                        },
                    ],
                }
            )
        return rows

    def _ai_keyword_block(self) -> List[Dict[str, Any]]:
        """
        构造「AI 归纳关键词」详情页区块（没有记录时返回空列表）。

        :return List[Dict[str, Any]]: Vuetify JSON 组件列表
        """

        state = load_state(self.get_data_path())
        log = list(state.get("ai_keyword_log") or [])
        if not log:
            return []
        rows: List[Dict[str, Any]] = []
        for item in reversed(log[-30:]):
            rows.append(
                {
                    "component": "tr",
                    "content": [
                        {"component": "td", "text": item.get("time", "")},
                        {"component": "td", "text": item.get("account", "")},
                        {"component": "td", "text": item.get("bot", "")},
                        {"component": "td", "text": item.get("verdict", "")},
                        {"component": "td", "text": item.get("keyword", "")},
                    ],
                }
            )
        return [
            self._group_header("AI 归纳关键词（只增不删、已查重）"),
            _table(["时间", "账号", "bot", "档位", "新增词"], rows),
        ]

    def _button(
        self,
        text: str,
        path: str,
        color: str = "primary",
        method: str = "get",
        params: Optional[Dict[str, Any]] = None,
    ) -> dict:
        """
        构造详情页操作按钮（按钮只在详情页可用）。

        :param text: 按钮文字
        :param path: 插件 API 路径（相对 ``plugin/<插件ID>``）
        :param color: 按钮颜色
        :param method: HTTP 方法
        :param params: 除 apikey 外要额外携带的参数（如 account/bot）
        :return dict: 表单节点
        """

        query: Dict[str, Any] = {"apikey": settings.API_TOKEN}
        if params:
            query.update(params)
        return {
            "component": "VBtn",
            "props": {
                "color": color,
                "variant": "flat",
                "size": "small",
                "class": "mr-2 mb-2",
            },
            "text": text,
            "events": {
                "click": {
                    "api": f"plugin/{self.__class__.__name__}{path}",
                    "method": method,
                    "params": query,
                }
            },
        }

    def _next_fire_text(self) -> str:
        """
        返回执行周期（cron）的下一次触发时间文本（解析失败时返回空串）。

        :return str: ``YYYY-MM-DD HH:MM:SS`` 或空串
        """

        try:
            trigger = CronTrigger.from_crontab(self._cron)
            fire = trigger.get_next_fire_time(None, datetime.now(TZ))
        except Exception:  # pylint: disable=broad-except
            return ""
        if fire is None:
            return ""
        return fire.astimezone(TZ).strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _stamp_text(value: Any) -> str:
        """
        把 unix 时间戳转成北京时间文本（空值或非法值返回空串）。

        :param value: 时间戳
        :return str: ``YYYY-MM-DD HH:MM:SS`` 或空串
        """

        try:
            stamp = float(value or 0)
        except (TypeError, ValueError):
            return ""
        if stamp <= 0:
            return ""
        return datetime.fromtimestamp(stamp, TZ).strftime("%Y-%m-%d %H:%M:%S")

    def _today_rows(self) -> List[Dict[str, Any]]:
        """
        构造「今日签到状态」表行（每个目标一行，2026-10-11 加）。

        数据来自 ``state.json`` 的 ``signin_state``（``core/retry.py`` 维护）：
        今日是否成功 / 尝试次数 / 最后一次尝试时间 / 按重试间隔算出的下次重试时间。

        :return List[Dict[str, Any]]: 表格行
        """

        self._refresh_parsed_config()
        state = load_state(self.get_data_path())
        signin_state = dict(state.get("signin_state") or {})
        today = today_text()
        interval_seconds = max(0, int(self._retry_interval_hours)) * 3600
        rows: List[Dict[str, Any]] = []
        for target in self._targets:
            key = f"{target.account_key}|{target.bot_username}"
            record = dict(signin_state.get(key) or {})
            if record.get("date") != today:
                status, attempts, last_text, next_text = "—（今天还没跑）", "0", "", ""
            else:
                if record.get("ok_today"):
                    status = "✅ 已成功"
                elif record.get("ok") is False:
                    status = "❌ 上次失败"
                else:
                    status = "—（未完成）"
                attempts = str(int(record.get("attempts") or 0))
                last_text = self._stamp_text(record.get("last_attempt_at"))
                next_text = ""
                if (
                    interval_seconds
                    and record.get("ok") is False
                    and record.get("last_attempt_at")
                ):
                    next_text = self._stamp_text(
                        float(record["last_attempt_at"]) + interval_seconds
                    )
            rows.append(
                {
                    "component": "tr",
                    "content": [
                        {
                            "component": "td",
                            "text": f"{target.account_key} → {target.bot_username}",
                        },
                        {"component": "td", "text": status},
                        {"component": "td", "text": attempts},
                        {"component": "td", "text": last_text or "—"},
                        {"component": "td", "text": next_text or "—"},
                        {
                            "component": "td",
                            "content": [
                                self._button(
                                    "测试",
                                    "/target/probe",
                                    color="secondary",
                                    params={
                                        "account": target.account_key,
                                        "bot": target.bot_username,
                                    },
                                )
                            ],
                        },
                    ],
                }
            )
        if not rows:
            rows.append(
                {
                    "component": "tr",
                    "content": [
                        {
                            "component": "td",
                            "props": {"colspan": 6},
                            "text": "还没有配置签到目标",
                        },
                    ],
                }
            )
        return rows

    def get_page(self) -> Optional[List[dict]]:
        """
        返回插件详情页：状态、操作按钮与最近结果表。

        :return Optional[List[dict]]: 页面结构
        """
        if not self._enabled:
            return [
                {
                    "component": "VAlert",
                    "props": {"type": "info", "text": "插件未启用，请先在设置里启用并保存。"},
                }
            ]

        self._refresh_parsed_config()
        state = load_state(self.get_data_path())
        pending = load_pending(self.get_data_path())
        proxy = self._proxy_tuple()
        next_fire = self._next_fire_text()

        header: List[dict] = [
            {
                "component": "VAlert",
                "props": {
                    "type": "info",
                    "class": "mb-3",
                    "text": f"代理：{proxy_desc(proxy)}　|　账号：{len(self._accounts)} 个"
                            f"　|　签到目标：{len(self._targets)} 条　|　"
                            f"执行周期：{self._cron}　|　下次执行：{next_fire or '—'}"
                            f"　|　通知：{self._notify_label()}",
                },
            }
        ]
        if self._config_problems:
            header.append(
                {
                    "component": "VAlert",
                    "props": {
                        "type": "warning",
                        "class": "mb-3",
                        "text": "配置待修：" + "；".join(self._config_problems[:5]),
                    },
                }
            )
        if pending:
            waiting = [
                f"{account.display()}"
                for account in self._accounts
                if pending.get(account.key)
            ]
            if waiting:
                header.append(
                    {
                        "component": "VAlert",
                        "props": {
                            "type": "success",
                            "class": "mb-3",
                            "text": "验证码已发送，等待确认：" + "、".join(waiting)
                                    + "　—　把验证码填进配置页后点对应账号的「确认登录」",
                        },
                    }
                )
        header.append(
            {
                "component": "VAlert",
                "props": {
                    "type": "success" if state.get("last_summary", "").count("/") and
                    "0/" not in state.get("last_summary", "") else "secondary",
                    "class": "mb-3",
                    "text": f"最近一次：{state.get('last_run_at') or '未运行'}"
                            f"（{state.get('last_source') or '-'}）　{state.get('last_summary') or ''}",
                },
            }
        )
        login_event = state.get("last_login") or {}
        if login_event:
            header.append(
                {
                    "component": "VAlert",
                    "props": {
                        "type": "success" if login_event.get("ok") else "error",
                        "class": "mb-3",
                        "text": f"最近登录动作：{login_event.get('time', '')} "
                                f"{login_event.get('account', '')} "
                                f"{login_event.get('action', '')} —— "
                                f"{login_event.get('message', '')}",
                    },
                }
            )

        actions: List[dict] = []
        for account in self._accounts:
            actions.append(
                self._button(
                    f"发码 · {account.label}",
                    "/login/send_code",
                    color="primary",
                    params={"account": account.key},
                )
            )
            actions.append(
                self._button(
                    f"确认 · {account.label}",
                    "/login/confirm",
                    color="success",
                    params={"account": account.key},
                )
            )
        actions.extend(
            [
                self._button("立即签到", "/signin/run", color="primary"),
                self._button("连通性自检", "/selftest", color="secondary"),
                self._button("清空验证码/密码", "/login/reset", color="warning"),
            ]
        )
        # 按钮必须包在 flex 容器里再进内容流：直接与 VAlert/VTable 平铺时，
        # 移动端会出现按钮与上方色块重叠（2026-10-06 用户实测反馈）。
        actions_block = {
            "component": "div",
            "props": {"class": "d-flex align-center flex-wrap mt-2 mb-4"},
            "content": actions,
        }

        return [
            {
                "component": "div",
                "props": {"class": "pa-4"},
                "content": header
                + [actions_block]
                + [
                    self._group_header("账号登录状态"),
                    _table(["账号", "手机号", "状态"], self._login_status_rows()),
                    self._group_header("今日签到状态（按目标）"),
                    _table(
                        [
                            "目标",
                            "今日状态",
                            "尝试次数",
                            "最后尝试",
                            "下次重试",
                            "操作",
                        ],
                        self._today_rows(),
                    ),
                    self._group_header(f"最近 {PAGE_RESULT_LIMIT} 条签到结果"),
                    _table(
                        [
                            "时间",
                            "账号",
                            "bot",
                            "状态",
                            "回复/错误",
                            "AI 复核",
                            "操作",
                        ],
                        self._result_rows(),
                    ),
                ]
                + self._ai_keyword_block(),
            }
        ]

    # ==================== API ====================

    def get_api(self) -> List[Dict[str, Any]]:
        """
        返回插件 API 列表。

        :return List[Dict[str, Any]]: API 定义列表
        """
        return [
            {
                "path": "/status",
                "endpoint": self.api_status,
                "methods": ["GET"],
                "summary": "查询插件状态",
                "description": "返回配置摘要、账号登录状态与最近签到结果。",
            },
            {
                "path": "/login/send_code",
                "endpoint": self.api_send_code,
                "methods": ["GET", "POST"],
                "summary": "阶段一：发送登录验证码",
                "description": "对配置里选中的账号发送 Telegram 登录验证码。",
            },
            {
                "path": "/login/confirm",
                "endpoint": self.api_confirm_login,
                "methods": ["GET", "POST"],
                "summary": "阶段二：确认登录",
                "description": "用配置里填写的验证码（含可选两步验证密码）完成登录。",
            },
            {
                "path": "/login/reset",
                "endpoint": self.api_login_reset,
                "methods": ["GET", "POST"],
                "summary": "清空验证码与两步验证密码",
                "description": "清掉配置里暂存的验证码/密码与待登录状态。",
            },
            {
                "path": "/signin/run",
                "endpoint": self.api_signin,
                "methods": ["GET", "POST"],
                "summary": "立即签到",
                "description": "立即执行签到，可用 account/bot 参数限定范围。",
            },
            {
                "path": "/selftest",
                "endpoint": self.api_selftest,
                "methods": ["GET", "POST"],
                "summary": "连通性自检",
                "description": "用公开应用凭据连接一次 Telegram，验证代理与网络。",
            },
            {
                "path": "/logout",
                "endpoint": self.api_logout,
                "methods": ["GET", "POST"],
                "summary": "退出登录（两阶段）",
                "description": "不带 confirm=true 时只做确认提示；带 confirm=true 才删除 session。",
            },
            {
                "path": "/target/probe",
                "endpoint": self.api_probe_target,
                "methods": ["GET", "POST"],
                "summary": "测试单个签到目标",
                "description": "发一次 /start（或命令）并回读实际看到的按钮文案，用于修正「按钮文字」。",
            },
        ]

    @staticmethod
    async def _read_params(request: Request) -> Dict[str, Any]:
        """
        合并读取查询参数与 JSON 请求体。

        :param request: FastAPI 请求对象
        :return Dict[str, Any]: 参数字典
        """
        params: Dict[str, Any] = dict(request.query_params)
        try:
            body = await request.json()
        except Exception:  # pylint: disable=broad-except
            body = None
        if isinstance(body, dict):
            for key, value in body.items():
                params.setdefault(key, value)
        return params

    def _find_account(self, key: str) -> Optional[AccountConfig]:
        """
        按标识查找账号。

        :param key: 账号标识
        :return Optional[AccountConfig]: 找到的账号配置
        """
        wanted = (key or "").strip()
        if not wanted and self._accounts:
            wanted = self._accounts[0].key
        return next((account for account in self._accounts if account.key == wanted), None)

    async def api_status(self, request: Request) -> Dict[str, Any]:
        """
        查询插件状态。

        :param request: FastAPI 请求对象
        :return Dict[str, Any]: 统一响应结构
        """
        del request
        self._refresh_parsed_config()
        state = load_state(self.get_data_path())
        return {
            "success": True,
            "message": state.get("last_summary") or "",
            "data": {
                "enabled": self._enabled,
                "cron": self._cron,
                "proxy": proxy_desc(
                    self._proxy_tuple()
                ),
                "problems": self._config_problems,
                "accounts": [
                    account.display() for account in self._accounts
                ],
                "targets": len(self._targets),
                "last_run_at": state.get("last_run_at", ""),
                "last_source": state.get("last_source", ""),
                "results": recent_results(state, PAGE_RESULT_LIMIT),
            },
        }

    async def api_send_code(self, request: Request) -> Dict[str, Any]:
        """
        阶段一：给指定账号发送登录验证码。

        后台执行：接口立刻返回，避免前台进度条；结果写状态并按需通知。

        :param request: FastAPI 请求对象
        :return Dict[str, Any]: 统一响应结构
        """
        if not self._enabled:
            return {"success": False, "message": "插件未启用", "data": None}
        params = await self._read_params(request)
        account = self._find_account(params.get("account"))
        if account is None:
            return {"success": False, "message": "找不到要登录的账号，请先配置", "data": None}
        if params.get("phone"):
            account.phone = str(params["phone"])
        if not self._spawn_background(
            lambda: self._run_sync(lambda: self._do_send_code(account)),
            f"send-code-{account.key}",
        ):
            return {
                "success": False,
                "message": f"{account.display()} 的发码任务已在运行，稍后看详情页状态",
                "data": {"account": account.key},
            }
        return {
            "success": True,
            "message": f"已在后台给 {account.display()} 发送验证码；收到后填进该账号的"
                       f"「登录验证码」并把「登录动作」选成「确认登录」再保存",
            "data": {"account": account.key, "background": True},
        }

    async def _do_send_code(self, account: AccountConfig) -> None:
        """
        后台执行发码，并把结果写进状态（失败发通知）。

        :param account: 账号配置
        :return None
        """

        result = await send_code(self.get_data_path(), account, self._proxy_tuple())
        ok = bool(result.get("ok"))
        message = str(result.get("message") or "")
        record_login_event(
            self.get_data_path(), account.key, LOGIN_ACTION_SEND, ok, message
        )
        logger.info("【TgSignin】发送验证码：%s → %s", account.key, message)
        if not ok:
            self._notify_login_failure(account, LOGIN_ACTION_SEND, message)

    async def api_confirm_login(self, request: Request) -> Dict[str, Any]:
        """
        阶段二：用验证码（含可选两步验证密码）完成登录。

        后台执行：接口立刻返回；验证码优先取请求参数，其次取该账号槽里的字段。

        :param request: FastAPI 请求对象
        :return Dict[str, Any]: 统一响应结构
        """
        if not self._enabled:
            return {"success": False, "message": "插件未启用", "data": None}
        params = await self._read_params(request)
        account = self._find_account(params.get("account"))
        if account is None:
            return {"success": False, "message": "找不到要登录的账号，请先配置", "data": None}
        slot = self._slot_login(account.key)
        code = str(params.get("code") or slot.get("code") or "").strip()
        password = str(params.get("password") or slot.get("password") or "")
        if not code:
            return {
                "success": False,
                "message": "验证码为空：请先把验证码填进该账号的「登录验证码」并保存",
                "data": {"account": account.key},
            }
        if not self._spawn_background(
            lambda: self._run_sync(
                lambda: self._do_confirm_login(account, code, password)
            ),
            f"confirm-{account.key}",
        ):
            return {
                "success": False,
                "message": f"{account.display()} 的确认登录已在运行，稍后看详情页状态",
                "data": {"account": account.key},
            }
        return {
            "success": True,
            "message": f"已在后台确认 {account.display()} 的登录，稍后到详情页看状态",
            "data": {"account": account.key, "background": True},
        }

    async def _do_confirm_login(
        self, account: AccountConfig, code: str, password: str
    ) -> None:
        """
        后台执行确认登录，并把结果写进状态（失败发通知）。

        :param account: 账号配置
        :param code: 登录验证码
        :param password: 两步验证密码（可为空）
        :return None
        """

        result = await confirm_login(
            self.get_data_path(), account, code, password, self._proxy_tuple()
        )
        ok = bool(result.get("ok"))
        message = str(result.get("message") or "")
        if ok:
            record_login(self.get_data_path(), account.key, result.get("me") or {})
            # 登录成功后立刻清掉该槽的一次性凭据（验证码 / 两步验证密码 / 动作），
            # 避免长期明文留在插件配置里（2026-10-11 加）
            self._clear_login_slot(account.key)
        record_login_event(
            self.get_data_path(), account.key, LOGIN_ACTION_CONFIRM, ok, message
        )
        logger.info("【TgSignin】确认登录：%s → %s", account.key, message)
        if not ok:
            self._notify_login_failure(account, LOGIN_ACTION_CONFIRM, message)

    def _logout_account(self, account: AccountConfig) -> Dict[str, Any]:
        """
        删除某个账号的 session 与登录记录（配置页「退出登录」动作与详情页 API 共用）。

        :param account: 账号配置
        :return Dict[str, Any]: ``{ok, message}``
        """

        removed = delete_session(self.get_data_path(), account.key)
        clear_pending(self.get_data_path(), account.key)
        state = load_state(self.get_data_path())
        (state.get("accounts") or {}).pop(account.key, None)
        save_state(self.get_data_path(), state)
        return {
            "ok": True,
            "message": f"已退出 {account.display()}"
            f"（{'已删除 session' if removed else '本就没有 session'}）",
        }

    @staticmethod
    def _logout_button_js(index: int) -> str:
        """
        生成配置表单「退出登录」按钮的前端脚本（原生确认弹窗 + 回填「登录动作」）。

        配置表单渲染器只认 ``props.on*`` 字符串脚本，且在 ``with(model)`` 作用域内以
        ``(value)(event)`` 求值（``events`` 在配置表单里不生效，只有详情页按钮支持），
        所以这里用浏览器原生 ``confirm`` 做二次确认，再把该账号槽的「登录动作」填成
        「退出登录」——**点完仍需按「保存」才会真正执行**。

        :param index: 账号槽序号（1 起）
        :return str: 可直接放进 ``props.onClick`` 的 JS 函数源码
        """

        guard = json.dumps(
            f"将退出「账号 {index}」（acc{index}）：\n"
            "删除该账号的 Telegram 登录态，之后需要重新登录才能签到。\n"
            "（这一步只是把「登录动作」填成「退出登录」，点完仍需按「保存」才会执行）\n"
            "确定退出？",
            ensure_ascii=False,
        )
        value = json.dumps(LOGIN_ACTION_LOGOUT, ensure_ascii=False)
        model = f"model.account_{index}_login_action"
        return (
            "function () { if (!confirm("
            + guard
            + ")) { return; } "
            + model
            + " = "
            + value
            + "; }"
        )

    def _clear_login_slot(self, account_key: str) -> None:
        """
        清空某个账号槽的一次性登录字段（登录动作 / 验证码 / 两步验证密码）。

        :param account_key: 账号标识（``acc1``…）
        :return None
        """

        payload = dict(self._raw_config)
        changed = False
        for index in range(1, MAX_ACCOUNT_SLOTS + 1):
            if f"acc{index}" != account_key:
                continue
            if (
                payload.get(f"account_{index}_login_action") != LOGIN_ACTION_NONE
                or payload.get(f"account_{index}_login_code")
                or payload.get(f"account_{index}_login_password")
            ):
                changed = True
            payload[f"account_{index}_login_action"] = LOGIN_ACTION_NONE
            payload[f"account_{index}_login_code"] = ""
            payload[f"account_{index}_login_password"] = ""
        if not changed:
            return
        self._raw_config = payload
        self.update_config(payload)

    def _notify_login_failure(
        self, account: AccountConfig, action: str, message: str
    ) -> None:
        """
        登录动作失败时发 MP 通知（后台执行的结果用户看不到前台提示）。

        :param account: 账号配置
        :param action: 动作名
        :param message: 失败原因
        :return None
        """

        try:
            self.post_message(
                mtype=MessageType.Plugin,
                title="【Telegram 登录】",
                text=f"{account.display()} {action} 失败：{message}",
            )
        except Exception as error:  # pylint: disable=broad-except
            logger.error("【TgSignin】登录失败通知发送出错：%s", error)

    async def api_login_reset(self, request: Request) -> Dict[str, Any]:
        """
        清空暂存的验证码/两步验证密码，并丢弃待登录状态。

        :param request: FastAPI 请求对象
        :return Dict[str, Any]: 统一响应结构
        """
        params = await self._read_params(request)
        account = self._find_account(params.get("account"))
        if account is not None:
            clear_pending(self.get_data_path(), account.key)
        payload = dict(self._raw_config)
        payload.update(
            {
                "enabled": self._enabled,
                "cron": self._cron,
                "notify_mode": self._notify_mode,
                "retry_interval_hours": self._retry_interval_hours,
                "success_keywords": "|".join(self._success_keywords),
                "repeated_keywords": "|".join(self._repeated_keywords),
                "failure_keywords": "|".join(self._failure_keywords),
                # 写回配置里真实的值：不要用合并后的 judge.enabled
                # （任一 AI 开关为真它都是 True，会把「只开了 AI 归纳」变成「AI 复核也开」）
                "ai_confirm_enabled": _truthy(
                    self._raw_config.get("ai_confirm_enabled")
                ),
                "concurrency": self._concurrency,
                "ai_keyword_autofill": self._ai_keyword_autofill,
                "use_text_mode": self._use_text_mode,
            }
        )
        for index in range(1, MAX_ACCOUNT_SLOTS + 1):
            payload[f"account_{index}_login_action"] = LOGIN_ACTION_NONE
            payload[f"account_{index}_login_code"] = ""
            payload[f"account_{index}_login_password"] = ""
        self.update_config(payload)
        self._raw_config = payload
        return {"success": True, "message": "已清空验证码/密码与待登录状态", "data": None}

    async def api_signin(self, request: Request) -> Dict[str, Any]:
        """
        立即执行一次签到（可限定账号或 bot）。

        后台执行：接口立刻返回，避免前台进度条；结果写状态并按需通知。

        :param request: FastAPI 请求对象
        :return Dict[str, Any]: 统一响应结构
        """
        if not self._enabled:
            return {"success": False, "message": "插件未启用", "data": None}
        params = await self._read_params(request)
        only_account = params.get("account") or None
        only_bot = params.get("bot") or None
        if not self._spawn_background(
            lambda: self._run_sync(
                lambda: self._signin(
                    source="手动", only_account=only_account, only_bot=only_bot
                )
            ),
            "signin",
        ):
            return {
                "success": False,
                "message": "已有签到任务在运行，请稍等到详情页看结果",
                "data": None,
            }
        return {
            "success": True,
            "message": "已在后台执行签到，稍后到详情页看结果（失败会通知）",
            "data": {"background": True},
        }

    async def api_selftest(self, request: Request) -> Dict[str, Any]:
        """
        用公开应用凭据做一次 Telegram 连通性自检。

        :param request: FastAPI 请求对象
        :return Dict[str, Any]: 统一响应结构
        """
        del request
        proxy = self._proxy_tuple()
        result = await connection_selftest(self.get_data_path(), proxy)
        return {
            "success": bool(result.get("ok")),
            "message": result.get("message", ""),
            "data": {"proxy": proxy_desc(proxy)},
        }

    def _targets_hint(self) -> str:
        """
        返回「可用账号 / bot」提示串（命令参数写错时展示）。

        :return str: 形如 ``账号 acc1、acc2；bot @example_bot_b、@example_bot_a``
        """

        self._refresh_parsed_config()
        accounts = "、".join(account.key for account in self._accounts) or "（未配置账号）"
        bots = (
            "、".join(target.bot_username for target in self._targets if target.enabled)
            or "（未配置目标）"
        )
        return f"账号 {accounts}；bot {bots}"

    async def api_probe_target(self, request: Request) -> Dict[str, Any]:
        """
        测试单个签到目标：发一次 /start（或命令）并回读实际看到的按钮文案。

        :param request: FastAPI 请求对象
        :return Dict[str, Any]: 统一响应结构
        """

        if not self._enabled:
            return {"success": False, "message": "插件未启用", "data": None}
        params = await self._read_params(request)
        account = self._find_account(params.get("account"))
        if account is None:
            return {
                "success": False,
                "message": f"找不到账号 {params.get('account')}",
                "data": {"hint": self._targets_hint()},
            }
        bot = str(params.get("bot") or "")
        target = next(
            (
                item
                for item in self._targets
                if item.account_key == account.key
                and normalize_bot(item.bot_username) == normalize_bot(bot)
            ),
            None,
        )
        if target is None:
            return {
                "success": False,
                "message": f"找不到目标 {bot}",
                "data": {"hint": self._targets_hint()},
            }
        result = await probe_target(
            target, self.get_data_path(), account, self._proxy_tuple()
        )
        return {
            "success": bool(result.get("ok")),
            "message": str(result.get("message") or ""),
            "data": {"buttons": result.get("buttons") or []},
        }

    @staticmethod
    def get_dashboard_meta() -> Optional[List[Dict[str, str]]]:
        """
        声明插件仪表盘（首页卡片）。

        :return Optional[List[Dict[str, str]]]: 仪表盘 key 与名称
        """

        return [{"key": "main", "name": "签到概览"}]

    def get_dashboard(
        self, key: str = "", **kwargs: Any
    ) -> Optional[Tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]]]]:
        """
        返回首页仪表盘内容（今日完成数 / 最近一次 / 失败目标）。

        :param key: 仪表盘 key（缺省返回固定的主卡片）
        :param kwargs: 宿主附加上下文（浏览器 UA 等）
        :return Optional[Tuple[Dict, Dict, List]]: ``(col 配置, 全局配置, 页面元素)``
        """

        del key, kwargs
        if not self._enabled:
            return None
        self._refresh_parsed_config()
        state = load_state(self.get_data_path())
        signin_state = dict(state.get("signin_state") or {})
        today = today_text()
        done = 0
        failed: List[str] = []
        for target in self._targets:
            record = dict(
                signin_state.get(f"{target.account_key}|{target.bot_username}") or {}
            )
            if record.get("date") != today:
                continue
            if record.get("ok_today"):
                done += 1
            elif record.get("ok") is False:
                failed.append(target.bot_username)
        elements: List[Dict[str, Any]] = [
            {
                "component": "div",
                "props": {"class": "text-h6"},
                "text": f"今日 {done}/{len(self._targets)} 个目标已完成",
            },
            {
                "component": "div",
                "props": {"class": "text-caption"},
                "text": f"最近一次：{state.get('last_run_at') or '未运行'}　"
                f"{state.get('last_summary') or ''}",
            },
        ]
        if failed:
            elements.append(
                {
                    "component": "div",
                    "props": {"class": "text-caption text-error"},
                    "text": "失败目标：" + "、".join(failed),
                }
            )
        return (
            {"cols": 12, "md": 6},
            {"refresh": 0, "title": "Telegram 签到", "subtitle": "今日概览"},
            elements,
        )

    async def api_logout(self, request: Request) -> Dict[str, Any]:
        """
        退出登录：两阶段确认后删除 session 文件。

        :param request: FastAPI 请求对象
        :return Dict[str, Any]: 统一响应结构
        """
        params = await self._read_params(request)
        account = self._find_account(params.get("account"))
        if account is None:
            return {"success": False, "message": "找不到账号", "data": None}
        if str(params.get("confirm", "")).lower() not in ("1", "true", "yes"):
            return {
                "success": False,
                "message": f"将删除 {account.display()} 的 session（需要重新登录才能签到）。"
                           f"确认请带 confirm=true 再调用一次。",
                "data": {"need_confirm": True, "account": account.key},
            }
        result = self._logout_account(account)
        return {
            "success": True,
            "message": str(result.get("message") or ""),
            "data": {"account": account.key},
        }

    # ==================== 签到执行 ====================

    @staticmethod
    def _run_in_thread(factory: Callable[[], Any]) -> Any:
        """
        在独立线程里跑协程并等待结果（供同步入口调用）。

        :param factory: 返回协程的零参函数
        :return Any: 协程返回值
        """
        box: Dict[str, Any] = {}

        def worker() -> None:
            """线程体：独立事件循环执行协程。"""
            try:
                box["value"] = asyncio.run(factory())
            except Exception as error:  # pylint: disable=broad-except
                box["error"] = error

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        thread.join()
        if "error" in box:
            raise box["error"]
        return box.get("value")

    def _run_sync(self, factory: Callable[[], Any]) -> Any:
        """
        在同步上下文执行协程（无运行中的事件循环时直接用 asyncio.run）。

        :param factory: 返回协程的零参函数
        :return Any: 协程返回值
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(factory())
        return self._run_in_thread(factory)

    async def _signin(
        self,
        source: str,
        only_account: Optional[str] = None,
        only_bot: Optional[str] = None,
        only_targets: Optional[Sequence[Any]] = None,
    ) -> Dict[str, Any]:
        """
        执行签到并按需通知失败。

        :param source: 触发来源（定时/手动/命令）
        :param only_account: 只跑该账号
        :param only_bot: 只跑该 bot
        :param only_targets: 只跑给定的目标集合（失败重试用），None 表示全部
        :return Dict[str, Any]: 统一响应结构
        """
        self._refresh_parsed_config()
        if not self._accounts:
            return {"success": False, "message": "没有配置任何账号", "data": None}

        proxy = self._proxy_tuple()
        logger.info(
            "【TgSignin】开始签到：来源=%s 账号=%s 代理=%s",
            source,
            only_account or "全部",
            proxy_desc(proxy),
        )
        # AI 复核/归纳：两个开关任一开启才传入（都关时完全不走 AI，零开销）
        from functools import partial  # pylint: disable=import-outside-toplevel

        ai_judge = (
            partial(self._ai_judge.judge, want_keywords=self._ai_keyword_autofill)
            if self._ai_judge.enabled
            else None
        )
        results = await run_all(
            self._accounts,
            self._targets,
            self.get_data_path(),
            proxy,
            only_account=only_account,
            only_bot=only_bot,
            only_targets=only_targets,
            success_keywords=self._success_keywords,
            repeated_keywords=self._repeated_keywords,
            failure_keywords=self._failure_keywords,
            ai_judge=ai_judge,
            concurrency=self._concurrency,
        )
        summary = summarize_results(results)
        record_run(self.get_data_path(), results, source, summary)
        # AI 自动归纳关键词：写回词表（只增不删、写入前查重）并记审计
        self._apply_ai_keywords(results)
        for item in results:
            logger.info(
                "【TgSignin】%s %s → %s %s｜状态=%s%s",
                "✅" if item.get("ok") else "❌",
                item.get("account"),
                item.get("bot"),
                str(item.get("reply") or item.get("error") or "（无返回内容）")[:120],
                item.get("status") or "",
                f"｜弹窗={str(item.get('alert'))[:60]}" if item.get("alert") else "",
            )
        self._notify_results(results, source)
        return {"success": True, "message": summary, "data": {"results": results}}

    def _apply_ai_keywords(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        把 AI 归纳出的关键词写回词表（**只增不删**，写入前查重）。

        规则（2026-10-08 用户定案）：

        - 只处理本轮**真的判出档位**（``ai_verdict``）的结果；
        - 候选词先与**本栏及另外两栏**的现有词查重，已存在则跳过 —— 绝不重复添加；
        - 长度/纯数字/过泛词/「必须逐字出现在回复里」的校验由 AI 解析层负责；
        - 有新增时立刻持久化配置并热更新内存词表，新增记录写进状态文件备查（详情页可回看）。

        :param results: 本次签到结果（含 ai_verdict / ai_keywords）
        :return List[Dict[str, Any]]: 本次实际新增的审计记录（无新增则为空列表）
        """

        from .core.store import (  # pylint: disable=import-outside-toplevel
            record_ai_keywords,
        )

        if not self._ai_keyword_autofill or not results:
            return []
        current = {
            "success": list(self._success_keywords),
            "repeated": list(self._repeated_keywords),
            "failure": list(self._failure_keywords),
        }
        candidates: Dict[str, List[Dict[str, Any]]] = {
            "success": [],
            "repeated": [],
            "failure": [],
        }
        for item in results:
            verdict = str(item.get("ai_verdict") or "")
            if verdict not in candidates:
                continue
            for word in item.get("ai_keywords") or []:
                candidates[verdict].append(
                    {
                        "word": str(word),
                        "account": item.get("account"),
                        "bot": item.get("bot"),
                    }
                )
        if not any(candidates.values()):
            return []

        stamp = now_text()
        audit: List[Dict[str, Any]] = []
        for verdict, items in candidates.items():
            words = [entry["word"] for entry in items]
            if not words:
                continue
            # 查重：本栏既有词 + 另外两栏的全部词（避免同词跨档位重复）
            blocked = [
                word
                for key, values in current.items()
                if key != verdict
                for word in values
            ]
            merged, added = merge_keywords(
                current[verdict], words, KEYWORD_LIST_LIMIT, blocked
            )
            if not added:
                continue
            current[verdict] = merged
            for word in added:
                entry = next(
                    (candidate for candidate in items if candidate["word"] == word), {}
                )
                audit.append(
                    {
                        "time": stamp,
                        "account": entry.get("account"),
                        "bot": entry.get("bot"),
                        "verdict": verdict,
                        "keyword": word,
                    }
                )
        if not audit:
            return []

        self._success_keywords = current["success"]
        self._repeated_keywords = current["repeated"]
        self._failure_keywords = current["failure"]
        # 持久化：沿用本插件既有的「整份 payload + 局部覆盖」写法（含热更新 _raw_config）
        payload = dict(self._raw_config)
        payload.update(
            {
                "success_keywords": "|".join(self._success_keywords),
                "repeated_keywords": "|".join(self._repeated_keywords),
                "failure_keywords": "|".join(self._failure_keywords),
            }
        )
        self._raw_config = payload
        self.update_config(payload)
        record_ai_keywords(self.get_data_path(), audit)
        logger.info(
            "【TgSignin】AI 归纳关键词新增 %d 个：%s",
            len(audit),
            "、".join(f"{item['verdict']}·{item['keyword']}" for item in audit),
        )
        return audit

    def _notify_results(self, results: List[Dict[str, Any]], source: str) -> None:
        """
        按「通知方式」生成并发送签到通知（成功/失败/都发/不发）。

        :param results: 本次签到结果
        :param source: 触发来源（定时/手动/命令）
        :return None
        """

        text = build_notify_text(results, source, self._notify_mode)
        if not text:
            return
        try:
            self.post_message(
                mtype=MessageType.Plugin,
                title="【Telegram 自动签到】",
                text=text,
            )
        except Exception as error:  # pylint: disable=broad-except
            logger.error("【TgSignin】发送签到通知出错：%s", error)

    def scheduled_signin(self) -> None:
        """
        定时服务入口（同步）：执行一次全量签到并记录日志。

        :return None
        """
        if not self._enabled:
            return
        try:
            self._run_sync(lambda: self._signin(source="定时"))
        except Exception as error:  # pylint: disable=broad-except
            logger.error("【TgSignin】定时签到失败：%s", error)

    def scheduled_retry(self) -> None:
        """
        失败重试服务入口（同步）：只重跑今天失败且已到重试间隔的目标。

        :return None
        """

        if not self._enabled or self._retry_interval_hours <= 0:
            return
        try:
            due, _ = evaluate_retry(
                self._targets,
                load_state(self.get_data_path()),
                retry_interval_hours=self._retry_interval_hours,
            )
            if not due:
                return
            logger.info(
                "【TgSignin】失败重试：%d 个目标到期（间隔 %d 小时）",
                len(due),
                self._retry_interval_hours,
            )
            self._run_sync(lambda: self._signin(source="失败重试", only_targets=due))
        except Exception as error:  # pylint: disable=broad-except
            logger.error("【TgSignin】失败重试异常：%s", error)

    # ==================== 命令 ====================

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """
        返回插件远程命令列表。

        :return List[Dict[str, Any]]: 命令定义
        """
        return [
            {
                "cmd": "/tgsignin",
                "event": EventType.PluginAction,
                "desc": "立即执行 Telegram 签到（可用参数限定账号或 bot）",
                "category": "",
                "data": {"action": "tgsignin_run"},
            },
            {
                "cmd": "/tglogin",
                "event": EventType.PluginAction,
                "desc": "登录 Telegram：/tglogin <账号标识> <验证码> [两步验证密码]",
                "category": "",
                "data": {"action": "tgsignin_login"},
            },
            {
                "cmd": "/tgstatus",
                "event": EventType.PluginAction,
                "desc": "查看 Telegram 签到状态与最近结果",
                "category": "",
                "data": {"action": "tgsignin_status"},
            },
        ]

    @eventmanager.register(EventType.PluginAction)
    def handle_plugin_action(self, event: Event) -> None:
        """
        处理插件命令（/tgsignin、/tglogin、/tgstatus）。

        :param event: 事件对象
        :return None
        """
        if not event or not event.event_data:
            return
        data = event.event_data
        action = str(data.get("action") or "")
        if not action.startswith("tgsignin_"):
            return
        channel = data.get("channel")
        source = data.get("source")
        userid = data.get("userid") or data.get("user")

        if action == "tgsignin_run":
            answer = "参数用法：/tgsignin [账号标识] [bot 用户名]；不带参数即全量签到"
            args = str(data.get("arg_str") or "").split()
            if len(args) > 2:
                self.post_message(channel=channel, source=source, userid=userid,
                                  title="Telegram 签到", text=answer)
                return
            only_account = args[0] if args else None
            only_bot = args[1] if len(args) > 1 else None
            try:
                result = self._run_sync(
                    lambda: self._signin(
                        source="命令", only_account=only_account, only_bot=only_bot
                    )
                )
                text = str(result.get("message") or "")
                data_block = result.get("data") or {}
                if not (data_block.get("results") or []):
                    # 参数写错（如漏了 @、或账号不存在）时把可用值列出来，
                    # 别只回一句「没有可执行的目标」（2026-10-11 加）
                    text = f"{text}；可用：{self._targets_hint()}"
            except Exception as error:  # pylint: disable=broad-except
                text = f"执行失败：{type(error).__name__}: {error}"
            self.post_message(
                channel=channel,
                source=source,
                userid=userid,
                title="【Telegram 自动签到】",
                text=text,
            )
            return

        if action == "tgsignin_login":
            args = str(data.get("arg_str") or "").split()
            if len(args) < 2:
                self.post_message(
                    channel=channel,
                    source=source,
                    userid=userid,
                    title="Telegram 登录",
                    text="用法：/tglogin <账号标识> <验证码> [两步验证密码]；"
                         "也可以先到插件详情页点「发送验证码」。",
                )
                return
            account = self._find_account(args[0])
            if account is None:
                self.post_message(channel=channel, source=source, userid=userid,
                                  title="Telegram 登录", text=f"找不到账号 {args[0]}")
                return
            code = args[1]
            password = args[2] if len(args) > 2 else ""

            async def _flow() -> Dict[str, Any]:
                """先发码（若需要）再确认登录的命令式流程。"""
                pending = load_pending(self.get_data_path()).get(account.key) or {}
                if not pending.get("phone_code_hash"):
                    sent = await send_code(
                        self.get_data_path(),
                        account,
                        self._proxy_tuple(),
                    )
                    if not sent.get("ok"):
                        return sent
                return await confirm_login(
                    self.get_data_path(),
                    account,
                    code,
                    password,
                    self._proxy_tuple(),
                )

            try:
                result = self._run_sync(_flow)
            except Exception as error:  # pylint: disable=broad-except
                result = {"ok": False, "message": f"登录异常：{type(error).__name__}: {error}"}
            if result.get("ok"):
                record_login(self.get_data_path(), account.key, result.get("me") or {})
            self.post_message(
                channel=channel,
                source=source,
                userid=userid,
                title="Telegram 登录",
                text=str(result.get("message") or ""),
            )
            return

        if action == "tgsignin_status":
            self._refresh_parsed_config()
            state = load_state(self.get_data_path())
            lines = [
                f"账号 {len(self._accounts)} 个 / 目标 {len(self._targets)} 条",
                f"最近一次：{state.get('last_run_at') or '未运行'}"
                f"（{state.get('last_source') or '-'}）{state.get('last_summary') or ''}",
            ]
            for item in recent_results(state, 10):
                lines.append(
                    f"{'✅' if item.get('ok') else '❌'} {item.get('time', '')} "
                    f"{item.get('account')} → {item.get('bot')} "
                    f"{str(item.get('reply') or item.get('error') or '')[:60]}"
                )
            self.post_message(
                channel=channel,
                source=source,
                userid=userid,
                title="【Telegram 签到状态】",
                text="\n".join(lines),
            )

    # ==================== 定时服务 ====================

    def get_service(self) -> List[Dict[str, Any]]:
        """
        注册插件定时服务（cron 签到）。

        :return List[Dict[str, Any]]: 服务定义
        """
        if not self._cron:
            return []
        try:
            trigger = CronTrigger.from_crontab(self._cron)
        except ValueError:
            logger.error("【TgSignin】cron 表达式非法，已跳过定时服务：%s", self._cron)
            return []
        services: List[Dict[str, Any]] = [
            {
                "id": "tgsignin_daily",
                "name": "Telegram 自动签到",
                "trigger": trigger,
                "func": self.scheduled_signin,
                "kwargs": {},
            }
        ]
        if self._retry_interval_hours > 0:
            # 失败重试：每小时检查一次，只跑「今天失败且已到重试间隔」的目标
            services.append(
                {
                    "id": "tgsignin_retry",
                    "name": "Telegram 签到失败重试",
                    "trigger": CronTrigger.from_crontab("0 * * * *"),
                    "func": self.scheduled_retry,
                    "kwargs": {},
                }
            )
        return services
