from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.core.context import MediaInfo
from app.core.meta import MetaBase
from app.schemas import FileItem


@dataclass
class TransferTask:
    """
    整理任务
    """

    fileitem: FileItem
    target_path: Path
    mediainfo: MediaInfo
    meta: MetaBase
    transfer_type: str
    overwrite_mode: Optional[str] = None  # 覆盖模式: always, size, latest, never

    need_rename: bool = True
    need_notify: Optional[bool] = None  # 是否需要通知（与 MP 目录 notify 一致）
    need_scrape: bool = False  # 是否与 MP transfer_media 的 need_scrape 一致
    scrape: Optional[bool] = None  # MP TransferTask.scrape 原始值（可选）
    manual: Optional[bool] = None  # 是否手动整理
    background: Optional[bool] = None  # 是否后台运行
    username: Optional[str] = None  # 用户名
    downloader: Optional[str] = None  # 下载器
    download_hash: Optional[str] = None  # 下载记录hash

    @property
    def target_dir(self) -> Path:
        """
        获取目标目录
        """
        return self.target_path.parent

    @property
    def target_name(self) -> str:
        """
        获取目标文件名
        """
        return self.target_path.name
