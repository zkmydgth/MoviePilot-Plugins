import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional

import grpc
from httpx import RequestError
from httpx import stream as httpx_stream

from app.core.config import global_vars, settings
from app.log import logger
from app.modules.filemanager.storages import transfer_process
from app.schemas import FileItem, StorageUsage

from clouddrive2_client import CloudDriveClient


# 上传状态枚举
_UPLOAD_STATUS_FINISH = 5
_UPLOAD_STATUS_ERROR = 9
_UPLOAD_STATUS_FATAL_ERROR = 10
# HashType: Md5=1, Sha1=2
_HASH_TYPE_MD5 = 1
_HASH_TYPE_SHA1 = 2


def _cloudfile_to_fileitem(
    f: Any, disk_name: str, parent_fileid: Optional[str] = None
) -> FileItem:
    """
    将 proto CloudDriveFile 转为 FileItem

    :param f: proto CloudDriveFile
    :param disk_name: 磁盘名称
    :param parent_fileid: 父文件ID
    :return: FileItem
    """
    path_str = f.fullPathName or ""
    if f.isDirectory and path_str and not path_str.endswith("/"):
        path_str = path_str + "/"
    name = f.name or ""
    stem = Path(name).stem
    suffix = Path(name).suffix
    extension = suffix[1:] if suffix and not f.isDirectory else None
    modify_time = None
    if f.writeTime and f.writeTime.seconds:
        modify_time = f.writeTime.seconds
    return FileItem(
        storage=disk_name,
        fileid=f.id or "",
        parent_fileid=parent_fileid,
        name=name,
        basename=stem,
        extension=extension,
        type="dir" if f.isDirectory else "file",
        path=path_str,
        size=f.size if not f.isDirectory else None,
        modify_time=modify_time,
    )


class CloudDriveApi:
    """
    CloudDrive 储存操作实现
    """

    def __init__(
        self,
        client: CloudDriveClient,
        disk_name: str,
        download_base: Optional[str] = None,
    ) -> None:
        """
        :param client: 已认证的 CloudDrive 客户端
        :param disk_name: 储存名称，与注册的 storage 一致
        :param download_base: 下载 URL 基地址（如 http://localhost:19798），用于拼接 downloadUrlPath
        """
        self.client = client
        self._disk_name = disk_name
        self._download_base = (download_base or "http://127.0.0.1:19798").rstrip("/")
        self.transtype = {"move": "移动", "copy": "复制"}

    def list(self, fileitem: FileItem) -> List[FileItem]:
        """
        列出目录下文件，或单文件则返回包含该文件的列表。

        :param fileitem: 目录或文件项
        :return: FileItem 列表
        """
        if fileitem.type == "file":
            item = self.get_item(Path(fileitem.path))
            return [item] if item else []
        path = (fileitem.path or "/").rstrip("/") or "/"
        try:
            sub = self.client.get_sub_files(path)
        except Exception as e:
            logger.warning("【CloudDrive】列出目录失败 %s: %s", path, e)
            return []
        return [
            _cloudfile_to_fileitem(f, self._disk_name, parent_fileid=fileitem.fileid)
            for f in sub
        ]

    def iter_files(self, fileitem: FileItem) -> Optional[List[FileItem]]:
        """
        递归遍历目录，返回扁平文件列表。

        :param fileitem: 目录或文件项
        :return: 所有文件项列表，失败返回 None
        """
        if fileitem.type == "file":
            item = self.get_item(Path(fileitem.path))
            return [item] if item else []
        items: List[FileItem] = []
        try:
            for child in self.list(fileitem):
                if child.type == "dir":
                    sub_list = self.iter_files(child)
                    if sub_list is not None:
                        items.extend(sub_list)
                else:
                    items.append(child)
            return items
        except Exception as e:
            logger.warning("【CloudDrive】递归列表失败 %s: %s", fileitem.path, e)
            return None

    def get_item(self, path: Path) -> Optional[FileItem]:
        """
        按路径获取文件或目录项。

        :param path: 路径
        :return: FileItem 或 None
        """
        path_str = path.as_posix()
        if not path_str or path_str == ".":
            path_str = "/"
        try:
            f = self.client.find_file_by_path(path_str)
        except grpc.RpcError as e:
            if e.code() == grpc.StatusCode.NOT_FOUND:
                return None
            logger.error("【CloudDrive】FindFileByPath 失败 %s: %s", path_str, e)
            return None
        except Exception as e:
            logger.error("【CloudDrive】FindFileByPath 失败 %s: %s", path_str, e)
            return None
        if not f or not f.id:
            return None
        return _cloudfile_to_fileitem(f, self._disk_name)

    def get_parent(self, fileitem: FileItem) -> Optional[FileItem]:
        """
        获取父目录项

        :param fileitem: 文件项
        :return: 父目录项，如果不存在则返回 None
        """
        parent_path = Path(fileitem.path).parent
        if parent_path.as_posix() == "." or parent_path.as_posix() == "":
            parent_path = Path("/")
        return self.get_item(parent_path)

    def create_folder(self, fileitem: FileItem, name: str) -> Optional[FileItem]:
        """
        在指定目录下创建文件夹。

        :param fileitem: 父目录项
        :param name: 新文件夹名
        :return: 新目录的 FileItem，失败返回 None
        """
        parent_path = (fileitem.path or "").rstrip("/") or "/"
        try:
            result = self.client.create_folder(parent_path, name)
        except Exception as e:
            logger.warning(
                "【CloudDrive】创建文件夹失败 %s/%s: %s", parent_path, name, e
            )
            return None
        if not result or not result.result or not result.result.success:
            return None
        if result.folderCreated and result.folderCreated.id:
            return _cloudfile_to_fileitem(
                result.folderCreated, self._disk_name, parent_fileid=fileitem.fileid
            )
        new_path = f"{parent_path.rstrip('/')}/{name}/"
        return self.get_item(Path(new_path))

    def get_folder(self, path: Path) -> Optional[FileItem]:
        """
        获取目录，如目录不存在则逐级创建。

        :param path: 目录路径
        :return: 目录文件项，若创建失败则返回 None
        """
        normalized = path.as_posix().rstrip("/") or "/"
        if normalized != "/":
            folder = self.get_item(Path(normalized))
            if folder:
                return folder

        def __find_dir(parent: FileItem, name: str) -> Optional[FileItem]:
            """在父目录下查找名为 name 的子目录。"""
            for sub in self.list(parent):
                if sub.type != "dir":
                    continue
                if sub.name == name:
                    return sub
            return None

        root = self.get_item(Path("/"))
        if not root:
            root = FileItem(
                storage=self._disk_name,
                fileid="",
                parent_fileid=None,
                name="",
                basename="",
                extension=None,
                type="dir",
                path="/",
                size=None,
                modify_time=None,
            )
        fileitem = root
        parts = path.parts[1:] if path.parts[0] == "/" else path.parts
        for part in parts:
            if not part or part == ".":
                continue
            dir_file = __find_dir(fileitem, part)
            if dir_file:
                fileitem = dir_file
            else:
                dir_file = self.create_folder(fileitem, part)
                if not dir_file:
                    logger.warning(
                        "【CloudDrive】创建目录 %s%s 失败",
                        fileitem.path,
                        part,
                    )
                    return None
                fileitem = dir_file
        return fileitem

    def detail(self, fileitem: FileItem) -> Optional[FileItem]:
        """
        获取文件详情

        :param fileitem: 文件项

        :return: 包含详细信息的文件项，如果获取失败则返回None
        """
        return self.get_item(Path(fileitem.path))

    def delete(self, fileitem: FileItem) -> bool:
        """
        删除文件或目录。

        :param fileitem: 文件项
        :return: 删除成功返回 True，失败返回 False
        """
        try:
            result = self.client.delete_file(fileitem.path)
            return bool(result and result.success)
        except Exception as e:
            logger.warning("【CloudDrive】删除失败 %s: %s", fileitem.path, e)
            return False

    def rename(self, fileitem: FileItem, name: str) -> bool:
        """
        重命名文件或目录。

        :param fileitem: 文件项
        :param name: 新名称
        :return: 重命名成功返回 True，失败返回 False
        """
        try:
            result = self.client.rename_file(fileitem.path, name)
            return bool(result and result.success)
        except Exception as e:
            logger.warning(
                "【CloudDrive】重命名失败 %s -> %s: %s", fileitem.path, name, e
            )
            return False

    def move(self, fileitem: FileItem, path: Path, new_name: str) -> bool:
        """
        移动文件或目录到目标位置。

        :param fileitem: 要移动的文件项
        :param path: 目标目录路径
        :param new_name: 移动后的文件名
        :return: 成功返回 True，失败返回 False
        """
        dest_path = (
            Path(path).as_posix().rstrip("/") if path is not None else "/"
        ) or "/"
        try:
            result = self.client.move_file([fileitem.path], dest_path)
            if not result or not result.success:
                logger.warning(
                    "【CloudDrive】移动失败 %s -> %s: %s",
                    fileitem.path,
                    dest_path,
                    getattr(result, "errorMessage", "") if result else "",
                )
                return False
            if fileitem.name != new_name:
                new_path = f"{dest_path}/{fileitem.name}"
                rename_result = self.client.rename_file(new_path, new_name)
                if not rename_result or not rename_result.success:
                    logger.warning(
                        "【CloudDrive】移动后重命名失败 %s -> %s: %s",
                        new_path,
                        new_name,
                        getattr(rename_result, "errorMessage", "")
                        if rename_result
                        else "",
                    )
                    return False
            return True
        except Exception as e:
            logger.warning(
                "【CloudDrive】移动失败 %s -> %s: %s",
                fileitem.path,
                dest_path,
                e,
            )
            return False

    def copy(self, fileitem: FileItem, path: Path, new_name: str) -> bool:
        """
        复制文件或目录到目标位置。

        :param fileitem: 要复制的文件项
        :param path: 目标目录路径
        :param new_name: 复制后的文件名
        :return: 成功返回 True，失败返回 False
        """
        dest_path = (
            Path(path).as_posix().rstrip("/") if path is not None else "/"
        ) or "/"
        try:
            result = self.client.copy_file([fileitem.path], dest_path)
            if not result or not result.success:
                logger.warning(
                    "【CloudDrive】复制失败 %s -> %s: %s",
                    fileitem.path,
                    dest_path,
                    getattr(result, "errorMessage", "") if result else "",
                )
                return False
            if fileitem.name != new_name:
                new_path = f"{dest_path}/{fileitem.name}"
                rename_result = self.client.rename_file(new_path, new_name)
                if not rename_result or not rename_result.success:
                    logger.warning(
                        "【CloudDrive】复制后重命名失败 %s -> %s: %s",
                        new_path,
                        new_name,
                        getattr(rename_result, "errorMessage", "")
                        if rename_result
                        else "",
                    )
                    return False
            return True
        except Exception as e:
            logger.warning(
                "【CloudDrive】复制失败 %s -> %s: %s",
                fileitem.path,
                dest_path,
                e,
            )
            return False

    def download(
        self, fileitem: FileItem, path: Optional[Path] = None
    ) -> Optional[Path]:
        """
        下载文件到本地。

        :param fileitem: 文件项
        :param path: 本地目录，None 则使用临时目录
        :return: 本地文件路径，失败返回 None
        """
        detail = self.get_item(Path(fileitem.path))
        if not detail:
            logger.error("【CloudDrive】获取文件详情失败: %s", fileitem.name)
            return None
        try:
            url_info = self.client.get_download_url(fileitem.path, get_direct_url=True)
        except Exception as e:
            logger.error("【CloudDrive】获取下载链接失败 %s: %s", fileitem.path, e)
            return None
        download_url = None
        # 直链时使用 API 返回的 userAgent 与 additionalHeaders（见 proto DownloadUrlPathInfo）
        headers: Dict[str, str] = {}
        if url_info.directUrl:
            download_url = url_info.directUrl
            headers["User-Agent"] = (
                url_info.userAgent if url_info.userAgent else settings.USER_AGENT
            )
            if getattr(url_info, "additionalHeaders", None):
                for k, v in url_info.additionalHeaders.items():
                    if k and v:
                        headers[k] = v
        elif url_info.downloadUrlPath:
            # 代理下载走 CloudDrive 服务，使用默认 UA
            headers["User-Agent"] = settings.USER_AGENT
            # 占位符替换：{SCHEME} {HOST} {PREVIEW}
            host_part = self._download_base.replace("http://", "").replace(
                "https://", ""
            )
            path_part = (
                url_info.downloadUrlPath.replace("{SCHEME}", "http")
                .replace("{HOST}", host_part)
                .replace("{PREVIEW}", "false")
            )
            download_url = self._download_base + path_part
        if not download_url:
            logger.error("【CloudDrive】下载链接为空: %s", fileitem.name)
            return None
        local_path = (path or settings.TEMP_PATH) / fileitem.name
        file_size = detail.size
        logger.info("【CloudDrive】开始下载: %s -> %s", fileitem.name, local_path)
        progress_callback = transfer_process(Path(fileitem.path).as_posix())
        try:
            with httpx_stream("GET", download_url, headers=headers) as r:
                r.raise_for_status()
                downloaded_size = 0
                with open(local_path, "wb") as f:
                    for chunk in r.iter_bytes(chunk_size=10 * 1024 * 1024):
                        if global_vars.is_transfer_stopped(fileitem.path):
                            logger.info("【CloudDrive】%s 下载已取消", fileitem.path)
                            r.close()
                            return None
                        f.write(chunk)
                        downloaded_size += len(chunk)
                        if file_size:
                            progress = (downloaded_size * 100) / file_size
                            progress_callback(progress)
                progress_callback(100)
                logger.info("【CloudDrive】下载完成: %s", fileitem.name)
        except RequestError as e:
            logger.error("【CloudDrive】下载网络错误 %s: %s", fileitem.name, e)
            if local_path.exists():
                local_path.unlink()
            return None
        except Exception as e:
            logger.error("【CloudDrive】下载失败 %s: %s", fileitem.name, e)
            if local_path.exists():
                local_path.unlink()
            return None
        return local_path

    def _compute_file_hash(self, path: Path, hash_type: int) -> str:
        """
        计算文件 MD5(1) 或 SHA1(2)，返回十六进制字符串。

        :param path: 文件路径
        :param hash_type: 哈希类型 (MD5/SHA1)
        :return: 十六进制字符串
        """
        h = hashlib.md5() if hash_type == _HASH_TYPE_MD5 else hashlib.sha1()
        with open(path, "rb") as f:
            while chunk := f.read(8192):
                h.update(chunk)
        return h.hexdigest()

    def upload(
        self,
        target_dir: FileItem,
        local_path: Path,
        new_name: Optional[str] = None,
    ) -> Optional[FileItem]:
        """
        上传文件到 CloudDrive（Remote Upload 协议）。

        :param target_dir: 目标目录 FileItem
        :param local_path: 本地文件路径
        :param new_name: 云端文件名，None 则用 local_path.name
        :return: 上传成功返回云端文件 FileItem，失败返回 None
        """
        if not local_path.exists() or not local_path.is_file():
            logger.error("【CloudDrive】上传文件不存在或非文件: %s", local_path)
            return None
        file_size = local_path.stat().st_size
        target_name = new_name or local_path.name
        parent_path = (target_dir.path or "").rstrip("/") or "/"
        target_path = f"{parent_path.rstrip('/')}/{target_name}"

        try:
            started = self.client.start_remote_upload(
                file_path=target_path,
                file_size=file_size,
                known_hashes={},
                client_can_calculate_hashes=True,
            )
        except Exception as e:
            logger.error("【CloudDrive】StartRemoteUpload 失败 %s: %s", target_path, e)
            return None
        upload_id = started.upload_id
        progress_callback = transfer_process(target_path)
        try:
            stream = self.client.remote_upload_channel(device_id="moviepilot")
            for reply in stream:
                if global_vars.is_transfer_stopped(target_path):
                    logger.info("【CloudDrive】上传已取消: %s", target_path)
                    self.client.remote_upload_control_cancel(upload_id)
                    return None
                which = reply.WhichOneof("request")
                if not which:
                    continue
                if reply.upload_id != upload_id:
                    continue
                if which == "read_data":
                    req = reply.read_data
                    offset = req.offset
                    length = req.length
                    with open(local_path, "rb") as f:
                        f.seek(offset)
                        data = f.read(length)
                    is_last = (offset + len(data)) >= file_size
                    try:
                        resp = self.client.remote_read_data(
                            upload_id=upload_id,
                            offset=offset,
                            length=len(data),
                            data=data,
                            is_last_chunk=is_last,
                        )
                    except Exception as e:
                        logger.error("【CloudDrive】RemoteReadData 失败: %s", e)
                        self.client.remote_upload_control_cancel(upload_id)
                        return None
                    if not resp.success:
                        logger.error(
                            "【CloudDrive】RemoteReadData 服务端失败: %s",
                            resp.error_message,
                        )
                        return None
                    if file_size:
                        progress_callback((offset + len(data)) * 100.0 / file_size)
                elif which == "hash_data":
                    req = reply.hash_data
                    ht = req.hash_type
                    hash_val = self._compute_file_hash(local_path, ht)
                    try:
                        self.client.remote_hash_progress(
                            upload_id=upload_id,
                            bytes_hashed=file_size,
                            total_bytes=file_size,
                            hash_type=ht,
                            hash_value=hash_val,
                        )
                    except Exception as e:
                        logger.warning("【CloudDrive】RemoteHashProgress 失败: %s", e)
                elif which == "status_changed":
                    st = reply.status_changed.status
                    if st == _UPLOAD_STATUS_FINISH:
                        progress_callback(100)
                        break
                    if st in (_UPLOAD_STATUS_ERROR, _UPLOAD_STATUS_FATAL_ERROR):
                        msg = reply.status_changed.error_message or "上传失败"
                        logger.error("【CloudDrive】上传状态错误: %s", msg)
                        return None
        except Exception as e:
            logger.error("【CloudDrive】上传过程异常: %s", e)
            try:
                self.client.remote_upload_control_cancel(upload_id)
            except Exception:
                pass
            return None
        return self.get_item(Path(target_path))

    def link(self, fileitem: FileItem, target_file: Path) -> bool:
        """
        硬链接文件
        云盘存储不支持硬链接操作

        :param fileitem: 文件项
        :param target_file: 目标文件路径
        :return: 始终返回False，表示不支持此操作
        """
        return False

    def softlink(self, fileitem: FileItem, target_file: Path) -> bool:
        """
        软链接文件
        云盘存储不支持软链接操作

        :param fileitem: 文件项
        :param target_file: 目标文件路径
        :return: 始终返回False，表示不支持此操作
        """
        return False

    def usage(self) -> Optional[StorageUsage]:
        """
        获取存储空间用量

        :return: 存储空间用量，如果获取失败则返回None
        """
        try:
            info = self.client.get_space_info("/")
            if not info:
                return None
            return StorageUsage(
                total=info.totalSpace or 0,
                available=info.freeSpace if info.freeSpace is not None else 0,
            )
        except Exception as e:
            logger.warning("【CloudDrive】GetSpaceInfo 失败: %s", e)
            return None

    def support_transtype(self) -> dict:
        """
        支持的整理方式

        :return: 支持的整理方式字典
        """
        return self.transtype

    def is_support_transtype(self, transtype: str) -> bool:
        """
        是否支持整理方式

        :param transtype: 整理方式 (move/copy)

        :return: 是否支持
        """
        return transtype in self.transtype
