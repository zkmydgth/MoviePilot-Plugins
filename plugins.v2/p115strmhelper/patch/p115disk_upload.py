from pathlib import Path
from typing import Optional, Any, Callable

from app.log import logger
from app.schemas import FileItem

try:
    from app.plugins.p115disk import P115Disk  # noqa: F401

    P115DISK_AVAILABLE = True
except (ImportError, Exception):
    P115DISK_AVAILABLE = False
    P115Disk = Any


from ..core.config import configer
from ..core.p115disk import P115DiskCore


class P115DiskPatcher:
    """
    P115Disk 上传增强补丁
    """

    _original_upload_file: Optional[Callable[..., Any]] = None
    _patched_class: Optional[Any] = None
    _active: bool = False

    @staticmethod
    def _patch_upload_file(
        self_instance: Any,
        fileitem: FileItem,
        path: Path,
        new_name: Optional[str] = None,
    ) -> Optional[FileItem]:
        """
        使用 P115DiskHelper 上传
        """
        p115_api = getattr(self_instance, "_p115_api", None)
        if not p115_api or not getattr(p115_api, "client", None):
            return None
        helper = P115DiskCore(client=p115_api.client)
        logger.debug("【P115Disk】调用补丁接口上传")
        return helper.upload(target_dir=fileitem, local_path=path, new_name=new_name)

    @classmethod
    def enable(cls) -> None:
        """
        应用补丁
        """
        if not P115DISK_AVAILABLE:
            return
        if not configer.upload_module_enhancement:
            return
        if cls._active:
            return
        cls._original_upload_file = P115Disk.upload_file
        P115Disk.upload_file = cls._patch_upload_file
        cls._patched_class = P115Disk
        cls._active = True
        logger.info("【P115Disk】上传接口补丁应用成功")

    @classmethod
    def disable(cls) -> None:
        """
        禁用补丁
        """
        if (
            not cls._active
            or cls._original_upload_file is None
            or cls._patched_class is None
        ):
            return
        cls._patched_class.upload_file = cls._original_upload_file
        cls._original_upload_file = None
        cls._patched_class = None
        cls._active = False
        logger.info("【P115Disk】上传接口恢复原始状态成功")
