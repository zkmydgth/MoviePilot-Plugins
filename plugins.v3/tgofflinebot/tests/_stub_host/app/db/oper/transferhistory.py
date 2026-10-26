# -*- coding: utf-8 -*-
"""
``TransferHistoryOper`` 替身：按 ID 返回预置历史记录。
"""

from typing import Any, Dict, Optional


class TransferHistoryOper:
    """整理历史操作替身。"""

    records: Dict[int, Any] = {}
    by_date: Dict[str, Any] = {}

    def get(self, history_id: int) -> Optional[Any]:
        """
        按 ID 读取历史记录。

        :param history_id: 历史 ID
        :return: 记录对象或 None
        """
        return self.records.get(int(history_id))

    def list_by_date(self, date: str) -> Any:
        """
        按日期返回预置历史记录。

        :param date: 日期字符串
        :return: 记录列表
        """
        return self.by_date.get(date, [])
