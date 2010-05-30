from pathlib import Path
from typing import Optional, Callable, Any, Dict

from app import schemas
from app.log import logger
from app.modules.filemanager.storages.u115 import U115Pan

from ..core.u115_open import U115OpenHelper
from ..core.config import configer


class U115Patcher:
    """
    一个用于控制 U115Pan.upload 方法猴子补丁的管理器。
    支持手动启用/禁用，也支持作为上下文管理器使用。
    """

    _original_method: Dict[str, Callable[..., Any]] = {"upload": None}
    _func_active: Dict[str, bool] = {"upload": False}

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
        if configer.get_config("upload_module_enhancement"):
            if cls._func_active["upload"]:
                return

            if cls._original_method["upload"] is None:
                cls._original_method["upload"] = U115Pan.upload

            U115Pan.upload = cls._patch_upload
            cls._func_active["upload"] = True
            logger.info("【P115Open】上传接口补丁应用成功")

    @classmethod
    def disable(cls):
        """
        禁用猴子补丁
        """
        log_map: Dict[str, str] = {"upload": "上传接口恢复原始状态成功"}

        for key, status in cls._func_active.items():
            if status:
                if cls._original_method[key] is not None:
                    U115Pan.upload = cls._original_method[key]

            cls._func_active[key] = False
            logger.info(f"【P115Open】{log_map[key]}")
