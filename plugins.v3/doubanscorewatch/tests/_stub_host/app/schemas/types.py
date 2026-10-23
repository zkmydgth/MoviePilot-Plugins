# -*- coding: utf-8 -*-
"""``app.schemas.types`` 替身：消息场景枚举。"""

from enum import Enum


class MessageType(Enum):
    """通知场景。"""

    Plugin = "插件"
    Movie = "电影"
    Tv = "电视剧"
    Music = "音乐"
    Download = "下载"
    Organize = "整理"
    Site = "站点"
    Other = "其它"
