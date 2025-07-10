# -*- coding: utf-8 -*-
"""
``app.db.downloadhistory_oper`` 替身：下载历史操作器。

真实实现基于 MoviePilot 的 SQLite 模型（``DownloadHistory`` / ``DownloadFiles``）。
替身用内存字典复刻插件真正依赖的三个方法：

- ``get_hash_by_fullpath(fullpath)`` —— 按文件路径反查下载 hash
- ``get_files_by_hash(hash, state)`` —— 列出某 hash 下的文件记录
- ``delete_file_by_fullpath(fullpath)`` —— 删除文件记录

测试通过 ``fake_downloadhis`` 夹具直接构造这些映射，从而在**不触碰真实数据库**
的前提下验证「所有文件都删完才删种子」的判定逻辑。

注意：替身**不会**在文件被 unlink 后自动更新记录，这正是真实 MoviePilot 的行为，
也是插件必须用 ``os.path.exists`` 做物理复核、而不能信任 ``state`` 字段的原因。
"""

from typing import Any, Dict, List, Optional


class _FileRecord:
    """``DownloadFiles`` 行替身。"""

    def __init__(self, fullpath: str, download_hash: str, state: int = 1) -> None:
        self.fullpath = fullpath
        self.download_hash = download_hash
        # 0-已删除 1-正常（与 MoviePilot 保持一致）
        self.state = state

    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        return f"<_FileRecord {self.fullpath!r} hash={self.download_hash!r}>"


class DownloadHistoryOper:
    """下载历史操作器替身。"""

    #: 类级共享存储，便于测试在实例化前预置数据
    path_to_hash: Dict[str, str] = {}
    hash_to_files: Dict[str, List[_FileRecord]] = {}
    #: 记录 ``delete_file_by_fullpath`` 的调用，供断言
    deleted_records: List[str] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    @classmethod
    def reset(cls) -> None:
        """清空所有替身数据，供 ``setUp`` 调用。"""
        cls.path_to_hash = {}
        cls.hash_to_files = {}
        cls.deleted_records = []

    # ------------------------------------------------------------------
    # 被插件使用的方法
    # ------------------------------------------------------------------
    def get_hash_by_fullpath(self, fullpath: str) -> str:
        """按文件全路径反查下载 hash；查不到返回空串。"""
        return self.path_to_hash.get(fullpath, "")

    def get_files_by_hash(self, download_hash: str,
                          state: Optional[int] = None) -> List[_FileRecord]:
        """按 hash 查询文件记录，可按 state 过滤。"""
        records = self.hash_to_files.get(download_hash, [])
        if state is None:
            return list(records)
        return [r for r in records if r.state == state]

    def delete_file_by_fullpath(self, fullpath: str) -> None:
        """删除文件记录。"""
        self.deleted_records.append(fullpath)

    # ------------------------------------------------------------------
    # 测试辅助
    # ------------------------------------------------------------------
    @classmethod
    def add_seed(cls, download_hash: str, paths: List[str],
                 state: int = 1) -> None:
        """登记一个种子及其文件（同时建立路径 → hash 反查索引）。"""
        cls.hash_to_files[download_hash] = [
            _FileRecord(p, download_hash, state) for p in paths
        ]
        for p in paths:
            cls.path_to_hash[p] = download_hash


__all__ = ["DownloadHistoryOper", "_FileRecord"]
