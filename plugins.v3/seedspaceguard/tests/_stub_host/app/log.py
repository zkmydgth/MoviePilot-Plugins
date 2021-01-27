# -*- coding: utf-8 -*-
"""
``app.log`` 兼容转发：V3 中该模块已迁移至 ``app.runtime.log``。

这里转发**同一个** logger 与 RECORDS 对象，保证既有测试对
``app.log.RECORDS`` / ``get_messages`` 的断言继续生效。
"""

from app.runtime.log import RECORDS, get_messages, logger, reset_records

__all__ = ["logger", "RECORDS", "reset_records", "get_messages"]
