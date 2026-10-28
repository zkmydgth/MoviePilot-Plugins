#!/usr/bin/env python3
"""
TG 磁力离线（TgOfflineBot）。

独立的 Telegram 机器人接收端：自带长轮询，只处理白名单用户发来的
磁力 / ed2k / 种子链接，并提交给 P115StrmHelper 做 115 离线下载。

刻意划定的边界：
- 不注册任何 MoviePilot 斜杠命令（get_command 恒为空列表）；
- 不进入 MoviePilot 消息链，不触发 AI 智能体，不产生会话；
- 不发送 MoviePilot 通知（从不调用 post_message）；
- 白名单与目标目录既可在配置页修改，也可用 bot 指令 /allow、/deny、/dir 热修改。

因此该 bot 的能力面只有「提交离线下载」与「查看自身状态」，
不具备站点、订阅、整理、重启、升级等任何管理入口。
"""

import asyncio
import base64
import inspect
import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qs, unquote, urlparse

import requests
from fastapi import Request
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from app.plugins import _PluginBase
from app.runtime.log import logger
from app.sdk.events import Event, eventmanager
from app.schemas.event import TransferInterceptEventData
from app.schemas.types import ChainEventType, EventType, MediaType, SystemConfigKey
from app.domain.metainfo import MetaInfo

from .version import VERSION

try:
    from app.chain.media.facade import MediaChain
except Exception:  # pragma: no cover - 宿主缺失时无法做二次识别
    MediaChain = None

try:
    from app.sdk.config import settings as _host_settings
except Exception:  # pragma: no cover - 宿主缺失时退化为环境变量
    _host_settings = None

try:
    from app.agent.llm import LLMHelper
except Exception:  # pragma: no cover - 旧版宿主路径
    try:
        from app.helper.llm import LLMHelper
    except Exception:
        LLMHelper = None

try:
    from app.db.oper.systemconfig import SystemConfigOper
except Exception:  # pragma: no cover - 宿主缺失时不支持写识别词
    SystemConfigOper = None


# Telegram Bot API 官方地址；自建反向代理时可在配置里覆盖
DEFAULT_API_BASE = "https://api.telegram.org"
# 白名单默认值：留空，新装后由用户在配置页或 bot 里自行填写
DEFAULT_ALLOWED_USER = ""
# 配置页占位示例（不是真实账号）
ALLOWED_USER_PLACEHOLDER = "123456789"
NOTIFY_CHAT_PLACEHOLDER = "-1001234567890"
# 长轮询等待秒数：取 10 秒而非 25 秒，使插件停止时最多等一个轮询周期
LONG_POLL_SECONDS = 10
# Telegram 单条消息正文上限 4096 字符，留出余量
TEXT_LIMIT = 3800
# 详情页保留的最近提交记录条数
HISTORY_LIMIT = 20
# 每批提交条数与批间隔的默认值（实测 20 条/批、30 秒间隔对 115 无风控）
DEFAULT_BATCH_SIZE = 20
DEFAULT_BATCH_INTERVAL = 30
# 配置允许的范围，避免误填出 1 条/批或十分钟一批这类极端值
MAX_BATCH_SIZE = 100
MAX_BATCH_INTERVAL = 600
# P115StrmHelper 插件 ID（离线下载接口由它提供）
STRM_HELPER_PLUGIN = "P115StrmHelper"

# 选择消息首次发送失败后的重发间隔（秒）
CHOICE_RESEND_INTERVAL = 60
# 同一任务删除成功后的通知去重窗口（秒）：115 列表有延迟，避免重复删除
OFFLINE_DELETE_DEDUPE_SECONDS = 1800
# 残留扫描的递归深度与单次文件上限
RESIDUE_SCAN_MAX_DEPTH = 3
RESIDUE_SCAN_MAX_FILES = 200
# 任务已完成但目标目录里找不到对应项时的宽限期（秒）
ORGANIZE_GIVEUP_SECONDS = 600
# 离线任务失败/重试中超过该分钟数仍未恢复则删除并通知
DEFAULT_OFFLINE_FAILED_TIMEOUT = 30
# 整理前比对：默认等待人工选择 10 分钟，超时按 MP 结果继续
DEFAULT_PRECHECK_TIMEOUT = 10
# 整理完成后延时多少秒核对 STRM 是否落盘
DEFAULT_STRM_CHECK_DELAY = 60
# AI 置信度低于该值时不做比对，直接放行，避免噪声打扰
AI_CONFIDENCE_FLOOR = 0.5
# 未配置比对范围时的默认值：P115StrmHelper 的待整理目录
DEFAULT_PRECHECK_SCOPE = "/待整理"
# 离线任务在 P115StrmHelper 离线目录下使用的专属子目录名（留空则直接使用离线目录）
DEFAULT_OFFLINE_SUBDIR = "TG"
# P115StrmHelper 配置（离线目录/整理目录）缓存时间（秒）
STRM_HELPER_CONFIG_TTL = 600
# 访问 115 目录时可用的存储标识兜底顺序；配置里存的历史显示名在宿主中可能没有对应实现
STRM_HELPER_STORAGE_FALLBACKS = ("u115", "n")
# 临时识别词的存活时间（秒），到期后由整理完成回调清理
IDENTIFIER_TTL_SECONDS = 3600
# 本插件提交任务的来源标记存活时间（秒）
OWNED_TTL_SECONDS = 86400
# 部分磁力链接不带 dn，115 的落盘名只能从离线任务列表取回；
# 提交后在该窗口内按 info_hash 轮询补记来源名称
OWNED_PENDING_TTL_SECONDS = 900
OWNED_SYNC_INTERVAL_SECONDS = 2.0
OWNED_SYNC_ATTEMPTS = 5
# 提交到指定目录后，等待「下载完成 → 主动触发整理」的登记有效期（秒）
PENDING_TRANSFER_TTL_SECONDS = 6 * 3600
# 触发整理到 MP 真正搬完文件的宽限期（秒）：这段时间目录里仍躺着正片，
# 残留清理必须跳过，否则会把还没整理的正片当成广告删掉
ORGANIZE_CLEANUP_GRACE_SECONDS = 1800
# 整理完成通知的静默聚合窗口（秒）：同一「标题+季」静默这么久才发一条汇总
DEFAULT_NOTIFY_WINDOW = 300
# 持续有集进来时最多压这么久必须发一次，避免通知饿死
NOTIFY_MAX_HOLD_SECONDS = 1800
# 残留清理不按扩展名设限：所有剩余文件都交给 AI 判断保留还是删除
# 同一目录的残留扫描节流（秒）
RESIDUE_SCAN_INTERVAL = 600
# 判定「整理目标是媒体文件」用的扩展名白名单：目录名常带点（Show.S01.1080p），
# 只看 Path.suffix 会把目录误当成文件
STRM_MEDIA_SUFFIXES = (
    ".mp4", ".mkv", ".ts", ".m2ts", ".avi", ".mov", ".wmv",
    ".flv", ".webm", ".mpg", ".mpeg", ".rmvb", ".iso", ".strm",
)
# 媒体源到识别词字段的映射（识别词只认这些字段名）
MEDIA_SOURCE_ID_FIELDS = {
    "tmdb": "tmdbid",
    "themoviedb": "tmdbid",
    "douban": "doubanid",
    "bangumi": "bangumiid",
    "anilist": "anilistid",
}


class AiRecognition(BaseModel):
    """AI 对单个文件名的结构化识别结果。"""

    name: str = Field(default="", description="标准化后的影视标题；无法判断时为空")
    year: str = Field(default="", description="四位年份；无法判断时为空")
    media_type: str = Field(default="unknown", description="movie、tv 或 unknown")
    season: int = Field(default=0, description="剧集季号，电影填 0")
    episode: int = Field(default=0, description="剧集集号，电影或未知填 0")
    confidence: float = Field(default=0.0, description="0 到 1 之间的置信度")
    reason: str = Field(default="", description="简短说明判断依据")


class ResidueVerdict(BaseModel):
    """AI 对残留文件的清理判定。"""

    delete_files: List[str] = Field(
        default_factory=list, description="应当删除的文件名；保留的不要列出"
    )
    reason: str = Field(default="", description="简短说明判断依据")

# 磁力链接：magnet:?xt=urn:btih:...
_RE_MAGNET = re.compile(r"magnet:\?[^\s\"'<>]+", re.IGNORECASE)
# ed2k 文件链接：ed2k://|file|名称|大小|HASH|/
_RE_ED2K = re.compile(
    r"ed2k://\|file\|[^|\r\n]+\|\d+\|[0-9A-Fa-f]{32}[^|\r\n]*\|/", re.IGNORECASE
)
# 种子文件直链
_RE_TORRENT = re.compile(
    r"https?://[^\s\"'<>]+\.torrent(?:\?[^\s\"'<>]*)?", re.IGNORECASE
)
# 白名单条目：用户 ID、群组 ID（负数）或 @用户名
_RE_ALLOWED_ID = re.compile(r"^(?:-?\d+|@[A-Za-z0-9_]{3,32})$")


def parse_offline_links(text: str) -> List[str]:
    """
    从消息文本中提取磁力、ed2k 与种子链接。

    按链接在原文中的出现顺序去重返回，便于用户一次粘贴多条。

    :param text: 用户消息正文
    :return: 去重后的链接列表，未识别到任何链接时为空列表
    """
    raw = text or ""
    matched: List[Tuple[int, str]] = []
    for pattern in (_RE_MAGNET, _RE_ED2K, _RE_TORRENT):
        for item in pattern.finditer(raw):
            matched.append((item.start(), item.group(0)))
    matched.sort(key=lambda pair: pair[0])
    result: List[str] = []
    seen: Set[str] = set()
    for _, link in matched:
        candidate = link.strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        result.append(candidate)
    return result


def parse_user_ids(raw: Any) -> Set[str]:
    """
    解析白名单配置，兼容逗号、分号、空格与换行分隔。

    :param raw: 配置原文或列表
    :return: 纯数字用户 ID 集合
    """
    if raw is None:
        return set()
    if isinstance(raw, (list, tuple, set)):
        items = [str(item) for item in raw]
    else:
        items = re.split(r"[,，;；\s]+", str(raw))
    # 白名单支持用户 ID（正数）、群组 ID（负数）与 @用户名
    return {item.strip() for item in items if _RE_ALLOWED_ID.fullmatch(item.strip())}


class TgOfflineBot(_PluginBase):
    """独立 Telegram 磁力离线机器人插件。"""

    # 插件元数据
    plugin_name = "TG 磁力离线"
    plugin_desc = (
        "配合 P115StrmHelper 使用：白名单用户发磁力 / ed2k / 种子链接即提交 115 离线，"
        "并在整理前做库存在检查与 MP / AI 识别比对。"
    )
    plugin_icon = "tgofflinebot.png"
    plugin_version = VERSION
    plugin_label = "工具"
    plugin_author = "zkmydgth"
    plugin_config_prefix = "tgofflinebot_"
    plugin_order = 100
    auth_level = 1

    # 运行状态
    _enabled: bool = False
    _bot_token: str = ""
    _api_base: str = DEFAULT_API_BASE
    _proxy: str = ""
    _allowed_users: Set[str] = set()
    _target_path: str = ""
    # 专属子目录名：在 P115StrmHelper 离线目录下再建一层，作为本插件任务的落地目录
    _offline_subdir: str = DEFAULT_OFFLINE_SUBDIR
    # P115StrmHelper 配置缓存：(时间戳, 配置字典)
    _strm_helper_cache: Optional[Tuple[float, Dict[str, Any]]] = None
    # 已提示过的存储标识兜底，避免每轮巡检重复写日志
    _storage_fallback_noted: Set[str] = set()
    # 待按 info_hash 触发整理的离线任务：info_hash -> 截止时间戳
    _pending_transfer: Dict[str, float] = {}
    # 「单文件与他任务混放、需手动整理」的提醒去重：info_hash -> 上次时间
    _organize_notified: Dict[str, float] = {}
    # 删除成功的离线任务去重：info_hash -> 删除时间
    _offline_deleted_notified: Dict[str, float] = {}
    # 已完成后在目标目录找不到对应项的首次时间：info_hash -> 时间戳
    _organize_seen: Dict[str, float] = {}
    # 已触发整理、但 MP 可能还在异步搬文件的目录：目录路径 -> 触发时间戳
    _organize_dispatched: Dict[str, float] = {}
    # 「整理队列未清空、已暂缓残留清理」是否已写过日志，避免每轮刷屏
    _organize_batch_noted: bool = False
    # 待重试删除的空目录：路径 -> 截止时间戳（115 列表有延迟，删完文件后目录可能仍显示非空）
    _pending_dir_removals: Dict[str, Dict[str, float]] = {}
    # 配置写回锁：/dir、/allow、/deny 等改动可能并发，读-改-写必须串行
    _config_write_lock = threading.Lock()
    # 单轮巡检里待汇总的手动整理提醒，合并成一条消息发出
    _organize_notices: List[str] = []
    _api_token: str = ""
    _host_port: int = 3001
    # 大批量提交时的分批参数：每批条数与批间隔（秒）
    _batch_size: int = DEFAULT_BATCH_SIZE
    _batch_interval: int = DEFAULT_BATCH_INTERVAL
    _thread: Optional[threading.Thread] = None
    _stop_event: threading.Event = threading.Event()
    _generation: int = 0
    _offset: int = 0
    _bot_username: str = ""
    _last_error: str = ""
    _last_result: str = ""
    _started_at: str = ""
    _history: List[Dict[str, str]] = []
    _lock: threading.Lock = threading.Lock()
    # 提交串行锁：多条大批量任务同时到达时排队，避免并发撞上 115 风控
    _submit_lock: threading.Lock = threading.Lock()
    # 整理前识别比对
    _precheck_enabled: bool = True
    _precheck_scope: str = ""
    _precheck_timeout: int = DEFAULT_PRECHECK_TIMEOUT
    # 通知开关
    _notify_transfer: bool = True
    _notify_strm: bool = False
    _notify_chat_id: str = ""
    # 只通知本插件提交范围（默认 /待整理）内的整理，避免订阅下载也刷屏
    _notify_only_from_bot: bool = True
    # 聚合窗口：同一「标题+季」在该秒数内的整理合并成一条通知，0 表示逐条发
    _notify_window: int = DEFAULT_NOTIFY_WINDOW
    _notify_buffer: Dict[str, Dict[str, Any]] = {}
    _notify_timers: Dict[str, threading.Timer] = {}
    # 失败通知去重：key -> 过期时间戳
    _failed_recent: Dict[str, float] = {}
    # 最近一次与 bot 交互的会话，作为通知默认目标
    _last_chat_id: Optional[int] = None
    # 待人工选择的比对项，key 为源文件路径
    _pending_choices: Dict[str, Dict[str, Any]] = {}
    # 按源目录聚合的待决策组：目录 -> {token, message_id, paths}
    _pending_groups: Dict[str, Dict[str, Any]] = {}
    # 已决策文件的临时放行表：路径 -> 过期时间戳
    _skip_precheck_until: Dict[str, float] = {}
    # 本插件写入的识别词及其过期时间，用于整理后清理
    _identifier_rules: Dict[str, float] = {}
    # 可重入锁：渲染待决策文本时会在持锁路径内再次读取同一批数据
    _choice_lock: threading.RLock = threading.RLock()
    # 整理前库内存在检查
    _library_check_enabled: bool = True
    # 库中已存在时删除 115 源文件（不可逆，可在配置里关闭）
    _delete_when_exists: bool = True
    # 允许洗版：开启后库中已有同媒体也放行整理，交给 MP 的覆盖策略处理
    _allow_upgrade: bool = False
    # 离线任务超时清理：超过该分钟数仍为 0% 的任务会被删除，0 表示关闭
    _offline_timeout: int = 5
    # 有进度但超过该分钟数仍未下载完成的任务也会被删除，0 表示关闭
    _offline_progress_timeout: int = 10
    # 下载失败或重试中超过该分钟数仍未恢复的任务会被删除，0 表示关闭
    _offline_failed_timeout: int = DEFAULT_OFFLINE_FAILED_TIMEOUT
    _offline_watch_thread: Optional[threading.Thread] = None
    # 最近一次提交离线任务的时间戳，用作清理范围下界
    _last_submit_ts: float = 0.0
    # 在途任务的有效期：没有在途任务时巡检直接跳过，不再请求离线列表
    _offline_active_until: float = 0.0
    # 离线任务删除失败的通知去重表：hash -> 上次通知时间
    _offline_delete_notified: Dict[str, float] = {}
    # 媒体库名与路径映射缓存：(时间戳, [(库名, [路径...])])
    _library_cache: Optional[Tuple[float, List[Tuple[str, List[str]]]]] = None
    # 本插件提交过的任务名称标记：归一化名称 -> 过期时间戳
    _owned_names: Dict[str, float] = {}
    # 待按 info_hash 补记来源名称的离线任务：info_hash -> 截止时间戳
    _pending_owned_hashes: Dict[str, float] = {}
    # 补记同步的单飞锁：短时间内多次提交只跑一个线程，避免并发打 115 接口
    _owned_sync_lock: threading.Lock = threading.Lock()
    # 整理后残留清理
    _cleanup_residue_enabled: bool = True
    _cleanup_unrecognized: bool = True
    _residue_checked: Dict[str, float] = {}
    # 目录静默多久后扫描残留（秒），同一目录有新完成事件会重置
    _residue_delay: int = 300
    _residue_timers: Dict[str, threading.Timer] = {}
    # 通用通知聚合缓冲：key -> 行列表，窗口结束后合并发送
    _misc_buffer: Dict[str, List[str]] = {}
    _misc_timers: Dict[str, threading.Timer] = {}
    # 目录清空后是否删除空目录
    _cleanup_empty_dirs: bool = True
    # 定期扫描待整理目录的间隔（秒），0 表示关闭
    _residue_sweep_interval: int = 1800
    # 待整理范围里已没有本插件任务目录时休眠，暂停定时巡检
    _residue_idle: bool = False
    _residue_idle_since: float = 0.0
    # 休眠后的兜底复查间隔（分钟），0 表示休眠期间不再复查
    _residue_idle_recheck: int = 0
    # 预整理缓存：同一部剧的 AI 识别与库存在查询只做一次
    _ai_cache: Dict[str, Tuple[float, Optional[AiRecognition]]] = {}
    _exists_cache: Dict[str, Tuple[float, Any]] = {}
    # AI 不可用提示的节流时间戳
    _ai_error_notified: float = 0.0
    # 空路径事件的日志节流时间戳
    _empty_event_logged: float = 0.0
    # 已按历史补发过通知的记录标识，避免重复
    _notified_histories: List[str] = []
    # STRM 通知使用的本地根、网盘根与核对间隔（秒）
    _strm_local_root: str = ""
    _strm_pan_root: str = ""
    _strm_check_delay: int = DEFAULT_STRM_CHECK_DELAY

    def init_plugin(self, config: dict = None) -> None:
        """
        根据插件配置初始化运行状态并启动长轮询。

        :param config: 插件配置字典；为空时仅复位状态不启动
        """
        self.stop_service()
        # 每次初始化都换用新的停止信号：上一轮（含类属性默认值）可能已被置位，
        # 复用会导致新实例的批量线程一启动就判定「已停止」
        self._stop_event = threading.Event()
        self._enabled = False
        self._bot_token = ""
        self._api_base = DEFAULT_API_BASE
        self._proxy = ""
        self._allowed_users = set()
        self._target_path = ""
        self._offline_subdir = DEFAULT_OFFLINE_SUBDIR
        self._strm_helper_cache = None
        self._storage_fallback_noted = set()
        self._pending_transfer = {}
        self._organize_notified = {}
        self._organize_notices = []
        self._offline_deleted_notified = {}
        self._organize_seen = {}
        self._organize_dispatched = {}
        self._organize_batch_noted = False
        self._pending_dir_removals = {}
        self._api_token = ""
        self._bot_username = ""
        self._last_error = ""
        self._started_at = ""
        self._batch_size = DEFAULT_BATCH_SIZE
        self._batch_interval = DEFAULT_BATCH_INTERVAL
        if not getattr(self, "_submit_lock", None):
            # 复用同一把锁：重载时旧批量线程仍持有它，换成新锁会让两路同时提交
            self._submit_lock = threading.Lock()
        self._precheck_enabled = True
        self._precheck_scope = ""
        self._precheck_timeout = DEFAULT_PRECHECK_TIMEOUT
        self._notify_transfer = True
        self._notify_strm = False
        self._notify_chat_id = ""
        self._notify_only_from_bot = True
        self._notify_window = DEFAULT_NOTIFY_WINDOW
        self._notify_buffer = {}
        self._notify_timers = {}
        self._failed_recent = {}
        self._last_chat_id = None
        self._pending_choices = {}
        self._pending_groups = {}
        self._skip_precheck_until = {}
        self._identifier_rules = {}
        self._choice_lock = threading.RLock()
        self._library_check_enabled = True
        self._delete_when_exists = True
        self._allow_upgrade = False
        self._offline_timeout = 5
        self._offline_progress_timeout = 10
        self._offline_failed_timeout = DEFAULT_OFFLINE_FAILED_TIMEOUT
        self._last_submit_ts = 0.0
        self._offline_active_until = 0.0
        self._offline_delete_notified = {}
        self._library_cache = None
        self._owned_names = {}
        self._pending_owned_hashes = {}
        self._owned_sync_lock = threading.Lock()
        self._cleanup_residue_enabled = True
        self._cleanup_unrecognized = True
        self._residue_checked = {}
        self._residue_delay = 300
        self._residue_timers = {}
        self._misc_buffer = {}
        self._misc_timers = {}
        self._cleanup_empty_dirs = True
        self._residue_sweep_interval = 1800
        self._residue_idle = False
        self._residue_idle_since = 0.0
        self._residue_idle_recheck = 0
        self._ai_cache = {}
        self._exists_cache = {}
        self._ai_error_notified = 0.0
        self._empty_event_logged = 0.0
        self._notified_histories = []
        self._strm_local_root = ""
        self._strm_pan_root = ""
        self._strm_check_delay = DEFAULT_STRM_CHECK_DELAY

        if not config:
            return

        self._enabled = bool(config.get("enabled"))
        self._bot_token = str(config.get("bot_token") or "").strip()
        self._api_base = (
            str(config.get("api_base") or "").strip() or DEFAULT_API_BASE
        ).rstrip("/")
        self._proxy = str(config.get("proxy") or "").strip()
        self._target_path = self._normalize_path(config.get("target_path"))
        self._offline_subdir = (
            str(config.get("offline_subdir") or DEFAULT_OFFLINE_SUBDIR)
            .strip()
            .strip("/")
        )
        self._strm_helper_cache = None
        self._storage_fallback_noted = set()
        self._pending_transfer = {}
        self._organize_notified = {}
        self._organize_notices = []
        self._offline_deleted_notified = {}
        self._organize_seen = {}
        self._organize_dispatched = {}
        self._organize_batch_noted = False
        self._pending_dir_removals = {}
        self._api_token = str(config.get("api_token") or "").strip()
        self._batch_size = self._clamp_int(
            config.get("batch_size"), DEFAULT_BATCH_SIZE, 1, MAX_BATCH_SIZE
        )
        self._batch_interval = self._clamp_int(
            config.get("batch_interval"),
            DEFAULT_BATCH_INTERVAL,
            0,
            MAX_BATCH_INTERVAL,
        )
        self._precheck_enabled = bool(config.get("precheck_enabled", True))
        self._precheck_scope = str(config.get("precheck_scope") or "").strip()
        self._precheck_timeout = self._clamp_int(
            config.get("precheck_timeout"), DEFAULT_PRECHECK_TIMEOUT, 0, 120
        )
        self._notify_transfer = bool(config.get("notify_transfer", True))
        self._notify_strm = bool(config.get("notify_strm", False))
        self._notify_chat_id = str(config.get("notify_chat_id") or "").strip()
        self._notify_only_from_bot = bool(config.get("notify_only_from_bot", True))
        self._notify_window = self._clamp_int(
            config.get("notify_window"), DEFAULT_NOTIFY_WINDOW, 0, 600
        )
        self._library_check_enabled = bool(config.get("library_check_enabled", True))
        self._delete_when_exists = bool(config.get("delete_when_exists", True))
        self._allow_upgrade = bool(config.get("allow_upgrade", False))
        self._cleanup_residue_enabled = bool(
            config.get("cleanup_residue_enabled", True)
        )
        self._cleanup_unrecognized = bool(config.get("cleanup_unrecognized", True))
        self._residue_delay = self._clamp_int(
            config.get("residue_delay"), 300, 60, 1800
        )
        self._residue_sweep_interval = self._clamp_int(
            config.get("residue_sweep_interval"), 1800, 0, 3600
        )
        self._residue_idle_recheck = self._clamp_int(
            config.get("residue_idle_recheck"), 0, 0, 1440
        )
        self._cleanup_empty_dirs = bool(config.get("cleanup_empty_dirs", True))
        self._offline_timeout = self._clamp_int(
            config.get("offline_timeout"), 5, 0, 120
        )
        self._offline_progress_timeout = self._clamp_int(
            config.get("offline_progress_timeout"), 10, 0, 240
        )
        self._offline_failed_timeout = self._clamp_int(
            config.get("offline_failed_timeout"),
            DEFAULT_OFFLINE_FAILED_TIMEOUT,
            0,
            1440,
        )
        self._strm_local_root = str(config.get("strm_local_root") or "").strip()
        self._strm_pan_root = str(config.get("strm_pan_root") or "").strip()
        self._strm_check_delay = self._clamp_int(
            config.get("strm_check_delay"), DEFAULT_STRM_CHECK_DELAY, 10, 600
        )
        self._allowed_users = parse_user_ids(config.get("allowed_users"))
        if not self._allowed_users and DEFAULT_ALLOWED_USER:
            # 首次安装未配置白名单时给出默认值，避免插件「启用了但谁都不能用」
            self._allowed_users = {DEFAULT_ALLOWED_USER}

        self._offset = int(self._read_data("offset") or 0)
        self._history = list(self._read_data("history") or [])[-HISTORY_LIMIT:]
        stored_owned = self._read_data("owned_names")
        if isinstance(stored_owned, dict):
            now_ts = time.time()
            self._owned_names = {
                str(key): float(value)
                for key, value in stored_owned.items()
                if isinstance(value, (int, float)) and float(value) > now_ts
            }
        stored_transfer = self._read_data("pending_transfer")
        if isinstance(stored_transfer, dict):
            now_ts = time.time()
            self._pending_transfer = {
                str(key): float(value)
                for key, value in stored_transfer.items()
                if isinstance(value, (int, float)) and float(value) > now_ts
            }
        stored_rules = self._read_data("identifier_rules")
        if isinstance(stored_rules, dict):
            now_ts = time.time()
            self._identifier_rules = {
                str(rule): float(created)
                for rule, created in stored_rules.items()
                if isinstance(created, (int, float))
                and now_ts - float(created) < IDENTIFIER_TTL_SECONDS
            }
        stored_notified = self._read_data("notified_histories")
        if isinstance(stored_notified, list):
            self._notified_histories = [str(item) for item in stored_notified]
        try:
            stored_submit = float(self._read_data("last_submit_ts") or 0)
        except (TypeError, ValueError):
            stored_submit = 0.0
        if stored_submit and time.time() - stored_submit < 3600:
            # 重启后继续巡检上一轮的在途任务，避免卡住的任务无人清理
            self._last_submit_ts = stored_submit
            self._offline_active_until = stored_submit + 3600

        if not self._enabled:
            return
        if not self._bot_token:
            self._last_error = "未配置 bot token，长轮询未启动"
            logger.warning("【TG磁力离线】未配置 bot token，插件不启动轮询")
            return

        self._stop_event = threading.Event()
        self._generation += 1
        generation = self._generation
        self._thread = threading.Thread(
            target=self._poll_loop,
            args=(generation,),
            name="TgOfflineBot-Polling",
            daemon=True,
        )
        self._thread.start()
        self._started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.info("【TG磁力离线】长轮询已启动")
        # 巡检线程常驻：既做离线任务清理，也负责让过期临时识别词到期被移除
        self._offline_watch_thread = threading.Thread(
            target=self._offline_watch_loop,
            args=(generation,),
            name="TgOfflineBot-OfflineWatch",
            daemon=True,
        )
        self._offline_watch_thread.start()

    def get_state(self) -> bool:
        """
        读取插件启用状态。

        :return: 插件是否启用
        """
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """
        返回 MoviePilot 远程命令列表。

        本插件刻意不注册任何命令：能力面只有自有 bot 的几条指令。

        :return: 固定为空列表
        """
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        """
        返回插件 API 列表，供后续在网页/脚本中维护白名单与查看状态。

        :return: API 路由定义列表
        """
        return [
            {
                "path": "/whitelist",
                "endpoint": self.api_whitelist,
                "methods": ["GET", "POST"],
                "auth": "apikey",
                "summary": "查看或增删 Telegram 白名单用户",
            },
            {
                "path": "/status",
                "endpoint": self.api_status,
                "methods": ["GET"],
                "auth": "apikey",
                "summary": "查看机器人与最近提交状态",
            },
        ]

    def get_form(self) -> Tuple[Optional[List[dict]], Dict[str, Any]]:
        """
        返回插件配置表单与默认配置。

        :return: Vuetify 表单结构与默认值
        """
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
                                            "text": "配合 P115StrmHelper 使用：白名单用户发磁力 / ed2k / "
                                                    "种子链接即提交 115 离线，并在整理前做库存在检查与 "
                                                    "MP / AI 识别比对。",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "component": "VSwitch",
                        "props": {
                            "model": "enabled",
                            "label": "启用插件",
                            "class": "mt-4",
                            "hint": "启用后立即启动长轮询；关闭即停止接收",
                            "persistent-hint": True,
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "bot_token",
                            "label": "Bot Token",
                            "class": "mt-4",
                            "type": "password",
                            "placeholder": "123456:ABC-DEF...",
                            "hint": "在 @BotFather 用 /newbot 新建，必须与 MoviePilot 消息渠道里的 bot 不同，"
                                    "同一 token 两处轮询会互相 409 冲突",
                            "persistent-hint": True,
                        },
                    },
                    {
                        "component": "VTextarea",
                        "props": {
                            "model": "allowed_users",
                            "label": "白名单用户 ID（逗号或换行分隔）",
                            "class": "mt-4",
                            "rows": 2,
                            "placeholder": ALLOWED_USER_PLACEHOLDER,
                            "hint": "用户 ID（正数）与群组 ID（负数，如 -1001234567890）都可填，"
                                    "逗号或换行分隔。群 ID 在白名单即授权该群成员提交链接，"
                                    "但改目录/白名单仍只允许个人白名单成员。"
                                    "群内普通消息需先在 @BotFather 用 /setprivacy 设为 Disable，"
                                    "否则机器人收不到群里的磁力消息。"
                                    "也可在 bot 里用 /allow <id>、/deny <id> 热修改",
                            "persistent-hint": True,
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "target_path",
                            "label": "115 目标目录（留空 = 自动使用专属子目录）",
                            "class": "mt-4",
                            "placeholder": "留空自动解析",
                            "hint": "留空时自动取 P115StrmHelper 的 offline_download_paths 首个目录，"
                                    "再拼上下方的专属子目录（默认 TG），不写死任何路径。"
                                    "填写具体 115 路径则以手填为准。也可在 bot 里用 /dir <路径> 热修改",
                            "persistent-hint": True,
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "offline_subdir",
                            "label": "专属子目录名（留空 = 直接使用离线目录）",
                            "class": "mt-4",
                            "placeholder": DEFAULT_OFFLINE_SUBDIR,
                            "hint": "在 P115StrmHelper 离线目录下再建这一层目录，本插件提交的任务全部落在其中；"
                                    "目录不存在时提交前自动创建，残留清理时不会删除该目录。"
                                    "目录内的任务按路径前缀判定为本插件任务，不再依赖名称匹配。"
                                    "下载完成后插件会主动触发一次网盘整理：多文件任务整理其子目录；"
                                    "单文件任务仅在目标目录里没有其它项目时才整理，否则改为提醒手动整理",
                            "persistent-hint": True,
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "batch_size",
                            "label": "每批提交条数",
                            "class": "mt-4",
                            "type": "number",
                            "placeholder": str(DEFAULT_BATCH_SIZE),
                            "hint": "链接数量超过该值时自动分批提交，避免一次性打太多任务触发 115 风控；范围 1-100",
                            "persistent-hint": True,
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "batch_interval",
                            "label": "批间隔（秒）",
                            "class": "mt-4",
                            "type": "number",
                            "placeholder": str(DEFAULT_BATCH_INTERVAL),
                            "hint": "每批之间的等待秒数，实测 30 秒对 115 无风控；有批次失败时自动延长到两倍间隔；范围 0-600",
                            "persistent-hint": True,
                        },
                    },
                    {
                        "component": "VSwitch",
                        "props": {
                            "model": "library_check_enabled",
                            "label": "整理前检查媒体库是否已有",
                            "class": "mt-4",
                            "hint": "开启后，预整理阶段先查库：已存在则发通知并跳过整理；"
                                    "电视剧按具体集判断，只缺集时仍会整理",
                            "persistent-hint": True,
                        },
                    },
                    {
                        "component": "VSwitch",
                        "props": {
                            "model": "allow_upgrade",
                            "label": "允许洗版（库中已有也照常整理）",
                            "class": "mt-4",
                            "hint": "默认关闭：库里已有同媒体时跳过整理（按下方开关决定是否删源）。"
                                    "开启后不再拦截已有媒体，是否替换由 MoviePilot 的覆盖模式决定",
                            "persistent-hint": True,
                        },
                    },
                    {
                        "component": "VSwitch",
                        "props": {
                            "model": "delete_when_exists",
                            "label": "库中已存在时删除 115 源文件",
                            "class": "mt-4",
                            "hint": "判定无需整理（库里已有）后删除 115 待整理目录里的源文件，"
                                    "避免长期占用空间；删除不可逆，可在 115 回收站恢复",
                            "persistent-hint": True,
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "offline_timeout",
                            "label": "离线卡住判定（分钟，0 = 不清理）",
                            "class": "mt-4",
                            "type": "number",
                            "placeholder": "5",
                            "hint": "115 通常秒下；超过该时间仍是 0% 的离线任务会被删除，"
                                    "只处理本插件提交之后出现的任务",
                            "persistent-hint": True,
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "offline_progress_timeout",
                            "label": "有进度未完成判定（分钟，0 = 不清理）",
                            "class": "mt-4",
                            "type": "number",
                            "placeholder": "10",
                            "hint": "已经有了下载进度、但超过该时间仍未完成的任务同样会被删除；"
                                    "适合清理 115 侧挂住的任务",
                            "persistent-hint": True,
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "offline_failed_timeout",
                            "label": "下载失败/重试中判定（分钟，0 = 不清理）",
                            "class": "mt-4",
                            "type": "number",
                            "placeholder": str(DEFAULT_OFFLINE_FAILED_TIMEOUT),
                            "hint": "115 侧标记为下载失败或重试中、超过该时间仍未恢复的离线任务"
                                    "会被删除并推送通知；只处理本插件提交之后出现的任务",
                            "persistent-hint": True,
                        },
                    },
                    {
                        "component": "VSwitch",
                        "props": {
                            "model": "precheck_enabled",
                            "label": "整理前比对 MP 与 AI 识别",
                            "class": "mt-4",
                            "hint": "一致直接整理；不一致时发按钮让你选择按哪边整理。"
                                    "AI 用的是本机已配置的 LLM",
                            "persistent-hint": True,
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "precheck_scope",
                            "label": "比对范围（115 路径前缀，逗号分隔）",
                            "class": "mt-4",
                            "placeholder": DEFAULT_PRECHECK_SCOPE,
                            "hint": "只处理这些目录下的整理；留空默认 /待整理",
                            "persistent-hint": True,
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "precheck_timeout",
                            "label": "等待选择的超时（分钟，0 表示一直等）",
                            "class": "mt-4",
                            "type": "number",
                            "placeholder": str(DEFAULT_PRECHECK_TIMEOUT),
                            "hint": "超时后自动按 MP 识别结果继续整理，避免文件卡在待整理",
                            "persistent-hint": True,
                        },
                    },
                    {
                        "component": "VSwitch",
                        "props": {
                            "model": "notify_transfer",
                            "label": "整理完成/失败通知",
                            "class": "mt-4",
                        },
                    },
                    {
                        "component": "VSwitch",
                        "props": {
                            "model": "notify_strm",
                            "label": "STRM 生成通知",
                            "class": "mt-4",
                            "hint": "整理完成后延时核对本地 STRM 是否落盘，最多等 10 分钟",
                            "persistent-hint": True,
                        },
                    },
                    {
                        "component": "VSwitch",
                        "props": {
                            "model": "notify_only_from_bot",
                            "label": "只通知本插件提交的整理",
                            "class": "mt-4",
                            "hint": "开启后只通知比对范围（默认 /待整理）内的整理，"
                                    "订阅下载与其它来源的整理不会推送到群里，建议保持开启",
                            "persistent-hint": True,
                        },
                    },
                    {
                        "component": "VSwitch",
                        "props": {
                            "model": "cleanup_residue_enabled",
                            "label": "整理后清理残留文件",
                            "class": "mt-4",
                            "hint": "整理完成后扫描源目录，删除广告、推广、网址等无关文件；"
                                    "正片、字幕、音轨、图片与 nfo 一律保留",
                            "persistent-hint": True,
                        },
                    },
                    {
                        "component": "VSwitch",
                        "props": {
                            "model": "cleanup_unrecognized",
                            "label": "AI 无法判断的文件也删除",
                            "class": "mt-4",
                            "hint": "关闭时只删除 AI 明确判定为广告的文件，其余保留",
                            "persistent-hint": True,
                        },
                    },
                    {
                        "component": "VSwitch",
                        "props": {
                            "model": "cleanup_empty_dirs",
                            "label": "清理完成后删除空目录",
                            "class": "mt-4",
                            "hint": "删掉文件后目录变空时一并删除，避免 115 待整理目录里留下空壳",
                            "persistent-hint": True,
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "residue_delay",
                            "label": "残留清理延迟（秒，目录静默后扫描）",
                            "class": "mt-4",
                            "type": "number",
                            "placeholder": "300",
                            "hint": "同一目录连续整理时每次完成都会重置计时，等目录安静下来再扫描，"
                                    "避免把同批还没整理的集当成残留处理",
                            "persistent-hint": True,
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "residue_sweep_interval",
                            "label": "待整理残留兜底巡检间隔（秒，0 = 只按新入库触发）",
                            "class": "mt-4",
                            "type": "number",
                            "placeholder": "1800",
                            "hint": "默认只在发现新的入库记录时扫描待整理目录；这里的间隔是兜底，"
                                    "用于清理被拦截、没有入库记录的场景，空闲时不会主动扫 115；"
                                    "待整理目录里没有本插件任务时会自动休眠",
                            "persistent-hint": True,
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "residue_idle_recheck",
                            "label": "休眠后兜底复查间隔（分钟，0 = 不复查）",
                            "class": "mt-4",
                            "type": "number",
                            "placeholder": "0",
                            "hint": "待整理目录里已没有本插件任务时暂停定时巡检，"
                                    "新提交、补记任务名、新入库记录或整理事件都会自动恢复；"
                                    "填大于 0 的数字则休眠期间每隔这么久再复查一次",
                            "persistent-hint": True,
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "notify_window",
                            "label": "通知聚合静默窗口（秒，0 = 逐条发）",
                            "class": "mt-4",
                            "type": "number",
                            "placeholder": "300",
                            "hint": "同一部剧静默这么久没有新集才发一条汇总（列出集号）；"
                                    "整季顺序整理只要单集间隔小于该值就会合并，"
                                    "持续有新集时最多压 30 分钟",
                            "persistent-hint": True,
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "notify_chat_id",
                            "label": "通知会话 ID（留空 = 最近一次私聊）",
                            "class": "mt-4",
                            "placeholder": NOTIFY_CHAT_PLACEHOLDER,
                            "hint": "整理进度、入库与 STRM 通知发到这个会话",
                            "persistent-hint": True,
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "strm_local_root",
                            "label": "STRM 本地根目录",
                            "class": "mt-4",
                            "placeholder": "/path/to/115strm",
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "strm_pan_root",
                            "label": "对应的 115 网盘根目录",
                            "class": "mt-4",
                            "placeholder": "/video",
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "strm_check_delay",
                            "label": "STRM 核对间隔（秒）",
                            "class": "mt-4",
                            "type": "number",
                            "placeholder": str(DEFAULT_STRM_CHECK_DELAY),
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "api_base",
                            "label": "Bot API 地址",
                            "class": "mt-4",
                            "placeholder": DEFAULT_API_BASE,
                            "hint": "默认官方地址；使用自建反代时填反代地址",
                            "persistent-hint": True,
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "proxy",
                            "label": "代理地址（留空 = 用 MoviePilot 的 PROXY 设置）",
                            "class": "mt-4",
                            "placeholder": "http://proxy.example.com:7890",
                            "hint": "访问 Telegram 所用代理；留空时读取宿主 PROXY 配置",
                            "persistent-hint": True,
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "api_token",
                            "label": "MoviePilot API Token（留空 = 自动读取宿主）",
                            "class": "mt-4",
                            "type": "password",
                            "hint": "仅用于调用本机 P115StrmHelper 的离线接口，默认自动取宿主 API_TOKEN",
                            "persistent-hint": True,
                        },
                    },
                ],
            }
        ], {
            "enabled": False,
            "bot_token": "",
            "allowed_users": DEFAULT_ALLOWED_USER,
            "target_path": "",
            "offline_subdir": DEFAULT_OFFLINE_SUBDIR,
            "batch_size": DEFAULT_BATCH_SIZE,
            "batch_interval": DEFAULT_BATCH_INTERVAL,
            "library_check_enabled": True,
            "allow_upgrade": False,
            "delete_when_exists": True,
            "offline_timeout": 5,
            "offline_progress_timeout": 10,
            "offline_failed_timeout": DEFAULT_OFFLINE_FAILED_TIMEOUT,
            "precheck_enabled": True,
            "precheck_scope": DEFAULT_PRECHECK_SCOPE,
            "precheck_timeout": DEFAULT_PRECHECK_TIMEOUT,
            "notify_transfer": True,
            "notify_strm": False,
            "notify_only_from_bot": True,
            "notify_window": DEFAULT_NOTIFY_WINDOW,
            "cleanup_residue_enabled": True,
            "cleanup_unrecognized": True,
            "cleanup_empty_dirs": True,
            "residue_delay": 300,
            "residue_sweep_interval": 600,
            "residue_idle_recheck": 0,
            "notify_chat_id": "",
            "strm_local_root": "",
            "strm_pan_root": "/video",
            "strm_check_delay": DEFAULT_STRM_CHECK_DELAY,
            "api_base": DEFAULT_API_BASE,
            "proxy": "",
            "api_token": "",
        }

    def get_page(self) -> Optional[List[dict]]:
        """
        返回插件详情页面：运行状态与最近提交记录。

        :return: Vuetify 页面结构；插件未启用时为 None
        """
        if not self._enabled:
            return None
        if self._last_error:
            state_type = "warning"
            state_text = f"未就绪：{self._last_error}"
        elif self._thread and self._thread.is_alive():
            state_type = "success"
            state_text = (
                f"长轮询运行中"
                f"　|　bot：@{self._bot_username or '未知'}"
                f"　|　启动于 {self._started_at or '未知'}"
            )
        else:
            state_type = "warning"
            state_text = "长轮询未运行"

        target_text = self._target_display()
        whitelist_text = "、".join(sorted(self._allowed_users)) or "（空）"
        history_items = [
            {
                "time": item.get("time", ""),
                "kind": item.get("kind", ""),
                "detail": item.get("detail", ""),
            }
            for item in reversed(self._history[-HISTORY_LIMIT:])
        ]
        page: List[dict] = [
            {
                "component": "VAlert",
                "props": {"type": state_type, "text": state_text},
            },
            {
                "component": "VAlert",
                "props": {
                    "type": "info",
                    "text": f"目标目录：{target_text}",
                },
            },
            {
                "component": "VAlert",
                "props": {
                    "type": "info",
                    "text": f"白名单：{whitelist_text}（共 {len(self._allowed_users)} 人）",
                },
            },
            {
                "component": "VAlert",
                "props": {
                    "type": "info",
                    "text": f"分批策略：每批 {self._batch_size} 条，间隔 {self._batch_interval} 秒"
                            f"（失败批次自动延长到 {self._batch_interval * 2} 秒）",
                },
            },
            {
                "component": "VAlert",
                "props": {
                    "type": "info",
                    "text": f"预整理：库检查{'开' if self._library_check_enabled else '关'}"
                            f"　|　MP/AI 比对{'开' if self._precheck_enabled else '关'}"
                            f"　|　等待选择 {self._precheck_timeout} 分钟"
                            f"　|　通知：整理{'开' if self._notify_transfer else '关'}"
                            f"/STRM{'开' if self._notify_strm else '关'}",
                },
            },
        ]
        if history_items:
            page.append(
                {
                    "component": "VTable",
                    "props": {
                        "headers": [
                            {"title": "时间", "key": "time"},
                            {"title": "结果", "key": "kind"},
                            {"title": "详情", "key": "detail"},
                        ],
                        "items": history_items,
                        "density": "compact",
                    },
                }
            )
        else:
            page.append(
                {
                    "component": "VAlert",
                    "props": {"type": "info", "text": "还没有提交记录"},
                }
            )
        return page

    def stop_service(self) -> None:
        """
        停止长轮询线程并释放资源。

        从轮询线程内部被调用时（例如线程内持久化配置触发重载）不做自我 join，
        避免 RuntimeError；线程会因 generation 变化在下一轮退出。
        """
        self._stop_event.set()
        with self._choice_lock:
            pending_timers = list(self._notify_timers.values())
            self._notify_timers.clear()
            pending_timers.extend(self._residue_timers.values())
            self._residue_timers.clear()
            pending_timers.extend(self._misc_timers.values())
            self._misc_timers.clear()
        for timer in pending_timers:
            timer.cancel()
        thread = self._thread
        self._thread = None
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=LONG_POLL_SECONDS + 5)
        # 巡检线程内含同步 HTTP，最多等 5 秒，剩余交给 stop_event/generation 自行退出
        watch = self._offline_watch_thread
        self._offline_watch_thread = None
        if watch and watch.is_alive() and watch is not threading.current_thread():
            watch.join(timeout=5)

    # ============================ API 入口 ============================

    async def api_whitelist(self, request: Request) -> Dict[str, Any]:
        """
        查看或增删白名单用户。

        查询参数：action=list|add|remove，user_id=<数字>。

        :param request: FastAPI 请求对象
        :return: 操作结果与当前白名单
        """
        params = dict(request.query_params)
        body: Dict[str, Any] = {}
        try:
            payload = await request.json()
            if isinstance(payload, dict):
                body = payload
        except Exception:
            body = {}
        action = str(body.get("action") or params.get("action") or "list").strip().lower()
        user_id = str(body.get("user_id") or params.get("user_id") or "").strip()
        if action not in ("list", "add", "remove"):
            return {"success": False, "message": "action 只支持 list / add / remove"}
        if action in ("add", "remove"):
            if not _RE_ALLOWED_ID.fullmatch(user_id):
                return {
                    "success": False,
                    "message": "user_id 支持用户 ID（正数）、群组 ID（负数）或 @用户名",
                }
            if action == "add":
                self._allowed_users.add(user_id)
            else:
                # 先判断再删除：直接 discard 后清空会导致白名单真的变空
                if len(self._allowed_users) <= 1 and user_id in self._allowed_users:
                    return {"success": False, "message": "拒绝清空白名单"}
                self._allowed_users.discard(user_id)
            self._persist_config_async(allowed_users=",".join(sorted(self._allowed_users)))
            self._append_history("白名单", f"{action} {user_id}")
        return {
            "success": True,
            "message": "ok",
            "allowed_users": sorted(self._allowed_users),
        }

    async def api_status(self, request: Request) -> Dict[str, Any]:
        """
        查看机器人运行状态与最近提交记录。

        :param request: FastAPI 请求对象
        :return: 状态字典
        """
        return {
            "success": True,
            "message": "ok",
            "enabled": self._enabled,
            "running": bool(self._thread and self._thread.is_alive()),
            "bot_username": self._bot_username,
            "allowed_users": sorted(self._allowed_users),
            "target_path": self._target_path,
            "offline_subdir": self._offline_subdir,
            "effective_target_path": self._effective_target_path(),
            "started_at": self._started_at,
            "last_error": self._last_error,
            "last_result": self._last_result,
            "history": list(reversed(self._history[-HISTORY_LIMIT:])),
        }

    # ============================ 长轮询 ============================

    def _poll_loop(self, generation: int) -> None:
        """
        长轮询主循环：拉取更新并分发处理。

        :param generation: 本次启动的代号；配置重载后旧线程据此退出
        """
        self._fetch_me()
        while not self._stop_event.is_set() and generation == self._generation:
            try:
                updates = self._get_updates()
            except Exception as err:  # noqa: BLE001 - 网络异常需要兜底重试
                message = self._mask_secrets(str(err))
                if "409" in message or "Conflict" in message:
                    message = "另一个实例正在使用同一 token 轮询（409 Conflict）"
                self._last_error = message
                logger.error(f"【TG磁力离线】拉取更新失败：{message}")
                self._stop_event.wait(5)
                continue
            self._last_error = ""
            for update in updates:
                if self._stop_event.is_set() or generation != self._generation:
                    break
                try:
                    self._handle_update(update)
                except Exception as err:  # noqa: BLE001 - 单条消息失败不影响后续
                    logger.error(f"【TG磁力离线】处理消息失败：{err}")
            if updates:
                self._persist_state()
        logger.info("【TG磁力离线】长轮询已退出")

    def _get_updates(self) -> List[Dict[str, Any]]:
        """
        调用 Bot API getUpdates 拉取一批更新。

        :return: 更新列表
        """
        params: Dict[str, Any] = {
            "timeout": LONG_POLL_SECONDS,
            # 必须同时订阅按钮回调，否则「按 MP / 按 AI 整理」的点击永远不会送达
            "allowed_updates": json.dumps(["message", "callback_query"]),
        }
        if self._offset:
            params["offset"] = self._offset
        response = requests.get(
            self._bot_url("getUpdates"),
            params=params,
            timeout=LONG_POLL_SECONDS + 15,
            proxies=self._telegram_proxies(),
        )
        payload = self._parse_bot_response(response)
        return payload.get("result") or []

    def _fetch_me(self) -> None:
        """
        读取 bot 自身信息，用于状态页展示用户名。
        """
        try:
            response = requests.get(
                self._bot_url("getMe"),
                timeout=20,
                proxies=self._telegram_proxies(),
            )
            payload = self._parse_bot_response(response)
            result = payload.get("result") or {}
            self._bot_username = str(result.get("username") or "")
            logger.info(f"【TG磁力离线】bot 用户名：@{self._bot_username}")
        except Exception as err:  # noqa: BLE001 - 取用户名失败不影响收消息
            self._last_error = f"获取 bot 信息失败：{self._mask_secrets(str(err))}"
            logger.error(f"【TG磁力离线】{self._last_error}")

    @staticmethod
    def _mask_secrets(text: str) -> str:
        """
        抹掉文本中的 bot token，避免异常信息把凭据写进日志。

        :param text: 原始文本
        :return: 脱敏后的文本
        """
        return re.sub(r"/bot[^/\s]+/", "/bot<redacted>/", str(text or ""))

    @staticmethod
    def _parse_bot_response(response: Any) -> Dict[str, Any]:
        """
        解析 Bot API 响应，非 200 时抛出可诊断错误。

        :param response: requests 响应对象
        :return: 响应 JSON
        """
        try:
            payload = response.json()
        except Exception as err:  # noqa: BLE001 - 非 JSON 响应按网络错误处理
            raise RuntimeError(f"Bot API 返回非 JSON（HTTP {response.status_code}）：{err}") from err
        if response.status_code != 200 or not payload.get("ok"):
            description = payload.get("description") or "未知错误"
            raise RuntimeError(f"Bot API 错误（HTTP {response.status_code}）：{description}")
        return payload

    def _bot_url(self, method: str) -> str:
        """
        拼接指定 Bot API 方法的完整地址。

        :param method: 方法名，如 getUpdates
        :return: 完整 URL（含 token，禁止写入日志）
        """
        return f"{self._api_base}/bot{self._bot_token}/{method}"

    def _telegram_proxies(self) -> Optional[Dict[str, str]]:
        """
        计算访问 Telegram 使用的代理配置。

        优先使用插件配置，其次读取宿主 PROXY 设置；都为空时直连。
        宿主 ``settings.PROXY`` 是 ``{"http": ..., "https": ...}`` 形式的字典，
        而插件配置项是单个地址字符串，两种形态都要支持。

        :return: requests 代理字典或 None
        """
        raw: Any = self._proxy or self._host_setting("PROXY", "")
        if isinstance(raw, dict):
            proxies = {
                str(key): str(value).strip()
                for key, value in raw.items()
                if str(key).strip() and str(value or "").strip()
            }
            return proxies or None
        proxy = str(raw or "").strip()
        if not proxy:
            return None
        return {"http": proxy, "https": proxy}

    def _handle_update(self, update: Dict[str, Any]) -> None:
        """
        处理单条 Telegram 更新。

        :param update: getUpdates 返回的单条更新
        """
        update_id = int(update.get("update_id") or 0)
        if update_id:
            with self._lock:
                self._offset = max(self._offset, update_id + 1)

        callback = update.get("callback_query")
        if callback:
            self._handle_choice_callback(callback)
            return

        message = update.get("message")
        if not message:
            return
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        user = message.get("from") or {}
        user_id = str(user.get("id") or "")
        message_id = message.get("message_id")

        chat_type = str(chat.get("type") or "")
        chat_key = str(chat_id or "")
        username = str(user.get("username") or "")
        allowed_names = {
            item.lower() for item in self._allowed_users if item.startswith("@")
        }
        is_allowed_user = user_id in self._allowed_users or (
            bool(username) and f"@{username}".lower() in allowed_names
        )
        if chat_type not in ("private", "group", "supergroup"):
            # 频道等其它会话不处理
            return
        if chat_type == "private":
            if not is_allowed_user:
                logger.warning(f"【TG磁力离线】忽略非白名单用户消息：{user_id}")
                self._append_history("已忽略", f"非白名单用户 {user_id}")
                return
            # 私聊里本人即拥有全部权限
            can_manage = True
            # 记录最近交互会话，作为整理通知的默认目标
            if isinstance(chat_id, int):
                self._last_chat_id = chat_id
        else:
            # 群组：群 ID 在白名单即视为该群已授权，群成员可提交链接；
            # 修改目录与白名单仍只允许个人白名单成员，避免群内任何人改配置
            if chat_key not in self._allowed_users and not is_allowed_user:
                logger.warning(
                    f"【TG磁力离线】忽略未授权群消息：chat={chat_key} "
                    f"type={chat_type} user={user_id}"
                )
                self._append_history("已忽略", f"未授权群 {chat_key}")
                return
            can_manage = is_allowed_user

        text = str(message.get("text") or "").strip()
        if not text:
            self._reply(chat_id, "只支持文本消息：直接发送磁力 / ed2k / 种子链接。", message_id)
            return

        if text.startswith("/"):
            # 按任意空白切分：全角空格、制表符、换行都能识别
            parts = text.split(maxsplit=1)
            command = parts[0]
            argument = parts[1].strip() if len(parts) > 1 else ""
            # 兼容 /cmd@bot_username 写法
            command = command.split("@", 1)[0].lower()
            argument = argument.strip()
            self._handle_command(command, argument, chat_id, message_id, can_manage)
            return

        links = parse_offline_links(text)
        if not links:
            self._reply(
                chat_id,
                "没有识别到磁力 / ed2k / 种子链接。\n发送 /help 查看用法。",
                message_id,
            )
            return
        self._submit_and_reply(links, chat_id, message_id)

    def _handle_command(
        self,
        command: str,
        argument: str,
        chat_id: Any,
        message_id: Any,
        can_manage: bool = True,
    ) -> None:
        """
        处理 bot 自有指令。

        :param command: 已归一化的命令名（含前导斜杠）
        :param argument: 命令参数
        :param chat_id: 目标会话
        :param message_id: 原消息 ID，用于回复
        :param can_manage: 当前发送者是否具备管理权限；群组里只有个人白名单成员为真
        """
        if command in ("/start", "/help"):
            self._reply(chat_id, self._help_text(), message_id)
            return
        if not can_manage:
            self._reply(
                chat_id,
                "本群已授权提交离线链接；修改目标目录、白名单与查看状态"
                "请由白名单成员操作或私聊机器人。",
                message_id,
            )
            return
        if command == "/status":
            self._reply(chat_id, self._status_text(), message_id)
            return
        if command == "/dir":
            self._handle_dir_command(argument, chat_id, message_id)
            return
        if command in ("/allow", "/deny"):
            self._handle_whitelist_command(command, argument, chat_id, message_id)
            return
        self._reply(chat_id, "未知指令。发送 /help 查看可用指令。", message_id)

    def _handle_dir_command(self, argument: str, chat_id: Any, message_id: Any) -> None:
        """
        查看或修改 115 目标目录。

        :param argument: 留空表示查看；填 "default" 或 "-" 表示恢复默认链路
        :param chat_id: 目标会话
        :param message_id: 原消息 ID
        """
        if not argument:
            current = self._target_display()
            self._reply(
                chat_id,
                f"当前目标目录：{current}\n"
                "用法：/dir <115 路径>　或　/dir default（恢复默认链路）",
                message_id,
            )
            return
        if argument.lower() in ("default", "-", "reset"):
            self._target_path = ""
            self._persist_config_async(target_path="")
            self._append_history("目录", "恢复自动解析")
            self._reply(
                chat_id,
                f"已恢复自动解析：{self._target_display()}",
                message_id,
            )
            return
        path = self._normalize_path(argument)
        if not path:
            self._reply(chat_id, "路径无效：需以 / 开头的 115 网盘路径", message_id)
            return
        self._target_path = path
        self._persist_config_async(target_path=path)
        self._append_history("目录", path)
        self._reply(
            chat_id,
            f"目标目录已改为：{path}\n注意：指定目录不会进入 MoviePilot 整理队列。",
            message_id,
        )

    def _handle_whitelist_command(
        self, command: str, argument: str, chat_id: Any, message_id: Any
    ) -> None:
        """
        增删白名单用户。

        :param command: /allow 或 /deny
        :param argument: 目标用户 ID
        :param chat_id: 目标会话
        :param message_id: 原消息 ID
        """
        target = argument.strip()
        if not _RE_ALLOWED_ID.fullmatch(target):
            self._reply(
                chat_id,
                "用法：/allow <用户ID / 群组ID / @用户名>　或　/deny <同上>",
                message_id,
            )
            return
        if command == "/allow":
            self._allowed_users.add(target)
            self._persist_config_async(allowed_users=",".join(sorted(self._allowed_users)))
            self._append_history("白名单", f"add {target}")
            self._reply(chat_id, f"已加入白名单：{target}", message_id)
            return
        self._allowed_users.discard(target)
        if not self._allowed_users:
            # 不允许把自己也删掉，否则只能进网页配置页恢复
            self._allowed_users.add(target)
            self._reply(chat_id, "拒绝清空白名单：至少保留一个用户。", message_id)
            return
        self._persist_config_async(allowed_users=",".join(sorted(self._allowed_users)))
        self._append_history("白名单", f"remove {target}")
        self._reply(chat_id, f"已移出白名单：{target}", message_id)

    def _submit_and_reply(
        self, links: List[str], chat_id: Any, message_id: Any
    ) -> Optional[threading.Thread]:
        """
        提交离线任务并把结果回执给用户。

        链接数不超过每批条数时同步提交并立即回执；超过则转后台分批提交，
        用同一条进度消息更新，避免大批量一次打到 115 触发风控。

        :param links: 待提交的链接列表
        :param chat_id: 目标会话
        :param message_id: 原消息 ID
        :return: 后台分批线程；同步提交时为 None
        """
        target = self._target_display()
        batch_size = max(1, self._batch_size)
        if len(links) <= batch_size:
            try:
                # 与后台分批共用一道闸，避免两路同时打 115 触发风控
                with self._submit_lock:
                    success, detail, submitted = self._submit_links(links)
            except Exception as err:  # noqa: BLE001 - 提交异常必须有回执
                logger.error(f"【TG磁力离线】同步提交异常：{err}")
                success, detail, submitted = False, f"提交异常：{err}", 0
            if success:
                self._last_result = detail
                # 回执只保留提交结果本身：条数和目标目录对用户没有增量信息，
                # 落点可以在 /status 里看
                text = detail
                self._append_history("成功", f"{submitted} 条 · {detail}")
            else:
                self._last_result = f"提交失败：{detail}"
                text = f"提交失败：{detail}"
                self._append_history("失败", detail)
            self._reply(chat_id, text, message_id)
            return None

        batches = [
            links[index : index + batch_size]
            for index in range(0, len(links), batch_size)
        ]
        thread = threading.Thread(
            target=self._submit_batches,
            args=(batches, chat_id, message_id),
            name="TgOfflineBot-Batch",
            daemon=True,
        )
        thread.start()
        return thread

    def _submit_batches(
        self, batches: List[List[str]], chat_id: Any, message_id: Any
    ) -> None:
        """
        分批提交离线任务，并用同一条消息持续更新进度。

        批次之间遵守配置的间隔；某一批失败时把间隔延长一倍再继续，
        给 115 侧留冷却时间，而不是直接放弃后面的链接。

        :param batches: 已切分好的批次
        :param chat_id: 目标会话
        :param message_id: 原消息 ID
        """
        total = sum(len(batch) for batch in batches)
        target = self._target_display()
        progress_id = self._send_text(
            chat_id,
            f"已接收 {total} 条链接，分 {len(batches)} 批提交"
            f"（每批 {self._batch_size} 条，间隔 {self._batch_interval} 秒）\n"
            f"目标：{target}",
            message_id,
        )
        submitted = 0
        cumulative = 0
        failures: List[str] = []
        stopped = False
        # 记录进入时的停止信号与代数：重载会替换这两个对象，
        # 用局部变量判断才能让旧线程在新一轮初始化后立刻停下
        stop_event = self._stop_event
        generation = self._generation

        with self._submit_lock:
            try:
                for index, batch in enumerate(batches, start=1):
                    if stop_event.is_set() or generation != self._generation:
                        stopped = True
                        break
                    cumulative += len(batch)
                    try:
                        success, detail, accepted = self._submit_links(batch)
                    except Exception as err:  # noqa: BLE001 - 单批异常不拖垮整轮
                        success, detail, accepted = False, f"提交异常：{err}", 0
                    if success:
                        submitted += accepted
                    else:
                        failures.append(f"第 {index} 批：{detail}")
                    logger.info(
                        f"【TG磁力离线】分批进度 {index}/{len(batches)}："
                        f"本批 {len(batch)} 条，{'成功' if success else '失败'}"
                    )
                    self._edit_text(
                        chat_id,
                        progress_id,
                        f"提交中：{index}/{len(batches)} 批，已成功 {submitted} 条"
                        f"，累计 {cumulative}/{total} 条"
                        + (f"\n失败 {len(failures)} 批" if failures else ""),
                    )
                    if index < len(batches):
                        # 失败后延长等待，降低连续触发风控的概率
                        wait_seconds = (
                            self._batch_interval
                            if success
                            else self._batch_interval * 2
                        )
                        if self._wait_between_batches(wait_seconds):
                            stopped = True
                            break
            finally:
                # 无论正常结束、停止还是异常中断，都要给出最终回执并落账
                if stopped:
                    final_text = f"已停止：成功提交 {submitted}/{total} 条"
                else:
                    final_text = f"提交完成：成功 {submitted}/{total} 条"
                if failures:
                    final_text += "\n" + "\n".join(failures[:5])
                    if len(failures) > 5:
                        final_text += f"\n……另有 {len(failures) - 5} 批失败"

                self._last_result = final_text.replace("\n", " ")
                self._edit_text(chat_id, progress_id, final_text)
                if progress_id is None:
                    self._send_text(chat_id, final_text, message_id)
                self._append_history(
                    "成功" if not failures and not stopped else "部分完成",
                    f"{submitted}/{total} 条",
                )

    def _wait_between_batches(self, seconds: float) -> bool:
        """
        等待批间隔，期间可被插件停止打断。

        :param seconds: 期望等待的秒数
        :return: 是否因插件停止而提前结束
        """
        deadline = time.monotonic() + max(0.0, float(seconds))
        while True:
            if self._stop_event.is_set():
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return self._stop_event.is_set()
            time.sleep(min(1.0, remaining))

    def _filter_existing_links(
        self, links: List[str]
    ) -> Tuple[List[str], List[str]]:
        """
        过滤掉 115 离线列表里已经存在的任务，避免重复提交被拒。

        :param links: 待提交链接
        :return: (仍需提交的链接, 已存在的名称)
        """
        if not links:
            return [], []
        tasks = self._list_offline_tasks()
        if not tasks:
            # 接口不可用或没有任务时不拦截，交给 115 自己判断
            return list(links), []
        existing = {
            self._name_key(str(task.get("name") or ""))
            for task in tasks
            if task.get("name")
        }
        existing = {name for name in existing if len(name) >= 8}
        pending: List[str] = []
        skipped: List[str] = []
        for link in links:
            hints = self._link_name_hints(link)
            key = self._name_key(hints[0]) if hints else ""
            # 只认「完全相等」或「已有任务名以新名为前缀」；反向子串会把
            # 名字更长的正常新任务误判成已存在
            # 只认完全同名：反向前缀会把「已有任务名更长」的正常新任务也判成已存在
            if key and key in existing:
                skipped.append(hints[0])
            else:
                pending.append(link)
        return pending, skipped

    def _submit_links(self, links: List[str]) -> Tuple[bool, str, int]:
        """
        调用本机 P115StrmHelper 的离线下载接口。

        :param links: 链接列表
        :return: (是否成功, 结果说明, 实际提交条数)
        """
        links, skipped = self._filter_existing_links(links)
        if not links:
            return True, f"{len(skipped)} 条任务均已在离线列表中，已跳过提交", 0
        token = self._api_token_value()
        if not token:
            return False, "缺少 MoviePilot API Token，无法调用离线接口", 0
        ready, target, note = self._ensure_submit_target()
        if not ready:
            return False, note, 0
        if note:
            logger.info(f"【TG磁力离线】{note}")
        url = f"http://127.0.0.1:{self._host_port}/api/v1/plugin/{STRM_HELPER_PLUGIN}/add_offline_task"
        payload = {"links": links, "path": target or None}
        try:
            response = requests.post(
                url,
                json=payload,
                headers={"X-API-KEY": token, "Content-Type": "application/json"},
                timeout=90,
                # 本机回环请求必须绕过系统代理，否则会被代理拦走
                proxies={"http": None, "https": None},
            )
        except Exception as err:  # noqa: BLE001 - 网络异常统一转成回执
            logger.error(f"【TG磁力离线】调用离线接口异常：{err}")
            return False, f"调用离线接口异常：{err}", 0
        if response.status_code == 404:
            return False, "未找到 P115StrmHelper 离线接口，请确认插件已安装并启用", 0
        if response.status_code in (401, 403):
            return False, f"离线接口鉴权失败（HTTP {response.status_code}），请检查 API Token", 0
        try:
            data = response.json()
        except Exception:  # noqa: BLE001 - 非 JSON 响应按失败处理
            return False, f"离线接口返回异常（HTTP {response.status_code}）", 0
        if response.status_code != 200:
            return False, f"离线接口返回 HTTP {response.status_code}：{data}", 0
        code = data.get("code", 0)
        message = str(data.get("msg") or data.get("message") or "").strip()
        if code not in (0, None):
            return False, message or f"插件返回错误码 {code}", 0
        landed_warning = ""
        if target and not self._get_existing_dir(target):
            # 接口返回成功但目录仍不可用：115 会静默把任务放进默认离线目录。
            # 任务确实已建好，仍要登记来源，否则后续预检/整理都不认它。
            logger.error(f"【TG磁力离线】提交后目标目录仍不可用：{target}")
            landed_warning = (
                f"目标目录 {target} 不可用，文件可能落在 115 默认离线目录，请检查"
            )
        logger.info(f"【TG磁力离线】提交成功：{len(links)} 条 · {message}")
        self._record_owned_links(links)
        self._last_submit_ts = time.time()
        self._offline_active_until = time.time() + 3600
        try:
            self.save_data("last_submit_ts", self._last_submit_ts)
        except Exception as err:  # noqa: BLE001 - 持久化失败不影响提交
            logger.warning(f"【TG磁力离线】保存提交时间失败：{err}")
        detail = message or f"{len(links)} 个新任务已提交"
        if skipped:
            detail += f"；跳过 {len(skipped)} 条已存在的任务"
        if landed_warning:
            detail += f"；{landed_warning}"
        return True, detail, len(links)

    # ============================ 整理前识别比对 ============================

    @eventmanager.register(ChainEventType.TransferIntercept)
    def _on_transfer_intercept(self, event: Event) -> None:
        """
        整理前拦截：比对 MP 识别与 AI 识别结果。

        一致时放行，按 MP 结果正常整理；不一致时取消本次整理，推送两个按钮
        让用户选择按哪一边整理；AI 不可用或置信度不足时放行，避免打扰。

        :param event: TransferIntercept 事件
        """
        if not self._enabled or not self._precheck_enabled:
            return
        data = event.event_data
        if not isinstance(data, TransferInterceptEventData) or data.cancel:
            return
        fileitem = getattr(data, "fileitem", None)
        mediainfo = getattr(data, "mediainfo", None)
        if not fileitem or not mediainfo:
            return
        source_path = str(getattr(fileitem, "path", "") or "")
        if not source_path or not self._in_precheck_scope(source_path):
            return
        # 只处理本插件提交的任务；其它渠道（例如直接从 P115StrmHelper 提交）一律不介入
        if not self._is_owned_path(source_path):
            logger.info(f"【TG磁力离线】非本插件任务，跳过预检：{source_path}")
            return
        if self._is_skipped(source_path):
            return

        # 库中已存在：在预整理阶段直接拦下并通知，不再做 AI 比对
        if self._library_check_enabled:
            exist_text = self._library_exists(mediainfo, fileitem)
            if exist_text:
                if self._allow_upgrade:
                    # 允许洗版：不拦截、不删源，交给 MP 的覆盖模式决定是否替换
                    self._append_history("洗版放行", exist_text)
                    logger.info(f"【TG磁力离线】允许洗版，放行整理：{source_path}")
                    return
                data.cancel = True
                data.source = "TgOfflineBot"
                data.reason = f"媒体库已存在：{exist_text}"
                logger.info(
                    f"【TG磁力离线】媒体库已存在，跳过整理：{source_path} -> {exist_text}"
                )
                self._append_history("库中已存在", exist_text)
                deleted = False
                if self._delete_when_exists:
                    deleted, delete_detail = self._delete_source(fileitem)
                    logger.info(
                        f"【TG磁力离线】库已存在，删除源文件"
                        f"{'成功' if deleted else '失败'}：{source_path}"
                    )
                episode = 0
                try:
                    meta = MetaInfo(Path(source_path).stem)
                    episode = meta.begin_episode or 0
                except Exception:  # noqa: BLE001 - 解析失败按文件名展示
                    episode = 0
                label = f"E{episode:02d}" if episode else Path(source_path).name
                suffix = "（已删源）" if deleted else ""
                title = str(getattr(mediainfo, "title", "") or "")
                season = 0
                seasons = getattr(mediainfo, "seasons", None) or {}
                if isinstance(seasons, dict) and seasons:
                    try:
                        season = int(sorted(seasons.keys())[0])
                    except (TypeError, ValueError):
                        season = 0
                self._buffer_misc_notify(
                    f"exists|{title}|{season}", f"{label}{suffix}"
                )
                # 被拦截的目录同样需要清残留：这里不会产生 TransferComplete
                self._schedule_residue_cleanup(source_path, fileitem)
                return

        ai = self._ai_recognize(source_path, mediainfo)
        if not ai or not ai.name or ai.confidence < AI_CONFIDENCE_FLOOR:
            logger.info(f"【TG磁力离线】AI 识别不可用或置信度不足，放行整理：{source_path}")
            return
        if self._same_media(mediainfo, ai):
            self._append_history("识别一致", source_path)
            return

        snapshot = self._mp_snapshot(mediainfo)
        data.cancel = True
        data.source = "TgOfflineBot"
        data.reason = "识别结果待确认：MP 与 AI 不一致"
        logger.info(
            f"【TG磁力离线】识别不一致，拦截整理：{source_path} | MP={snapshot} | AI={ai.model_dump()}"
        )
        self._ask_choice(source_path, snapshot, ai, fileitem)

    @staticmethod
    def _mp_snapshot(mediainfo: Any) -> Dict[str, str]:
        """
        把 MP 识别结果压缩成可展示、可比较的字段。

        :param mediainfo: MP 的 MediaInfo 对象
        :return: 标题、年份、类型、季集、媒体 ID 等字段
        """
        raw_type = str(getattr(mediainfo, "type", "") or "")
        if raw_type == str(MediaType.TV):
            kind = "tv"
        elif raw_type == str(MediaType.MOVIE):
            kind = "movie"
        else:
            kind = raw_type or "unknown"
        seasons = getattr(mediainfo, "seasons", None) or {}
        season_text = ""
        episode_text = ""
        if isinstance(seasons, dict) and seasons:
            try:
                first_season = sorted(seasons.keys())[0]
                season_text = f"第{int(first_season)}季"
                episodes = seasons.get(first_season) or []
                if episodes:
                    # 季集值可能是 list 也可能是 set，统一取最小集号
                    first_episode = min(int(value) for value in episodes)
                    episode_text = f"S{int(first_season):02d}E{first_episode:02d}"
            except (TypeError, ValueError):
                # 季号已经解析成功时不要一并清空，只丢弃集号
                episode_text = ""
        return {
            "title": str(getattr(mediainfo, "title", "") or ""),
            "year": str(getattr(mediainfo, "year", "") or ""),
            "type": kind,
            "season": season_text,
            "episode": episode_text,
            "media_id": str(getattr(mediainfo, "media_id", "") or ""),
            "source": str(getattr(mediainfo, "media_source", "") or ""),
        }

    def _in_precheck_scope(self, path: str) -> bool:
        """
        判断源路径是否属于需要比对的整理范围。

        :param path: 待整理源文件路径
        :return: 是否在范围内
        """
        raw = self._precheck_scope or DEFAULT_PRECHECK_SCOPE
        prefixes = [item.strip() for item in re.split(r"[,，\n]", raw) if item.strip()]
        for prefix in prefixes:
            normalized = prefix.rstrip("/")
            if path == normalized or path.startswith(normalized + "/"):
                return True
        # 手填的 target_path 可能不在比对范围里；否则预检会静默失效
        target = self._effective_target_path()
        if target and (path == target or path.startswith(target + "/")):
            return True
        return False

    def _is_skipped(self, path: str) -> bool:
        """
        判断该路径是否处于「已决策、跳过比对」的时间窗内。

        :param path: 源文件路径
        :return: 是否需要跳过比对
        """
        with self._choice_lock:
            until = self._skip_precheck_until.get(path)
            if until is None:
                return False
            if until < time.time():
                self._skip_precheck_until.pop(path, None)
                return False
            return True

    @staticmethod
    def _name_key(text: str) -> str:
        """
        归一化名称，用于来源匹配：去空白、标点并转小写。

        :param text: 原始文本
        :return: 归一化结果
        """
        value = re.sub(
            r"[\s\-_:：·,，.。'\"!！?？()（）\[\]【】]", "", str(text or "")
        )
        return value.lower()

    @staticmethod
    def _link_name_hints(link: str) -> List[str]:
        """
        从提交链接中提取可用于识别任务来源的名称提示。

        :param link: 磁力 / ed2k / 种子链接
        :return: 名称提示列表
        """
        hints: List[str] = []
        text = str(link or "").strip()
        lower = text.lower()
        if lower.startswith("magnet:"):
            try:
                params = parse_qs(urlparse(text).query)
            except Exception:  # noqa: BLE001 - 解析失败时不产生提示
                params = {}
            for value in params.get("dn", []):
                name = str(value or "").strip()
                if name:
                    hints.append(name)
        elif lower.startswith("ed2k://"):
            match = re.search(r"ed2k://\|file\|([^|]+)\|", text, re.IGNORECASE)
            if match:
                hints.append(match.group(1).strip())
        else:
            try:
                name = unquote(Path(urlparse(text).path).name)
            except Exception:  # noqa: BLE001 - 解析失败时不产生提示
                name = ""
            if name:
                hints.append(Path(name).stem)
        return [item for item in hints if item]

    @staticmethod
    def _link_info_hash(link: str) -> str:
        """
        提取磁力链接的 BT info_hash（40 位十六进制小写）。

        只有磁力链接能直接给出 info_hash；ed2k 与种子直链返回空字符串，
        仍按名称提示匹配。32 位 base32 形式的 btih 会先解码再归一化。

        :param link: 磁力 / ed2k / 种子链接
        :return: info_hash；无法取得时为空字符串
        """
        text = str(link or "").strip()
        if not text.lower().startswith("magnet:"):
            return ""
        try:
            params = parse_qs(urlparse(text).query)
        except Exception:  # noqa: BLE001 - 解析失败按无法识别处理
            return ""
        for value in params.get("xt", []):
            raw = str(value or "").strip()
            if not raw.lower().startswith("urn:btih:"):
                continue
            digest = raw.split(":", 2)[-1].strip()
            if re.fullmatch(r"[0-9A-Fa-f]{40}", digest):
                return digest.lower()
            if re.fullmatch(r"[A-Za-z2-7]{32}", digest):
                try:
                    return base64.b32decode(digest.upper()).hex()
                except Exception:  # noqa: BLE001 - 非法 base32 不记录
                    return ""
        return ""

    def _record_owned_links(self, links: List[str]) -> None:
        """
        记录本次提交任务的来源标记，后续只处理这些任务。

        :param links: 已成功提交的链接
        """
        now = time.time()
        updated = False
        with self._choice_lock:
            for link in links:
                for hint in self._link_name_hints(link):
                    key = self._name_key(hint)
                    # 太短的名称容易误命中别的内容，直接忽略
                    if len(key) >= 6:
                        self._owned_names[key] = now + OWNED_TTL_SECONDS
                        updated = True
            self._prune_owned_names_locked()
        if updated:
            self._persist_owned_names()
            # 新提交的任务马上可能落盘，唤醒已休眠的兜底巡检
            self._wake_residue_sweep("提交了新任务")
        hashes = [
            info_hash
            for info_hash in (self._link_info_hash(link) for link in links)
            if info_hash
        ]
        if not hashes:
            return
        # 指定了目标目录时，P115StrmHelper 不会把任务登记进整理队列，
        # 完成后由本插件按 info_hash 触发整理，这里先登记待办。
        if self._effective_target_path():
            transfer_deadline = now + PENDING_TRANSFER_TTL_SECONDS
            with self._choice_lock:
                for info_hash in hashes:
                    self._pending_transfer[info_hash] = transfer_deadline
            self._persist_pending_transfer()
        deadline = now + OWNED_PENDING_TTL_SECONDS
        with self._choice_lock:
            for info_hash in hashes:
                self._pending_owned_hashes[info_hash] = deadline
        self._start_owned_sync(hashes)

    def _start_owned_sync(self, hashes: List[str]) -> None:
        """
        后台把本次提交的 info_hash 与 115 离线任务名对齐。

        磁力链接不带 dn 时，落盘名只能从离线任务列表拿到；不补记的话
        后续整理预检会把这些任务误判成「非本插件任务」。
        同一时刻只允许一个补记线程，它会把期间新登记的 hash 一起处理。

        :param hashes: 本次提交的 info_hash 列表
        """
        if not hashes:
            return
        if not self._owned_sync_lock.acquire(blocking=False):
            return
        threading.Thread(
            target=self._run_owned_sync,
            args=(list(hashes),),
            name="TgOfflineBot-OwnedSync",
            daemon=True,
        ).start()

    def _run_owned_sync(self, hashes: List[str]) -> None:
        """
        补记线程主体：执行同步并确保释放单飞锁。

        :param hashes: 本次提交的 info_hash 列表
        """
        try:
            self._sync_owned_task_names(hashes)
        finally:
            self._owned_sync_lock.release()

    def _sync_owned_task_names(
        self,
        hashes: Optional[List[str]] = None,
        attempts: int = OWNED_SYNC_ATTEMPTS,
    ) -> None:
        """
        按 info_hash 把 115 离线任务名补记为来源标记。

        提交后任务不一定立刻出现在离线列表里，因此按固定间隔重试；
        只认本次提交的 info_hash，不会把其它渠道的任务算成本插件任务。

        :param hashes: 本次需要补齐的 info_hash；为空时处理全部待办
        :param attempts: 最多轮询次数
        """
        targets = {str(item).lower() for item in (hashes or []) if item}
        if targets:
            with self._choice_lock:
                for info_hash in targets:
                    if info_hash not in self._pending_owned_hashes:
                        self._pending_owned_hashes[info_hash] = (
                            time.time() + OWNED_PENDING_TTL_SECONDS
                        )
        for attempt in range(max(1, attempts)):
            now = time.time()
            with self._choice_lock:
                for expired in [
                    info_hash
                    for info_hash, deadline in self._pending_owned_hashes.items()
                    if deadline <= now
                ]:
                    self._pending_owned_hashes.pop(expired, None)
                pending = dict(self._pending_owned_hashes)
            if not pending:
                return
            try:
                tasks = self._list_offline_tasks()
            except Exception as err:  # noqa: BLE001 - 网络异常下一轮再试
                logger.warning(
                    f"【TG磁力离线】补记任务名称失败：{self._mask_secrets(str(err))}"
                )
                tasks = None
            names: List[str] = []
            if tasks:
                with self._choice_lock:
                    for task in tasks:
                        if not isinstance(task, dict):
                            continue
                        info_hash = str(task.get("info_hash") or "").lower()
                        if info_hash not in pending:
                            continue
                        key = self._name_key(str(task.get("name") or ""))
                        if not key:
                            continue
                        self._owned_names[key] = time.time() + OWNED_TTL_SECONDS
                        self._pending_owned_hashes.pop(info_hash, None)
                        names.append(str(task.get("name") or ""))
                if names:
                    self._persist_owned_names()
                    self._wake_residue_sweep("补记到离线任务名")
                    logger.info(
                        f"【TG磁力离线】已按离线任务补记来源：{len(names)} 个 · "
                        f"{names[0][:60]}"
                    )
            with self._choice_lock:
                finished = not self._pending_owned_hashes
            if finished:
                return
            if attempt + 1 < max(1, attempts):
                if self._stop_event.wait(OWNED_SYNC_INTERVAL_SECONDS):
                    return

    def _sync_owned_once(self) -> None:
        """
        巡检线程使用的单次补记：有待办时才请求，且不与后台同步并发。
        """
        with self._choice_lock:
            has_pending = bool(self._pending_owned_hashes)
        if not has_pending:
            return
        if not self._owned_sync_lock.acquire(blocking=False):
            return
        try:
            self._sync_owned_task_names(attempts=1)
        finally:
            self._owned_sync_lock.release()

    def _prune_owned_names_locked(self) -> None:
        """清理已过期的来源标记（调用方需持有 choice 锁）。"""
        now = time.time()
        for key in [k for k, expire in self._owned_names.items() if expire <= now]:
            self._owned_names.pop(key, None)

    def _persist_owned_names(self) -> None:
        """把来源标记写入插件数据，重启后仍能识别。"""
        try:
            with self._choice_lock:
                snapshot = dict(self._owned_names)
            self.save_data("owned_names", snapshot)
        except Exception as err:  # noqa: BLE001 - 持久化失败只记录
            logger.warning(f"【TG磁力离线】保存任务标记失败：{err}")

    def _persist_pending_transfer(self) -> None:
        """把待触发整理的登记表写入插件数据，重启后仍能接力。"""
        try:
            with self._choice_lock:
                snapshot = dict(self._pending_transfer)
            self.save_data("pending_transfer", snapshot)
        except Exception as err:  # noqa: BLE001 - 持久化失败只记录
            logger.warning(f"【TG磁力离线】保存待整理登记失败：{err}")

    def _persist_identifier_rules(self) -> None:
        """把临时识别词清单写入插件数据，重启后仍能到期清理。"""
        try:
            with self._choice_lock:
                snapshot = dict(self._identifier_rules)
            self.save_data("identifier_rules", snapshot)
        except Exception as err:  # noqa: BLE001 - 持久化失败只记录
            logger.warning(f"【TG磁力离线】保存识别词清单失败：{err}")

    def _remember_notified(self, key: str) -> None:
        """
        记录已通知的整理记录并持久化，避免插件重载后重复补发。

        :param key: 记录标识（目标路径或源路径）
        """
        with self._choice_lock:
            if key not in self._notified_histories:
                self._notified_histories.append(key)
            if len(self._notified_histories) > 200:
                # list 保序：保留最近 100 条，避免 set 随机裁剪把刚写入的键丢掉
                self._notified_histories = self._notified_histories[-100:]
            snapshot = list(self._notified_histories)
        try:
            self.save_data("notified_histories", snapshot)
        except Exception as err:  # noqa: BLE001 - 持久化失败不影响通知
            logger.warning(f"【TG磁力离线】保存通知去重表失败：{err}")

    def _is_owned_text(self, text: str) -> bool:
        """
        判断给定文本（路径或任务名）是否属于本插件提交的任务。

        :param text: 文件路径或任务名称
        :return: 是否匹配到本插件的来源标记
        """
        if not text:
            return False
        path_key = self._name_key(text)
        if not path_key:
            return False
        now = time.time()
        with self._choice_lock:
            keys = [
                key for key, expire in self._owned_names.items() if expire > now
            ]
        for key in keys:
            if key in path_key:
                return True
            # 115 落盘名常在中间插入广告词（如「最新电影www.xxx.com」），
            # 完整子串匹配会失败，用足够长的前缀再兜一次
            if len(key) >= 12 and key[:12] in path_key:
                return True
        return False

    def _is_owned_path(self, path: str) -> bool:
        """
        判断待整理路径是否来自本插件提交。

        配置了专属目录时按目录前缀判定：该目录下的一切都算本插件任务；
        没有专属目录时退回名称标记判定。

        :param path: 源文件路径
        :return: 是否属于本插件任务
        """
        if not path:
            return False
        target = self._effective_target_path()
        if target and str(path).startswith(target + "/"):
            return True
        return self._is_owned_text(path)

    def _is_scope_root(self, dir_key: str) -> bool:
        """
        判断目录是否就是比对范围的根本身。

        根目录下通常还混着别的任务，按目录重跑会波及无关文件，需要逐个文件处理。

        :param dir_key: 源目录
        :return: 是否为范围根
        """
        return dir_key.rstrip("/") in set(self._scope_roots())

    def _scope_roots(self) -> List[str]:
        """
        返回比对范围的根目录列表。

        :return: 以 / 开头、结尾无斜杠的路径列表
        """
        raw = self._precheck_scope or DEFAULT_PRECHECK_SCOPE
        roots: List[str] = []
        for item in re.split(r"[,，\n]", raw):
            # 与 _is_scope_root / _in_precheck_scope 的比较口径保持一致：
            # 补前导斜杠、合并重复斜杠、去掉尾斜杠，避免配置少写斜杠时判定整体失效
            value = self._normalize_path(item)
            if value and value not in roots:
                roots.append(value)
        return roots

    def _skip_once(self, path: str, minutes: int = 10) -> None:
        """
        标记该路径在若干分钟内跳过比对，供重整理时放行。

        :param path: 源文件路径
        :param minutes: 有效分钟数
        """
        with self._choice_lock:
            self._skip_precheck_until[path] = time.time() + minutes * 60

    @staticmethod
    def _run_awaitable(value: Any) -> Any:
        """
        兼容 LLMHelper.get_llm 的同步与异步两种返回形态。

        :param value: 可能是 awaitable 的返回值
        :return: 实际结果
        """
        if not inspect.isawaitable(value):
            return value
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(value)
        box: Dict[str, Any] = {}

        def worker() -> None:
            """在独立线程内执行协程。"""
            box["value"] = asyncio.run(value)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        thread.join(timeout=60)
        return box.get("value")

    def _invoke_llm_text(
        self, prompt: ChatPromptTemplate, payload: Dict[str, Any], timeout: int
    ) -> Optional[str]:
        """
        以纯文本方式调用 LLM，返回模型原始输出。

        :param prompt: 提示词模板
        :param payload: 模板变量
        :param timeout: 超时秒数
        :return: 模型输出文本；未配置 LLM 或调用失败时为 None
        """
        if LLMHelper is None:
            return None
        try:
            llm = self._run_awaitable(LLMHelper.get_llm(streaming=False))
            if llm is None:
                return None
            message = (prompt | llm).invoke(
                payload, config={"configurable": {"timeout": timeout}}
            )
            return str(getattr(message, "content", message) or "")
        except Exception as err:  # noqa: BLE001 - 调用失败由上层决定降级
            logger.warning(f"【TG磁力离线】LLM 调用失败：{err}")
            return None

    @staticmethod
    def _parse_json_object(text: str) -> Optional[Dict[str, Any]]:
        """
        从模型输出中解析 JSON 对象，容忍代码围栏与前后说明文字。

        :param text: 模型原始输出
        :return: 解析出的字典；无法解析时为 None
        """
        raw = str(text or "").strip()
        if not raw:
            return None
        fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
        if fenced:
            raw = fenced.group(1).strip()
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            raw = raw[start : end + 1]
        try:
            data = json.loads(raw)
        except Exception:  # noqa: BLE001 - 非 JSON 视为解析失败
            return None
        return data if isinstance(data, dict) else None

    def _notify_ai_unavailable(self, detail: str) -> None:
        """
        记录并（最多 6 小时一次）通知 AI 不可用，避免比对静默失效。

        :param detail: 失败说明
        """
        now = time.time()
        with self._choice_lock:
            if self._ai_error_notified and now - self._ai_error_notified < 21600:
                return
            self._ai_error_notified = now
        self._append_history("AI不可用", str(detail)[:100])
        self._send_notify(
            f"AI 识别不可用，预整理比对已跳过\n{str(detail)[:200]}"
        )

    @staticmethod
    def _ai_prompt() -> ChatPromptTemplate:
        """
        构造 AI 识别提示词。

        :return: 提示词模板
        """
        return ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "你是影视文件名识别助手。只依据给出的文件名与 MP 的解析提示推断，"
                    "不要臆造。输出标准化标题（去掉分辨率、制作组、音频编码、网盘标记等噪音）、"
                    "四位年份、媒体类型（movie、tv、unknown）、季号与集号（电影填 0）、"
                    "0 到 1 的置信度。不确定时把类型设为 unknown 并降低置信度。"
                    "只输出一个 JSON 对象，字段为 name、year、media_type、season、"
                    "episode、confidence、reason；不要输出解释、标题行或 Markdown 代码块。"
                ),
                (
                    "human",
                    "文件名：\n{title}\n\n完整路径：\n{path}\n\nMP 当前解析：\n{meta_hint}\n",
                ),
            ]
        )

    def _ai_recognize(self, file_path: str, mediainfo: Any) -> Optional[AiRecognition]:
        """
        调用本机 LLM 对文件名做一次结构化识别。

        :param file_path: 源文件路径
        :param mediainfo: MP 识别结果，作为提示输入
        :return: AI 识别结果；未配置 LLM 或调用失败时返回 None
        """
        if LLMHelper is None:
            return None
        cache_key = self._ai_cache_key(file_path, mediainfo)
        now = time.time()
        with self._choice_lock:
            cached = self._ai_cache.get(cache_key)
            if cached and now - cached[0] < 600:
                return cached[1]
        title = Path(file_path).name or file_path
        hint = json.dumps(self._mp_snapshot(mediainfo), ensure_ascii=False)
        # 不用 with_structured_output：部分 OpenAI 兼容端点不支持 json_schema，
        # 会退化成自然语言输出。这里自己解析 JSON，并容忍代码围栏与前后说明。
        text = self._invoke_llm_text(
            self._ai_prompt(),
            {"title": title, "path": file_path, "meta_hint": hint},
            20,
        )
        parsed: Optional[AiRecognition] = None
        data = self._parse_json_object(text or "")
        if data:
            try:
                # 模型常把年份返回成数字，这里统一归一化，避免 Pydantic 校验失败
                parsed = AiRecognition(
                    name=str(data.get("name") or ""),
                    year=str(data.get("year") or ""),
                    media_type=str(data.get("media_type") or "unknown"),
                    season=int(data.get("season") or 0),
                    episode=int(data.get("episode") or 0),
                    confidence=float(data.get("confidence") or 0),
                    reason=str(data.get("reason") or ""),
                )
            except (TypeError, ValueError) as err:
                logger.warning(f"【TG磁力离线】AI 返回字段不合法：{err}")
        if parsed is None:
            self._notify_ai_unavailable(f"模型返回无法解析：{(text or '')[:80]}")
        with self._choice_lock:
            # 失败也短暂缓存，避免同剧每集都重复等超时
            self._ai_cache[cache_key] = (now, parsed)
        return parsed

    @staticmethod
    def _ai_cache_key(file_path: str, mediainfo: Any) -> str:
        """
        构造 AI 识别缓存键：同目录、同标题、同季、同集视为同一条记录。

        集号必须进键：否则整季所有集会复用第一集的结果，逐集比对全部误判为
        「识别不一致」。

        :param file_path: 源文件路径
        :param mediainfo: MP 识别结果
        :return: 缓存键
        """
        snapshot = TgOfflineBot._mp_snapshot(mediainfo)
        return (
            f"{Path(file_path).parent.as_posix()}|{snapshot['title']}|"
            f"{snapshot['season']}|{snapshot['episode']}"
        )

    @staticmethod
    def _normalize_title(text: str) -> str:
        """
        归一化标题用于比较，忽略空白、标点与大小写差异。

        :param text: 原始标题
        :return: 归一化后的标题
        """
        value = re.sub(r"[\s\-_:：·,，.。'\"!！?？()（）\[\]【】]", "", str(text or ""))
        return value.lower()

    @staticmethod
    def _mp_title_candidates(mediainfo: Any) -> List[str]:
        """
        收集 MP 侧可用于比对的标题候选。

        TMDB 在中文环境下返回中文标题（如「忘却之岛」），而磁力文件名多是
        英文原名（如 `Forgotten Island`）；只比 `title` 会把同一部片判成不一致。
        这里把多语言标题与别名一并纳入候选，任一命中即视为同一部片。

        :param mediainfo: MP 的 MediaInfo 对象
        :return: 去重后的标题候选列表，保持原有顺序
        """
        raw_titles: List[str] = []
        for field in (
            "title",
            "en_title",
            "hk_title",
            "tw_title",
            "sg_title",
            "original_title",
            "original_name",
        ):
            value = str(getattr(mediainfo, field, "") or "").strip()
            if value:
                raw_titles.append(value)
        names = getattr(mediainfo, "names", None)
        if isinstance(names, (list, tuple)):
            for name in names:
                value = str(name or "").strip()
                if value:
                    raw_titles.append(value)
        candidates: List[str] = []
        for title in raw_titles:
            if title not in candidates:
                candidates.append(title)
        return candidates

    def _same_media(self, mediainfo: Any, ai: AiRecognition) -> bool:
        """
        判断 MP 与 AI 的识别结果是否指向同一部媒体。

        比较类型、多语言标题候选、年份与季号；任一项明确冲突即判为不一致。

        :param mediainfo: MP 识别结果
        :param ai: AI 识别结果
        :return: 是否一致
        """
        snapshot = self._mp_snapshot(mediainfo)
        ai_type = str(ai.media_type or "unknown").lower()
        mp_type = snapshot["type"]
        if mp_type in ("movie", "tv") and ai_type in ("movie", "tv") and mp_type != ai_type:
            return False
        mp_titles = {
            self._normalize_title(title)
            for title in self._mp_title_candidates(mediainfo)
        }
        ai_title = self._normalize_title(ai.name)
        if ai_title and ai_title not in mp_titles:
            return False
        if snapshot["year"] and ai.year and snapshot["year"] != str(ai.year):
            return False
        if ai_type == "tv" and ai.season:
            expected = f"第{int(ai.season)}季"
            if snapshot["season"] and snapshot["season"] != expected:
                return False
        return True

    def _notify_target(self) -> Optional[int]:
        """
        计算通知与会话消息的默认目标。

        优先使用配置的通知 chat，其次最近一次与 bot 交互的会话。

        :return: 会话 ID；都不可用时为 None
        """
        if self._notify_chat_id:
            try:
                return int(self._notify_chat_id)
            except ValueError:
                pass
        if self._last_chat_id:
            return self._last_chat_id
        for user_id in sorted(self._allowed_users):
            if user_id.isdigit():
                return int(user_id)
        return None

    def _ask_choice(
        self,
        source_path: str,
        snapshot: Dict[str, str],
        ai: AiRecognition,
        fileitem: Any = None,
    ) -> None:
        """
        记录待决策项并推送带按钮的对比消息。

        :param source_path: 源文件路径
        :param snapshot: MP 识别结果摘要
        :param ai: AI 识别结果
        :param fileitem: 待整理文件项，重新整理时直接复用，避免目录级重扫
        """
        dir_key = Path(source_path).parent.as_posix()
        chat_id = self._notify_target()
        with self._choice_lock:
            self._pending_choices[source_path] = {
                "path": source_path,
                "mp": snapshot,
                "ai": ai.model_dump(),
                "fileitem": fileitem,
                "created_at": time.time(),
                "chat_id": chat_id,
            }
            group = self._pending_groups.get(dir_key)
            if group is None:
                token = f"{int(time.time())}{abs(hash(dir_key)) % 10000:04d}"
                group = {
                    "token": token,
                    "dir": dir_key,
                    "chat_id": chat_id,
                    "message_id": None,
                    "paths": [],
                    "created_at": time.time(),
                }
                self._pending_groups[dir_key] = group
            else:
                token = str(group.get("token") or "")
            if source_path not in group["paths"]:
                group["paths"].append(source_path)
            text = self._render_pending_text(dir_key)
            message_id = group.get("message_id")
            retry_at = float(group.get("retry_at") or 0.0)
            timeout_started = bool(group.get("timeout_started"))
        now = time.time()
        if message_id is None:
            # 首条消息没发出去（网络抖动、Telegram 超时）时不能就此放弃：
            # 否则后续文件只会去编辑一个不存在的消息，用户永远看不到按钮。
            if now < retry_at:
                return
            sent_id = self._send_text(chat_id, text, keyboard=self._choice_keyboard(token))
            with self._choice_lock:
                current = self._pending_groups.get(dir_key)
                if current and current.get("token") == token:
                    if sent_id is None:
                        current["retry_at"] = now + CHOICE_RESEND_INTERVAL
                    else:
                        current["message_id"] = sent_id
                        current["timeout_started"] = True
            if sent_id is not None and not timeout_started and self._precheck_timeout > 0:
                threading.Thread(
                    target=self._choice_timeout_worker,
                    args=(dir_key, token, self._precheck_timeout * 60),
                    name="TgOfflineBot-ChoiceTimeout",
                    daemon=True,
                ).start()
            return
        self._edit_text(chat_id, message_id, text)

    def _choice_keyboard(self, token: str) -> Dict[str, Any]:
        """
        构造「按 MP / 按 AI 整理」的内联键盘。

        :param token: 待决策组的令牌
        :return: inline keyboard 结构
        """
        return {
            "inline_keyboard": [
                [
                    {"text": "按 MP 识别整理", "callback_data": f"tgob:mp:{token}"},
                    {"text": "按 AI 识别整理", "callback_data": f"tgob:ai:{token}"},
                ]
            ]
        }

    def _render_pending_text(self, dir_key: str) -> str:
        """
        渲染一个待决策组的对比文本。

        :param dir_key: 源目录
        :return: 消息正文
        """
        with self._choice_lock:
            group = self._pending_groups.get(dir_key) or {}
            paths = list(group.get("paths") or [])
            items = [(path, self._pending_choices.get(path) or {}) for path in paths]
        lines = [
            f"整理前识别不一致（{len(items)} 个文件），请选择这批按哪个结果整理：",
            "",
        ]
        for path, item in items[:8]:
            mp = item.get("mp") or {}
            ai = item.get("ai") or {}
            lines.append(Path(path).name)
            lines.append(
                f"  MP：{mp.get('title')}（{mp.get('year') or '无年份'}，{mp.get('type')}）"
                f"{mp.get('episode') or ''}"
            )
            lines.append(
                f"  AI：{ai.get('name')}（{ai.get('year') or '无年份'}，"
                f"{ai.get('media_type')}）"
            )
        if len(items) > 8:
            lines.append(f"……另有 {len(items) - 8} 个文件")
        lines.append("")
        lines.append(f"超时 {self._precheck_timeout} 分钟将按 MP 结果继续。")
        lines.append("群内任何成员都可以点击下面的按钮。")
        return "\n".join(lines)

    def _choice_timeout_worker(self, dir_key: str, token: str, seconds: int) -> None:
        """
        等待人工选择；超时后按 MP 结果继续整理该目录下的整批文件。

        :param dir_key: 源目录
        :param token: 待决策组令牌
        :param seconds: 等待秒数
        """
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self._stop_event.is_set():
                return
            with self._choice_lock:
                group = self._pending_groups.get(dir_key)
            if not group or group.get("token") != token:
                return
            time.sleep(min(5.0, max(0.0, deadline - time.monotonic())))
        with self._choice_lock:
            group = self._pending_groups.get(dir_key)
            if not group or group.get("token") != token:
                # 这批已被用户选择，或已被同目录的新一批替换：不能按旧批次收尾
                return
            self._pending_groups.pop(dir_key, None)
            paths = list((group or {}).get("paths") or [])
            items = {path: self._pending_choices.pop(path, {}) for path in paths}
        if not paths:
            return
        logger.info(
            f"【TG磁力离线】识别选择超时，按 MP 结果继续：{dir_key}（{len(paths)} 个文件）"
        )
        for path in paths:
            self._skip_once(path)
        ok = False
        detail = ""
        if not self._is_scope_root(dir_key):
            ok, detail = self._trigger_manual_transfer(dir_key)
        if not ok:
            for path in paths:
                single_ok, single_detail = self._retry_transfer(
                    path, (items.get(path) or {}).get("fileitem")
                )
                ok = ok or single_ok
                if not single_ok:
                    detail = single_detail
        status = "已按 MP 结果继续整理" if ok else f"继续整理失败：{detail}"
        self._send_notify(f"{status}\n{Path(dir_key).name}（{len(paths)} 个文件）")

    def _handle_choice_callback(self, call: Dict[str, Any]) -> None:
        """
        处理「按 MP / 按 AI 整理」按钮回调。

        :param call: Telegram callback_query 对象
        """
        data = str(call.get("data") or "")
        user_id = str((call.get("from") or {}).get("id") or "")
        message = call.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        chat_type = str(chat.get("type") or "")
        message_id = message.get("message_id")
        self._answer_callback(call.get("id"))
        # 群里任何成员都可以选择；私聊仍要求白名单
        if chat_type not in ("group", "supergroup") and user_id not in self._allowed_users:
            self._edit_text(chat_id, message_id, "仅白名单用户可操作。")
            return
        if (
            chat_type in ("group", "supergroup")
            and str(chat_id or "") not in self._allowed_users
            and user_id not in self._allowed_users
        ):
            # 未授权群里的按钮不应生效，避免 token 外泄后被他人操作
            self._edit_text(chat_id, message_id, "该群未授权，无法操作。")
            return
        parts = data.split(":")
        if len(parts) != 3 or parts[0] != "tgob" or parts[1] not in ("mp", "ai"):
            return
        choice, token = parts[1], parts[2]
        with self._choice_lock:
            group = next(
                (
                    item
                    for item in self._pending_groups.values()
                    if item.get("token") == token
                ),
                None,
            )
        if not group:
            self._edit_text(chat_id, message_id, "该选项已过期或已处理。")
            return
        result_text = self._apply_choice_group(str(group.get("dir") or ""), choice == "ai")
        self._edit_text(chat_id, message_id, result_text)

    def _apply_choice_group(self, dir_key: str, use_ai: bool) -> str:
        """
        按用户选择重新整理整批文件；选择 AI 时写入多条限定文件名的临时识别词。

        :param dir_key: 源目录
        :param use_ai: 是否按 AI 结果整理
        :return: 结果说明文本
        """
        with self._choice_lock:
            group = self._pending_groups.pop(dir_key, None)
            paths = list((group or {}).get("paths") or [])
            items = {path: self._pending_choices.pop(path, {}) for path in paths}
        if not paths:
            return "该选项已过期或已处理。"
        for path in paths:
            self._skip_once(path)
        note = ""
        if use_ai:
            rules: List[str] = []
            for path in paths:
                rule = self._make_identifier_rule(
                    path, (items.get(path) or {}).get("ai") or {}
                )
                if rule:
                    rules.append(rule)
            added = self._append_identifiers(rules)
            if added:
                with self._choice_lock:
                    for rule in added:
                        self._identifier_rules[rule] = time.time()
                note = f"\n已写入 {len(added)} 条临时识别词"
                self._persist_identifier_rules()
        ok = False
        detail = ""
        if not self._is_scope_root(dir_key):
            ok, detail = self._trigger_manual_transfer(dir_key)
        if not ok:
            for path in paths:
                single_ok, single_detail = self._retry_transfer(
                    path, (items.get(path) or {}).get("fileitem")
                )
                ok = ok or single_ok
                if not single_ok:
                    detail = single_detail
        self._append_history(
            "按AI整理" if use_ai else "按MP整理",
            f"{dir_key}（{len(paths)} 个文件）",
        )
        status = "已重新触发整理" if ok else f"重新整理失败：{detail}"
        return f"{status}（{'AI' if use_ai else 'MP'} 结果，{len(paths)} 个文件）{note}"

    def _retry_transfer(self, source_path: str, fileitem: Any) -> Tuple[bool, str]:
        """
        重新触发单个文件的整理。

        优先用 MP 整理链按文件重跑：只处理该文件，不遍历目录，不会因为
        P115StrmHelper 的目录级手动整理而产生额外的 115 枚举请求。
        只在拿不到文件项时退回按父目录手动整理。

        :param source_path: 源文件路径
        :param fileitem: 拦截时保存的文件项
        :return: (是否成功, 结果说明)
        """
        if fileitem is not None:
            try:
                from app.chain.transfer import TransferChain

                ok, message = TransferChain().do_transfer(
                    fileitem=fileitem, background=True
                )
                if ok:
                    return True, "按文件重新整理"
                logger.warning(f"【TG磁力离线】按文件重跑失败：{message}")
            except Exception as err:  # noqa: BLE001 - 失败后回退目录重跑
                logger.warning(f"【TG磁力离线】按文件重跑异常：{err}")
        parent = Path(source_path).parent.as_posix()
        if self._is_scope_root(parent):
            # 范围根（如 /待整理）下混着多个来源的任务，整目录重跑会把别人一起推进队列
            return False, "父目录是待整理范围根，拒绝整目录重跑"
        if self._is_target_root(parent):
            # 目标目录根下可能混着多个任务：整目录重跑会把别人一起推进整理队列
            children = self._list_target_children()
            if children is None or len(children) != 1:
                return False, "单个文件重跑失败，且目标目录里还有其他任务，未做整目录重跑"
        ok, detail = self._trigger_manual_transfer(parent)
        return ok, detail if not ok else f"已按目录重跑（{parent}）"

    def _make_identifier_rule(
        self, source_path: str, ai: Dict[str, Any]
    ) -> Optional[str]:
        """
        生成一条限定该文件名的识别词，把识别结果强制到 AI 指定的媒体。

        :param source_path: 源文件路径
        :param ai: AI 识别结果字典
        :return: 识别词规则；无法生成时为 None
        """
        name = str(ai.get("name") or "").strip()
        if not name:
            return None
        media_type = "tv" if str(ai.get("media_type")) == "tv" else "movie"
        identity = self._resolve_ai_media_id(name, str(ai.get("year") or ""), media_type)
        anchor = re.escape(Path(source_path).stem)
        if identity:
            media_id, source = identity
            # media_source 可能是枚举，取 value 后再映射，避免生成 "MediaSource.TMDBid"
            raw_source = str(getattr(source, "value", source))
            id_field = MEDIA_SOURCE_ID_FIELDS.get(raw_source.lower()) or f"{raw_source}id"
            spec = f"{id_field}={media_id};type={media_type}"
            # 季集以文件名为准：AI 结果可能来自同剧首集的缓存，直接沿用会串集
            season = int(ai.get("season") or 0)
            episode = int(ai.get("episode") or 0)
            try:
                meta = MetaInfo(Path(source_path).stem)
                season = meta.begin_season or season
                episode = meta.begin_episode or episode
            except Exception:  # noqa: BLE001 - 解析失败时退回 AI 结果
                pass
            if season:
                spec += f";s={season}"
            if episode:
                spec += f";e={episode}"
            return f"{anchor} => {{[{spec}]}}"
        replacement = f"{name} {str(ai.get('year') or '')}".strip()
        return f"{anchor} => {replacement}"

    def _resolve_ai_media_id(
        self, name: str, year: str, media_type: str
    ) -> Optional[Tuple[str, str]]:
        """
        用 MP 识别引擎把 AI 标题解析成来源原生媒体 ID。

        :param name: AI 给出的标题
        :param year: 年份
        :param media_type: movie 或 tv
        :return: (媒体 ID, 数据源)；失败时为 None
        """
        if MediaChain is None:
            return None
        try:
            meta = MetaInfo(name)
            meta.name = name
            meta.year = year or None
            media = MediaChain().recognize_by_meta(meta)
            if not media:
                return None
            media_id = str(getattr(media, "media_id", "") or "")
            source = str(getattr(media, "media_source", "") or "")
            if not media_id or not source:
                return None
            return media_id, source
        except Exception as err:  # noqa: BLE001 - 二次识别失败时退化为标题替换
            logger.warning(f"【TG磁力离线】AI 结果二次识别失败：{err}")
            return None

    def _append_identifiers(self, rules: List[str]) -> List[str]:
        """
        把识别词追加到 MP 自定义识别词列表。

        :param rules: 待追加的规则
        :return: 实际新增的规则
        """
        if SystemConfigOper is None or not rules:
            return []
        try:
            oper = SystemConfigOper()
            existing = list(oper.get(SystemConfigKey.CustomIdentifiers) or [])
            added = [rule for rule in rules if rule and rule not in existing]
            if added:
                oper.set(SystemConfigKey.CustomIdentifiers, existing + added)
            return added
        except Exception as err:  # noqa: BLE001 - 写识别词失败时仅记录
            logger.error(f"【TG磁力离线】写入识别词失败：{err}")
            return []

    def _cleanup_identifiers(self) -> None:
        """
        清理本插件写入且已超过存活时间的临时识别词。
        """
        if SystemConfigOper is None:
            return
        now = time.time()
        with self._choice_lock:
            expired = [
                rule
                for rule, created in self._identifier_rules.items()
                if now - created >= IDENTIFIER_TTL_SECONDS
            ]
        if not expired:
            return
        try:
            oper = SystemConfigOper()
            existing = list(oper.get(SystemConfigKey.CustomIdentifiers) or [])
            remaining = [item for item in existing if item not in expired]
            if len(remaining) != len(existing):
                oper.set(SystemConfigKey.CustomIdentifiers, remaining)
            with self._choice_lock:
                for rule in expired:
                    self._identifier_rules.pop(rule, None)
            logger.info(f"【TG磁力离线】已清理临时识别词：{expired}")
            self._persist_identifier_rules()
        except Exception as err:  # noqa: BLE001 - 清理失败下次再试
            logger.error(f"【TG磁力离线】清理识别词失败：{err}")

    def _organize_completed_tasks(self) -> None:
        """
        对下载完成的本插件任务主动触发一次网盘整理。

        指定了目标目录时，P115StrmHelper 只负责下载、不登记整理队列，也不产生
        生活事件，因此完成与否只能由本插件按离线任务列表判断。

        多文件任务在目标目录下有独立子目录，直接整理该子目录；
        单文件任务直接落在目标目录根下，只有根目录里没有其它项目时才整理根目录，
        避免把别的任务一起推进整理队列。
        """
        target = self._effective_target_path()
        if not target:
            return
        now = time.time()
        with self._choice_lock:
            pending = {
                info_hash: deadline
                for info_hash, deadline in self._pending_transfer.items()
                if deadline > now
            }
            pruned = len(pending) != len(self._pending_transfer)
            if pruned:
                self._pending_transfer = dict(pending)
        if pruned:
            self._persist_pending_transfer()
        if not pending:
            return
        tasks = self._list_offline_tasks()
        if tasks is None:
            # 接口暂时不可用：保留登记，下一轮再试
            return
        # 队列语义：按提交（登记）顺序排队，最早提交、最早下完的先整理；
        # 115 列表通常把最新任务排在前面，直接遍历会变成后提交的先整理
        order = {info_hash: index for index, info_hash in enumerate(pending)}
        ordered_tasks = sorted(
            (
                task
                for task in tasks
                if isinstance(task, dict)
                and str(task.get("info_hash") or "").lower() in order
            ),
            key=lambda task: order[str(task.get("info_hash") or "").lower()],
        )
        children: Optional[List[Any]] = None
        dispatched_this_round = False
        for task in ordered_tasks:
            if not isinstance(task, dict):
                continue
            info_hash = str(task.get("info_hash") or "").lower()
            if info_hash not in pending:
                continue
            try:
                status = int(task.get("status", -1))
            except (TypeError, ValueError):
                continue
            if status != 2:
                # 只处理已完成的任务，下载中/失败/重试中留给下一轮
                continue
            name = str(task.get("name") or "")
            if not name:
                continue
            if children is None:
                children = self._list_target_children()
                if children is None:
                    self._flush_organize_notices()
                    return
            item = self._match_task_item(children, name)
            path = str(getattr(item, "path", "") or "") if item is not None else ""
            if path and str(getattr(item, "type", "") or "") == "dir":
                organize_path = path
            elif path and len(children) == 1:
                # 单文件且目标目录里只有它：整理根目录等价于只整理这个任务
                organize_path = target
            elif item is None:
                # 任务已完成但目标目录里找不到对应项：多半已被残留清理或整理移除，
                # 与「单文件与其它任务混放」不是一回事，不能发手动整理提醒；
                # 超过宽限期就注销登记，避免每轮空转请求 115
                if self._pending_finished_without_target(info_hash):
                    logger.info(
                        f"【TG磁力离线】任务已完成但目标目录无对应项，注销登记：{name}"
                    )
                else:
                    logger.info(
                        f"【TG磁力离线】任务已完成但目标目录暂无对应项，等落盘：{name}"
                    )
                continue
            else:
                self._defer_organize_notice(info_hash, name)
                continue
            if dispatched_this_round:
                # 队列语义：一轮只整理一个任务，其余排队等这批整理收尾后再来
                continue
            ok, detail = self._trigger_manual_transfer(organize_path)
            if not ok:
                logger.warning(
                    f"【TG磁力离线】触发整理失败，下一轮重试：{organize_path} - {detail}"
                )
                continue
            dispatched_this_round = True
            with self._choice_lock:
                self._pending_transfer.pop(info_hash, None)
                self._organize_seen.pop(info_hash, None)
                # MP 搬文件是异步的：触发整理后这段时间内目录里仍是正片，
                # 记下触发时间，让残留巡检先跳过该目录
                self._organize_dispatched[organize_path.rstrip("/")] = time.time()
                self._prune_organize_dispatched_locked(time.time())
            self._persist_pending_transfer()
            self._append_history("整理", Path(organize_path).name)
            logger.info(f"【TG磁力离线】已触发网盘整理：{organize_path}")
            self._wake_residue_sweep("已触发网盘整理")
        # 任务整条从离线列表消失（被清理或 115 自行删除）时同样注销登记，
        # 否则要等 6 小时 TTL 才失效，期间每轮都在空转
        present = {
            str(task.get("info_hash") or "").lower()
            for task in tasks
            if isinstance(task, dict)
        }
        for info_hash in [item for item in pending if item not in present]:
            if self._pending_finished_without_target(info_hash):
                logger.info(
                    f"【TG磁力离线】任务已不在离线列表，注销待整理登记：{info_hash[:8]}"
                )
        self._flush_organize_notices()

    def _pending_finished_without_target(self, info_hash: str) -> bool:
        """
        判断「任务已完成但目标目录找不到对应项」是否已超过宽限期。

        115 落盘到列表可见有几秒延迟；超过宽限期的说明文件已被整理或删除，
        继续保留登记只会让巡检每轮空转请求 115。

        :param info_hash: 任务 info_hash
        :return: 是否需要注销登记
        """
        now = time.time()
        changed = False
        with self._choice_lock:
            first = self._organize_seen.get(info_hash)
            if first is None:
                self._organize_seen[info_hash] = now
                return False
            if now - first < ORGANIZE_GIVEUP_SECONDS:
                return False
            self._organize_seen.pop(info_hash, None)
            if self._pending_transfer.pop(info_hash, None) is not None:
                changed = True
        if changed:
            self._persist_pending_transfer()
        return True

    def _list_target_children(self) -> Optional[List[Any]]:
        """
        列出目标目录的直接子项。

        :return: 子项列表；目录不可达时为 None
        """
        target = self._effective_target_path()
        if not target:
            return None
        dir_item = self._get_existing_dir(target)
        if not dir_item:
            return None
        try:
            from app.chain.storage import StorageChain

            return StorageChain().list_files(dir_item) or []
        except Exception as err:  # noqa: BLE001 - 列举失败按不可达处理
            logger.warning(f"【TG磁力离线】列举目标目录失败：{target} - {err}")
            return None

    def _match_task_item(self, children: List[Any], task_name: str) -> Any:
        """
        在目标目录的直系子项里找与离线任务对应的那一项。

        多文件任务会生成同名子目录；单文件任务的落盘名等于任务名加扩展名，
        因此额外用去扩展名的名称比对。

        :param children: 目标目录的直接子项
        :param task_name: 115 离线任务名
        :return: 匹配到的子项；找不到时为 None
        """
        key = self._name_key(task_name)
        if not key:
            return None
        fuzzy: Any = None
        for child in children:
            name = str(getattr(child, "name", "") or "")
            if not name:
                continue
            keys = (self._name_key(name), self._name_key(Path(name).stem))
            if key in keys:
                return child
            if fuzzy is None and len(key) >= 8:
                if any(item and (key.startswith(item) or item.startswith(key)) for item in keys):
                    fuzzy = child
        return fuzzy

    def _defer_organize_notice(self, info_hash: str, name: str) -> None:
        """
        把需要手动整理的任务登记到本轮汇总。

        同一任务在登记有效期内只汇总一次，避免每轮巡检重复发消息。

        :param info_hash: 任务 info_hash
        :param name: 任务名
        """
        marker = f"organize:{info_hash}"
        now = time.time()
        with self._choice_lock:
            last = self._organize_notified.get(marker)
            if last and now - last < PENDING_TRANSFER_TTL_SECONDS:
                return
            self._organize_notified[marker] = now
            self._organize_notices.append(name)

    def _flush_organize_notices(self) -> None:
        """把本轮汇总的手动整理提醒合并成一条消息发出。"""
        with self._choice_lock:
            names = list(self._organize_notices)
            self._organize_notices = []
        if not names:
            return
        shown = "\n".join(f"- {name}" for name in names[:10])
        if len(names) > 10:
            shown += f"\n- …另有 {len(names) - 10} 个"
        self._send_notify(
            f"以下 {len(names)} 个任务与他人混在同一目录，未自动整理：\n{shown}\n"
            f"请手动整理：{self._effective_target_path()}"
        )

    def _get_item_by_path(self, path: str) -> Any:
        """
        按路径查询已存在的文件或目录项（不会创建目录）。

        :param path: 存储内路径
        :return: 匹配到的项；不存在或无法确认时为 None
        """
        try:
            from app.chain.storage import StorageChain

            chain = StorageChain()
            for storage in self._storage_candidates():
                item = chain.get_file_item(storage, Path(path))
                if item:
                    self._note_storage_fallback(storage)
                    return item
            return None
        except Exception as err:  # noqa: BLE001 - 查询失败按无法确认处理
            logger.warning(f"【TG磁力离线】查询路径失败：{path} - {err}")
            return None

    def _get_existing_dir(self, path: str) -> Any:
        """
        只读地取目录项，不存在的目录不会被创建。

        宿主 get_folder 会按需创建目录：巡检、空目录清理、残留扫描这类读路径
        用它，会让刚被删掉的空目录反复被建回来，还会在删除前先造一个空壳。
        因此读路径统一走查询接口，只有提交前的 _ensure_target_dir 才创建目录。

        :param path: 目录路径
        :return: 目录项；不存在或不是目录时为 None
        """
        item = self._get_item_by_path(path)
        if item is None:
            return None
        if str(getattr(item, "type", "") or "") != "dir":
            return None
        return item

    def _parent_if_file(self, path: str) -> str:
        """
        判断路径是否为文件，是则返回其父目录。

        手动整理接口只能按目录调用，传文件路径会在 115 上建出同名空目录，
        所以调用前必须用真实类型确认，而不是靠扩展名猜。

        :param path: 待判断路径
        :return: 文件所在目录；路径是目录或无法确认时为空字符串
        """
        item = self._get_item_by_path(path)
        if item is None:
            return ""
        if str(getattr(item, "type", "") or "") != "file":
            return ""
        return Path(path).parent.as_posix()

    def _trigger_manual_transfer(self, path: str) -> Tuple[bool, str]:
        """
        调用 P115StrmHelper 的手动整理接口，重新整理指定网盘路径。

        :param path: 115 网盘路径
        :return: (是否成功, 结果说明)
        """
        token = self._api_token_value()
        if not token:
            return False, "缺少 MoviePilot API Token"
        # 该接口按目录处理：传文件路径会被当成目录，并在 115 上创建同名空目录。
        # 是否有扩展名不能用来判断（场景目录名里也常带点），改为向存储层确认真实类型。
        clean_path = str(path or "").strip().rstrip("/")
        if not clean_path:
            return False, "路径为空"
        parent = self._parent_if_file(clean_path)
        if parent:
            clean_path = parent
        url = (
            f"http://127.0.0.1:{self._host_port}/api/v1/plugin/"
            f"{STRM_HELPER_PLUGIN}/manual_transfer"
        )
        try:
            response = requests.post(
                url,
                json={"path": clean_path},
                headers={"X-API-KEY": token, "Content-Type": "application/json"},
                timeout=60,
                proxies={"http": None, "https": None},
            )
            data = response.json()
        except Exception as err:  # noqa: BLE001 - 重跑失败按回执反馈
            return False, f"调用整理接口异常：{err}"
        if response.status_code != 200 or data.get("code", 0) != 0:
            return False, str(data.get("msg") or f"HTTP {response.status_code}")
        return True, "ok"

    # ============================ 离线任务超时清理 ============================

    def _offline_watch_loop(self, generation: int) -> None:
        """定期巡检离线任务、清理过期识别词，并扫描待整理目录的残留。"""
        last_sweep = 0.0
        while not self._stop_event.is_set() and generation == self._generation:
            try:
                # 即使整理失败、没有触发完成事件，也要让识别词到期被移除
                self._cleanup_identifiers()
                # 提交后短窗口内按 info_hash 补记离线任务名，兜底后台同步线程
                self._sync_owned_once()
                self._cleanup_stuck_offline()
                # 清理残留后可能因 115 列表延迟漏删空目录，这里每轮重试
                self._retry_dir_removals()
                # 指定目录提交的任务不会被 P115StrmHelper 收进整理队列，
                # 下载完成后由这里主动触发整理
                self._organize_completed_tasks()
                with self._choice_lock:
                    has_owned = any(
                        expire > time.time()
                        for expire in self._owned_names.values()
                    )
                if has_owned:
                    # 115 网盘整理不一定发 TransferComplete，靠历史巡检补通知
                    if self._sweep_recent_histories():
                        # 有新入库记录才扫待整理目录，空闲时不再定时扫 115
                        self._sweep_pending_residue()
                now = time.time()
                if (
                    self._residue_sweep_interval > 0
                    and now - last_sweep >= self._residue_sweep_interval
                    and self._residue_sweep_allowed(now)
                ):
                    if self._sweep_pending_residue():
                        last_sweep = now
                    else:
                        # 本轮被跳过（队列未清空/目录不可用），60 秒后再试；
                        # 不要把「没扫」记成「已扫」，否则要再等一个完整周期
                        last_sweep = now - self._residue_sweep_interval + 60
            except Exception as err:  # noqa: BLE001 - 巡检失败下一轮重试
                logger.error(
                    f"【TG磁力离线】离线任务巡检失败：{self._mask_secrets(str(err))}"
                )
            if self._stop_event.wait(60):
                return

    def _list_offline_tasks(self) -> Optional[List[Dict[str, Any]]]:
        """
        读取 P115StrmHelper 的离线任务列表。

        :return: 任务字典列表；接口不可用时为 None（与「没有任务」区分开）
        """
        token = self._api_token_value()
        if not token:
            return None
        tasks: List[Dict[str, Any]] = []
        # 分页拉取，避免任务超过一页时漏掉卡住的条目
        for page in range(1, 6):
            batch = self._request_offline_tasks(token, page, 100)
            if batch is None:
                return None
            tasks.extend(batch)
            if len(batch) < 100:
                break
        return tasks

    def _request_offline_tasks(
        self, token: str, page: int, limit: int
    ) -> Optional[List[Dict[str, Any]]]:
        """
        拉取一页离线任务。

        :param token: 本机 API Token
        :param page: 页码
        :param limit: 每页条数
        :return: 任务列表；接口不可用时为 None
        """
        url = (
            f"http://127.0.0.1:{self._host_port}/api/v1/plugin/"
            f"{STRM_HELPER_PLUGIN}/offline_tasks"
        )
        response = requests.post(
            url,
            json={"page": page, "limit": limit},
            headers={"X-API-KEY": token, "Content-Type": "application/json"},
            timeout=90,
            proxies={"http": None, "https": None},
        )
        if response.status_code != 200:
            return None
        try:
            data = response.json()
        except Exception:  # noqa: BLE001 - 非 JSON 视为接口不可用
            return None
        if data.get("code", 0) != 0:
            return None
        payload = data.get("data") or {}
        tasks = payload.get("tasks") or []
        return [item for item in tasks if isinstance(item, dict)]

    def _cleanup_stuck_offline(self) -> None:
        """
        删除卡住或失败的离线任务并通知。

        处理三类：进度 0%、有进度但长时间未完成、115 侧已标记失败或重试中。
        判定时长统一从任务创建时间算起；只处理本插件提交之后出现的任务，
        单轮最多删除 10 个，避免误伤历史任务。

        只要还有本插件未完成的任务就持续巡检，不再受「提交后 1 小时」限制。
        """
        expired = not self._offline_active_until or time.time() > self._offline_active_until
        now_ts = time.time()
        with self._choice_lock:
            has_pending = bool(self._pending_transfer)
            has_owned = any(expire > now_ts for expire in self._owned_names.values())
        if expired and not has_pending and not has_owned:
            # 没有待整理登记、也没有未过期的来源标记时才停：
            # 未配置目标目录时本就没有 pending，不能据此判定「没有在途任务」
            return
        tasks = self._list_offline_tasks()
        if tasks is None:
            # 接口暂时不可用：保留在途标记，下一轮再试，不能当成「没有任务」
            return
        now = time.time()
        with self._choice_lock:
            pending = dict(self._pending_transfer)
        unfinished: List[str] = []
        for task in tasks:
            try:
                status = int(task.get("status", -1))
            except (TypeError, ValueError):
                continue
            info_hash = str(task.get("info_hash") or "").lower()
            if status in (0, 1, 3) and (
                info_hash in pending
                or self._is_owned_text(str(task.get("name") or ""))
            ):
                unfinished.append(info_hash)
        # 还有未完成任务：续期登记并把巡检窗口往后推，避免长时间下载被漏掉
        if unfinished:
            with self._choice_lock:
                for info_hash in unfinished:
                    if info_hash in self._pending_transfer:
                        self._pending_transfer[info_hash] = (
                            now + PENDING_TRANSFER_TTL_SECONDS
                        )
            self._offline_active_until = now + 3600
        else:
            self._offline_active_until = 0.0
        zero_threshold = self._offline_timeout * 60
        progress_threshold = self._offline_progress_timeout * 60
        failed_threshold = self._offline_failed_timeout * 60
        candidates: List[Tuple[Dict[str, Any], str]] = []
        for task in tasks:
            try:
                status = int(task.get("status", -1))
                percent = float(task.get("percent") or 0)
                add_time = float(task.get("add_time") or 0)
            except (TypeError, ValueError):
                continue
            if not add_time:
                continue
            # 只清理本插件提交的任务，其它渠道的离线任务一律不动
            if not self._is_owned_text(str(task.get("name") or "")):
                continue
            age = now - add_time
            if status in (1, 3):
                # 115 侧已失败或卡在重试：超过阈值仍未恢复就清理
                if self._offline_failed_timeout <= 0 or age < failed_threshold:
                    continue
                if status == 1:
                    reason = f"下载失败超过 {self._offline_failed_timeout} 分钟未恢复"
                else:
                    reason = f"重试中超过 {self._offline_failed_timeout} 分钟未完成"
            elif status == 0:
                if percent <= 0:
                    # 完全没开始的卡死任务
                    if self._offline_timeout <= 0 or age < zero_threshold:
                        continue
                    reason = f"0% 超过 {self._offline_timeout} 分钟"
                else:
                    # 有进度但长时间没下完
                    if (
                        self._offline_progress_timeout <= 0
                        or age < progress_threshold
                    ):
                        continue
                    reason = (
                        f"{percent:.0f}% 超过 {self._offline_progress_timeout} 分钟未完成"
                    )
            else:
                continue
            # 只清理本插件提交之后出现的任务，历史遗留不动
            if self._last_submit_ts and add_time < self._last_submit_ts - 600:
                continue
            candidates.append((task, reason))
        removed: List[Tuple[str, str]] = []
        pending_changed = False
        for task, reason in candidates[:10]:
            info_hash = str(task.get("info_hash") or "")
            name = str(task.get("name") or info_hash)
            if not info_hash or self._recently_deleted(info_hash):
                # 115 列表有延迟：同一任务可能连续两轮都返回，避免重复删除与重复通知
                continue
            if not self._delete_offline_task(info_hash):
                self._notify_delete_failure(info_hash, name)
                continue
            with self._choice_lock:
                self._offline_deleted_notified[info_hash.lower()] = time.time()
                if self._pending_transfer.pop(info_hash.lower(), None) is not None:
                    pending_changed = True
                self._organize_seen.pop(info_hash.lower(), None)
            logger.info(f"【TG磁力离线】已删除异常的离线任务：{name}（{reason}）")
            removed.append((name, reason))
        if pending_changed:
            self._persist_pending_transfer()
        if removed:
            # 一轮里删掉多个任务时只发一条汇总，避免刷屏
            shown = "\n".join(f"- {name}\n  {reason}" for name, reason in removed[:10])
            if len(removed) > 10:
                shown += f"\n- …另有 {len(removed) - 10} 个"
            self._send_notify(f"已删除 {len(removed)} 个异常的离线任务：\n{shown}")

    def _recently_deleted(self, info_hash: str) -> bool:
        """
        判断该任务是否在去重窗口内已经删除过。

        115 的任务列表有延迟，删除成功后下一轮仍可能返回同一条；
        没有去重就会重复删除、重复通知。

        :param info_hash: 任务哈希
        :return: 窗口内已删除过返回 True
        """
        now = time.time()
        with self._choice_lock:
            last = self._offline_deleted_notified.get(info_hash.lower())
            return bool(last and now - last < OFFLINE_DELETE_DEDUPE_SECONDS)

    def _notify_delete_failure(self, key: str, name: str) -> None:
        """
        删除离线任务失败时通知一次，同一任务 10 分钟内不重复。

        :param key: 任务哈希
        :param name: 任务名称
        """
        now = time.time()
        with self._choice_lock:
            last = self._offline_delete_notified.get(key)
            if last and now - last < 600:
                return
            self._offline_delete_notified[key] = now
        self._send_notify(
            f"离线任务删除失败\n{name}\n请检查 115 登录状态或稍后重试"
        )
    def _delete_offline_task(self, info_hash: str) -> bool:
        """
        调用 115 客户端删除指定离线任务。

        :param info_hash: 任务哈希
        :return: 是否删除成功
        """
        if not info_hash:
            return False
        client = self._p115_client()
        if client is None:
            logger.warning("【TG磁力离线】无法获取 115 客户端，跳过删除离线任务")
            return False
        try:
            result = client.clouddownload_task_del([info_hash])
            if isinstance(result, dict) and result.get("state") is False:
                logger.warning(f"【TG磁力离线】删除离线任务返回失败：{result}")
                return False
            return True
        except Exception as err:  # noqa: BLE001 - 删除失败只记录，不重试
            logger.error(f"【TG磁力离线】删除离线任务异常：{err}")
            return False

    @staticmethod
    def _p115_client() -> Any:
        """
        取 P115StrmHelper 的 115 客户端实例。

        :return: P115Client 实例；插件未安装或未初始化时为 None
        """
        try:
            from app.plugins.p115strmhelper.service import servicer

            return getattr(servicer, "client", None)
        except Exception as err:  # noqa: BLE001 - 跨插件依赖失败时降级
            logger.warning(f"【TG磁力离线】获取 115 客户端失败：{err}")
            return None

    def _delete_source(self, fileitem: Any) -> Tuple[bool, str]:
        """
        删除 115 上的源文件或目录，用于「库中已存在」的场景。

        目录型源不自动删除：整季包目录名里通常没有集号，无法确认库里是否已覆盖
        包内所有集，直接删会丢数据，因此只提示手动确认。

        :param fileitem: 待整理文件项
        :return: (是否已删除, 未删除原因)
        """
        file_type = str(getattr(fileitem, "type", "") or "")
        if file_type and file_type != "file":
            return False, "源是目录，为避免误删整包已跳过"
        try:
            from app.chain.storage import StorageChain

            if StorageChain().delete_file(fileitem):
                return True, ""
            return False, "存储删除返回失败"
        except Exception as err:  # noqa: BLE001 - 删除失败只记录
            logger.error(f"【TG磁力离线】删除源文件失败：{err}")
            return False, f"删除异常：{err}"

    def _answer_callback(self, callback_id: Any) -> None:
        """
        应答按钮回调，避免客户端一直显示加载中。

        :param callback_id: 回调查询 ID
        """
        if not callback_id:
            return
        try:
            requests.post(
                self._bot_url("answerCallbackQuery"),
                json={"callback_query_id": callback_id},
                timeout=15,
                proxies=self._telegram_proxies(),
            )
        except Exception as err:  # noqa: BLE001 - 应答失败不影响主流程
            logger.warning(f"【TG磁力离线】应答回调失败：{self._mask_secrets(str(err))}")

    # ============================ 库内存在检查与通知 ============================

    def _library_exists(self, mediainfo: Any, fileitem: Any) -> Optional[str]:
        """
        查询媒体库是否已存在该媒体；电视剧按具体集判断，避免整剧存在就拦掉缺集。

        :param mediainfo: MP 识别结果
        :param fileitem: 待整理文件项
        :return: 已存在说明；不存在或查询失败时为 None
        """
        exist = self._exists_info(mediainfo)
        if not exist:
            return None
        if str(getattr(mediainfo, "type", "") or "") == str(MediaType.TV):
            seasons = getattr(exist, "seasons", None) or {}
            source_name = Path(str(getattr(fileitem, "path", "") or "")).name
            try:
                meta = MetaInfo(source_name)
            except Exception:  # noqa: BLE001 - 解析失败按整剧存在处理
                meta = None
            if meta is not None and isinstance(seasons, dict):
                season = meta.begin_season
                episodes = set(meta.episode_list or [])
                if season is not None:
                    existing = set(seasons.get(season) or [])
                    if episodes and not episodes.issubset(existing):
                        logger.info(
                            f"【TG磁力离线】库中缺集，放行整理：{source_name} "
                            f"集 {sorted(episodes)} 不在已有 {sorted(existing)} 内"
                        )
                        return None
        title = str(getattr(mediainfo, "title", "") or "")
        server = str(getattr(exist, "server", "") or "")
        return f"{title}（{server or '媒体库'}）"

    def _exists_info(self, mediainfo: Any) -> Any:
        """
        查询媒体库是否已有该媒体，结果缓存 60 秒。

        同一部剧的每一集都会走到这里，缓存可以避免批量整理时反复查媒体服务器。

        :param mediainfo: MP 识别结果
        :return: 存在信息对象；不存在或查询失败时为 None
        """
        key = f"{getattr(mediainfo, 'media_id', '')}|{getattr(mediainfo, 'type', '')}"
        now = time.time()
        with self._choice_lock:
            cached = self._exists_cache.get(key)
            if cached and now - cached[0] < 60:
                return cached[1]
        try:
            exist = self.chain.media_exists(mediainfo=mediainfo)
        except Exception as err:  # noqa: BLE001 - 查询失败不阻塞整理
            logger.warning(f"【TG磁力离线】查询媒体库失败：{err}")
            return None
        with self._choice_lock:
            self._exists_cache[key] = (now, exist)
        return exist

    def _send_notify(self, text: str) -> None:
        """
        向默认目标推送一条运行通知。

        :param text: 通知正文
        """
        chat_id = self._notify_target()
        if chat_id is None:
            logger.warning("【TG磁力离线】没有可用的通知会话，跳过通知")
            return
        logger.info(
            f"【TG磁力离线】发送通知到 {chat_id}：{text.splitlines()[0][:60]}"
        )
        message_id = self._send_text(chat_id, text)
        if message_id is None:
            # Telegram 抖动时常一次性失败：重试一次，仍失败就明确记录，便于区分
            # 「插件没发」和「发出去了但没收到」
            time.sleep(1)
            message_id = self._send_text(chat_id, text)
        if message_id is None:
            logger.warning(
                f"【TG磁力离线】通知未送达（chat {chat_id}）："
                f"{text.splitlines()[0][:40]}"
            )
        else:
            logger.info(f"【TG磁力离线】通知已送达（message_id={message_id}）")

    @staticmethod
    def _event_value(data: Any, name: str, default: Any = None) -> Any:
        """
        兼容对象与字典两种事件载荷取值。

        outbox 重投的事件可能以字典形式送达，直接用 getattr 会全部取空。

        :param data: 事件数据（pydantic 对象或字典）
        :param name: 字段名
        :param default: 缺失时的默认值
        :return: 字段值
        """
        if isinstance(data, dict):
            return data.get(name, default)
        return getattr(data, name, default)

    @staticmethod
    def _history_source(data: Any) -> str:
        """
        按整理历史 ID 反查源路径。

        部分整理事件里 fileitem 与 transferinfo.fileitem 都为空，只能靠历史 ID 取路径。

        :param data: 整理结果事件数据
        :return: 源路径；无法取得时为空字符串
        """
        history_id = TgOfflineBot._event_value(data, "transfer_history_id")
        if not history_id:
            return ""
        try:
            from app.db.oper.transferhistory import TransferHistoryOper

            record = TransferHistoryOper().get(int(history_id))
            return str(getattr(record, "src", "") or "")
        except Exception as err:  # noqa: BLE001 - 取不到时按无来源处理
            logger.warning(f"【TG磁力离线】按历史 ID 取源路径失败：{err}")
            return ""

    def _recent_owned_history(self, within: float = 180.0) -> Any:
        """
        取最近入库、且来源匹配本插件的最新整理历史。

        部分完成事件既没有路径也没有历史 ID，只能按时间窗从整理历史里反查。

        :param within: 时间窗秒数
        :return: 历史记录对象；找不到时为 None
        """
        try:
            from app.db.oper.transferhistory import TransferHistoryOper

            today = datetime.now().strftime("%Y-%m-%d")
            records = TransferHistoryOper().list_by_date(today) or []
        except Exception as err:  # noqa: BLE001 - 查询失败按无来源处理
            logger.warning(f"【TG磁力离线】查询最近整理历史失败：{err}")
            return None
        matches = self._owned_histories_from(records, within)
        return matches[-1] if matches else None

    def _owned_histories_from(
        self, records: List[Any], within: float
    ) -> List[Any]:
        """
        从历史记录中筛出时间窗内、来源匹配本插件的记录（按时间升序）。

        :param records: 历史记录列表
        :param within: 时间窗秒数
        :return: 匹配的记录列表
        """
        cutoff = time.time() - within
        if self._last_submit_ts:
            # 只补发本插件最近一次提交之后的记录，避免重启后把历史记录重发一遍
            cutoff = max(cutoff, self._last_submit_ts - 60)
        matches: List[Tuple[float, Any]] = []
        for record in records:
            src = str(getattr(record, "src", "") or "")
            if not src or not self._is_owned_path(src):
                continue
            # 拦截（库已存在/待确认）在 MP 里会写失败历史，不能当成「已入库」通知
            if getattr(record, "status", True) in (False, 0):
                continue
            date_text = str(getattr(record, "date", "") or "")
            try:
                stamp = time.mktime(time.strptime(date_text, "%Y-%m-%d %H:%M:%S"))
            except (TypeError, ValueError):
                continue
            if stamp < cutoff:
                continue
            matches.append((stamp, record))
        matches.sort(key=lambda item: item[0])
        return [record for _, record in matches]

    def _sweep_recent_histories(self) -> bool:
        """
        定期按整理历史补发完成通知。

        115 网盘整理（P115StrmHelper 的 move/strm）不一定发布 TransferComplete 事件，
        因此不能只靠事件；这里每轮巡检按最近历史补一次，并对已通知记录去重。

        :return: 本轮是否发现了新的入库记录
        """
        try:
            from app.db.oper.transferhistory import TransferHistoryOper

            today = datetime.now().strftime("%Y-%m-%d")
            records = TransferHistoryOper().list_by_date(today) or []
        except Exception as err:  # noqa: BLE001 - 查询失败下轮再试
            logger.warning(f"【TG磁力离线】巡检整理历史失败：{err}")
            return False
        found_new = False
        for record in self._owned_histories_from(records, within=600):
            dest = str(getattr(record, "dest", "") or "")
            src = str(getattr(record, "src", "") or "")
            key = dest or src
            if not key:
                continue
            with self._choice_lock:
                if key in self._notified_histories:
                    continue
            self._remember_notified(key)
            title = str(getattr(record, "title", "") or "")
            year = str(getattr(record, "year", "") or "")
            season = 0
            match_season = re.search(
                r"S(\d+)", str(getattr(record, "seasons", "") or "")
            )
            if match_season:
                season = int(match_season.group(1))
            episode = 0
            match_episode = re.search(
                r"E(\d+)", str(getattr(record, "episodes", "") or "")
            )
            if match_episode:
                episode = int(match_episode.group(1))
            logger.info(f"【TG磁力离线】按历史补发完成通知：{title} {src}")
            self._buffer_complete(
                title, year, season, episode, dest, getattr(record, "type", "")
            )
            found_new = True
        if found_new:
            self._wake_residue_sweep("发现新的入库记录")
        return found_new

    def _notify_from_recent_history(self) -> None:
        """
        延迟按最近整理历史补发完成通知。

        完成事件通常早于历史记录落库，事件里又可能没有路径，因此延迟再查一次。
        """
        record = self._recent_owned_history(within=600)
        if record is None:
            logger.info("【TG磁力离线】延迟反查未找到本插件的入库记录，跳过补发")
            return
        dest = str(getattr(record, "dest", "") or "")
        src = str(getattr(record, "src", "") or "")
        key = dest or src
        with self._choice_lock:
            if key in self._notified_histories:
                return
        self._remember_notified(key)
        title = str(getattr(record, "title", "") or "")
        year = str(getattr(record, "year", "") or "")
        season = 0
        match_season = re.search(r"S(\d+)", str(getattr(record, "seasons", "") or ""))
        if match_season:
            season = int(match_season.group(1))
        episode = 0
        match_episode = re.search(
            r"E(\d+)", str(getattr(record, "episodes", "") or "")
        )
        if match_episode:
            episode = int(match_episode.group(1))
        logger.info(f"【TG磁力离线】按历史补发完成通知：{title} {src}")
        self._buffer_complete(
            title, year, season, episode, dest, getattr(record, "type", "")
        )

    @eventmanager.register(EventType.TransferComplete)
    def _on_transfer_complete(self, event: Event) -> None:
        """
        整理完成：按「标题+季」聚合后推送入库通知，并核对 STRM。

        批量整理时每个文件都会触发一次事件，逐条发送会刷屏；这里在聚合窗口内
        合并为一条，并对同一集被本地与网盘两侧重复整理的情况去重。

        :param event: TransferComplete 事件
        """
        if not self._enabled:
            return
        data = event.event_data
        mediainfo = self._event_value(data, "mediainfo")
        transferinfo = self._event_value(data, "transferinfo")
        fileitem = self._event_value(data, "fileitem")
        source = str(self._event_value(fileitem, "path") or "")
        if not source:
            # 部分整理完成事件的 fileitem 为空，退回整理结果里的源文件项
            result_item = self._event_value(transferinfo, "fileitem")
            source = str(self._event_value(result_item, "path") or "")
        if not source:
            source = self._history_source(data)
        fallback_record = None
        if not source:
            fallback_record = self._recent_owned_history()
            if fallback_record is not None:
                source = str(getattr(fallback_record, "src", "") or "")
        if not source:
            now_ts = time.time()
            with self._choice_lock:
                should_log = (
                    not self._empty_event_logged
                    or now_ts - self._empty_event_logged > 600
                )
                if should_log:
                    self._empty_event_logged = now_ts
            if should_log:
                logger.warning(
                    "【TG磁力离线】整理完成事件缺少源路径且无法反查，已跳过"
                    "（同类日志 10 分钟内只记一次）"
                )
            # 记录落库通常晚于事件送达，延迟再按最近历史补一次
            timer = threading.Timer(30.0, self._notify_from_recent_history)
            timer.daemon = True
            timer.start()
            self._cleanup_identifiers()
            return
        # 默认只通知本插件提交范围（/待整理）内的整理，避免订阅下载也刷屏
        if self._notify_only_from_bot and (
            not self._in_precheck_scope(source) or not self._is_owned_path(source)
        ):
            logger.info(
                f"【TG磁力离线】整理完成事件被跳过（范围或来源不匹配）：{source}"
            )
            self._cleanup_identifiers()
            return
        logger.info(f"【TG磁力离线】收到整理完成事件：{source}")
        record_title = (
            str(getattr(fallback_record, "title", "") or "") if fallback_record else ""
        )
        record_year = (
            str(getattr(fallback_record, "year", "") or "") if fallback_record else ""
        )
        title = str(self._event_value(mediainfo, "title") or "") or record_title
        year = str(self._event_value(mediainfo, "year") or "") or record_year
        season = 0
        seasons = self._event_value(mediainfo, "seasons") or {}
        if not seasons and fallback_record is not None:
            raw_season = str(getattr(fallback_record, "seasons", "") or "")
            match_season = re.search(r"S(\d+)", raw_season)
            if match_season:
                seasons = {int(match_season.group(1)): []}
        if isinstance(seasons, dict) and seasons:
            try:
                season = int(sorted(seasons.keys())[0])
            except (TypeError, ValueError):
                season = 0
        episode = 0
        meta = self._event_value(data, "meta")
        if meta is not None:
            try:
                episodes = list(self._event_value(meta, "episode_list") or [])
                episode = int(episodes[0]) if episodes else 0
            except (TypeError, ValueError):
                episode = 0
        if not episode and fallback_record is not None:
            raw_episode = str(getattr(fallback_record, "episodes", "") or "")
            match_episode = re.search(r"E(\d+)", raw_episode)
            if match_episode:
                episode = int(match_episode.group(1))
        target_item = self._event_value(transferinfo, "target_item")
        target_dir = self._event_value(transferinfo, "target_diritem")
        dest = str(
            self._event_value(target_item, "path")
            or self._event_value(target_dir, "path")
            or (
                str(getattr(fallback_record, "dest", "") or "")
                if fallback_record is not None
                else ""
            )
            or source
            or ""
        )
        if self._notify_transfer:
            self._buffer_complete(
                title,
                year,
                season,
                episode,
                dest,
                self._event_value(mediainfo, "type")
                or self._event_value(mediainfo, "media_type"),
            )
        self._cleanup_identifiers()
        if (
            self._cleanup_residue_enabled
            and fileitem is not None
            and source
            and self._is_owned_path(source)
        ):
            self._schedule_residue_cleanup(source, fileitem)

    @eventmanager.register(EventType.TransferFailed)
    def _on_transfer_failed(self, event: Event) -> None:
        """
        整理失败：推送失败通知；同一失败在 5 分钟内只发一次，避免 outbox 重投刷屏。

        :param event: TransferFailed 事件
        """
        if not self._enabled or not self._notify_transfer:
            return
        data = event.event_data
        mediainfo = self._event_value(data, "mediainfo")
        transferinfo = self._event_value(data, "transferinfo")
        fileitem = self._event_value(data, "fileitem")
        source = str(self._event_value(fileitem, "path") or "")
        if not source:
            result_item = self._event_value(transferinfo, "fileitem")
            source = str(self._event_value(result_item, "path") or "")
        if not source:
            source = self._history_source(data)
        if self._notify_only_from_bot and (
            not self._in_precheck_scope(source) or not self._is_owned_path(source)
        ):
            return
        title = str(self._event_value(mediainfo, "title") or Path(source).name)
        reason = str(self._event_value(transferinfo, "message") or "")
        stage = str(self._event_value(transferinfo, "failure_stage") or "")
        dedup_key = f"{title}|{stage}|{reason}"
        now = time.time()
        with self._choice_lock:
            expired = [key for key, ts in self._failed_recent.items() if now - ts > 300]
            for key in expired:
                self._failed_recent.pop(key, None)
            if dedup_key in self._failed_recent:
                return
            self._failed_recent[dedup_key] = now
        lines = [f"整理失败：{title}"]
        if stage:
            lines.append(f"阶段：{stage}")
        if reason:
            lines.append(f"原因：{reason}")
        # 失败路径只写日志，不推送到会话
        if source:
            logger.info(f"【TG磁力离线】整理失败路径：{source}")
            # 失败记录写入后再清理该目录遗留的失败历史与队列项
            timer = threading.Timer(
                10.0,
                self._purge_stale_records,
                args=(Path(source).parent.as_posix(),),
            )
            timer.daemon = True
            timer.start()
        # 失败通知对用户没有价值（广告文件也会触发），只写日志
        logger.info("【TG磁力离线】" + " / ".join(lines))

    def _buffer_complete(
        self,
        title: str,
        year: str,
        season: int,
        episode: int,
        dest: str,
        media_type: Any = "",
    ) -> None:
        """
        把一次整理完成事件并入聚合缓冲，并按窗口延时发送汇总。

        聚合键包含年份与媒体类型：同名不同年份的电影、同年同名的电影与剧集版
        都不会被合并进同一条通知；不同季、不同剧本来就各自独立。

        :param title: 媒体标题
        :param year: 年份
        :param season: 季号，0 表示电影或未知
        :param episode: 集号，0 表示电影或未知
        :param dest: 整理目标路径
        :param media_type: 媒体类型（电影 / 电视剧），用于区分同名条目
        """
        type_key = self._name_key(
            str(getattr(media_type, "value", media_type) or "")
        )
        key = f"{title}|{year}|{season}|{type_key}"
        window = max(0, self._notify_window)
        now = time.time()
        with self._choice_lock:
            item = self._notify_buffer.setdefault(
                key,
                {
                    "title": title,
                    "year": year,
                    "season": season,
                    "episodes": set(),
                    "dests": set(),
                    "first_ts": now,
                },
            )
            if episode:
                item["episodes"].add(int(episode))
            if dest:
                item["dests"].add(dest)
            existing = self._notify_timers.pop(key, None)
            if existing:
                existing.cancel()
            # 持续有新集进来时定时器会被不断重置，压太久要强制发一次，避免通知饿死
            forced = (
                window > 0
                and now - float(item.get("first_ts") or now) >= NOTIFY_MAX_HOLD_SECONDS
            )
            timer = None
            if window > 0 and not forced:
                timer = threading.Timer(window, self._flush_notify, args=(key,))
                timer.daemon = True
                self._notify_timers[key] = timer
        if timer is None:
            self._flush_notify(key)
        else:
            timer.start()

    def _flush_notify(self, key: str) -> None:
        """
        发送聚合后的整理完成通知，并按需启动 STRM 批量核对。

        :param key: 聚合键（标题+季）
        """
        with self._choice_lock:
            self._notify_timers.pop(key, None)
            item = self._notify_buffer.pop(key, None)
        if not item:
            return
        logger.info(f"【TG磁力离线】发送聚合通知：{key}")
        title = item["title"]
        year = item["year"]
        season = item["season"]
        episodes = sorted(item["episodes"])
        dests = sorted(item["dests"])
        label = title
        if year:
            label += f"（{year}）"
        if season:
            label += f" 第{season}季"
        lines = [f"整理完成，已入库\n{label}"]
        library = self._library_name_for(dests[0]) if dests else ""
        if library:
            lines.append(f"媒体库：{library}")
        if episodes:
            shown = ",".join(f"E{value:02d}" for value in episodes[:20])
            if len(episodes) > 20:
                shown += f" 等 {len(episodes)} 集"
            lines.append(shown)
        # 入库通知只给结论与集数，具体路径写日志，不推送到会话
        if dests:
            logger.info(f"【TG磁力离线】入库路径：{', '.join(dests[:5])}")
        self._send_notify("\n".join(lines))
        if self._notify_strm and dests:
            threading.Thread(
                target=self._verify_strm_batch,
                args=(dests, label),
                name="TgOfflineBot-StrmCheck",
                daemon=True,
            ).start()

    def _verify_strm_batch(self, dests: List[str], label: str) -> None:
        """
        批量核对 STRM 是否落盘，只汇总发一条通知。

        :param dests: 本批整理的目标路径
        :param label: 媒体标签，用于通知
        """
        targets: List[Path] = []
        for dest in dests:
            target = self._strm_target(dest)
            if target is not None and target not in targets:
                targets.append(target)
        if not targets:
            return
        pending = list(range(len(targets)))
        found: List[Path] = []
        delay = max(10, self._strm_check_delay)
        deadline = time.monotonic() + 600
        while pending and time.monotonic() < deadline:
            if self._stop_event.is_set():
                return
            for index in list(pending):
                target = targets[index]
                try:
                    ready = (
                        target.exists()
                        if target.suffix == ".strm"
                        else target.is_dir() and any(target.glob("*.strm"))
                    )
                except OSError:
                    ready = False
                if ready:
                    found.append(target)
                    pending.remove(index)
            if pending:
                time.sleep(delay)
        self._send_notify(f"STRM：{len(found)}/{len(targets)} 已生成\n{label}")

    # ============================ 整理后残留清理 ============================

    def _buffer_misc_notify(self, key: str, line: str) -> None:
        """
        把一行通知并入同 key 的缓冲区，窗口结束后合并发送。

        :param key: 聚合键，形如 ``exists|标题|季`` 或 ``residue|目录``
        :param line: 单行内容
        """
        window = max(0, self._notify_window)
        with self._choice_lock:
            lines = self._misc_buffer.setdefault(key, [])
            if line and line not in lines:
                lines.append(line)
            existing = self._misc_timers.pop(key, None)
            if existing:
                existing.cancel()
            timer = None
            if window > 0:
                timer = threading.Timer(window, self._flush_misc_notify, args=(key,))
                timer.daemon = True
                self._misc_timers[key] = timer
        if timer is None:
            self._flush_misc_notify(key)
        else:
            timer.start()

    def _flush_misc_notify(self, key: str) -> None:
        """
        发送合并后的通知。

        :param key: 聚合键
        """
        with self._choice_lock:
            self._misc_timers.pop(key, None)
            lines = self._misc_buffer.pop(key, [])
        if not lines:
            return
        kind, _, label = key.partition("|")
        titles = {
            "exists": "媒体库已存在，跳过整理",
            "residue": "已清理残留文件",
            "emptydir": "已删除空目录",
        }
        title = titles.get(kind, "运行通知")
        header = f"{title}：{label}" if label else title
        shown = "\n".join(lines[:20])
        if len(lines) > 20:
            shown += f"\n……另有 {len(lines) - 20} 项"
        self._send_notify(f"{header}\n{shown}")

    def _remove_if_empty(self, parent: str) -> bool:
        """
        目录为空时删除它。

        115 的列表有延迟，刚删完文件的目录可能还显示非空；调用方可以把路径
        放进 `_pending_dir_removals` 由后台重试，所以这里返回是否已完成。

        :param parent: 目录路径
        :return: 目录已不存在或删除成功返回 True
        """
        if not self._cleanup_empty_dirs:
            # 不清理空目录：视为完成，避免重试队列白占一小时
            return True
        # 范围根与专属目录都是容器目录，清理时一律保留
        if self._is_scope_root(parent) or self._is_target_root(parent):
            return False
        dir_item = self._get_existing_dir(parent)
        if not dir_item:
            # 已经不在了，视为完成
            return True
        try:
            from app.chain.storage import StorageChain

            chain = StorageChain()
            items = chain.list_files(dir_item) or []
            if items:
                return False
            if chain.delete_file(dir_item):
                logger.info(f"【TG磁力离线】已删除空目录：{parent}")
                return True
            return False
        except Exception as err:  # noqa: BLE001 - 删除失败只记录
            logger.warning(f"【TG磁力离线】删除空目录失败：{parent} - {err}")
            return False

    def _retry_dir_removals(self) -> None:
        """
        重试删除清理残留后可能漏删的空目录。

        删除文件与列举目录之间 115 有延迟，当场判断可能仍是「非空」；
        把目录排进队列后每轮巡检重试，成功或超时后出队。
        """
        with self._choice_lock:
            pending = {key: dict(value) for key, value in self._pending_dir_removals.items()}
        if not pending:
            return
        now = time.time()
        finished: List[str] = []
        updates: Dict[str, Dict[str, float]] = {}
        for path, state in pending.items():
            deadline = float(state.get("deadline") or 0)
            if deadline <= now:
                finished.append(path)
                continue
            if now < float(state.get("next_try") or 0):
                # 还在退避窗口内，本轮不请求 115
                continue
            if self._remove_if_empty(path):
                finished.append(path)
                continue
            attempts = int(state.get("attempts") or 0) + 1
            if attempts >= 3:
                # 连续 3 次仍非空：大概率是含正片的目录，出队避免持续请求 115
                logger.info(f"【TG磁力离线】空目录重试放弃（仍非空）：{path}")
                finished.append(path)
                continue
            updates[path] = {
                "deadline": deadline,
                "attempts": attempts,
                "next_try": now + (60 if attempts == 1 else 300),
            }
        if not finished and not updates:
            return
        with self._choice_lock:
            for path in finished:
                self._pending_dir_removals.pop(path, None)
            for path, state in updates.items():
                if path in self._pending_dir_removals:
                    self._pending_dir_removals[path] = state

    def _enqueue_dir_removal(self, paths: List[str]) -> None:
        """
        把目录排进空目录删除重试队列。

        :param paths: 目录路径列表
        """
        if not paths:
            return
        now = time.time()
        target = self._effective_target_path()
        roots = set(self._scope_roots())
        with self._choice_lock:
            for path in paths:
                if not path:
                    continue
                key = path.rstrip("/")
                if key in roots or (target and key == target):
                    # 容器目录一律保留，不进重试队列
                    continue
                state = self._pending_dir_removals.get(key)
                if state is None:
                    if len(self._pending_dir_removals) >= 200:
                        continue
                    self._pending_dir_removals[key] = {
                        "deadline": now + 3600,
                        "attempts": 0,
                        "next_try": now,
                    }
                else:
                    # 已在队列：只续总期限，不重置尝试次数与退避，避免永久重试
                    state["deadline"] = max(float(state.get("deadline") or 0), now + 3600)

    def _cleanup_residue(self, parent: str, fileitem: Any) -> None:
        """
        清理源目录里的广告与无关残留文件。

        正片、字幕、音轨、图片与 nfo 一律保留；其余文件交给 AI 判断，AI 没有结论时
        按「无法识别也删除」开关处理。同一目录 10 分钟内只扫描一次。

        :param parent: 源文件所在目录
        :param fileitem: 源文件项
        """
        if not self._cleanup_residue_enabled or fileitem is None:
            return
        if self._is_scope_root(parent) or self._is_target_root(parent):
            # 源文件直接在范围根/专属目录根下，同目录混着别的任务，不扫描
            return
        now = time.time()
        with self._choice_lock:
            last = self._residue_checked.get(parent)
            if last and now - last < RESIDUE_SCAN_INTERVAL:
                return
            self._residue_checked[parent] = now
            expired = [
                key
                for key, ts in self._residue_checked.items()
                if now - ts > RESIDUE_SCAN_INTERVAL * 6
            ]
            for key in expired:
                self._residue_checked.pop(key, None)
        files, dirs = self._collect_residue_entries(fileitem, parent)
        # 即使没有残留文件，也要清掉该目录在 MP 里留下的失败/未完成记录
        self._purge_stale_records(parent)
        candidates: List[Tuple[Any, str]] = []
        for item in files:
            raw_path = str(getattr(item, "path", "") or getattr(item, "name", ""))
            name = str(getattr(item, "name", "") or Path(raw_path).name)
            if not name:
                continue
            candidates.append((item, name))
        deleted: List[str] = []
        if candidates:
            sizes = {
                name: float(getattr(item, "size", 0) or 0)
                for item, name in candidates
            }
            verdict = self._ai_residue_verdict(
                [name for _, name in candidates], sizes
            )
            if candidates and not verdict:
                # AI 一个结论都没给：不记为「已扫描」，下一轮巡检立刻重试
                with self._choice_lock:
                    self._residue_checked.pop(parent, None)
            for item, name in candidates:
                if name not in verdict:
                    # 超出单次送审上限的文件保守跳过，不做「无结论即删」
                    continue
                decision = verdict[name]
                if decision is None:
                    # AI 没给出结论的文件：按开关决定是否统一删除
                    if not self._cleanup_unrecognized:
                        continue
                    decision = True
                if not decision:
                    continue
                if self._delete_residue_item(item):
                    deleted.append(name)
        # 子目录里的文件被清掉后，从深到浅删掉空目录（含 Featurettes 这类中间层）；
        # 115 列表可能还没反映删除，剩下的交给重试队列
        ordered_dirs = sorted(dirs, key=len, reverse=True)
        for path in ordered_dirs:
            self._remove_if_empty(path)
        # 仅两种情况需要重试删除：本轮删过文件（目录可能刚变空），
        # 或目录树里已经没有任何文件（只剩空目录）。含正片的目录不入队，
        # 否则每分钟都会对每个任务目录发起 115 请求。
        if deleted or not files:
            self._enqueue_dir_removal(ordered_dirs + [parent])
        if deleted:
            logger.info(f"【TG磁力离线】清理残留文件：{deleted}")
            self._append_history("清理残留", f"{len(deleted)} 个")
        # 本层目录为空时同样清掉；目标目录根由 _is_target_root 保护
        self._remove_if_empty(parent)

    def _purge_stale_records(self, dir_prefix: str) -> None:
        """
        删除该目录下失败或未完成的整理记录与队列项。

        被本插件拦截（库已存在）的任务在 MP 里会留下失败历史与 planned 队列项，
        广告文件还会留下「未识别到媒体信息」的队列项；这里按目录前缀一并清掉。

        :param dir_prefix: 源目录路径
        """
        token = self._api_token_value()
        if not token:
            return
        base = f"http://127.0.0.1:{self._host_port}/api/v1"
        headers = {"X-API-KEY": token, "Content-Type": "application/json"}
        prefix = dir_prefix.rstrip("/") + "/"
        try:
            queue_resp = requests.get(
                f"{base}/transfer/queue",
                params={"page": 1, "count": 100},
                headers=headers,
                timeout=60,
                proxies={"http": None, "https": None},
            )
            queue_items = (queue_resp.json() or {}).get("data") or []
            for item in queue_items:
                path = str((item or {}).get("path") or "")
                if not path.startswith(prefix):
                    continue
                # 只处理名称能对上本插件来源标记的条目，避免误删同目录其它来源
                if not self._is_owned_text(path):
                    continue
                requests.delete(
                    f"{base}/transfer/queue",
                    json=item,
                    headers=headers,
                    timeout=60,
                    proxies={"http": None, "https": None},
                )
                logger.info(f"【TG磁力离线】已移除整理队列项：{path}")
        except Exception as err:  # noqa: BLE001 - 清理失败不影响其它步骤
            logger.warning(f"【TG磁力离线】清理整理队列失败：{err}")
        try:
            history_resp = requests.get(
                f"{base}/history/transfer",
                params={"page": 1, "count": 100, "status": False},
                headers=headers,
                timeout=60,
                proxies={"http": None, "https": None},
            )
            records = ((history_resp.json() or {}).get("data") or {}).get("list") or []
            for record in records:
                # 只删失败历史：接口的 status 过滤不可靠，必须本地再校验一次
                if (record or {}).get("status") not in (False, 0):
                    continue
                src = str((record or {}).get("src") or "")
                if not src.startswith(prefix):
                    continue
                if not self._is_owned_text(src):
                    continue
                requests.delete(
                    f"{base}/history/transfer",
                    params={"deletedest": False, "deletesrc": False},
                    json=record,
                    headers=headers,
                    timeout=60,
                    proxies={"http": None, "https": None},
                )
                logger.info(
                    f"【TG磁力离线】已删除失败整理记录：{record.get('title')} {record.get('seasons')}{record.get('episodes')}"
                )
        except Exception as err:  # noqa: BLE001 - 清理失败不影响其它步骤
            logger.warning(f"【TG磁力离线】清理整理历史失败：{err}")

    def _schedule_residue_cleanup(
        self,
        source_path: str,
        fileitem: Any,
        delay_seconds: Optional[float] = None,
    ) -> None:
        """
        安排目录静默后的残留清理。

        同一目录多集连续整理时每次完成都会重置计时器，等目录安静下来再扫描，
        避免把同批还没整理的集当成残留删掉。

        :param source_path: 源文件路径
        :param fileitem: 源文件项
        :param delay_seconds: 覆盖默认延迟（秒），用于被跳过后的重排
        """
        if not self._cleanup_residue_enabled or fileitem is None:
            return
        # 有整理事件说明待整理范围里刚出现了本插件目录，唤醒已休眠的兜底巡检
        self._wake_residue_sweep("整理事件")
        parent = Path(source_path).parent.as_posix()
        if self._is_scope_root(parent):
            return
        delay = (
            max(60, self._residue_delay)
            if delay_seconds is None
            else max(10, float(delay_seconds))
        )
        with self._choice_lock:
            existing = self._residue_timers.pop(parent, None)
            if existing:
                existing.cancel()
            timer = threading.Timer(
                delay, self._run_residue_cleanup, args=(parent, fileitem)
            )
            timer.daemon = True
            self._residue_timers[parent] = timer
        timer.start()

    def _run_residue_cleanup(self, parent: str, fileitem: Any) -> None:
        """
        计时器到点后执行一次残留扫描；被更新的计时器替换时直接退出。

        :param parent: 源文件所在目录
        :param fileitem: 源文件项
        """
        with self._choice_lock:
            current = self._residue_timers.get(parent)
            if current is not threading.current_thread():
                return
            self._residue_timers.pop(parent, None)
        if self._organize_batch_active():
            # 队列还没整理完：这一轮的残留清理交给整批结束后的统一巡检
            logger.info(
                f"【TG磁力离线】整理队列尚未清空，暂缓清理该目录：{parent}"
            )
            # 重排一次短延迟，避免本轮跳过后再等一个完整巡检周期
            self._schedule_residue_cleanup(parent, fileitem, delay_seconds=300)
            return
        if self._residue_cleanup_blocked(parent):
            # 刚触发整理、MP 还在异步搬文件，此时目录里仍是正片
            logger.info(
                f"【TG磁力离线】该目录刚触发整理，暂缓清理：{parent}"
            )
            self._schedule_residue_cleanup(parent, fileitem, delay_seconds=300)
            return
        self._cleanup_residue(parent, fileitem)

    def _collect_residue_entries(
        self, fileitem: Any, parent_path: str = ""
    ) -> Tuple[List[Any], List[str]]:
        """
        递归收集任务目录下的文件与子目录。

        花絮、样片常放在子目录里（如 Featurettes/Promos），只扫一层会漏掉；
        这里按深度与文件数设上限，避免面对整季大目录时过度请求。

        :param fileitem: 源文件项
        :param parent_path: 源文件所在目录，文件已被移走时用它定位目录
        :return: (文件项列表, 子目录路径列表)
        """
        files: List[Any] = []
        dirs: List[str] = []
        try:
            from app.chain.storage import StorageChain

            chain = StorageChain()
            root = self._get_existing_dir(parent_path) if parent_path else None
            if root is None:
                root = chain.get_parent_item(fileitem)
            if root is None:
                return files, dirs
            queue: List[Tuple[Any, int]] = [(root, 0)]
            while queue and len(files) < RESIDUE_SCAN_MAX_FILES:
                current, depth = queue.pop(0)
                try:
                    children = chain.list_files(current) or []
                except Exception as err:  # noqa: BLE001 - 单层失败不影响其它
                    logger.warning(f"【TG磁力离线】列举残留目录失败：{err}")
                    continue
                for child in children:
                    kind = str(getattr(child, "type", "") or "")
                    if kind == "file":
                        files.append(child)
                        if len(files) >= RESIDUE_SCAN_MAX_FILES:
                            break
                    elif kind == "dir" and depth + 1 < RESIDUE_SCAN_MAX_DEPTH:
                        path = str(getattr(child, "path", "") or "")
                        if path:
                            dirs.append(path)
                        queue.append((child, depth + 1))
        except Exception as err:  # noqa: BLE001 - 扫描失败只记录
            logger.warning(f"【TG磁力离线】扫描残留文件失败：{err}")
        return files, dirs

    def _delete_residue_item(self, item: Any) -> bool:
        """
        删除一个残留文件项。

        :param item: 文件项
        :return: 是否删除成功
        """
        try:
            from app.chain.storage import StorageChain

            return bool(StorageChain().delete_file(item))
        except Exception as err:  # noqa: BLE001 - 单个文件失败不影响其它
            logger.warning(f"【TG磁力离线】删除残留文件失败：{err}")
            return False

    def _strm_helper_config(self) -> Dict[str, Any]:
        """
        读取 P115StrmHelper 的配置（离线目录、整理目录等），带短时缓存。

        :return: 配置字典；读取失败时返回上次缓存或空字典
        """
        now = time.time()
        cached = self._strm_helper_cache
        if cached and now - cached[0] < STRM_HELPER_CONFIG_TTL:
            return cached[1]
        # 先读宿主里的插件配置（无网络请求），读不到再问一次插件接口
        data = self._strm_helper_config_from_host()
        if not data:
            data = self._strm_helper_config_from_api()
        if not data:
            return dict(cached[1]) if cached else {}
        self._strm_helper_cache = (now, data)
        return data

    def _strm_helper_config_from_host(self) -> Dict[str, Any]:
        """
        从宿主系统配置中读取 P115StrmHelper 配置。

        :return: 配置字典；取不到时为空字典
        """
        try:
            if SystemConfigOper is None:
                return {}
            return dict(SystemConfigOper().get("plugin.P115StrmHelper") or {})
        except Exception as err:  # noqa: BLE001 - 读取失败时交给接口兜底
            logger.warning(f"【TG磁力离线】读取宿主插件配置失败：{err}")
            return {}

    def _strm_helper_config_from_api(self) -> Dict[str, Any]:
        """
        通过 P115StrmHelper 的 get_config 接口读取配置。

        :return: 配置字典；接口不可用时为空字典
        """
        token = self._api_token_value()
        if not token:
            return {}
        url = (
            f"http://127.0.0.1:{self._host_port}/api/v1/plugin/"
            f"{STRM_HELPER_PLUGIN}/get_config"
        )
        try:
            response = requests.get(
                url,
                headers={"X-API-KEY": token},
                timeout=30,
                proxies={"http": None, "https": None},
            )
            data = response.json()
        except Exception as err:  # noqa: BLE001 - 读不到时交给上层缓存
            logger.warning(f"【TG磁力离线】读取 P115StrmHelper 配置失败：{err}")
            return {}
        if response.status_code != 200 or not isinstance(data, dict):
            return {}
        return data

    @staticmethod
    def _first_pan_path(raw: Any) -> str:
        """
        从多行文本或列表配置里取第一个有效的 115 路径。

        :param raw: P115StrmHelper 的配置值（列表、JSON 文本或换行/逗号分隔文本）
        :return: 以 / 开头、无尾斜杠的路径；取不到时为空字符串
        """
        candidates: List[str] = []
        if isinstance(raw, (list, tuple, set)):
            candidates = [str(item) for item in raw]
        else:
            text = str(raw or "").strip()
            if text.startswith("["):
                try:
                    loaded = json.loads(text)
                except Exception:  # noqa: BLE001 - 非 JSON 配置按文本处理
                    loaded = None
                if isinstance(loaded, list):
                    candidates = [str(item) for item in loaded]
            if not candidates:
                candidates = re.split(r"[,，\n]", text)
        for item in candidates:
            path = str(item or "").strip().strip('"').strip("/")
            if path:
                return "/" + path
        return ""

    def _offline_base(self) -> str:
        """
        取 P115StrmHelper 的离线下载目录；未配置时退回其整理目录。

        目录完全来自插件配置，不在代码里写死具体路径或目录名。

        :return: 以 / 开头、无尾斜杠的路径；取不到时为空字符串
        """
        config = self._strm_helper_config()
        base = self._first_pan_path(config.get("offline_download_paths"))
        if not base:
            base = self._first_pan_path(config.get("pan_transfer_paths"))
        return base.rstrip("/")

    def _effective_target_path(self) -> str:
        """
        实际提交目录。

        手填 target_path 时以手填为准；否则为「P115StrmHelper 离线目录 + 专属子目录」。

        :return: 以 / 开头、无尾斜杠的路径；无法解析时为空字符串
        """
        if self._target_path:
            return self._target_path.rstrip("/")
        base = self._offline_base()
        if not base:
            return ""
        subdir = self._offline_subdir.strip().strip("/")
        return f"{base}/{subdir}" if subdir else base

    def _target_display(self) -> str:
        """
        提交目录的可读描述，用于页面与消息回执。

        :return: 目录路径；解析不到时说明走默认链路
        """
        target = self._effective_target_path()
        return target or "默认链路（自动整理）"

    def _ensure_submit_target(self) -> Tuple[bool, str, str]:
        """
        提交前确认目标目录可用，必要时清缓存重建。

        目标目录被其它插件当空目录清理掉之后，路径→ID 缓存仍指向已删除的目录，
        取目录会一直失败；此时 115 会静默把离线任务放进默认离线目录（/云下载），
        提交接口却照样返回成功。所以这里必须显式确认目录能用。

        :return: (是否可用, 目录路径, 说明)
        """
        target = self._effective_target_path()
        if not target:
            # 没有专属目录：走 P115StrmHelper 默认链路，无需准备
            return True, "", ""
        if self._get_existing_dir(target):
            return True, target, ""
        self._ensure_target_dir()
        if self._get_existing_dir(target):
            return True, target, ""
        self._clear_path_caches()
        self._ensure_target_dir()
        if self._get_existing_dir(target):
            return True, target, "目标目录缓存已失效，已清缓存并重建"
        return False, target, f"目标目录不可用：{target}"

    def _clear_path_caches(self) -> None:
        """
        清理 115 侧路径与文件 ID 缓存。

        P115StrmHelper 与 115 网盘储存插件各自缓存「路径→ID」，目录被外部删除后
        缓存会指向失效 ID，必须清掉才能重新解析路径。
        """
        token = self._api_token_value()
        if not token:
            return
        headers = {"X-API-KEY": token, "Content-Type": "application/json"}
        endpoints = (
            f"http://127.0.0.1:{self._host_port}/api/v1/plugin/"
            f"{STRM_HELPER_PLUGIN}/clear_id_path_cache",
            f"http://127.0.0.1:{self._host_port}/api/v1/plugin/P115Disk/clear_cache",
        )
        for url in endpoints:
            try:
                response = requests.post(
                    url,
                    headers=headers,
                    timeout=60,
                    proxies={"http": None, "https": None},
                )
                logger.info(
                    f"【TG磁力离线】清理路径缓存：{url.rsplit('/', 2)[-2]}"
                    f" → HTTP {response.status_code}"
                )
            except Exception as err:  # noqa: BLE001 - 清缓存失败不阻断
                logger.warning(f"【TG磁力离线】清理路径缓存失败：{url} - {err}")

    def _ensure_target_dir(self) -> Tuple[bool, str]:
        """
        确保提交目录存在，不存在时由存储层递归创建。

        :return: (是否就绪, 目录路径)
        """
        target = self._effective_target_path()
        if not target:
            return True, ""
        if self._get_dir_item(target):
            return True, target
        return False, target

    def _is_target_root(self, path: str) -> bool:
        """
        判断路径是否为提交目录本身。

        该目录是本插件的容器目录，清理残留时不能把它一起删掉。

        :param path: 待判断路径
        :return: 是否为提交目录
        """
        target = self._effective_target_path()
        if not target:
            return False
        return str(path or "").rstrip("/") == target

    @staticmethod
    def _storage_module() -> str:
        """
        读取 P115StrmHelper 使用的存储模块名。

        :return: 存储模块名；读取失败时为空字符串
        """
        try:
            if SystemConfigOper is None:
                return ""
            config = SystemConfigOper().get("plugin.P115StrmHelper") or {}
            return str(config.get("storage_module") or "")
        except Exception as err:  # noqa: BLE001 - 取不到时跳过巡检
            logger.warning(f"【TG磁力离线】读取存储模块失败：{err}")
            return ""

    def _get_dir_item(self, path: str) -> Any:
        """
        按路径取目录项。

        优先使用 P115StrmHelper 配置的存储标识；该值可能是历史遗留的显示名，
        宿主里并没有对应实现，因此再按官方 115 存储兜底，避免永远取不到目录。

        :param path: 目录路径
        :return: 目录项；失败时为 None
        """
        try:
            from app.chain.storage import StorageChain

            chain = StorageChain()
            for storage in self._storage_candidates():
                item = chain.get_folder(storage, Path(path))
                if item:
                    self._note_storage_fallback(storage)
                    return item
            return None
        except Exception as err:  # noqa: BLE001 - 取不到时跳过本次
            logger.warning(f"【TG磁力离线】获取目录失败：{path} - {err}")
            return None

    def _storage_candidates(self) -> List[str]:
        """
        返回访问 115 目录时可用的存储标识候选（按优先级）。

        配置值优先，其后追加官方 115 存储标识。绝不回退到本地存储：
        115 网盘路径交给 local 会在容器里建出同名目录。

        :return: 去重后的存储标识列表
        """
        configured = self._storage_module()
        ordered: List[str] = [configured] if configured else []
        for item in STRM_HELPER_STORAGE_FALLBACKS:
            if item not in ordered:
                ordered.append(item)
        return [item for item in ordered if item and item != "local"]

    def _note_storage_fallback(self, storage: str) -> None:
        """
        记录一次「配置的存储标识不可用、改用兜底」的日志。

        :param storage: 实际取到目录的存储标识
        """
        configured = self._storage_module()
        if storage == configured or storage in self._storage_fallback_noted:
            return
        self._storage_fallback_noted.add(storage)
        logger.info(
            f"【TG磁力离线】存储标识「{configured or '空'}」不可用，"
            f"改用 {storage} 访问 115 目录"
        )

    def _prune_organize_dispatched_locked(self, now: float) -> None:
        """
        清理过期的「已触发整理」标记。

        :param now: 当前时间戳
        """
        expired = [
            key
            for key, stamp in self._organize_dispatched.items()
            if now - stamp >= ORGANIZE_CLEANUP_GRACE_SECONDS
        ]
        for key in expired:
            self._organize_dispatched.pop(key, None)

    def _organize_batch_active(self) -> bool:
        """
        判断整理队列里是否还有没触发整理的任务。

        还有待整理登记就说明这批任务没走完；此时不做残留清理，
        等整批任务都整理完再统一清一次。刚触发整理、MP 正在搬文件的
        那一个目录由 `_residue_cleanup_blocked` 单独保护，不拖住整批。

        :return: 批次仍在进行时返回 True
        """
        now = time.time()
        with self._choice_lock:
            return any(
                deadline > now for deadline in self._pending_transfer.values()
            )

    def _residue_cleanup_blocked(self, path: str) -> bool:
        """
        判断该目录是否仍在「刚触发整理、MP 正在搬文件」的宽限期内。

        触发整理是异步的：这几分钟里目录中仍是正片，判成残留就会误删；
        过了宽限期还没搬走的文件才交给 AI 判。

        :param path: 任务目录路径
        :return: 需要跳过清理时返回 True
        """
        key = (path or "").rstrip("/")
        if not key:
            return False
        now = time.time()
        with self._choice_lock:
            self._prune_organize_dispatched_locked(now)
            dispatched = self._organize_dispatched.get(key)
        return bool(
            dispatched and now - dispatched < ORGANIZE_CLEANUP_GRACE_SECONDS
        )

    def _sweep_pending_residue(self) -> bool:
        """
        定期扫描待整理范围下属于本插件提交的目录，清理残留文件。

        既覆盖「库已存在被拦截」这类没有整理完成事件的场景，也能清掉此前遗漏的目录。
        扫描后若范围里已没有本插件任务目录，就进入休眠，等新任务或整理事件唤醒。

        :return: 本轮是否真正执行了扫描（被跳过时返回 False，调用方据此决定是否推进节流）
        """
        if not self._cleanup_residue_enabled:
            return False
        now = time.time()
        with self._choice_lock:
            has_owned = any(
                expire > now for expire in self._owned_names.values()
            )
        target = self._effective_target_path()
        if not has_owned and not target:
            # 既没有名称标记也没配置专属目录时不发请求，避免空轮询
            return False
        if self._organize_batch_active():
            # 队列语义：整批任务都整理完之前不清理残留，避免把还没入库的正片当广告删掉
            if not self._organize_batch_noted:
                self._organize_batch_noted = True
                logger.info(
                    "【TG磁力离线】整理队列尚未清空，暂缓残留清理，整批整理完再统一清理"
                )
            self._wake_residue_sweep("整理队列未清空")
            return False
        self._organize_batch_noted = False
        try:
            from app.chain.storage import StorageChain

            chain = StorageChain()
        except Exception as err:  # noqa: BLE001 - 巡检失败下轮再试
            logger.warning(f"【TG磁力离线】残留巡检不可用：{err}")
            return
        found_owned = False
        reachable = 0
        roots = list(self._scope_roots())
        if target and target not in roots:
            # 专属目录本身也要巡检：其下每个任务目录都归本插件
            roots.append(target)
        for root in roots:
            root_item = self._get_existing_dir(root)
            if not root_item:
                continue
            try:
                children = chain.list_files(root_item) or []
            except Exception as err:  # noqa: BLE001 - 单个目录失败不影响其它
                logger.warning(f"【TG磁力离线】列举目录失败：{root} - {err}")
                continue
            reachable += 1
            in_target = bool(target) and root.rstrip("/") == target
            for item in children:
                if str(getattr(item, "type", "") or "") != "dir":
                    continue
                name = str(getattr(item, "name", "") or "")
                if not name:
                    continue
                path = str(getattr(item, "path", "") or "")
                if self._is_target_root(path or name):
                    # 专属目录本身不是任务目录，不能清理，也不能当作「仍有任务」
                    continue
                # 专属目录下的子目录一律视为本插件任务；其它范围仍按名称标记判定
                if not (in_target or self._is_owned_text(name)):
                    continue
                found_owned = True
                if path and self._residue_cleanup_blocked(path):
                    # 这个目录刚触发过整理，MP 还在异步搬文件，先不动它
                    continue
                if path:
                    self._cleanup_residue(path, item)
        if not reachable:
            # 目录都取不到时无法判断是否为空，保持现状，避免误休眠
            return False
        if found_owned:
            self._wake_residue_sweep("待整理目录里仍有本插件任务")
            return True
        self._sleep_residue_sweep()
        return True

    def _residue_sweep_allowed(self, now: float) -> bool:
        """
        判断当前是否允许执行一次定时残留巡检。

        休眠期间只有到达兜底复查间隔才放行；间隔为 0 表示休眠后不再复查，
        等新任务、入库记录或整理事件唤醒。

        :param now: 当前时间戳
        :return: 允许执行返回 True
        """
        if not self._residue_idle:
            return True
        if self._residue_idle_recheck <= 0:
            return False
        return now - self._residue_idle_since >= self._residue_idle_recheck * 60

    def _sleep_residue_sweep(self) -> None:
        """
        待整理范围里已没有本插件任务目录时休眠，暂停定时巡检以减少 115 请求。

        有在途离线任务时不休眠：115 的目录要等下载完成才出现，提前睡下会漏掉残留。
        重复调用会刷新休眠起点，供兜底复查计时。
        """
        if self._residue_in_flight():
            return
        with self._choice_lock:
            was_idle = self._residue_idle
            self._residue_idle = True
            self._residue_idle_since = time.time()
        if not was_idle:
            logger.info(
                "【TG磁力离线】待整理目录里已没有本插件任务，暂停定时残留巡检"
                "（有新任务、入库记录或整理事件时恢复）"
            )

    def _wake_residue_sweep(self, reason: str) -> None:
        """
        结束残留巡检的休眠状态。

        :param reason: 唤醒原因，仅用于日志
        """
        with self._choice_lock:
            if not self._residue_idle:
                return
            self._residue_idle = False
            self._residue_idle_since = 0.0
        logger.info(f"【TG磁力离线】恢复待整理残留巡检：{reason}")

    def _residue_in_flight(self) -> bool:
        """
        判断是否还有本插件的在途离线任务。

        在途期间 115 目录可能还没落盘，此时不能休眠。先看提交后的在途窗口，
        窗口过期后再向本机 P115StrmHelper 接口确认一次（不消耗 115 请求）；
        接口不可用时按「仍有在途」处理，宁可多扫一次也不漏清。

        :return: 仍有在途任务时返回 True
        """
        now = time.time()
        if self._offline_active_until and now <= self._offline_active_until:
            return True
        try:
            tasks = self._list_offline_tasks()
        except Exception as err:  # noqa: BLE001 - 接口异常时按在途处理
            logger.warning(
                "【TG磁力离线】确认在途任务失败，暂不休眠："
                f"{self._mask_secrets(str(err))}"
            )
            return True
        if tasks is None:
            return True
        for task in tasks:
            if not isinstance(task, dict):
                continue
            try:
                status = int(task.get("status", -1))
            except (TypeError, ValueError):
                continue
            if status == 0 and self._is_owned_text(str(task.get("name") or "")):
                return True
        return False

    @staticmethod
    def _residue_prompt() -> ChatPromptTemplate:
        """
        构造残留文件判定提示词。

        :return: 提示词模板
        """
        return ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "你是影视下载目录的清理助手。整理完成后，这个目录里只应该保留正片。"
                    "请找出其中的正片：通常是与目录名一致的唯一主影片文件，体积显著大于其它文件。"
                    "如果目录里已经没有正片（正片已被整理走），那么所有文件都应当删除。"
                    "除正片之外的一切都要删除：花絮、访谈、幕后、预告片、样片、广告、推广、"
                    "网址文本、图片、字幕、音轨、nfo 以及下载器残留。"
                    "文件名里带推广网址（如 www.xxx.com）、「更多无水印」「请访问」「扫码」"
                    "「加群」等特征的，即使扩展名是视频也一律删除。"
                    "拿不准是否正片时按「删除」处理。"
                    "只把应当删除的文件名放进 delete_files，保留的正片不要列出。"
                    "只输出一个 JSON 对象：{{\"delete_files\": [\"文件名\"], \"reason\": \"...\"}}，"
                    "不要输出任何其它文字。"
                ),
                ("human", "文件名清单：\n{names}\n"),
            ]
        )

    def _ai_residue_verdict(
        self, names: List[str], sizes: Optional[Dict[str, float]] = None
    ) -> Dict[str, Optional[bool]]:
        """
        调用本机 LLM 判断残留文件是否应删除。

        体积会一起送审：判断「哪个是正片」时体积比名字更可靠。

        :param names: 待判定文件名列表
        :param sizes: 文件名 -> 体积（字节），可为空
        :return: 文件名 -> True（删除）/ False（保留）/ None（AI 无结论）
        """
        # 单次最多送审 50 个；返回空 dict 表示「未判定」，调用方会跳过删除
        reviewed = names[:50]
        if not reviewed or LLMHelper is None:
            return {}
        lines: List[str] = []
        for name in reviewed:
            size = (sizes or {}).get(name)
            if size:
                lines.append(f"{name}（{self._size_text(size)}）")
            else:
                lines.append(name)
        text = self._invoke_llm_text(
            self._residue_prompt(), {"names": "\n".join(lines)}, 30
        )
        data = self._parse_json_object(text or "")
        if not data:
            # 无法解析时按「未判定」处理：本次不删除任何文件，避免误删
            logger.warning(
                f"【TG磁力离线】残留判定返回无法解析，本次不删除文件：{(text or '')[:80]}"
            )
            self._notify_ai_unavailable(f"残留判定无法解析：{(text or '')[:80]}")
            return {}
        raw_files = data.get("delete_files")
        if not isinstance(raw_files, list):
            raw_files = []
        delete_set = {
            str(item).strip() for item in raw_files if str(item).strip()
        }
        # 送审时带了体积说明，模型回填时可能把「（1.2 GB）」一起带回来
        normalized = {
            re.sub(r"[（(][^（()）]*[)）]\s*$", "", item).strip()
            for item in delete_set
        }
        return {
            name: (name in delete_set or name in normalized) for name in reviewed
        }

    @staticmethod
    def _size_text(size: Any) -> str:
        """
        把字节数转成便于阅读的体积文本。

        :param size: 字节数
        :return: 形如「1.2 GB」；无法解析时为空字符串
        """
        try:
            value = float(size)
        except (TypeError, ValueError):
            return ""
        if value <= 0:
            return ""
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if value < 1024 or unit == "TB":
                return f"{value:.1f} {unit}"
            value /= 1024
        return ""

    def _media_library_map(self) -> List[Tuple[str, List[str]]]:
        """
        读取 Emby / Jellyfin 的库名与路径映射，带 10 分钟缓存。

        :return: [(库名, [库路径...])]
        """
        now = time.time()
        if self._library_cache and now - self._library_cache[0] < 600:
            return self._library_cache[1]
        result: List[Tuple[str, List[str]]] = []
        if SystemConfigOper is not None:
            try:
                servers = SystemConfigOper().get("MediaServers") or []
                for server in servers:
                    if not isinstance(server, dict):
                        continue
                    if server.get("type") not in ("emby", "jellyfin"):
                        continue
                    if not server.get("enabled"):
                        continue
                    config = server.get("config") or {}
                    host = str(config.get("host") or "").rstrip("/")
                    apikey = str(config.get("apikey") or "")
                    if not host or not apikey:
                        continue
                    response = requests.get(
                        f"{host}/Library/VirtualFolders",
                        params={"api_key": apikey},
                        timeout=10,
                        proxies={"http": None, "https": None},
                    )
                    for library in response.json() or []:
                        name = str(library.get("Name") or "")
                        locations = [str(item) for item in (library.get("Locations") or [])]
                        if name and locations:
                            result.append((name, locations))
            except Exception as err:  # noqa: BLE001 - 取库名失败不影响通知
                logger.warning(f"【TG磁力离线】读取媒体库列表失败：{err}")
        self._library_cache = (now, result)
        return result

    def _library_name_for(self, dest: str) -> str:
        """
        根据整理目标路径判断入库到了哪个媒体库。

        :param dest: 整理目标路径（网盘或本地）
        :return: 库名；无法判断时为空字符串
        """
        local = self._strm_target(dest)
        if local is None:
            return ""
        local_path = str(local)
        if local_path.endswith(".strm"):
            local_path = str(local.parent)
        best_name = ""
        best_length = -1
        for name, locations in self._media_library_map():
            for location in locations:
                base = location.rstrip("/")
                if not base:
                    continue
                if local_path == base or local_path.startswith(base + "/"):
                    if len(base) > best_length:
                        best_name, best_length = name, len(base)
        return best_name

    def _strm_roots(self) -> Tuple[str, str]:
        """
        读取本地 STRM 根目录与对应的网盘根目录。

        :return: (本地根, 网盘根)
        """
        local_root = (self._strm_local_root or "").rstrip("/")
        pan_root = (self._strm_pan_root or "").rstrip("/")
        return local_root, pan_root

    def _strm_target(self, dest: str) -> Optional[Path]:
        """
        把整理目标路径映射为本地应出现的 STRM 路径。

        :param dest: 整理目标路径（网盘路径或本地路径）
        :return: 对应的 .strm 路径；无法映射时为 None
        """
        if not dest:
            return None
        local_root, pan_root = self._strm_roots()
        if not local_root or not pan_root:
            # 未配置目录映射时不猜路径，避免误报「未检测到 STRM」
            return None
        path = dest
        if pan_root and (path == pan_root or path.startswith(pan_root + "/")):
            path = local_root + path[len(pan_root):]
        elif not path.startswith(local_root):
            return None
        target = Path(path)
        if target.suffix.lower() in STRM_MEDIA_SUFFIXES:
            return target.with_suffix(".strm")
        return target

    def _reply(self, chat_id: Any, text: str, message_id: Any = None) -> None:
        """
        向指定会话发送纯文本回复。

        :param chat_id: 目标会话 ID
        :param text: 回复正文
        :param message_id: 需要引用回复的原消息 ID，可为空
        """
        self._send_text(chat_id, text, message_id)

    def _send_text(
        self,
        chat_id: Any,
        text: str,
        message_id: Any = None,
        keyboard: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        """
        发送纯文本消息并返回 Telegram 侧的消息 ID。

        :param chat_id: 目标会话 ID
        :param text: 正文
        :param message_id: 需要引用回复的原消息 ID，可为空
        :param keyboard: 内联键盘结构，可为空
        :return: 新消息 ID；发送失败或未配置会话时为 None
        """
        if chat_id is None:
            return None
        body: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text[:TEXT_LIMIT],
            "disable_web_page_preview": True,
        }
        if message_id:
            body["reply_to_message_id"] = message_id
        if keyboard:
            body["reply_markup"] = keyboard
        try:
            response = requests.post(
                self._bot_url("sendMessage"),
                json=body,
                timeout=20,
                proxies=self._telegram_proxies(),
            )
            payload = self._parse_bot_response(response)
            result = payload.get("result") or {}
            sent_id = result.get("message_id")
            return int(sent_id) if sent_id is not None else None
        except Exception as err:  # noqa: BLE001 - 回执失败只记日志
            logger.error(f"【TG磁力离线】发送回复失败：{self._mask_secrets(str(err))}")
            return None

    def _edit_text(self, chat_id: Any, message_id: Any, text: str) -> None:
        """
        编辑已发送的消息，用于更新分批提交进度。

        :param chat_id: 目标会话 ID
        :param message_id: 待编辑的消息 ID
        :param text: 新正文
        """
        if chat_id is None or not message_id:
            return
        try:
            response = requests.post(
                self._bot_url("editMessageText"),
                json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": text[:TEXT_LIMIT],
                    "disable_web_page_preview": True,
                },
                timeout=20,
                proxies=self._telegram_proxies(),
            )
            self._parse_bot_response(response)
        except Exception as err:  # noqa: BLE001 - 进度更新失败不影响继续提交
            if "not modified" not in str(err):
                logger.error(
                    f"【TG磁力离线】更新进度消息失败：{self._mask_secrets(str(err))}"
                )

    # ============================ 辅助方法 ============================

    def _help_text(self) -> str:
        """
        构造帮助文本。

        :return: 帮助说明
        """
        current = self._target_display()
        return (
            "用法：直接发送磁力 / ed2k / 种子链接，可一次发多条。\n"
            f"当前目标目录：{current}\n"
            f"分批策略：每批 {self._batch_size} 条，间隔 {self._batch_interval} 秒"
            "（超过自动分批，防 115 风控）\n\n"
            "整理前：先查媒体库，已存在则通知并跳过；"
            "MP 与 AI 识别不一致时发按钮选择按哪边整理。\n\n"
            "指令：\n"
            "/dir　　　　查看目标目录\n"
            "/dir <路径>　改为指定 115 目录（覆盖自动解析）\n"
            "/dir default　恢复自动解析（离线目录/专属子目录）\n"
            "/allow <ID|@用户名>　加入白名单\n"
            "/deny <ID|@用户名>　 移出白名单\n"
            "/status　　　运行状态\n"
            "群组：把群 ID（负数）加入白名单后，群成员可直接发链接；"
            "群内管理指令仅白名单成员可用。\n"
            "群内如需机器人看到普通消息，请在 @BotFather 用 /setprivacy 关闭隐私模式。\n"
            "本 bot 只有离线下载能力，没有其它管理功能。"
        )

    def _status_text(self) -> str:
        """
        构造状态文本。

        :return: 状态说明
        """
        running = bool(self._thread and self._thread.is_alive())
        lines = [
            f"运行状态：{'运行中' if running else '未运行'}",
            f"bot：@{self._bot_username or '未知'}",
            f"白名单：{len(self._allowed_users)} 人（{'、'.join(sorted(self._allowed_users))}）",
            f"目标目录：{self._target_display()}",
            f"分批策略：每批 {self._batch_size} 条 / 间隔 {self._batch_interval} 秒",
            f"启动时间：{self._started_at or '未知'}",
        ]
        if self._last_error:
            lines.append(f"最近错误：{self._last_error}")
        if self._last_result:
            lines.append(f"最近结果：{self._last_result}")
        return "\n".join(lines)

    def _append_history(self, kind: str, detail: str) -> None:
        """
        追加一条运行记录，仅在内存保留最近若干条。

        :param kind: 结果类型，如 成功 / 失败 / 已忽略
        :param detail: 详情文本
        """
        entry = {
            "time": datetime.now().strftime("%m-%d %H:%M:%S"),
            "kind": kind,
            "detail": str(detail)[:200],
        }
        with self._lock:
            self._history.append(entry)
            self._history = self._history[-HISTORY_LIMIT:]

    def _persist_state(self) -> None:
        """
        持久化轮询游标与运行记录，避免重启后重复消费消息。
        """
        try:
            self.save_data("offset", self._offset)
            self.save_data("history", self._history[-HISTORY_LIMIT:])
        except Exception as err:  # noqa: BLE001 - 持久化失败不影响收消息
            logger.error(f"【TG磁力离线】保存运行状态失败：{err}")

    def _read_data(self, key: str) -> Any:
        """
        读取插件运行数据，异常时返回 None。

        :param key: 数据键
        :return: 数据值或 None
        """
        try:
            return self.get_data(key)
        except Exception as err:  # noqa: BLE001 - 数据损坏不应阻止插件启动
            logger.error(f"【TG磁力离线】读取数据 {key} 失败：{err}")
            return None

    def _persist_config_async(self, **changes: Any) -> None:
        """
        在独立线程中写回插件配置，避免轮询线程内触发配置重载时自阻塞。

        :param changes: 需要合并进配置的字段
        """
        def writer() -> None:
            """执行配置写回。"""
            try:
                # 串行化读-改-写：并发写回会互相覆盖，丢掉其中一个改动
                with self._config_write_lock:
                    config = self.get_config() or {}
                    config.update(changes)
                    self.update_config(config)
            except Exception as err:  # noqa: BLE001 - 配置写回失败只记日志
                logger.error(f"【TG磁力离线】写回配置失败：{err}")

        threading.Thread(target=writer, name="TgOfflineBot-Config", daemon=True).start()

    def _api_token_value(self) -> str:
        """
        解析调用本机 API 使用的 Token。

        :return: 优先插件配置，其次宿主 API_TOKEN 与环境变量
        """
        if self._api_token:
            return self._api_token
        return str(self._host_setting("API_TOKEN", "") or "").strip()

    def _host_setting(self, name: str, default: Any = None) -> Any:
        """
        读取宿主运行时设置，缺失时回退环境变量。

        :param name: 设置名
        :param default: 都取不到时的默认值
        :return: 设置值
        """
        if _host_settings is not None:
            value = getattr(_host_settings, name, None)
            if value not in (None, ""):
                return value
        import os

        env_value = os.getenv(name)
        return env_value if env_value not in (None, "") else default

    @staticmethod
    def _clamp_int(raw: Any, default: int, low: int, high: int) -> int:
        """
        解析整数配置并夹取到允许区间。

        :param raw: 配置原值，可能是字符串、数字或空
        :param default: 无法解析时的默认值
        :param low: 允许的最小值
        :param high: 允许的最大值
        :return: 夹取后的整数
        """
        if raw in (None, ""):
            return default
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            return default
        return max(low, min(high, value))

    @staticmethod
    def _normalize_path(raw: Any) -> str:
        """
        规范化 115 目标目录。

        :param raw: 原始配置值
        :return: 以 / 开头、结尾无斜杠的路径；无效时为空字符串
        """
        text = str(raw or "").strip()
        if not text:
            return ""
        if not text.startswith("/"):
            text = "/" + text
        text = re.sub(r"/{2,}", "/", text).rstrip("/")
        return text if text and text != "/" else ""
