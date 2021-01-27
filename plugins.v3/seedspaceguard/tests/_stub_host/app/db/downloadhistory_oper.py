# -*- coding: utf-8 -*-
"""
``app.db.downloadhistory_oper`` 兼容转发：V3 中该模块迁至
``app.db.oper.downloadhistory``。

转发**同一个** ``DownloadHistoryOper`` 类（类级存储随之共享），
使既有测试的预置与断言继续生效。
"""

from app.db.oper.downloadhistory import DownloadHistoryOper, _FileRecord

__all__ = ["DownloadHistoryOper", "_FileRecord"]
