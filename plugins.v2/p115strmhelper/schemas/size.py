from pydantic import BaseModel


class CompareMinSize(BaseModel):
    """
    文件大小最小值比较模型
    """

    min_size: int | None
    file_size: int | None
