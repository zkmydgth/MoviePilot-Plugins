# -*- coding: utf-8 -*-
"""
``app.db.transferhistory_oper`` 兼容转发：V3 中该模块迁至
``app.db.oper.transferhistory``。

转发**同一个** ``TransferHistoryOper`` 类（类级存储随之共享），
使既有测试的预置与断言继续生效。
"""

from app.db.oper.transferhistory import TransferHistoryOper, _TransferRecord

__all__ = ["TransferHistoryOper", "_TransferRecord"]
