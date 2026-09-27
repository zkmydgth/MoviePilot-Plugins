# -*- coding: utf-8 -*-
"""
``app.core.event`` 兼容转发：V3 中事件接口迁移至 ``app.sdk.events``。

转发**同一个** ``eventmanager`` 单例，使既有测试通过旧路径取到的仍是同一对象。
"""

from app.sdk.events import Event, EventManager, eventmanager

__all__ = ["Event", "EventManager", "eventmanager"]
