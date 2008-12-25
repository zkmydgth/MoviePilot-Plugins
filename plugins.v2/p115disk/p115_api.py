from pathlib import Path
from time import time, monotonic
from typing import Optional, List, Dict

from p115client import P115Client, check_response
from p115client.tool.attr import normalize_attr, get_id_to_path, get_attr
from p115client.tool.fs_files import iter_fs_files
from p115pickcode import to_pickcode

from app.chain.storage import StorageChain
from app.log import logger
from app.schemas import FileItem, StorageUsage

from .cache import IdPathCache, ItemIdCache
from .tools import RateLimiter


class P115Api:
    """
    115 网盘基础操作类
    """

    def __init__(self, client: P115Client, disk_name: str):
        """
        初始化 115 网盘 API

        :param client: 115 网盘客户端实例
        :param disk_name: 网盘名称
        """
        self.client = client
        self._disk_name = disk_name
        self._id_cache: IdPathCache = IdPathCache(maxsize=4096)
        self._id_item_cache: ItemIdCache = ItemIdCache(maxsize=4096)
        self.transtype = {"move": "移动", "copy": "复制"}

        self._rename_call_counter = 0
        self._copy_call_counter = 0
        self._move_call_counter = 0
        self._delete_call_counter = 0

        self._get_item_rate_limiter = RateLimiter(
            max_calls=2, time_window=1.0, name="get_item"
        )
        self._delete_rate_limiter = RateLimiter(
            max_calls=1, time_window=1.0, name="delete"
        )

        self._get_item_fail_records: Dict[str, Dict[str, float]] = {}
        self._get_item_blacklist: Dict[str, float] = {}

    def get_pid_by_path(self, path: Path) -> int:
        """
        通过文件夹路径获取文件夹 ID

        :param path: 文件夹路径

        :return: 目录 ID
        """
        if path.as_posix() == "/":
            return 0
        pid = self._id_cache.get_id_by_dir(directory=path.as_posix())
        if pid:
            return pid
        resp = self.client.fs_dir_getid(path.as_posix())
        check_response(resp)
        pid = resp.get("id", -1)
        if pid == 0:
            resp = self.client.fs_makedirs_app(path.as_posix(), pid=0)
            check_response(resp)
            pid = resp["cid"]
            self._id_cache.add_cache(id=int(pid), directory=path.as_posix())
            return pid
        if pid != 0:
            return pid
        return -1

    def list(self, fileitem: FileItem) -> List[FileItem]:
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
                file_id = self.get_pid_by_path(Path(fileitem.path))
            if file_id == -1:
                return []

        items = []
        try:
            for data in iter_fs_files(self.client, file_id, cooldown=1.5):
                logger.debug(f"【P115Disk】浏览目录 {data}")
                for item in data.get("data", []):
                    item = normalize_attr(item)
                    path = f"{fileitem.path}{item['name']}"
                    file_path = path + ("/" if item["is_dir"] else "")
                    self._id_cache.add_cache(id=item["id"], directory=path)
                    self._id_item_cache.add_cache(
                        id=item["id"],
                        item={
                            "path": file_path,
                            "id": item["id"],
                            "size": item["size"],
                            "modify_time": item["ctime"],
                            "pickcode": item["pickcode"],
                            "is_dir": item["is_dir"],
                        },
                    )
                    items.append(
                        FileItem(
                            storage=self._disk_name,
                            fileid=str(item["id"]),
                            parent_fileid=str(item["parent_id"]),
                            name=item["name"],
                            basename=Path(item["name"]).stem,
                            extension=Path(item["name"]).suffix[1:]
                            if not item["is_dir"]
                            else None,
                            type="dir" if item["is_dir"] else "file",
                            path=file_path,
                            size=item["size"] if not item["is_dir"] else None,
                            modify_time=item["ctime"],
                            pickcode=item["pickcode"],
                        )
                    )
        except Exception as e:
            logger.warn(f"【P115Disk】获取信息失败: {str(e)}")
            try:
                storage_chain = StorageChain()
                fileitem = FileItem(
                    storage="u115",
                    **fileitem.model_dump(exclude={"storage"}),
                )
                fallback_items = storage_chain.list_files(
                    fileitem=fileitem, recursion=False
                )
                if fallback_items:
                    result_items = []
                    for item in fallback_items:
                        if item.fileid:
                            self._id_cache.add_cache(
                                id=int(item.fileid), directory=item.path.rstrip("/")
                            )
                            self._id_item_cache.add_cache(
                                id=int(item.fileid),
                                item={
                                    "path": item.path,
                                    "id": int(item.fileid),
                                    "size": item.size,
                                    "modify_time": item.modify_time,
                                    "pickcode": item.pickcode,
                                    "is_dir": bool(item.type == "dir"),
                                },
                            )
                        result_item = FileItem(
                            storage=self._disk_name,
                            **item.model_dump(exclude={"storage"}),
                        )
                        result_items.append(result_item)
                    return result_items
            except Exception as e:
                logger.error(f"【P115Disk】获取信息失败（原版）: {str(e)}")
            return items
        return items

    def create_folder(self, fileitem: FileItem, name: str) -> Optional[FileItem]:
        """
        创建目录

        :param fileitem: 父目录文件项
        :param name: 要创建的目录名称

        :return: 创建成功返回目录文件项，失败返回None
        """
        try:
            new_path = Path(fileitem.path) / name
            parent_id = self.get_pid_by_path(Path(fileitem.path))
            if parent_id == -1:
                return None
            payload = {
                "cname": name,
                "pid": parent_id,
            }
            resp = self.client.fs_mkdir(payload)
            check_response(resp)
            logger.info(f"【P115Disk】创建目录: {resp}")
            data = resp.get("cid", resp.get("file_id", None))
            if not data:
                logger.error(f"【P115Disk】创建目录失败: {resp}")
                return None
            self._id_cache.add_cache(id=data, directory=new_path.as_posix())
            modify_time = int(time())
            self._id_item_cache.add_cache(
                id=data,
                item={
                    "path": new_path.as_posix(),
                    "id": data,
                    "size": None,
                    "modify_time": modify_time,
                    "pickcode": to_pickcode(data),
                    "is_dir": True,
                },
            )
            return FileItem(
                storage=self._disk_name,
                fileid=str(data),
                path=new_path.as_posix() + "/",
                name=name,
                basename=name,
                type="dir",
                modify_time=modify_time,
                pickcode=to_pickcode(data),
            )
        except Exception as e:
            logger.error(f"【P115Disk】创建目录失败: {str(e)}")
            return None

    def get_folder(self, path: Path) -> Optional[FileItem]:
        """
        获取目录，如目录不存在则创建

        :param path: 目录路径

        :return: 目录文件项，如果创建失败则返回None
        """
        folder = self.get_item(path)
        if folder:
            return folder

        try:
            resp = self.client.fs_makedirs_app(path.as_posix(), pid=0)
            check_response(resp)
            logger.info(f"【P115Disk】创建目录: {resp}")
            self._id_cache.add_cache(id=int(resp["cid"]), directory=path.as_posix())
            modify_time = int(time())
            self._id_item_cache.add_cache(
                id=resp["cid"],
                item={
                    "path": path.as_posix(),
                    "id": resp["cid"],
                    "size": None,
                    "modify_time": modify_time,
                    "pickcode": to_pickcode(resp["cid"]),
                    "is_dir": True,
                },
            )
            return FileItem(
                storage=self._disk_name,
                fileid=str(resp["cid"]),
                path=path.as_posix() + "/",
                name=path.name,
                basename=path.name,
                type="dir",
                modify_time=modify_time,
                pickcode=to_pickcode(resp["cid"]),
            )
        except Exception as e:
            logger.error(f"【P115Disk】{path} 目录创建出现未知错误：{e}")
            return None

    def get_item(self, path: Path) -> Optional[FileItem]:
        """
        获取文件或目录，不存在返回None
        如果连续三次调用都是同一个目录且API都返回不存在，则接下来的15秒内直接返回None

        :param path: 文件或目录路径

        :return: 文件项，如果不存在则返回None
        """
        path_str = path.as_posix()
        now = monotonic()

        if path_str in self._get_item_blacklist:
            if now < self._get_item_blacklist[path_str]:
                return None
            else:
                del self._get_item_blacklist[path_str]

        id = self._id_cache.get_id_by_dir(path_str)
        if id:
            item = self._id_item_cache.get_item(id)
            if item:
                if path_str in self._get_item_fail_records:
                    del self._get_item_fail_records[path_str]
                logger.debug(f"【P115Disk】缓存获取: {item}")
                path = Path(item["path"])
                if item["is_dir"]:
                    return FileItem(
                        storage=self._disk_name,
                        fileid=str(item["id"]),
                        path=path.as_posix() + "/",
                        name=path.name,
                        basename=path.name,
                        type="dir",
                        modify_time=item["modify_time"],
                        pickcode=item["pickcode"],
                    )
                else:
                    return FileItem(
                        storage=self._disk_name,
                        fileid=str(item["id"]),
                        parent_fileid=None,
                        name=path.name,
                        basename=path.stem,
                        extension=path.suffix[1:],
                        type="file",
                        path=path.as_posix(),
                        size=item["size"],
                        modify_time=item["modify_time"],
                        pickcode=item["pickcode"],
                    )

        self._get_item_rate_limiter.acquire()

        try:
            file_id = get_id_to_path(client=self.client, path=path_str)
            file_item = get_attr(client=self.client, id=file_id)
            logger.debug(f"【P115Disk】文件信息: {file_item}")
            if path_str in self._get_item_fail_records:
                del self._get_item_fail_records[path_str]
            self._id_cache.add_cache(id=file_item["id"], directory=path_str)
            self._id_item_cache.add_cache(
                id=file_item["id"],
                item={
                    "path": path_str,
                    "id": file_item["id"],
                    "size": file_item["size"],
                    "modify_time": file_item["mtime"],
                    "pickcode": file_item["pickcode"],
                    "is_dir": file_item["is_dir"],
                },
            )
            if file_item["is_dir"]:
                return FileItem(
                    storage=self._disk_name,
                    fileid=str(file_item["id"]),
                    parent_fileid=str(file_item["parent_id"]),
                    path=path_str + "/",
                    name=file_item["name"],
                    basename=file_item["name"],
                    type="dir",
                    modify_time=file_item["mtime"],
                    pickcode=file_item["pickcode"],
                )
            else:
                return FileItem(
                    storage=self._disk_name,
                    fileid=str(file_item["id"]),
                    parent_fileid=str(file_item["parent_id"]),
                    name=file_item["name"],
                    basename=path.stem,
                    extension=path.suffix[1:],
                    type="file",
                    path=path_str,
                    size=file_item["size"],
                    modify_time=file_item["mtime"],
                    pickcode=file_item["pickcode"],
                )
        except FileNotFoundError:
            self._record_get_item_failure(path_str, now)
            return None
        except Exception:
            storage_chain = StorageChain()
            file_item = storage_chain.get_file_item(storage="u115", path=path)
            if file_item:
                if path_str in self._get_item_fail_records:
                    del self._get_item_fail_records[path_str]
                self._id_cache.add_cache(id=int(file_item.fileid), directory=path_str)
                self._id_item_cache.add_cache(
                    id=int(file_item.fileid),
                    item={
                        "path": path_str,
                        "id": int(file_item.fileid),
                        "size": file_item.size,
                        "modify_time": file_item.modify_time,
                        "pickcode": file_item.pickcode,
                        "is_dir": bool(file_item.type == "dir"),
                    },
                )
            else:
                self._record_get_item_failure(path_str, now)
                return None
            file_item = FileItem(
                storage=self._disk_name, **file_item.model_dump(exclude={"storage"})
            )
            return file_item

    def _record_get_item_failure(self, path_str: str, now: float):
        """
        记录 get_item 失败，如果连续失败3次则加入黑名单15秒

        :param path_str: 目录路径
        :param now: 当前时间戳
        """
        if path_str not in self._get_item_fail_records:
            self._get_item_fail_records[path_str] = {"count": 0, "first_fail_time": now}

        record = self._get_item_fail_records[path_str]
        if now - record["first_fail_time"] > 10:
            record["count"] = 0
            record["first_fail_time"] = now

        record["count"] += 1

        if record["count"] >= 3:
            self._get_item_blacklist[path_str] = now + 15.0
            del self._get_item_fail_records[path_str]

    def get_parent(self, fileitem: FileItem) -> Optional[FileItem]:
        """
        获取父目录

        :param fileitem: 文件项

        :return: 父目录文件项，如果不存在则返回None
        """
        return self.get_item(Path(fileitem.path).parent)

    def delete(self, fileitem: FileItem) -> bool:
        """
        删除文件或目录
        此操作将文件移动到回收站，不会永久删除

        :param fileitem: 要删除的文件项

        :return: 删除成功返回True，失败返回False
        """
        self._delete_rate_limiter.acquire()

        try:
            self._delete_call_counter = (self._delete_call_counter + 1) % 2
            if self._delete_call_counter == 0:
                resp = self.client.fs_delete(fileitem.fileid)
            else:
                resp = self.client.fs_delete_app(fileitem.fileid)
            check_response(resp)
            logger.info(f"【P115Disk】删除文件: {resp}")
            self._id_cache.remove(id=int(fileitem.fileid))
            self._id_item_cache.remove(int(fileitem.fileid))
            return True
        except Exception as e:
            logger.warn(f"【P115Disk】删除文件错误: {e}")
            storage_chain = StorageChain()
            fileitem = FileItem(
                storage="u115",
                **fileitem.model_dump(exclude={"storage"}),
            )
            resp = storage_chain.delete_file(fileitem=fileitem)
            if resp:
                self._id_cache.remove(id=int(fileitem.fileid))
                self._id_item_cache.remove(int(fileitem.fileid))
            return resp

    def rename(self, fileitem: FileItem, name: str) -> bool:
        """
        重命名文件或目录

        :param fileitem: 要重命名的文件项
        :param name: 新名称

        :return: 重命名成功返回True，失败返回False
        """
        try:
            self._rename_call_counter = (self._rename_call_counter + 1) % 2
            if self._rename_call_counter == 0:
                resp = self.client.fs_rename((int(fileitem.fileid), name))
            else:
                resp = self.client.fs_rename_app((int(fileitem.fileid), name))
            check_response(resp)

            old_cache_path = self._id_cache.get_dir_by_id(int(fileitem.fileid))
            if old_cache_path:
                self._id_cache.remove(id=int(fileitem.fileid))
                new_path = Path(old_cache_path).parent / name
                self._id_cache.add_cache(
                    id=int(fileitem.fileid),
                    directory=new_path.as_posix(),
                )
            old_cache_item = self._id_item_cache.get_item(int(fileitem.fileid))
            if old_cache_item:
                new_path = Path(old_cache_item["path"]).parent / name
                self._id_item_cache.add_cache(
                    id=int(fileitem.fileid),
                    item={
                        "path": new_path.as_posix(),
                        "id": old_cache_item["id"],
                        "size": old_cache_item["size"],
                        "modify_time": old_cache_item["modify_time"],
                        "pickcode": old_cache_item["pickcode"],
                        "is_dir": old_cache_item["is_dir"],
                    },
                )
            return True
        except Exception as e:
            logger.error(f"【P115Disk】重命名文件错误: {e}")
            return False

    def download(self, fileitem: FileItem, path: Path = None) -> Optional[Path]:
        """
        下载文件，保存到本地，返回本地临时文件地址

        :param fileitem: 要下载的文件项
        :param path: 文件保存路径，如果为None则保存到临时目录

        :return: 下载成功返回本地文件路径，失败返回None
        """
        storage_chain = StorageChain()
        fileitem = FileItem(
            storage="u115",
            **fileitem.model_dump(exclude={"storage"}),
        )
        return storage_chain.download_file(fileitem=fileitem, path=path)

    def upload(
        self,
        target_dir: FileItem,
        local_path: Path,
        new_name: Optional[str] = None,
    ) -> Optional[FileItem]:
        """
        上传文件到云盘

        :param target_dir: 上传目标目录项
        :param local_path: 本地文件路径
        :param new_name: 上传后的文件名，如果为None则使用本地文件名

        :return: 上传成功返回文件项，失败返回None
        """
        storage_chain = StorageChain()
        target_dir = FileItem(
            storage="u115",
            **target_dir.model_dump(exclude={"storage"}),
        )
        resp = storage_chain.upload_file(
            fileitem=target_dir, path=local_path, new_name=new_name
        )
        if not resp:
            return None
        resp = FileItem(
            storage=self._disk_name,
            **resp.model_dump(exclude={"storage"}),
        )
        return resp

    def detail(self, fileitem: FileItem) -> Optional[FileItem]:
        """
        获取文件详情

        :param fileitem: 文件项

        :return: 包含详细信息的文件项，如果获取失败则返回None
        """
        return self.get_item(Path(fileitem.path))

    def copy(self, fileitem: FileItem, path: Path, new_name: str) -> bool:
        """
        复制文件或目录到目标位置

        :param fileitem: 要复制的文件项
        :param path: 目标目录路径
        :param new_name: 复制后的新文件名

        :return: 复制成功返回True，失败返回False
        """
        try:
            parent_id = self.get_pid_by_path(path)
            if parent_id == -1:
                return False
            self._copy_call_counter = (self._copy_call_counter + 1) % 2
            if self._copy_call_counter == 0:
                resp = self.client.fs_copy(fileitem.fileid, pid=parent_id)
            else:
                resp = self.client.fs_copy_app(fileitem.fileid, pid=parent_id)
            check_response(resp)
            logger.debug(f"【P115Disk】复制文件: {resp}")
            new_path = Path(path) / fileitem.name
            new_item = self.get_item(new_path)
            self.rename(new_item, new_name)
            return True
        except Exception as e:
            logger.error(f"【P115Disk】复制文件出错: {e}")
            return False

    def move(self, fileitem: FileItem, path: Path, new_name: str) -> bool:
        """
        移动文件或目录到目标位置

        :param fileitem: 要移动的文件项
        :param path: 目标目录路径
        :param new_name: 移动后的新文件名

        :return: 移动成功返回True，失败返回False
        """
        try:
            parent_id = self.get_pid_by_path(path)
            if parent_id == -1:
                return False
            self._move_call_counter = (self._move_call_counter + 1) % 2
            if self._move_call_counter == 0:
                resp = self.client.fs_move(fileitem.fileid, pid=parent_id)
            else:
                resp = self.client.fs_move_app(fileitem.fileid, pid=parent_id)
            check_response(resp)
            logger.debug(f"【P115Disk】移动文件: {resp}")
            new_path = Path(path) / fileitem.name

            old_cache_path = self._id_cache.get_dir_by_id(int(fileitem.fileid))
            if old_cache_path:
                self._id_cache.remove(id=int(fileitem.fileid))
                self._id_cache.add_cache(
                    id=int(fileitem.fileid),
                    directory=new_path.as_posix(),
                )
            old_cache_item = self._id_item_cache.get_item(int(fileitem.fileid))
            if old_cache_item:
                self._id_item_cache.add_cache(
                    id=int(fileitem.fileid),
                    item={
                        "path": new_path.as_posix(),
                        "id": old_cache_item["id"],
                        "size": old_cache_item["size"],
                        "modify_time": old_cache_item["modify_time"],
                        "pickcode": old_cache_item["pickcode"],
                        "is_dir": old_cache_item["is_dir"],
                    },
                )

            new_item = self.get_item(new_path)
            self.rename(new_item, new_name)
            return True
        except Exception as e:
            logger.error(f"移动文件出错: {e}")
            return False

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
        获取存储使用情况

        :return: 存储使用情况对象，包含总容量和可用容量，获取失败返回None
        """
        try:
            resp = self.client.fs_index_info(0)
            check_response(resp)
            return StorageUsage(
                total=resp["data"]["space_info"]["all_total"]["size"],
                available=int(resp["data"]["space_info"]["all_total"]["size"])
                - int(resp["data"]["space_info"]["all_use"]["size"]),
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
