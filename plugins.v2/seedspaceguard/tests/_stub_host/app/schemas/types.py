# -*- coding: utf-8 -*-
"""
``app.schemas.types`` 替身：提供插件使用的枚举类型。

保持与 MoviePilot V2 一致的成员名与取值方式，使断言可读。
"""

from enum import Enum
from typing import Any, Dict, List, Optional


class EventType(Enum):
    """事件类型。"""

    PluginAction = "插件动作"
    PluginReload = "插件重载"
    PluginData = "插件数据"
    TransferComplete = "整理完成"


class DownloaderType(Enum):
    """下载器类型。"""

    Qbittorrent = "qbittorrent"
    Transmission = "transmission"


class NotificationType(Enum):
    """通知类型。"""

    Plugin = "插件"
    Download = "下载"
    MediaServer = "媒体服务器"


# V3 中 NotificationType 被重命名为 MessageType，保留别名以便同一份测试
MessageType = NotificationType


class MediaType(Enum):
    """媒体类型。"""

    MOVIE = "电影"
    TV = "电视剧"
    UNKNOWN = "未知"


class ServiceType(Enum):
    """服务类型。"""

    Qbittorrent = "Qbittorrent"
    Transmission = "Transmission"
    Emby = "Emby"


class MediaServerType(Enum):
    """媒体服务器类型。"""

    Emby = "emby"
    Jellyfin = "jellyfin"
    Plex = "plex"


# ----------------------------------------------------------------------
# 轻量数据模型（插件仅做类型标注，不做实际校验）
# ----------------------------------------------------------------------
class ServiceInfo:
    """媒体服务器服务信息。"""

    def __init__(self, name: str = "", type: str = "", instance: Any = None) -> None:
        self.name = name
        self.type = type
        self.instance = instance


class FileItem:
    """文件项。"""

    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


class DownloaderTorrent:
    """种子信息。"""

    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


__all__ = [
    "EventType",
    "DownloaderType",
    "NotificationType",
    "MessageType",
    "MediaType",
    "ServiceType",
    "MediaServerType",
    "ServiceInfo",
    "FileItem",
    "DownloaderTorrent",
]

# 保留类型引用，供类型标注
_ = (Any, Dict, List, Optional)
