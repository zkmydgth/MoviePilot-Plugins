__author__ = "ChenyangGao <https://chenyanggao.github.io>"
__license__ = "GPLv3 <https://www.gnu.org/licenses/gpl-3.0.txt>"
__all__ = ["P115FuseOperations", "FUSE_AVAILABLE"]

from errno import EIO, ENOENT, ENOTDIR
from collections.abc import Callable, Mapping
from functools import wraps
from itertools import count
from os import PathLike
from os.path import exists
from posixpath import split as splitpath
from shutil import rmtree
from stat import S_IFDIR, S_IFREG
from time import sleep
from typing import Any
from uuid import uuid4

try:
    from mfusepy import FUSE, Operations

    FUSE_AVAILABLE = True
except (ImportError, OSError):
    FUSE = None
    Operations = None
    FUSE_AVAILABLE = False

from orjson import dumps
from p115client import P115Client

from app.log import logger
from app.core.cache import TTLCache

from ...core.cache import IntKeyCacheAdapter
from ...core.config import configer
from ...utils.sentry import sentry_manager


def _safe_repr(obj: Any) -> Any:
    """
    安全地表示对象
    """
    if isinstance(obj, bytes):
        return f"<bytes: {len(obj)} bytes>"
    elif isinstance(obj, (list, tuple)):
        return type(obj)(_safe_repr(item) for item in obj)
    elif isinstance(obj, dict):
        return {k: _safe_repr(v) for k, v in obj.items()}
    else:
        return obj


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
                    safe_args = _safe_repr(args)
                    safe_kwargs = _safe_repr(kwargs)
                    safe_result = _safe_repr(result)
                    logger.debug(
                        f"{f.__name__} called with args={safe_args}, kwargs={safe_kwargs}, result={safe_result}"
                    )
                return result
            except Exception as e:
                try:
                    error_msg = str(e)
                except (UnicodeDecodeError, UnicodeError):
                    error_msg = f"<Exception: {type(e).__name__}>"
                logger.error(f"{f.__name__} failed: {error_msg}", exc_info=True)
                sentry_manager.sentry_hub.capture_exception(e)
                raise

        return wrapper

    if func is None:
        return decorator
    else:
        return decorator(func)


def attr_to_stat(attr: Mapping, /, uid: int = 0, gid: int = 0) -> dict:
    return {
        "st_mode": (S_IFDIR if attr["is_dir"] else S_IFREG) | 0o777,
        "st_ino": attr["id"],
        "st_dev": 0,
        "st_nlink": 1,
        "st_uid": uid,
        "st_gid": gid,
        "st_size": attr.get("size") or 0,
        "st_atime": attr.get("atime") or attr.get("mtime") or 1.0,
        "st_mtime": attr.get("mtime") or 1.0,
        "st_ctime": attr.get("ctime") or 1.0,
        "xattr": attr,
    }


if not FUSE_AVAILABLE:

    class Operations:
        """
        占位基类，当 mfusepy 不可用时使用
        """

        pass


class P115FuseOperations(Operations):
    def __init__(
        self,
        /,
        client: str | PathLike | P115Client = None,
        readdir_ttl: float = 60,
        uid: int = 0,
        gid: int = 0,
    ):
        """
        初始化 FUSE 操作类

        :param client: P115Client 实例或 cookie 字符串/路径
        :param readdir_ttl: 目录读取缓存 TTL（秒）
        :param uid: 文件所有者 UID
        :param gid: 文件所有者 GID
        """
        if not FUSE_AVAILABLE:
            raise ImportError(
                "FUSE 功能不可用。可能的原因："
                "1. mfusepy 未安装，请运行: pip install mfusepy"
                "2. libfuse 未找到，请安装系统 FUSE 库"
            )

        if client is None:
            raise ValueError("client 参数不能为 None，请提供 P115Client 实例或 cookie")

        if not isinstance(client, P115Client):
            client = P115Client(client, check_for_relogin=True)
        self.client = client
        self.uid = uid
        self.gid = gid
        ttl_cache = TTLCache(
            ttl=int(readdir_ttl),
            region="p115strmhelper_fuse_readdir",
            maxsize=8096000,
        )
        id_to_readdir_cache = IntKeyCacheAdapter(ttl_cache)
        self.fs = client.get_fs(id_to_readdir=id_to_readdir_cache)  # type: ignore[arg-type]
        self._opened: dict[int, Any] = {}
        self._get_id: Callable[[], int] = count(1).__next__

    def getattr(self, /, path: str, fh: int = 0) -> dict[str, Any]:
        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                return attr_to_stat(
                    self.fs.get_attr(path, **configer.get_ios_ua_app(app=False)),
                    uid=self.uid,
                    gid=self.gid,
                )
            except FileNotFoundError:
                raise OSError(ENOENT, path)
            except OSError:
                raise
            except Exception as e:
                if attempt < max_retries:
                    sleep(1)
                    continue
                sentry_manager.sentry_hub.capture_exception(e)
                logger.error(f"【FUSE】getattr failed ({path}): {e}", exc_info=True)
                raise OSError(EIO, str(e))

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
        self.fs.mkdir(dir_, name, **configer.get_ios_ua_app(app=False))
        return 0

    @log
    def open(self, /, path: str, flags: int) -> int:
        file = self.fs.open(path, mode="rb", **configer.get_ios_ua_app(app=False))
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
        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                children = self.fs.readdir(path, **configer.get_ios_ua_app(app=False))
                return [".", "..", *(a["name"] for a in children)]
            except FileNotFoundError:
                raise OSError(ENOENT, path)
            except OSError:
                raise
            except Exception as e:
                if attempt < max_retries:
                    sleep(1)
                    continue
                raise OSError(EIO, str(e))

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
            attr = self.fs.get_attr(src, **configer.get_ios_ua_app(app=False))
            if src_dir != dst_dir:
                if dst_dir == "/":
                    cid = 0
                else:
                    dstdir_attr = self.fs.get_attr(
                        dst_dir, **configer.get_ios_ua_app(app=False)
                    )
                    if not dstdir_attr["is_dir"]:
                        raise NotADirectoryError(ENOTDIR, dst_dir)
                    cid = dstdir_attr["id"]
                self.fs.move(attr, cid, **configer.get_ios_ua_app(app=False))
            if src_name != dst_name:
                self.fs.rename(attr, dst_name, **configer.get_ios_ua_app(app=False))
        return 0

    @log
    def unlink(self, /, path: str) -> int:
        self.fs.remove(path, **configer.get_ios_ua_app(app=False))
        return 0

    @log
    def rmdir(self, /, path: str) -> int:
        self.fs.remove(path, **configer.get_ios_ua_app(app=False))
        return 0

    def run_forever(self, /, mountpoint: None | str = None, **options):
        if not FUSE_AVAILABLE:
            raise ImportError(
                "FUSE 功能不可用。可能的原因："
                "1. mfusepy 未安装，请运行: pip install mfusepy"
                "2. libfuse 未找到，请安装系统 FUSE 库"
            )

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
