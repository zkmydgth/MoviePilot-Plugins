import ast
import time
from pathlib import Path
from typing import Optional, List, Dict
from hashlib import md5
from datetime import datetime

import requests
from p123client import check_response

from app import schemas
from app.log import logger
from app.core.config import settings, global_vars
from app.modules.filemanager.storages import transfer_process
from app.utils.string import StringUtils

from .tool import P123AutoClient


class P123Api:
    """
    123云盘基础操作类
    """

    # FileId和路径缓存
    _id_cache: Dict[str, str] = {}

    def __init__(self, client: P123AutoClient, disk_name: str):
        """
        初始化123云盘API

        :param client: 123云盘客户端实例
        :param disk_name: 云盘名称
        """
        self.client = client
        self._disk_name = disk_name
        self.transtype = {"move": "移动", "copy": "复制"}

    def _path_to_id(self, path: str) -> str:
        """
        通过路径获取文件ID

        :param path: 文件或目录路径
        :return: 文件ID字符串
        :raises FileNotFoundError: 当路径不存在时抛出异常
        """
        # 根目录
        if path == "/":
            return "0"
        if len(path) > 1 and path.endswith("/"):
            path = path[:-1]
        # 检查缓存
        if path in self._id_cache:
            return self._id_cache[path]
        # 逐级查找缓存
        current_id = 0
        parent_path = "/"
        for p in Path(path).parents:
            if str(p) in self._id_cache:
                parent_path = str(p)
                current_id = self._id_cache[parent_path]
                break
        # 计算相对路径
        rel_path = Path(path).relative_to(parent_path)
        for part in Path(rel_path).parts:
            find_part = False
            page = 1
            _next = 0
            first_find = True
            while True:
                payload = {
                    "limit": 100,
                    "next": _next,
                    "Page": page,
                    "parentFileId": int(current_id),
                    "inDirectSpace": "false",
                }
                if first_find:
                    first_find = False
                else:
                    time.sleep(1)
                resp = self.client.fs_list(payload)
                check_response(resp)
                item_list = resp.get("data").get("InfoList")
                if not item_list:
                    break
                for item in item_list:
                    if item["FileName"] == part:
                        current_id = item["FileId"]
                        find_part = True
                        break
                if find_part:
                    break
                if resp.get("data").get("Next") == "-1":
                    break
                else:
                    page += 1
                    _next = resp.get("data").get("Next")
            if not find_part:
                raise FileNotFoundError(f"【123】{path} 不存在")
        if not current_id:
            raise FileNotFoundError(f"【123】{path} 不存在")
        # 缓存路径
        self._id_cache[path] = str(current_id)
        return str(current_id)

    def list(self, fileitem: schemas.FileItem) -> List[schemas.FileItem]:
        """
        浏览文件或目录

        :param fileitem: 文件项，可以是文件或目录
        :return: 文件项列表，如果是文件则返回包含该文件的列表，如果是目录则返回目录下的所有文件和子目录
        """
        if fileitem.type == "file":
            item = self.detail(fileitem)
            if item:
                return [item]
            return []
        if fileitem.path == "/":
            file_id = "0"
        else:
            file_id = fileitem.fileid
            if not file_id:
                file_id = self._path_to_id(fileitem.path)

        items = []
        try:
            page = 1
            _next = 0
            first_find = True
            while True:
                payload = {
                    "limit": 100,
                    "next": _next,
                    "Page": page,
                    "parentFileId": int(file_id),
                    "inDirectSpace": "false",
                }
                if first_find:
                    first_find = False
                else:
                    time.sleep(1)
                resp = self.client.fs_list(payload)
                check_response(resp)
                item_list = resp.get("data").get("InfoList")
                if not item_list:
                    break
                for item in item_list:
                    path = f"{fileitem.path}{item['FileName']}"
                    self._id_cache[path] = str(item["FileId"])

                    file_path = path + ("/" if item["Type"] == 1 else "")
                    items.append(
                        schemas.FileItem(
                            storage=self._disk_name,
                            fileid=str(item["FileId"]),
                            parent_fileid=str(item["ParentFileId"]),
                            name=item["FileName"],
                            basename=Path(item["FileName"]).stem,
                            extension=Path(item["FileName"]).suffix[1:]
                            if item["Type"] == 0
                            else None,
                            type="dir" if item["Type"] == 1 else "file",
                            path=file_path,
                            size=item["Size"] if item["Type"] == 0 else None,
                            modify_time=int(
                                datetime.fromisoformat(item["UpdateAt"]).timestamp()
                            ),
                            pickcode=str(item),
                        )
                    )
                if resp.get("data").get("Next") == "-1":
                    break
                else:
                    page += 1
                    _next = resp.get("data").get("Next")
        except Exception as e:
            logger.debug(f"【123】获取信息失败: {str(e)}")
            return items
        return items

    def create_folder(
        self, fileitem: schemas.FileItem, name: str
    ) -> Optional[schemas.FileItem]:
        """
        创建目录

        :param fileitem: 父目录文件项
        :param name: 要创建的目录名称
        :return: 创建成功返回目录文件项，失败返回None
        """
        try:
            new_path = Path(fileitem.path) / name
            resp = self.client.fs_mkdir(name, parent_id=self._path_to_id(fileitem.path))
            check_response(resp)
            logger.debug(f"【123】创建目录: {resp}")
            data = resp["data"]["Info"]
            # 缓存新目录
            self._id_cache[str(new_path)] = str(data["FileId"])
            return schemas.FileItem(
                storage=self._disk_name,
                fileid=str(data["FileId"]),
                path=str(new_path) + "/",
                name=name,
                basename=name,
                type="dir",
                modify_time=int(datetime.fromisoformat(data["UpdateAt"]).timestamp()),
                pickcode=str(data),
            )
        except Exception as e:
            logger.debug(f"【123】创建目录失败: {str(e)}")
            return None

    def get_folder(self, path: Path) -> Optional[schemas.FileItem]:
        """
        获取目录，如目录不存在则创建

        :param path: 目录路径
        :return: 目录文件项，如果创建失败则返回None
        """

        def __find_dir(
            _fileitem: schemas.FileItem, _name: str
        ) -> Optional[schemas.FileItem]:
            """
            查找下级目录中匹配名称的目录

            :param _fileitem: 父目录文件项
            :param _name: 要查找的目录名称
            :return: 找到的目录文件项，未找到返回None
            """
            for sub_folder in self.list(_fileitem):
                if sub_folder.type != "dir":
                    continue
                if sub_folder.name == _name:
                    return sub_folder
            return None

        # 是否已存在
        folder = self.get_item(path)
        if folder:
            return folder
        # 逐级查找和创建目录
        fileitem = schemas.FileItem(storage=self._disk_name, path="/")
        for part in path.parts[1:]:
            dir_file = __find_dir(fileitem, part)
            if dir_file:
                fileitem = dir_file
            else:
                dir_file = self.create_folder(fileitem, part)
                if not dir_file:
                    logger.warn(f"【123】创建目录 {fileitem.path}{part} 失败！")
                    return None
                fileitem = dir_file
        return fileitem

    def get_item(self, path: Path) -> Optional[schemas.FileItem]:
        """
        获取文件或目录，不存在返回None

        :param path: 文件或目录路径
        :return: 文件项，如果不存在则返回None
        """
        try:
            file_id = self._path_to_id(str(path))
            if not file_id:
                return None
            resp = self.client.fs_info(int(file_id))
            check_response(resp)
            logger.debug(f"【123】获取文件信息: {resp}")
            data = resp["data"]["infoList"][0]
            return schemas.FileItem(
                storage=self._disk_name,
                fileid=str(data["FileId"]),
                path=str(path) + ("/" if data["Type"] == 1 else ""),
                type="file" if data["Type"] == 0 else "dir",
                name=data["FileName"],
                basename=Path(data["FileName"]).stem,
                extension=Path(data["FileName"]).suffix[1:]
                if data["Type"] == 0
                else None,
                pickcode=str(data),
                size=data["Size"] if data["Type"] == 0 else None,
                modify_time=int(datetime.fromisoformat(data["UpdateAt"]).timestamp()),
            )
        except Exception as e:
            logger.debug(f"【123】获取文件信息失败: {str(e)}")
            return None

    def get_parent(self, fileitem: schemas.FileItem) -> Optional[schemas.FileItem]:
        """
        获取父目录

        :param fileitem: 文件项
        :return: 父目录文件项，如果不存在则返回None
        """
        return self.get_item(Path(fileitem.path).parent)

    def delete(self, fileitem: schemas.FileItem) -> bool:
        """
        删除文件或目录
        此操作将文件移动到回收站，不会永久删除

        :param fileitem: 要删除的文件项
        :return: 删除成功返回True，失败返回False
        """
        try:
            resp = self.client.fs_trash(int(fileitem.fileid), event="intoRecycle")
            check_response(resp)
            logger.debug(f"【123】删除文件: {resp}")
            return True
        except Exception:
            return False

    def rename(self, fileitem: schemas.FileItem, name: str) -> bool:
        """
        重命名文件或目录

        :param fileitem: 要重命名的文件项
        :param name: 新名称
        :return: 重命名成功返回True，失败返回False
        """
        try:
            payload = {
                "FileId": int(fileitem.fileid),
                "fileName": name,
                "duplicate": 2,
            }
            resp = self.client.fs_rename(payload)
            check_response(resp)
            logger.debug(f"【123】重命名文件: {resp}")
            return True
        except Exception:
            return False

    def download(self, fileitem: schemas.FileItem, path: Path = None) -> Optional[Path]:
        """
        下载文件，保存到本地，返回本地临时文件地址

        :param fileitem: 要下载的文件项
        :param path: 文件保存路径，如果为None则保存到临时目录
        :return: 下载成功返回本地文件路径，失败返回None
        """
        json_obj = ast.literal_eval(fileitem.pickcode)
        s3keyflag = json_obj["S3KeyFlag"]
        file_id = fileitem.fileid
        file_name = fileitem.name
        _md5 = json_obj["Etag"]
        size = json_obj["Size"]
        try:
            payload = {
                "Etag": _md5,
                "FileID": int(file_id),
                "FileName": file_name,
                "S3KeyFlag": s3keyflag,
                "Size": int(size),
            }
            resp = self.client.download_info(payload)
            check_response(resp)
            download_url = resp["data"]["DownloadUrl"]
            local_path = (path or settings.TEMP_PATH) / fileitem.name
        except Exception as e:
            logger.error(f"【123】获取下载链接失败: {fileitem.name} - {str(e)}")
            return None

        # 获取文件大小
        file_size = fileitem.size

        # 初始化进度条
        logger.info(f"【123】开始下载: {fileitem.name} -> {local_path}")
        progress_callback = transfer_process(Path(fileitem.path).as_posix())

        try:
            with requests.get(download_url, stream=True) as r:
                r.raise_for_status()
                downloaded_size = 0

                with open(local_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=10 * 1024 * 1024):
                        if global_vars.is_transfer_stopped(fileitem.path):
                            logger.info(f"【123】{fileitem.path} 下载已取消！")
                            return None
                        if chunk:
                            f.write(chunk)
                            downloaded_size += len(chunk)
                            # 更新进度
                            if file_size:
                                progress = (downloaded_size * 100) / file_size
                                progress_callback(progress)

                # 完成下载
                progress_callback(100)
                logger.info(f"【123】下载完成: {fileitem.name}")

        except requests.exceptions.RequestException as e:
            logger.error(f"【123】下载网络错误: {fileitem.name} - {str(e)}")
            if local_path.exists():
                local_path.unlink()
            return None
        except Exception as e:
            logger.error(f"【123】下载失败: {fileitem.name} - {str(e)}")
            if local_path.exists():
                local_path.unlink()
            return None

        return local_path

    def upload(
        self,
        target_dir: schemas.FileItem,
        local_path: Path,
        new_name: Optional[str] = None,
    ) -> Optional[schemas.FileItem]:
        """
        上传文件到云盘
        支持秒传、分块上传和普通上传，自动根据文件大小选择上传方式

        :param target_dir: 上传目标目录项
        :param local_path: 本地文件路径
        :param new_name: 上传后的文件名，如果为None则使用本地文件名
        :return: 上传成功返回文件项，失败返回None
        """
        target_name = new_name or local_path.name
        target_path = Path(target_dir.path) / target_name

        file_size = local_path.stat().st_size

        logger.debug(f"【123】{local_path} 开始计算 md5 值...")
        file_md5 = ""
        with open(local_path, "rb") as f:
            hash_md5 = md5()
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
            file_md5 = hash_md5.hexdigest()

        try:
            # 秒传文件
            resp = self.client.upload_request(
                {
                    "etag": file_md5,
                    "fileName": target_name,
                    "size": file_size,
                    "parentFileId": int(target_dir.fileid),
                    "type": 0,
                    "duplicate": 2,
                }
            )
            check_response(resp)
            if resp.get("data").get("Reuse"):
                logger.info(f"【123】{target_name} 秒传成功")
                logger.debug(resp)
                data = resp.get("data", {}).get("Info", {})
                return schemas.FileItem(
                    storage=self._disk_name,
                    fileid=str(data["FileId"]),
                    path=str(target_path) + ("/" if data["Type"] == 1 else ""),
                    type="file" if data["Type"] == 0 else "dir",
                    name=data["FileName"],
                    basename=Path(data["FileName"]).stem,
                    extension=Path(data["FileName"]).suffix[1:]
                    if data["Type"] == 0
                    else None,
                    pickcode=str(data),
                    size=data["Size"] if data["Type"] == 0 else None,
                    modify_time=int(
                        datetime.fromisoformat(data["UpdateAt"]).timestamp()
                    ),
                )
        except Exception as e:
            logger.error(f"【123】{target_name} 秒传出现未知错误：{e}")
            return None

        try:
            # 上传信息
            upload_data = resp["data"]
            # 分块大小
            slice_size = int(upload_data["SliceSize"])

            upload_request_kwargs = {
                "method": "PUT",
                "headers": {"authorization": ""},
                "parse": ...,
                "timeout": 300,  # 设置5分钟超时
            }

            if file_size > slice_size:
                # 大文件分块上传
                logger.info(
                    f"【123】开始上传: {local_path} -> {target_path}，分片大小：{StringUtils.str_filesize(slice_size)}"
                )
                # 初始化进度条
                progress_callback = transfer_process(local_path.as_posix())

                with open(local_path, "rb") as f:
                    slice_no = 1
                    offset = 0
                    for chunk in iter(lambda: f.read(slice_size), b""):
                        if global_vars.is_transfer_stopped(local_path.as_posix()):
                            logger.info(f"【123】{local_path} 上传已取消！")
                            return None

                        if not chunk:
                            break

                        num_to_upload = min(slice_size, file_size - offset)

                        # 准备分片信息
                        upload_data["partNumberStart"] = slice_no
                        upload_data["partNumberEnd"] = slice_no + 1
                        upload_url_resp = self.client.upload_prepare(
                            upload_data,
                        )
                        check_response(upload_url_resp)

                        logger.info(
                            f"【123】开始上传 {target_name} 分片 {slice_no}: {offset} -> {offset + num_to_upload}"
                        )
                        logger.debug(f"{upload_url_resp} {upload_data}")

                        # 上传分片，失败时重试5次，每次重新获取上传URL
                        max_retries = 6
                        retry_count = 0
                        upload_success = False
                        current_upload_url_resp = upload_url_resp

                        while retry_count < max_retries and not upload_success:
                            try:
                                self.client.request(
                                    current_upload_url_resp["data"]["presignedUrls"][
                                        str(slice_no)
                                    ],
                                    data=chunk,
                                    **upload_request_kwargs,
                                )
                                upload_success = True
                            except Exception as upload_err:
                                retry_count += 1
                                if retry_count < max_retries:
                                    logger.warning(
                                        f"【123】{target_name} 分片 {slice_no} 上传失败，正在重试 ({retry_count}/{max_retries}): {upload_err}"
                                    )
                                    time.sleep(10)  # 等待10秒后重试

                                    # 重新获取上传URL
                                    try:
                                        logger.info(
                                            f"【123】重新获取分片 {slice_no} 的上传URL"
                                        )
                                        current_upload_url_resp = (
                                            self.client.upload_prepare(
                                                upload_data,
                                            )
                                        )
                                        check_response(current_upload_url_resp)
                                    except Exception as url_err:
                                        logger.error(
                                            f"【123】重新获取上传URL失败: {url_err}"
                                        )
                                        raise
                                else:
                                    logger.error(
                                        f"【123】{target_name} 分片 {slice_no} 上传失败，已达到最大重试次数: {upload_err}"
                                    )
                                    raise
                        slice_no += 1
                        offset += num_to_upload

                        # 更新进度
                        progress = (offset * 100) / file_size
                        progress_callback(progress)

                # 完成上传
                progress_callback(100)
            else:
                # 小文件直接上传
                logger.info(f"【123】开始上传: {local_path} -> {target_path}")

                resp = self.client.upload_auth(
                    upload_data,
                )
                check_response(resp)

                # 上传文件，失败时重试6次，每次重新获取上传URL
                max_retries = 6
                retry_count = 0
                upload_success = False
                current_resp = resp

                with open(local_path, "rb") as f:
                    file_data = f.read()

                while retry_count < max_retries and not upload_success:
                    try:
                        self.client.request(
                            current_resp["data"]["presignedUrls"]["1"],
                            data=file_data,
                            **upload_request_kwargs,
                        )
                        upload_success = True
                    except Exception as upload_err:
                        retry_count += 1
                        if retry_count < max_retries:
                            logger.warning(
                                f"【123】{target_name} 上传失败，正在重试 ({retry_count}/{max_retries}): {upload_err}"
                            )
                            time.sleep(10)  # 等待10秒后重试

                            # 重新获取上传URL
                            try:
                                logger.info("【123】重新获取上传URL")
                                current_resp = self.client.upload_auth(
                                    upload_data,
                                )
                                check_response(current_resp)
                            except Exception as url_err:
                                logger.error(f"【123】重新获取上传URL失败: {url_err}")
                                raise
                        else:
                            logger.error(
                                f"【123】{target_name} 上传失败，已达到最大重试次数: {upload_err}"
                            )
                            raise

            upload_data["isMultipart"] = file_size > slice_size
            complete_resp = self.client.upload_complete(
                upload_data,
            )
            check_response(complete_resp)

            data = complete_resp.get("data", {}).get("file_info", {})
            return schemas.FileItem(
                storage=self._disk_name,
                fileid=str(data["FileId"]),
                path=str(target_path) + ("/" if data["Type"] == 1 else ""),
                type="file" if data["Type"] == 0 else "dir",
                name=data["FileName"],
                basename=Path(data["FileName"]).stem,
                extension=Path(data["FileName"]).suffix[1:]
                if data["Type"] == 0
                else None,
                pickcode=str(data),
                size=data["Size"] if data["Type"] == 0 else None,
                modify_time=int(datetime.fromisoformat(data["UpdateAt"]).timestamp()),
            )
        except Exception as e:
            logger.error(f"【123】{target_name} 上传出现未知错误：{e}")
            return None

    def detail(self, fileitem: schemas.FileItem) -> Optional[schemas.FileItem]:
        """
        获取文件详情

        :param fileitem: 文件项
        :return: 包含详细信息的文件项，如果获取失败则返回None
        """
        return self.get_item(Path(fileitem.path))

    def copy(self, fileitem: schemas.FileItem, path: Path, new_name: str) -> bool:
        """
        复制文件或目录到目标位置

        :param fileitem: 要复制的文件项
        :param path: 目标目录路径
        :param new_name: 复制后的新文件名
        :return: 复制成功返回True，失败返回False
        """
        try:
            resp = self.client.fs_copy(
                fileitem.fileid, parent_id=self._path_to_id(str(path))
            )
            check_response(resp)
            logger.debug(f"【123】复制文件: {resp}")
            new_path = Path(path) / fileitem.name
            new_item = self.get_item(new_path)
            self.rename(new_item, new_name)
            # 更新缓存
            del self._id_cache[fileitem.path]
            rename_new_path = Path(path) / new_name
            self._id_cache[str(rename_new_path)] = new_item.fileid
            return True
        except Exception:
            return False

    def move(self, fileitem: schemas.FileItem, path: Path, new_name: str) -> bool:
        """
        移动文件或目录到目标位置

        :param fileitem: 要移动的文件项
        :param path: 目标目录路径
        :param new_name: 移动后的新文件名
        :return: 移动成功返回True，失败返回False
        """
        try:
            resp = self.client.fs_move(
                fileitem.fileid, parent_id=self._path_to_id(str(path))
            )
            check_response(resp)
            logger.debug(f"【123】移动文件: {resp}")
            new_path = Path(path) / fileitem.name
            new_item = self.get_item(new_path)
            self.rename(new_item, new_name)
            # 更新缓存
            del self._id_cache[fileitem.path]
            rename_new_path = Path(path) / new_name
            self._id_cache[str(rename_new_path)] = new_item.fileid
            return True
        except Exception:
            return False

    def link(self, fileitem: schemas.FileItem, target_file: Path) -> bool:
        """
        硬链接文件
        云盘存储不支持硬链接操作

        :param fileitem: 文件项
        :param target_file: 目标文件路径
        :return: 始终返回False，表示不支持此操作
        """
        return False

    def softlink(self, fileitem: schemas.FileItem, target_file: Path) -> bool:
        """
        软链接文件
        云盘存储不支持软链接操作

        :param fileitem: 文件项
        :param target_file: 目标文件路径
        :return: 始终返回False，表示不支持此操作
        """
        return False

    def usage(self) -> Optional[schemas.StorageUsage]:
        """
        获取存储使用情况

        :return: 存储使用情况对象，包含总容量和可用容量，获取失败返回None
        """
        try:
            resp = self.client.user_info()
            check_response(resp)
            return schemas.StorageUsage(
                total=resp["data"]["SpacePermanent"],
                available=int(resp["data"]["SpacePermanent"])
                - int(resp["data"]["SpaceUsed"]),
            )
        except Exception:
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
