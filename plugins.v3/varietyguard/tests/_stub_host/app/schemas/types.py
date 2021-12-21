# -*- coding: utf-8 -*-
"""``app.schemas.types`` 替身：插件用到的事件/媒体/消息类型枚举。"""

from enum import Enum


class EventType(Enum):
    """事件类型替身。"""

    PluginAction = "plugin.action"
    TransferComplete = "transfer.complete"


class MediaType(Enum):
    """媒体类型替身。"""

    MOVIE = "电影"
    TV = "电视剧"
    MUSIC = "音乐"


class MessageType(Enum):
    """消息类型替身。"""

    Plugin = "插件"
    Other = "其它"
