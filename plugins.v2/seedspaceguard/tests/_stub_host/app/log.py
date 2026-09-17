# -*- coding: utf-8 -*-
"""
``app.log`` 替身：插件通过 ``from app.log import logger`` 获取日志器。

记录全部日志调用，供测试断言「是否发出告警」等行为。
"""

import logging
import sys
from typing import List, Tuple

# 供测试读取的日志记录：(level, message, args)
RECORDS: List[Tuple[str, str, tuple]] = []


class _CaptureHandler(logging.Handler):
    """把日志写入模块级 RECORDS，便于测试断言。"""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            RECORDS.append((record.levelname, record.getMessage(), record.args))
        except Exception:  # pragma: no cover - 日志格式化失败不应影响被测代码
            pass


logger = logging.getLogger("stub.seedspaceguard")

if not any(isinstance(h, _CaptureHandler) for h in logger.handlers):
    _handler = _CaptureHandler()
    _handler.setLevel(logging.DEBUG)
    logger.addHandler(_handler)
    logger.setLevel(logging.DEBUG)
    # 避免向 stderr 重复输出
    logger.propagate = False


def reset_records() -> None:
    """清空已捕获的日志记录。"""
    RECORDS.clear()


def get_messages(level: str = "") -> List[str]:
    """
    获取已捕获的日志消息。

    :param level: 过滤级别（如 WARNING），为空返回全部
    :return: 消息列表
    """
    if not level:
        return [msg for _lvl, msg, _args in RECORDS]
    return [msg for lvl, msg, _args in RECORDS if lvl == level]


__all__ = ["logger", "RECORDS", "reset_records", "get_messages"]

# 让 ``sys`` 引用不被 lint 判为未使用（保留以便调试）
_ = sys
