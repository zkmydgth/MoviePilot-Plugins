from itertools import batched
from pathlib import Path
from threading import Lock
from time import time as time_unix
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from app.db.transferhistory_oper import TransferHistoryOper
from app.log import logger

from share_strm_scan import Pair, ShareStrmScanCache

from ....core.config import configer
from ....helper.mediasyncdel import MediaSyncDelHelper
from ....utils.path import PathRemoveUtils
from ....utils.sharded_list import ShardedPluginListStore
from ....utils.sentry import sentry_manager

from .oof import ShareOOPServerHelper


class ShareStrmCleaner:
    """
    分享 STRM 清理器
    """

    _SHARE_VALIDATE_SNAP_BATCH = 2000
    _PENDING_KEY = "pending_share_strm_cleanup_batches"
    _LAST_SUMMARY_KEY = "share_strm_cleanup_last_summary"
    _MISSING_IDX = "share_strm_missing_media__idx"
    _MISSING_SHARD_PREFIX = "share_strm_missing_media__s"

    def __init__(self) -> None:
        self.scaner = ShareStrmScanCache()
        self._run_lock = Lock()
        self._missing_store = ShardedPluginListStore(
            self._MISSING_IDX,
            self._MISSING_SHARD_PREFIX,
            max_per_shard=200,
        )

    def __del__(self) -> None:
        self.scaner.invalidate()

    def scan_invalid_shares(self, path: Path) -> Tuple[bool, Dict[Pair, List[str]]]:
        """
        扫描目录，校验分享有效性并返回失效 Pair 对应的 STRM 路径映射

        :param path: 本地扫描根目录
        :return: 成功时为 ``(True, { (share_code, receive_code): [strm_paths...] })``，失败为 ``(False, {})``
        """
        try:
            client = ShareOOPServerHelper.get_client()
            valid_total = 0
            invalid_pairs: List[Tuple[str, str]] = []
            for batch in batched(
                self.scaner.scan(path), self._SHARE_VALIDATE_SNAP_BATCH
            ):
                chunk = [
                    [share_code, receive_code] for share_code, receive_code in batch
                ]
                resp = client.share_validate_snap(chunk)
                valid_total += resp.valid_count
                for i in resp.invalid:
                    logger.warn(
                        f"【分享STRM清理】无效分享: {i.share_code} {i.receive_code} {i.error}"
                    )
                    invalid_pairs.append((i.share_code, i.receive_code))
            logger.info(
                f"【分享STRM清理】验证分享有效性成功，有效分享数量: {valid_total}，无效分享数量: {len(invalid_pairs)}"
            )
        except Exception as e:
            logger.error(
                f"【分享STRM清理】扫描目录或验证分享有效性失败: {e}",
                exc_info=True,
            )
            return False, {}
        try:
            invalid_paths = self.scaner.paths_for_many(path, invalid_pairs)
        except Exception as e:
            logger.error(f"【分享STRM清理】获取无效分享路径失败: {e}")
            return False, {}
        return True, invalid_paths

    @staticmethod
    def _normalize_cleanup_roots(paths: List[str]) -> List[str]:
        """
        将配置中的路径转为绝对路径、去重并跳过非目录

        :param paths: 原始路径字符串列表
        :return: 规范化后的绝对路径字符串列表（顺序保留首次出现）
        """
        seen: set[str] = set()
        out: List[str] = []
        for raw in paths or []:
            s = (raw or "").strip()
            if not s:
                continue
            try:
                p = Path(s).expanduser().resolve()
            except Exception:
                continue
            if not p.is_dir():
                logger.warning(f"【分享STRM清理】跳过不存在的目录: {s}")
                continue
            key = str(p)
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
        return out

    def _transfer_to_missing_row(
        self,
        th: Any,
        strm_path: str,
        share_code: str,
        receive_code: str,
    ) -> Dict[str, Any]:
        """
        组装写入分片存储的「缺失媒体」字典（含固定字段与整理记录子集）

        :param th: ``TransferHistory`` 模型实例
        :param strm_path: STRM 路径
        :param share_code: 分享码
        :param receive_code: 接收码（提取码）
        :return: 含 ``uid``、``reason``、``detected_at`` 及 ``id``/``title`` 等 API 字段的字典
        """
        uid = str(uuid4())
        base: Dict[str, Any] = {
            "uid": uid,
            "strm_path": strm_path,
            "share_code": share_code,
            "receive_code": receive_code,
            "detected_at": time_unix(),
            "reason": "invalid_share",
            "id": getattr(th, "id", None),
            "type": getattr(th, "type", None),
            "title": getattr(th, "title", None),
            "year": getattr(th, "year", None),
            "tmdbid": getattr(th, "tmdbid", None),
            "tvdbid": getattr(th, "tvdbid", None),
            "imdbid": getattr(th, "imdbid", None),
            "doubanid": getattr(th, "doubanid", None),
            "seasons": getattr(th, "seasons", None),
            "episodes": getattr(th, "episodes", None),
            "image": getattr(th, "image", None),
        }
        return base

    def _execute_paths_physical(
        self,
        paths: List[str],
        remove_related_mediainfo: bool,
        remove_empty_parent_dirs: bool,
        remove_stale_transfer_history: bool = False,
    ) -> Tuple[int, Optional[str]]:
        """
        物理删除 STRM 并按配置清理关联媒体文件、空父目录与 MP 整理记录

        :param paths: 待删除 STRM 绝对路径列表
        :param remove_related_mediainfo: 是否调用 ``clean_related_files``
        :param remove_empty_parent_dirs: 是否 ``remove_parent_dir``（strm 模式）
        :param remove_stale_transfer_history: 是否按路径清理 MP 整理记录
        :return: ``(成功删除条数, 最后一则错误信息；全部成功为 None)``
        """
        ok = 0
        last_err: Optional[str] = None
        sync_del_helper = (
            MediaSyncDelHelper() if remove_stale_transfer_history else None
        )
        for remove_path in paths:
            try:
                logger.info(f"【分享STRM清理】删除无效 STRM: {remove_path}")
                Path(remove_path).unlink(missing_ok=True)
                if remove_related_mediainfo:
                    PathRemoveUtils.clean_related_files(
                        file_path=Path(remove_path),
                        func_type="【分享STRM清理】",
                    )
                if remove_empty_parent_dirs:
                    PathRemoveUtils.remove_parent_dir(
                        file_path=Path(remove_path),
                        mode=["strm"],
                        func_type="【分享STRM清理】",
                    )
                ok += 1
            except Exception as e:
                sentry_manager.sentry_hub.capture_exception(e)
                last_err = str(e)
                logger.error(
                    f"【分享STRM清理】删除失败: {remove_path} {e}",
                    exc_info=True,
                )
            if sync_del_helper is not None:
                try:
                    sync_del_helper.remove_by_path(remove_path, del_source=False)
                except Exception as e:
                    logger.error(
                        f"【分享STRM清理】整理记录删除失败: {remove_path} {e}",
                        exc_info=True,
                    )
        return ok, last_err

    def _save_last_summary(self, summary: Dict[str, Any]) -> None:
        """
        将最近一次扫描摘要写入 ``plugin_data``

        :param summary: 摘要字典，供仪表盘等读取
        """
        configer.save_plugin_data(self._LAST_SUMMARY_KEY, summary)

    def run_full_cleanup(self) -> Dict[str, Any]:
        """
        执行完整清理流程：多根扫描、可选缺失媒体写入、立即删除或入队待确认

        结束时释放扫描缓存与运行锁；若已有实例在跑则返回 ``message=already_running``

        :return: 摘要字典，常见键含 ``ok``、``roots_scanned``、``invalid_strm_count``、
            ``deleted_count``、``queued_batch``、``request_id``、``delete_mode``、``message``
        """
        cfg = configer.share_strm_cleanup_config
        roots = self._normalize_cleanup_roots(list(cfg.cleanup_paths or []))
        record_missing = bool(cfg.record_missing_media_from_history)
        summary: Dict[str, Any] = {
            "ok": True,
            "roots_scanned": 0,
            "invalid_strm_count": 0,
            "deleted_count": 0,
            "missing_recorded": 0,
            "missing_skipped_no_history": 0,
            "queued_batch": False,
            "request_id": None,
            "delete_mode": cfg.delete_mode,
            "message": "",
        }
        if not self._run_lock.acquire(blocking=False):
            summary["ok"] = False
            summary["message"] = "already_running"
            return summary
        try:
            if not roots:
                logger.info("【分享STRM清理】cleanup_paths 为空或无效，跳过")
                summary["message"] = "no_cleanup_paths"
                self._save_last_summary(summary)
                return summary

            paths_only: List[str] = []
            missing_rows: List[Dict[str, Any]] = []
            oper = TransferHistoryOper() if record_missing else None
            skipped_no_history = 0

            for root in roots:
                ok, inv = self.scan_invalid_shares(Path(root))
                summary["roots_scanned"] += 1
                if not (ok and inv):
                    continue
                for (sc, rc), pths in inv.items():
                    for p in pths:
                        paths_only.append(p)
                        if oper is None:
                            continue
                        th = oper.get_by_dest(p)
                        if th is None:
                            skipped_no_history += 1
                            continue
                        missing_rows.append(
                            self._transfer_to_missing_row(th, p, sc, rc)
                        )
                inv = None  # type: ignore[assignment]

            summary["invalid_strm_count"] = len(paths_only)

            if missing_rows:
                self._missing_store.extend(missing_rows)
                summary["missing_recorded"] = len(missing_rows)
            if record_missing:
                summary["missing_skipped_no_history"] = skipped_no_history
            missing_rows = []  # free

            if cfg.delete_mode == "immediate":
                deleted, last_err = self._execute_paths_physical(
                    paths_only,
                    cfg.remove_related_mediainfo,
                    cfg.remove_empty_parent_dirs,
                    cfg.remove_stale_transfer_history,
                )
                summary["deleted_count"] = deleted
                if last_err:
                    summary["message"] = last_err
            elif paths_only:
                rid = uuid4().hex[:16]
                self._append_pending_batch(
                    rid,
                    paths_only,
                    cfg.remove_related_mediainfo,
                    cfg.remove_empty_parent_dirs,
                    cfg.remove_stale_transfer_history,
                )
                summary["queued_batch"] = True
                summary["request_id"] = rid

            self._save_last_summary(summary)
            return summary
        finally:
            self.scaner.invalidate()
            self._run_lock.release()

    def _load_pending_store(self) -> Dict[str, Any]:
        """
        读取待确认删除批次的 ``plugin_data`` 结构

        :return: 至少含 ``batches`` 列表的字典
        """
        raw = configer.get_plugin_data(self._PENDING_KEY)
        if not raw or not isinstance(raw, dict):
            return {"batches": []}
        batches = raw.get("batches")
        if not isinstance(batches, list):
            raw["batches"] = []
        return raw

    def _save_pending_store(self, data: Dict[str, Any]) -> None:
        """
        持久化待确认批次存储

        :param data: 含 ``batches`` 的完整存储对象
        """
        configer.save_plugin_data(self._PENDING_KEY, data)

    def _append_pending_batch(
        self,
        request_id: str,
        paths: List[str],
        remove_related_mediainfo: bool,
        remove_empty_parent_dirs: bool,
        remove_stale_transfer_history: bool,
    ) -> None:
        """
        追加一批待用户确认的删除任务

        :param request_id: 批次唯一标识
        :param paths: 待删 STRM 路径列表
        :param remove_related_mediainfo: 确认执行时是否清理关联媒体信息文件
        :param remove_empty_parent_dirs: 确认执行时是否清理无效 STRM 目录
        :param remove_stale_transfer_history: 确认执行时是否删除 MP 整理记录
        """
        store = self._load_pending_store()
        store["batches"].append(
            {
                "request_id": request_id,
                "created_at": time_unix(),
                "paths": paths,
                "remove_related_mediainfo": bool(remove_related_mediainfo),
                "remove_empty_parent_dirs": bool(remove_empty_parent_dirs),
                "remove_stale_transfer_history": bool(remove_stale_transfer_history),
            }
        )
        self._save_pending_store(store)

    def _pop_batch_by_id(
        self, store: Dict[str, Any], request_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        在 ``store['batches']`` 中按 ``request_id`` 原地弹出匹配批次

        :param store: ``_load_pending_store`` 返回的存储对象
        :param request_id: 批次 ID
        :return: 命中则返回被弹出的批次字典，否则 ``None``
        """
        batches: List[Dict[str, Any]] = store["batches"]
        for i, b in enumerate(batches):
            if isinstance(b, dict) and b.get("request_id") == request_id:
                return batches.pop(i)
        return None

    def list_pending_summaries(self) -> List[Dict[str, Any]]:
        """
        返回当前所有待确认批次的轻量摘要（不含 ``paths``，避免数万条路径拷贝）

        :return: 每项含 ``request_id``、``created_at``、``path_count`` 及标志位
        """
        out: List[Dict[str, Any]] = []
        for b in self._load_pending_store()["batches"]:
            if not isinstance(b, dict):
                continue
            paths = b.get("paths")
            out.append(
                {
                    "request_id": b.get("request_id"),
                    "created_at": b.get("created_at"),
                    "path_count": len(paths) if isinstance(paths, list) else 0,
                    "remove_related_mediainfo": bool(b.get("remove_related_mediainfo")),
                    "remove_empty_parent_dirs": bool(b.get("remove_empty_parent_dirs")),
                    "remove_stale_transfer_history": bool(
                        b.get("remove_stale_transfer_history")
                    ),
                }
            )
        return out

    def pending_batch_paths_page(
        self, request_id: str, page: int, limit: int
    ) -> Tuple[bool, List[str], int]:
        """
        分页返回某待确认批次内的 STRM 路径（服务端切片，避免一次返回数万条）

        :param request_id: 批次 ID
        :param page: 页码，从 1 开始
        :param limit: 每页条数，上限 500
        :return: ``(是否找到批次, 当前页路径字符串列表, 路径总条数)``
        """
        rid = (request_id or "").strip()
        if not rid:
            return False, [], 0
        for b in self._load_pending_store()["batches"]:
            if not isinstance(b, dict) or b.get("request_id") != rid:
                continue
            paths = b.get("paths") or []
            if not isinstance(paths, list):
                return True, [], 0
            total = len(paths)
            lim = min(max(1, limit), 500)
            offset = (max(1, page) - 1) * lim
            if offset >= total:
                return True, [], total
            return True, paths[offset : offset + lim], total
        return False, [], 0

    def cancel_pending_batch(self, request_id: str) -> bool:
        """
        从队列移除指定批次，不删除磁盘文件

        :param request_id: 批次 ID
        :return: 是否找到并移除
        """
        store = self._load_pending_store()
        if self._pop_batch_by_id(store, request_id) is None:
            return False
        self._save_pending_store(store)
        return True

    def claim_pending_batch(
        self, request_id: str
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        从待确认队列中原子取出批次并持久化，供后续在后台执行删除

        :param request_id: 批次 ID
        :return: ``(批次字典, None)`` 表示已取出；``(None, 错误码)`` 为 ``batch_not_found`` 或 ``invalid_batch``
        """
        store = self._load_pending_store()
        batch = self._pop_batch_by_id(store, request_id)
        if batch is None:
            return None, "batch_not_found"
        self._save_pending_store(store)
        paths = batch.get("paths")
        if not isinstance(paths, list) or len(paths) == 0:
            return None, "invalid_batch"
        return batch, None

    def execute_claimed_batch(self, batch: Dict[str, Any]) -> Tuple[int, Optional[str]]:
        """
        对已脱离队列的批次执行物理删除及可选整理记录清理

        :param batch: ``claim_pending_batch`` 返回的字典
        :return: ``(删除成功条数, 最后一则物理删除错误；全部成功为 None)``
        """
        paths = batch.get("paths")
        if not isinstance(paths, list) or len(paths) == 0:
            return 0, "invalid_batch"
        return self._execute_paths_physical(
            paths,
            bool(batch.get("remove_related_mediainfo")),
            bool(batch.get("remove_empty_parent_dirs")),
            bool(batch.get("remove_stale_transfer_history")),
        )

    def execute_pending_batch(self, request_id: str) -> Tuple[int, Optional[str]]:
        """
        从队列取出批次并同步执行物理删除（claim + execute_claimed_batch）

        :param request_id: 批次 ID
        :return: ``(删除成功条数, 错误码或错误信息)``
        """
        batch, cerr = self.claim_pending_batch(request_id)
        if cerr:
            return 0, cerr
        assert batch is not None
        return self.execute_claimed_batch(batch)

    def missing_media_page(
        self, page: int, limit: int
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        分页读取缺失媒体分片列表（仅加载当前页涉及分片）

        :param page: 页码，从 1 开始
        :param limit: 每页条数
        :return: ``(当前页条目列表, 总条数)``
        """
        return self._missing_store.page(page, limit)

    def missing_media_clear(self, uid: Optional[str], clear_all: bool) -> bool:
        """
        清空全部分片或按 ``uid`` 删除单条

        :param uid: 记录 ``uid``，与 ``clear_all`` 互斥时生效
        :param clear_all: 为真时删除索引及全部分片
        :return: 清空全量恒为 ``True``；按 ``uid`` 删除时是否找到并删除
        """
        if clear_all:
            self._missing_store.clear_all()
            return True
        if uid:
            return self._missing_store.delete_by_uid(uid)
        return False

    def get_last_summary(self) -> Optional[Dict[str, Any]]:
        """
        读取最近一次 ``run_full_cleanup`` 写入的摘要

        :return: 摘要字典，不存在或格式不对则为 ``None``
        """
        raw = configer.get_plugin_data(self._LAST_SUMMARY_KEY)
        if isinstance(raw, dict):
            return raw
        return None


share_strm_cleaner = ShareStrmCleaner()
