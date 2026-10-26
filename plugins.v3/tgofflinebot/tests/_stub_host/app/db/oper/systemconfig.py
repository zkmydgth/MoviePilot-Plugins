# -*- coding: utf-8 -*-
"""
``SystemConfigOper`` 替身：用内存字典保存配置。
"""

from typing import Any, Dict


class SystemConfigOper:
    """系统配置操作替身。"""

    _store: Dict[str, Any] = {}

    def get(self, key: Any) -> Any:
        """
        读取配置值。

        :param key: 配置键
        :return: 配置值
        """
        return self._store.get(str(key))

    def set(self, key: Any, value: Any) -> None:
        """
        写入配置值。

        :param key: 配置键
        :param value: 配置值
        """
        self._store[str(key)] = value
