# -*- coding: utf-8 -*-
"""
``app.runtime.log`` 替身：把日志写入内存，便于断言不抛异常。
"""

from typing import List


class _Logger:
    """最小日志器。"""

    def __init__(self) -> None:
        self.records: List[str] = []

    def info(self, message: str, *args: object, **kwargs: object) -> None:
        """记录 info 日志。"""
        self.records.append(f"info: {message}")

    def warning(self, message: str, *args: object, **kwargs: object) -> None:
        """记录 warning 日志。"""
        self.records.append(f"warning: {message}")

    def error(self, message: str, *args: object, **kwargs: object) -> None:
        """记录 error 日志。"""
        self.records.append(f"error: {message}")

    def debug(self, message: str, *args: object, **kwargs: object) -> None:
        """记录 debug 日志。"""
        self.records.append(f"debug: {message}")


logger = _Logger()
