from pathlib import Path
from typing import Optional

from app import schemas
from app.log import logger
from app.modules.filemanager.storages.u115 import U115Pan

from ..core.u115_open import U115OpenHelper


class U115Patcher:
    """
    一个用于控制 U115Pan.upload 方法猴子补丁的管理器。
    支持手动启用/禁用，也支持作为上下文管理器使用。
    """

    _original_method = None
    _is_active = False

    @staticmethod
    def _patch_upload(
        self_instance: U115Pan,
        target_dir: schemas.FileItem,
        local_path: Path,
        new_name: Optional[str] = None,
    ) -> Optional[schemas.FileItem]:
        """
        自定义 upload
        """
        helper = U115OpenHelper()
        logger.debug("【P115Open】调用补丁接口上传")
        return helper.upload(
            target_dir=target_dir, local_path=local_path, new_name=new_name
        )

    @classmethod
    def enable(cls):
        """
        启用猴子补丁
        """
        if cls._is_active:
            return

        if cls._original_method is None:
            cls._original_method = U115Pan.upload

        U115Pan.upload = cls._patch_upload
        cls._is_active = True
        logger.info("【P115Open】上传接口补丁应用成功")

    @classmethod
    def disable(cls):
        """
        禁用猴子补丁
        """
        if not cls._is_active:
            return

        if cls._original_method is not None:
            U115Pan.upload = cls._original_method

        cls._is_active = False
        logger.info("【P115Open】上传接口恢复原始状态成功")
