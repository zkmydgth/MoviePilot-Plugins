from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.core.event import Event, eventmanager
from app.helper.storage import StorageHelper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import FileItem, StorageOperSelectionEventData, StorageUsage
from app.schemas.types import ChainEventType

from .clouddrive_api import CloudDriveApi
from clouddrive2_client import CloudDriveClient
from .version import VERSION


class CloudDriveDisk(_PluginBase):
    """
    CloudDrive2 储存插件
    """

    plugin_name = "CloudDrive2储存"
    plugin_desc = "使存储支持 CloudDrive2，grpc 原生 API 操作。"
    plugin_icon = "Cloudrive_A.png"
    plugin_version = VERSION
    plugin_author = "DDSRem"
    author_url = "https://github.com/DDSRem"
    plugin_config_prefix = "clouddrivedisk_"
    plugin_order = 99
    auth_level = 1

    _enabled = False
    _client: Optional[CloudDriveClient] = None
    _disk_name = "CloudDrive储存"
    _clouddrive_api: Optional[CloudDriveApi] = None
    _host = ""
    _port = "19798"
    _username = ""
    _password = ""

    def __init__(self) -> None:
        super().__init__()

    def init_plugin(self, config: Optional[Dict] = None) -> None:
        """
        初始化插件

        :param config: 插件配置，含 enabled、host、port、username、password。
        """
        if not config:
            return
        storage_helper = StorageHelper()
        storages = storage_helper.get_storagies()
        if not any(
            s.type == self._disk_name and s.name == self._disk_name for s in storages
        ):
            storage_helper.add_storage(
                storage=self._disk_name, name=self._disk_name, conf={}
            )
        self._enabled = config.get("enabled", False)
        self._host = (config.get("host") or "localhost").strip()
        self._port = str(config.get("port") or "19798").strip()
        self._username = (config.get("username") or "").strip()
        self._password = config.get("password") or ""

        self._client = None
        self._clouddrive_api = None
        if not self._enabled:
            return
        if not self._username or not self._password:
            logger.warning("【CloudDrive】未配置用户名或密码，储存模块将不可用")
            return
        address = f"{self._host}:{self._port}"
        try:
            self._client = CloudDriveClient(address)
            if not self._client.authenticate(self._username, self._password):
                logger.error("【CloudDrive】认证失败，请检查用户名与密码")
                self._client.close()
                self._client = None
                return
            download_base = f"http://{self._host}:{self._port}"
            self._clouddrive_api = CloudDriveApi(
                self._client, disk_name=self._disk_name, download_base=download_base
            )
        except Exception as e:
            logger.error("【CloudDrive】客户端创建失败: %s", e)
            if self._client:
                try:
                    self._client.close()
                except Exception:
                    pass
                self._client = None

    def get_state(self) -> bool:
        """
        返回插件是否已启用。
        """
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        pass

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "enabled",
                                            "label": "启用插件",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "host",
                                            "label": "CloudDrive 地址",
                                            "hint": "如 localhost 或 192.168.1.100，不要带 http(s)",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "port",
                                            "label": "端口",
                                            "hint": "默认 19798",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "username",
                                            "label": "用户名",
                                            "hint": "CloudDrive 登录用户名",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "password",
                                            "label": "密码",
                                            "type": "password",
                                            "hint": "CloudDrive 登录密码",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                ],
            }
        ], {
            "enabled": False,
            "host": "localhost",
            "port": "19798",
            "username": "",
            "password": "",
        }

    def get_page(self) -> List[dict]:
        pass

    def get_module(self) -> Dict[str, Any]:
        """
        返回储存模块能力映射
        """
        return {
            "list_files": self.list_files,
            "any_files": self.any_files,
            "download_file": self.download_file,
            "upload_file": self.upload_file,
            "delete_file": self.delete_file,
            "rename_file": self.rename_file,
            "get_file_item": self.get_file_item,
            "get_parent_item": self.get_parent_item,
            "snapshot_storage": self.snapshot_storage,
            "storage_usage": self.storage_usage,
            "support_transtype": self.support_transtype,
            "create_folder": self.create_folder,
            "exists": self.exists,
            "get_item": self.get_item,
        }

    @eventmanager.register(ChainEventType.StorageOperSelection)
    def storage_oper_selection(self, event: Event) -> None:
        """
        监听储存选择事件，当所选储存为本插件时注入 storage_oper 为 CloudDriveApi。

        :param event: 事件对象，event.event_data 含 storage、storage_oper。
        """
        if not self._enabled or not self._clouddrive_api:
            return
        event_data: StorageOperSelectionEventData = event.event_data
        if event_data.storage == self._disk_name:
            event_data.storage_oper = self._clouddrive_api  # noqa

    def list_files(
        self, fileitem: FileItem, recursion: bool = False
    ) -> Optional[List[FileItem]]:
        """
        列出目录下文件（及可选递归子目录）。

        :param fileitem: 目录或文件项，storage 需为本插件储存名。
        :param recursion: 是否递归列出子目录中的文件。
        :return: 文件项列表；非本储存或未就绪时返回空列表。
        """
        if fileitem.storage != self._disk_name or not self._clouddrive_api:
            return []
        if recursion:
            result = self._clouddrive_api.iter_files(fileitem)
            if result is not None:
                return result
        result: List[FileItem] = []

        def __get_files(_item: FileItem, _r: bool = False) -> None:
            _items = self._clouddrive_api.list(_item)  # type: ignore[union-attr]
            if _items:
                if _r:
                    for t in _items:
                        if t.type == "dir":
                            __get_files(t, _r)
                        else:
                            result.append(t)
                else:
                    result.extend(_items)

        __get_files(fileitem, recursion)
        return result

    def any_files(
        self, fileitem: FileItem, extensions: Optional[list] = None
    ) -> Optional[bool]:
        """
        判断目录（含子目录）下是否存在文件；可限定扩展名。

        :param fileitem: 目录项。
        :param extensions: 扩展名列表（如 [".mp4", ".mkv"]），None 表示任意文件。
        :return: 存在返回 True，不存在返回 False；非本储存或未就绪返回 None。
        """
        if fileitem.storage != self._disk_name or not self._clouddrive_api:
            return None

        def __any_file(_item: FileItem) -> bool:
            _items = self._clouddrive_api.list(_item)  # type: ignore[union-attr]
            if _items:
                if not extensions:
                    return True
                for t in _items:
                    if (
                        t.type == "file"
                        and t.extension
                        and f".{t.extension.lower()}" in extensions
                    ):
                        return True
                    if t.type == "dir" and __any_file(t):
                        return True
            return False

        return __any_file(fileitem)

    def create_folder(self, fileitem: FileItem, name: str) -> Optional[FileItem]:
        """
        在指定目录下创建文件夹。

        :param fileitem: 父目录项。
        :param name: 新文件夹名称。
        :return: 新目录的 FileItem；失败或非本储存时返回 None。
        """
        if fileitem.storage != self._disk_name or not self._clouddrive_api:
            return None
        return self._clouddrive_api.create_folder(fileitem, name)

    def download_file(
        self, fileitem: FileItem, path: Optional[Path] = None
    ) -> Optional[Path]:
        """
        将云端文件下载到本地。

        :param fileitem: 要下载的文件项。
        :param path: 本地保存目录，None 时使用临时目录。
        :return: 本地文件路径；失败或非本储存时返回 None。
        """
        if fileitem.storage != self._disk_name or not self._clouddrive_api:
            return None
        return self._clouddrive_api.download(fileitem, path)

    def upload_file(
        self,
        fileitem: FileItem,
        path: Path,
        new_name: Optional[str] = None,
    ) -> Optional[FileItem]:
        """
        将本地文件上传到云端指定目录。

        :param fileitem: 目标目录项。
        :param path: 本地文件路径。
        :param new_name: 云端文件名，None 时使用本地文件名。
        :return: 上传成功后的云端文件 FileItem；失败或非本储存时返回 None。
        """
        if fileitem.storage != self._disk_name or not self._clouddrive_api:
            return None
        return self._clouddrive_api.upload(fileitem, path, new_name)

    def delete_file(self, fileitem: FileItem) -> Optional[bool]:
        """
        删除云端文件或目录。

        :param fileitem: 要删除的文件或目录项。
        :return: 成功返回 True，失败返回 False；非本储存或未就绪返回 None。
        """
        if fileitem.storage != self._disk_name or not self._clouddrive_api:
            return None
        return self._clouddrive_api.delete(fileitem)

    def rename_file(self, fileitem: FileItem, name: str) -> Optional[bool]:
        """
        重命名云端文件或目录。

        :param fileitem: 要重命名的项。
        :param name: 新名称。
        :return: 成功返回 True，失败返回 False；非本储存或未就绪返回 None。
        """
        if fileitem.storage != self._disk_name or not self._clouddrive_api:
            return None
        return self._clouddrive_api.rename(fileitem, name)

    def exists(self, fileitem: FileItem) -> Optional[bool]:
        """
        判断指定路径在云端是否存在。

        :param fileitem: 文件或目录项（含 storage、path）。
        :return: 存在返回 True，不存在返回 False；非本储存返回 None。
        """
        if fileitem.storage != self._disk_name:
            return None
        return True if self.get_item(fileitem) else False

    def get_item(self, fileitem: FileItem) -> Optional[FileItem]:
        """
        按文件项获取对应的云端项（用于校验或取详情）。

        :param fileitem: 含 storage、path 的文件项。
        :return: 云端 FileItem；不存在或非本储存时返回 None。
        """
        if fileitem.storage != self._disk_name or not self._clouddrive_api:
            return None
        return self.get_file_item(storage=fileitem.storage, path=Path(fileitem.path))

    def get_file_item(self, storage: str, path: Path) -> Optional[FileItem]:
        """
        按储存名与路径获取云端文件或目录项。

        :param storage: 储存名称，需为本插件储存名。
        :param path: 云端路径。
        :return: FileItem；不存在或非本储存时返回 None。
        """
        if storage != self._disk_name or not self._clouddrive_api:
            return None
        return self._clouddrive_api.get_item(path)

    def get_parent_item(self, fileitem: FileItem) -> Optional[FileItem]:
        """
        获取指定文件或目录的父目录项。

        :param fileitem: 当前项。
        :return: 父目录 FileItem；非本储存或未就绪时返回 None。
        """
        if fileitem.storage != self._disk_name or not self._clouddrive_api:
            return None
        return self._clouddrive_api.get_parent(fileitem)

    def snapshot_storage(
        self,
        storage: str,
        path: Path,
        last_snapshot_time: Optional[float] = None,
        max_depth: int = 5,
    ) -> Optional[Dict[str, Dict]]:
        """
        对指定目录做快照，收集路径下的文件信息（路径、大小、修改时间等）。

        :param storage: 储存名称。
        :param path: 快照根路径。
        :param last_snapshot_time: 仅收录修改时间大于此时间戳的文件，用于增量快照。
        :param max_depth: 最大递归深度。
        :return: 路径到文件信息字典的映射；非本储存或未就绪时返回 None，根路径不存在时返回空字典。
        """
        if storage != self._disk_name or not self._clouddrive_api:
            return None
        files_info: Dict[str, Dict] = {}

        def __snapshot_file(_fileitem: FileItem, current_depth: int = 0) -> None:
            try:
                if _fileitem.type == "dir":
                    if current_depth >= max_depth:
                        return
                    if (
                        last_snapshot_time
                        and _fileitem.modify_time
                        and _fileitem.modify_time <= last_snapshot_time
                    ):
                        return
                    sub_files = self._clouddrive_api.list(_fileitem)  # type: ignore[union-attr]
                    for sub_file in sub_files:
                        __snapshot_file(sub_file, current_depth + 1)
                else:
                    if (getattr(_fileitem, "modify_time", 0) or 0) > (
                        last_snapshot_time or 0
                    ):
                        files_info[_fileitem.path] = {
                            "size": _fileitem.size or 0,
                            "modify_time": getattr(_fileitem, "modify_time", 0),
                            "type": _fileitem.type,
                        }
            except Exception as e:
                logger.debug("Snapshot error for %s: %s", _fileitem.path, e)

        fileitem = self._clouddrive_api.get_item(path)
        if not fileitem:
            return {}
        __snapshot_file(fileitem)
        return files_info

    def storage_usage(self, storage: str) -> Optional[StorageUsage]:
        """
        获取储存空间用量（总空间、已用、可用）。

        :param storage: 储存名称。
        :return: StorageUsage；非本储存或未就绪时返回 None。
        """
        if storage != self._disk_name or not self._clouddrive_api:
            return None
        return self._clouddrive_api.usage()

    def support_transtype(self, storage: str) -> Optional[Dict[str, str]]:
        """
        返回该储存支持的整理方式（如移动、复制）及展示名称。

        :param storage: 储存名称。
        :return: 如 {"move": "移动", "copy": "复制"}；非本储存或未就绪时返回 None。
        """
        if storage != self._disk_name or not self._clouddrive_api:
            return None
        return self._clouddrive_api.transtype

    def stop_service(self) -> None:
        """
        退出插件
        """
        if self._client:
            try:
                self._client.close()
            except Exception as e:
                logger.debug("【CloudDrive】关闭客户端: %s", e)
            self._client = None
        self._clouddrive_api = None
