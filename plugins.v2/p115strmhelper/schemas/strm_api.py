from typing import Optional, List

from pydantic import BaseModel


class StrmApiConfig(BaseModel):
    """
    API 调用生成 STRM 配置
    """

    local_path: Optional[str] = None
    pan_path: Optional[str] = None


class StrmApiData(BaseModel):
    """
    API 调用生成 STRM 数据
    """

    id: Optional[int] = None
    name: Optional[str] = None
    sha1: Optional[str] = None
    size: Optional[int] = None
    pick_code: Optional[str] = None
    local_path: Optional[str] = None
    pan_path: Optional[str] = None
    pan_media_path: Optional[str] = None
    media_server_refresh: Optional[bool] = None
    scrape_metadata: Optional[bool] = None


class StrmApiResponseFail(StrmApiData):
    """
    生成失败 STRM 信息
    """

    code: int
    reason: Optional[str] = None


class StrmApiPayloadData(BaseModel):
    """
    API 调用生成 STRM 参数
    """

    data: List[StrmApiData] = []


class StrmApiResponseData(BaseModel):
    """
    API 返回生成 STRM 信息
    """

    success: List[StrmApiData] = []
    fail: List[StrmApiResponseFail] = []
    success_count: int = 0
    fail_count: int = 0


class StrmApiStatusCode:
    """
    API STRM 错误
    """

    # 成功
    Success: int = 10200
    # 未传有效参数
    MissPayload: int = 10400
    # 缺失必要参数，pick_code 或 id
    MissPcOrId: int = 10422
    # 文件扩展名不属于可整理媒体文件扩展名
    NotRmtMediaExt: int = 10600
    # 无法获取本地生成 STRM 路径
    GetLocalPathError: int = 10601
    # 无法获取网盘媒体库路径
    GetPanMediaPathError: int = 10602
    # STRM 文件生成失败
    CreateStrmError: int = 10911
