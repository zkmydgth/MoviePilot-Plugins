from dataclasses import dataclass
from typing import Optional


@dataclass
class UploadResult:
    success: bool
    target_name: str
    file_size: int
    elapsed_time: Optional[float] = None
    error_msg: Optional[str] = None
