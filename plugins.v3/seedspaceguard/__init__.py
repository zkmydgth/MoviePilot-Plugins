#!/usr/bin/env python3
"""
保种空间守护（SeedSpaceGuard）MoviePilot 插件。

存储空间不足时，自动清理 PT 保种目录中「保种最久」的资源，避免触发 H&R。

- 支持多目录：每行一个路径，可同时填写「下载目录」与「媒体库目录」等；
- 种子级模式：直接调用 qBittorrent/Transmission 下载器，按种子真实添加时间
  从早到晚删除种子（连带删除文件），一步到位不留红种；
- 仅文件模式：按文件修改时间从旧到新删除文件。下载目录与媒体库目录中的同名
  文件常为同一 inode 的硬链接，插件按 (st_dev, st_ino) 自动识别并**双侧一并删除**，
  无需依赖其它插件联动。
- 安全兜底：每轮删除后实测空间释放量，若空间几乎未释放（如硬链接仍有残留引用、
  快照占用），立即停止并告警，宁可空间不足也不过量删除。
- 清理范围：**除「保护文件后缀」命中的文件外，配置目录下所有文件均纳入清理候选**，
  不区分文件类型（视频、nfo、图片、字幕等一视同仁）。需要保留的文件请写入保护后缀。
- 联动清理：可选的「删除种子 / 删除转移记录」两项联动，由本插件主动执行，
  无需依赖其它插件。其中仅文件模式删除种子有严格前置条件：
  **必须该种子下的所有文件都已删除**，任一文件仍在磁盘上（含被保护后缀跳过的）
  即保留种子。
"""

import os
import re
import shutil
import stat
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from apscheduler.triggers.cron import CronTrigger
from fastapi import Request

from app.sdk.events import Event, eventmanager
from app.application.downloader import DownloaderHelper
from app.db.oper.downloadhistory import DownloadHistoryOper
from app.db.oper.transferhistory import TransferHistoryOper
from app.runtime.log import logger
from app.plugins import _PluginBase
from app.schemas.types import DownloaderType, EventType, MessageType

from .version import VERSION


# ============================ 释放校验常量 ============================

# 实测释放量 / 名义释放量 的达标比例：删除期间下载器与媒体库几乎必然有写入，
# 留 10% 余量吸收噪声；不能设为 1.0，否则永远判为「未达标」
RELEASE_TOLERANCE: float = 0.9
# 「部分释放」容忍轮数：释放缓慢（如 Btrfs 延迟分配）时最多容忍几轮，
# 超过即停止，避免在空间迟迟不回收的情况下持续扩张删除范围
MAX_STALL_ROUNDS: int = 2
# 释放轮询间隔（秒）：达标即提前退出，无需等到 sync_wait_seconds 上限
RELEASE_POLL_SECONDS: int = 5
# 名义释放量低于该值时跳过「未释放即停止」硬判定，避免小文件被测量噪声误伤
RELEASE_HARD_STOP_MIN_BYTES: int = 1024 ** 3
GIB: int = 1024 ** 3
# 空壳判定的目录扫描上限：超过该条目数即视为「扫不完」，保守判定为「有文件」。
# 设上限是为了防御异常巨大的目录，绝不允许因扫不完而把有内容的种子判成空壳。
DIR_SCAN_MAX_ENTRIES: int = 5000


class SeedSpaceGuard(_PluginBase):
    """
    保种空间守护插件。
    """

    # 插件元数据
    plugin_name = "保种空间守护"
    plugin_desc = ("存储空间不足时自动清理保种目录中「保种最久」的资源（种子+文件），"
                   "避免 H&R。支持种子级删除与仅文件两种模式，可限定目标下载器；"
                   "除保护后缀外所有文件均纳入清理，可选联动删除种子与转移记录。")
    plugin_version = VERSION
    plugin_author = "zkmydgth"
    plugin_config_prefix = "seedspaceguard_"
    plugin_order = 100
    auth_level = 1

    # 运行状态
    _enabled: bool = False
    # 保种/清理目录列表（由多行文本解析而来，已去重并剔除嵌套目录）
    _target_dirs: List[str] = []
    # 当前生效的目录集合（_target_dirs 中实际存在的那部分）
    _active_dirs: List[str] = []
    # 当前待删文件的 inode 索引：(st_dev, st_ino) -> 该文件在所有配置目录内的全部路径
    _ino_paths: Dict[Tuple[int, int], List[str]] = {}
    _volume_path: str = "/volume1"
    _threshold_gb: int = 500
    _cron: str = "0 */6 * * *"
    _recent_skip_days: int = 1
    _mode: str = "seed"
    _protect_pattern: str = "*.part|*.!qb|*.download|*.aria2|*.tmp|*.crdownload"

    # Synology DSM 媒体索引目录与其中的元数据文件（删除真实文件后不会自动回收，
    # 会阻止父目录 rmdir，需按白名单甄别后清理，避免误删 @eaDir 下的用户数据）
    _SYNO_META_DIR: str = "@eaDir"
    _SYNO_META_FILE_PREFIXES: Tuple[str, ...] = ("SYNOINDEX", "SYNO_", "SYNOFILE", "THUMB")
    _SYNO_META_FILES: Tuple[str, ...] = ("Thumbs.db", ".DS_Store")
    # 下载器模块「按 hash 查询种子」的方法名。MoviePilot v2 起统一为
    # list_torrents，更早的版本叫 get_torrents（注意 get_torrents 本身是
    # transmission_rpc / qbittorrent-api 客户端实例的方法，不是模块方法）；
    # 基类并不提供任一方法，故只能逐个探测，两者都尝试以兼容不同版本。
    _TORRENT_QUERY_METHODS: Tuple[str, ...] = ("list_torrents", "get_torrents")
    _dry_run: bool = False
    _notify: bool = True
    _downloaders: List[str] = []
    # 真实删除后等待「源文件联动清理」等插件释放媒体库侧硬链接的秒数
    _sync_wait_seconds: int = 90
    # === 联动清理开关（默认全关，保守） ===
    # 删除文件后联动删除对应下载器种子
    _delete_torrents: bool = False
    # 删除文件后清理对应的转移历史记录
    _delete_history: bool = False
    # 种子级模式：删除种子后连带清理媒体库侧硬链接与同内容辅种（默认开）。
    # 仅种子级生效。关闭后恢复旧行为：只删主种子，硬链接与辅种不动。
    _companion_cleanup: bool = True
    # 种子级模式：清理「无主文件」——不被任何种子引用的孤儿硬链接（默认关）。
    # 仅种子级生效。默认关是因为它主动删除用户文件、判定逻辑复杂，需显式开启。
    _orphan_cleanup: bool = False
    # 空壳回收的扫描范围：是否覆盖「内容路径不在监控目录内」的种子（默认关）。
    # 默认关是因为它会把手伸到配置目录之外，属行为边界扩大，须显式授权。
    # ⚠️ 依赖 delete_torrents（联动删除种子）——总闸不开则本开关无任何效果。
    _orphan_seed_scope: bool = False
    # 数据层操作器（懒加载，导入失败时置 None 并降级跳过联动）
    _downloadhis: Optional[DownloadHistoryOper] = None
    _transferhis: Optional[TransferHistoryOper] = None
    _running: bool = False
    _last_result: str = ""
    # 单次清理的聚合计数（文件/转移记录/种子/辅种/无主文件），仅用于构造通知摘要，
    # 不进明细：明细全部写入日志由用户自行查阅
    _clean_stats: dict = {
        "files": 0,
        "transfers": 0,
        "seeds": 0,
        "companions": 0,
        "orphans": 0,
        "stalled": False,
        "dry_run": False,
    }
    _lock: threading.Lock = threading.Lock()

    def init_plugin(self, config: dict = None) -> None:
        """根据插件配置初始化运行状态。"""
        self.stop_service()
        self._enabled = False
        self._target_dirs = []
        self._active_dirs = []
        self._ino_paths = {}
        self._volume_path = "/volume1"
        self._threshold_gb = 500
        self._cron = "0 */6 * * *"
        self._recent_skip_days = 1
        self._mode = "seed"
        self._protect_pattern = "*.part|*.!qb|*.download|*.aria2|*.tmp|*.crdownload"
        self._dry_run = False
        self._notify = True
        self._downloaders = []
        self._sync_wait_seconds = 90
        self._delete_torrents = False
        self._delete_history = False
        self._companion_cleanup = True
        self._orphan_cleanup = False
        self._orphan_seed_scope = False
        if not config:
            return
        self._enabled = bool(config.get("enabled"))
        # 多目录配置：优先读新字段 target_dirs；为空则从旧的单目录 target_dir 迁移
        raw_dirs = config.get("target_dirs")
        if not (isinstance(raw_dirs, str) and raw_dirs.strip()):
            legacy = str(config.get("target_dir") or "").strip()
            if legacy:
                raw_dirs = legacy
                logger.info(
                    "【保种空间守护】检测到旧版单目录配置，自动迁移为多目录格式：%s", legacy
                )
                try:
                    saved = self.get_config() or {}
                    self.update_config(
                        {**saved, "target_dirs": legacy, "target_dir": ""}
                    )
                except Exception as err:
                    # 迁移回写失败不影响本次运行（内存中已生效），下次启动会重试
                    logger.error("【保种空间守护】迁移多目录配置失败：%s", err)
        self._target_dirs = self._parse_dirs(raw_dirs)
        self._volume_path = str(config.get("volume_path") or "/volume1").strip()
        self._threshold_gb = int(config.get("threshold_gb") or 500)
        self._cron = str(config.get("cron") or "0 */6 * * *").strip()
        self._recent_skip_days = max(0, int(config.get("recent_skip_days") or 0))
        self._mode = str(config.get("mode") or "seed").strip() or "seed"
        self._protect_pattern = str(
            config.get("protect_pattern")
            or "*.part|*.!qb|*.download|*.aria2|*.tmp|*.crdownload"
        ).strip()
        self._dry_run = bool(config.get("dry_run"))
        self._notify = bool(config.get("notify"))
        # 联动清理开关：默认关闭，仅显式为真时开启
        self._delete_torrents = bool(config.get("delete_torrents"))
        self._delete_history = bool(config.get("delete_history"))
        # 种子级连带清理（硬链接 + 辅种）：默认开启，仅显式为 False 时关闭。
        # 注意此处刻意不用 bool()——bool(None) 为 False，会让「未配置」变成关闭，
        # 与「默认开」的语义相反。
        self._companion_cleanup = config.get("companion_cleanup", True) is not False
        # 种子级「无主文件」清理：默认**关闭**，仅显式为真时开启。
        # 与上一项相反，这里刻意用 bool()——它主动删除用户文件且判定逻辑
        # 复杂，必须由用户显式勾选，绝不能因「未配置」而默认打开。
        self._orphan_cleanup = bool(config.get("orphan_cleanup"))
        # 空壳回收范围：默认**关闭**，仅显式为真时覆盖监控目录外的种子。
        # 同样用 bool()——它把手伸到配置目录之外，属行为边界扩大，
        # 必须显式授权，绝不可因「未配置」而默认打开。
        self._orphan_seed_scope = bool(config.get("orphan_seed_scope"))
        self._init_linkage_opers()
        # 联动释放等待秒数：0~1800，默认 90
        raw_wait = config.get("sync_wait_seconds")
        try:
            self._sync_wait_seconds = (
                max(0, min(1800, int(raw_wait))) if raw_wait not in (None, "") else 90
            )
        except (TypeError, ValueError):
            self._sync_wait_seconds = 90
        # 目标下载器：VSelect 多选保存为列表，也兼容逗号/空格分隔字符串；空=全部已启用下载器
        raw_dl = config.get("downloaders")
        if isinstance(raw_dl, list):
            self._downloaders = [str(x).strip() for x in raw_dl if str(x).strip()]
        else:
            self._downloaders = [
                item.strip()
                for item in re.split(r"[,，;；\s]+", str(raw_dl or ""))
                if item.strip()
            ]
        self._last_result = ""
        self._running = False
        # 手动触发动作：选中后保存即执行一次，执行后自动复位，避免重复触发
        manual_action = str(config.get("manual_action") or "").strip()
        if manual_action in ("dry", "clean"):
            try:
                saved = self.get_config() or {}
                self.update_config({**saved, "manual_action": ""})
            except Exception as err:
                logger.error("【保种空间守护】复位手动触发配置失败：%s", err)
            action_label = "试运行" if manual_action == "dry" else "正式清理"
            logger.info("【保种空间守护】收到手动触发（%s），后台执行中…", action_label)
            threading.Thread(
                target=self.check_and_clean,
                kwargs={
                    "source": "手动",
                    "dry_run_override": (manual_action == "dry"),
                },
                daemon=True,
            ).start()
        if not self._enabled:
            return
        if not self._target_dirs:
            logger.error("【保种空间守护】未配置清理目录，插件不生效")
        else:
            logger.info(
                "【保种空间守护】插件已启用，监控 %d 个目录：\n%s",
                len(self._target_dirs),
                "\n".join(f"  - {d}" for d in self._target_dirs),
            )

    def get_state(self) -> bool:
        """获取插件启用状态。"""
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """返回插件远程命令列表。"""
        return [
            {
                "cmd": "/seedguard",
                "event": EventType.PluginAction,
                "desc": "保种空间守护：立即执行一次空间检查与清理",
                "category": "管理",
                "data": {"action": "run"},
            }
        ]

    def get_api(self) -> List[Dict[str, Any]]:
        """返回插件 API 列表。"""
        return [
            {
                "path": "/run",
                "endpoint": self.api_run,
                "methods": ["POST"],
                "summary": "手动触发空间检查与清理",
                "description": "立即执行一次空间检查；低于阈值时按配置清理。"
                               "可选参数：mode=seed|file、dry_run=true|false 临时覆盖配置。",
            },
            {
                "path": "/status",
                "endpoint": self.api_status,
                "methods": ["GET"],
                "summary": "查询插件运行状态",
                "description": "返回当前配置、卷剩余空间与最近一次运行结果。",
            },
        ]

    def _get_downloader_items(self) -> List[Dict[str, str]]:
        """
        读取 MoviePilot 已启用的下载器，生成下拉选项。

        :return: VSelect items（title/value 对）
        """
        items: List[Dict[str, str]] = []
        try:
            # V3：ServiceConfigHelper 已彻底移除，改由应用层下载器目录提供配置。
            # get_configs() 已按 name/type/enabled 过滤（默认不含未启用项）。
            for conf in DownloaderHelper().get_configs().values():
                if not conf.name:
                    continue
                title = conf.name
                if conf.type:
                    title = f"{title}（{conf.type}）"
                items.append({"title": title, "value": conf.name})
        except Exception as err:
            logger.error("【保种空间守护】读取下载器列表失败：%s", err)
        return items

    @staticmethod
    def _group_header(title: str, desc: str = "",
                      show: Optional[str] = None) -> dict:
        """构造配置表单的「分组标题」节点（无 model，纯展示）。

        背景：本插件配置项较多，且部分项只在某一清理模式下生效，
        平铺展示时用户难以分辨「某项到底属于哪种模式」（历史误会来源）。
        故按「全局 / 种子级 / 仅文件 / 种子联动」分四组，用标题条区隔。

        :param title: 组标题
        :param desc: 组说明（一句话讲清生效范围）
        :param show: 条件显示的表达式（如 ``mode === 'seed'``）。
                     由 MoviePilot 前端 FormRender 求值为假时隐藏整条；
                     不传则该组标题常驻显示。
        :return: 表单节点 dict（不带 model，故不会被「表单字段须在默认
                 配置中」的校验误判）
        """
        props: Dict[str, Any] = {
            "type": "info",
            "variant": "tonal",
            "class": "mt-6",
            "text": f"▼ {title}　—　{desc}" if desc else f"▼ {title}",
        }
        # 注意：此处传的是 props 层级的 "show"，由前端 FormRender 解析；
        # 不要写成 v-show，二者前端都支持，但 show 更简洁。
        if show:
            props["show"] = show
        return {"component": "VAlert", "props": props}

    def get_form(self) -> Tuple[Optional[List[dict]], Dict[str, Any]]:
        """返回插件配置表单与默认配置。"""
        downloader_items = self._get_downloader_items()
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
                                            "text": "使用说明：空间低于阈值时，按「保种最久」优先清理，"
                                                    "直到恢复到阈值以上。删除后会实测真实释放量，"
                                                    "若几乎未释放则立即停止并告警——"
                                                    "宁可空间不足，也不过量删除。"
                                                    "首次使用请先用「立即试运行一次」预览将删内容，"
                                                    "确认无误后再正式清理。",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    # ---- 分组标题：全局基础设置 ----
                    self._group_header(
                        "全局基础设置",
                        "以下设置与清理模式无关，两种模式均生效",
                    ),
                    {
                        "component": "VSelect",
                        "props": {
                            "model": "manual_action",
                            "label": "手动触发一次",
                            "class": "mt-4",
                            "hint": "选择动作后点击保存即后台执行一次，执行完自动复位。"
                                   "空间未低于阈值时提示无需清理；正式清理为真删，请先试运行确认",
                            "items": [
                                {"title": "— 选择动作后保存触发 —", "value": ""},
                                {"title": "立即试运行一次（只列不删）", "value": "dry"},
                                {"title": "立即正式清理一次（真实删除）", "value": "clean"},
                            ],
                        },
                    },
                    {
                        "component": "VSwitch",
                        "props": {
                            "model": "enabled",
                            "label": "启用插件",
                            "class": "mt-4",
                            "hint": "启用后按下方定时规则检查空间，不足时自动清理保种最久的资源",
                        },
                    },
                    {
                        "component": "VSelect",
                        "props": {
                            "model": "mode",
                            "label": "清理模式",
                            "class": "mt-4",
                            "hint": "种子级：直接删除下载器中最老的已完成种子（连带文件），一步到位不留红种；"
                                   "仅文件：只删文件，种子是否联动删除由下方「联动删除种子」开关决定"
                                   "（需该种子文件全部删除后才删种）",
                            "items": [
                                {"title": "种子级（推荐）", "value": "seed"},
                                {"title": "仅文件", "value": "file"},
                            ],
                        },
                    },
                    {
                        "component": "VTextarea",
                        "props": {
                            "model": "target_dirs",
                            "label": "保种/清理目录（每行一个）",
                            "class": "mt-4",
                            "rows": 4,
                            "placeholder": "/volume1/video/下载\n/volume1/video/媒体库",
                            "hint": "每行一个绝对路径。若下载目录与媒体库目录中的文件互为硬链接，"
                                   "请把两个目录都填进来，插件会自动识别并两侧一并删除；"
                                   "只填一侧会导致删除后空间不释放（插件检测到会停止并告警）。"
                                   "以 # 开头的行会被忽略，可用于临时停用某个目录",
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "volume_path",
                            "label": "空间检查路径",
                            "class": "mt-4",
                            "placeholder": "/volume1",
                            "hint": "df 对应的卷路径，插件读取其剩余空间",
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "threshold_gb",
                            "label": "剩余空间阈值（GB）",
                            "class": "mt-4",
                            "placeholder": "500",
                            "hint": "剩余空间低于该值才触发清理，清理到恢复至该值为止",
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "recent_skip_days",
                            "label": "保护最近添加天数",
                            "class": "mt-4",
                            "placeholder": "1",
                            "hint": "最近 N 天添加的种子/文件不清理（保种时间短，删了易触发 H&R）",
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "cron",
                            "label": "定时检查规则（cron）",
                            "class": "mt-4",
                            "placeholder": "0 */6 * * *",
                            "hint": "默认每 6 小时检查一次",
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "sync_wait_seconds",
                            "label": "空间释放最长等待秒数",
                            "class": "mt-4",
                            "placeholder": "90",
                            "hint": "删除后轮询等待空间释放的时长上限，默认 90，可填 0-1800（0=不等待）。"
                                    "每 5 秒轮询一次，释放达标即提前结束，无需空等整个时长；"
                                    "仅当释放滞后（如快照占用、文件系统延迟回收）时才会等满",
                        },
                    },
                    {
                        "component": "VSwitch",
                        "props": {
                            "model": "dry_run",
                            "label": "试运行（只列不删）",
                            "class": "mt-4",
                            "hint": "开启后仅输出将清理的清单，不实际删除，建议首次先试运行",
                        },
                    },
                    {
                        "component": "VSwitch",
                        "props": {
                            "model": "notify",
                            "label": "完成后通知",
                            "class": "mt-4",
                            "hint": "清理完成后发送站内消息通知",
                        },
                    },
                    # ---- 分组标题：种子级模式设置（仅 mode=seed 显示） ----
                    self._group_header(
                        "种子级模式设置",
                        "仅在「清理模式 = 种子级」下生效；当前模式下显示",
                        show="mode === 'seed'",
                    ),
                    {
                        "component": "VSelect",
                        "props": {
                            "model": "downloaders",
                            "label": "目标下载器",
                            "class": "mt-4",
                            "hint": "弹出选项卡多选；不选 = 处理所有已启用下载器。"
                                    "仅种子级模式生效",
                            "multiple": True,
                            "chips": True,
                            "items": downloader_items,
                            "show": "mode === 'seed'",
                        },
                    },
                    {
                        "component": "VSwitch",
                        "props": {
                            "model": "companion_cleanup",
                            "label": "连带清理硬链接与辅种",
                            "class": "mt-4",
                            "hint": "仅种子级模式生效，默认开启。删除种子后顺带清理两样东西："
                                    "①媒体库侧的硬链接（不清理则空间不释放，插件会误判"
                                    "「删了没释放」而停手告警）；"
                                    "②同一内容的所有辅种（同目录、同种子名的其它站点种子；"
                                    "文件已不存在，辅种无做种意义）。"
                                    "辅种不受保护期约束（H&R 只针对下载的种子）。"
                                    "关闭后恢复旧行为：只删主种子，硬链接与辅种不动",
                            "show": "mode === 'seed'",
                        },
                    },
                    {
                        "component": "VSwitch",
                        "props": {
                            "model": "orphan_cleanup",
                            "label": "清理无主文件（无种子引用）",
                            "class": "mt-4",
                            "hint": "仅种子级模式生效，**默认关闭**。清理「不被任何种子引用」"
                                    "的孤儿硬链接——典型成因：种子的内容与种子本身都被"
                                    "移到了「保种/清理目录」之外，只剩媒体库侧的硬链接"
                                    "残留，既无种子可依、也不被「仅文件」模式触及。"
                                    "判定采取保守策略：无法确证种子清单时整轮跳过；"
                                    "同一 inode 只要有任一路径被种子引用就整组保留；"
                                    "保护期内的文件与保护后缀不动。"
                                    "因涉及主动删除用户文件，请务必先开「试运行」核对清单",
                            "show": "mode === 'seed'",
                        },
                    },
                    # ---- 分组标题：仅文件模式设置（仅 mode=file 显示） ----
                    self._group_header(
                        "仅文件模式设置",
                        "仅在「清理模式 = 仅文件」下生效；当前模式下显示",
                        show="mode === 'file'",
                    ),
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "protect_pattern",
                            "label": "保护文件后缀",
                            "class": "mt-4",
                            "placeholder": "*.part|*.!qb|*.download|*.aria2|*.tmp|*.crdownload",
                            "hint": "以 | 分隔的通配符，命中的文件不删除。"
                                    "在「仅文件」模式与种子级的「清理无主文件」中生效；"
                                    "种子级主链路（直接删种子）不适用",
                            "show": "mode === 'file'",
                        },
                    },
                    # ---- 分组标题：种子联动设置（两模式通用，不显隐） ----
                    self._group_header(
                        "种子联动设置（两种模式通用）",
                        "以下开关在两种模式下均生效",
                    ),
                    {
                        "component": "VSwitch",
                        "props": {
                            "model": "delete_torrents",
                            "label": "联动删除种子",
                            "class": "mt-4",
                            "hint": "**两种模式通用**。仅文件模式下：删除文件后联动删除对应种子，"
                                    "**必须该种子的所有文件都已删除**才会删种，"
                                    "任一文件仍在（含被保护后缀跳过的）则保留种子。"
                                    "种子级模式下：它是「空壳回收」的总闸——"
                                    "关闭后，文件已删完但仍留在下载器里的空壳种子不会被回收",
                        },
                    },
                    {
                        "component": "VSwitch",
                        "props": {
                            "model": "delete_history",
                            "label": "删除转移记录",
                            "class": "mt-4",
                            "hint": "**两种模式通用**。删除文件后，顺带删除 MoviePilot 中对应的"
                                    "转移历史记录（先按目标路径匹配，未命中再按源路径匹配）",
                        },
                    },
                    {
                        "component": "VSwitch",
                        "props": {
                            "model": "orphan_seed_scope",
                            "label": "空壳回收覆盖监控目录外的种子",
                            "class": "mt-4",
                            "hint": "**两种模式通用，默认关闭，需先开启「联动删除种子」才生效**。"
                                    "空壳回收原本只扫「内容路径在监控目录内」的种子，"
                                    "而空壳越彻底（目录已消失）越容易被这道范围过滤挡掉——"
                                    "最常见的情况是种子做过保存目录变更或做种转移，"
                                    "其内容路径已不在监控范围内，于是永远扫不到。"
                                    "开启后会把范围外的已完成种子一并纳入判定："
                                    "**只回收已无任何文件的空壳，只摘种子、不删除任何文件**；"
                                    "只要磁盘上仍有文件就绝不回收。"
                                    "注意这会让插件的行为边界扩展到配置目录之外，"
                                    "请确认理解后再开启",
                        },
                    },
                ],
            }
        ], self._default_config()

    def get_page(self) -> Optional[List[dict]]:
        """返回插件详情页面。"""
        if not self._enabled:
            return None
        free_gb = self._disk_free_gb()
        free_text = f"{free_gb}GB" if free_gb is not None else "未知"
        if not self._target_dirs:
            dirs_text = "（未配置）"
        elif len(self._target_dirs) <= 3:
            dirs_text = "、".join(self._target_dirs)
        else:
            head = "、".join(self._target_dirs[:3])
            dirs_text = f"{head} 等 {len(self._target_dirs)} 个目录"
        return [
            {
                "component": "VAlert",
                "props": {
                    "type": "info",
                    "text": f"监控目录：{dirs_text}　|　当前剩余空间：{free_text}"
                            f"　|　阈值：{self._threshold_gb}GB　|　模式：{'种子级' if self._mode == 'seed' else '仅文件'}",
                },
            },
            {
                "component": "VAlert",
                "props": {
                    "type": "success" if "成功" in self._last_result or "充足" in self._last_result else "warning",
                    "text": self._last_result or "尚未运行，可在插件命令 /seedguard 或 API 手动触发",
                },
            },
        ]

    def get_service(self) -> List[Dict[str, Any]]:
        """注册插件定时服务。"""
        return [
            {
                "id": "space_check",
                "name": "保种空间检查与清理",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self.check_and_clean,
                "kwargs": {},
            }
        ]

    def stop_service(self) -> None:
        """停止插件后台服务并释放资源。"""
        self._running = False

    # ============================ API 入口 ============================

    async def api_run(self, request: Request) -> Dict[str, Any]:
        """
        手动触发空间检查与清理（API 入口）。

        :param request: FastAPI 请求对象
        """
        params = dict(request.query_params)
        body = {}
        try:
            data = await request.json()
            if isinstance(data, dict):
                body = data
        except Exception:
            body = {}
        mode = body.get("mode") or params.get("mode")
        raw_dry = body.get("dry_run", params.get("dry_run"))
        dry_run = None
        if raw_dry is not None:
            dry_run = str(raw_dry).strip().lower() in ("true", "1", "yes")
        mode_override = str(mode).strip().lower() if mode else None
        result = self.check_and_clean(
            source="手动",
            mode_override=mode_override,
            dry_run_override=dry_run,
        )
        # 宿主 envelope 契约：前端 isApiResponse() 要求恰好三键
        # （success / message / data），且 success 为 bool、message 为 str。
        return {"success": True, "message": result, "data": None}

    async def api_status(self, request: Request) -> Dict[str, Any]:
        """
        查询插件运行状态（API 入口）。

        :param request: FastAPI 请求对象
        """
        free_gb = self._disk_free_gb()
        # 业务字段一律放进 data，不得平铺在顶层：
        # 顶层多一个键就会破坏宿主 envelope 的三键契约，前端弹「无效响应」。
        return {
            "success": True,
            "message": "获取成功",
            "data": {
                "enabled": self._enabled,
                "target_dirs": self._target_dirs,
                # 兼容旧字段：返回首个目录，便于老调用方平滑过渡
                "target_dir": self._target_dirs[0] if self._target_dirs else "",
                "volume_path": self._volume_path,
                "free_gb": free_gb,
                "threshold_gb": self._threshold_gb,
                "mode": self._mode,
                "dry_run": self._dry_run,
                "last_result": self._last_result,
            },
        }

    # ============================ 命令事件 ============================

    @eventmanager.register(EventType.PluginAction)
    def _command_handler(self, event: Event) -> None:
        """
        处理插件命令事件（/seedguard 触发）。

        :param event: 插件动作事件
        """
        if not event:
            return
        event_data = event.event_data or {}
        if event_data.get("action") != "run":
            return
        self.check_and_clean(source="命令")

    # ============================ 核心逻辑 ============================

    def check_and_clean(self, source: str = "定时",
                        mode_override: Optional[str] = None,
                        dry_run_override: Optional[bool] = None) -> str:
        """
        执行一次空间检查，剩余空间低于阈值时按模式清理保种最久的资源。

        :param source: 触发来源（定时/手动/命令）
        :param mode_override: 临时覆盖清理模式（seed/file）
        :param dry_run_override: 临时覆盖试运行开关
        :return: 运行结果摘要
        """
        if not self._enabled and source != "手动":
            return "插件未启用"
        if not self._lock.acquire(blocking=False):
            return "已有清理任务正在运行，本次跳过"
        try:
            if self._running:
                return "已有清理任务正在运行，本次跳过"
            self._running = True
            mode = mode_override or self._mode
            dry_run = self._dry_run if dry_run_override is None else bool(dry_run_override)
            # 重置本次清理的聚合计数（供通知摘要使用，明细只进日志）
            self._clean_stats = {
                "files": 0,
                "transfers": 0,
                "seeds": 0,
                "companions": 0,
                "orphans": 0,
                "stalled": False,
                "dry_run": dry_run,
            }

            if not self._target_dirs:
                return self._finish("未配置清理目录（target_dirs）", None)
            # 过滤出实际存在的目录，不存在的记警告并跳过（不因个别目录失效而整体罢工）
            invalid = [d for d in self._target_dirs if not os.path.isdir(d)]
            self._active_dirs = [d for d in self._target_dirs if os.path.isdir(d)]
            # 无效目录不仅记日志，还要带进结果消息：用户配错路径时需要被明确
            # 告知，否则会误以为插件在正常工作
            invalid_note = ""
            if invalid:
                invalid_note = f"（已跳过不存在的目录：{'、'.join(invalid)}）"
                logger.warning(
                    "【保种空间守护】以下目录不存在，本次已跳过：%s", "、".join(invalid)
                )
            if not self._active_dirs:
                return self._finish(
                    f"所有配置目录均不存在：{'、'.join(self._target_dirs)}", None
                )

            free_bytes = self._disk_free_bytes()
            if free_bytes is None:
                return self._finish(f"无法获取 {self._volume_path} 剩余空间", None)
            free_gb = int(free_bytes // GIB)
            threshold_bytes = self._threshold_gb * GIB

            prefix = f"[{'试运行' if dry_run else '清理'}] "

            # 孤儿种子回收：先于「空间是否充足」的早退执行。
            #
            # 半删种子的成因是「单轮只删到预留空间即停」，其剩余文件要等后续轮次
            # 才会被删完。而一旦删完，空间往往刚刚越过阈值，后续轮次直接以
            # 「空间充足」早退，删种判定再无机会执行——种子便永久卡在下载器里。
            # 因此这里在早退之前无条件回收一次：只针对「文件已全部删除但种子仍
            # 在」的空壳，不删任何文件，空间充足时也不产生额外删除行为。
            orphan_stats = self._reap_orphan_seeds(dry_run)
            orphan_lines = orphan_stats.pop("_lines", [])
            orphan_note = ""
            if orphan_stats["torrent"]:
                orphan_note = (
                    f"（另{'预计' if dry_run else ''}回收空壳种子 "
                    f"{orphan_stats['torrent']} 个）"
                )
                # 空壳回收的本质就是「删种」，必须计入统一种子计数。
                # 否则摘要会出现「另回收空壳种子 130 个」与「删除种子：0 个」
                # 两个数字互相打架，用户会以为回收动作没生效。
                self._clean_stats["seeds"] = (
                    (self._clean_stats.get("seeds") or 0)
                    + orphan_stats["torrent"]
                )

            # 阈值比较用字节，避免 GB 取整导致的边界反复触发或永不触发
            if free_bytes >= threshold_bytes:
                # 带上 prefix：试运行与正式清理必须一眼可辨，否则用户无法
                # 从结果判断本次到底是「只列不删」还是「已真删」
                msg = (
                    f"{prefix}空间充足（{free_gb}GB ≥ {self._threshold_gb}GB），"
                    f"无需清理{orphan_note}{invalid_note}"
                )
                logger.info("【保种空间守护】%s", msg)
                # 定时触发默认静默：cron 每 6 小时跑一次，若每次都推
                # 「无需清理」，一天 4 条纯噪音。
                # 但手动（界面按钮）与命令（/seedguard）是用户主动发起的，
                # 必须给回执——否则用户点了按钮却收不到任何结果，
                # 观感等同于「点了没反应」。本轮若确实回收了空壳种子，
                # 定时触发也要通知，否则回收动作会悄无声息。
                need_notify = bool(orphan_lines) or source != "定时"
                return self._finish(msg, orphan_lines or None,
                                    notify=need_notify)

            logger.info("【保种空间守护】空间不足（%sGB < %sGB），开始%s处理（%s），"
                        "监控 %d 个目录",
                        free_gb, self._threshold_gb,
                        "试运行" if dry_run else "清理",
                        "种子级" if mode == "seed" else "仅文件",
                        len(self._active_dirs))

            if mode == "seed":
                result = self._clean_by_seed(free_bytes, dry_run)
                # 种子级主链路跑完后，若仍未达标且用户开启了「无主文件」清理，
                # 再补一轮：处理「不被任何种子引用」的孤儿硬链接——它们既无种子
                # 可依（种子不在监控目录内），又不被文件级链路触及（模式互斥），
                # 若不在此处清理便永久残留。
                deleted, released_gb, detail_lines = result
                try:
                    free_after = self._disk_free_bytes()
                except Exception:
                    free_after = None
                if self._orphan_cleanup and (
                    free_after is None
                    or free_after < self._threshold_gb * GIB
                ):
                    o_deleted, o_gb, o_lines = self._clean_orphan_files(dry_run)
                    if o_deleted:
                        detail_lines = list(detail_lines or []) + o_lines
                        deleted += o_deleted
                        released_gb += o_gb
                result = (deleted, released_gb, detail_lines)
            else:
                result = self._clean_by_file(free_bytes, dry_run)
            deleted, released_gb, detail_lines = result
            detail_lines = orphan_lines + list(detail_lines or [])

            suffix = ""
            if deleted == -1:
                suffix = "（未找到可清理的已完成种子，可能均处于保护期内或无下载器连接）"
                deleted = 0
            elif dry_run:
                suffix = f"，共列出 {deleted} 个待删资源（预计释放约 {released_gb}GB）"
            elif deleted > 0:
                suffix = f"，释放约 {released_gb}GB"
            # 空壳回收量同样要进结果消息：它发生在删文件之前，若不汇总，
            # 用户在「空间不足」这一主路径上反而看不到任何空壳回收痕迹
            suffix += orphan_note
            free_gb_now = self._disk_free_gb()
            msg = f"{prefix}空间不足处理完成{suffix}，当前剩余 {free_gb_now}GB{invalid_note}"
            return self._finish(msg, detail_lines)
        finally:
            self._running = False
            self._lock.release()

    def _finish(self, msg: str, details: Optional[List[str]] = None,
                notify: Optional[bool] = None) -> str:
        """
        记录结果并按需通知。

        通知只给「汇总计数」（删除文件数 / 转移记录数 / 种子数），不罗列每个
        被删文件的明细——明细全部写入插件日志，用户可自行查阅。

        :param msg: 结果摘要
        :param details: 明细行（仅入日志，不进通知）
        :param notify: 是否通知，None 表示按配置
        """
        self._last_result = msg
        logger.info("【保种空间守护】%s", msg)
        if details:
            for line in details:
                logger.info("【保种空间守护】  %s", line)
        do_notify = self._notify if notify is None else notify
        if do_notify:
            try:
                text = self._build_notify_text(msg)
                self.post_message(
                    mtype=MessageType.Plugin,
                    title="保种空间守护",
                    text=text,
                )
            except Exception as err:
                logger.error("【保种空间守护】发送通知失败：%s", err)
        return msg

    def _build_notify_text(self, msg: str) -> str:
        """
        根据聚合计数构造通知正文：仅展示三类汇总数字，不罗列文件明细。

        :param msg: 结果摘要（已含运行状态与释放量）
        :return: 通知正文
        """
        s = getattr(self, "_clean_stats", {}) or {}
        has_count = (
            s.get("files") or s.get("transfers") or s.get("seeds")
            or s.get("companions") or s.get("orphans") or s.get("stalled")
        )
        if not has_count:
            return msg
        prefix = "预计" if s.get("dry_run") else "共"
        lines = []
        if s.get("stalled"):
            lines.append("⚠️ 空间未如期释放，已停止清理（详见插件日志）")
        lines.append(f"{prefix}删除文件：{s.get('files') or 0} 个")
        lines.append(f"{prefix}删除转移记录：{s.get('transfers') or 0} 条")
        lines.append(f"{prefix}删除种子：{s.get('seeds') or 0} 个")
        if s.get("companions"):
            # 辅种独立成行：并入「删除种子」会让总数虚高，用户无法分辨
            # 删掉的是主种子还是连带摘除的辅种
            lines.append(f"{prefix}连带删除辅种：{s.get('companions')} 个")
        if s.get("orphans"):
            # 同样独立成行：无主文件既非「按种子删」也非「按文件删」，
            # 与 files/seeds 混在一起会让用户看不懂数字来源
            lines.append(f"{prefix}清理无主文件：{s.get('orphans')} 个")
        return msg + "\n" + "\n".join(lines)

    def _disk_free_bytes(self) -> Optional[int]:
        """
        获取卷剩余空间（字节，精确值）。

        判定空间是否真正释放必须用字节精度：GB 取整后小于 1GB 的释放量恒为 0，
        会导致「明明释放了却判定为未释放」的误伤。

        :return: 剩余字节数，失败返回 None
        """
        try:
            return shutil.disk_usage(self._volume_path).free
        except Exception as err:
            logger.error("【保种空间守护】获取 %s 剩余空间失败：%s", self._volume_path, err)
            return None

    def _disk_free_gb(self) -> Optional[int]:
        """
        获取卷剩余空间（GB，取整，仅用于展示）。

        :return: 剩余 GB，失败返回 None
        """
        free = self._disk_free_bytes()
        return None if free is None else int(free // GIB)

    def _wait_for_release(self, free_before: int, nominal: int) -> int:
        """
        轮询等待空间释放，最长 self._sync_wait_seconds 秒。

        相比固定 sleep，轮询可在释放达标时提前退出：独立目录（无硬链接）通常
        数秒内即达标，无需空等整个等待时长；仅在释放滞后时才等到上限。

        :param free_before: 删除前的剩余字节数
        :param nominal: 本轮名义释放字节数（按 inode 去重后的真实占用）
        :return: 实际观察到的释放字节数（可能为负，表示期间有其他进程写入）
        """
        if self._sync_wait_seconds <= 0:
            now = self._disk_free_bytes()
            return (now if now is not None else free_before) - free_before
        deadline = time.monotonic() + self._sync_wait_seconds
        target = int(nominal * RELEASE_TOLERANCE)
        free_now = free_before
        while True:
            measured = self._disk_free_bytes()
            free_now = measured if measured is not None else free_before
            if free_now - free_before >= target:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(RELEASE_POLL_SECONDS, remaining))
        return free_now - free_before

    # ============================ 种子级清理 ============================

    def _clean_by_seed(self, free_bytes: int, dry_run: bool) -> Tuple[int, float, List[str]]:
        """
        种子级清理：按种子完成时间从早到晚删除已完成种子（连带文件），
        直到剩余空间恢复到阈值以上。

        真实删除采用「按缺口预选 → 删除 → 等待释放 → 复核」的分轮策略：
        删除前先按当前缺口从旧到新预选种子清单（名义累计 ≥ 缺口），删除后轮询
        等待空间真正回收，再实测空间决定是否按新缺口补删下一轮——避免边删边用
        滞后的实时空间判停而一次性删除过量或永不停止。

        「连带清理」（``_companion_cleanup`` 开启时，默认开）在每个种子删除时
        按严格顺序执行，顺序不可调换：

        1. **删种前**建 inode 索引：删种后下载器会删掉下载侧文件，届时该 inode
           的下载侧路径消失，只剩媒体库侧孤证，无法再确认双侧关系
        2. 删主种子（``delete_file=True``）
        3. **删种后**删硬链接：把索引中仍在盘上的同 inode 路径（媒体库侧）
           unlink 掉，空间才真正释放
        4. 连带删辅种：同内容（同路径 + 同种子名）的其它 hash 一并摘种。文件已
           不存在，辅种无做种意义；辅种不受保护期约束（H&R 只针对下载的种子）

        注意：``_companion_cleanup`` 关闭时恢复旧行为——由下载器负责删文件，
        媒体库侧硬链接插件不去触碰，空间迟迟不释放时按下述闭环停止并告警。

        :param free_bytes: 当前剩余空间（字节）
        :param dry_run: 是否试运行
        :return: （处理数，预计/实际释放 GB，明细行）
        """
        candidates = self._collect_seed_candidates()
        if not candidates:
            return -1, 0.0, []

        # 辅种映射：整轮构建一次，避免每个种子重复扫描候选列表。
        # 注意候选已由 _collect_seed_candidates 过滤为「内容在监控目录内」，
        # 因此监控目录外的同名种子不会被牵连删除。
        companion_map = (
            self._build_companion_map(candidates) if self._companion_cleanup else {}
        )

        cutoff = time.time() - self._recent_skip_days * 86400
        candidates = [c for c in candidates if c["added"] < cutoff]
        candidates.sort(key=lambda c: c["added"])

        detail_lines: List[str] = []
        deleted = 0
        released_gb = 0.0
        # 本方法自身也保证统计存在（测试可能直接调用），不与 check_and_clean 重置冲突
        if "seeds" not in getattr(self, "_clean_stats", {}):
            self._clean_stats = {
                "files": 0, "transfers": 0, "seeds": 0, "companions": 0,
                "orphans": 0, "stalled": False, "dry_run": dry_run,
            }
        self._clean_stats["dry_run"] = dry_run

        # 试运行：空间不会真正释放，用累计候选大小模拟释放量判断是否达标
        if dry_run:
            est_free = free_bytes / GIB
            for cand in candidates:
                if est_free >= self._threshold_gb:
                    break
                added_text = datetime.fromtimestamp(cand["added"]).strftime("%Y-%m-%d")
                detail_lines.append(
                    f"[试运行] 将删除种子：{cand['title']}（{cand['downloader']}，"
                    f"添加于 {added_text}，{cand['size_gb']}GB）"
                )
                logger.info("【保种空间守护】%s", detail_lines[-1])
                # 如实预告连带清理范围：用户需据此核对是否会误伤辅种
                if self._companion_cleanup:
                    peers = [
                        h for h in companion_map.get(
                            self._content_key(cand), []) if h != cand["hash"]
                    ]
                    if peers:
                        line = (
                            f"[试运行] 　└ 将连带删除辅种 {len(peers)} 个"
                            f"（同内容其它站点）"
                        )
                        detail_lines.append(line)
                        logger.info("【保种空间守护】%s", line)
                deleted += 1
                released_gb += float(cand["size_gb"])
                est_free += float(cand["size_gb"])
            # 累加而非覆盖：本轮可能在进入本方法前就已回收过空壳种子，
            # 直接赋值会把那部分计数冲掉，导致摘要少报。
            self._clean_stats["seeds"] = (
                (self._clean_stats.get("seeds") or 0) + deleted
            )
            return deleted, round(released_gb, 1), detail_lines

        # 真实删除：分轮按缺口预选并删除，等待释放后复核
        idx = 0
        stall = 0
        while True:
            free_now = self._disk_free_bytes()
            free_now = free_bytes if free_now is None else free_now
            if free_now >= self._threshold_gb * GIB:
                break
            # 本轮按缺口预选种子：从最旧开始累计名义大小达到缺口即止
            gap_bytes = max(GIB, self._threshold_gb * GIB - free_now)
            plan: List[Dict[str, Any]] = []
            planned_bytes = 0.0
            while idx < len(candidates):
                cand = candidates[idx]
                idx += 1
                plan.append(cand)
                planned_bytes += float(cand["size_gb"]) * GIB
                if planned_bytes >= gap_bytes:
                    break
            if not plan:
                # 候选耗尽仍未达标，结束
                break
            for cand in plan:
                # ---- ① 删种前建 inode 索引（顺序关键，不可后移）----
                # 删种后下载器会删掉下载侧文件，届时该 inode 的下载侧路径消失，
                # 只剩媒体库侧孤证，无法再确认「哪些路径是同一文件」。
                ino_index: Dict[Tuple[int, int], List[str]] = {}
                if self._companion_cleanup:
                    related = self._seed_related_paths(cand)
                    ino_index = self._build_inode_index(related)
                    logger.info(
                        "【保种空间守护】种子 %s 关联路径 %d 条，inode %d 个"
                        "（含媒体库侧硬链接）",
                        cand["title"], len(related), len(ino_index),
                    )

                # ---- ② 删主种子 ----
                try:
                    ok = cand["module"].remove_torrents(
                        hashs=cand["hash"],
                        delete_file=True,
                        downloader=cand["downloader"],
                    )
                except Exception as err:
                    logger.error("【保种空间守护】删除种子 %s 失败：%s", cand["title"], err)
                    continue
                if not ok:
                    continue

                deleted += 1
                released_gb += float(cand["size_gb"])
                added_text = datetime.fromtimestamp(
                    cand["added"]).strftime("%Y-%m-%d")
                detail_lines.append(
                    f"已删除种子：{cand['title']}（{cand['downloader']}，"
                    f"添加于 {added_text}）"
                )
                logger.info("【保种空间守护】%s", detail_lines[-1])

                # ---- ③ 删硬链接：媒体库侧同 inode 路径一并 unlink ----
                # 下载侧已被下载器删除，此处清理的是媒体库侧；不清理则空间
                # 不释放，_release_is_healthy 会误判「删了没释放」而停手告警。
                if self._companion_cleanup and ino_index:
                    link_removed = self._clean_hardlinks_for(
                        ino_index, cand["title"]
                    )
                    if link_removed:
                        line = (
                            f"已清理硬链接 {link_removed} 条"
                            f"（种子：{cand['title']}）"
                        )
                        detail_lines.append(line)
                        logger.info("【保种空间守护】%s", line)

                # ---- ④ 连带删辅种：同内容其它 hash 一并摘种 ----
                # 文件已不存在，辅种无做种意义。辅种不受保护期约束——保护期
                # 针对的是「新下载尚未做种」的资源，而 H&R 只针对下载的种子。
                if self._companion_cleanup and companion_map:
                    peers = [
                        h for h in companion_map.get(
                            self._content_key(cand), []) if h != cand["hash"]
                    ]
                    if peers:
                        done = 0
                        for peer_hash in peers:
                            if self._delete_torrent_by_hash(peer_hash, cand["title"]):
                                done += 1
                        if done:
                            line = (
                                f"已连带删除辅种 {done} 个"
                                f"（内容：{cand['title']}）"
                            )
                            detail_lines.append(line)
                            logger.info("【保种空间守护】%s", line)
                            self._clean_stats["companions"] = (
                                (self._clean_stats.get("companions") or 0) + done
                            )

                # ---- ⑤ 联动收尾：转移记录 + 空目录 ----
                # 种子级模式下种子已被下载器删除，无需再按「文件全删才删种」
                # 判定；这里只需顺带清理文件侧残留（刮削产物、转移记录）
                history = self._run_linkage_on_seed_deleted(
                    cand, detail_lines, dry_run
                )
                self._clean_stats["transfers"] += history
                # 删除种子连带文件后，清理可能遗留的空目录（保护配置目录本身）
                self._prune_empty_dirs(cand["path"])
            # 轮询等待空间释放（达标提前退出），再复核
            logger.info(
                "【保种空间守护】本轮已删除 %d 个种子，等待空间释放（最长 %d 秒）后复核…",
                len(plan), self._sync_wait_seconds,
            )
            released_bytes = self._wait_for_release(free_now, int(planned_bytes))
            if not self._release_is_healthy(released_bytes, int(planned_bytes)):
                stall += 1
                if released_bytes <= 0 or stall >= MAX_STALL_ROUNDS:
                    warn = (
                        f"删除 {len(plan)} 个种子后空间未按预期释放"
                        f"（预期 {planned_bytes / GIB:.1f}GB，"
                        f"实测 {released_bytes / GIB:.1f}GB），"
                        f"可能存在快照引用或媒体库侧硬链接未被清理。"
                        f"已停止清理以避免过量删除，请检查「保种/清理目录」"
                        f"是否已包含媒体库目录"
                    )
                    logger.warning("【保种空间守护】%s", warn)
                    detail_lines.append(warn)
                    self._clean_stats["stalled"] = True
                    break
        # 累加而非覆盖：保留进入本方法前已回收的空壳种子计数
        self._clean_stats["seeds"] = (
            (self._clean_stats.get("seeds") or 0) + deleted
        )
        return deleted, round(released_gb, 1), detail_lines

    def _release_is_healthy(self, released: int, nominal: int) -> bool:
        """
        判定本轮空间释放是否正常。

        - 名义释放量过小时直接视为正常：小文件组的测量噪声会淹没真实信号，
          若据此停止会误伤正常清理
        - 释放量达到名义值的 RELEASE_TOLERANCE 即正常（删除期间下载器与媒体库
          几乎必然有写入，需留余量）

        :param released: 实测释放字节数（可为负）
        :param nominal: 名义释放字节数
        :return: 是否视为正常释放
        """
        if nominal <= RELEASE_HARD_STOP_MIN_BYTES:
            return True
        return released >= int(nominal * RELEASE_TOLERANCE)

    # ======================= 联动清理（种子/记录/刮削） =======================

    def _init_linkage_opers(self) -> None:
        """
        懒加载数据层操作器。

        导入或实例化失败（如 MoviePilot 版本差异导致接口变更）时降级为 None，
        并在下一行日志说明原因——联动功能失效不应影响主清理流程。
        """
        self._downloadhis = None
        self._transferhis = None
        if not (self._delete_torrents or self._delete_history):
            return
        try:
            self._downloadhis = DownloadHistoryOper()
            self._transferhis = TransferHistoryOper()
        except Exception as err:
            logger.error("【保种空间守护】联动清理初始化失败，联动功能本次不可用：%s", err)

    def _linkage_enabled(self) -> bool:
        """是否存在任一已开启的联动清理项。"""
        return bool(self._delete_torrents or self._delete_history)

    def _delete_transfer_history(self, path: str) -> bool:
        """
        删除与给定路径关联的转移历史记录。

        与「源文件联动清理」一致，先按目标路径（dest）匹配，未命中再按源路径
        （src）匹配；两者都未命中说明该文件未经 MoviePilot 整理，直接跳过。

        :param path: 已删除的文件路径
        :return: 是否确实删除了一条记录
        """
        if not self._delete_history or not self._transferhis:
            return False
        record = None
        for query in (self._transferhis.get_by_dest,
                      self._transferhis.get_by_src):
            try:
                record = query(path)
            except Exception as err:
                logger.error("【保种空间守护】查询转移记录失败（%s）：%s", path, err)
                continue
            if record:
                break
        if not record:
            return False
        try:
            self._transferhis.delete(record.id)
            return True
        except Exception as err:
            logger.error("【保种空间守护】删除转移记录失败（%s）：%s", path, err)
            return False

    def _resolve_hash_by_path(self, path: str) -> str:
        """
        按文件路径反查下载 hash，查不到返回空串。

        两级策略：

        1. **精确匹配**：``DownloadFiles.fullpath`` 直接命中。影片文件（.mkv/.mp4）
           走这条，命中率最高。
        2. **目录前缀兜底**：``DownloadFiles`` 只登记正片文件，``.md5``/``.nfo``/
           ``.srt``/``@eaDir`` 等刮削残留**不在表中**，精确匹配必然落空。此时改用
           「文件所在目录」与种子的 ``savepath`` 做最长前缀匹配，把残留文件归回
           它所属的种子。

        为什么必须有第 2 级：仅文件模式删完最后一个残留文件时，种子其实已经
        「文件全部删除」，本该联动删种；若反查落空导致 ``pending_hashes`` 为空，
        删种判定会被整段跳过，种子永远留在下载器里做种（实测 BUG）。

        :param path: 文件路径
        :return: 下载 hash，无法归属时返回空串
        """
        if not self._downloadhis or not path:
            return ""
        try:
            exact = str(self._downloadhis.get_hash_by_fullpath(path) or "")
        except Exception as err:
            logger.error("【保种空间守护】反查下载 hash 失败（%s）：%s", path, err)
            exact = ""
        if exact:
            return exact
        # 精确匹配落空：多半是刮削残留，退化为按目录前缀归属
        return self._resolve_hash_by_parent_dir(path)

    def _resolve_hash_by_parent_dir(self, path: str) -> str:
        """
        按「文件所在目录」反查下载 hash（刮削残留兜底）。

        ``DownloadFiles`` 只登记正片文件，``.md5``/``.nfo``/``.srt``/``@eaDir``
        这类刮削残留没有记录，拿它们的完整路径去精确匹配必然落空。但残留文件
        一定与正片同处一个**种子目录**，而目录名就是种子名，因此改用
        「被删文件所在目录」去和「活跃种子的内容路径」做最长前缀匹配。

        数据源取下载器实时种子列表（``_collect_seed_candidates``），而非
        ``DownloadFiles`` 表：前者能拿到全部种子且自带 ``module``，后者既无
        列举接口、又可能因历史未登记而缺项。

        :param path: 文件路径
        :return: 下载 hash，无法归属时返回空串
        """
        parent = os.path.dirname(os.path.normpath(path))
        if not parent:
            return ""
        cache = getattr(self, "_dir_hash_cache", None)
        if cache is None:
            cache = {}
            self._dir_hash_cache = cache
        if parent in cache:
            return cache[parent]

        hit = ""
        best_len = -1
        try:
            candidates = self._collect_seed_candidates()
        except Exception as err:
            logger.error("【保种空间守护】按目录反查种子失败：%s", err)
            candidates = []
        for cand in candidates:
            seed_path = os.path.normpath(str(cand.get("path") or ""))
            hash_str = str(cand.get("hash") or "")
            if not seed_path or not hash_str:
                continue
            if parent == seed_path or parent.startswith(seed_path + os.sep):
                if len(seed_path) > best_len:
                    best_len = len(seed_path)
                    hit = hash_str
        cache[parent] = hit
        return hit

    def _collect_all_torrent_refs(self) -> Optional[Set[str]]:
        """
        收集「全部种子」引用的规范化路径集合，供「无主文件」判定使用。

        与 ``_collect_seed_candidates`` 的**关键区别**（这正是本方法存在的理由）：

        1. **不过滤 content_path 范围**。候选收集会把内容路径不在监控目录内的
           种子标为 out_of_scope 并丢弃；但这类种子**仍在做种**，其文件绝不能被
           当成「无主」删掉——那样会直接破坏做种。
        2. **不过滤未完成种子**。``_parse_torrent`` 对 ``progress < 0.999`` 返回
           None，而未完成的种子**同样在磁盘上占着文件**。故此处不复用它，直接读
           原始条目的路径字段。

        每个种子取三级来源，任一命中即视为「有主」：

        1. 内容路径 / 保存目录（下载器原始报告，最权威）
        2. 「配置目录 + 种子名」推断（覆盖记录缺失与路径迁移）
        3. ``DownloadFiles`` 记录（含各集完整路径）

        :return: 规范化路径集合；**返回 None 表示无法确证**（下载器枚举失败
                 或无可用下载器）——调用方必须据此放弃整轮判定，宁可不动
        """
        refs: Set[str] = set()
        try:
            services = DownloaderHelper().get_services()
        except Exception as err:
            logger.error("【保种空间守护】无主判定：获取下载器失败：%s", err)
            return None
        if not services:
            logger.warning("【保种空间守护】无主判定：无可用下载器，放弃本轮判定")
            return None

        bases = self._active_dirs or self._target_dirs
        probed = 0
        for name, service in services.items():
            # 与候选收集保持一致：仅处理配置选定的目标下载器（空=全部）
            if self._downloaders and name not in self._downloaders:
                continue
            server = getattr(service, "instance", None)
            if server is None:
                continue
            try:
                ret = server.get_torrents()
            except Exception as err:
                # 单个下载器读不到 → 整轮判定不可信，直接放弃
                logger.error(
                    "【保种空间守护】无主判定：读取下载器 %s 种子列表失败：%s",
                    name, err,
                )
                return None
            items = ret[0] if isinstance(ret, tuple) else ret
            for item in items or []:
                probed += 1
                # ① 内容路径 / 保存目录：不判完成度，未完成的种子同样在占文件
                content = str(
                    self._pick_attr(item, "content_path", "contentPath",
                                    default="") or ""
                ).strip()
                if content:
                    refs.add(os.path.normpath(content))
                save_path = str(
                    self._pick_attr(item, "save_path", "savePath",
                                    "download_dir", "downloadDir",
                                    default="") or ""
                ).strip()
                if save_path:
                    refs.add(os.path.normpath(save_path))
                # ② 种子名 → 各配置目录下的推断路径
                title = str(self._pick_attr(item, "name", default="") or "").strip()
                if title:
                    for base in bases:
                        refs.add(os.path.normpath(os.path.join(base, title)))
                # ③ DownloadFiles 记录：按 hash 取各集完整路径
                hash_str = str(self._pick_attr(item, "hash", default="") or "")
                if hash_str:
                    for path in self._seed_file_paths(
                        {"hash": hash_str, "title": title, "files": []}
                    ):
                        refs.add(os.path.normpath(path))

        logger.info(
            "【保种空间守护】无主判定：扫描 %d 个种子（含未完成与监控范围外），"
            "得到 %d 条有主路径",
            probed, len(refs),
        )
        return refs

    @staticmethod
    def _is_owned_path(path: str, owned: Set[str]) -> bool:
        """
        判断某路径是否被任一「有主」路径覆盖（自身命中或落在其目录前缀下）。

        前缀匹配而非全等：种子的有主集合里可能只登记了**目录**
        （``content_path`` 指向种子目录），而其下的各集文件是逐个出现在
        磁盘上的，必须按前缀归属，否则会把正在做种的文件误判为无主。

        :param path: 待判定的文件路径
        :param owned: ``_collect_all_torrent_refs`` 的返回值
        :return: 是否属于某个有主路径
        """
        if not path or not owned:
            return False
        norm = os.path.normpath(path)
        if norm in owned:
            return True
        for owner in owned:
            if norm.startswith(owner + os.sep):
                return True
        return False

    def _clean_orphan_files(self, dry_run: bool) -> Tuple[int, float, List[str]]:
        """
        清理「无主文件」：不被任何种子引用的孤儿硬链接。

        典型成因：种子 A 的文件在媒体库目录生成了硬链接，之后 A 的内容与种子
        都被移到了监控目录之外。此时残留的硬链接既无种子可依（种子级候选要求
        种子在监控目录内），又不被文件级链路触及（模式二选一），于是永久残留。

        **保守优先**是这个方法的最高原则——只有能证明「不被任何种子引用」才删：

        - ``_collect_all_torrent_refs`` 返回 None（无法确证）→ 整轮放弃
        - 同 inode 的任一路径有主 → **整组保留**（同 inode 即同一物理文件，
          一侧有主说明它仍被引用，删任何一侧都会破坏做种）
        - 保护期内的文件（按 mtime）、命中保护后缀的文件 → 保留
        - 路径不在配置目录内 → 保留（``_delete_one`` 三层边界自动拦截）

        与 ``_clean_by_seed`` 主链路的关系：本方法**不参与**其「名义释放量」
        闭环。删除后空间未释放（该 inode 在监控范围外仍有链接）时只记明细告警，
        **不置 stalled**，避免污染主流程的停手判定。

        :param dry_run: 是否试运行（照常扫描并如实预告，但不执行删除）
        :return: （删除文件数，释放 GB，明细行）
        """
        detail_lines: List[str] = []

        # ① 先确证「有主」集合；拿不到就整轮放弃（宁可漏删不可误删）
        owned = self._collect_all_torrent_refs()
        if owned is None:
            line = "无主文件清理：无法获取种子清单（下载器不可用或读取失败），本轮跳过"
            logger.warning("【保种空间守护】%s", line)
            detail_lines.append(line)
            return 0, 0.0, detail_lines

        # ② 建立候选索引（复用文件级的遍历口径：保护期与保护后缀均已生效）
        patterns = [
            p.strip()
            for p in re.split(r"[,|，]", self._protect_pattern)
            if p.strip()
        ]
        recent_secs = self._recent_skip_days * 86400
        files, ino_paths = self._index_files(patterns, recent_secs)
        if not files:
            return 0, 0.0, detail_lines

        # _delete_one 从该成员取「同 inode 的全部路径」，这里必须是本次索引
        self._ino_paths = ino_paths

        # ③ 按 inode 归组：同 inode 任一路径有主 → 整组保留
        orphan_keys: List[Tuple[int, int]] = []
        seen_keys: Set[Tuple[int, int]] = set()
        for _mtime, _fpath, _size, key in files:
            if key in seen_keys:
                continue
            seen_keys.add(key)
            plist = ino_paths.get(key, [])
            if any(self._is_owned_path(p, owned) for p in plist):
                continue
            orphan_keys.append(key)

        if not orphan_keys:
            logger.info("【保种空间守护】无主文件清理：未发现无主文件")
            return 0, 0.0, detail_lines

        size_by_key = {key: size for _m, _p, size, key in files}

        # ④ 试运行：照常扫描，如实预告（不因 dry_run 而跳过扫描）
        if dry_run:
            total_gb = 0.0
            for key in orphan_keys:
                plist = self._ino_paths.get(key, [])
                rep = plist[0] if plist else ""
                size = float(size_by_key.get(key, 0))
                total_gb += size / GIB
                detail_lines.append(
                    f"[试运行] 将删除无主文件：{rep}"
                    f"（无种子引用，共 {len(plist)} 条路径）"
                )
                logger.info("【保种空间守护】%s", detail_lines[-1])
            self._clean_stats["orphans"] = (
                (self._clean_stats.get("orphans") or 0) + len(orphan_keys)
            )
            return len(orphan_keys), round(total_gb, 1), detail_lines

        # ⑤ 真实删除：逐组删，每组后复核空间，达标即停
        deleted = 0
        released_gb = 0.0
        for key in orphan_keys:
            if self._disk_free_bytes() is not None:
                if self._disk_free_bytes() >= self._threshold_gb * GIB:
                    break
            plist = self._ino_paths.get(key, [])
            rep = plist[0] if plist else ""
            if not rep:
                continue
            removed = self._delete_one(rep, key)
            if removed <= 0:
                continue
            deleted += 1
            released_gb += float(size_by_key.get(key, 0)) / GIB
            detail_lines.append(
                f"已删除无主文件：{rep}（无种子引用，清理 {removed} 条路径）"
            )
            logger.info("【保种空间守护】%s", detail_lines[-1])

        if deleted:
            self._clean_stats["orphans"] = (
                (self._clean_stats.get("orphans") or 0) + deleted
            )
        return deleted, round(released_gb, 1), detail_lines

    def _reap_orphan_seeds(self, dry_run: bool) -> Dict[str, Any]:
        """
        回收「空壳种子」：文件已全部删除，但种子仍留在下载器里做种。

        这是「跨轮补全」的收尾环节。仅文件模式的删除策略是「删到预留空间即停」，
        半删种子要等后续轮次才被删完；而删完的那一刻空间往往刚好越过阈值，
        后续轮次会以「空间充足」直接早退，删种判定再无入口。因此在每轮开始时
        无条件扫一次空壳：**不删任何文件**，只删已经被删空的种子。

        「已删空」的判定复用 ``_seed_fully_removed``（对磁盘做物理复核），
        与文件删除链路的判定标准完全一致，不存在两套口径。

        有意**不受保护期限制**：保护期保护的是「新下载、尚未做种的资源」，
        而文件都不存在了的种子已无保种价值，继续保留只会占着保号名额。

        试运行（``dry_run``）**照常扫描全部候选**，只把「删种」换成「记一笔」：
        试运行的价值就在于如实预告「这一轮将会回收哪些种子」，若因试运行而
        整段跳过扫描，用户得到的「回收 0 个」便是假象而非实情。真正的前置
        阻断只有两条——没有可用下载器、或用户未开启删种开关，此时扫了也无
        意义，直接返回。

        :param dry_run: 是否试运行（不执行真实删种，仅记录将回收的种子）
        :return: 统计字典（torrent 回收数 / checked 候选数 / _lines 明细行）
        """
        stats: Dict[str, Any] = {
            "torrent": 0, "checked": 0, "alive": 0, "failed": 0, "_lines": [],
        }
        # 前置阻断：无下载器则无从扫描；删种开关未开则扫了也没用。
        # 注意此处刻意**不含 dry_run**——试运行要如实预告将回收的种子。
        if not self._downloader_available() or not self._delete_torrents:
            return stats

        try:
            # 候选范围由开关决定：默认只扫监控目录内（原行为）；
            # 开启 orphan_seed_scope 后连监控目录外的种子一并纳入——
            # 空壳的典型形态就是「目录已消失」，此时范围前缀匹配必然失败，
            # 最该回收的那批反而被挡在门外（详见 _collect_all_seed_candidates）。
            if self._orphan_seed_scope:
                candidates = self._collect_all_seed_candidates()
            else:
                candidates = self._collect_seed_candidates()
        except Exception as err:
            logger.error("【保种空间守护】空壳种子扫描失败：%s", err)
            return stats
        if not candidates:
            logger.info("【保种空间守护】空壳种子扫描：候选种子 0 个，跳过")
            return stats

        for cand in candidates:
            hash_str = str(cand.get("hash") or "")
            if not hash_str:
                continue
            stats["checked"] += 1
            try:
                # 传入候选对象：判定需要种子的真实内容路径做物理复核，
                # 仅凭 hash 查 DownloadFiles 在双下载器/路径迁移场景会误判
                if not self._seed_fully_removed(hash_str, cand):
                    stats["alive"] += 1
                    continue
            except Exception as err:
                logger.error("【保种空间守护】空壳种子判定失败（%s）：%s", hash_str, err)
                stats["failed"] += 1
                continue
            title = str(cand.get("title") or hash_str)
            module = cand.get("module")
            downloader = cand.get("downloader")
            # 试运行：判定照做，但不真删，只记录「将会回收」的种子
            if dry_run:
                stats["torrent"] += 1
                line = f"[试运行] 将回收空壳种子（该种子文件已全部删除）：{title}"
                stats["_lines"].append(line)
                logger.info("【保种空间守护】%s", line)
                continue
            try:
                ok = module.remove_torrents(
                    hashs=[hash_str], delete_file=False, downloader=downloader,
                ) if module else False
            except Exception as err:
                logger.error("【保种空间守护】回收空壳种子失败（%s）：%s", hash_str, err)
                ok = False
            if ok:
                stats["torrent"] += 1
                line = f"已回收空壳种子（该种子文件已全部删除）：{title}"
                stats["_lines"].append(line)
                logger.info("【保种空间守护】%s", line)

        if stats["torrent"] or stats["checked"]:
            logger.info(
                "【保种空间守护】空壳种子回收完成：扫描 %d 个，%s %d 个"
                "（判定仍有文件 %d 个，判定失败 %d 个）",
                stats["checked"],
                "预计回收" if dry_run else "回收",
                stats["torrent"],
                stats["alive"], stats["failed"],
            )
        return stats

    def _downloader_available(self) -> bool:
        """是否存在任一可用的已启用下载器（供空壳回收做前置判断）。"""
        try:
            # V3：ModuleManager 已移除。get_services() 返回带有效配置的运行中
            # 下载器（ServiceInfo），非空即代表至少有一个可用下载器。
            return bool(DownloaderHelper().get_services())
        except Exception as err:
            logger.error("【保种空间守护】检测下载器可用性失败：%s", err)
        return False

    # V3 适配：DownloaderConf.type 是普通字符串（如 "qbittorrent"），
    # 而 DownloaderType 是**纯 Enum**（值为 "Qbittorrent"），二者用 == 比较
    # 恒为 False；这里统一归一化为枚举成员后再交给 _parse_torrent 判定。
    _DOWNLOADER_TYPE_MAP = {
        "qbittorrent": DownloaderType.Qbittorrent,
        "transmission": DownloaderType.Transmission,
    }

    @classmethod
    def _resolve_downloader_type(
            cls, type_str: Optional[str]) -> Optional[DownloaderType]:
        """把下载器配置里的类型字符串安全归一化为 DownloaderType 枚举。"""
        if isinstance(type_str, DownloaderType):
            return type_str
        if not type_str:
            return None
        return cls._DOWNLOADER_TYPE_MAP.get(str(type_str).strip().lower())

    def _seed_fully_removed(self, hash_str: str,
                            cand: Optional[Dict[str, Any]] = None) -> bool:
        """
        判定某 hash 对应的种子文件是否**已全部删除**。

        以「物理存在性」为准而非信任 ``DownloadFiles.state``：state 由
        MoviePilot 自身维护，我们用 ``os.unlink`` 删除文件后它不会被同步置 0，
        若直接查 ``state=1`` 会得到「仍有文件」的错误结论，导致种子永远删不掉。

        判定分两级，**任一环节发现「有文件」即立刻返回 False（保留种子）**：

        1. **记录复核**：列出该 hash 下所有 ``DownloadFiles`` 记录，逐个
           ``os.path.exists`` 复核。只要有任意一个文件在磁盘上仍然存在，
           就认为该种子尚未删完。
        2. **自身文件复核**（关键加固，v3.0.5 起）：向下载器查该种子
           **自己的**文件清单（``_get_seed_files``），逐个 ``os.path.exists``。
           任一文件仍在 → 未删空。取不到清单时**退回**扫 ``cand["path"]``
           目录的旧逻辑（保守：宁可漏回收不可误删）。

        为什么必须有第 2 级：``DownloadFiles`` 记录的是**首次下载时**的路径，
        而种子可能经历 qBittorrent → Transmission 做种转移、下载器清空重建、
        目录迁移等变化，记录路径随之失效（指向已不存在的旧路径，或为空串）。
        此时仅凭第 1 级会得出「记录里的文件都不在了 → 已删空」的**错误结论**，
        把一个磁盘上文件完好的种子误删掉（实测事故）。第 2 级以下载器当前
        报告的真实清单为准，绕开失效记录，从根本上堵住这个误判。

        为什么第 2 级要用「自身清单」而非「扫目录」（v3.0.5 修复）：扫目录的
        语义是「这个种子**所在目录**里还有文件吗」，而一个目录下常同时存放
        多个不同种子的文件（实测某剧集目录下 19 个种子各管一集）。此时
        「自己的文件已删光、同目录还有兄弟种子的文件」会被误判为未删空，
        该种子便永远回收不掉——功能静默失效，且日志上毫无异常痕迹。

        **保守原则**：所有「判不了」的情况一律返回 False（保留种子）——
        记录为空、路径为空、路径不可读、查询异常等，宁可不删也不误删。

        :param hash_str: 下载 hash
        :param cand: 种子候选项（含 ``module`` / ``dl_dir`` / ``path``），可为 None
        :return: 是否已全部删除（仅当两级复核都确认无文件时才为 True）
        """
        if not hash_str:
            return False

        # ---- 第 1 级：DownloadFiles 记录复核 ----
        records_checked = 0
        record_paths: List[str] = []
        if self._downloadhis:
            try:
                records = self._downloadhis.get_files_by_hash(hash_str)
            except Exception as err:
                logger.error("【保种空间守护】查询种子文件记录失败（%s）：%s", hash_str, err)
                return False
            for record in records or []:
                fullpath = str(getattr(record, "fullpath", "") or "")
                if not fullpath:
                    continue
                records_checked += 1
                record_paths.append(fullpath)
                if os.path.exists(fullpath):
                    return False

        # ---- 第 2 级：按「本种子自身文件清单」做物理复核 ----
        #
        # v3.0.5 改造：此前这里扫的是 cand["path"] 指向的**整个目录**，
        # 语义是「这个种子**所在目录**里还有文件吗」。一个目录下常同时存放
        # 多个不同种子的文件（实测某剧集目录下有 19 个种子各管一集），
        # 于是「自己的文件已删光、但同目录还有兄弟种子的文件」会被误判为
        # 「未删空」→ 该种子永远回收不掉（功能静默失效）。
        #
        # 现在改为先问下载器「这个种子自己有哪些文件」，只复核这些路径。
        # 取不到清单时**退回旧的扫目录逻辑**——宁可漏回收也不误删。
        own_files: List[str] = []
        if cand is not None:
            own_files = self._get_seed_files(hash_str, cand)
        if own_files:
            if any(os.path.exists(p) for p in own_files):
                # 自己的文件还在 → 保留（与旧逻辑一致）
                return False
            # 自己的文件全没了 → 落到下方「确凿无文件」判定
        elif cand is not None:
            # 取不到清单 → 退回旧的「扫内容目录」逻辑（保守垫）
            content_path = str(cand.get("path") or "").strip()
            if content_path and os.path.exists(content_path):
                # 路径存在：目录要确认其中确无文件，文件则直接算「存在」
                if os.path.isdir(content_path):
                    if self._dir_has_any_file(content_path):
                        return False
                else:
                    return False

        # ---- 只有两级都拿到确凿的「无文件」证据，才允许判定为已删空 ----
        if records_checked == 0 and not own_files and not (cand and cand.get("path")):
            # 完全没有任何可复核的依据：无从判定，保守保留种子
            return False
        return True

    # ---------------------- 种子自身文件清单 ----------------------

    # 下载器模块上「取种子文件清单」的方法名（按优先级探测，兼容别名）
    _TORRENT_FILE_METHODS = ("torrent_files", "get_files")

    def _get_seed_files(self, hash_str: str,
                        cand: Optional[Dict[str, Any]]) -> List[str]:
        """
        取该种子**自身**负责的文件的绝对路径清单。

        与 ``_dir_has_any_file(cand["path"])`` 的**根本区别**：后者的语义是
        「这个种子**所在目录**里还有文件吗」，而本方法回答「这个种子**自己的**
        文件还在吗」。一个目录下常同时存放多个不同种子的文件（实测：某剧集
        目录下 19 个种子各管一集），两种语义在同目录多种子时必然分叉 ——
        前者会把「同目录的其它种子」误当成自己的残留，导致本该回收的种子
        永远回收不掉（v3.0.5 修复的核心缺陷）。

        走 MoviePilot 下载器模块的统一接口 ``torrent_files(tid, downloader)``
        （别名 ``get_files``）。两种下载器的返回形态不同，必须分别处理：

        - **qBittorrent**：``TorrentFilesList``（dict 列表），文件名取
          ``f["name"]`` / ``f.get("name")``
        - **Transmission**：``transmission_rpc.File`` 对象列表，取 ``f.name``

        两种下载器的 ``name`` 均为**相对种子根的路径**（含种子名子目录，
        如 ``The.Long.Watch.../E13.mkv``），必须与下载目录拼接成绝对路径
        才能落到磁盘上复核。

        **拼接基准**的取法（按可靠性排序）：

        1. ``cand["dl_dir"]``（解析种子时记下的下载目录，最可靠）
        2. 兜底：从 ``cand["path"]`` 反推 —— 若 ``path`` 是目录，则
           ``dirname(path)`` 即下载目录；若 ``path`` 是单个文件（qB 的
           ``content_path`` 可能是文件），则取 ``path`` 去掉种子名后的父级

        **保守原则**：任何「取不到」的情况一律返回**空列表** —— 无候选、
        无模块、方法不存在、调用抛异常、返回为空、基准目录取不到。调用方
        据此退回「扫内容目录」的旧逻辑，宁可漏回收也不误删。

        :param hash_str: 下载 hash
        :param cand: 种子候选（需含 ``module``；``dl_dir`` / ``path`` 用于拼接）
        :return: 绝对路径列表；任何异常/不支持/无结果 → 空列表
        """
        if not hash_str or not cand:
            return []
        module = cand.get("module")
        if module is None:
            return []

        raw = self._call_torrent_files(module, hash_str, cand.get("downloader"))
        if not raw:
            return []

        names = self._extract_file_names(raw)
        if not names:
            return []

        # 拼接基准：下载目录。取不到基准目录 → 整体放弃（清单是相对路径，
        # 拼不出绝对路径；绝不拿相对路径去 os.path.exists，那会在进程
        # 工作目录下意外命中，把判定建立在错误依据上）。
        base = self._seed_download_base(cand)
        if not base:
            return []
        return [os.path.normpath(os.path.join(base, name)) for name in names]

    def _call_torrent_files(self, module: Any, hash_str: str,
                            downloader: Any) -> Any:
        """
        调用下载器模块的取文件清单接口，兼容方法名与签名差异。

        方法名按 ``_TORRENT_FILE_METHODS`` 优先探测（``torrent_files`` 为
        新版统一名，``get_files`` 为部分宿主版本的旧名）。签名上，
        少数版本不接受 ``downloader`` 关键字，捕获 ``TypeError`` 后退回
        只传 ``tid`` 重试。

        :param module: 下载器模块对象
        :param hash_str: 种子 hash（作为 tid 传入）
        :param downloader: 下载器实例名，可为 None
        :return: 原始返回（形态由下载器决定）；不可用/异常/无结果 → None
        """
        method = None
        for method_name in self._TORRENT_FILE_METHODS:
            candidate = getattr(module, method_name, None)
            if callable(candidate):
                method = candidate
                break
        if method is None:
            return None
        try:
            return method(tid=hash_str, downloader=downloader)
        except TypeError:
            # 签名不接受 downloader 关键字，退回只传 tid
            try:
                return method(tid=hash_str)
            except Exception as err:
                logger.warning(
                    "【保种空间守护】获取种子文件清单失败（%s）：%s", hash_str, err
                )
                return None
        except Exception as err:
            logger.warning(
                "【保种空间守护】获取种子文件清单失败（%s）：%s", hash_str, err
            )
            return None

    @staticmethod
    def _extract_file_names(raw: Any) -> List[str]:
        """
        从下载器返回的文件清单中提取文件名（相对路径）。

        兼容两种形态：``qBittorrent`` 返回 dict 列表（取 ``["name"]``），
        ``Transmission`` 返回 ``transmission_rpc.File`` 对象列表（取 ``.name``）。

        :param raw: 下载器 ``torrent_files`` 的原始返回
        :return: 非空文件名列表（顺序与清单一致）
        """
        names: List[str] = []
        for item in raw:
            if isinstance(item, dict):
                name = item.get("name")
            else:
                name = getattr(item, "name", None)
            name = str(name or "").strip()
            if name:
                names.append(name)
        return names

    def _seed_download_base(self, cand: Dict[str, Any]) -> str:
        """
        推断该种子文件清单的相对路径基准（即下载器的「保存目录」）。

        优先取 ``cand["dl_dir"]``（``_parse_torrent`` 解析时就地记下）。
        取不到时从 ``cand["path"]`` 反推，两条路径分别对应两种下载器：

        - **Transmission**：``path = os.path.join(dl_dir, name)`` → 是目录，
          下载目录 = ``dirname(path)``
        - **qBittorrent**：``path`` 取 ``content_path``，**可能是单文件路径**
          （实测：单集种子时 content_path 直接指向 .mkv）；此时文件名末尾
          恰好是种子名目录之后的部分，需逐级向上找到「种子名」这一层

        :param cand: 种子候选
        :return: 基准目录绝对路径；无法推断时返回空串
        """
        dl_dir = str(cand.get("dl_dir") or "").strip()
        if dl_dir:
            return dl_dir
        path = str(cand.get("path") or "").strip()
        if not path:
            return ""
        title = str(cand.get("title") or "").strip()
        # 目录形态：path 本身是种子目录 → 上一级即下载目录
        if os.path.isdir(path):
            parent = os.path.dirname(os.path.normpath(path))
            return parent if parent and parent != path else ""
        # 文件形态（qB 单文件种子）：沿路径向上找与种子名同名的目录层
        current = os.path.dirname(os.path.normpath(path))
        limit = 0
        while current and current != os.path.dirname(current) and limit < 8:
            if title and os.path.basename(current) == title:
                parent = os.path.dirname(current)
                return parent if parent and parent != current else ""
            current = os.path.dirname(current)
            limit += 1
        return ""

    @staticmethod
    def _dir_has_any_file(path: str, max_scan: Optional[int] = None,
                          _depth: int = 0) -> bool:
        """
        扫描目录，判断其中是否还存在任何普通文件（含子目录）。

        用于空壳判定的物理复核：只关心「有没有文件」，不关心有多少，
        因此一旦发现第一个文件立即返回 True，避免在超大种子目录上做全量遍历。

        :param path: 待扫描目录
        :param max_scan: 每层最多扫描的条目数（缺省取 DIR_SCAN_MAX_ENTRIES）
        :param _depth: 当前递归深度（内部使用，限制最大深度）
        :return: 目录中是否存在至少一个普通文件
        """
        if max_scan is None:
            max_scan = DIR_SCAN_MAX_ENTRIES
        try:
            count = 0
            with os.scandir(path) as it:
                for entry in it:
                    count += 1
                    if count > max_scan:
                        # 条目过多时保守判定「有文件」，绝不因扫不完而误删
                        return True
                    try:
                        if entry.is_file(follow_symlinks=False):
                            return True
                        if entry.is_dir(follow_symlinks=False) and _depth < 8:
                            # 子目录里有文件同样算「未删空」
                            if SeedSpaceGuard._dir_has_any_file(
                                entry.path, max_scan, _depth + 1
                            ):
                                return True
                    except OSError:
                        # 单个条目读不了：保守认为有内容
                        return True
        except OSError as err:
            logger.error("【保种空间守护】扫描目录失败（%s）：%s", path, err)
            # 读不了目录 → 无从判定 → 保守保留
            return True
        return False

    def _delete_torrent_by_hash(self, hash_str: str, title: str = "") -> bool:
        """
        调用下载器删除指定 hash 的种子（不连带删文件，文件已由插件删除）。

        与「源文件联动清理」不同，这里不复用 ``DownloadFileDeleted`` 事件：
        该事件在 MoviePilot 内置处理器中要求 ``hash`` 字段，而删除动作本身
        也可能被其它插件拦截或异步化；直接调用下载器更可靠、结果可判定。

        删除前会尝试匹配配置的「目标下载器」范围；未配置则遍历全部已启用下载器。
        """
        if not hash_str:
            return False
        try:
            module = self._get_downloader_for(hash_str)
        except Exception as err:
            logger.error("【保种空间守护】获取下载器失败（%s）：%s", hash_str, err)
            return False
        if not module:
            logger.warning(
                "【保种空间守护】未找到 hash %s 对应的下载器，跳过删种%s",
                hash_str, f"（{title}）" if title else "",
            )
            return False
        try:
            ok = module.remove_torrents(
                hashs=[hash_str] if isinstance(hash_str, str) else hash_str,
                delete_file=False,
            )
            if ok:
                logger.info(
                    "【保种空间守护】已联动删除种子%s（hash：%s）",
                    f"：{title}" if title else "", hash_str,
                )
            return bool(ok)
        except Exception as err:
            logger.error("【保种空间守护】联动删除种子失败（%s）：%s", hash_str, err)
            return False

    def _query_torrents_by_hash(self, module, hash_str: str, name: str):
        """
        在单个下载器模块上按 hash 查询种子，兼容新旧方法名。

        MoviePilot 的下载器模块并未在基类统一提供查询方法：v2 起方法名为
        ``list_torrents``，更早的版本叫 ``get_torrents``（``get_torrents``
        实际是 transmission_rpc / qbittorrent-api 客户端实例的方法，不是
        模块方法）。这里按优先级逐个探测，取首个可用方法的结果。

        ``list_torrents`` 默认只返回带 MoviePilot 内置标签的种子，手动添加
        或从别处转移来的种子会被漏掉，因此显式传 ``include_all_tags=True``；
        若该版本的实现不接受此参数，捕获 TypeError 后退回不带参数的调用。

        :param module: 下载器模块实例
        :param hash_str: 种子 hash
        :param name: 下载器名称，仅用于日志
        :return: ``(种子列表, 命中的方法名, 异常)``，语义如下：
                 - 方法名为空：模块不具备任一查询方法（多半不是下载器模块）
                 - 异常非空：查询失败，种子列表不可信
                 - 否则：种子列表即查询结果，可能为空
        """
        for method_name in self._TORRENT_QUERY_METHODS:
            method = getattr(module, method_name, None)
            if not callable(method):
                continue
            attempts = [{"hashs": [hash_str]}]
            if method_name == "list_torrents":
                attempts.insert(0, {"hashs": [hash_str], "include_all_tags": True})
            last_err = None
            for kwargs in attempts:
                try:
                    return method(**kwargs), method_name, None
                except TypeError as err:
                    # 多半是签名不支持 include_all_tags，换一组参数重试
                    last_err = err
                    continue
                except Exception as err:
                    last_err = err
                    break
            logger.warning(
                "【保种空间守护】下载器 %s 的 %s 查询异常（hash：%s）：%s",
                name, method_name, hash_str, last_err,
            )
            return None, method_name, last_err
        return None, "", None

    def _get_downloader_for(self, hash_str: str):
        """
        找出该 hash 所属的下载器实例。

        先在实际持有该种子的下载器中定位（避免下发给不相关的下载器），
        再按配置的「目标下载器」范围过滤；未配置范围时返回命中实例。
        """
        try:
            # V3：ModuleManager 已移除，改由 DownloaderHelper 枚举运行中的下载器
            services = DownloaderHelper().get_services()
        except Exception as err:
            logger.error("【保种空间守护】读取下载器模块失败：%s", err)
            return None
        checked: List[str] = []
        seen: List[str] = []
        for service in services.values():
            name = str(getattr(service, "name", "") or "") or "?"
            # ServiceInfo.module 为下载器模块对象，同时具备种子查询
            # （list_torrents）与删种（remove_torrents）能力
            module = getattr(service, "module", None)
            if module is None:
                seen.append(f"{name}（无模块对象）")
                continue
            torrents, method_name, err = self._query_torrents_by_hash(
                module, hash_str, name
            )
            if not method_name:
                # 不具备查询方法的模块静默跳过
                seen.append(f"{name}（无查询方法）")
                continue
            seen.append(f"{name}（{method_name}）")
            if err is not None:
                checked.append(f"{name}（查询异常）")
                continue
            if not torrents:
                logger.info(
                    "【保种空间守护】下载器 %s 未持有 hash %s 的种子", name, hash_str
                )
                checked.append(f"{name}（未持有）")
                continue
            if self._downloaders and name not in self._downloaders:
                logger.info(
                    "【保种空间守护】下载器 %s 持有 hash %s，但不在配置的目标下载器范围内，跳过",
                    name, hash_str,
                )
                checked.append(f"{name}（范围外）")
                continue
            logger.info(
                "【保种空间守护】定位到下载器 %s 持有 hash %s（查询方法：%s）",
                name, hash_str, method_name,
            )
            return module
        if not checked:
            logger.warning(
                "【保种空间守护】未找到可查询种子的下载器（hash：%s）；已遍历模块：%s",
                hash_str, "、".join(seen) or "无",
            )
        else:
            logger.warning(
                "【保种空间守护】遍历下载器均未命中 hash %s：%s", hash_str, "、".join(checked)
            )
        return None

    def _run_linkage_after_delete(
        self, deleted_paths: List[str], detail_lines: List[str], dry_run: bool
    ) -> Dict[str, int]:
        """
        在文件删除完成后执行联动清理（转移记录 → 种子）。

        顺序有意为之：先删转移记录，最后才判定删种。记录删除不影响文件
        存在性判定，但放在删种之前可保证「删种」是整条链路的最后动作。

        仅文件模式的删种约束（用户明确要求）：
        **必须该种子下的所有文件都已删除，才删除该种子**——用
        ``_seed_fully_removed`` 对磁盘做物理复核，任一文件仍在即保留种子。

        :param deleted_paths: 本轮实际删除的文件路径
        :param detail_lines: 明细行（就地追加）
        :param dry_run: 试运行时不产生任何联动副作用
        :return: 各项联动计数统计
        """
        stats = {"history": 0, "torrent": 0, "torrent_kept": 0}
        if dry_run or not deleted_paths or not self._linkage_enabled():
            return stats

        # 待判定的 hash 集合：多个文件可能同属一个种子，去重后统一判定
        pending_hashes: Dict[str, str] = {}

        for path in deleted_paths:
            if self._delete_history and self._delete_transfer_history(path):
                stats["history"] += 1
                detail_lines.append(f"已删除转移记录：{os.path.basename(path)}")
                logger.info("【保种空间守护】%s", detail_lines[-1])
            if self._delete_torrents:
                hash_str = self._resolve_hash_by_path(path)
                if hash_str:
                    pending_hashes.setdefault(hash_str, path)
                else:
                    # 反查失败会让该文件所属种子彻底失去删种机会，必须留痕；
                    # 常见于未经 MoviePilot 登记的刮削残留或手动放入的文件。
                    logger.warning(
                        "【保种空间守护】无法按路径归属种子，跳过该文件的删种判定：%s",
                        path,
                    )

        # 种子级判定：仅文件模式下「所有文件都删完」才删种
        if self._delete_torrents and self._mode == "file":
            # 按 hash 取一次种子的**完整候选对象**，供物理复核使用。
            #
            # ⚠️ 这里必须传完整候选（含 module / dl_dir / path），不能只传
            # path：_seed_fully_removed 现在要先向下载器查「这个种子自己的
            # 文件清单」（_get_seed_files），而该方法需要 cand["module"]。
            # 只传 path 会让清单永远取不到 → 退回扫目录 → 同目录多种子时
            # 依旧误判「未删空」，本次修复在该链路等于没做（v3.0.5 补漏）。
            #
            # 同样不能用「被删文件的父目录」当复核对象：一个目录下常同时存放
            # 多个不同种子的文件，扫父目录会把「同目录的其它种子」误当成自己的
            # 残留，导致本该删掉的种子永远保留（功能静默失效）。
            cand_by_hash: Dict[str, Dict[str, Any]] = {}
            try:
                for cand in self._collect_seed_candidates():
                    h = str(cand.get("hash") or "")
                    if h and h not in cand_by_hash:
                        cand_by_hash[h] = cand
            except Exception as err:
                logger.error("【保种空间守护】联动删种前获取种子信息失败：%s", err)

            for hash_str, sample_path in pending_hashes.items():
                # 取不到候选（多为范围外/解析失败）→ probe=None，
                # 退化为「仅有第 1 级记录复核」，与改造前行为一致，不误删
                probe = cand_by_hash.get(hash_str)
                if self._seed_fully_removed(hash_str, probe):
                    if self._delete_torrent_by_hash(hash_str,
                                                    os.path.basename(sample_path)):
                        stats["torrent"] += 1
                        detail_lines.append(f"已联动删除种子（该种子文件已全部删除）：{hash_str}")
                        logger.info("【保种空间守护】%s", detail_lines[-1])
                else:
                    stats["torrent_kept"] += 1
                    logger.info(
                        "【保种空间守护】种子 %s 仍有文件未删除，保留种子不删", hash_str
                    )
        return stats

    def _run_linkage_on_seed_deleted(
        self, cand: Dict[str, Any], detail_lines: List[str], dry_run: bool
    ) -> int:
        """
        种子级模式下，种子（连带文件）已被下载器删除后的联动收尾。

        此模式下种子本身就是清理对象，不存在「文件未删完」的顾虑，因此
        **不执行删种判定**，只处理文件侧的遗留：删除该种子下各文件的转移历史记录。

        文件路径来源见 ``_seed_file_paths``；拿不到时跳过，不做全盘扫描。

        :return: 本次清理的转移记录条数（供通知聚合，不进明细）
        """
        if dry_run or not self._linkage_enabled():
            return 0
        paths = self._seed_file_paths(cand)
        if not paths:
            return 0
        history = 0
        for path in paths:
            if self._delete_history and self._delete_transfer_history(path):
                history += 1
        if history:
            detail_lines.append(f"已删除转移记录 {history} 条（种子：{cand['title']}）")
            logger.info("【保种空间守护】%s", detail_lines[-1])
        return history

    def _seed_file_paths(self, cand: Dict[str, Any]) -> List[str]:
        """
        取种子对应的本地文件路径清单。

        优先级：
        1. 种子自带的文件清单（``cand["files"]``，若上游提供）
        2. 按 hash 查 MoviePilot 下载文件记录（最可靠，与「仅文件」模式同源）
        3. 按「配置目录 + 种子名」推断，仅返回磁盘上确实存在过的路径

        均不做全盘遍历，避免在大媒体库上产生额外开销。
        """
        paths = [str(p) for p in (cand.get("files") or []) if str(p).strip()]
        if paths:
            return paths
        hash_str = str(cand.get("hash") or "").strip()
        if hash_str and self._downloadhis is None:
            # 种子级模式下若未开启「删除转移记录」，oper 可能未初始化，
            # 这里按需补建，使刮削/记录清理可用
            try:
                self._downloadhis = DownloadHistoryOper()
            except Exception as err:
                logger.error("【保种空间守护】初始化下载历史操作器失败：%s", err)
        if hash_str and self._downloadhis:
            try:
                records = self._downloadhis.get_files_by_hash(hash_str) or []
                paths = [
                    str(getattr(r, "fullpath", "") or "").strip()
                    for r in records
                    if str(getattr(r, "fullpath", "") or "").strip()
                ]
            except Exception as err:
                logger.error("【保种空间守护】按 hash 查询文件记录失败：%s", err)
        if paths:
            return paths
        title = str(cand.get("title") or "").strip()
        if not title:
            return []
        guess: List[str] = []
        for base in self._active_dirs or self._target_dirs:
            candidate = os.path.join(base, title)
            if os.path.exists(candidate):
                guess.append(candidate)
        return guess

    def _collect_seed_candidates(self) -> List[Dict[str, Any]]:
        """
        收集目标目录下所有下载器的已完成种子候选。

        排除两类种子：**未完成**的（下载中/暂停/校验中），以及**内容路径不在
        监控目录内**的。排除数量会在汇总日志中列出，便于核对「下载器里有 N 个
        种子，为何只有 M 个进入候选」。

        :return: 候选列表，每项含 module/downloader/hash/title/added/size_gb
        """
        return self._collect_candidates(scope_only=True)

    def _collect_all_seed_candidates(self) -> List[Dict[str, Any]]:
        """
        收集**全部**已完成种子候选（**不判内容路径范围**），供空壳回收使用。

        与 ``_collect_seed_candidates`` 的**唯一差异**是取消范围过滤：

        - ``_collect_seed_candidates`` 会把 ``content_path`` 不在监控目录内的
          种子标为 ``out_of_scope`` 并丢弃；
        - 本方法**保留**它们。理由：**空壳越彻底越容易被范围过滤挡掉** ——
          空壳的典型形态就是「文件全没了、目录也没了」，此时 ``content_path``
          指向已不存在的路径，``_path_under_any`` 的前缀匹配必然失败。于是
          「一个种子越是变成空壳，越进不了空壳回收的候选」，最该回收的那批
          反而被拦在门外（实测：3 个空壳只回收 1 个）。

        其余行为与 ``_collect_seed_candidates`` **完全一致**：仍过滤未完成种子、
        仍复用 ``_parse_torrent`` 解析、仍受「目标下载器」配置约束。

        > 未完成种子（``progress < 0.999``）的文件被删光同样会形成空壳，但
        > 处理它涉及「暂停态可能被用户主动保留待续传」的语义分歧，**本版
        > 暂不覆盖**，留待后续评估。

        :return: 候选列表（结构同 ``_collect_seed_candidates``）
        """
        return self._collect_candidates(scope_only=False)

    def _collect_candidates(self, scope_only: bool) -> List[Dict[str, Any]]:
        """
        候选收集核心实现，由上面两个入口按 ``scope_only`` 择路调用。

        :param scope_only: True=只收监控目录内的种子（主链路）；
                           False=不过滤范围（空壳回收用）
        :return: 候选列表，每项含 module/downloader/hash/title/added/size_gb
        """
        candidates: List[Dict[str, Any]] = []
        counted = {"total": 0, "unfinished": 0, "out_of_scope": 0}
        try:
            services = DownloaderHelper().get_services()
        except Exception as err:
            logger.error("【保种空间守护】获取下载器失败：%s", err)
            return candidates
        for name, service in services.items():
            # 仅处理配置选定的目标下载器（空=全部）
            if self._downloaders and name not in self._downloaders:
                continue
            # DownloaderConf.type 是字符串；_parse_torrent 依赖 DownloaderType
            # 枚举做严格判定，必须先归一化
            dl_type = self._resolve_downloader_type(service.type)
            if dl_type not in (DownloaderType.Qbittorrent,
                               DownloaderType.Transmission):
                continue
            # instance 为具体客户端实例（负责枚举种子），
            # module 为下载器模块对象（负责后续删种）
            server = getattr(service, "instance", None)
            module = getattr(service, "module", None)
            if server is None or module is None:
                logger.error("【保种空间守护】下载器 %s 缺少可用实例或模块，跳过", name)
                continue
            try:
                ret = server.get_torrents()
            except Exception as err:
                logger.error("【保种空间守护】读取下载器 %s 种子列表失败：%s", name, err)
                continue
            items = ret[0] if isinstance(ret, tuple) else ret
            for item in items or []:
                counted["total"] += 1
                cand = self._parse_torrent(dl_type, item)
                if not cand:
                    # 未完成（下载中/暂停/校验）的种子一律排除。
                    # 两个入口在此**行为一致**——空壳回收暂不覆盖未完成种子。
                    counted["unfinished"] += 1
                    continue
                in_scope = bool(cand["path"]) and self._path_under_any(cand["path"])
                if not in_scope:
                    counted["out_of_scope"] += 1
                    # 主链路：范围外直接丢弃；空壳回收：保留（见上方方法说明）
                    if scope_only:
                        continue
                cand["module"] = module
                cand["downloader"] = name
                candidates.append(cand)
        # 汇总日志：说明「下载器里有多少 / 为何只剩这些候选」，
        # 避免用户看到「tr 有一千多个种子却只扫了几百个」时无从判断
        if scope_only:
            logger.info(
                "【保种空间守护】种子候选收集：下载器共 %d 个，"
                "未完成 %d 个，不在监控目录 %d 个，纳入候选 %d 个",
                counted["total"], counted["unfinished"],
                counted["out_of_scope"], len(candidates),
            )
        else:
            # 空壳回收口径：范围外种子被保留，日志要如实说明，避免用户
            # 误以为「不在监控目录的种子也被排除」而放弃排查
            logger.info(
                "【保种空间守护】种子候选收集（含监控目录外）：下载器共 %d 个，"
                "未完成 %d 个（仍排除），范围外 %d 个（本次保留），纳入候选 %d 个",
                counted["total"], counted["unfinished"],
                counted["out_of_scope"], len(candidates),
            )
        return candidates

    @staticmethod
    def _pick_attr(item: Any, *names: str, default: Any = None) -> Any:
        """
        按候选属性名依次取值，兼容同一字段的不同命名风格。

        ``transmission_rpc`` 的 ``Torrent`` 对象暴露的是 **snake_case** 属性
        （``percent_done`` / ``download_dir`` / ``total_size``），而 MoviePilot
        在部分版本或包装层里会转成 **camelCase**（``percentDone`` / ``downloadDir``）。
        插件此前只按 camelCase 取，导致 Transmission 的种子全部因
        ``percentDone`` 缺失而被判为「未完成」丢弃——实测 1765 个种子全数解析失败，
        删种与空壳回收彻底失效。这里对两种命名都做兼容。

        :param item: 种子对象（对象或 dict）
        :param names: 候选属性名，按顺序取第一个「存在且非 None」的值
        :param default: 全部缺失时的返回值
        :return: 取到的值或 default
        """
        for name in names:
            if isinstance(item, dict):
                if name in item and item[name] is not None:
                    return item[name]
                continue
            value = getattr(item, name, None)
            if value is not None:
                return value
        return default

    def _parse_torrent(self, dl_type: DownloaderType,
                       item: Any) -> Optional[Dict[str, Any]]:
        """
        解析单个种子对象为统一候选结构。

        :param dl_type: 下载器类型
        :param item: 下载器返回的种子对象
        :return: 候选字典或 None（未完成/缺关键字段）
        """
        if dl_type == DownloaderType.Qbittorrent:
            # qBittorrent：TorrentDictionary，camelCase 之外的键名保持原样
            progress = float(self._pick_attr(item, "progress", default=0) or 0)
            if progress < 0.999:
                return None
            path = (
                self._pick_attr(item, "content_path", "contentPath", default="")
                or self._pick_attr(item, "save_path", "savePath", default="")
                or ""
            )
            # 保种起点优先取完成时间（completion_on），无则退化为添加时间
            added = int(
                self._pick_attr(item, "completion_on", "completionOn",
                                "added_on", "addedOn", default=0) or 0
            )
            return {
                "path": path,
                "hash": self._pick_attr(item, "hash", default=""),
                "title": self._pick_attr(item, "name", default="") or "",
                "added": added,
                "size_gb": round(
                    int(self._pick_attr(item, "size", "total_size",
                                        "totalSize", default=0) or 0)
                    / (1024 ** 3), 1
                ),
                # 种子文件清单（torrent_files）返回的是**相对路径**，
                # 需要一个基准目录才能拼成绝对路径做物理复核。
                # qB 的 save_path 即「保存目录」，语义正确。
                "dl_dir": str(
                    self._pick_attr(item, "save_path", "savePath",
                                    default="") or ""
                ).strip(),
            }
        # Transmission：Torrent 对象（snake_case）或 dict（camelCase）
        percent = float(
            self._pick_attr(item, "percent_done", "percentDone", default=0) or 0
        )
        if percent < 0.999:
            return None
        dl_dir = self._pick_attr(item, "download_dir", "downloadDir", default="") or ""
        name = self._pick_attr(item, "name", default="") or ""
        path = os.path.join(dl_dir, name) if dl_dir else ""
        # 保种起点优先取完成时间（done_date），无则退化为添加时间（addedDate）
        done = self._pick_attr(item, "done_date", "date_done", "doneDate",
                               "dateDone", default=None)
        added = 0
        if done is not None:
            try:
                added = int(done.timestamp())
            except (AttributeError, OSError, ValueError):
                added = 0
        if not added:
            added = int(
                self._pick_attr(item, "added_date", "addedDate", default=0) or 0
            )
        return {
            "path": path,
            "hash": self._pick_attr(item, "hash_string", "hashString",
                                    "id", default="") or "",
            "title": name,
            "added": added,
            "size_gb": round(
                int(self._pick_attr(item, "total_size", "totalSize", default=0) or 0)
                / (1024 ** 3), 1
            ),
            # 文件清单的相对路径基准（TR 即 download_dir）
            "dl_dir": str(dl_dir).strip(),
        }

    # ============ 种子级连带清理（硬链接 + 辅种） ============

    @staticmethod
    def _content_key(cand: Dict[str, Any]) -> Tuple[str, str]:
        """
        计算种子的「内容身份」键，用于识别辅种。

        键为 (归一化内容路径, 归一化种子名)。**两者都必须相同**才视为同一
        内容的不同种子：

        - 同目录 + 同种子名 + 多 hash → **真辅种**（同一内容在不同站点各一条
          种子），文件已删则无做种意义，应连带删除
        - 同目录 + **不同**种子名 + 多 hash → **合集/多集各自成种**（如一部剧
          33 集各自一条种子共用一个目录），**绝不能**连带删除

        实测生产环境：真辅种 29 组（单组最多 19 个 hash），合集多集 121 组。
        仅凭路径判定会把后者全部误删，是本次改造最高危的点。

        :param cand: 种子候选
        :return: (normpath(path), title.casefold())
        """
        path = os.path.normpath(str(cand.get("path") or ""))
        title = str(cand.get("title") or "").strip().casefold()
        return path, title

    def _build_companion_map(self, cands: List[Dict[str, Any]]
                             ) -> Dict[Tuple[str, str], List[str]]:
        """
        构建「内容键 → hash 列表」映射，整轮复用。

        只纳入已被 ``_collect_seed_candidates`` 判定为「内容路径在监控目录内」
        的种子——即辅种连带同样受「只删监控目录内种子」的约束，范围外的同名
        种子不会被牵连。

        :param cands: 候选种子列表
        :return: {(path, title): [hash, ...]}
        """
        mapping: Dict[Tuple[str, str], List[str]] = {}
        for cand in cands:
            key = self._content_key(cand)
            h = str(cand.get("hash") or "")
            if not key[0] or not key[1] or not h:
                continue
            mapping.setdefault(key, []).append(h)
        return mapping

    def _seed_related_paths(self, cand: Dict[str, Any]) -> List[str]:
        """
        汇总某种子在配置目录内的全部可能路径（下载侧 + 媒体库侧）。

        与 ``_seed_file_paths`` 的区别：本方法**不过滤存在性**，且额外补充
        「按种子名在各配置目录下推断的路径」——下载目录与媒体库目录常常同名
        同结构，媒体库侧的硬链接正是落在这里。

        这三个来源缺一不可：
        1. DownloadFiles 记录 → 下载侧的权威清单（含各集文件的完整路径）
        2. 各配置目录 + 种子名 → 媒体库侧硬链接的落点
        3. 下载器报告的 content_path → 兜底（记录缺失/路径迁移时）

        :param cand: 种子候选
        :return: 去重后的路径列表（可能含尚不存在的路径，由调用方过滤）
        """
        paths: List[str] = []
        seen: set = set()

        def _add(p: str) -> None:
            p = os.path.normpath(str(p or "").strip())
            if p and p != "." and p not in seen:
                seen.add(p)
                paths.append(p)

        # ① DownloadFiles 记录：下载侧权威清单
        for p in self._seed_file_paths(cand):
            _add(p)
        # ② 各配置目录 + 种子名：媒体库侧硬链接的落点
        title = str(cand.get("title") or "").strip()
        if title:
            for base in (self._active_dirs or self._target_dirs):
                _add(os.path.join(base, title))
        # ③ 下载器报告的真实内容路径
        _add(str(cand.get("path") or ""))
        return paths

    def _build_inode_index(self, paths: List[str]
                           ) -> Dict[Tuple[int, int], List[str]]:
        """
        对给定路径清单建立 inode 索引（不遍历全盘）。

        复用 ``_index_files`` 的 ``(st_dev, st_ino)`` 身份判定与 ``os.lstat``
        语义（symlink 不算普通文件），但作用域限定为「本次要删的种子涉及的
        路径」，避免在大媒体库上做全盘 walk。

        索引必须**在删种之前**建立：删种后下载器会删掉下载侧文件，届时再想
        枚举该 inode 的所有路径已不可能（下载侧路径消失，只剩媒体库侧孤证）。

        :param paths: 待索引的路径（文件或目录）
        :return: (st_dev, st_ino) -> 该 inode 在配置目录内的全部路径
        """
        index: Dict[Tuple[int, int], List[str]] = {}

        def _record(fpath: str) -> None:
            st = os.lstat(fpath)
            if not stat.S_ISREG(st.st_mode):
                return
            plist = index.setdefault((st.st_dev, st.st_ino), [])
            if fpath not in plist:
                plist.append(fpath)

        for p in paths:
            if not p or not self._path_under_any(p):
                continue
            if os.path.isdir(p):
                for root, dirs, fnames in os.walk(p):
                    # 与 _index_files 一致：跳过 DSM 系统目录与回收站
                    dirs[:] = [d for d in dirs
                               if not d.startswith("@") and d != "#recycle"]
                    for fname in fnames:
                        _record(os.path.join(root, fname))
            else:
                _record(p)
        return index

    def _clean_hardlinks_for(
        self, ino_index: Dict[Tuple[int, int], List[str]], title: str = ""
    ) -> int:
        """
        删除给定 inode 索引中仍存在于磁盘的同 inode 路径（媒体库侧硬链接）。

        复用 ``_delete_one`` 的三层安全边界（词法校验 / inode 复核 / realpath
        校验），确保不会越出配置目录、不会误删已被重建的同名新文件。

        调用时机：**主种子删除之后**。此时下载侧路径已被下载器删除，索引中
        仍存在的基本就是媒体库侧的硬链接。

        :param ino_index: ``_build_inode_index`` 的返回值
        :param title: 种子名（仅用于日志）
        :return: 成功 unlink 的路径数
        """
        if not ino_index:
            return 0
        # _delete_one 从该成员取「同 inode 的全部路径」
        self._ino_paths = ino_index
        removed = 0
        for key, plist in ino_index.items():
            for path in plist:
                if not os.path.exists(path):
                    # 下载侧已被下载器删除，属预期
                    continue
                removed += self._delete_one(path, key)
        return removed

    # ============================ 仅文件清理 ============================

    def _index_files(self, patterns: List[str], recent_secs: float
                     ) -> Tuple[List[Tuple[float, str, int, Tuple[int, int]]],
                                Dict[Tuple[int, int], List[str]]]:
        """
        遍历所有配置目录，按 inode 建立文件索引。

        下载目录与媒体库目录中的同名文件常为同一 inode 的硬链接：删除单个路径
        （``os.remove``）只是减少一处目录项引用，inode 仍被其它链接引用，**空间
        不会释放**。因此这里以 ``(st_dev, st_ino)`` 为文件身份，把同一文件在所有
        配置目录内的全部路径收集起来，删除时一并处理。

        关键点：
        - 使用 ``os.lstat`` 而非 ``os.stat``：symlink 会被 ``lstat`` 标记为链接
          类型，从而被 ``S_ISREG`` 过滤掉；用 ``stat`` 会取到目标文件的 inode，
          导致把符号链接误认为硬链接而删除错误对象
        - ``st_size`` 每个 inode 只累计**一份**，避免同一文件两侧各计一次、
          预计释放量虚高约 100%
        - inode 去重判定在保护期过滤**之前**：同一 inode 两侧 mtime 必然相同，
          但若先过滤 mtime，一旦某侧被保护期拦截会出现去重失效
        - 除 protect_pattern 命中的文件外，**目录下所有文件均纳入候选**
          （含 nfo/图片/字幕等刮削产物）：统一由一条规则决定删除范围，
          不区分文件类型。需要保留的文件请写入「保护文件后缀」

        :param patterns: 保护文件后缀通配符列表
        :param recent_secs: 保护期秒数，最近修改的文件不纳入候选
        :return: (候选文件列表, inode→全部路径映射)
                 候选元素为 (mtime, 代表路径, 大小字节, inode 键)，按 mtime 升序
        """
        now = time.time()
        files: List[Tuple[float, str, int, Tuple[int, int]]] = []
        ino_paths: Dict[Tuple[int, int], List[str]] = {}
        seen_ino: set = set()
        for target in (self._active_dirs or self._target_dirs):
            for root, dirs, fnames in os.walk(target):
                # 排除 DSM 系统目录（@eaDir/@tmp/@SynoFinder/@Recently-Snapshot 等）
                # 与回收站 #recycle，避免把 0 字节系统文件纳入清理候选
                dirs[:] = [d for d in dirs if not d.startswith("@") and d != "#recycle"]
                for fname in fnames:
                    if any(self._match_pattern(fname, pat) for pat in patterns):
                        continue
                    fpath = os.path.join(root, fname)
                    try:
                        # lstat：不跟随符号链接，symlink 会被下面的 S_ISREG 排除
                        st = os.lstat(fpath)
                    except OSError:
                        continue
                    if not stat.S_ISREG(st.st_mode):
                        # 跳过 symlink/目录/设备文件/fifo 等非普通文件
                        continue
                    key = (st.st_dev, st.st_ino)
                    # 先登记路径映射（不受保护期影响），确保双侧都能被识别到
                    paths = ino_paths.setdefault(key, [])
                    if fpath not in paths:
                        paths.append(fpath)
                    if key in seen_ino:
                        continue
                    if now - st.st_mtime < recent_secs:
                        continue
                    seen_ino.add(key)
                    files.append((st.st_mtime, fpath, st.st_size, key))
        files.sort(key=lambda x: x[0])
        return files, ino_paths

    def _delete_one(self, fpath: str, key: Tuple[int, int]) -> int:
        """
        删除同一 inode 在所有配置目录内的全部路径（硬链接两侧一并删除）。

        三层安全边界，任一不满足即跳过该路径：
        1. 词法校验：路径必须落在某个配置目录内
        2. inode 复核：``lstat`` 确认仍是普通文件且 inode 未变
           （防止遍历之后下载器重建了同名文件，删错新文件）
        3. realpath 校验：解析符号链接后仍须在配置目录内，杜绝链接逃逸

        :param fpath: 代表路径（仅在索引缺失时作为兜底）
        :param key: 文件身份 (st_dev, st_ino)
        :return: 成功 unlink 的路径数
        """
        removed = 0
        for path in self._ino_paths.get(key, [fpath]):
            # 边界①：必须在配置目录内
            if not self._path_under_any(path):
                logger.warning("【保种空间守护】跳过配置目录外的路径：%s", path)
                continue
            try:
                st = os.lstat(path)
            except FileNotFoundError:
                # 已不存在（可能被其它进程删除），视为已处理
                removed += 1
                continue
            except OSError as err:
                logger.warning("【保种空间守护】无法读取 %s：%s", path, err)
                continue
            # 边界②：仍是普通文件且 inode 未变
            if not stat.S_ISREG(st.st_mode):
                logger.warning("【保种空间守护】跳过非普通文件：%s", path)
                continue
            if (st.st_dev, st.st_ino) != key:
                logger.warning("【保种空间守护】inode 已变化，跳过（文件可能被重建）：%s", path)
                continue
            # 边界③：realpath 不得越界
            if not self._path_under_any(os.path.realpath(path)):
                logger.warning("【保种空间守护】realpath 越界，跳过：%s", path)
                continue
            try:
                os.unlink(path)
                removed += 1
                # 删除后清理可能遗留的空目录（保护配置目录本身）
                self._prune_empty_dirs(path)
            except OSError as err:
                logger.warning("【保种空间守护】删除文件 %s 失败：%s", path, err)
        return removed

    def _clean_by_file(self, free_bytes: int, dry_run: bool) -> Tuple[int, float, List[str]]:
        """
        仅文件清理：按文件修改时间从旧到新删除配置目录内文件，
        直到剩余空间恢复到阈值以上。

        下载目录与媒体库目录中的同名文件常为同一 inode 的硬链接：只删一侧空间
        不会释放。因此本方法按 ``(st_dev, st_ino)`` 识别硬链接并**两侧一并删除**，
        无需依赖「源文件联动清理」等插件。

        真实删除采用「按缺口预选 → 删除 → 等待释放 → 复核」的分轮策略：
        删除后轮询等待空间真正回收，再实测空间决定是否按新缺口补删下一轮。
        若空间几乎未释放（如仍有配置目录外的硬链接、或 Btrfs 快照占用），
        立即停止并告警，宁可空间不足也不过量删除。

        :param free_bytes: 当前剩余空间（字节）
        :param dry_run: 是否试运行
        :return: （处理数，预计/实际释放 GB，明细行）
        """
        patterns = [p.strip() for p in re.split(r"[,|，]", self._protect_pattern) if p.strip()]
        recent_secs = self._recent_skip_days * 86400
        files, ino_paths = self._index_files(patterns, recent_secs)
        self._ino_paths = ino_paths

        detail_lines: List[str] = []
        deleted = 0
        released_gb = 0.0
        # 本方法自身也保证统计存在（测试可能直接调用），不与 check_and_clean 重置冲突
        if "files" not in getattr(self, "_clean_stats", {}):
            self._clean_stats = {
                "files": 0, "transfers": 0, "seeds": 0, "companions": 0,
                "orphans": 0, "stalled": False, "dry_run": dry_run,
            }
        self._clean_stats["dry_run"] = dry_run

        # 试运行：空间不会真正释放，用累计文件大小模拟释放量判断是否达标
        if dry_run:
            est_free = free_bytes / GIB
            for mtime, fpath, size, key in files:
                if est_free >= self._threshold_gb:
                    break
                mtime_text = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
                linked = ino_paths.get(key, [fpath])
                side_note = (
                    f"，含 {len(linked)} 条路径（硬链接，共占 {round(size / GIB, 1)}GB）"
                    if len(linked) > 1 else ""
                )
                detail_lines.append(
                    f"[试运行] 将删除：{fpath}（修改于 {mtime_text}，"
                    f"{round(size / GIB, 1)}GB{side_note}）"
                )
                logger.info("【保种空间守护】%s", detail_lines[-1])
                deleted += 1
                released_gb += size / GIB
                est_free += size / GIB
            self._clean_stats["files"] = deleted
            return deleted, round(released_gb, 1), detail_lines

        # 真实删除：分轮按缺口预选文件并删除，等待空间真正回收后复核，
        # 达标或候选耗尽即止；出现「删了也不释放」的硬信号则立即停止
        idx = 0
        stall = 0
        # 本轮实际删除的文件路径，供删除结束后执行联动清理
        deleted_paths: List[str] = []
        while True:
            free_now = self._disk_free_bytes()
            free_now = free_bytes if free_now is None else free_now
            if free_now >= self._threshold_gb * GIB:
                break
            # 本轮按缺口预选文件：从最旧开始累计名义大小达到缺口即止
            gap_bytes = max(GIB, self._threshold_gb * GIB - free_now)
            plan: List[Tuple[float, str, int, Tuple[int, int]]] = []
            planned_bytes = 0.0
            while idx < len(files):
                item = files[idx]
                idx += 1
                plan.append(item)
                planned_bytes += float(item[2])
                if planned_bytes >= gap_bytes:
                    break
            if not plan:
                # 候选耗尽仍未达标，结束
                break
            for mtime, fpath, size, key in plan:
                removed = self._delete_one(fpath, key)
                if removed <= 0:
                    continue
                deleted += 1
                released_gb += size / GIB
                # 记录代表路径（仅一次），供联动清理反查 hash / 转移记录 / 刮削
                if fpath not in deleted_paths:
                    deleted_paths.append(fpath)
                mtime_text = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
                linked = self._ino_paths.get(key, [fpath])
                side_note = (
                    f"，连同其余 {len(linked) - 1} 条路径一并删除"
                    if len(linked) > 1 else ""
                )
                detail_lines.append(
                    f"已删除文件：{fpath}（修改于 {mtime_text}{side_note}）"
                )
                logger.info("【保种空间守护】%s", detail_lines[-1])
            # 轮询等待空间释放（达标提前退出），再复核
            logger.info(
                "【保种空间守护】本轮已删除 %d 个文件，等待空间释放（最长 %d 秒）后复核…",
                len(plan), self._sync_wait_seconds,
            )
            released_bytes = self._wait_for_release(free_now, int(planned_bytes))
            if not self._release_is_healthy(released_bytes, int(planned_bytes)):
                stall += 1
                if released_bytes <= 0 or stall >= MAX_STALL_ROUNDS:
                    warn = (
                        f"删除 {len(plan)} 个文件后空间未按预期释放"
                        f"（预期 {planned_bytes / GIB:.1f}GB，"
                        f"实测 {released_bytes / GIB:.1f}GB），"
                        f"可能存在快照引用，或该文件的其它硬链接不在配置目录内。"
                        f"已停止清理以避免过量删除，请检查「保种/清理目录」"
                        f"是否已包含与之互为硬链接的全部目录"
                    )
                    logger.warning("【保种空间守护】%s", warn)
                    detail_lines.append(warn)
                    self._clean_stats["stalled"] = True
                    break
        # 删除流程结束后统一执行联动清理（转移记录 / 种子）
        if deleted_paths and self._linkage_enabled():
            stats = self._run_linkage_after_delete(deleted_paths, detail_lines, dry_run)
            self._clean_stats["transfers"] += stats["history"]
            self._clean_stats["seeds"] += stats["torrent"]
            logger.info(
                "【保种空间守护】联动清理完成：转移记录 %d 条，"
                "删除种子 %d 个，保留种子 %d 个",
                stats["history"], stats["torrent"], stats["torrent_kept"],
            )
        self._clean_stats["files"] = deleted
        return deleted, round(released_gb, 1), detail_lines

    # ============================ 工具方法 ============================

    def _owner_dir_of(self, path: str) -> Optional[str]:
        """
        找出路径所属的配置目录（取最长匹配）。

        _parse_dirs 已剔除嵌套目录，因此正常情况下 owner 唯一；这里取最长匹配
        仅作防御，避免配置被绕过修改后出现歧义。

        :param path: 待判断路径
        :return: 所属配置目录，不属于任何配置目录时返回 None
        """
        owner: Optional[str] = None
        for target in (self._active_dirs or self._target_dirs):
            normalized = os.path.normpath(target)
            if path == normalized or path.startswith(normalized + os.sep):
                if owner is None or len(normalized) > len(owner):
                    owner = normalized
        return owner

    def _prune_empty_dirs(self, file_or_dir: str) -> None:
        """
        自底向上清理删除后遗留的空目录，直到所属配置目录为止。
        - 每层先清理 @eaDir 中「已无对应真实文件」的 DSM 索引残片：Synology 不会
          自动回收这些残片，会阻止父目录 rmdir，留下「仅剩 @eaDir」的空壳目录
        - 只删除空目录（os.rmdir 语义，非空目录会抛 OSError 自动停止）
        - 不删除配置目录本身
        - 目录已不存在（如下载器删除种子时已顺带清理）则继续向上

        :param file_or_dir: 被删除的文件或目录路径，从它所在层开始向上清理
        """
        start = os.path.normpath(
            file_or_dir if os.path.isdir(file_or_dir) else os.path.dirname(file_or_dir) or ""
        )
        owner = self._owner_dir_of(start)
        if not owner:
            # 不属于任何配置目录，不动它
            return
        current = start
        while current and current != owner and current.startswith(owner + os.sep):
            # 先清 DSM 索引残片，否则仅剩 @eaDir 的目录永远无法 rmdir
            self._purge_syno_index(current)
            try:
                os.rmdir(current)
            except FileNotFoundError:
                # 目录已被其他进程删除，继续向上清理父级
                pass
            except OSError:
                # 目录仍有内容（未删除文件或不可清理的残片），停止向上清理
                break
            current = os.path.dirname(current)
        # 配置目录本身不删除，但其内部的索引残片同样会长期堆积，需一并清理
        self._purge_syno_index(owner)

    # ---------------------- DSM 索引残片清理 ----------------------

    def _purge_syno_index(self, dir_path: str) -> int:
        """
        清理目录内 @eaDir 中已失去对应真实文件的 DSM 媒体索引残片。

        Synology 会为媒体文件在 @eaDir 下创建「同名子目录 + SYNOINDEX_MEDIA_INFO」，
        且不随原文件删除自动回收，会导致父目录长期非空、无法 rmdir。

        :param dir_path: 待清理目录
        :return: 清理的条目数
        """
        if not dir_path:
            return 0
        ea_dir = os.path.join(dir_path, self._SYNO_META_DIR)
        if not os.path.isdir(ea_dir):
            return 0
        try:
            entries = os.listdir(ea_dir)
        except OSError:
            return 0
        removed = 0
        for entry in entries:
            if entry == self._SYNO_META_DIR:
                continue
            # 真实文件仍在 → 保留其索引，避免误删仍在保种内容的元数据
            if os.path.exists(os.path.join(dir_path, entry)):
                continue
            entry_path = os.path.join(ea_dir, entry)
            if not self._is_syno_meta_tree(entry_path):
                logger.debug("【保种空间守护】跳过非 DSM 索引残片：%s", entry_path)
                continue
            try:
                if os.path.isdir(entry_path) and not os.path.islink(entry_path):
                    shutil.rmtree(entry_path)
                else:
                    os.remove(entry_path)
                removed += 1
            except OSError as err:
                logger.warning("【保种空间守护】清理 DSM 索引残片 %s 失败：%s", entry_path, err)
        # 索引目录已空则一并删除，父目录才能继续向上清理
        try:
            if os.path.isdir(ea_dir) and not os.listdir(ea_dir):
                os.rmdir(ea_dir)
        except OSError:
            pass
        if removed:
            logger.info("【保种空间守护】已清理 %d 项 DSM 索引残片：%s", removed, ea_dir)
        return removed

    def _is_syno_meta_tree(self, path: str) -> bool:
        """
        判断路径是否为 DSM 自动生成的媒体索引残片（内部只含 SYNOINDEX 等元数据文件）。

        只认可白名单内的元数据文件，任一无关键都会让整个条目被保留，
        避免误删 @eaDir 下可能存在的用户数据。

        :param path: 待判断路径
        :return: 是否可安全删除
        """
        if os.path.isdir(path) and not os.path.islink(path):
            for _root, _dirs, fnames in os.walk(path):
                for fname in fnames:
                    if not self._is_syno_meta_file(fname):
                        return False
            return True
        return self._is_syno_meta_file(os.path.basename(path))

    def _is_syno_meta_file(self, name: str) -> bool:
        """
        判断文件名是否为 DSM 媒体索引元数据文件。

        :param name: 文件名
        :return: 是否为 DSM 索引文件
        """
        if not name:
            return False
        if name in self._SYNO_META_FILES:
            return True
        upper = name.upper()
        return any(upper.startswith(prefix) for prefix in self._SYNO_META_FILE_PREFIXES)

    @staticmethod
    def _path_under(path: str, target_dir: str) -> bool:
        """
        判断路径是否位于目标目录下。

        :param path: 待判断路径
        :param target_dir: 目标目录
        :return: 是否在其下
        """
        if not path:
            return False
        norm = os.path.normpath(path).replace("\\", "/")
        target = os.path.normpath(target_dir).replace("\\", "/")
        return norm == target or norm.startswith(target + "/")

    def _path_under_any(self, path: str, dirs: Optional[List[str]] = None) -> bool:
        """
        判断路径是否位于任一配置目录下（安全边界校验用）。

        :param path: 待判断路径
        :param dirs: 目录列表，默认取当前生效目录
        :return: 是否在任一目录下
        """
        targets = self._active_dirs or dirs or self._target_dirs
        return any(self._path_under(path, d) for d in targets)

    @staticmethod
    def _parse_dirs(raw: Any) -> List[str]:
        """
        解析多行目录配置为规范化路径列表。

        处理内容：跳过空行与 ``#`` 注释行、normpath 规范化、去重、
        剔除嵌套目录（父目录已覆盖子目录时丢弃子目录，避免重复遍历与
        空目录清理时的归属歧义）。

        :param raw: 多行文本（每行一个路径），也兼容单个路径字符串
        :return: 规范化后的目录列表
        """
        seen, dirs = set(), []
        for line in str(raw or "").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            path = os.path.normpath(line)
            if path not in seen:
                seen.add(path)
                dirs.append(path)
        # 剔除嵌套：/a 与 /a/b 同时存在时丢弃 /a/b
        return [
            d for d in dirs
            if not any(d != other and d.startswith(other + os.sep) for other in dirs)
        ]

    @staticmethod
    def _match_pattern(fname: str, pattern: str) -> bool:
        """
        使用 fnmatch 匹配文件名模式。

        :param fname: 文件名
        :param pattern: 通配符模式
        :return: 是否匹配
        """
        import fnmatch
        return fnmatch.fnmatch(fname, pattern)

    def _default_config(self) -> Dict[str, Any]:
        """返回默认配置。"""
        return {
            "enabled": False,
            "mode": "seed",
            "target_dirs": "/volume1/video/下载",
            # 兼容字段：旧版单目录配置，升级后由 init_plugin 自动迁移至 target_dirs
            "target_dir": "",
            "volume_path": "/volume1",
            "threshold_gb": 500,
            "recent_skip_days": 1,
            "cron": "0 */6 * * *",
            "sync_wait_seconds": 90,
            "protect_pattern": "*.part|*.!qb|*.download|*.aria2|*.tmp|*.crdownload",
            "dry_run": False,
            "notify": True,
            "delete_torrents": False,
            "delete_history": False,
            "companion_cleanup": True,
            "orphan_cleanup": False,
            "orphan_seed_scope": False,
            "downloaders": [],
            "manual_action": "",
        }
