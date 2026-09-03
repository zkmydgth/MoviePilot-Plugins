#!/usr/bin/env python3
"""
保种空间守护（SeedSpaceGuard）MoviePilot 插件。

存储空间不足时，自动清理 PT 保种目录中「保种最久」的资源，避免触发 H&R。

- 种子级模式：直接调用 qBittorrent/Transmission 下载器，按种子真实添加时间
  从早到晚删除种子（连带删除文件），一步到位不留红种；
- 仅文件模式：按文件修改时间从旧到新删除文件，种子清理由已安装的
  「源文件联动清理」+「下载器助手」插件联动完成。
"""

import os
import re
import shutil
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from apscheduler.triggers.cron import CronTrigger
from fastapi import Request

from app.core.event import Event, eventmanager
from app.core.module import ModuleManager
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import DownloaderType, EventType, NotificationType


class SeedSpaceGuard(_PluginBase):
    """
    保种空间守护插件。
    """

    # 插件元数据
    plugin_name = "保种空间守护"
    plugin_desc = ("存储空间不足时自动清理保种目录中「保种最久」的资源（种子+文件），"
                   "避免 H&R。支持种子级删除与仅文件两种模式，可限定目标下载器。")
    plugin_version = "1.0.5"
    plugin_author = "zkmydgth"
    plugin_config_prefix = "seedspaceguard_"
    plugin_order = 100
    auth_level = 1

    # 运行状态
    _enabled: bool = False
    _target_dir: str = ""
    _volume_path: str = "/volume1"
    _threshold_gb: int = 500
    _cron: str = "0 */6 * * *"
    _recent_skip_days: int = 1
    _mode: str = "seed"
    _protect_pattern: str = "*.part|*.!qb|*.download|*.aria2|*.tmp|*.crdownload"
    _dry_run: bool = False
    _notify: bool = True
    _downloaders: List[str] = []
    _running: bool = False
    _last_result: str = ""
    _lock: threading.Lock = threading.Lock()

    def init_plugin(self, config: dict = None) -> None:
        """根据插件配置初始化运行状态。"""
        self.stop_service()
        self._enabled = False
        self._target_dir = ""
        self._volume_path = "/volume1"
        self._threshold_gb = 500
        self._cron = "0 */6 * * *"
        self._recent_skip_days = 1
        self._mode = "seed"
        self._protect_pattern = "*.part|*.!qb|*.download|*.aria2|*.tmp|*.crdownload"
        self._dry_run = False
        self._notify = True
        self._downloaders = []
        if not config:
            return
        self._enabled = bool(config.get("enabled"))
        self._target_dir = str(config.get("target_dir") or "").strip()
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
        # 目标下载器：逗号/空格分隔的下载器名，留空=全部已启用下载器
        self._downloaders = [
            item.strip()
            for item in re.split(r"[,，;；\s]+", str(config.get("downloaders") or ""))
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
        if not self._target_dir:
            logger.error("【保种空间守护】未配置清理目录，插件不生效")
        else:
            logger.info("【保种空间守护】插件已启用，监控目录：%s", self._target_dir)

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

    def get_form(self) -> Tuple[Optional[List[dict]], Dict[str, Any]]:
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
                                            "text": "使用说明：卷剩余空间低于阈值时，按「保种最久」优先自动清理下载目录中的资源，"
                                                    "直到空间恢复到阈值以上。种子级=删除下载器中最旧的已完成种子（连带文件，"
                                                    "可用下方「目标下载器」限定范围，留空=全部）；"
                                                    "仅文件=只删文件（可配合源文件联动插件）。建议先试运行预览将删内容，确认后再正式启用；"
                                                    "清理目录本身不会被删除。",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "component": "VSelect",
                        "props": {
                            "model": "manual_action",
                            "label": "手动触发一次",
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
                            "hint": "启用后按下方定时规则检查空间，不足时自动清理保种最久的资源",
                        },
                    },
                    {
                        "component": "VSelect",
                        "props": {
                            "model": "mode",
                            "label": "清理模式",
                            "hint": "种子级：直接删除下载器中最老的已完成种子（连带文件），一步到位不留红种；"
                                   "仅文件：删除文件，种子由「源文件联动清理」插件联动处理",
                            "items": [
                                {"title": "种子级（推荐）", "value": "seed"},
                                {"title": "仅文件", "value": "file"},
                            ],
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "downloaders",
                            "label": "目标下载器（种子级模式）",
                            "placeholder": "留空=全部",
                            "hint": "多个用逗号分隔（如 qbit,tr）；留空表示处理 MoviePilot 中所有已启用下载器",
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "target_dir",
                            "label": "保种/清理目录",
                            "placeholder": "/volume1/video/下载",
                            "hint": "只清理该目录下、由本插件判定为保种最久的资源",
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "volume_path",
                            "label": "空间检查路径",
                            "placeholder": "/volume1",
                            "hint": "df 对应的卷路径，插件读取其剩余空间",
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "threshold_gb",
                            "label": "剩余空间阈值（GB）",
                            "placeholder": "500",
                            "hint": "剩余空间低于该值才触发清理，清理到恢复至该值为止",
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "recent_skip_days",
                            "label": "保护最近添加天数",
                            "placeholder": "1",
                            "hint": "最近 N 天添加的种子/文件不清理（保种时间短，删了易触发 H&R）",
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "cron",
                            "label": "定时检查规则（cron）",
                            "placeholder": "0 */6 * * *",
                            "hint": "默认每 6 小时检查一次",
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "protect_pattern",
                            "label": "保护文件后缀（仅文件模式）",
                            "placeholder": "*.part|*.!qb|*.download|*.aria2|*.tmp|*.crdownload",
                            "hint": "以 | 分隔的通配符，命中的文件不删除",
                        },
                    },
                    {
                        "component": "VSwitch",
                        "props": {
                            "model": "dry_run",
                            "label": "试运行（只列不删）",
                            "hint": "开启后仅输出将清理的清单，不实际删除，建议首次先试运行",
                        },
                    },
                    {
                        "component": "VSwitch",
                        "props": {
                            "model": "notify",
                            "label": "完成后通知",
                            "hint": "清理完成后发送站内消息通知",
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
        return [
            {
                "component": "VAlert",
                "props": {
                    "type": "info",
                    "text": f"监控目录：{self._target_dir}　|　当前剩余空间：{free_text}"
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
        return {"success": True, "message": result}

    async def api_status(self, request: Request) -> Dict[str, Any]:
        """
        查询插件运行状态（API 入口）。

        :param request: FastAPI 请求对象
        """
        free_gb = self._disk_free_gb()
        return {
            "success": True,
            "enabled": self._enabled,
            "target_dir": self._target_dir,
            "volume_path": self._volume_path,
            "free_gb": free_gb,
            "threshold_gb": self._threshold_gb,
            "mode": self._mode,
            "dry_run": self._dry_run,
            "last_result": self._last_result,
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

            if not self._target_dir:
                return self._finish("未配置清理目录（target_dir）", None)
            if not os.path.isdir(self._target_dir):
                return self._finish(f"目标目录不存在：{self._target_dir}", None)

            free_gb = self._disk_free_gb()
            if free_gb is None:
                return self._finish(f"无法获取 {self._volume_path} 剩余空间", None)

            prefix = f"[{'试运行' if dry_run else '清理'}] "
            if free_gb >= self._threshold_gb:
                msg = (f"空间充足（{free_gb}GB ≥ {self._threshold_gb}GB），无需清理")
                logger.info("【保种空间守护】%s", msg)
                return self._finish(msg, None, notify=False)

            logger.info("【保种空间守护】空间不足（%sGB < %sGB），开始%s处理（%s）",
                        free_gb, self._threshold_gb,
                        "试运行" if dry_run else "清理",
                        "种子级" if mode == "seed" else "仅文件")

            if mode == "seed":
                result = self._clean_by_seed(free_gb, dry_run)
            else:
                result = self._clean_by_file(free_gb, dry_run)
            deleted, released_gb, detail_lines = result

            suffix = ""
            if deleted == -1:
                suffix = "（未找到可清理的已完成种子，可能均处于保护期内或无下载器连接）"
                deleted = 0
            elif dry_run:
                suffix = f"，共列出 {deleted} 个待删资源（预计释放约 {released_gb}GB）"
            elif deleted > 0:
                suffix = f"，共删除 {deleted} 个（释放约 {released_gb}GB）"
            free_gb_now = self._disk_free_gb()
            msg = f"{prefix}空间不足处理完成{suffix}，当前剩余 {free_gb_now}GB"
            return self._finish(msg, detail_lines[:10])
        finally:
            self._running = False
            self._lock.release()

    def _finish(self, msg: str, details: Optional[List[str]],
                notify: Optional[bool] = None) -> str:
        """
        记录结果并按需通知。

        :param msg: 结果摘要
        :param details: 明细行
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
                self.post_message(
                    mtype=NotificationType.Plugin,
                    title="保种空间守护",
                    text=msg + ("\n" + "\n".join(details) if details else ""),
                )
            except Exception as err:
                logger.error("【保种空间守护】发送通知失败：%s", err)
        return msg

    def _disk_free_gb(self) -> Optional[int]:
        """
        获取卷剩余空间（GB）。

        :return: 剩余 GB，失败返回 None
        """
        try:
            usage = shutil.disk_usage(self._volume_path)
            return int(usage.free // (1024 ** 3))
        except Exception as err:
            logger.error("【保种空间守护】获取 %s 剩余空间失败：%s", self._volume_path, err)
            return None

    # ============================ 种子级清理 ============================

    def _clean_by_seed(self, free_gb: int, dry_run: bool) -> Tuple[int, float, List[str]]:
        """
        种子级清理：按种子真实添加时间从早到晚删除已完成种子（连带文件），
        直到剩余空间恢复到阈值以上。

        :param free_gb: 当前剩余空间（GB）
        :param dry_run: 是否试运行
        :return: （处理数，预计/实际释放 GB，明细行）
        """
        candidates = self._collect_seed_candidates()
        if not candidates:
            return -1, 0.0, []

        cutoff = time.time() - self._recent_skip_days * 86400
        candidates = [c for c in candidates if c["added"] < cutoff]
        candidates.sort(key=lambda c: c["added"])

        detail_lines: List[str] = []
        deleted = 0
        released_gb = 0.0
        for cand in candidates:
            # 试运行时空间不会真正释放，用累计候选大小模拟释放量判断是否达标
            cur_free = free_gb + released_gb if dry_run else (self._disk_free_gb() or free_gb)
            if cur_free >= self._threshold_gb:
                break
            added_text = datetime.fromtimestamp(cand["added"]).strftime("%Y-%m-%d")
            if dry_run:
                detail_lines.append(
                    f"[试运行] 将删除种子：{cand['title']}（{cand['downloader']}，"
                    f"添加于 {added_text}，{cand['size_gb']}GB）"
                )
                logger.info("【保种空间守护】%s", detail_lines[-1])
                deleted += 1
                released_gb += float(cand["size_gb"])
                continue
            try:
                ok = cand["module"].remove_torrents(
                    hashs=cand["hash"],
                    delete_file=True,
                    downloader=cand["downloader"],
                )
            except Exception as err:
                logger.error("【保种空间守护】删除种子 %s 失败：%s", cand["title"], err)
                continue
            if ok:
                deleted += 1
                released_gb += float(cand["size_gb"])
                detail_lines.append(
                    f"已删除种子：{cand['title']}（{cand['downloader']}，"
                    f"添加于 {added_text}）"
                )
                logger.info("【保种空间守护】%s", detail_lines[-1])
                # 删除种子连带文件后，清理可能遗留的空目录（保护 target_dir 本身）
                self._prune_empty_dirs(cand["path"])
        return deleted, round(released_gb, 1), detail_lines

    def _collect_seed_candidates(self) -> List[Dict[str, Any]]:
        """
        收集目标目录下所有下载器的已完成种子候选。

        :return: 候选列表，每项含 module/downloader/hash/title/added/size_gb
        """
        target = os.path.normpath(self._target_dir)
        candidates: List[Dict[str, Any]] = []
        manager = ModuleManager()
        for dl_type in (DownloaderType.Qbittorrent, DownloaderType.Transmission):
            try:
                modules = list(manager.get_running_subtype_module(dl_type))
            except Exception as err:
                logger.error("【保种空间守护】获取下载器模块失败：%s", err)
                continue
            for module in modules:
                try:
                    instances = module.get_instances() or {}
                except Exception as err:
                    logger.error("【保种空间守护】读取下载器实例失败：%s", err)
                    continue
                for name, server in instances.items():
                    # 仅处理配置选定的目标下载器（空=全部）
                    if self._downloaders and name not in self._downloaders:
                        continue
                    try:
                        ret = server.get_torrents()
                    except Exception as err:
                        logger.error("【保种空间守护】读取下载器 %s 种子列表失败：%s", name, err)
                        continue
                    items = ret[0] if isinstance(ret, tuple) else ret
                    for item in items or []:
                        cand = self._parse_torrent(dl_type, item)
                        if not cand:
                            continue
                        if cand["path"] and self._path_under(cand["path"], target):
                            cand["module"] = module
                            cand["downloader"] = name
                            candidates.append(cand)
        return candidates

    def _parse_torrent(self, dl_type: DownloaderType,
                       item: Any) -> Optional[Dict[str, Any]]:
        """
        解析单个种子对象为统一候选结构。

        :param dl_type: 下载器类型
        :param item: 下载器返回的种子对象
        :return: 候选字典或 None（未完成/缺关键字段）
        """
        if dl_type == DownloaderType.Qbittorrent:
            # qBittorrent：dict 字段
            progress = float(item.get("progress") or 0)
            if progress < 0.999:
                return None
            path = item.get("content_path") or item.get("save_path") or ""
            # 保种起点优先取完成时间（completion_on），无则退化为添加时间
            added = int(item.get("completion_on") or item.get("added_on") or 0)
            return {
                "path": path,
                "hash": item.get("hash"),
                "title": item.get("name") or item.get("hash") or "",
                "added": added,
                "size_gb": round(int(item.get("size") or 0) / (1024 ** 3), 1),
            }
        # Transmission：Torrent 对象
        if float(getattr(item, "percentDone", 0) or 0) < 0.999:
            return None
        dl_dir = getattr(item, "downloadDir", "") or ""
        name = getattr(item, "name", "") or ""
        path = os.path.join(dl_dir, name) if dl_dir else ""
        # 保种起点优先取完成时间（done_date），无则退化为添加时间（addedDate）
        done = getattr(item, "done_date", None) or getattr(item, "date_done", None)
        added = 0
        if done is not None:
            try:
                added = int(done.timestamp())
            except (AttributeError, OSError, ValueError):
                added = 0
        if not added:
            added = int(getattr(item, "addedDate", 0) or 0)
        return {
            "path": path,
            "hash": getattr(item, "hashString", "") or getattr(item, "id", ""),
            "title": name or str(getattr(item, "id", "")),
            "added": added,
            "size_gb": round(int(getattr(item, "totalSize", 0) or 0) / (1024 ** 3), 1),
        }

    # ============================ 仅文件清理 ============================

    def _clean_by_file(self, free_gb: int, dry_run: bool) -> Tuple[int, List[str]]:
        """
        仅文件清理：按文件修改时间从旧到新删除目标目录内文件。

        :param free_gb: 当前剩余空间（GB）
        :param dry_run: 是否试运行
        :return: （处理数，明细行）
        """
        patterns = [p.strip() for p in re.split(r"[,|，]", self._protect_pattern) if p.strip()]
        recent_secs = self._recent_skip_days * 86400
        now = time.time()
        files: List[Tuple[float, str, int]] = []
        for root, dirs, fnames in os.walk(self._target_dir):
            # 排除 DSM 系统目录（@eaDir/@tmp/@SynoFinder/@Recently-Snapshot 等）与回收站
            # #recycle，避免把 0 字节系统文件（如 SYNOINDEX_MEDIA_INFO）纳入清理候选
            dirs[:] = [d for d in dirs if not d.startswith("@") and d != "#recycle"]
            for fname in fnames:
                if any(self._match_pattern(fname, pat) for pat in patterns):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    st = os.stat(fpath)
                except OSError:
                    continue
                if now - st.st_mtime < recent_secs:
                    continue
                files.append((st.st_mtime, fpath, st.st_size))
        files.sort(key=lambda x: x[0])

        detail_lines: List[str] = []
        deleted = 0
        released_gb = 0.0
        for mtime, fpath, size in files:
            # 试运行时空间不会真正释放，用累计文件大小模拟释放量判断是否达标
            cur_free = free_gb + released_gb if dry_run else (self._disk_free_gb() or free_gb)
            if cur_free >= self._threshold_gb:
                break
            mtime_text = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
            if dry_run:
                detail_lines.append(
                    f"[试运行] 将删除文件：{fpath}（修改于 {mtime_text}，"
                    f"{round(size / (1024 ** 3), 1)}GB）"
                )
                logger.info("【保种空间守护】%s", detail_lines[-1])
                deleted += 1
                released_gb += size / (1024 ** 3)
                continue
            try:
                os.remove(fpath)
            except OSError as err:
                logger.warning("【保种空间守护】删除文件 %s 失败：%s", fpath, err)
                continue
            deleted += 1
            released_gb += size / (1024 ** 3)
            detail_lines.append(f"已删除文件：{fpath}（修改于 {mtime_text}）")
            logger.info("【保种空间守护】%s", detail_lines[-1])
            # 删除文件后清理可能遗留的空目录（保护 target_dir 本身）
            self._prune_empty_dirs(fpath)
        return deleted, round(released_gb, 1), detail_lines

    # ============================ 工具方法 ============================

    def _prune_empty_dirs(self, file_or_dir: str) -> None:
        """
        自底向上清理删除后遗留的空目录，直到清理目录（target_dir）为止。
        - 只删除空目录（os.rmdir 语义，非空目录会抛 OSError 自动停止）
        - 不删除 target_dir 本身
        - 目录已不存在（如下载器删除种子时已顺带清理）则继续向上

        :param file_or_dir: 被删除的文件或目录路径，从它所在层开始向上清理
        """
        target = os.path.normpath(self._target_dir)
        start = file_or_dir if os.path.isdir(file_or_dir) else os.path.dirname(file_or_dir)
        current = os.path.normpath(start or "")
        while current and current != target and current.startswith(target + os.sep):
            try:
                os.rmdir(current)
            except FileNotFoundError:
                # 目录已被其他进程删除，继续向上清理父级
                pass
            except OSError:
                # 目录非空或删除失败（如含 @eaDir 子目录），停止向上清理
                break
            current = os.path.dirname(current)

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
            "target_dir": "/volume1/video/下载",
            "volume_path": "/volume1",
            "threshold_gb": 500,
            "recent_skip_days": 1,
            "cron": "0 */6 * * *",
            "protect_pattern": "*.part|*.!qb|*.download|*.aria2|*.tmp|*.crdownload",
            "dry_run": False,
            "notify": True,
            "downloaders": "",
            "manual_action": "",
        }
