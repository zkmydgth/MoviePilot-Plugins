import ast
import time
from pathlib import Path
from typing import Optional, List, Dict
from datetime import datetime

import pytz
import requests
from p123client import P123Client, check_response

import schemas
from app.core.config import settings
from app.log import logger


class P123Api:
    """
    123云盘基础操作
    """

    # FileId和路径缓存
    _id_cache: Dict[str, str] = {}

    def __init__(self, client: P123Client, disk_name: str):
        self.client = client
        self._disk_name = disk_name

    def _path_to_id(self, path: str):
        """
        通过路径获取ID
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
        浏览文件
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
        :param fileitem: 父目录
        :param name: 目录名
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
        """

        def __find_dir(
            _fileitem: schemas.FileItem, _name: str
        ) -> Optional[schemas.FileItem]:
            """
            查找下级目录中匹配名称的目录
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
        """
        return self.get_item(Path(fileitem.path).parent)

    def delete(self, fileitem: schemas.FileItem) -> bool:
        """
        删除文件
        此操作将保留回收站文件
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
        重命名文件
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

    def download(self, fileitem: schemas.FileItem, path: Path = None) -> Path:
        """
        下载文件，保存到本地，返回本地临时文件地址
        :param fileitem: 文件项
        :param path: 文件保存路径
        """
        json_obj = ast.literal_eval(fileitem.pickcode)
        s3keyflag = json_obj["S3KeyFlag"]
        file_id = fileitem.fileid
        file_name = fileitem.name
        md5 = json_obj["Etag"]
        size = json_obj["Size"]
        try:
            payload = {
                "Etag": md5,
                "FileID": int(file_id),
                "FileName": file_name,
                "S3KeyFlag": s3keyflag,
                "Size": int(size),
            }
            resp = self.client.download_info(payload)
            check_response(resp)
            download_url = resp["data"]["DownloadUrl"]
            local_path = path or settings.TEMP_PATH / fileitem.name
            with requests.get(download_url, stream=True) as r:
                r.raise_for_status()
                with open(local_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
            return local_path
        except Exception:
            return None

    def upload(
        self,
        target_dir: schemas.FileItem,
        local_path: Path,
        new_name: Optional[str] = None,
    ) -> Optional[schemas.FileItem]:
        """
        上传文件
        :param target_dir: 上传目录项
        :param local_path: 本地文件路径
        :param new_name: 上传后文件名
        """
        target_name = new_name or local_path.name
        target_path = Path(target_dir.path) / target_name
        try:
            for _ in range(3):
                try:
                    resp = self.client.upload_file_fast(
                        file=local_path,
                        duplicate=2,
                        file_name=new_name,
                        parent_id=target_dir.fileid,
                    )
                    logger.debug(f"【123】{new_name} 秒传文件信息: {resp}")
                    check_response(resp)
                    data = resp.get("data", {}).get("Info", None)
                    if data:
                        logger.info(f"【123】{new_name} 秒传文件成功: {target_path}")
                        logger.debug(f"【123】{new_name} 秒传文件: {data}")
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

                    resp = self.client.upload_file(
                        file=local_path,
                        duplicate=2,
                        file_name=new_name,
                        parent_id=target_dir.fileid,
                    )
                    logger.debug(f"【123】{new_name} 上传文件信息: {resp}")
                    check_response(resp)
                    data = resp.get("data", {}).get("file_info", None)
                    if data:
                        logger.info(f"【123】{new_name} 上传文件成功: {target_path}")
                        logger.debug(f"【123】{new_name} 上传文件: {data}")
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
                    logger.error(f"【123】{new_name} 上传文件出现未知错误: {e}")

                logger.warn(f"【123】{new_name} 上传文件自动重试")
                time.sleep(5)

            logger.error(f"【123】{new_name} 上传文件失败")
            return None
        except Exception as e:
            logger.error(f"【123】{new_name} 上传文件失败: {e}")
            return None

    def detail(self, fileitem: schemas.FileItem) -> Optional[schemas.FileItem]:
        """
        获取文件详情
        """
        return self.get_item(Path(fileitem.path))

    def copy(self, fileitem: schemas.FileItem, path: Path, new_name: str) -> bool:
        """
        复制文件
        :param fileitem: 文件项
        :param path: 目标目录
        :param new_name: 新文件名
        """
        try:
            resp = self.client.client.fs_copy(
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
        移动文件
        :param fileitem: 文件项
        :param path: 目标目录
        :param new_name: 新文件名
        """
        try:
            resp = self.client.client.fs_move(
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
        """
        pass

    def softlink(self, fileitem: schemas.FileItem, target_file: Path) -> bool:
        """
        软链接文件
        """
        pass

    def usage(self) -> Optional[schemas.StorageUsage]:
        """
        存储使用情况
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
