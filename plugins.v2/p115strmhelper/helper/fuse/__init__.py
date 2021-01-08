__author__ = "ChenyangGao <https://chenyanggao.github.io>"
__license__ = "GPLv3 <https://www.gnu.org/licenses/gpl-3.0.txt>"
__all__ = ["P115FuseOperations"]

from errno import ENOTDIR
from collections.abc import Callable, Mapping
from functools import wraps
from itertools import count
from os import PathLike
from os.path import exists
from posixpath import split as splitpath
from shutil import rmtree
from stat import S_IFDIR, S_IFREG
from typing import Any
from uuid import uuid4

from mfusepy import FUSE, Operations
from orjson import dumps
from p115client import P115Client

from app.log import logger
from app.core.cache import TTLCache

from ...core.cache import IntKeyCacheAdapter
from ...utils.sentry import sentry_manager


def log(func=None, *, level=None):
    """
    访问日志装饰器
    """

    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            try:
                result = f(*args, **kwargs)
                if level is None:
                    logger.debug(
                        f"{f.__name__} called with args={args}, kwargs={kwargs}, result={result}"
                    )
                return result
            except Exception as e:
                logger.error(f"{f.__name__} failed: {e}", exc_info=True)
                sentry_manager.sentry_hub.capture_exception(e)
                raise

        return wrapper

    if func is None:
        return decorator
    else:
        return decorator(func)


def attr_to_stat(attr: Mapping, /) -> dict:
    return {
        "st_mode": (S_IFDIR if attr["is_dir"] else S_IFREG) | 0o777,
        "st_ino": attr["id"],
        "st_dev": 0,
        "st_nlink": 1,
        "st_uid": 0,
        "st_gid": 0,
        "st_size": attr.get("size", 0),
        "st_atime": attr.get("atime") or attr.get("mtime", 0),
        "st_mtime": attr.get("mtime", 0),
        "st_ctime": attr.get("ctime", 0),
        "xattr": attr,
    }


class P115FuseOperations(Operations):
    def __init__(
        self,
        /,
        client: str | PathLike | P115Client = None,
        readdir_ttl: float = 60,
    ):
        """
        初始化 FUSE 操作类

        :param client: P115Client 实例或 cookie 字符串/路径
        :param readdir_ttl: 目录读取缓存 TTL（秒）
        """
        if client is None:
            raise ValueError("client 参数不能为 None，请提供 P115Client 实例或 cookie")

        if not isinstance(client, P115Client):
            client = P115Client(client, check_for_relogin=True)
        self.client = client
        ttl_cache = TTLCache(
            ttl=int(readdir_ttl),
            region="p115strmhelper_fuse_readdir",
            maxsize=8096000,
        )
        id_to_readdir_cache = IntKeyCacheAdapter(ttl_cache)
        self.fs = client.get_fs(id_to_readdir=id_to_readdir_cache)  # type: ignore[arg-type]
        self._opened: dict[int, Any] = {}
        self._get_id: Callable[[], int] = count(1).__next__

    @log
    def getattr(self, /, path: str, fh: int = 0) -> dict[str, Any]:
        return attr_to_stat(self.fs.get_attr(path))

    @log
    def getxattr(self, /, path: str, name: str, position: int = 0) -> bytes:
        attr = self.getattr(path)["xattr"]
        if name in attr:
            return dumps(attr[name])
        return b""

    @log
    def listxattr(self, /, path: str) -> list[str]:
        attr = self.getattr(path)["xattr"]
        return list(attr)

    @log
    def mkdir(self, /, path: str, mode: int = 0) -> int:
        dir_, name = splitpath(path)
        self.fs.mkdir(dir_, name)
        return 0

    @log
    def open(self, /, path: str, flags: int) -> int:
        file = self.fs.open(path)
        fh = self._get_id()
        self._opened[fh] = file
        return fh

    @log
    def opendir(self, /, path: str) -> int:
        return 0

    @log
    def read(self, /, path: str, size: int, offset: int, fh: int) -> bytes:
        file = self._opened[fh]
        file.seek(offset)
        return file.read(size)

    @log
    def readdir(self, /, path: str, fh: int = 0) -> list[str]:
        children = self.fs.readdir(path)
        return [".", "..", *(a["name"] for a in children)]

    @log
    def release(self, /, path: str, fh: int) -> int:
        if file := self._opened.pop(fh, None):
            file.close()
        return 0

    @log
    def releasedir(self, /, path: str, fh: int) -> int:
        return 0

    @log
    def rename(self, /, src: str, dst: str) -> int:
        if src != dst:
            src_dir, src_name = splitpath(src)
            dst_dir, dst_name = splitpath(dst)
            attr = self.fs.get_attr(src)
            if src_dir != dst_dir:
                if dst_dir == "/":
                    cid = 0
                else:
                    dstdir_attr = self.fs.get_attr(dst_dir)
                    if not dstdir_attr["is_dir"]:
                        raise NotADirectoryError(ENOTDIR, dst_dir)
                    cid = dstdir_attr["id"]
                self.fs.move(attr, cid)
            if src_name != dst_name:
                self.fs.rename(attr, dst_name)
        return 0

    @log
    def unlink(self, /, path: str) -> int:
        self.fs.remove(path)
        return 0

    @log
    def rmdir(self, /, path: str) -> int:
        self.fs.remove(path)
        return 0

    def run_forever(self, /, mountpoint: None | str = None, **options):
        if not mountpoint:
            mountpoint = str(uuid4())
        will_remove_mountpoint = not exists(mountpoint)
        try:
            logger.info(f"🏠 mountpoint: \x1b[4;34m{mountpoint!r}\x1b[0m")
            logger.info(f"🔨 options: {options}")
            return FUSE(self, mountpoint, **options)
        finally:
            if will_remove_mountpoint:
                rmtree(mountpoint)
