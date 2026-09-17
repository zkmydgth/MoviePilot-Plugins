# -*- coding: utf-8 -*-
"""
``app.db.transferhistory_oper`` 替身：转移历史操作器。

真实实现基于 ``TransferHistory`` 表。替身复刻插件依赖的查询/删除方法：
``get_by_dest`` / ``get_by_src`` / ``delete``。

与真实实现一致：``get_by_dest`` 未命中时返回 ``None``（而非抛异常），
插件据此回退到 ``get_by_src``，替身需保留这一语义才能测出回退路径。
"""

from typing import Any, Dict, List, Optional


class _TransferRecord:
    """``TransferHistory`` 行替身。"""

    def __init__(self, record_id: int, src: str, dest: str) -> None:
        self.id = record_id
        self.src = src
        self.dest = dest

    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        return f"<_TransferRecord id={self.id} {self.src!r} -> {self.dest!r}>"


class TransferHistoryOper:
    """转移历史操作器替身。"""

    #: 类级共享存储：{id: 记录}
    records: Dict[int, _TransferRecord] = {}
    #: 记录 ``delete`` 的调用，供断言
    deleted_ids: List[int] = []
    #: 置真时所有查询抛异常，用于验证插件的异常降级
    raise_on_query: bool = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    @classmethod
    def reset(cls) -> None:
        """清空所有替身数据，供 ``setUp`` 调用。"""
        cls.records = {}
        cls.deleted_ids = []
        cls.raise_on_query = False

    @classmethod
    def add_record(cls, record_id: int, src: str, dest: str) -> None:
        """登记一条转移记录。"""
        cls.records[record_id] = _TransferRecord(record_id, src, dest)

    # ------------------------------------------------------------------
    # 被插件使用的方法
    # ------------------------------------------------------------------
    def get_by_dest(self, dest: str,
                    storage: Optional[str] = None) -> Optional[_TransferRecord]:
        """按目标路径查询转移记录，未命中返回 None。"""
        if self.raise_on_query:
            raise RuntimeError("模拟查询异常")
        for record in self.records.values():
            if record.dest == dest:
                return record
        return None

    def get_by_src(self, src: str,
                   storage: Optional[str] = None) -> Optional[_TransferRecord]:
        """按源路径查询转移记录，未命中返回 None。"""
        if self.raise_on_query:
            raise RuntimeError("模拟查询异常")
        for record in self.records.values():
            if record.src == src:
                return record
        return None

    def delete(self, historyid: int) -> None:
        """按 id 删除转移记录。"""
        self.deleted_ids.append(historyid)
        self.records.pop(historyid, None)


__all__ = ["TransferHistoryOper", "_TransferRecord"]
