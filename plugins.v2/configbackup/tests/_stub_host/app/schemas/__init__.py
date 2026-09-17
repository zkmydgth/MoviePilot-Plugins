# -*- coding: utf-8 -*-
"""宿主桩：app.schemas。"""

from enum import Enum


class NotificationType(Enum):
    """通知类型（只用得到 S）。"""

    S = "SiteMessage"
