#!/usr/bin/env python3
"""
综艺正片守卫（VarietyGuard）MoviePilot 插件。

整理综艺整季包时，按关键词只保留正片、静默跳过非正片（先导 / 纯享 / 花絮 / 加更 / 预告等）。

为什么需要它
------------
综艺整季包常把「正片」与衍生内容放在同一个包里，且**共用同一集号**
（例如先导片与正片都叫 E01）。整理链只认集号，结果就是非正片顶掉正片，
或两个文件争同一个目标名。

为什么不用另外两条路（2026-10-08 在本机取证）
------------------------------------------
1. 系统设置「转移屏蔽词」``TransferExcludeWords``：全局按**完整文件路径**做正则匹配
   （``app/chain/transfer/history.py::_is_blocked_by_exclude_words``），
   无法按媒体类型限定 → 会误伤剧集、电影里同名的「花絮 / 预告」文件。
2. ``transfer.intercept`` 事件的 ``cancel``：宿主把它当**整理失败**
   （``app/chain/transfer/settlement.py`` 写失败历史 + 无条件发送失败通知），
   在开启「文件整理失败智能接管」的实例上会触发接管重试循环。

因此本插件走**计划阶段过滤**：包装 ``TransHandler.plan_transfer``，
在候选清单 ``TransferPlanCheckpoint.items`` 生成之后剔除命中的非正片文件。
全部被剔除时，借用宿主既有的 ``skip_reason`` 分支静默跳过
（宿主返回 ``TransferInfo(success=True)``：不写失败历史、不发通知、不进重试）。

安全边界
--------
- **只在配置的媒体范围内生效**（默认：媒体类型=电视剧 且 分类含「综艺」），
  范围外的整理一律原样返回 → 不影响其它媒体类型。
- **只做只读判定**：不移动、不删除、不重命名任何源文件 → 不影响做种。
- **失败即放行**（fail-open）：补丁失配、正则非法、内部异常都只记日志并返回宿主
  的原始计划，绝不阻断正常整理。
- 支持**试运行**：只记录「本应跳过」的文件，不真正过滤，便于先观察再启用。
- **可选词表同步**（配置 ``sync_rule_id``）：插件在加载与保存配置时，把生效词表**整条镜像**
  写入指定的自定义过滤规则 —— 新增与删减都同步、即时生效、不额外消耗模型额度；
  留空即关闭；目标规则不存在或词表异常时只记日志、绝不改动规则。
"""

import dataclasses
import re
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.plugins import _PluginBase
from app.runtime.log import logger
from app.schemas.types import EventType, MessageType
from app.sdk.events import Event, eventmanager

# 可选能力（词表同步）的宿主依赖：缺失时仅关闭该能力，不影响过滤主链。
try:  # pragma: no cover - 宿主版本差异分支
    from app.application.configuration import get_configured_system_config
    from app.application.rules import RuleHelper
    from app.schemas.types import SystemConfigKey
except Exception:  # noqa: BLE001 - 兼容不提供该应用服务的宿主
    get_configured_system_config = None  # type: ignore[assignment]
    RuleHelper = None  # type: ignore[assignment]
    SystemConfigKey = None  # type: ignore[assignment]

try:  # pragma: no cover - 宿主版本差异分支
    from app.sdk.scheduler import add_plugin_once_job
except Exception:  # noqa: BLE001 - 兼容不提供该调度门面的宿主
    add_plugin_once_job = None  # type: ignore[assignment]

from .version import VERSION

# ============================ 默认配置 ============================

# 默认的非正片关键词（中文原样；纯 ASCII 词在加载时统一加词边界，见 _wrap_boundary）。
# ⚠️ 词边界是必需的：实测本机下载目录里有 70 个文件名含发行组名 MAXPLUS
#    （如 `...60Fps.MAXPLUS.H265...`），裸写 Plus 会把正片一起误伤（2026-10-08 实测）。
# 词表来源：用户 2026-10-08 提供的 RULE4「排除综艺非正片」词表，去重后并入。
#   v1.0.4 起 51 项：+ Extra / EX / 尝鲜篇 / 森林体验篇
#   **v1.0.5 起 60 项**（本版把此前只存在于配置层的 9 个词并入默认词表）：
#     + 超前 / 尊享版 / 陪看 / 直拍 / 探班（S02 观众站 ADWeb 包实测漏网，描述「含加更|特辑|超前」）
#     + 直播 / Live / Pilot / Fancam（S03 观众站 ADWeb 包五类命名：先导.Pilot / 直播.Live / 加更.Plus / 直拍.Fancam / 正片）
#       —— 后三个英文词解决「只写英文标签、无中文词」的漏网（原词表依赖中文标签兜底）
_RAW_DEFAULT_EXCLUDE: Tuple[str, ...] = (
    # —— 中文关键词（无需边界）——
    "纯享",
    "花絮",
    "预告",
    "学院",
    "互动",
    "采访",
    "幕后",
    "会员版",
    "加更",
    "抢先",
    "先导",
    "衍生",
    "见面会",
    "发布会",
    "巅峰",
    "盛典",
    "颁奖",
    "群访",
    "晋级",
    "突围",
    "直击",
    "速看",
    "特别篇",
    "特辑",
    "彩蛋",
    "未播",
    "独家",
    "尝鲜篇",
    "森林体验篇",
    # —— v1.0.5 新增中文词 ——
    "超前",
    "尊享版",
    "陪看",
    "直拍",
    "探班",
    "直播",
    # —— 英文/数字关键词（自动加词边界）——
    "Dinner",
    "Pure",
    "Plus",
    "Teaser",
    "Preview",
    "Behind",
    "Making",
    "Club",
    "Rapid Case",
    "E00",
    "EP00",
    "Battle",
    "Round",
    "Cut",
    "Reaction",
    "Prologue",
    "Epilogue",
    "Start",
    "SP",
    "Detective Club",
    "Extra",
    "EX",
    # —— v1.0.5 新增英文词（自动加词边界；实测 LiveHouse / Pilotlight 不误伤）——
    "Live",
    "Pilot",
    "Fancam",
)


# 刻意不加词边界的 ASCII 关键词（2026-10-08 用户定：站内 E00 / EP00 命名变体较多，
# 需要更宽松的匹配；裸词也会命中 `S01E0012` 这类串，属有意为之）
_BARE_KEYWORDS: frozenset[str] = frozenset({"E00", "EP00"})


def _wrap_boundary(keyword: str) -> str:
    """给纯 ASCII 关键词加词边界，避免命中 MAXPLUS / StartUp 这类"含词串"。

    - `_BARE_KEYWORDS` 里的词（E00 / EP00）**原样返回**，不加边界；
    - 中文（或含中文）关键词原样返回；
    - 多词短语允许 `.` `_` `-` 空格 作为词间分隔（如 `Rapid Case` 也能命中 `Rapid.Case`）；
    - 大小写不敏感由匹配处的 re.IGNORECASE 保证（与 Plus 一致）。
    """
    if keyword in _BARE_KEYWORDS:
        return keyword
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._-]*", keyword or ""):
        return keyword
    parts = [re.escape(part) for part in re.split(r"[ ._-]+", keyword) if part]
    body = r"[ ._-]*".join(parts)
    return rf"(?<![A-Za-z]){body}(?![A-Za-z])"


# 生效的默认词表（ASCII 关键词已带词边界，E00 / EP00 例外）
DEFAULT_EXCLUDE_KEYWORDS: Tuple[str, ...] = tuple(
    _wrap_boundary(word) for word in _RAW_DEFAULT_EXCLUDE
)

# 默认的正片白名单：命中即保留，优先于排除词（用于「正片花絮」这类混合文件名）。
DEFAULT_ALLOW_KEYWORDS: Tuple[str, ...] = (
    "正片",
)

# 默认作用域：只在「电视剧 + 分类含综艺」时生效
DEFAULT_SCOPE_MEDIA_TYPES: str = "电视剧"
DEFAULT_SCOPE_CATEGORIES: str = "综艺"

# 插件数据键
DATA_KEY_STATS = "stats"
DATA_KEY_RECORDS = "records"
DATA_KEY_RULE_SYNC = "rule_sync"

# 词表同步（可选能力）：一次性任务 ID 与写入前的安全闸区间
RULE_SYNC_JOB_ID = "sync_rule_keywords"
RULE_SYNC_MIN_ITEMS = 10
RULE_SYNC_MAX_ITEMS = 200

# 补丁安装失败时的提示（供详情页显示）
PATCH_STATE_OK = "installed"
PATCH_STATE_FAILED = "failed"


class VarietyGuard(_PluginBase):
    """综艺正片守卫插件。"""

    # 插件元数据
    plugin_name = "综艺正片守卫"
    plugin_desc = "整理综艺整季包时按关键词只保留正片、静默跳过先导/花絮/加更等非正片；不产生整理失败记录，也不影响其它媒体类型。"
    plugin_icon = "varietyguard.png"
    plugin_version = VERSION
    plugin_author = "zkmydgth"
    plugin_config_prefix = "varietyguard_"
    plugin_order = 30
    auth_level = 1

    # 运行状态
    _enabled: bool = False
    _dry_run: bool = True
    _exclude_keywords: List[str] = []
    _allow_keywords: List[str] = []
    _scope_media_types: List[str] = []
    _scope_categories: List[str] = []
    _match_full_path: bool = False
    _notify: bool = False
    _keep_records: int = 200
    _sync_rule_id: str = ""

    # 补丁状态
    _patch_state: str = ""
    _patch_message: str = ""
    _original_plan_transfer: Optional[Any] = None
    _patch_lock = threading.RLock()

    # ==================== 生命周期 ====================

    def init_plugin(self, config: dict = None) -> None:
        """读取配置并按需安装整理计划补丁。"""
        self.stop_service()
        self._reset_runtime_state()
        if not config:
            return
        self._enabled = bool(config.get("enabled"))
        self._dry_run = bool(config.get("dry_run", True))
        self._exclude_keywords = self._parse_keywords(
            config.get("exclude_keywords"), DEFAULT_EXCLUDE_KEYWORDS
        )
        self._allow_keywords = self._parse_keywords(
            config.get("allow_keywords"), DEFAULT_ALLOW_KEYWORDS
        )
        self._scope_media_types = self._split_list(
            config.get("scope_media_types"), DEFAULT_SCOPE_MEDIA_TYPES
        )
        self._scope_categories = self._split_list(
            config.get("scope_categories"), DEFAULT_SCOPE_CATEGORIES
        )
        self._match_full_path = bool(config.get("match_full_path", False))
        self._notify = bool(config.get("notify", False))
        self._keep_records = self._coerce_int(config.get("keep_records"), 200, 0, 5000)
        self._sync_rule_id = str(config.get("sync_rule_id") or "").strip()
        # 词表同步与「是否启用」无关：规则是过滤链的前置条件，两份词表应始终一致。
        self._schedule_rule_sync()

        if not self._enabled:
            logger.info("【综艺正片守卫】插件未启用，不做任何拦截")
            return
        self._install_patch()

    def get_state(self) -> bool:
        """返回插件启用状态。"""
        return self._enabled

    def stop_service(self) -> None:
        """卸载整理计划补丁，避免插件停用后仍生效。"""
        self._remove_patch()

    def _reset_runtime_state(self) -> None:
        """重置运行期字段（不触碰补丁）。"""
        self._patch_state = ""
        self._patch_message = ""

    # ==================== 补丁安装 / 卸载 ====================

    def _install_patch(self) -> None:
        """把过滤包装安装到宿主的整理计划方法上；失败只记录不抛出。"""
        with self._patch_lock:
            if self._original_plan_transfer is not None:
                # 已安装，幂等返回，避免重复包装导致叠加调用
                return
            try:
                from app.modules.filemanager.transhandler import TransHandler
            except Exception as err:
                self._mark_patch_failed(f"导入宿主整理处理器失败：{err}")
                return
            original = getattr(TransHandler, "plan_transfer", None)
            if original is None or not callable(original):
                self._mark_patch_failed("宿主 TransHandler.plan_transfer 不存在，插件不生效")
                return

            def patched_plan_transfer(handler_self: Any, *args: Any, **kwargs: Any) -> Any:
                """宿主整理计划入口的包装：先执行原计划，再按配置过滤候选文件。"""
                checkpoint = original(handler_self, *args, **kwargs)
                return self._filter_checkpoint(checkpoint, args, kwargs)

            patched_plan_transfer.__name__ = getattr(original, "__name__", "plan_transfer")
            patched_plan_transfer.__doc__ = getattr(original, "__doc__", None)
            try:
                TransHandler.plan_transfer = patched_plan_transfer
            except Exception as err:
                self._mark_patch_failed(f"替换宿主方法失败：{err}")
                return
            self._original_plan_transfer = original
            self._patch_state = PATCH_STATE_OK
            self._patch_message = "已安装整理计划过滤补丁"
            logger.info(
                "【综艺正片守卫】已安装计划过滤（作用域：媒体类型=%s 分类=%s，试运行=%s）",
                self._scope_media_types or "全部",
                self._scope_categories or "全部",
                self._dry_run,
            )

    def _remove_patch(self) -> None:
        """还原宿主方法；未安装时为空操作。"""
        with self._patch_lock:
            original = self._original_plan_transfer
            self._original_plan_transfer = None
            if original is None:
                return
            try:
                from app.modules.filemanager.transhandler import TransHandler

                current = getattr(TransHandler, "plan_transfer", None)
                # 只在方法仍为本插件包装时才还原，避免覆盖其它插件的补丁
                if current is not None and getattr(current, "__closure__", None):
                    TransHandler.plan_transfer = original
                    logger.info("【综艺正片守卫】已卸载计划过滤补丁")
                else:
                    logger.warning("【综艺正片守卫】宿主方法已被其它补丁接管，跳过还原")
            except Exception as err:
                logger.error("【综艺正片守卫】卸载补丁失败：%s", err)
            self._patch_state = ""
            self._patch_message = ""

    def _mark_patch_failed(self, message: str) -> None:
        """记录补丁未生效的原因（不抛出，保证宿主照常整理）。"""
        self._patch_state = PATCH_STATE_FAILED
        self._patch_message = message
        logger.error("【综艺正片守卫】补丁未生效：%s", message)

    # ==================== 核心过滤逻辑 ====================

    def _filter_checkpoint(
        self, checkpoint: Any, args: Tuple[Any, ...], kwargs: Dict[str, Any]
    ) -> Any:
        """按配置过滤整理计划中的候选文件；异常一律放行原计划。"""
        try:
            return self._filter_checkpoint_inner(checkpoint, args, kwargs)
        except Exception as err:  # pragma: no cover - 兜底防御，定向测试覆盖
            logger.error("【综艺正片守卫】过滤失败，按原计划放行：%s", err, exc_info=True)
            return checkpoint

    def _filter_checkpoint_inner(
        self, checkpoint: Any, args: Tuple[Any, ...], kwargs: Dict[str, Any]
    ) -> Any:
        """过滤主体：判定作用域 → 逐项匹配关键词 → 重建计划。"""
        items = getattr(checkpoint, "items", None)
        if not items:
            # 单片整理或预览计划：没有逐文件候选，无需处理
            return checkpoint
        mediainfo = self._extract_mediainfo(args, kwargs)
        if not self._in_scope(mediainfo):
            return checkpoint

        kept: List[Any] = []
        skipped: List[Tuple[str, str]] = []
        for item in items:
            text = self._item_text(item)
            if not text:
                kept.append(item)
                continue
            allowed = self._match_first(self._allow_keywords, text)
            if allowed:
                kept.append(item)
                continue
            hit = self._match_first(self._exclude_keywords, text)
            if hit:
                skipped.append((text, hit))
            else:
                kept.append(item)

        if not skipped:
            return checkpoint

        title = self._media_title(mediainfo)
        if self._dry_run:
            self._record(title, skipped, applied=False)
            logger.info(
                "【综艺正片守卫】试运行：%s 命中 %d 个非正片候选（未过滤）：%s",
                title,
                len(skipped),
                "、".join(name for name, _ in skipped[:5]),
            )
            return checkpoint

        if not kept:
            # 全为非正片：交给宿主既有的「无候选」静默跳过分支
            new_checkpoint = dataclasses.replace(
                checkpoint, items=(), skip_reason=f"综艺非正片已全部跳过（{len(skipped)} 个文件）"
            )
        else:
            resequenced = [
                dataclasses.replace(item, sequence=index) for index, item in enumerate(kept)
            ]
            new_checkpoint = dataclasses.replace(checkpoint, items=tuple(resequenced))

        self._record(title, skipped, applied=True)
        logger.info(
            "【综艺正片守卫】%s 跳过 %d 个非正片文件、保留 %d 个正片文件",
            title,
            len(skipped),
            len(kept),
        )
        self._notify_skip(title, skipped)
        return new_checkpoint

    def _extract_mediainfo(
        self, args: Tuple[Any, ...], kwargs: Dict[str, Any]
    ) -> Optional[Any]:
        """从调用参数里取出媒体信息对象（关键字优先，其次按鸭子类型扫描）。"""
        mediainfo = kwargs.get("mediainfo")
        if mediainfo is not None:
            return mediainfo
        for candidate in args:
            if hasattr(candidate, "type") and (
                hasattr(candidate, "library_category") or hasattr(candidate, "category")
            ):
                return candidate
        return None

    def _in_scope(self, mediainfo: Optional[Any]) -> bool:
        """判断本次整理是否落在插件作用域内（默认只认电视剧 + 综艺分类）。"""
        if mediainfo is None:
            return False
        if self._scope_media_types:
            media_type = getattr(getattr(mediainfo, "type", None), "value", None)
            if media_type is None:
                media_type = getattr(mediainfo, "type", None)
            if str(media_type or "") not in self._scope_media_types:
                return False
        if self._scope_categories:
            blob = " ".join(
                str(getattr(mediainfo, field, "") or "")
                for field in ("library_category", "category", "metadata_category")
            )
            return any(keyword and keyword in blob for keyword in self._scope_categories)
        return True

    def _item_text(self, item: Any) -> str:
        """取出用于匹配的文本：默认文件名，可选完整路径。"""
        source = getattr(item, "source_fileitem", None)
        if isinstance(source, dict):
            name = str(source.get("name") or source.get("basename") or "")
            if self._match_full_path:
                return str(source.get("path") or name)
            return name or str(source.get("path") or "")
        return str(getattr(item, "target_path", "") or "")

    @staticmethod
    def _media_title(mediainfo: Optional[Any]) -> str:
        """拼出用于日志与统计的媒体标题。"""
        if mediainfo is None:
            return "未知媒体"
        title = str(getattr(mediainfo, "title", "") or "")
        year = str(getattr(mediainfo, "year", "") or "")
        return f"{title} ({year})" if title and year else (title or "未知媒体")

    def _match_first(self, patterns: Iterable[str], text: str) -> Optional[str]:
        """返回首个命中的模式；非法正则按不命中处理。"""
        for pattern in patterns:
            if not pattern:
                continue
            try:
                if re.search(pattern, text, re.IGNORECASE):
                    return pattern
            except re.error as err:
                logger.warning("【综艺正片守卫】关键词不是合法正则，已跳过：%s（%s）", pattern, err)
        return None

    # ==================== 记录与通知 ====================

    def _record(self, title: str, skipped: List[Tuple[str, str]], applied: bool) -> None:
        """累计统计并保留最近若干条跳过记录。"""
        try:
            stats = self.get_data(DATA_KEY_STATS) or {}
            if not isinstance(stats, dict):
                stats = {}
            stats["total_skipped"] = int(stats.get("total_skipped") or 0) + len(skipped)
            stats["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
            stats["dry_run"] = self._dry_run
            by_keyword = stats.get("by_keyword")
            if not isinstance(by_keyword, dict):
                by_keyword = {}
            for _, keyword in skipped:
                by_keyword[keyword] = int(by_keyword.get(keyword) or 0) + 1
            stats["by_keyword"] = by_keyword
            self.save_data(DATA_KEY_STATS, stats)

            if self._keep_records <= 0:
                return
            records = self.get_data(DATA_KEY_RECORDS) or []
            if not isinstance(records, list):
                records = []
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            for name, keyword in skipped:
                records.insert(
                    0,
                    {
                        "time": stamp,
                        "title": title,
                        "keyword": keyword,
                        "name": name,
                        "applied": applied,
                    },
                )
            self.save_data(DATA_KEY_RECORDS, records[: self._keep_records])
        except Exception as err:  # pragma: no cover - 统计失败不得影响整理
            logger.error("【综艺正片守卫】记录统计失败：%s", err)

    def _notify_skip(self, title: str, skipped: List[Tuple[str, str]]) -> None:
        """按需发送跳过通知（仅真正过滤时发送）。"""
        if not self._notify or not skipped:
            return
        try:
            lines = [f"- {name}（命中：{keyword}）" for name, keyword in skipped[:10]]
            more = f"\n…另有 {len(skipped) - 10} 个文件" if len(skipped) > 10 else ""
            self.post_message(
                mtype=MessageType.Plugin,
                title="【综艺正片守卫】已跳过非正片文件",
                text=f"媒体：{title}\n跳过 {len(skipped)} 个非正片文件：\n" + "\n".join(lines) + more,
            )
        except Exception as err:  # pragma: no cover - 通知失败不得影响整理
            logger.error("【综艺正片守卫】发送通知失败：%s", err)

    # ==================== 配置解析工具 ====================

    @classmethod
    def _parse_keywords(cls, raw: Any, default: Tuple[str, ...]) -> List[str]:
        """解析关键词配置：支持换行/逗号分隔的字符串与列表。"""
        values = cls._split_list(raw, ",".join(default))
        return [value for value in values if value]

    @staticmethod
    def _split_list(raw: Any, default: str = "") -> List[str]:
        """把配置值归一化为字符串列表（兼容表单可能回传对象/列表）。"""
        if raw is None:
            raw = default
        if isinstance(raw, (list, tuple)):
            # 列表必须逐项归一化：整体套 _coerce_scalar 会被折叠成首项
            items = [VarietyGuard._coerce_scalar(item) for item in raw]
        else:
            text = str(VarietyGuard._coerce_scalar(raw))
            items = re.split(r"[\n,，;；]+", text)
        return [str(item).strip() for item in items if str(item).strip()]

    @staticmethod
    def _coerce_scalar(value: Any) -> Any:
        """把下拉等组件回传的对象归一化为标量（值形态可能是 {"title","value"}）。"""
        if isinstance(value, dict):
            for key in ("value", "title", "text"):
                if key in value:
                    return VarietyGuard._coerce_scalar(value[key])
            return ""
        if isinstance(value, (list, tuple)):
            for item in value:
                coerced = VarietyGuard._coerce_scalar(item)
                if coerced not in (None, ""):
                    return coerced
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        return value

    @staticmethod
    def _coerce_int(value: Any, default: int, minimum: int, maximum: int) -> int:
        """把配置值归一化为区间内的整数。"""
        try:
            number = int(VarietyGuard._coerce_scalar(value))
        except (TypeError, ValueError):
            return default
        return max(minimum, min(maximum, number))

    # ==================== 词表同步（可选能力） ====================

    def _schedule_rule_sync(self) -> None:
        """安排一次词表镜像同步；未配置规则 ID 或宿主无调度门面时只记日志。"""
        if not self._sync_rule_id:
            return
        if add_plugin_once_job is None:
            logger.warning(
                "【综艺正片守卫】宿主未提供调度门面，无法同步词表到 %s", self._sync_rule_id
            )
            return
        scheduled = add_plugin_once_job(
            self.__class__.__name__,
            RULE_SYNC_JOB_ID,
            self._sync_rule_keywords,
            "同步非正片词表到自定义过滤规则",
            0,
        )
        if not scheduled:
            logger.warning(
                "【综艺正片守卫】调度器未运行，本次跳过词表同步（下次保存配置会自动重试）"
            )

    async def _sync_rule_keywords(self) -> None:
        """把生效词表整条镜像写入目标自定义过滤规则（任何异常都只记日志）。"""
        rule_id = self._sync_rule_id
        if not rule_id:
            return
        try:
            desired = "|".join(self._exclude_keywords)
            rejected = self._rule_sync_guard(desired)
            if rejected:
                logger.error("【综艺正片守卫】词表同步已跳过（安全闸）：%s", rejected)
                return
            if RuleHelper is None or SystemConfigKey is None or get_configured_system_config is None:
                logger.warning(
                    "【综艺正片守卫】宿主未提供规则服务，无法同步词表到 %s", rule_id
                )
                return
            rules = [rule.model_dump(exclude_none=True) for rule in RuleHelper.get_custom_rules()]
            target = next((rule for rule in rules if rule.get("id") == rule_id), None)
            if target is None:
                logger.warning(
                    "【综艺正片守卫】自定义过滤规则 %s 不存在，词表同步已跳过", rule_id
                )
                return
            current = str(target.get("exclude") or "")
            if current == desired:
                return  # 已一致：静默
            updated = [
                dict(rule, exclude=desired) if rule.get("id") == rule_id else rule
                for rule in rules
            ]
            await get_configured_system_config().async_set_with_normalized_value(
                SystemConfigKey.CustomFilterRules, updated
            )
            added, removed = self._diff_rule_keywords(current, desired)
            self.save_data(
                DATA_KEY_RULE_SYNC,
                {
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "rule_id": rule_id,
                    "count": len(self._exclude_keywords),
                    "added": added,
                    "removed": removed,
                },
            )
            logger.info(
                "【综艺正片守卫】已同步 %d 项非正片词到过滤规则 %s（新增 %d、删除 %d）",
                len(self._exclude_keywords),
                rule_id,
                len(added),
                len(removed),
            )
        except Exception as err:  # noqa: BLE001 - 同步失败必须 fail-open
            logger.error(
                "【综艺正片守卫】词表同步失败（已忽略，不影响过滤）：%s", err, exc_info=True
            )

    def _rule_sync_guard(self, desired: str) -> str:
        """校验待写入的规则文本；返回非空字符串表示应当跳过本次同步。"""
        items = self._exclude_keywords
        if not RULE_SYNC_MIN_ITEMS <= len(items) <= RULE_SYNC_MAX_ITEMS:
            return (
                f"词表项数 {len(items)} 超出安全区间 "
                f"[{RULE_SYNC_MIN_ITEMS}, {RULE_SYNC_MAX_ITEMS}]"
            )
        if any("|" in item or "\n" in item for item in items):
            return "词表存在含 | 或换行的条目（会破坏规则的单个正则结构）"
        try:
            re.compile(desired)
        except re.error as err:
            return f"拼接后的正则不合法：{err}"
        return ""

    @staticmethod
    def _diff_rule_keywords(current: str, desired: str) -> Tuple[List[str], List[str]]:
        """比较规则前后文本，返回（新增项、删除项）。"""
        before = {item for item in current.split("|") if item}
        after = {item for item in desired.split("|") if item}
        added = [item for item in after if item not in before]
        removed = [item for item in before if item not in after]
        return added, removed

    # ==================== 命令 ====================

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """注册远程命令：查询统计与重置统计。"""
        return [
            {
                "cmd": "/varietyguard_status",
                "event": EventType.PluginAction,
                "desc": "查看综艺正片守卫统计",
                "category": "插件",
                "data": {"action": "varietyguard_status"},
            },
            {
                "cmd": "/varietyguard_reset",
                "event": EventType.PluginAction,
                "desc": "重置综艺正片守卫统计",
                "category": "插件",
                "data": {"action": "varietyguard_reset"},
            },
        ]

    @eventmanager.register(EventType.PluginAction)
    def on_plugin_action(self, event: Event) -> None:
        """处理插件命令：输出统计或清空统计。"""
        event_data = getattr(event, "event_data", None) or {}
        action = event_data.get("action") if isinstance(event_data, dict) else None
        if action == "varietyguard_status":
            self.post_message(
                mtype=MessageType.Plugin,
                title="【综艺正片守卫】运行统计",
                text=self._stats_text(),
            )
        elif action == "varietyguard_reset":
            self.save_data(DATA_KEY_STATS, {})
            self.save_data(DATA_KEY_RECORDS, [])
            self.post_message(
                mtype=MessageType.Plugin,
                title="【综艺正片守卫】统计已重置",
                text="跳过统计与记录已清空。",
            )

    def _stats_text(self) -> str:
        """生成统计文本（供命令与详情页复用）。"""
        stats = self.get_data(DATA_KEY_STATS) or {}
        if not isinstance(stats, dict):
            stats = {}
        by_keyword = stats.get("by_keyword") if isinstance(stats.get("by_keyword"), dict) else {}
        detail = "、".join(f"{k}×{v}" for k, v in list(by_keyword.items())[:10]) or "无"
        return (
            f"启用：{'是' if self._enabled else '否'}\n"
            f"试运行：{'是' if self._dry_run else '否'}\n"
            f"补丁状态：{self._patch_state or '未安装'}"
            f"{('（' + self._patch_message + '）') if self._patch_message else ''}\n"
            f"累计跳过：{stats.get('total_skipped') or 0} 个文件\n"
            f"最近执行：{stats.get('last_run') or '无'}\n"
            f"关键词命中：{detail}"
        )

    # ==================== API ====================

    def get_api(self) -> List[Dict[str, Any]]:
        """本插件不额外暴露 HTTP 接口。"""
        return []

    # ==================== 配置表单 ====================

    def get_form(self) -> Tuple[Optional[List[dict]], Dict[str, Any]]:
        """返回 Vuetify JSON 配置表单与默认值。"""
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
                                            "class": "mb-3",
                                            "text": (
                                                "整理综艺整季包时只保留正片、静默跳过非正片。"
                                                "插件只在「媒体类型=电视剧 且 分类含综艺」的整理中生效，"
                                                "不产生整理失败记录，也不移动或删除源文件（不影响做种）。"
                                                "首次使用建议先开试运行，观察日志确认命中范围后再关闭。"
                                            ),
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
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
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "dry_run",
                                            "label": "试运行（只记录，不过滤）",
                                            "persistent-hint": True,
                                            "hint": "开启后仅在日志与统计里记录命中的非正片文件，不实际跳过。",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "notify",
                                            "label": "跳过时发送通知",
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
                                            "type": "success",
                                            "variant": "tonal",
                                            "class": "mb-3",
                                            "text": "匹配规则",
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
                                            "model": "exclude_keywords",
                                            "label": "非正片关键词（每行一个，支持正则）",
                                            "rows": 6,
                                            "persistent-hint": True,
                                            "hint": "命中即跳过（正则、忽略大小写）。内置默认 60 项：中文原样匹配，英文词自动包成「非字母边界」（多词短语可用 . _ - 空格分隔），E00 / EP00 刻意不加边界（站内变体较多，属有意为之）。⚠️ 自己新增的英文词不会自动加边界，若要防误伤请照写 (?<![A-Za-z])Word(?![A-Za-z])。",
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
                                            "model": "allow_keywords",
                                            "label": "正片白名单（每行一个，优先级更高）",
                                            "rows": 6,
                                            "persistent-hint": True,
                                            "hint": "命中白名单的文件一律保留，用于「正片花絮」这类混合命名。",
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
                                            "type": "success",
                                            "variant": "tonal",
                                            "class": "mb-3",
                                            "text": "作用范围",
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
                                            "model": "scope_media_types",
                                            "label": "生效媒体类型（逗号分隔）",
                                            "persistent-hint": True,
                                            "hint": "默认「电视剧」。留空表示不限类型。",
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
                                            "model": "scope_categories",
                                            "label": "生效媒体分类（逗号分隔）",
                                            "persistent-hint": True,
                                            "hint": "默认「综艺」。只有分类包含该词的整理才会被过滤，其它内容一律放行。",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "match_full_path",
                                            "label": "匹配完整路径（默认只匹配文件名）",
                                            "persistent-hint": True,
                                            "hint": "整季包目录名常含「先导」等词，开启后会连正片一起跳过，谨慎使用。",
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
                                            "model": "keep_records",
                                            "label": "保留记录条数（0 表示不保留）",
                                            "persistent-hint": True,
                                            "hint": "仅影响详情页展示与统计记录，不影响过滤行为。",
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
                                            "type": "success",
                                            "variant": "tonal",
                                            "class": "mb-3",
                                            "text": "词表同步（可选）",
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
                                            "model": "sync_rule_id",
                                            "label": "同步到自定义过滤规则（rule_id，留空=关闭）",
                                            "persistent-hint": True,
                                            "hint": "填写自定义过滤规则的 ID（如 RULE4）：插件在加载与保存配置时，把自己的非正片词表（含内置默认）整条镜像写入该规则的「排除」项 —— 新增与删减都会同步、即时生效、不消耗模型额度。留空则完全不动作；规则不存在、词表项数超出 10~200、或拼接后的正则不合法时，只记日志、不改动规则。",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                ],
            }
        ], {
            "enabled": False,
            "dry_run": True,
            "notify": False,
            "exclude_keywords": "\n".join(DEFAULT_EXCLUDE_KEYWORDS),
            "allow_keywords": "\n".join(DEFAULT_ALLOW_KEYWORDS),
            "scope_media_types": DEFAULT_SCOPE_MEDIA_TYPES,
            "scope_categories": DEFAULT_SCOPE_CATEGORIES,
            "match_full_path": False,
            "keep_records": 200,
            "sync_rule_id": "",
        }

    # ==================== 详情页 ====================

    def get_page(self) -> Optional[List[dict]]:
        """返回插件详情页（统计与最近记录）。"""
        records = self.get_data(DATA_KEY_RECORDS) or []
        if not isinstance(records, list):
            records = []
        rows = []
        for record in records[:20]:
            if not isinstance(record, dict):
                continue
            rows.append(
                {
                    "component": "VAlert",
                    "props": {
                        "type": "warning" if record.get("applied") else "info",
                        "variant": "tonal",
                        "class": "mb-3",
                        "text": (
                            f"{record.get('time')}｜{record.get('title')}｜"
                            f"命中「{record.get('keyword')}」｜{record.get('name')}"
                            + ("" if record.get("applied") else "（试运行，未过滤）")
                        ),
                    },
                }
            )
        return [
            {
                "component": "VAlert",
                "props": {
                    "type": "info" if self._patch_state == PATCH_STATE_OK else "warning",
                    "variant": "tonal",
                    "class": "mb-3",
                    "text": self._stats_text(),
                },
            },
            {
                "component": "VAlert",
                "props": {
                    "type": "info",
                    "variant": "tonal",
                    "class": "mb-3",
                    "text": (
                        "查看统计：远程命令 /varietyguard_status；"
                        "重置统计：远程命令 /varietyguard_reset。"
                    ),
                },
            },
            *rows,
        ]
