from datetime import datetime
from typing import Dict, Optional

from pydantic import BaseModel


class ShareSaveParent(BaseModel):
    """
    分享转存目录信息
    """

    path: str
    id: int | str


class ShareResponseData(BaseModel):
    """
    分享转存返回信息
    """

    media_info: Optional[Dict] = None
    save_parent: ShareSaveParent


class ShareApiData(BaseModel):
    """
    分享转存 API 返回数据
    """

    code: int = 0
    msg: str = "success"
    data: Optional[ShareResponseData] = None
    timestamp: Optional[datetime] = None
