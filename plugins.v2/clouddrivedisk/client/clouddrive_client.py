from typing import List, Optional

from grpc import insecure_channel

from . import clouddrive_pb2
from . import clouddrive_pb2_grpc
from google.protobuf import empty_pb2


class CloudDriveClient:
    """
    CloudDrive gRPC 客户端
    """

    def __init__(self, address: str) -> None:
        """
        初始化 CloudDrive 客户端。

        :param address: 服务器地址
        """
        self.channel = insecure_channel(address)
        self.stub = clouddrive_pb2_grpc.CloudDriveFileSrvStub(self.channel)
        self.jwt_token: Optional[str] = None

    def close(self) -> None:
        """
        关闭 gRPC 通道
        """
        self.channel.close()

    def authenticate(self, username: str, password: str) -> bool:
        """
        认证并获取 JWT 令牌。

        :param username: 用户名
        :param password: 密码
        :return: 认证成功返回 True，否则 False
        """
        request = clouddrive_pb2.GetTokenRequest(userName=username, password=password)
        response = self.stub.GetToken(request)
        if response.success:
            self.jwt_token = response.token
            return True
        return False

    def _create_authorized_metadata(self) -> List[tuple]:
        """
        创建带 Bearer 的元数据，用于需认证的 RPC。
        """
        if not self.jwt_token:
            return []
        return [("authorization", f"Bearer {self.jwt_token}")]

    def get_system_info(self):
        """
        获取系统信息（无需认证）。
        """
        return self.stub.GetSystemInfo(empty_pb2.Empty())

    def get_sub_files(self, path: str, force_refresh: bool = False) -> List:
        """
        列出目录中的文件。

        :param path: 目录路径
        :param force_refresh: 是否强制刷新缓存
        :return: CloudDriveFile 列表
        """
        request = clouddrive_pb2.ListSubFileRequest(
            path=path, forceRefresh=force_refresh
        )
        metadata = self._create_authorized_metadata()
        files: List = []
        for response in self.stub.GetSubFiles(request, metadata=metadata):
            files.extend(response.subFiles)
        return files

    def find_file_by_path(self, path: str):
        """
        按完整路径查找文件或目录。

        :param path: 完整路径
        :return: CloudDriveFile 或空响应，不存在时可能抛异常或返回空
        """
        request = clouddrive_pb2.FindFileByPathRequest(parentPath="", path=path)
        metadata = self._create_authorized_metadata()
        return self.stub.FindFileByPath(request, metadata=metadata)

    def get_space_info(self, path: str = "/"):
        """
        获取指定路径下的空间信息（总/已用/可用）。

        :param path: 路径，通常为根或挂载点
        :return: SpaceInfo (totalSpace, usedSpace, freeSpace)
        """
        request = clouddrive_pb2.FileRequest(path=path)
        metadata = self._create_authorized_metadata()
        return self.stub.GetSpaceInfo(request, metadata=metadata)

    def create_folder(self, parent_path: str, folder_name: str):
        """
        在父目录下创建文件夹。

        :param parent_path: 父目录路径
        :param folder_name: 新文件夹名称
        :return: CreateFolderResult
        """
        request = clouddrive_pb2.CreateFolderRequest(
            parentPath=parent_path, folderName=folder_name
        )
        metadata = self._create_authorized_metadata()
        return self.stub.CreateFolder(request, metadata=metadata)

    def delete_file(self, file_path: str):
        """
        删除文件或文件夹。

        :param file_path: 文件或文件夹路径
        :return: FileOperationResult
        """
        request = clouddrive_pb2.FileRequest(path=file_path)
        metadata = self._create_authorized_metadata()
        return self.stub.DeleteFile(request, metadata=metadata)

    def rename_file(self, file_path: str, new_name: str):
        """
        重命名文件或目录。

        :param file_path: 当前路径
        :param new_name: 新名称
        :return: FileOperationResult
        """
        request = clouddrive_pb2.RenameFileRequest(
            theFilePath=file_path, newName=new_name
        )
        metadata = self._create_authorized_metadata()
        return self.stub.RenameFile(request, metadata=metadata)

    def move_file(self, the_file_paths: List[str], dest_path: str):
        """
        移动文件或目录到目标路径。

        :param the_file_paths: 要移动的路径列表（云盘内路径）
        :param dest_path: 目标目录路径
        :return: FileOperationResult
        """
        request = clouddrive_pb2.MoveFileRequest(
            theFilePaths=the_file_paths, destPath=dest_path
        )
        metadata = self._create_authorized_metadata()
        return self.stub.MoveFile(request, metadata=metadata)

    def copy_file(self, the_file_paths: List[str], dest_path: str):
        """
        复制文件或目录到目标路径。

        :param the_file_paths: 要复制的路径列表（云盘内路径）
        :param dest_path: 目标目录路径
        :return: FileOperationResult
        """
        request = clouddrive_pb2.CopyFileRequest(
            theFilePaths=the_file_paths, destPath=dest_path
        )
        metadata = self._create_authorized_metadata()
        return self.stub.CopyFile(request, metadata=metadata)

    def get_download_url(
        self,
        path: str,
        preview: bool = False,
        lazy_read: bool = False,
        get_direct_url: bool = True,
    ):
        """
        获取文件下载 URL。

        :param path: 文件路径
        :param preview: 是否预览
        :param lazy_read: 是否延迟读取
        :param get_direct_url: 是否尝试获取直链
        :return: DownloadUrlPathInfo
        """
        request = clouddrive_pb2.GetDownloadUrlPathRequest(
            path=path,
            preview=preview,
            lazy_read=lazy_read,
            get_direct_url=get_direct_url,
        )
        metadata = self._create_authorized_metadata()
        return self.stub.GetDownloadUrlPath(request, metadata=metadata)

    def start_remote_upload(
        self,
        file_path: str,
        file_size: int,
        known_hashes: Optional[dict] = None,
        client_can_calculate_hashes: bool = True,
    ):
        """
        启动远程上传会话。

        :param file_path: 云端目标路径（含文件名）
        :param file_size: 文件大小（字节）
        :param known_hashes: 已知哈希，key 为 HashType (1=Md5, 2=Sha1)，value 为十六进制字符串
        :param client_can_calculate_hashes: 客户端可本地计算哈希
        :return: RemoteUploadStarted，含 upload_id
        """
        request = clouddrive_pb2.StartRemoteUploadRequest(
            file_path=file_path,
            file_size=file_size,
            known_hashes=known_hashes or {},
            client_can_calculate_hashes=client_can_calculate_hashes,
        )
        metadata = self._create_authorized_metadata()
        return self.stub.StartRemoteUpload(request, metadata=metadata)

    def remote_upload_channel(self, device_id: str = "moviepilot"):
        """
        打开远程上传通道，服务端通过流下发 read_data / hash_data / status_changed。

        :param device_id: 设备标识
        :return: 流式迭代器，产出 RemoteUploadChannelReply
        """
        request = clouddrive_pb2.RemoteUploadChannelRequest(device_id=device_id)
        metadata = self._create_authorized_metadata()
        return self.stub.RemoteUploadChannel(request, metadata=metadata)

    def remote_read_data(
        self,
        upload_id: str,
        offset: int,
        length: int,
        data: bytes,
        is_last_chunk: bool,
        lazy_read: bool = False,
    ):
        """
        响应服务端的读数据请求，上传文件块。

        :return: RemoteReadDataReply (success, error_message, bytes_received, is_last_chunk)
        """
        request = clouddrive_pb2.RemoteReadDataUpload(
            upload_id=upload_id,
            offset=offset,
            length=length,
            lazy_read=lazy_read,
            data=data,
            is_last_chunk=is_last_chunk,
        )
        metadata = self._create_authorized_metadata()
        return self.stub.RemoteReadData(request, metadata=metadata)

    def remote_upload_control_cancel(self, upload_id: str) -> None:
        """
        取消远程上传。

        :param upload_id: 上传ID
        """
        request = clouddrive_pb2.RemoteUploadControlRequest(
            upload_id=upload_id,
            cancel=clouddrive_pb2.CancelRemoteUpload(),
        )
        metadata = self._create_authorized_metadata()
        self.stub.RemoteUploadControl(request, metadata=metadata)

    def remote_hash_progress(
        self,
        upload_id: str,
        bytes_hashed: int,
        total_bytes: int,
        hash_type: int,
        hash_value: Optional[str] = None,
        block_hashes: Optional[list] = None,
    ):
        """
        上报本地计算的哈希进度或结果。

        :param hash_type: CloudDriveFile.HashType (1=Md5, 2=Sha1)
        :param hash_value: 最终哈希值（十六进制字符串），可选
        :param block_hashes: 分块 MD5 等，可选
        """
        request = clouddrive_pb2.RemoteHashProgressUpload(
            upload_id=upload_id,
            bytes_hashed=bytes_hashed,
            total_bytes=total_bytes,
            hash_type=hash_type,
            hash_value=hash_value or "",
            block_hashes=block_hashes or [],
        )
        metadata = self._create_authorized_metadata()
        return self.stub.RemoteHashProgress(request, metadata=metadata)


# TODO: 封装成 PyPi 包，方便其他项目使用
