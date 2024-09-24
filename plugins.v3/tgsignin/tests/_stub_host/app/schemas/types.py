"""宿主桩：``app.schemas.types`` 中的枚举。"""

from enum import Enum


class EventType(Enum):
    """事件类型桩。"""

    PluginAction = "PluginAction"


class MessageType(Enum):
    """消息类型桩。"""

    Plugin = "Plugin"


class NotificationChannel(Enum):
    """通知渠道桩。"""

    Telegram = "Telegram"
