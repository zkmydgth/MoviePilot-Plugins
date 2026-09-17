# -*- coding: utf-8 -*-
"""宿主桩：app.log.logger。"""


class _Logger:
    """把日志吞掉，避免测试输出噪声；同时记录调用便于断言。"""

    records = []

    def _log(self, level, msg, *args, **kwargs):
        self.records.append((level, str(msg)))

    def debug(self, msg, *args, **kwargs):
        self._log("debug", msg)

    def info(self, msg, *args, **kwargs):
        self._log("info", msg)

    def warning(self, msg, *args, **kwargs):
        self._log("warning", msg)

    def error(self, msg, *args, **kwargs):
        self._log("error", msg)

    @classmethod
    def reset(cls):
        cls.records = []


logger = _Logger()
