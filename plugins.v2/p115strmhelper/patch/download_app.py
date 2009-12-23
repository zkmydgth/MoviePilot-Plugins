from typing import Any, Callable

import p115client.client as _p115_client_mod

from app.log import logger


_TARGET_METHODS = ("download_folders_app", "download_files_app")


class DownloadAppPatcher:
    """
    download_folders_app / download_files_app 补丁
    """

    _originals: dict = {}
    _active: bool = False

    @staticmethod
    def _force_chrome(original: Callable[..., Any]) -> Callable[..., Any]:
        """
        包装原方法，忽略传入的 app 参数，强制使用 chrome
        """

        def wrapper(
            self_instance: Any,
            payload: Any,
            /,
            app: str = "chrome",
            *args: Any,
            **kwargs: Any,
        ) -> Any:
            return original(self_instance, payload, "chrome", *args, **kwargs)

        return wrapper

    @classmethod
    def enable(cls) -> None:
        """
        应用补丁
        """
        if cls._active:
            return

        client_cls = getattr(_p115_client_mod, "P115Client", None)
        if client_cls is None:
            logger.warning(
                "【download_app】未找到 p115client.client.P115Client，跳过补丁"
                "（p115client 版本可能不兼容）"
            )
            return

        missing = [m for m in _TARGET_METHODS if not hasattr(client_cls, m)]
        if missing:
            logger.warning(
                f"【download_app】未找到方法 {missing}，跳过补丁"
                "（p115client 版本可能不兼容）"
            )
            return

        for name in _TARGET_METHODS:
            original = getattr(client_cls, name)
            cls._originals[name] = original
            setattr(client_cls, name, cls._force_chrome(original))

        cls._active = True
        logger.info(
            "【download_app】download_folders_app/download_files_app 补丁应用成功，强制走 chrome"
        )

    @classmethod
    def disable(cls) -> None:
        """
        禁用补丁
        """
        if not cls._active:
            return

        client_cls = getattr(_p115_client_mod, "P115Client", None)
        if client_cls is not None:
            for name, original in cls._originals.items():
                setattr(client_cls, name, original)

        cls._originals = {}
        cls._active = False
        logger.info(
            "【download_app】download_folders_app/download_files_app 补丁恢复原始状态成功"
        )
