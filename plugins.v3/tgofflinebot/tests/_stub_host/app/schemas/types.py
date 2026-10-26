# -*- coding: utf-8 -*-
"""
``app.schemas.types`` 替身：只提供插件用到的枚举与常量。
"""


class MediaType:
    """媒体类型替身。"""

    MOVIE = "电影"
    TV = "电视剧"


class ChainEventType:
    """链式事件类型替身。"""

    TransferIntercept = "transfer.intercept"


class EventType:
    """系统事件类型替身。"""

    TransferComplete = "transfer.complete"
    TransferFailed = "transfer.failed"


class SystemConfigKey:
    """系统配置键替身。"""

    CustomIdentifiers = "CustomIdentifiers"
