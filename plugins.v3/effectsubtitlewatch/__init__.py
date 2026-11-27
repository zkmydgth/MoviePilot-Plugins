#!/usr/bin/env python3
"""
特效字幕资源监控（EffectSubtitleWatch）MoviePilot 插件。

职责只有一件事：定时浏览各站点最新种子，发现标题 / 副标题 / 标签带“特效”的
影视资源后推送通知。**不下载、不订阅、不改媒体库。**

数据来源
--------
``TorrentsChain().browse(domain, keyword=...)`` —— 与 MP 站点搜索同一条链路，
沿用站点 Cookie、代理、限流与分类适配，不另写爬虫。

去重
----
- 站内：同一轮按 (站点, 标题, 字节大小) 去重。
- 跨站（严格）：``规范化标题 + 精确字节大小`` 相同才视为同一资源，
  合并为一条通知并列出所有来源站点；链接取站点优先级最高的一条。

iOS App 兼容
------------
配置表单只用 VSwitch / VTextField / VAlert；详情页只输出文本块；
通知走 ``post_message``。不使用 Vue 联邦、自定义 JS、外链字体或静态资源。
"""

from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.plugins import _PluginBase
from app.runtime.log import logger
from app.schemas.types import MessageType
from apscheduler.triggers.cron import CronTrigger

try:  # 旧宿主可能没有一次性任务门面
    from app.sdk.scheduler import add_plugin_once_job
except Exception:  # pragma: no cover - 宿主版本差异分支
    add_plugin_once_job = None

try:  # 组名与画质解析依赖宿主解析器，测试桩中可能不存在
    from app.domain.metainfo import MetaInfo
except Exception:  # pragma: no cover - 宿主版本差异分支
    MetaInfo = None

from .version import VERSION

# ============================ 常量 ============================

DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": False,
    "notify": True,
    "keywords": "特效",
    "match_scope": "all",
    "sites": [],
    "interval_minutes": 360,
    "exclude_words": "求片,求字幕",
    "baseline_on_first_run": True,
    "max_items": 10,
    "notify_new_site": False,
    "team_patterns": "",
    "run_once": False,
}

#: 去重记录保留天数
SEEN_KEEP_DAYS = 30
#: 去重记录上限
SEEN_LIMIT = 5000
#: 详情页展示的最近命中条数
RECENT_LIMIT = 20
#: 单轮并发站点数
MAX_WORKERS = 4
#: 单轮最多调用识别链的条数，避免一轮打太多元数据请求
MAX_RECOGNIZE_PER_ROUND = 20
#: 调度心跳间隔（分钟）：心跳只判断“是否到期”，宿主重载不会重置检查周期
TICK_MINUTES = 5

#: 尾部组名误判黑名单（编码、音频、分辨率等）
_TEAM_BLOCKLIST = {
    "hd", "ma", "dts", "ddp", "aac", "atmos", "truehd", "x264", "x265",
    "h264", "h265", "hevc", "avc", "web", "dl", "webdl", "bluray", "remux",
    "hdr", "dovi", "dv", "uhd", "4k", "2160p", "1080p", "720p", "10bit",
    "8bit", "hfr", "60fps", "sdr", "hq", "rip",
}

#: 允许的种子分类
_ALLOWED_CATEGORIES = {"", "电影", "电视剧"}

#: 匹配范围选项：标题+站点标签（精准）/ 全部字段（含副标题，覆盖广但噪音大）
MATCH_SCOPE_OPTIONS = (
    {"title": "仅标题", "value": "title_only"},
    {"title": "标题 + 站点标签", "value": "title_labels"},
    {"title": "标题 + 标签 + 副标题", "value": "all"},
)


# ============================ 纯函数 ============================


def _split_terms(value: Any) -> List[str]:
    """按逗号、分号与换行拆分配置文本，去空并保持顺序去重。"""
    if value is None:
        return []
    parts = re.split(r"[,，;；\n\r]+", str(value))
    result: List[str] = []
    for part in parts:
        term = part.strip()
        if term and term not in result:
            result.append(term)
    return result


def _normalize_title(title: str) -> str:
    """规范化标题：去常见扩展名、转小写、标点压成单个空格。"""
    text = (title or "").strip()
    text = re.sub(r"\.(?:mkv|mp4|ts|iso|ass|ssa|srt|zip|rar|7z)$", "", text, flags=re.I)
    text = text.lower()
    text = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _fingerprint(title: str, size: Any) -> str:
    """严格指纹：规范化标题 + 精确字节大小。"""
    try:
        size_value = int(round(float(size or 0)))
    except (TypeError, ValueError):
        size_value = 0
    return f"{_normalize_title(title)}|{size_value}"


def _format_size(size: Any) -> str:
    """把字节数格式化为 GB / TB 文本。"""
    try:
        value = float(size or 0)
    except (TypeError, ValueError):
        return "未知"
    if value <= 0:
        return "未知"
    if value >= 1024 ** 4:
        return f"{value / 1024 ** 4:.2f} TB"
    if value >= 1024 ** 3:
        return f"{value / 1024 ** 3:.1f} GB"
    if value >= 1024 ** 2:
        return f"{value / 1024 ** 2:.0f} MB"
    return f"{value / 1024:.0f} KB"


def _parse_site_ids(value: Any) -> List[int]:
    """把站点配置（多选列表或旧版逗号文本）解析为去重整数列表，非法项忽略。"""
    if isinstance(value, (list, tuple, set)):
        raw_terms: List[Any] = list(value)
    else:
        raw_terms = _split_terms(value)
    result: List[int] = []
    for term in raw_terms:
        try:
            site_id = int(term)
        except (TypeError, ValueError):
            continue
        if site_id > 0 and site_id not in result:
            result.append(site_id)
    return result


def _season_episode_text(meta: Any) -> str:
    """把识别出的季集号格式化为 SxxExx 文本，未识别时返回空串。"""
    parts: List[str] = []
    try:
        season = getattr(meta, "begin_season", None)
        episode = getattr(meta, "begin_episode", None)
        if season:
            parts.append(f"S{int(season):02d}")
        if episode:
            parts.append(f"E{int(episode):02d}")
    except (TypeError, ValueError):
        return ""
    return "".join(parts)


def _parse_state_time(value: Any) -> Optional[datetime]:
    """把状态里保存的 ISO 时间解析为 datetime，缺失或非法时返回 None。"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _is_due(
    last_run: Any, interval_minutes: int, now: Optional[datetime] = None
) -> bool:
    """按上次运行时间判断是否到期；没有可用记录或时间倒流时视为到期。"""
    last = _parse_state_time(last_run)
    if last is None:
        return True
    current = now or datetime.now()
    if current < last:
        return True
    try:
        interval = max(1, int(interval_minutes))
    except (TypeError, ValueError):
        interval = 1
    return current - last >= timedelta(minutes=interval)


def _next_due_at(last_run: Any, interval_minutes: int) -> Optional[datetime]:
    """算出下一次到期时间，用于心跳判定与详情页展示；无记录时返回 None。"""
    last = _parse_state_time(last_run)
    if last is None:
        return None
    try:
        interval = max(1, int(interval_minutes))
    except (TypeError, ValueError):
        interval = 1
    return last + timedelta(minutes=interval)


def _compose_display(cn_name: Any, year: Any, season_episode: str, fallback: str) -> str:
    """拼出通知里显示的中文名；拿不到中文名时退回原标题。"""
    name = str(cn_name or "").strip()
    if not name:
        return fallback
    text = name
    try:
        if year:
            text += f"（{int(year)}）"
    except (TypeError, ValueError):
        pass
    if season_episode:
        text += f" {season_episode}"
    return text


def _short(value: Any, limit: int = 55) -> str:
    """压成单行并按字符数截断，避免 App 端长文本挤压布局。"""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else f"{text[:limit - 1]}…"


def _pubdate_rank(value: str) -> int:
    """把发布时间文本压成可排序整数，缺失或异常时返回 0。"""
    digits = re.sub(r"\D", "", value or "")
    return int(digits[:14]) if digits else 0


def _extract_team_by_pattern(title: str) -> str:
    """用标题结构兜底提取制作组：字幕组括号 → 尾部 -组名 / @组名。"""
    text = (title or "").strip()
    if not text:
        return ""
    bracket = re.search(r"【([^】]{1,40})】", text)
    if bracket:
        inner = bracket.group(1)
        matched = re.search(
            r"([A-Za-z0-9._-]{0,12}[\u4e00-\u9fa5]{2,12}(?:字幕组|字幕社|压制组|制作组))",
            inner,
        )
        if matched:
            return matched.group(1).strip()
    candidates = re.findall(r"[-@]([A-Za-z][A-Za-z0-9@._-]{1,30})(?=[.\s\[\]()（）]|$)", text)
    for candidate in reversed(candidates):
        if candidate.lower().replace(".", "") in _TEAM_BLOCKLIST:
            continue
        return candidate
    return ""


def _parse_title(title: str, extra_patterns: Optional[List[str]] = None) -> Dict[str, str]:
    """一次解析标题，返回制作组与画质标记。"""
    result = {"team": "", "quality": ""}
    text = (title or "").strip()
    if not text:
        return result
    meta = None
    if MetaInfo is not None:
        try:
            meta = MetaInfo(title=text)
        except Exception:  # noqa: BLE001 - 解析失败只降级，不影响通知
            meta = None
    if meta is not None:
        team = getattr(meta, "resource_team", None)
        if team:
            result["team"] = str(team).strip()
        pixels = str(getattr(meta, "resource_pix", "") or "").strip()
        edition = str(getattr(meta, "edition", "") or "").strip()
        result["quality"] = " ".join(part for part in (pixels, edition) if part)
    if not result["team"]:
        for pattern in extra_patterns or []:
            try:
                matched = re.search(pattern, text)
            except re.error:
                continue
            if matched:
                value = (matched.group(1) if matched.groups() else matched.group(0)).strip()
                if value:
                    result["team"] = value
                    break
    if not result["team"]:
        result["team"] = _extract_team_by_pattern(text)
    if not result["quality"]:
        matched = re.search(r"(2160p|1080p|720p|4K|UHD)", text, re.I)
        result["quality"] = matched.group(1) if matched else ""
    return result


def _group_candidates(
    candidates: List[Dict[str, Any]], team_patterns: Optional[List[str]] = None
) -> Dict[str, Dict[str, Any]]:
    """按严格指纹把候选合并成资源条目，并选出优先级最高的链接。"""
    grouped: Dict[str, Dict[str, Any]] = {}
    for item in candidates:
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        fingerprint = _fingerprint(title, item.get("size"))
        entry = grouped.get(fingerprint)
        if entry is None:
            parsed = _parse_title(title, team_patterns)
            entry = {
                "fp": fingerprint,
                "title": title,
                "size": item.get("size") or 0,
                "pubdate": str(item.get("pubdate") or ""),
                "team": parsed["team"],
                "quality": parsed["quality"],
                "sites": [],
                "link": "",
                "best_pri": 9999,
            }
            grouped[fingerprint] = entry
        site_id = int(item.get("site_id") or 0)
        if site_id and not any(site["id"] == site_id for site in entry["sites"]):
            entry["sites"].append(
                {
                    "id": site_id,
                    "name": str(item.get("site_name") or ""),
                    "pri": int(item.get("pri") or 0),
                    "url": str(item.get("page_url") or ""),
                }
            )
        priority = int(item.get("pri") or 0)
        if priority < entry["best_pri"]:
            entry["best_pri"] = priority
            entry_link = str(item.get("page_url") or "")
            if entry_link:
                entry["link"] = entry_link
    for entry in grouped.values():
        entry["sites"].sort(key=lambda site: (site["pri"], site["name"]))
    return grouped


def _classify(
    grouped: Dict[str, Dict[str, Any]],
    state: Dict[str, Any],
    notify_new_site: bool,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """区分“全新资源”与“老资源出现在新站点”，供通知使用。"""
    known_items = state.get("items") or {}
    new_items: List[Dict[str, Any]] = []
    new_site_items: List[Dict[str, Any]] = []
    for fingerprint, entry in grouped.items():
        known = known_items.get(fingerprint)
        if not known:
            new_items.append(entry)
            continue
        if not notify_new_site:
            continue
        known_sites = {str(key) for key in (known.get("sites") or {}).keys()}
        fresh = [site for site in entry["sites"] if str(site["id"]) not in known_sites]
        if fresh:
            extra = dict(entry)
            extra["new_sites"] = fresh
            new_site_items.append(extra)
    return new_items, new_site_items


def _prune_seen(
    items: Dict[str, Any], keep_days: int = SEEN_KEEP_DAYS, limit: int = SEEN_LIMIT
) -> Dict[str, Any]:
    """删除超期与超量的去重记录，返回新字典。"""
    if not isinstance(items, dict):
        return {}
    cutoff = (datetime.now() - timedelta(days=keep_days)).isoformat(timespec="seconds")
    kept = {
        key: value
        for key, value in items.items()
        if str(value.get("last") or value.get("first") or "") >= cutoff
    }
    if len(kept) > limit:
        ordered = sorted(
            kept.items(),
            key=lambda pair: str(pair[1].get("last") or pair[1].get("first") or ""),
            reverse=True,
        )[:limit]
        kept = dict(ordered)
    return kept


def _build_recent(
    grouped: Dict[str, Dict[str, Any]], names: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """生成详情页与状态用的最近命中列表，优先显示识别后的中文名。"""
    names = names or {}
    entries = sorted(
        grouped.values(),
        key=lambda item: (str(item.get("pubdate") or ""), str(item.get("title") or "")),
        reverse=True,
    )[:RECENT_LIMIT]
    recent: List[Dict[str, Any]] = []
    for entry in entries:
        raw_title = str(entry.get("title") or "")
        display = str(names.get(entry.get("fp")) or raw_title)
        recent.append(
            {
                "title": _short(display, 70),
                "raw": _short(raw_title, 70) if display != raw_title else "",
                "team": entry.get("team") or "未知",
                "quality": entry.get("quality") or "",
                "size": _format_size(entry.get("size")),
                "pubdate": str(entry.get("pubdate") or "")[:10],
                "sites": [site["name"] for site in entry.get("sites") or []],
                "link": entry.get("link") or "",
            }
        )
    return recent


# ============================ 插件 ============================


class EffectSubtitleWatch(_PluginBase):
    """定时监控带“特效”字样的站点资源，并只推送通知。"""

    plugin_name = "特效字幕监控"
    plugin_desc = "定时浏览站点最新种子，发现标题/副标题/标签带“特效”的影视资源后推送通知，不下载、不订阅。"
    plugin_icon = "effectsubtitlewatch.png"
    plugin_version = VERSION
    plugin_label = "站点,通知,字幕"
    plugin_author = "zkmydgth"
    plugin_config_prefix = "effectsubtitlewatch_"
    plugin_order = 60
    auth_level = 1

    def __init__(self) -> None:
        """初始化运行容器。"""
        super().__init__()
        self._config: Dict[str, Any] = dict(DEFAULT_CONFIG)
        self._enabled = False
        self._notify_enabled = True
        self._keywords: List[str] = ["特效"]
        self._match_scope = "title_labels"
        self._site_ids: List[int] = []
        self._interval_minutes = 360
        self._exclude_words: List[str] = []
        self._baseline_on_first_run = True
        self._max_items = 10
        self._notify_new_site = False
        self._team_patterns: List[str] = []
        self._state: Dict[str, Any] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._next_check_at: Optional[datetime] = None

    def init_plugin(self, config: dict = None) -> None:
        """读取配置、恢复去重状态，并按需安排一次立即检查。"""
        self.stop_service()
        source = config or {}
        self._config = {
            key: source.get(key, default) for key, default in DEFAULT_CONFIG.items()
        }
        self._enabled = bool(self._config.get("enabled"))
        self._notify_enabled = bool(self._config.get("notify", True))
        self._keywords = _split_terms(self._config.get("keywords")) or ["特效"]
        scope = str(self._config.get("match_scope") or "title_labels").strip()
        self._match_scope = (
            scope if scope in {item["value"] for item in MATCH_SCOPE_OPTIONS} else "title_labels"
        )
        self._site_ids = _parse_site_ids(self._config.get("sites"))
        self._interval_minutes = self._to_int(
            self._config.get("interval_minutes"), 360, 30, 10080
        )
        self._exclude_words = _split_terms(self._config.get("exclude_words"))
        self._baseline_on_first_run = bool(self._config.get("baseline_on_first_run", True))
        self._max_items = self._to_int(self._config.get("max_items"), 10, 1, 50)
        self._notify_new_site = bool(self._config.get("notify_new_site", False))
        self._team_patterns = _split_terms(self._config.get("team_patterns"))
        self._stop_event = threading.Event()
        self._state = self._load_state()
        self._next_check_at = _next_due_at(
            self._state.get("last_run"), self._interval_minutes
        )
        if bool(self._config.get("run_once")):
            self._schedule_once()

    def get_state(self) -> bool:
        """返回插件启用状态。"""
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """返回远程命令列表（本插件不提供命令）。"""
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        """返回插件 API 列表（本插件不提供 API）。"""
        return []

    def get_service(self) -> List[Dict[str, Any]]:
        """注册分钟级心跳；心跳只判断是否到期，重载或重启不会重置检查周期。"""
        if not self._enabled:
            return []
        return [
            {
                "id": "EffectSubtitleWatch",
                "name": "特效资源检查",
                "trigger": CronTrigger(minute=f"*/{TICK_MINUTES}"),
                "func": self._tick,
                "kwargs": {},
            }
        ]

    def stop_service(self) -> None:
        """停止后台检查并释放运行标记。"""
        event = getattr(self, "_stop_event", None)
        if event is not None:
            event.set()

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """返回配置表单与默认配置（只用 App 端可渲染的基础控件）。"""
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
                                            "text": "定时浏览站点最新种子，按“匹配范围”查找关键词（默认“特效”）。"
                                                    "推送时显示识别后的中文名，识别不到才退回原标题。"
                                                    "只通知，不下载、不订阅；跨站按“标题 + 精确大小”合并，链接取站点优先级最高的一条。"
                                                    "首次运行只建立基线，之后只推新增。",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "enabled", "label": "启用插件"},
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "notify", "label": "发送通知"},
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "keywords",
                                            "label": "关键词（逗号分隔）",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "match_scope",
                                            "label": "匹配范围",
                                            "items": [dict(item) for item in MATCH_SCOPE_OPTIONS],
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "sites",
                                            "label": "监控站点（留空=全部）",
                                            "items": self._site_options(),
                                            "multiple": True,
                                            "chips": True,
                                            "clearable": True,
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "interval_minutes",
                                            "label": "检查间隔（分钟）",
                                            "type": "number",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "exclude_words",
                                            "label": "排除词（逗号分隔）",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "baseline_on_first_run",
                                            "label": "首次运行只建立基线",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "max_items",
                                            "label": "单次最多推送条数",
                                            "type": "number",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "notify_new_site",
                                            "label": "老资源新增站点时补提示",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "team_patterns",
                                            "label": "制作组补充正则（可选）",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "run_once",
                                            "label": "保存后立即检查一次",
                                        },
                                    }
                                ],
                            },
                        ],
                    }
                ],
            }
        ], dict(DEFAULT_CONFIG)

    def get_page(self) -> Optional[List[dict]]:
        """返回详情页：只用文本块，保证 App 端可渲染。"""
        state = self._state or self._load_state()
        summary = state.get("last_summary") or {}
        next_due = self._next_check_at or _next_due_at(
            state.get("last_run"), self._interval_minutes
        )
        header_lines = [
            f"状态：{'已启用' if self._enabled else '未启用'}",
            f"关键词：{'、'.join(self._keywords)}",
            f"检查间隔：{self._interval_minutes} 分钟",
            f"上次检查：{state.get('last_run') or '尚未运行'}",
            f"下次检查：{next_due.strftime('%Y-%m-%d %H:%M') if next_due else '下次心跳即执行'}",
        ]
        if summary:
            header_lines.append(
                f"上次结果：候选 {summary.get('matched', 0)} 条 · 新增 {summary.get('new', 0)} 条"
            )
        pages: List[dict] = [
            {
                "component": "VAlert",
                "props": {
                    "type": "info",
                    "variant": "tonal",
                    "text": "\n".join(header_lines),
                },
            }
        ]
        site_rows = summary.get("sites") or []
        if site_rows:
            lines = []
            for row in site_rows[:20]:
                if row.get("error"):
                    lines.append(f"{row.get('name')}：失败（{_short(row.get('error'), 40)}）")
                else:
                    lines.append(f"{row.get('name')}：返回 {row.get('count', 0)} 条")
            pages.append(
                {
                    "component": "VAlert",
                    "props": {
                        "type": "info",
                        "variant": "outlined",
                        "text": "站点情况\n" + "\n".join(lines),
                    },
                }
            )
        recent = state.get("recent") or []
        if recent:
            lines = []
            for item in recent[:10]:
                meta = " · ".join(
                    part
                    for part in (
                        f"制作组：{item.get('team') or '未知'}",
                        str(item.get("quality") or ""),
                        str(item.get("size") or ""),
                        str(item.get("pubdate") or ""),
                    )
                    if part
                )
                sites = "、".join(item.get("sites") or [])
                block = f"· {_short(item.get('title'), 60)}\n  {meta}\n  {sites}"
                if item.get("raw"):
                    block += f"\n  原名：{item['raw']}"
                lines.append(block)
            pages.append(
                {
                    "component": "VAlert",
                    "props": {
                        "type": "success",
                        "variant": "tonal",
                        "text": "最近命中\n" + "\n".join(lines),
                    },
                }
            )
        else:
            pages.append(
                {
                    "component": "VAlert",
                    "props": {
                        "type": "warning",
                        "variant": "tonal",
                        "text": "还没有命中记录。首次运行会建立基线，之后只推送新增资源。",
                    },
                }
            )
        return pages

    # ==================== 定时与执行 ====================

    def _tick(self) -> None:
        """心跳入口：上次运行已超过检查间隔才启动一轮，避免重载重置周期。"""
        if not self._enabled:
            return
        now = datetime.now()
        if self._next_check_at is not None and now < self._next_check_at:
            return
        self._start_round("定时")

    def _schedule_once(self) -> None:
        """安排一次立即检查，并复位“保存后立即检查”开关。"""
        scheduled = False
        if add_plugin_once_job is not None:
            try:
                scheduled = bool(
                    add_plugin_once_job(
                        self.__class__.__name__,
                        "run_once",
                        self._run_once_job,
                        "立即检查一次特效资源",
                        3,
                    )
                )
            except Exception as err:  # noqa: BLE001 - 调度失败时退回线程
                logger.warning("【特效字幕监控】调度立即检查失败：%s", err)
        if not scheduled:
            threading.Thread(target=self._run_once_job, daemon=True).start()
        self._config["run_once"] = False
        try:
            self.update_config(dict(self._config))
        except Exception as err:  # noqa: BLE001 - 配置复位失败不影响本轮
            logger.warning("【特效字幕监控】复位立即检查开关失败：%s", err)

    def _run_once_job(self) -> None:
        """执行一次手动触发的检查。"""
        self._start_round("手动")

    def _start_round(self, trigger: str) -> None:
        """在后台线程中执行一轮检查。"""
        thread = threading.Thread(target=self._round_with_lock, args=(trigger,), daemon=True)
        thread.start()

    def _round_with_lock(self, trigger: str) -> None:
        """加锁执行一轮，上一轮未结束时直接跳过。"""
        lock = getattr(self, "_lock", None)
        if lock is None:
            return
        if not lock.acquire(blocking=False):
            logger.info("【特效字幕监控】上一轮仍在执行，本次跳过")
            return
        try:
            self._run_round(trigger)
        except Exception as err:  # noqa: BLE001 - 单轮失败只记日志
            logger.error("【特效字幕监控】本轮执行失败：%s", err)
        finally:
            self._next_check_at = datetime.now() + timedelta(minutes=self._interval_minutes)
            lock.release()

    def _run_round(self, trigger: str) -> None:
        """执行一轮：取站点 → 过滤 → 归类 → 通知 → 落库。"""
        started = datetime.now()
        state = self._load_state()
        sites = self._resolve_sites()
        if not sites:
            logger.warning("【特效字幕监控】没有可用的已启用站点")
            state["last_run"] = started.isoformat(timespec="seconds")
            state["last_summary"] = {"trigger": trigger, "sites": [], "matched": 0, "new": 0}
            self._save_state(state)
            return
        site_stats, candidates = self._collect(sites)
        matched = self._filter_candidates(candidates)
        grouped = _group_candidates(matched, self._team_patterns)
        logger.info(
            "【特效字幕监控】本轮触发=%s 站点=%d 候选=%d 命中=%d",
            trigger, len(sites), len(candidates), len(grouped),
        )
        if not state.get("baseline_done"):
            if self._baseline_on_first_run:
                self._fill_names(state, grouped)
                self._decorate_names(state, grouped)
                self._apply_baseline(state, grouped, started)
                state["last_summary"] = self._summary(trigger, site_stats, len(grouped), 0)
                state["last_run"] = started.isoformat(timespec="seconds")
                state["recent"] = _build_recent(grouped, state.get("names"))
                self._prune_names(state)
                self._save_state(state)
                self._notify_baseline(len(grouped))
                return
            state["baseline_done"] = True
        new_items, new_site_items = _classify(grouped, state, self._notify_new_site)
        more = max(0, len(new_items) - self._max_items)
        priority = [entry.get("fp") for entry in (new_items + new_site_items)]
        self._fill_names(state, grouped, priority)
        self._decorate_names(state, grouped)
        self._record(state, grouped, new_items, new_site_items)
        state["last_summary"] = self._summary(trigger, site_stats, len(grouped), len(new_items))
        state["last_run"] = started.isoformat(timespec="seconds")
        state["recent"] = _build_recent(grouped, state.get("names"))
        state["items"] = _prune_seen(state.get("items") or {})
        self._prune_names(state)
        self._save_state(state)
        if new_items or new_site_items:
            self._notify_new(new_items, new_site_items, more)
        else:
            logger.info("【特效字幕监控】没有新增资源")

    # ==================== 站点与候选 ====================

    def _site_options(self) -> List[Dict[str, Any]]:
        """生成配置表单的站点多选项，读取失败时返回空列表。"""
        try:
            from app.db.oper.site import SiteOper

            rows = SiteOper().list_order_by_pri() or []
        except Exception as err:  # noqa: BLE001 - 宿主接口差异只记日志
            logger.warning("【特效字幕监控】读取站点下拉项失败：%s", err)
            return []
        items: List[Dict[str, Any]] = []
        for row in rows:
            if not bool(getattr(row, "is_active", True)):
                continue
            site_id = getattr(row, "id", None)
            if not site_id:
                continue
            name = str(getattr(row, "name", "") or getattr(row, "domain", "") or site_id)
            items.append({"title": name, "value": int(site_id)})
        return items

    def _resolve_sites(self) -> List[Dict[str, Any]]:
        """读取已启用站点，必要时按配置的站点 ID 过滤。"""
        try:
            from app.db.oper.site import SiteOper

            rows = SiteOper().list_order_by_pri() or []
        except Exception as err:  # noqa: BLE001 - 宿主接口差异只记日志
            logger.error("【特效字幕监控】读取站点失败：%s", err)
            return []
        sites: List[Dict[str, Any]] = []
        for row in rows:
            if not bool(getattr(row, "is_active", True)):
                continue
            site_id = getattr(row, "id", None)
            domain = str(getattr(row, "domain", "") or "").strip()
            if not site_id or not domain:
                continue
            if self._site_ids and int(site_id) not in self._site_ids:
                continue
            sites.append(
                {
                    "id": int(site_id),
                    "name": str(getattr(row, "name", "") or domain),
                    "domain": domain,
                    "pri": int(getattr(row, "pri", 0) or 0),
                }
            )
        return sites

    def _collect(
        self, sites: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """并发浏览各站点最新种子，返回站点统计与候选列表。"""
        try:
            from app.chain.torrents import TorrentsChain
        except Exception as err:  # noqa: BLE001 - 宿主接口缺失直接返回空
            logger.error("【特效字幕监控】导入站点浏览链失败：%s", err)
            return [], []

        stats: Dict[int, Dict[str, Any]] = {
            site["id"]: {"name": site["name"], "count": 0, "error": ""} for site in sites
        }
        candidates: List[Dict[str, Any]] = []
        tasks = [(site, keyword) for site in sites for keyword in self._keywords]

        def fetch(site: Dict[str, Any], keyword: str) -> List[Any]:
            """同步浏览一个站点的最新种子。"""
            return TorrentsChain().browse(domain=site["domain"], keyword=keyword, page=0)

        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, max(1, len(tasks)))) as pool:
            futures = {pool.submit(fetch, site, keyword): site for site, keyword in tasks}
            for future in as_completed(futures):
                site = futures[future]
                if self._stop_event.is_set():
                    break
                try:
                    rows = future.result() or []
                except Exception as err:  # noqa: BLE001 - 单站失败不影响其他站
                    stats[site["id"]]["error"] = _short(err, 80)
                    logger.warning("【特效字幕监控】站点 %s 查询失败：%s", site["name"], err)
                    continue
                stats[site["id"]]["count"] += len(rows)
                for row in rows:
                    candidates.append(self._to_candidate(site, row))
        return list(stats.values()), candidates

    @staticmethod
    def _to_candidate(site: Dict[str, Any], row: Any) -> Dict[str, Any]:
        """把宿主 TorrentInfo 归一化为普通字典。"""
        labels = getattr(row, "labels", None) or []
        return {
            "site_id": int(site.get("id") or 0),
            "site_name": str(site.get("name") or ""),
            "pri": int(site.get("pri") or 0),
            "title": str(getattr(row, "title", "") or ""),
            "description": str(getattr(row, "description", "") or ""),
            "size": float(getattr(row, "size", 0) or 0),
            "pubdate": str(getattr(row, "pubdate", "") or ""),
            "page_url": str(getattr(row, "page_url", "") or ""),
            "labels": [str(label) for label in labels],
            "category": str(getattr(row, "category", "") or ""),
        }

    def _filter_candidates(self, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """按分类、关键词、排除词与大小过滤候选。"""
        result: List[Dict[str, Any]] = []
        seen_keys = set()
        keywords = [term.casefold() for term in self._keywords]
        excludes = [term.casefold() for term in self._exclude_words]
        for item in candidates:
            if item["size"] <= 0 or not item["title"]:
                continue
            if str(item.get("category") or "") not in _ALLOWED_CATEGORIES:
                continue
            if self._match_scope == "all":
                blob = f"{item['title']} {item['description']} {' '.join(item['labels'])}"
            elif self._match_scope == "title_only":
                blob = item["title"]
            else:
                blob = f"{item['title']} {' '.join(item['labels'])}"
            blob = blob.casefold()
            if not any(keyword in blob for keyword in keywords):
                continue
            if any(word in blob for word in excludes):
                continue
            key = (item["site_id"], item["title"], int(item["size"]))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            result.append(item)
        return result

    # ==================== 名称识别 ====================

    def _recognize_display(self, title: str) -> str:
        """用宿主识别链把种子标题换成中文名；失败或仍是原名时返回空串。"""
        if MetaInfo is None or not title:
            return ""
        try:
            meta = MetaInfo(title=title)
        except Exception:  # noqa: BLE001 - 解析失败按未识别处理
            return ""
        raw_name = str(getattr(meta, "name", "") or "").strip()
        if not raw_name:
            return ""
        try:
            media = self.chain.recognize_media(meta=meta)
        except Exception as err:  # noqa: BLE001 - 识别失败退回原标题
            logger.warning("【特效字幕监控】识别失败（%s）：%s", _short(title, 40), err)
            return ""
        if not media:
            return ""
        cn_name = str(getattr(media, "title", "") or "").strip()
        meta_cn = str(getattr(meta, "cn_name", "") or "").strip()
        if meta_cn and (not cn_name or cn_name.casefold() == raw_name.casefold()):
            cn_name = meta_cn
        if not cn_name or cn_name.casefold() == raw_name.casefold():
            return ""
        return _compose_display(
            cn_name, getattr(media, "year", None), _season_episode_text(meta), title
        )

    def _fill_names(
        self,
        state: Dict[str, Any],
        grouped: Dict[str, Dict[str, Any]],
        priority_fps: Optional[List[Any]] = None,
        limit: int = MAX_RECOGNIZE_PER_ROUND,
    ) -> int:
        """为待展示资源补齐中文名，命中缓存的不再请求识别，返回本次新增条数。"""
        names = state.get("names")
        if not isinstance(names, dict):
            names = {}
            state["names"] = names
        pending = [
            entry for entry in grouped.values() if entry.get("fp") and entry["fp"] not in names
        ]
        if not pending:
            return 0
        order = {str(fp): index for index, fp in enumerate(priority_fps or [])}
        pending.sort(
            key=lambda item: (
                order.get(str(item.get("fp")), len(order)),
                -_pubdate_rank(str(item.get("pubdate") or "")),
            )
        )
        added = 0
        for entry in pending[: max(0, int(limit))]:
            display = self._recognize_display(str(entry.get("title") or ""))
            if display:
                names[entry["fp"]] = display
                added += 1
        if added:
            logger.info("【特效字幕监控】本轮识别资源名 %d 条，新增中文名 %d 条", len(pending), added)
        return added

    def _decorate_names(
        self, state: Dict[str, Any], grouped: Dict[str, Dict[str, Any]]
    ) -> None:
        """把缓存的中文名写到条目上，供通知与详情页使用。"""
        names = state.get("names") or {}
        for entry in grouped.values():
            display = names.get(entry.get("fp"))
            entry["display"] = str(display or entry.get("title") or "")

    @staticmethod
    def _prune_names(state: Dict[str, Any]) -> None:
        """丢弃已不在去重表里的中文名缓存，避免状态无限增长。"""
        items = state.get("items") or {}
        names = state.get("names")
        if isinstance(names, dict):
            state["names"] = {key: value for key, value in names.items() if key in items}

    # ==================== 状态与通知 ====================

    def _load_state(self) -> Dict[str, Any]:
        """读取去重与运行状态，异常时返回空状态。"""
        try:
            data = self.get_data("state")
        except Exception:  # noqa: BLE001 - 数据层异常按空状态处理
            data = None
        if not isinstance(data, dict):
            data = {}
        if not isinstance(data.get("items"), dict):
            data["items"] = {}
        if not isinstance(data.get("recent"), list):
            data["recent"] = []
        data.setdefault("baseline_done", False)
        return data

    def _save_state(self, state: Dict[str, Any]) -> None:
        """保存去重与运行状态。"""
        self._state = state
        try:
            self.save_data("state", state)
        except Exception as err:  # noqa: BLE001 - 保存失败只记日志
            logger.error("【特效字幕监控】保存状态失败：%s", err)

    def _apply_baseline(
        self, state: Dict[str, Any], grouped: Dict[str, Dict[str, Any]], started: datetime
    ) -> None:
        """把当前全部资源记入去重表，避免首次运行刷屏。"""
        stamp = started.isoformat(timespec="seconds")
        items = state.setdefault("items", {})
        for fingerprint, entry in grouped.items():
            items[fingerprint] = {
                "first": stamp,
                "last": stamp,
                "sites": self._site_map(entry),
            }
        state["baseline_done"] = True
        state["items"] = _prune_seen(items)

    def _record(
        self,
        state: Dict[str, Any],
        grouped: Dict[str, Dict[str, Any]],
        new_items: List[Dict[str, Any]],
        new_site_items: List[Dict[str, Any]],
    ) -> None:
        """把新增资源与新站点写入去重表。"""
        stamp = datetime.now().isoformat(timespec="seconds")
        items = state.setdefault("items", {})
        for entry in new_items:
            items[entry["fp"]] = {
                "first": stamp,
                "last": stamp,
                "sites": self._site_map(entry),
            }
        for entry in new_site_items:
            known = items.setdefault(entry["fp"], {"first": stamp, "last": stamp, "sites": {}})
            known["last"] = stamp
            sites = known.setdefault("sites", {})
            for site in entry.get("new_sites") or []:
                sites[str(site["id"])] = {
                    "name": site["name"],
                    "url": site.get("url") or "",
                }

    @staticmethod
    def _site_map(entry: Dict[str, Any]) -> Dict[str, Any]:
        """把资源条目的站点列表转成去重表需要的字典。"""
        return {
            str(site["id"]): {"name": site["name"], "url": site.get("url") or ""}
            for site in entry.get("sites") or []
        }

    @staticmethod
    def _summary(
        trigger: str, site_stats: List[Dict[str, Any]], matched: int, new_count: int
    ) -> Dict[str, Any]:
        """生成详情页使用的本轮摘要。"""
        return {
            "trigger": trigger,
            "sites": site_stats,
            "matched": matched,
            "new": new_count,
        }

    def _notify_baseline(self, count: int) -> None:
        """首次运行发送一条基线说明，不逐条推送。"""
        if not self._notify_enabled:
            return
        try:
            self.post_message(
                mtype=MessageType.Plugin,
                title="【特效字幕监控】基线已建立",
                text=f"已记录现有 {count} 条特效资源，之后只推送新增。\n"
                     f"关键词：{'、'.join(self._keywords)}；间隔：{self._interval_minutes} 分钟。",
            )
        except Exception as err:  # noqa: BLE001 - 通知失败不影响状态
            logger.error("【特效字幕监控】发送基线通知失败：%s", err)

    def _notify_new(
        self,
        new_items: List[Dict[str, Any]],
        new_site_items: List[Dict[str, Any]],
        more: int,
    ) -> None:
        """发送新增资源通知，超过单次上限时只提示数量。"""
        if not self._notify_enabled:
            return
        lines: List[str] = []
        link = ""
        if new_items:
            for entry in new_items[: self._max_items]:
                lines.extend(self._describe_entry(entry))
                if not link:
                    link = str(entry.get("link") or "")
            title = f"【特效字幕监控】发现 {len(new_items)} 条新资源"
            if more:
                lines.append(f"还有 {more} 条未展示，可打开插件详情页查看。")
        else:
            for entry in new_site_items[: self._max_items]:
                sites = "、".join(site["name"] for site in entry.get("new_sites") or [])
                lines.append(f"· {_short(entry.get('title'), 55)}")
                lines.append(f"  新增站点：{sites}")
                if not link:
                    link = str(entry.get("link") or "")
            title = f"【特效字幕监控】{len(new_site_items)} 条资源新增站点"
        if not lines:
            return
        try:
            self.post_message(
                mtype=MessageType.Plugin,
                title=title,
                text="\n".join(lines),
                link=link or None,
            )
        except Exception as err:  # noqa: BLE001 - 通知失败不影响状态
            logger.error("【特效字幕监控】发送通知失败：%s", err)

    @staticmethod
    def _describe_entry(entry: Dict[str, Any]) -> List[str]:
        """生成单条资源的通知文本行。"""
        display = str(entry.get("display") or entry.get("title") or "")
        meta = " · ".join(
            part
            for part in (
                f"制作组：{entry.get('team') or '未知'}",
                str(entry.get("quality") or ""),
                _format_size(entry.get("size")),
                str(entry.get("pubdate") or "")[:10],
            )
            if part
        )
        sites = "、".join(site["name"] for site in entry.get("sites") or [])
        return [
            f"· {_short(display, 55)}",
            f"  {meta}",
            f"  {sites}",
        ]

    @staticmethod
    def _to_int(value: Any, default: int, minimum: int, maximum: int) -> int:
        """把配置值归一化为区间内的整数。"""
        try:
            number = int(float(str(value).strip()))
        except (TypeError, ValueError):
            return default
        return max(minimum, min(maximum, number))
