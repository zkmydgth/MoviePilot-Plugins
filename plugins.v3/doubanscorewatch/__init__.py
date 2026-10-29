# -*- coding: utf-8 -*-
"""
豆瓣开分通知：跟踪订阅资源的豆瓣评分，从「暂无评分」变为「有评分」时发送通知。

插件按豆瓣条目为追踪主键，支持一个媒体绑定多个豆瓣条目（豆瓣按季拆分的情况），
也支持多个订阅季共用同一个豆瓣条目（豆瓣整剧单条目的情况）。
"""

from __future__ import annotations

import random
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from apscheduler.triggers.cron import CronTrigger

from app.plugins import _PluginBase
from app.runtime.log import logger
from app.schemas import MessageType

from .douban_api import DoubanScoreClient
from .matcher import (
    build_ai_messages,
    episode_slot,
    parse_ai_bindings,
    season_marker,
    select_candidates,
)
from .version import VERSION


class DoubanScoreWatch(_PluginBase):
    """豆瓣开分通知插件。"""

    # 插件名称
    plugin_name = "豆瓣开分通知"
    # 插件描述
    plugin_desc = "跟踪订阅资源的豆瓣评分，从无分变为有分时推送分数、评价人数与首次观测日期。"
    # 插件版本
    plugin_version = VERSION
    # 插件作者
    plugin_author = "zkmydgth"
    # 插件配置项前缀
    plugin_config_prefix = "doubanscorewatch_"
    # 加载顺序
    plugin_order = 62
    # 可使用的用户级别
    auth_level = 1

    #: 插件状态数据键
    _STATE_KEY = "track_state"
    #: 豆瓣条目链接前缀
    _DOUBAN_URL = "https://movie.douban.com/subject/{}/"
    #: 状态展示文案
    _STATUS_LABELS = {
        "pending": "待开分",
        "scored": "已开分",
        "unmatched": "未匹配",
        "ignored": "已忽略",
    }
    #: 连续失败多少条后结束本轮，避免风控下持续打接口
    _FAILURE_BREAKER = 5
    #: 单条通知最多尝试发送次数
    _NOTICE_MAX_ATTEMPTS = 5
    #: 单条绑定保留的评分历史条数
    _HISTORY_KEEP = 30

    # 运行配置
    _enabled = False
    _notify = True
    _vote_threshold = 50
    _window_start = "21:00"
    _window_end = "23:00"
    _rounds_per_day = 1
    _keep_tracking = False
    _change_delta = 0.5
    _track_days = 90
    _ai_enabled = False
    _ai_auto_accept = False
    _ai_max_calls = 10
    _ai_timeout = 60
    _douban_cookie = ""
    _manual_bindings = ""
    _quiet_start = ""
    _quiet_end = ""

    # 运行时状态
    _state: Dict[str, Any] = {}
    _round_lock = threading.Lock()
    _notice_lock = threading.Lock()
    _stop_event: Optional[threading.Event] = None
    _last_error = ""

    def init_plugin(self, config: dict = None) -> None:
        """根据插件配置初始化运行状态。"""
        self.stop_service()
        config = config or {}
        self._enabled = bool(config.get("enabled"))
        self._notify = bool(config.get("notify", True))
        self._vote_threshold = self._to_int(config.get("vote_threshold"), 50, 0, 100000)
        self._window_start = self._to_clock(config.get("window_start"), "21:00")
        self._window_end = self._to_clock(config.get("window_end"), "23:00")
        self._rounds_per_day = self._to_int(config.get("rounds_per_day"), 1, 1, 4)
        self._keep_tracking = bool(config.get("keep_tracking"))
        self._change_delta = self._to_float(config.get("change_delta"), 0.5, 0.1, 5.0)
        self._track_days = self._to_int(config.get("track_days"), 90, 1, 3650)
        self._ai_enabled = bool(config.get("ai_enabled"))
        self._ai_auto_accept = bool(config.get("ai_auto_accept"))
        self._ai_max_calls = self._to_int(config.get("ai_max_calls"), 10, 1, 50)
        self._ai_timeout = self._to_int(config.get("ai_timeout"), 60, 10, 300)
        self._douban_cookie = str(config.get("douban_cookie") or "").strip()
        self._manual_bindings = str(config.get("manual_bindings") or "").strip()
        self._quiet_start = self._to_clock(config.get("quiet_start"), "")
        self._quiet_end = self._to_clock(config.get("quiet_end"), "")
        self._stop_event = threading.Event()
        self._state = self._load_state()
        self._migrate_season_bindings()
        self._apply_manual_bindings()
        self._save_state()

    def get_state(self) -> bool:
        """获取插件启用状态。"""
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """返回插件远程命令列表。"""
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        """返回插件 API 列表。"""
        return [
            {
                "path": "/check_now",
                "endpoint": self.api_check_now,
                "methods": ["GET"],
                "summary": "立即检查一轮豆瓣评分",
                "description": "跳过时间窗口限制，立即执行一次评分检查",
            },
            {
                "path": "/retry",
                "endpoint": self.api_retry,
                "methods": ["GET"],
                "summary": "重新匹配豆瓣条目",
                "description": "清空该媒体的候选与绑定结果，下一轮重新匹配",
            },
            {
                "path": "/ignore",
                "endpoint": self.api_ignore,
                "methods": ["GET"],
                "summary": "忽略该媒体",
                "description": "停止追踪指定媒体，不再检查与通知",
            },
            {
                "path": "/restore",
                "endpoint": self.api_restore,
                "methods": ["GET"],
                "summary": "恢复追踪该媒体",
                "description": "把已忽略的媒体恢复为待开分状态",
            },
            {
                "path": "/bind",
                "endpoint": self.api_bind,
                "methods": ["GET"],
                "summary": "手动绑定豆瓣条目",
                "description": "把指定媒体绑定到给定的豆瓣 ID，并立即检查一次",
            },
            {
                "path": "/unbind",
                "endpoint": self.api_unbind,
                "methods": ["GET"],
                "summary": "解除豆瓣条目绑定",
                "description": "删除该媒体下指定豆瓣条目的绑定",
            },
            {
                "path": "/test",
                "endpoint": self.api_test,
                "methods": ["GET"],
                "summary": "测试豆瓣接口",
                "description": "按豆瓣 ID 读取一次评分，验证接口与 Cookie 是否可用",
            },
        ]

    def get_service(self) -> List[Dict[str, Any]]:
        """注册插件定时服务。"""
        if not self._enabled:
            return []
        return [
            {
                "id": "DoubanScoreWatch",
                "name": "豆瓣开分检查",
                "trigger": CronTrigger.from_crontab("*/10 * * * *"),
                "func": self._tick,
                "kwargs": {},
            }
        ]

    def stop_service(self) -> None:
        """停止插件后台服务并释放运行时状态。"""
        if self._stop_event is not None:
            self._stop_event.set()
        self._last_error = ""

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """返回插件配置表单与默认配置。"""
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "info",
                                            "variant": "tonal",
                                            "text": "跟踪订阅资源的豆瓣评分：只追当前无分的条目，评分人数达到门槛后"
                                                    "视为「开分」并推送一次通知。默认每天一轮，可手动调整时段与轮数。"
                                                    "豆瓣条目不提供开分日期，通知中的日期为插件首次观测到有效评分的日期。"
                                                    "删除订阅会在下一轮同步时移除对应追踪条目；订阅完结的不删，继续追开分。",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
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
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "notify", "label": "发送通知"},
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "keep_tracking", "label": "开分后继续追评分变化"},
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
                                            "model": "vote_threshold",
                                            "label": "评价人数门槛",
                                            "type": "number",
                                            "hint": "评分人数达到该值才算开分",
                                            "persistent-hint": True,
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
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "window_start",
                                            "label": "检查窗口开始",
                                            "type": "time",
                                            "hint": "只在这个时段内查豆瓣，轮次在窗口内随机",
                                            "persistent-hint": True,
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
                                            "model": "window_end",
                                            "label": "检查窗口结束",
                                            "type": "time",
                                            "hint": "与开始时间共同构成每天的检查时段",
                                            "persistent-hint": True,
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
                                            "model": "rounds_per_day",
                                            "label": "每天轮数",
                                            "type": "number",
                                            "hint": "1-4，轮次在窗口内随机分布",
                                            "persistent-hint": True,
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
                                            "model": "track_days",
                                            "label": "最长追踪天数",
                                            "type": "number",
                                            "hint": "仅继续追变化时生效",
                                            "persistent-hint": True,
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
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "change_delta",
                                            "label": "评分变动提醒阈值",
                                            "type": "number",
                                            "hint": "继续追变化时，分差达到该值才提醒",
                                            "persistent-hint": True,
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
                                            "model": "quiet_start",
                                            "label": "静默开始",
                                            "type": "time",
                                            "clearable": True,
                                            "hint": "只推迟通知，不停止检查；两个框都留空即关闭，"
                                                    "只填一侧时另一侧按 00:00 / 23:59 处理",
                                            "persistent-hint": True,
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
                                            "model": "quiet_end",
                                            "label": "静默结束",
                                            "type": "time",
                                            "clearable": True,
                                            "hint": "结束早于开始表示跨零点，例如 23:00 到 08:00",
                                            "persistent-hint": True,
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
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "info",
                                            "variant": "tonal",
                                            "text": "静默时段说明：它只影响通知的发送时间，不影响检查。"
                                                    "静默期间插件照常查豆瓣、照常记录开分，只是把通知攒在队列里，"
                                                    "等静默结束后下一次轮询（最多 10 分钟）补发，不会丢通知。"
                                                    "两个时间都留空即关闭静默。",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "ai_enabled", "label": "AI 介入季映射"},
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "ai_auto_accept", "label": "AI 高置信度自动绑定"},
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {"model": "ai_max_calls", "label": "每轮 AI 调用上限", "type": "number"},
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {"model": "ai_timeout", "label": "AI 超时秒数", "type": "number"},
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
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VTextarea",
                                        "props": {
                                            "model": "douban_cookie",
                                            "label": "自定义豆瓣 Cookie",
                                            "rows": 2,
                                            "hint": "留空则只走匿名接口；接口被风控时可用 Cookie 抓网页兜底",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            }
                        ],
                    },
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
                                            "model": "manual_bindings",
                                            "label": "手动绑定（每行一条）",
                                            "rows": 3,
                                            "hint": "格式：媒体来源:媒体ID=豆瓣ID，例如 themoviedb:12345=34925294",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                ],
            }
        ], {
            "enabled": False,
            "notify": True,
            "keep_tracking": False,
            "vote_threshold": 50,
            "window_start": "21:00",
            "window_end": "23:00",
            "rounds_per_day": 1,
            "track_days": 90,
            "change_delta": 0.5,
            "quiet_start": "",
            "quiet_end": "",
            "ai_enabled": False,
            "ai_auto_accept": False,
            "ai_max_calls": 10,
            "ai_timeout": 60,
            "douban_cookie": "",
            "manual_bindings": "",
        }

    def get_page(self) -> Optional[List[dict]]:
        """返回插件详情页面：统计、操作入口与追踪列表。"""
        if not self._enabled:
            return [
                {
                    "component": "VAlert",
                    "props": {"type": "info", "text": "插件未启用，请先在设置中启用并保存配置。"},
                }
            ]
        entries = list((self._state.get("entries") or {}).values())
        counts = {"pending": 0, "scored": 0, "unmatched": 0, "ignored": 0}
        for entry in entries:
            status = str(entry.get("status") or "pending")
            if status in counts:
                counts[status] += 1
        header: List[dict] = [
            {
                "component": "div",
                "props": {"class": "d-flex align-center flex-wrap mb-2"},
                "content": [
                    {
                        "component": "VBtn",
                        "props": {
                            "color": "primary",
                            "variant": "flat",
                            "size": "small",
                            "prependIcon": "mdi-refresh",
                        },
                        "text": "立即检查",
                        "events": {
                            "click": {
                                "api": f"plugin/{self.__class__.__name__}/check_now",
                                "method": "get",
                                "params": {"apikey": self._api_token()},
                            }
                        },
                    }
                ],
            },
            {
                "component": "div",
                "props": {"class": "d-flex flex-wrap ga-2 mb-2"},
                "content": [
                    self._chip(f"待开分 {counts['pending']}", "primary"),
                    self._chip(f"已开分 {counts['scored']}", "success"),
                    self._chip(f"未匹配 {counts['unmatched']}", "warning"),
                    self._chip(f"已忽略 {counts['ignored']}", "grey"),
                ],
            },
            {
                "component": "p",
                "props": {"class": "text-caption"},
                "text": self._summary_text(),
            },
        ]
        if not entries:
            header.append(
                {
                    "component": "VAlert",
                    "props": {
                        "type": "info",
                        "variant": "tonal",
                        "text": "还没有追踪记录。插件会按订阅自动建立追踪，也可以在下一轮检查前点【立即检查】。",
                    },
                }
            )
            return header
        table_rows = [self._row_head()]
        for entry in sorted(entries, key=lambda item: str(item.get("title") or "")):
            group = [self._row_entry(entry)]
            suggested_ids = {
                str(item.get("douban_id")) for item in entry.get("ai_suggestions") or []
            }
            for suggestion in entry.get("ai_suggestions") or []:
                group.append(self._row_ai_suggestion(entry, suggestion))
            # 未匹配条目把豆瓣候选也列出来，页面可直接点选绑定
            for candidate in (entry.get("candidates") or [])[:5]:
                if str(candidate.get("douban_id")) in suggested_ids:
                    continue
                group.append(self._row_candidate(entry, candidate))
            if entry.get("status") == "unmatched" and not (entry.get("candidates") or []):
                group.append(self._row_hint(entry))
            # 条目之间加分隔线，候选 / AI 建议行留在本条目的分组内
            group[-1] = self._with_divider(group[-1])
            table_rows.extend(group)
        header.append(
            {
                "component": "div",
                "props": {"style": "overflow-x: auto;"},
                "content": [
                    {
                        "component": "table",
                        "props": {"class": "text-sm w-100"},
                        "content": [
                            {"component": "thead", "content": [self._row_head()]},
                            {"component": "tbody", "content": table_rows[1:]},
                        ],
                    }
                ],
            }
        )
        return header

    @staticmethod
    def _chip(text: str, color: str) -> dict:
        """构造统计用标签组件。"""
        return {
            "component": "VChip",
            "props": {"color": color, "size": "small", "variant": "tonal"},
            "text": text,
        }

    @staticmethod
    def _row_head() -> dict:
        """构造表头行。"""
        return {
            "component": "tr",
            "content": [
                {"component": "th", "props": {"class": "text-left"}, "text": "媒体"},
                {"component": "th", "props": {"class": "text-left"}, "text": "豆瓣"},
                {"component": "th", "props": {"class": "text-left"}, "text": "评分"},
                {"component": "th", "props": {"class": "text-left"}, "text": "人数"},
                {"component": "th", "props": {"class": "text-left"}, "text": "状态"},
                {"component": "th", "props": {"class": "text-left"}, "text": "最后检查"},
                {"component": "th", "props": {"class": "text-right"}, "text": "操作"},
            ],
        }

    def _row_entry(self, entry: Dict[str, Any]) -> dict:
        """构造单个媒体的表格行。"""
        bindings = entry.get("bindings") or []
        binding = bindings[0] if bindings else {}
        title = str(entry.get("title") or "")
        meta = " / ".join(
            part
            for part in [
                str(entry.get("type") or ""),
                str(entry.get("year") or ""),
                ("S" + ",".join(str(s) for s in entry.get("seasons") or [])) if entry.get("seasons") else "",
            ]
            if part
        )
        douban_text = "-"
        if binding:
            douban_text = str(binding.get("title") or binding.get("douban_id") or "-")
            if len(bindings) > 1:
                douban_text += f" 等 {len(bindings)} 个条目"
            if binding.get("match_note"):
                douban_text += f"\n匹配依据：{binding.get('match_note')}"
        rating_text = f"{binding.get('rating'):.1f}" if binding.get("rating") else "-"
        votes_text = str(binding.get("votes") or "-") if binding else "-"
        status_text = self._STATUS_LABELS.get(str(entry.get("status")), str(entry.get("status")))
        if entry.get("status") == "unmatched" and entry.get("match_reason"):
            status_text = f"{status_text}（{entry.get('match_reason')}）"
        if entry.get("status") == "unmatched" and entry.get("next_try"):
            status_text = f"{status_text}\n{str(entry.get('next_try'))[:16]} 后重试"
        if binding.get("pre_scored"):
            status_text = f"{status_text}（绑定前已有评分）"
        if entry.get("sub_completed"):
            status_text = f"{status_text}（订阅已完结）"
        actions: List[dict] = []
        key = str(entry.get("key") or "")
        if entry.get("status") == "ignored":
            actions.append(self._action_button("恢复", "success", "restore", {"key": key}, "mdi-restore"))
        else:
            actions.append(self._action_button("重匹配", "primary", "retry", {"key": key}, "mdi-magnify"))
            actions.append(self._action_button("忽略", "grey", "ignore", {"key": key}, "mdi-eye-off"))
        cells = [
            self._cell(f"{title}\n{meta}"),
            self._cell(douban_text),
            self._cell(rating_text),
            self._cell(votes_text),
            self._cell(status_text),
            self._cell(str(entry.get("last_check") or "-")),
            {
                "component": "td",
                "props": {"class": "text-right", "style": "white-space: nowrap;"},
                "content": actions,
            },
        ]
        return {"component": "tr", "content": cells}

    def _row_ai_suggestion(self, entry: Dict[str, Any], suggestion: Dict[str, Any]) -> dict:
        """构造 AI 建议绑定行，供人工一键确认。"""
        key = str(entry.get("key") or "")
        douban_id = str(suggestion.get("douban_id") or "")
        text = (
            f"AI 建议绑定：{suggestion.get('title') or douban_id}"
            f"（{suggestion.get('year') or '年份未知'}，置信度 {suggestion.get('confidence', 0)}）"
        )
        return {
            "component": "tr",
            "content": [
                {"component": "td", "props": {"colspan": 6}, "content": [{"component": "p", "props": {"class": "mb-0 text-caption"}, "text": text}]},
                {
                    "component": "td",
                    "props": {"class": "text-right"},
                    "content": [
                        self._action_button(
                            "确认绑定",
                            "warning",
                            "bind",
                            {"key": key, "douban_id": douban_id},
                            "mdi-link-variant",
                        )
                    ],
                },
            ],
        }

    def _row_candidate(self, entry: Dict[str, Any], candidate: Dict[str, Any]) -> dict:
        """
        构造豆瓣候选行，供人工点选绑定。

        :param entry: 追踪条目
        :param candidate: 豆瓣候选
        :return: 表格行组件
        """
        key = str(entry.get("key") or "")
        douban_id = str(candidate.get("douban_id") or "")
        rating = candidate.get("rating") or 0
        votes = candidate.get("votes") or 0
        score_text = f"{rating} 分 / {votes} 人" if rating else "暂无评分"
        text = (
            f"候选：{candidate.get('title') or douban_id}"
            f"（{candidate.get('year') or '年份未知'}，{score_text}，豆瓣 {douban_id}）"
        )
        return {
            "component": "tr",
            "content": [
                {
                    "component": "td",
                    "props": {"colspan": 6},
                    "content": [
                        {"component": "p", "props": {"class": "mb-0 text-caption"}, "text": text}
                    ],
                },
                {
                    "component": "td",
                    "props": {"class": "text-right"},
                    "content": [
                        self._action_button(
                            "绑定",
                            "primary",
                            "bind",
                            {"key": key, "douban_id": douban_id},
                            "mdi-link-variant",
                        )
                    ],
                },
            ],
        }

    def _row_hint(self, entry: Dict[str, Any]) -> dict:
        """
        构造未搜到条目时的提示行。

        :param entry: 追踪条目
        :return: 表格行组件
        """
        text = (
            f"未搜到豆瓣条目：{entry.get('title')}（{entry.get('year') or '年份未知'}）。"
            "可点该行【重匹配】再试，或在设置页「手动绑定」里填「媒体来源:媒体ID=豆瓣ID」。"
        )
        return {
            "component": "tr",
            "content": [
                {
                    "component": "td",
                    "props": {"colspan": 7},
                    "content": [
                        {"component": "p", "props": {"class": "mb-0 text-caption text-medium-emphasis"}, "text": text}
                    ],
                }
            ],
        }

    @staticmethod
    def _cell(text: str) -> dict:
        """构造普通文本单元格，保留换行。"""
        return {
            "component": "td",
            "props": {"style": "white-space: pre-line; padding-top: 6px; padding-bottom: 6px;"},
            "content": [{"component": "p", "props": {"class": "mb-0"}, "text": text}],
        }

    @staticmethod
    def _with_divider(row: dict) -> dict:
        """
        给条目分组的最后一行加分隔线，避免相邻条目在列表里粘连。

        :param row: 表格行组件
        :return: 同一个行组件
        """
        border = "border-bottom: 1px solid rgba(var(--v-border-color, 0, 0, 0), var(--v-border-opacity, 0.12));"
        for cell in row.get("content") or []:
            props = cell.setdefault("props", {})
            style = str(props.get("style") or "").strip()
            props["style"] = f"{style} {border}".strip()
        return row

    def _action_button(self, text: str, color: str, action: str, params: Dict[str, Any], icon: str) -> dict:
        """构造行内操作按钮。"""
        payload = {"apikey": self._api_token()}
        payload.update(params)
        return {
            "component": "VBtn",
            "props": {
                "color": color,
                "variant": "text",
                "size": "x-small",
                "prependIcon": icon,
            },
            "text": text,
            "events": {
                "click": {
                    "api": f"plugin/{self.__class__.__name__}/{action}",
                    "method": "get",
                    "params": payload,
                }
            },
        }

    @staticmethod
    def _api_token() -> str:
        """读取宿主 API Token，供页面按钮直接调用插件接口。"""
        try:
            from app.runtime.config import settings

            return str(settings.API_TOKEN or "")
        except Exception:  # noqa: BLE001 - 缺少配置时按钮仍可渲染
            return ""

    def _summary_text(self) -> str:
        """生成页面顶部的运行摘要。"""
        state = self._state or {}
        last_round = state.get("last_round") or "尚未执行"
        last_result = state.get("last_result") or "无"
        plan = state.get("plan") or {}
        times = "、".join(plan.get("times") or []) or "待生成"
        lines = [
            f"上次检查：{last_round}｜结果：{last_result}｜今日计划：{times}｜"
            f"评价人数门槛：{self._vote_threshold}｜AI 季映射：{'开' if self._ai_enabled else '关'}"
        ]
        sync_error = str(state.get("last_sync_error") or "")
        if sync_error:
            at = state.get("last_sync_error_at") or "时间未知"
            lines.append(f"订阅同步异常：{sync_error}（{at}）")
        if self._last_error:
            lines.append(f"上一轮异常：{self._last_error}")
        zero_rounds = int(state.get("zero_match_rounds") or 0)
        if zero_rounds >= 2:
            lines.append(f"已连续 {zero_rounds} 轮没有任何新匹配，请检查豆瓣接口或手动绑定")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 调度与检查
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        """定时入口：判断是否到点，满足条件时执行一轮检查。"""
        if not self._enabled:
            return
        now = datetime.now()
        state = self._state or self._load_state()
        self._ensure_plan(state, now)
        self._state = state
        done = self._rounds_done(state, now.date())
        if done >= self._rounds_per_day:
            self._note_once(
                state,
                "done",
                f"今日定时检查已完成 {done}/{self._rounds_per_day} 轮，跳过",
            )
            self._flush_notices(state)
            return
        if not self._in_window(now, self._window_start, self._window_end):
            self._note_once(
                state,
                "outside",
                f"当前 {now:%H:%M} 不在检查窗口 {self._window_start}-{self._window_end}，跳过",
            )
            self._flush_notices(state)
            return
        planned = self._next_plan_time(state, now)
        if planned is None:
            self._note_once(state, "noplan", "今日没有待执行计划，跳过")
            self._flush_notices(state)
            return
        if now < planned:
            self._note_once(state, "wait", f"等待今日计划时间 {planned:%H:%M}（当前 {now:%H:%M}）")
            self._flush_notices(state)
            return
        self._run_round(state, now)
        self._flush_notices(state)

    def _note_once(self, state: Dict[str, Any], key: str, message: str) -> None:
        """
        同一自然日、同一原因只记一次 INFO 日志，避免每十分钟刷屏。

        :param state: 插件状态字典
        :param key: 原因标识
        :param message: 日志正文
        """
        today = date.today().isoformat()
        logged = state.setdefault("logged_reasons", {})
        if logged.get(key) == today:
            return
        logged[key] = today
        state["logged_reasons"] = {name: day for name, day in logged.items() if day == today}
        self._save_state(state)
        logger.info(f"【豆瓣开分】{message}")

    def _run_round(
        self,
        state: Dict[str, Any],
        now: Optional[datetime] = None,
        scheduled: bool = True,
    ) -> str:
        """
        执行一轮完整检查。

        :param state: 插件状态字典
        :param now: 当前时间，缺省取系统时间
        :param scheduled: 是否为定时轮次；手动检查不占用当天轮次
        :return: 本轮结果摘要
        """
        if not self._round_lock.acquire(blocking=False):
            return "上一轮仍在执行"
        try:
            now = now or datetime.now()
            self._state = state
            client = DoubanScoreClient(cookie=self._douban_cookie)
            entries = state.setdefault("entries", {})
            synced, removed = self._sync_subscriptions(entries)
            # 订阅同步后条目才存在，此时再应用一次配置里的手动绑定
            self._apply_manual_bindings()
            ai_budget = [self._ai_max_calls]
            matched = 0
            checked = 0
            aborted = False
            notices_before = len(state.get("pending_notices") or [])
            for entry in entries.values():
                if self._should_stop():
                    aborted = True
                    break
                if entry.get("status") in ("ignored",):
                    continue
                try:
                    if not entry.get("bindings"):
                        # 匹配失败过的条目按退避间隔重试，避免每轮都打豆瓣
                        if not self._match_due(entry, now):
                            continue
                        if self._match_entry(entry, client, ai_budget):
                            matched += 1
                        # 刚绑定成功的条目同一轮就查一次分，避免手动检查要等下一轮
                        if not entry.get("bindings"):
                            continue
                    self._check_entry(entry, client, state)
                    checked += 1
                except Exception as error:  # noqa: BLE001 - 单条媒体失败不影响其它条目
                    logger.warning(f"【豆瓣开分】处理 {entry.get('title')} 失败：{error}")
                    continue
                failures = int(getattr(client, "consecutive_failures", 0) or 0)
                if failures >= self._FAILURE_BREAKER:
                    aborted = True
                    logger.warning(f"【豆瓣开分】豆瓣连续 {failures} 次请求失败，本轮提前结束")
                    break
                # 豆瓣接口没有公开配额，逐条降速避免连续请求触发风控
                time.sleep(random.uniform(1.0, 2.0))
            if self._should_stop():
                logger.info("【豆瓣开分】插件已停用或重载，本轮中止")
                return "插件已停用或重载，本轮中止"
            # 只统计本轮真正入队的开分通知，预开分与评分不足都不算
            opened = max(0, len(state.get("pending_notices") or []) - notices_before)
            unmatched = sum(1 for item in entries.values() if item.get("status") == "unmatched")
            if unmatched and not matched:
                state["zero_match_rounds"] = int(state.get("zero_match_rounds") or 0) + 1
            else:
                state["zero_match_rounds"] = 0
            state["last_round"] = now.strftime("%Y-%m-%d %H:%M")
            if scheduled:
                self._mark_round_done(state, now.date())
            summary = (
                f"同步 {synced} 条订阅，删除 {removed} 条已退订，"
                f"匹配 {matched} 条，检查 {checked} 条，开分 {opened} 条"
            )
            if aborted:
                summary = f"{summary}（豆瓣连续失败，提前结束）"
            state["last_result"] = summary
            self._save_state(state)
            logger.info(f"【豆瓣开分】本轮完成：{summary}")
            return summary
        except Exception as error:  # noqa: BLE001 - 单轮失败不打断定时服务
            self._last_error = str(error)
            logger.error(f"【豆瓣开分】检查失败：{error}")
            return f"检查失败：{error}"
        finally:
            self._round_lock.release()

    def _sync_subscriptions(self, entries: Dict[str, Any]) -> tuple[int, int]:
        """
        按订阅表建立或更新追踪条目。

        订阅被删除后同步移除对应条目；因订阅完结而消失的条目保留下来继续追开分，
        因为宿主完成订阅时只写订阅历史并删除订阅记录。

        :param entries: 追踪条目字典
        :return: (本次涉及的订阅数量, 本轮删除的已退订条目数量)
        """
        try:
            subscriptions = self._load_subscriptions()
        except Exception as error:  # noqa: BLE001 - 订阅读取失败时保留既有追踪
            logger.warning(f"【豆瓣开分】读取订阅失败：{error}")
            self._record_sync_error(f"读取订阅失败：{error}")
            return 0, 0
        self._record_sync_error("")
        seen_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        seen: set[str] = set()
        seasons_seen: Dict[str, set] = {}
        for sub in subscriptions:
            media_type = str(getattr(sub, "type", "") or "")
            if media_type not in ("电影", "电视剧"):
                continue
            media_source = str(getattr(sub, "media_source", "") or "")
            media_id = str(getattr(sub, "media_id", "") or "")
            if not media_id:
                continue
            key = f"{media_source}:{media_id}"
            seen.add(key)
            entry = entries.get(key)
            if entry is None:
                entry = {
                    "key": key,
                    "media_source": media_source,
                    "media_id": media_id,
                    "type": media_type,
                    "title": str(getattr(sub, "name", "") or ""),
                    "year": str(getattr(sub, "year", "") or ""),
                    "episode_group": getattr(sub, "episode_group", None),
                    "seasons": [],
                    "status": "pending",
                    "bindings": [],
                    "candidates": [],
                    "ai_suggestions": [],
                    "sub_seen_at": seen_at,
                    "tries": 0,
                    "last_check": "",
                }
                entries[key] = entry
            season = getattr(sub, "season", None)
            if isinstance(season, int):
                seasons_seen.setdefault(key, set()).add(season)
            entry["total_episode"] = getattr(sub, "total_episode", None)
            entry["lack_episode"] = getattr(sub, "lack_episode", None)
            entry["sub_state"] = str(getattr(sub, "state", "") or "")
            entry["episode_group"] = getattr(sub, "episode_group", None)
            # 记录最后一次看到该订阅的时间，用于区分「完结」和「删除」
            entry["sub_seen_at"] = seen_at
            # 重新订阅后回到活动订阅，原先的完结标记不再生效
            entry["sub_completed"] = False
        # 季号按当前订阅重建，退订某一季不再残留在关联季里
        for key, seasons in seasons_seen.items():
            entries[key]["seasons"] = sorted(seasons)
        removed = 0
        for key in list(entries):
            if key in seen:
                continue
            entry = entries[key]
            if self._is_subscription_completed(entry):
                entry["sub_completed"] = True
                continue
            entries.pop(key, None)
            removed += 1
        return len(seen), removed

    def _record_sync_error(self, message: str) -> None:
        """
        记录最近一次订阅同步结果，供页面与排查使用。

        :param message: 失败原因；空字符串表示本次成功
        """
        state = self._state
        if not isinstance(state, dict):
            return
        if not message:
            state.pop("last_sync_error", None)
            state.pop("last_sync_error_at", None)
            return
        state["last_sync_error"] = message
        state["last_sync_error_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")

    @staticmethod
    def _load_subscriptions() -> List[Any]:
        """读取宿主订阅表。"""
        from app.db.subscribe_oper import SubscribeOper

        return SubscribeOper().list() or []

    def _is_subscription_completed(self, entry: Dict[str, Any]) -> bool:
        """
        判断已消失的订阅是「完结」还是「被删除」。

        宿主完成订阅时写订阅历史并删除订阅记录，普通删除不写历史。只看「有没有
        历史」会把「完结后又重新订阅、再手动删除」误判成完结，所以再比对完结时间
        与最后一次看到该订阅的时间：完结时间更早说明那条历史属于上一轮订阅。
        读历史失败时按完结处理，宁可保留也不误删仍在用的条目。

        :param entry: 追踪条目
        :return: 是否按完结保留条目
        """
        if entry.get("sub_completed"):
            return True
        try:
            finished_at = self._completion_finished_at(entry)
        except Exception as error:  # noqa: BLE001 - 历史不可读时保守保留
            logger.warning(f"【豆瓣开分】读取订阅完成历史失败，保留 {entry.get('title')}：{error}")
            return True
        if finished_at is None:
            return False
        seen_at = self._parse_timestamp(entry.get("sub_seen_at"))
        if seen_at is None:
            return True
        return finished_at >= seen_at

    @staticmethod
    def _completion_finished_at(entry: Dict[str, Any]) -> Optional[datetime]:
        """
        读取该媒体最近一次订阅完结时间。

        :param entry: 追踪条目
        :return: 完结时间；没有任何完结记录时为 None
        """
        from app.db.oper.subscribehistory import SubscribeHistoryOper
        from app.schemas.query import QueryPageRequest, SubscriptionHistoryFilter

        oper = SubscribeHistoryOper()
        seasons = [season for season in entry.get("seasons") or [] if isinstance(season, int)]
        finished_at: Optional[datetime] = None
        for season in seasons or [None]:
            records, _total = oper.query(
                SubscriptionHistoryFilter(
                    media_source=str(entry.get("media_source") or ""),
                    media_id=str(entry.get("media_id") or ""),
                    season=season,
                    episode_group=entry.get("episode_group") or None,
                ),
                QueryPageRequest(page=1, count=1),
            )
            for record in records or []:
                moment = DoubanScoreWatch._parse_timestamp(getattr(record, "date", None))
                if moment and (finished_at is None or moment > finished_at):
                    finished_at = moment
        return finished_at

    @staticmethod
    def _parse_timestamp(value: Any) -> Optional[datetime]:
        """
        解析宿主历史与插件状态里的时间文本。

        :param value: 时间文本
        :return: 解析结果；无法解析时为 None
        """
        text = str(value or "").strip()
        if not text:
            return None
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, pattern)
            except ValueError:
                continue
        return None

    def _should_stop(self) -> bool:
        """判断插件是否已被停用或重载。"""
        return bool(self._stop_event is not None and self._stop_event.is_set())

    def _match_due(self, entry: Dict[str, Any], now: Optional[datetime] = None) -> bool:
        """
        判断未匹配条目是否到了重试时间。

        :param entry: 追踪条目
        :param now: 当前时间，缺省取系统时间
        :return: 可以重试时为 True
        """
        planned = self._parse_timestamp(entry.get("next_try"))
        if planned is None:
            return True
        return (now or datetime.now()) >= planned

    def _defer_match(self, entry: Dict[str, Any]) -> None:
        """
        匹配失败后按次数退避，避免每轮都重复搜索豆瓣。

        :param entry: 追踪条目
        """
        tries = int(entry.get("tries") or 0) + 1
        entry["tries"] = tries
        moment = datetime.now() + timedelta(days=min(tries, 3))
        entry["next_try"] = moment.strftime("%Y-%m-%d %H:%M:%S")

    def _match_entry(
        self,
        entry: Dict[str, Any],
        client: DoubanScoreClient,
        ai_budget: List[int],
    ) -> bool:
        """
        为尚未绑定豆瓣条目的媒体尝试匹配。

        :param entry: 追踪条目
        :param client: 豆瓣查询客户端
        :param ai_budget: 本轮剩余 AI 调用次数
        :return: 是否成功绑定
        """
        if entry.get("manual_skip"):
            return False
        if str(entry.get("media_source")) == "douban":
            douban_id = str(entry.get("media_id") or "")
            detail = client.detail(douban_id, str(entry.get("type") or ""))
            candidate: Dict[str, Any] = {
                "douban_id": douban_id,
                "title": entry.get("title"),
                "year": entry.get("year"),
                "type": entry.get("type"),
            }
            if detail:
                # 豆瓣来源的订阅本身就带分，先读现状避免误判成刚开分
                candidate.update(
                    {
                        "rating": detail.get("rating") or 0,
                        "votes": detail.get("votes") or 0,
                    }
                )
            self._bind(entry, candidate, "id")
            return True
        candidates = client.search(str(entry.get("title") or ""), str(entry.get("type") or ""))
        seasons = entry.get("seasons") or []
        # 单季订阅按季对齐豆瓣条目；多季订阅按整剧条目处理
        wanted_season = seasons[0] if len(seasons) == 1 else None
        auto, pool, note = self._resolve_candidates(entry, candidates, client, wanted_season)
        if auto:
            self._bind(entry, auto, "search")
            entry["bindings"][-1]["match_note"] = note
            return True
        entry["candidates"] = pool
        if pool and self._ai_enabled and ai_budget[0] > 0:
            ai_budget[0] -= 1
            suggestions = self._ai_bind(entry, pool, client)
            if suggestions:
                if self._ai_auto_accept and max(s.get("confidence", 0) for s in suggestions) >= 0.8:
                    pool_map = {str(item.get("douban_id")): item for item in pool}
                    for suggestion in suggestions:
                        candidate = pool_map.get(str(suggestion.get("douban_id")))
                        if candidate:
                            self._bind(entry, candidate, "ai", suggestion.get("seasons"))
                    return bool(entry.get("bindings"))
                entry["ai_suggestions"] = suggestions
        # 搜不到条目也算未匹配：页面要能看到并手动处理，而不是误显示成「待开分」
        entry["status"] = "unmatched"
        entry["match_reason"] = "豆瓣未搜到条目" if not pool else f"{len(pool)} 个候选待确认"
        entry["last_check"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._defer_match(entry)
        return False

    def _resolve_candidates(
        self,
        entry: Dict[str, Any],
        candidates: List[Dict[str, Any]],
        client: DoubanScoreClient,
        wanted_season: Optional[int],
    ) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]], str]:
        """
        在候选中确定要绑定的豆瓣条目。

        优先级：季标记且集数能覆盖当前进度 → 按累计集数区间定位 → 标题与年份。
        长篇动画常见「TMDB 一季、豆瓣拆成第一季/年番1/年番2」，必须按集数区间落位。

        :param entry: 追踪条目
        :param candidates: 豆瓣候选
        :param client: 豆瓣查询客户端，用于补齐条目集数
        :param wanted_season: 单季订阅的季号
        :return: (自动绑定条目或 None, 候选列表, 判定说明)
        """
        if not candidates:
            return None, [], "豆瓣未搜到条目"
        current_episode = self._current_episode(entry)
        # 没有集数信息时退回纯标题/年份匹配
        if not current_episode:
            result = select_candidates(
                candidates, str(entry.get("title") or ""), entry.get("year"), wanted_season
            )
            return result.get("auto"), result.get("candidates") or [], str(result.get("reason") or "")
        enriched = self._enrich_candidates(entry, candidates, client)
        if wanted_season:
            same = [
                item for item in enriched if season_marker(item.get("title")) == wanted_season
            ]
            if len(same) == 1:
                episodes = same[0].get("episodes")
                if not episodes or episodes >= current_episode:
                    return same[0], [], f"第{wanted_season}季命中（{episodes or '集数未知'}集）"
            if len(same) > 1:
                return None, same[:5], f"豆瓣有多个第{wanted_season}季候选"
        hit = episode_slot(enriched, current_episode)
        if hit:
            return (
                hit,
                [],
                f"按集数区间匹配：第 {hit.get('episode_start')}-{hit.get('episode_end')} 集 → "
                f"{hit.get('title')}（当前第 {current_episode} 集）",
            )
        result = select_candidates(
            enriched, str(entry.get("title") or ""), entry.get("year"), wanted_season
        )
        if result.get("auto"):
            return result.get("auto"), [], str(result.get("reason") or "")
        return None, (result.get("candidates") or enriched)[:5], str(result.get("reason") or "")

    def _current_episode(self, entry: Dict[str, Any]) -> int:
        """
        计算订阅当前进度到第几集。

        :param entry: 追踪条目
        :return: 集号；信息不足时返回 0
        """
        total = entry.get("total_episode")
        lack = entry.get("lack_episode")
        if isinstance(total, int) and isinstance(lack, int) and total > 0 and 0 <= lack <= total:
            return total - lack
        return 0

    def _enrich_candidates(
        self,
        entry: Dict[str, Any],
        candidates: List[Dict[str, Any]],
        client: DoubanScoreClient,
    ) -> List[Dict[str, Any]]:
        """
        为候选补充豆瓣条目的集数与年份。

        :param entry: 追踪条目
        :param candidates: 豆瓣候选
        :param client: 豆瓣查询客户端
        :return: 补充后的候选列表
        """
        enriched: List[Dict[str, Any]] = []
        for item in candidates[:8]:
            detail = client.detail(str(item.get("douban_id")), str(entry.get("type") or "")) or {}
            merged = dict(item)
            if detail.get("episodes"):
                merged["episodes"] = detail["episodes"]
            if detail.get("year"):
                merged["year"] = detail["year"]
            enriched.append(merged)
            # 逐条补齐，避免连续请求触发豆瓣风控
            time.sleep(0.8)
        return enriched

    def _check_entry(
        self,
        entry: Dict[str, Any],
        client: DoubanScoreClient,
        state: Dict[str, Any],
    ) -> None:
        """
        检查一个媒体下所有绑定的豆瓣条目评分。

        :param entry: 追踪条目
        :param client: 豆瓣查询客户端
        :param state: 插件状态字典
        """
        entry["last_check"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        all_scored = True
        examined = 0
        for binding in entry.get("bindings") or []:
            # 已开分且不再追变化、或超过最长追踪天数的绑定不再请求豆瓣
            if not self._keep_tracking and binding.get("closed"):
                continue
            if self._keep_tracking and self._expired(binding):
                continue
            examined += 1
            detail = client.detail(str(binding.get("douban_id")), str(entry.get("type") or ""))
            if not detail:
                all_scored = False
                continue
            binding["title"] = detail.get("title") or binding.get("title")
            binding["year"] = detail.get("year") or binding.get("year")
            binding["rating"] = detail.get("rating") or 0
            binding["votes"] = detail.get("votes") or 0
            if not (binding["rating"] > 0 and binding["votes"] >= self._vote_threshold):
                # 当前没有有效评分：基线确认完成，之后出现评分就按开分通知
                binding.pop("baseline_pending", None)
                all_scored = False
                continue
            today = date.today().isoformat()
            if binding.get("baseline_pending"):
                # 绑定时拿不到分数，现在已有有效评分：按「绑定前就已开分」处理，不补发
                binding.pop("baseline_pending", None)
                self._mark_pre_scored(binding, today)
                continue
            if not binding.get("first_seen"):
                already_scored = float(binding.get("initial_rating") or 0) > 0 and int(
                    binding.get("initial_votes") or 0
                ) >= self._vote_threshold
                if already_scored:
                    self._mark_pre_scored(binding, today)
                    continue
                binding["first_seen"] = today
                binding["history"] = [{"date": today, "rating": binding["rating"], "votes": binding["votes"]}]
                binding["notified"] = False
            if not binding.get("notified"):
                self._queue_open_notice(state, entry, binding)
                binding["notified"] = True
                binding["last_notified_rating"] = binding["rating"]
                if not self._keep_tracking:
                    binding["closed"] = True
                continue
            if not self._keep_tracking:
                binding["closed"] = True
                continue
            last_rating = float(binding.get("last_notified_rating") or binding["rating"])
            if abs(binding["rating"] - last_rating) >= self._change_delta and not self._expired(binding):
                history = list(binding.get("history") or [])
                history.append({"date": today, "rating": binding["rating"], "votes": binding["votes"]})
                binding["history"] = history[-self._HISTORY_KEEP:]
                binding["last_notified_rating"] = binding["rating"]
                self._queue_change_notice(state, entry, binding)
        if all_scored and examined:
            entry["status"] = "scored"

    def _mark_pre_scored(self, binding: Dict[str, Any], today: str) -> None:
        """
        把绑定登记为「订阅前就已开分」，避免补发开分通知。

        :param binding: 豆瓣绑定
        :param today: 当天日期文本
        """
        binding["pre_scored"] = True
        binding["notified"] = True
        binding["last_notified_rating"] = binding["rating"]
        binding["history"] = [{"date": today, "rating": binding["rating"], "votes": binding["votes"]}]
        if not self._keep_tracking:
            binding["closed"] = True

    def _expired(self, binding: Dict[str, Any]) -> bool:
        """判断绑定是否超过最长追踪天数。"""
        first_seen = str(binding.get("first_seen") or "")
        if not first_seen:
            return False
        try:
            start = date.fromisoformat(first_seen)
        except ValueError:
            return False
        return (date.today() - start).days > self._track_days

    def _bind(
        self,
        entry: Dict[str, Any],
        candidate: Dict[str, Any],
        source: str,
        seasons: Optional[List[int]] = None,
        config_key: Optional[str] = None,
    ) -> None:
        """
        把一个豆瓣条目绑定到媒体上。

        :param entry: 追踪条目
        :param candidate: 豆瓣候选
        :param source: 绑定来源（id/search/ai/manual）
        :param seasons: 该条目覆盖的季号，缺省沿用订阅季
        :param config_key: 来自设置页手动绑定时记录的媒体键
        """
        douban_id = str(candidate.get("douban_id") or "").strip()
        if not douban_id:
            return
        for binding in entry.get("bindings") or []:
            if str(binding.get("douban_id")) == douban_id:
                return
        entry.setdefault("bindings", []).append(
            {
                "douban_id": douban_id,
                "title": str(candidate.get("title") or ""),
                "year": str(candidate.get("year") or ""),
                "seasons": list(seasons or entry.get("seasons") or []),
                "match_source": source,
                "rating": 0,
                "votes": 0,
                # 绑定时豆瓣已有的分数：命中说明「订阅前就已开分」，不再补发开分通知
                "initial_rating": float(candidate.get("rating") or 0),
                "initial_votes": int(candidate.get("votes") or 0),
                # 候选没带分数（手动绑定）时先记基线待确认，读到评分再判断是否补发
                "baseline_pending": candidate.get("rating") is None,
                "config_key": config_key,
                "first_seen": "",
                "last_check": "",
                "notified": False,
                "closed": False,
                "history": [],
            }
        )
        entry.pop("next_try", None)
        entry["tries"] = 0
        entry["status"] = "pending"
        entry["candidates"] = []
        entry["ai_suggestions"] = []

    def _ai_bind(
        self,
        entry: Dict[str, Any],
        candidates: List[Dict[str, Any]],
        client: DoubanScoreClient,
    ) -> List[Dict[str, Any]]:
        """
        调用宿主 LLM 完成季映射，返回通过校验的建议绑定。

        :param entry: 追踪条目
        :param candidates: 豆瓣候选
        :param client: 豆瓣客户端（预留，便于补充详情）
        :return: 建议绑定列表
        """
        del client
        seasons = [int(item) for item in entry.get("seasons") or [] if isinstance(item, int)]
        messages = build_ai_messages(
            str(entry.get("title") or ""),
            entry.get("year"),
            str(entry.get("type") or ""),
            seasons,
            candidates,
        )
        try:
            text = self._invoke_llm(messages)
        except Exception as error:  # noqa: BLE001 - AI 失败不影响主流程
            logger.warning(f"【豆瓣开分】AI 季映射失败：{error}")
            return []
        bindings = parse_ai_bindings(text, candidates, seasons, 0.6)
        suggestions: List[Dict[str, Any]] = []
        for item in bindings:
            candidate = item.get("candidate") or {}
            suggestions.append(
                {
                    "douban_id": str(candidate.get("douban_id")),
                    "title": candidate.get("title"),
                    "year": candidate.get("year"),
                    "seasons": item.get("seasons") or seasons,
                    "confidence": round(float(item.get("confidence") or 0), 2),
                    "reason": item.get("reason") or "",
                }
            )
        return suggestions

    def _invoke_llm(self, messages: List[Dict[str, str]]) -> str:
        """
        同步调用宿主配置的 LLM 并返回文本内容。

        :param messages: 对话消息列表
        :return: 模型输出文本
        """
        from app.agent.llm.helper import LLMHelper

        llm = self._run_async(LLMHelper.get_llm(streaming=False))
        response = llm.invoke(messages, config={"configurable": {"timeout": self._ai_timeout}})
        return self._extract_content(response)

    @staticmethod
    def _run_async(value: Any) -> Any:
        """
        兼容宿主 get_llm 的同步/异步返回。

        :param value: 可能的 awaitable
        :return: 实际结果
        """
        import asyncio
        import inspect
        import threading as _threading

        if not inspect.isawaitable(value):
            return value
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(value)
        box: Dict[str, Any] = {}

        def worker() -> None:
            """在独立线程中执行协程。"""
            try:
                box["value"] = asyncio.run(value)
            except BaseException as error:  # noqa: BLE001 - 原样抛回调用方
                box["error"] = error

        thread = _threading.Thread(target=worker, daemon=True)
        thread.start()
        thread.join()
        if "error" in box:
            raise box["error"]
        return box.get("value")

    @staticmethod
    def _extract_content(response: Any) -> str:
        """把模型响应统一转换为文本。"""
        content = getattr(response, "content", response)
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    parts.append(str(item.get("text") or ""))
                else:
                    parts.append(str(item))
            return "".join(parts)
        return str(content or "")

    # ------------------------------------------------------------------
    # 通知
    # ------------------------------------------------------------------

    def _queue_open_notice(self, state: Dict[str, Any], entry: Dict[str, Any], binding: Dict[str, Any]) -> None:
        """把开分通知放入待发送队列。"""
        title = f"【豆瓣开分】{entry.get('title')} {binding.get('rating')} 分"
        text = self._notice_text(entry, binding, change=False)
        self._enqueue(state, title, text)

    def _queue_change_notice(self, state: Dict[str, Any], entry: Dict[str, Any], binding: Dict[str, Any]) -> None:
        """把评分变化通知放入待发送队列。"""
        title = f"【豆瓣评分变动】{entry.get('title')} {binding.get('rating')} 分"
        text = self._notice_text(entry, binding, change=True)
        self._enqueue(state, title, text)

    def _enqueue(self, state: Dict[str, Any], title: str, text: str) -> None:
        """写入待发送通知队列。"""
        notice = {
            "title": title,
            "text": text,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "attempts": 0,
            "next_try": "",
        }
        with self._notice_lock:
            queue = list(state.get("pending_notices") or [])
            queue.append(notice)
            state["pending_notices"] = queue[-100:]
            self._save_state(state)

    def _flush_notices(self, state: Dict[str, Any]) -> None:
        """
        在非静默时段发送队列中的通知。

        先在锁内把待发通知整体取出，再在锁外逐条发送，避免多个线程同时冲刷
        造成重复发送；发送失败的通知按次数退避重试，超过上限后丢弃并记错误日志。
        """
        if not self._notify:
            return
        now = datetime.now()
        if self._in_quiet(now):
            return
        with self._notice_lock:
            queue = list(state.get("pending_notices") or [])
            if not queue:
                return
            ready = [notice for notice in queue if self._notice_ready(notice, now)]
            state["pending_notices"] = [notice for notice in queue if not self._notice_ready(notice, now)]
        failed: List[Dict[str, Any]] = []
        for notice in ready:
            try:
                self.post_message(
                    mtype=MessageType.Plugin,
                    title=str(notice.get("title") or "豆瓣开分通知"),
                    text=str(notice.get("text") or ""),
                )
            except Exception as error:  # noqa: BLE001 - 单条失败按退避重试
                logger.warning(f"【豆瓣开分】通知发送失败：{error}")
                attempts = int(notice.get("attempts") or 0) + 1
                notice["attempts"] = attempts
                if attempts >= self._NOTICE_MAX_ATTEMPTS:
                    logger.error(
                        f"【豆瓣开分】通知连续 {attempts} 次发送失败，已丢弃：{notice.get('title')}"
                    )
                    continue
                notice["next_try"] = (now + timedelta(minutes=10 * attempts)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                failed.append(notice)
        with self._notice_lock:
            pending = list(state.get("pending_notices") or [])
            state["pending_notices"] = (failed + pending)[-100:]
            self._save_state(state)

    @staticmethod
    def _notice_ready(notice: Dict[str, Any], now: datetime) -> bool:
        """
        判断通知是否到了可发送时间。

        :param notice: 队列中的通知
        :param now: 当前时间
        :return: 可以尝试发送时为 True
        """
        planned = DoubanScoreWatch._parse_timestamp(notice.get("next_try"))
        if planned is None:
            return True
        return now >= planned

    def _notice_text(self, entry: Dict[str, Any], binding: Dict[str, Any], change: bool) -> str:
        """生成通知正文。"""
        seasons = "、".join(f"S{item}" for item in entry.get("seasons") or []) or "未知"
        lines = [
            f"媒体：{entry.get('title')}（{entry.get('year') or '年份未知'}，{entry.get('type')}）",
            f"豆瓣条目：{binding.get('title') or binding.get('douban_id')}"
            + (f"（{binding.get('year')}）" if binding.get("year") else ""),
            f"豆瓣评分：{binding.get('rating')} 分（{binding.get('votes')} 人评价）",
            f"首次观测：{binding.get('first_seen') or '未知'}（插件首次读到有效评分的日期，非豆瓣官方开分日）",
            f"关联订阅季：{seasons}",
            f"豆瓣链接：{self._DOUBAN_URL.format(binding.get('douban_id'))}",
        ]
        if change:
            lines.insert(0, "豆瓣评分发生变化：")
        return "\n".join(lines)

    def _in_quiet(self, now: datetime) -> bool:
        """判断当前是否处于静默时段；只填一侧时，另一侧按 00:00 / 23:59 处理。"""
        if not self._quiet_start and not self._quiet_end:
            return False
        return self._in_window(now, self._quiet_start or "00:00", self._quiet_end or "23:59")

    # ------------------------------------------------------------------
    # 计划与时间
    # ------------------------------------------------------------------

    def _ensure_plan(self, state: Dict[str, Any], now: datetime) -> None:
        """确保当天已生成随机的检查时间点。"""
        plan = state.get("plan") or {}
        today = now.date().isoformat()
        if plan.get("date") == today and plan.get("times"):
            return
        times = self._build_plan(now.date())
        state["plan"] = {"date": today, "times": times, "done": 0}
        self._save_state(state)

    def _build_plan(self, day: date) -> List[str]:
        """在检查窗口内随机生成当天的轮次时间。"""
        start = self._clock_minutes(self._window_start, 21 * 60)
        end = self._clock_minutes(self._window_end, 23 * 60)
        if end <= start:
            end = start + 60
        span = max(1, end - start)
        minutes = sorted(random.sample(range(span), min(self._rounds_per_day, span)))
        return [
            f"{(start + offset) // 60:02d}:{(start + offset) % 60:02d}"
            for offset in minutes
        ]

    def _next_plan_time(self, state: Dict[str, Any], now: datetime) -> Optional[datetime]:
        """返回当天下一个尚未执行的计划时间。"""
        plan = state.get("plan") or {}
        times = plan.get("times") or []
        done = int(plan.get("done") or 0)
        if done >= len(times):
            return None
        try:
            hour, minute = [int(part) for part in str(times[done]).split(":", 1)]
        except (TypeError, ValueError):
            return None
        return now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    def _rounds_done(self, state: Dict[str, Any], day: date) -> int:
        """返回当天已完成的轮数。"""
        plan = state.get("plan") or {}
        if plan.get("date") != day.isoformat():
            return 0
        return int(plan.get("done") or 0)

    def _mark_round_done(self, state: Dict[str, Any], day: date) -> None:
        """记录当天完成一轮。"""
        plan = state.setdefault("plan", {})
        if plan.get("date") != day.isoformat():
            plan.update({"date": day.isoformat(), "times": self._build_plan(day), "done": 0})
        plan["done"] = int(plan.get("done") or 0) + 1

    @staticmethod
    def _clock_minutes(value: str, default: int) -> int:
        """把 HH:MM 或 HH:MM:SS 文本转换为当天分钟数。"""
        try:
            parts = str(value).strip().split(":")
            hour, minute = int(parts[0]), int(parts[1])
            return hour * 60 + minute
        except (IndexError, TypeError, ValueError):
            return default

    def _in_window(self, now: datetime, start: str, end: str) -> bool:
        """判断当前时间是否落在指定窗口内，支持跨零点。"""
        if not start or not end:
            return True
        start_minutes = self._clock_minutes(start, 0)
        end_minutes = self._clock_minutes(end, 24 * 60 - 1)
        current = now.hour * 60 + now.minute
        if start_minutes <= end_minutes:
            return start_minutes <= current <= end_minutes
        return current >= start_minutes or current <= end_minutes

    # ------------------------------------------------------------------
    # 配置与状态
    # ------------------------------------------------------------------

    def _load_state(self) -> Dict[str, Any]:
        """读取插件持久化状态。"""
        data = self.get_data(self._STATE_KEY)
        if isinstance(data, dict):
            data.setdefault("entries", {})
            data.setdefault("pending_notices", [])
            return data
        return {"version": 1, "entries": {}, "pending_notices": [], "plan": {}, "last_round": "", "last_result": ""}

    def _save_state(self, state: Optional[Dict[str, Any]] = None) -> None:
        """保存插件持久化状态。"""
        self._state = state or self._state or {}
        self.save_data(self._STATE_KEY, self._state)

    def _apply_manual_bindings(self) -> None:
        """
        把配置中的手动绑定应用到追踪条目。

        配置里删掉的行会解绑对应豆瓣条目；页面手点出来的绑定不受影响。
        """
        entries = (self._state or {}).get("entries") or {}
        desired: Dict[str, set] = {}
        for line in (self._manual_bindings or "").splitlines():
            text = line.strip()
            if not text or "=" not in text:
                continue
            left, right = text.split("=", 1)
            key = left.strip()
            douban_id = right.strip().rstrip("/").rsplit("/", 1)[-1]
            if not key or not douban_id.isdigit():
                continue
            desired.setdefault(key, set()).add(douban_id)
        for key, douban_ids in desired.items():
            entry = entries.get(key)
            if not entry:
                continue
            for douban_id in sorted(douban_ids):
                self._bind(
                    entry,
                    {"douban_id": douban_id, "title": "", "year": ""},
                    "manual",
                    config_key=key,
                )
        for key, entry in entries.items():
            wanted = desired.get(key) or set()
            bindings = entry.get("bindings") or []
            kept = [
                binding
                for binding in bindings
                if not binding.get("config_key") or str(binding.get("douban_id")) in wanted
            ]
            if len(kept) == len(bindings):
                continue
            entry["bindings"] = kept
            if not kept and entry.get("status") == "scored":
                entry["status"] = "pending"

    def _migrate_season_bindings(self) -> None:
        """
        清理旧版本的错误绑定。

        v1：单季订阅且季号大于 1 时，绑定标题不带该季标记（或是别的季）的一律解绑。
        v2：单季订阅（S1）却绑到带「第X季 / 年番X」标记的条目时也解绑，
            让下一轮按累计集数区间重新落位（长篇动画常见这种拆分）。
        """
        state = self._state or {}
        # 旧版本会把已退订条目留在 state 里，这里一次性清掉
        for key in list(state.get("entries") or {}):
            if (state["entries"][key] or {}).get("status") == "removed":
                state["entries"].pop(key, None)
        if not state.get("season_migrated_v1"):
            for entry in (state.get("entries") or {}).values():
                seasons = entry.get("seasons") or []
                if len(seasons) != 1:
                    continue
                wanted = seasons[0]
                if not isinstance(wanted, int) or wanted <= 1:
                    continue
                bindings = entry.get("bindings") or []
                kept = [
                    binding
                    for binding in bindings
                    if season_marker(binding.get("title") or "") == wanted
                ]
                if len(kept) != len(bindings):
                    entry["bindings"] = kept
                    entry["candidates"] = []
                    if not kept:
                        entry["status"] = "pending"
            state["season_migrated_v1"] = True
        if not state.get("episode_migrated_v1"):
            for entry in (state.get("entries") or {}).values():
                seasons = entry.get("seasons") or []
                if seasons != [1]:
                    continue
                bindings = entry.get("bindings") or []
                kept = [
                    binding
                    for binding in bindings
                    if season_marker(binding.get("title") or "") is None
                ]
                if len(kept) != len(bindings):
                    entry["bindings"] = kept
                    entry["candidates"] = []
                    if not kept:
                        entry["status"] = "pending"
            state["episode_migrated_v1"] = True
        self._save_state(state)

    @staticmethod
    def _to_int(value: Any, default: int, minimum: int, maximum: int) -> int:
        """转换为区间内的整数。"""
        try:
            result = int(value)
        except (TypeError, ValueError):
            return default
        return max(minimum, min(maximum, result))

    @staticmethod
    def _to_float(value: Any, default: float, minimum: float, maximum: float) -> float:
        """转换为区间内的浮点数。"""
        try:
            result = float(value)
        except (TypeError, ValueError):
            return default
        return max(minimum, min(maximum, result))

    @staticmethod
    def _to_clock(value: Any, default: str) -> str:
        """校验 HH:MM 格式文本，非法时返回默认值。"""
        text = str(value or "").strip()
        if not text:
            return default
        try:
            parts = text.split(":")
            hour, minute = int(parts[0]), int(parts[1])
        except (IndexError, TypeError, ValueError):
            return default
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"
        return default

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------

    def api_check_now(self) -> Dict[str, Any]:
        """API：在后台立即执行一轮检查。"""
        if not self._enabled:
            return {"success": False, "message": "插件未启用", "data": None}
        if self._state is None:
            self._state = self._load_state()
        if self._round_lock.locked():
            return {"success": True, "message": "上一轮仍在执行，请稍后刷新", "data": None}

        def worker() -> None:
            """后台执行检查并发送通知。"""
            state = self._state
            result = self._run_round(state, scheduled=False)
            self._flush_notices(state)
            logger.info(f"【豆瓣开分】手动检查完成：{result}")

        threading.Thread(target=worker, daemon=True).start()
        return {"success": True, "message": "已在后台开始检查，稍后刷新查看结果", "data": None}

    def api_retry(self, key: str = "") -> Dict[str, Any]:
        """API：重置指定媒体的匹配状态。"""
        entry = self._find_entry(key)
        if not entry:
            return {"success": False, "message": "未找到该追踪记录", "data": None}
        entry["bindings"] = []
        entry["candidates"] = []
        entry["ai_suggestions"] = []
        entry["status"] = "pending"
        entry["manual_skip"] = False
        entry["tries"] = 0
        entry.pop("next_try", None)
        self._save_state()
        return {"success": True, "message": "已重置，下一轮重新匹配", "data": None}

    def api_ignore(self, key: str = "") -> Dict[str, Any]:
        """API：忽略指定媒体。"""
        entry = self._find_entry(key)
        if not entry:
            return {"success": False, "message": "未找到该追踪记录", "data": None}
        entry["status"] = "ignored"
        self._save_state()
        return {"success": True, "message": "已忽略", "data": None}

    def api_restore(self, key: str = "") -> Dict[str, Any]:
        """API：恢复指定媒体的追踪。"""
        entry = self._find_entry(key)
        if not entry:
            return {"success": False, "message": "未找到该追踪记录", "data": None}
        entry["status"] = "pending"
        self._save_state()
        return {"success": True, "message": "已恢复追踪", "data": None}

    def api_bind(self, key: str = "", douban_id: str = "") -> Dict[str, Any]:
        """API：手动绑定豆瓣条目。"""
        entry = self._find_entry(key)
        if not entry:
            return {"success": False, "message": "未找到该追踪记录", "data": None}
        douban_id = str(douban_id or "").strip()
        if not douban_id.isdigit():
            return {"success": False, "message": "豆瓣 ID 必须是数字", "data": None}
        # 不在请求线程里访问豆瓣：先记基线待确认，下一轮读到评分再判断是否补发
        self._bind(entry, {"douban_id": douban_id, "title": "", "year": ""}, "manual")
        entry["ai_suggestions"] = []
        self._save_state()
        return {
            "success": True,
            "message": f"已绑定豆瓣 {douban_id}，下一轮读取评分（绑定前已有分的不补发开分通知）",
            "data": None,
        }

    def api_unbind(self, key: str = "", douban_id: str = "") -> Dict[str, Any]:
        """API：解除指定豆瓣条目绑定。"""
        entry = self._find_entry(key)
        if not entry:
            return {"success": False, "message": "未找到该追踪记录", "data": None}
        bindings = [
            item for item in entry.get("bindings") or [] if str(item.get("douban_id")) != str(douban_id)
        ]
        entry["bindings"] = bindings
        if not bindings:
            entry["status"] = "pending"
        self._save_state()
        return {"success": True, "message": "已解除绑定", "data": None}

    def api_test(self, douban_id: str = "", type_name: str = "电视剧") -> Dict[str, Any]:
        """API：测试豆瓣接口与 Cookie。"""
        douban_id = str(douban_id or "").strip()
        if not douban_id.isdigit():
            return {"success": False, "message": "请填写数字豆瓣 ID", "data": None}
        client = DoubanScoreClient(cookie=self._douban_cookie, timeout=8)
        return client.test(douban_id, type_name or "电视剧")

    def _find_entry(self, key: str) -> Optional[Dict[str, Any]]:
        """按媒体键查找追踪条目。"""
        if not key:
            return None
        entries = (self._state or {}).get("entries") or {}
        entry = entries.get(key)
        return entry if isinstance(entry, dict) else None
