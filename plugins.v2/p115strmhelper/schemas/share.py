from datetime import datetime
from typing import Dict, Optional

from pydantic import BaseModel, model_validator


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


class ShareStrmConfig(BaseModel):
    """
    分享 STRM 生成配置
    """

    # 分享链接
    share_link: Optional[str] = None
    # 分享码
    share_code: Optional[str] = None
    # 分享密码
    share_receive: Optional[str] = None
    # 分享路径
    share_path: Optional[str] = None
    # 本地路径
    local_path: Optional[str] = None
    # 分享生成最小文件大小
    min_file_size: Optional[int] = None
    # 交由 MoviePilot 整理
    moviepilot_transfer: bool = False
    # 自动下载网盘元数据
    auto_download_mediainfo: bool = False
    # 刷新媒体服务器
    media_server_refresh: bool = False
    # 是否刮削元数据
    scrape_metadata: bool = False

    @model_validator(mode='after')
    def enforce_moviepilot_constraints(self):
        """
        当 moviepilot_transfer 为 True 时，强制关闭其他相关选项
        """
        if self.moviepilot_transfer:
            self.auto_download_mediainfo = False
            self.media_server_refresh = False
            self.scrape_metadata = False
        return self


class ShareCode(BaseModel):
    """
    分享码
    """

    share_code: Optional[str] = None
    receive_code: Optional[str] = None
