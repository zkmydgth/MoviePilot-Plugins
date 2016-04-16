from typing import Optional, List

from pydantic import BaseModel, Field


class StrmApiConfig(BaseModel):
    """
    API 调用生成 STRM 配置
    """

    local_path: Optional[str] = Field(default=None, description="本地路径")
    pan_path: Optional[str] = Field(default=None, description="网盘路径")


class StrmApiData(BaseModel):
    """
    API 调用生成 STRM 数据
    """

    id: Optional[int] = Field(default=None, description="文件ID")
    name: Optional[str] = Field(default=None, description="文件名")
    sha1: Optional[str] = Field(default=None, description="文件SHA1")
    size: Optional[int] = Field(default=None, description="文件大小")
    pick_code: Optional[str] = Field(default=None, description="文件pickcode")
    local_path: Optional[str] = Field(default=None, description="本地路径")
    pan_path: Optional[str] = Field(default=None, description="网盘路径")
    pan_media_path: Optional[str] = Field(default=None, description="网盘媒体库路径")
    media_server_refresh: Optional[bool] = Field(
        default=None, description="是否刷新媒体服务器"
    )
    scrape_metadata: Optional[bool] = Field(default=None, description="是否刮削元数据")


class StrmApiResponseFail(StrmApiData):
    """
    生成失败 STRM 信息
    """

    code: int = Field(description="错误代码")
    reason: Optional[str] = Field(default=None, description="失败原因")


class StrmApiPayloadData(BaseModel):
    """
    API 调用生成 STRM 参数
    """

    data: List[StrmApiData] = Field(
        default_factory=list, description="STRM生成数据列表"
    )


class StrmApiPayloadByPathItem(BaseModel):
    """
    API 调用生成 STRM 路径组
    """

    local_path: Optional[str] = Field(default=None, description="本地路径")
    pan_media_path: str = Field(description="网盘媒体库路径")


class StrmApiPayloadByPathData(BaseModel):
    """
    API 调用生成 STRM 参数（by_path）
    """

    data: List[StrmApiPayloadByPathItem] = Field(
        default_factory=list, description="需要生成STRM的一组文件夹列表"
    )
    media_server_refresh: Optional[bool] = Field(
        default=None, description="是否刷新媒体服务器"
    )
    scrape_metadata: Optional[bool] = Field(default=None, description="是否刮削元数据")


class StrmApiResponseData(BaseModel):
    """
    API 返回生成 STRM 信息
    """

    success: List[StrmApiData] = Field(
        default_factory=list, description="成功生成的STRM列表"
    )
    fail: List[StrmApiResponseFail] = Field(
        default_factory=list, description="生成失败的STRM列表"
    )
    success_count: int = Field(default=0, description="成功数量")
    fail_count: int = Field(default=0, description="失败数量")


class StrmApiStatusCode:
    """
    API STRM 错误
    """

    # 成功
    Success: int = 10200
    # 未传有效参数
    MissPayload: int = 10400
    # 缺失必要参数，pick_code，id 或 pan_path 参数
    MissPcOrId: int = 10422
    # 文件扩展名不属于可整理媒体文件扩展名
    NotRmtMediaExt: int = 10600
    # 无法获取本地生成 STRM 路径
    GetLocalPathError: int = 10601
    # 无法获取网盘媒体库路径
    GetPanMediaPathError: int = 10602
    # STRM 文件生成失败
    CreateStrmError: int = 10911
