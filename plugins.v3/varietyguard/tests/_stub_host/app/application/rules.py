# -*- coding: utf-8 -*-
"""``app.application.rules`` 替身：自定义过滤规则查询桩。"""

from typing import Any, Dict, List


class StubCustomRule:
    """自定义规则替身：支持插件使用的 ``model_dump(exclude_none=True)``。"""

    def __init__(self, payload: Dict[str, Any]) -> None:
        self._payload = dict(payload)

    def model_dump(self, exclude_none: bool = False) -> Dict[str, Any]:
        """按宿主约定输出字典；``exclude_none`` 为真时丢弃 None 字段。"""
        if exclude_none:
            return {key: value for key, value in self._payload.items() if value is not None}
        return dict(self._payload)


# 测试可写：模块级规则清单（每个用例自行重置）。
RULES: List[Dict[str, Any]] = []


class RuleHelper:
    """规则查询替身：从模块级 ``RULES`` 读取。"""

    @staticmethod
    def get_custom_rules() -> List[StubCustomRule]:
        """返回全部自定义过滤规则。"""
        return [StubCustomRule(rule) for rule in RULES]
