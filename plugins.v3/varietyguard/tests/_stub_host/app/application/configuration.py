# -*- coding: utf-8 -*-
"""``app.application.configuration`` 替身：系统配置读写（含写入记账与故障模拟）。"""

from typing import Any, List, Tuple


class SystemConfigWriteResult:
    """写入结果替身：与宿主一致地带 ``changed`` 与 ``normalized_value``。"""

    def __init__(self, changed: Any, normalized_value: Any) -> None:
        self.changed = changed
        self.normalized_value = normalized_value


class SystemConfigServiceStub:
    """系统配置服务替身：把每次异步写入记入 ``writes``，便于断言。"""

    def __init__(self) -> None:
        self.writes: List[Tuple[Any, Any]] = []
        self.fail_on_write = False

    async def async_set_with_normalized_value(
        self, key: Any, value: Any
    ) -> SystemConfigWriteResult:
        """异步写入并记账；``fail_on_write=True`` 时抛错，用于验证 fail-open。"""
        if self.fail_on_write:
            raise RuntimeError("stub system config write failure")
        self.writes.append((key, value))
        return SystemConfigWriteResult(changed=True, normalized_value=value)


SERVICE = SystemConfigServiceStub()


def get_configured_system_config() -> SystemConfigServiceStub:
    """返回全局替身服务，测试可直接读取 ``SERVICE.writes``。"""
    return SERVICE
