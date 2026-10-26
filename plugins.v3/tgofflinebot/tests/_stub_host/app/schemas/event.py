# -*- coding: utf-8 -*-
"""
``app.schemas.event`` 替身：只提供整理拦截事件的数据模型。
"""

from typing import Any, Optional


class TransferInterceptEventData:
    """整理拦截事件数据替身。"""

    def __init__(
        self,
        fileitem: Any = None,
        meta: Any = None,
        mediainfo: Any = None,
        target_storage: str = "",
        target_path: Any = None,
        transfer_type: str = "move",
        options: Optional[dict] = None,
    ) -> None:
        """保存输入字段与输出字段的默认值。"""
        self.fileitem = fileitem
        self.meta = meta
        self.mediainfo = mediainfo
        self.target_storage = target_storage
        self.target_path = target_path
        self.transfer_type = transfer_type
        self.options = options
        self.cancel = False
        self.source = "未知拦截源"
        self.reason = ""
