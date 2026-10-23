# -*- coding: utf-8 -*-
"""``app.runtime.log`` 替身：把日志写进列表，便于断言。"""

from typing import List


class _Logger:
    """最小日志器替身。"""

    def __init__(self) -> None:
        """初始化日志缓冲。"""
        self.records: List[str] = []

    def info(self, message: str, *args, **kwargs) -> None:
        """记录 INFO 日志。"""
        del args, kwargs
        self.records.append(str(message))

    def warning(self, message: str, *args, **kwargs) -> None:
        """记录 WARNING 日志。"""
        del args, kwargs
        self.records.append(str(message))

    def error(self, message: str, *args, **kwargs) -> None:
        """记录 ERROR 日志。"""
        del args, kwargs
        self.records.append(str(message))

    def debug(self, message: str, *args, **kwargs) -> None:
        """记录 DEBUG 日志。"""
        del args, kwargs
        self.records.append(str(message))


logger = _Logger()
