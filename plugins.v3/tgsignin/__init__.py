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
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

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
    AccountConfig,
    BotTarget,
    accounts_to_text,
    parse_accounts,
    parse_targets,
    targets_to_text,
    validate_config,
)
from .core.login import clear_pending, confirm_login, load_pending, send_code
from .core.session import (
    build_proxy,
    connection_selftest,
    delete_session,
    proxy_desc,
    session_files,
)
from .core.signin import now_text, run_all, summarize_results
from .core.store import (
    PAGE_RESULT_LIMIT,
    load_state,
    recent_results,
    record_login,
    record_run,
)
from .version import VERSION

__all__ = ["TgSignin"]


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
    _notify_on_failure: bool = True
    _accounts_text: str = DEFAULT_ACCOUNTS_TEXT
    _targets_text: str = DEFAULT_TARGETS_TEXT
    _login_account: str = ""
    _login_code: str = ""
    _login_password: str = ""
    _proxy_type: str = "socks5"
    _proxy_host: str = "192.0.2.94"
    _proxy_port: int = 7893
    _api_id: int = DEFAULT_API_ID
    _api_hash: str = DEFAULT_API_HASH
    # 解析后的配置（每次 init_plugin 刷新）
    _accounts: List[AccountConfig] = []
    _targets: List[BotTarget] = []
    _config_problems: List[str] = []

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
        self._notify_on_failure = True
        self._accounts_text = DEFAULT_ACCOUNTS_TEXT
        self._targets_text = DEFAULT_TARGETS_TEXT
        self._login_account = ""
        self._login_code = ""
        self._login_password = ""
        self._proxy_type = "socks5"
        self._proxy_host = "192.0.2.94"
        self._proxy_port = 7893
        self._api_id = DEFAULT_API_ID
        self._api_hash = DEFAULT_API_HASH

        if config:
            self._enabled = bool(config.get("enabled"))
            self._cron = str(config.get("cron") or "0 9 * * *")
            self._notify_on_failure = bool(config.get("notify_on_failure", True))
            self._accounts_text = str(config.get("accounts_text") or DEFAULT_ACCOUNTS_TEXT)
            self._targets_text = str(config.get("targets_text") or DEFAULT_TARGETS_TEXT)
            self._login_account = str(config.get("login_account") or "")
            self._login_code = str(config.get("login_code") or "")
            self._login_password = str(config.get("login_password") or "")
            self._proxy_type = str(config.get("proxy_type") or "socks5")
            self._proxy_host = str(config.get("proxy_host") or "")
            try:
                self._proxy_port = int(config.get("proxy_port") or 7893)
            except (TypeError, ValueError):
                self._proxy_port = 7893
            try:
                self._api_id = int(config.get("api_id") or DEFAULT_API_ID)
            except (TypeError, ValueError):
                self._api_id = DEFAULT_API_ID
            self._api_hash = str(config.get("api_hash") or DEFAULT_API_HASH)

        self._refresh_parsed_config()

    def _refresh_parsed_config(self) -> None:
        """
        重新解析账号与签到目标配置，并刷新校验问题列表。

        :return None
        """
        self._accounts = parse_accounts(self._accounts_text, self._api_id, self._api_hash)
        self._targets = parse_targets(self._targets_text)
        self._config_problems = validate_config(self._accounts, self._targets)
        for problem in self._config_problems:
            logger.warning("【TgSignin】配置问题：%s", problem)

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
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "enabled",
                                            "label": "启用插件",
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
                                            "model": "cron",
                                            "label": "定时签到（cron）",
                                            "placeholder": "0 9 * * *",
                                            "persistent-hint": True,
                                            "hint": "五段式 cron，默认每天 09:00 签到一次",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "notify_on_failure",
                                            "label": "失败时通知",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    self._group_header("账号", "一行一个；改完保存后详情页的账号下拉会同步刷新"),
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VTextarea",
                                        "props": {
                                            "model": "accounts_text",
                                            "label": "Telegram 账号列表",
                                            "rows": 4,
                                            "persistent-hint": True,
                                            "hint": "格式：标识 | 显示名 | 手机号（含国际区号）。"
                                                    "标识只允许小写字母/数字/_/-，会用作 session 文件名。"
                                                    "需要单独指定 api_id/api_hash 时，在行尾再补两段即可。",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    self._group_header("签到目标", "每个账号可配多个 bot；按钮式与命令式二选一"),
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VTextarea",
                                        "props": {
                                            "model": "targets_text",
                                            "label": "签到目标列表",
                                            "rows": 6,
                                            "persistent-hint": True,
                                            "hint": "格式：账号标识 | bot用户名 | 按钮或命令 | 按钮文字或命令 [| 等待秒数]。"
                                                    "例：acc1 | @okemby_bot | 按钮 | 签到　/　acc1 | @HDHaven_Bot | 命令 | /checkin",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    self._group_header("登录（两阶段）", "先保存配置，再到详情页点按钮"),
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "login_account",
                                            "label": "本次要登录的账号",
                                            "items": account_items,
                                            "persistent-hint": True,
                                            "hint": "先在这里选中账号并保存，再到详情页点「发送验证码」",
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
                                            "model": "login_code",
                                            "label": "登录验证码",
                                            "persistent-hint": True,
                                            "hint": "收到 Telegram 验证码后填这里并保存，再点详情页「确认登录」",
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
                                            "model": "login_password",
                                            "label": "两步验证密码（可选）",
                                            "type": "password",
                                            "persistent-hint": True,
                                            "hint": "该账号启用了两步验证时填写；登录成功后建议清空该项",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    self._group_header("网络与凭据", "默认走旁路由 socks5 代理；凭据为 Telegram 应用级公开值"),
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
                                            "model": "proxy_type",
                                            "label": "代理类型",
                                            "items": [
                                                {"title": "socks5", "value": "socks5"},
                                                {"title": "http", "value": "http"},
                                            ],
                                            "persistent-hint": True,
                                            "hint": "留空代理主机即直连",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 5},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "proxy_host",
                                            "label": "代理主机",
                                            "placeholder": "192.0.2.94",
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
                                            "model": "proxy_port",
                                            "label": "代理端口",
                                            "placeholder": "7893",
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
                                        "props": {
                                            "model": "api_hash",
                                            "label": "api_hash",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                ]
            }
        ], {
            "enabled": False,
            "cron": "0 9 * * *",
            "notify_on_failure": True,
            "accounts_text": DEFAULT_ACCOUNTS_TEXT,
            "targets_text": DEFAULT_TARGETS_TEXT,
            "login_account": "",
            "login_code": "",
            "login_password": "",
            "proxy_type": "socks5",
            "proxy_host": "192.0.2.94",
            "proxy_port": 7893,
            "api_id": DEFAULT_API_ID,
            "api_hash": DEFAULT_API_HASH,
        }

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
            detail = item.get("reply") or item.get("error") or ""
            rows.append(
                {
                    "component": "tr",
                    "content": [
                        {"component": "td", "text": item.get("time", "")},
                        {"component": "td", "text": item.get("account", "")},
                        {"component": "td", "text": item.get("bot", "")},
                        {"component": "td", "text": "✅ 成功" if ok else "❌ 失败"},
                        {"component": "td", "text": str(detail)[:120]},
                    ],
                }
            )
        if not rows:
            rows.append(
                {
                    "component": "tr",
                    "content": [
                        {"component": "td", "props": {"colspan": 5}, "text": "还没有签到记录"},
                    ],
                }
            )
        return rows

    def _button(
        self,
        text: str,
        path: str,
        color: str = "primary",
        method: str = "get",
    ) -> dict:
        """
        构造详情页操作按钮（按钮只在详情页可用）。

        :param text: 按钮文字
        :param path: 插件 API 路径（相对 ``plugin/<插件ID>``）
        :param color: 按钮颜色
        :param method: HTTP 方法
        :return dict: 表单节点
        """

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
                    "params": {"apikey": settings.API_TOKEN},
                }
            },
        }

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
        proxy = build_proxy(self._proxy_type, self._proxy_host, self._proxy_port)
        login_key = self._login_account or (self._accounts[0].key if self._accounts else "")
        login_account = next(
            (account for account in self._accounts if account.key == login_key), None
        )

        header: List[dict] = [
            {
                "component": "VAlert",
                "props": {
                    "type": "info",
                    "text": f"代理：{proxy_desc(proxy)}　|　账号：{len(self._accounts)} 个"
                            f"　|　签到目标：{len(self._targets)} 条　|　"
                            f"定时：{self._cron}",
                },
            }
        ]
        if self._config_problems:
            header.append(
                {
                    "component": "VAlert",
                    "props": {
                        "type": "warning",
                        "text": "配置待修：" + "；".join(self._config_problems[:5]),
                    },
                }
            )
        if login_account is not None:
            pending_info = pending.get(login_account.key) or {}
            header.append(
                {
                    "component": "VAlert",
                    "props": {
                        "type": "success" if pending_info else "secondary",
                        "text": f"待登录账号：{login_account.display()}（{login_account.phone or '未填手机号'}）"
                                + ("　—　验证码已发送，等待确认" if pending_info else ""),
                    },
                }
            )
        header.append(
            {
                "component": "VAlert",
                "props": {
                    "type": "success" if state.get("last_summary", "").count("/") and
                    "0/" not in state.get("last_summary", "") else "secondary",
                    "text": f"最近一次：{state.get('last_run_at') or '未运行'}"
                            f"（{state.get('last_source') or '-'}）　{state.get('last_summary') or ''}",
                },
            }
        )

        actions = [
            self._button("① 发送验证码", "/login/send_code"),
            self._button("② 确认登录", "/login/confirm", color="success"),
            self._button("立即签到", "/signin/run", color="primary"),
            self._button("连通性自检", "/selftest", color="secondary"),
            self._button("清空验证码/密码", "/login/reset", color="warning"),
        ]

        def table(headers: List[str], rows: List[Dict[str, Any]]) -> dict:
            """
            构造一个紧凑表格节点。

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

        return [
            {
                "component": "div",
                "props": {"class": "pa-4"},
                "content": header
                + actions
                + [
                    self._group_header("账号登录状态"),
                    table(["账号", "手机号", "状态"], self._login_status_rows()),
                    self._group_header(f"最近 {PAGE_RESULT_LIMIT} 条签到结果"),
                    table(["时间", "账号", "bot", "结果", "回复/错误"], self._result_rows()),
                ],
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
        if not wanted:
            wanted = self._login_account
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
                    build_proxy(self._proxy_type, self._proxy_host, self._proxy_port)
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
        proxy = build_proxy(self._proxy_type, self._proxy_host, self._proxy_port)
        result = await send_code(self.get_data_path(), account, proxy)
        logger.info(
            "【TgSignin】发送验证码：%s → %s", account.key, result.get("message")
        )
        return {
            "success": bool(result.get("ok")),
            "message": result.get("message", ""),
            "data": {"account": account.key, "already": bool(result.get("already"))},
        }

    async def api_confirm_login(self, request: Request) -> Dict[str, Any]:
        """
        阶段二：用验证码（含可选两步验证密码）完成登录。

        :param request: FastAPI 请求对象
        :return Dict[str, Any]: 统一响应结构
        """
        if not self._enabled:
            return {"success": False, "message": "插件未启用", "data": None}
        params = await self._read_params(request)
        account = self._find_account(params.get("account"))
        if account is None:
            return {"success": False, "message": "找不到要登录的账号，请先配置", "data": None}
        code = str(params.get("code") or self._login_code or "").strip()
        password = str(params.get("password") or self._login_password or "").strip()
        proxy = build_proxy(self._proxy_type, self._proxy_host, self._proxy_port)
        result = await confirm_login(
            self.get_data_path(), account, code, password, proxy
        )
        if result.get("ok"):
            record_login(self.get_data_path(), account.key, result.get("me") or {})
        logger.info("【TgSignin】确认登录：%s → %s", account.key, result.get("message"))
        return {
            "success": bool(result.get("ok")),
            "message": result.get("message", ""),
            "data": result.get("me"),
        }

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
        self.update_config(
            {
                "enabled": self._enabled,
                "cron": self._cron,
                "notify_on_failure": self._notify_on_failure,
                "accounts_text": self._accounts_text,
                "targets_text": self._targets_text,
                "login_account": self._login_account,
                "login_code": "",
                "login_password": "",
                "proxy_type": self._proxy_type,
                "proxy_host": self._proxy_host,
                "proxy_port": self._proxy_port,
                "api_id": self._api_id,
                "api_hash": self._api_hash,
            }
        )
        self._login_code = ""
        self._login_password = ""
        return {"success": True, "message": "已清空验证码/密码与待登录状态", "data": None}

    async def api_signin(self, request: Request) -> Dict[str, Any]:
        """
        立即执行一次签到（可限定账号或 bot）。

        :param request: FastAPI 请求对象
        :return Dict[str, Any]: 统一响应结构
        """
        if not self._enabled:
            return {"success": False, "message": "插件未启用", "data": None}
        params = await self._read_params(request)
        result = await self._signin(
            source="手动",
            only_account=(params.get("account") or None),
            only_bot=(params.get("bot") or None),
        )
        return result

    async def api_selftest(self, request: Request) -> Dict[str, Any]:
        """
        用公开应用凭据做一次 Telegram 连通性自检。

        :param request: FastAPI 请求对象
        :return Dict[str, Any]: 统一响应结构
        """
        del request
        proxy = build_proxy(self._proxy_type, self._proxy_host, self._proxy_port)
        result = await connection_selftest(self.get_data_path(), proxy)
        return {
            "success": bool(result.get("ok")),
            "message": result.get("message", ""),
            "data": {"proxy": proxy_desc(proxy)},
        }

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
        removed = delete_session(self.get_data_path(), account.key)
        clear_pending(self.get_data_path(), account.key)
        state = load_state(self.get_data_path())
        (state.get("accounts") or {}).pop(account.key, None)
        from .core.store import save_state  # pylint: disable=import-outside-toplevel

        save_state(self.get_data_path(), state)
        return {
            "success": True,
            "message": f"已退出 {account.display()}（{'已删除 session' if removed else '本就没有 session'}）",
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
    ) -> Dict[str, Any]:
        """
        执行签到并按需通知失败。

        :param source: 触发来源（定时/手动/命令）
        :param only_account: 只跑该账号
        :param only_bot: 只跑该 bot
        :return Dict[str, Any]: 统一响应结构
        """
        self._refresh_parsed_config()
        if not self._accounts:
            return {"success": False, "message": "没有配置任何账号", "data": None}

        proxy = build_proxy(self._proxy_type, self._proxy_host, self._proxy_port)
        logger.info(
            "【TgSignin】开始签到：来源=%s 账号=%s 代理=%s",
            source,
            only_account or "全部",
            proxy_desc(proxy),
        )
        results = await run_all(
            self._accounts,
            self._targets,
            self.get_data_path(),
            proxy,
            only_account=only_account,
            only_bot=only_bot,
        )
        summary = summarize_results(results)
        record_run(self.get_data_path(), results, source, summary)
        for item in results:
            logger.info(
                "【TgSignin】%s %s → %s %s",
                "✅" if item.get("ok") else "❌",
                item.get("account"),
                item.get("bot"),
                str(item.get("reply") or item.get("error") or "")[:120],
            )
        self._notify_failure(results)
        return {"success": True, "message": summary, "data": {"results": results}}

    def _notify_failure(self, results: List[Dict[str, Any]]) -> None:
        """
        签到失败时按配置发送 MoviePilot 通知。

        :param results: 本次签到结果
        :return None
        """
        if not self._notify_on_failure:
            return
        failed = [item for item in results if not item.get("ok")]
        if not failed:
            return
        lines = [
            f"- {item.get('account')} → {item.get('bot')}："
            f"{str(item.get('error') or item.get('reply') or '无回复')[:120]}"
            for item in failed[:10]
        ]
        text = f"共 {len(failed)} 项签到失败：\n" + "\n".join(lines)
        try:
            self.post_message(
                mtype=MessageType.Plugin,
                title="【Telegram 自动签到】",
                text=text,
            )
        except Exception as error:  # pylint: disable=broad-except
            logger.error("【TgSignin】发送失败通知出错：%s", error)

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
                        build_proxy(self._proxy_type, self._proxy_host, self._proxy_port),
                    )
                    if not sent.get("ok"):
                        return sent
                return await confirm_login(
                    self.get_data_path(),
                    account,
                    code,
                    password,
                    build_proxy(self._proxy_type, self._proxy_host, self._proxy_port),
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
        return [
            {
                "id": "tgsignin_daily",
                "name": "Telegram 自动签到",
                "trigger": trigger,
                "func": self.scheduled_signin,
                "kwargs": {},
            }
        ]
