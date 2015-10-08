__author__ = "DDSRem <https://ddsrem.com>"
__all__ = ["iter_share_files_with_path", "get_pid_by_path"]


from itertools import cycle
from os import PathLike
from pathlib import Path
from threading import Lock, Event
from queue import Queue, Empty
from typing import Iterator, Literal, List
from concurrent.futures import ThreadPoolExecutor

from p115client import P115Client, check_response, normalize_attr

from ..core.cache import idpathcacher


def iter_share_files_with_path(
    client: str | PathLike | P115Client,
    share_code: str,
    receive_code: str = "",
    cid: int = 0,
    page_size: int = 0,
    order: Literal[
        "file_name", "file_size", "file_type", "user_utime", "user_ptime", "user_otime"
    ] = "user_ptime",
    asc: Literal[0, 1] = 1,
    max_workers: int = 100,
    **request_kwargs,
) -> Iterator[dict]:
    """
    批量获取分享链接下的文件列表

    :param client: 115 客户端或 cookies
    :param share_code: 分享码或链接
    :param receive_code: 接收码
    :param cid: 目录的 id
    :param page_size: 分页大小
    :param order: 排序

        - "file_name": 文件名
        - "file_size": 文件大小
        - "file_type": 文件种类
        - "user_utime": 修改时间
        - "user_ptime": 创建时间
        - "user_otime": 上一次打开时间

    :param asc: 升序排列。0: 否，1: 是
    :param max_workers: 最大工作线程数

    :return: 迭代器，返回此分享链接下的（所有文件）文件信息
    """

    class AtomicCounter:
        def __init__(self, initial=0):
            self.value = initial
            self._lock = Lock()

        def increment(self, value=1):
            with self._lock:
                self.value += value

        def decrement(self):
            with self._lock:
                self.value -= 1

        def is_zero(self):
            with self._lock:
                return self.value == 0

    if isinstance(client, (str, PathLike)):
        client = P115Client(client, check_for_relogin=True)
    if page_size <= 0:
        page_size = 1_500
    request_kwargs.setdefault(
        "base_url", cycle(("http://pro.api.115.com", "https://proapi.115.com")).__next__
    )
    task_queue = Queue()
    result_queue = Queue(maxsize=max_workers * 2)
    active_tasks = AtomicCounter(1)
    task_queue.put((cid, "", 0))
    exit_event = Event()
    exception_holder: List = []
    apps = [
        "ios",
        "android",
        "ipad",
        "web",
        "115ios",
        "115android",
        "115ipad",
        "harmony",
        "mac",
        "linux",
        "windows",
    ]
    apis = [
        lambda payload: P115Client.share_snap(payload),
    ]
    apis.extend(
        [
            lambda payload: client.share_snap_app(
                {**payload, "app": app}, **request_kwargs
            )
            for app in apps
        ]
    )
    api_cycler = cycle(apis)

    def worker():
        while not exit_event.is_set():
            try:
                current_cid, current_path_prefix, offset = task_queue.get(timeout=0.1)
            except Empty:
                if active_tasks.is_zero():
                    break
                continue
            if exit_event.is_set():
                break
            payload = {
                "share_code": share_code,
                "receive_code": receive_code,
                "cid": current_cid,
                "limit": page_size,
                "offset": offset,
                "asc": asc,
                "o": order,
            }
            api_to_use = next(api_cycler)
            try:
                resp = api_to_use(payload)
                check_response(resp)
                data = resp.get("data", {})
                count = data.get("count", 0)
                items = data.get("list", [])
                new_tasks = []
                for attr in items:
                    attr["share_code"] = share_code
                    attr["receive_code"] = receive_code
                    attr = normalize_attr(attr)
                    path = (
                        f"{current_path_prefix}/{attr['name']}"
                        if current_path_prefix
                        else f"/{attr['name']}"
                    )
                    if attr["is_dir"]:
                        new_tasks.append((int(attr["id"]), path, 0))
                    else:
                        attr["path"] = path
                        result_queue.put(attr)
                new_offset = offset + len(items)
                if new_offset < count and len(items) > 0:
                    new_tasks.append((current_cid, current_path_prefix, new_offset))
                if new_tasks:
                    active_tasks.increment(len(new_tasks))
                    for task in new_tasks:
                        task_queue.put(task)
            except Exception as e:
                if not exit_event.is_set():
                    exception_holder.append(e)
                exit_event.set()
            finally:
                active_tasks.decrement()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for _ in range(max_workers):
            executor.submit(worker)
        while not active_tasks.is_zero() or not result_queue.empty():
            try:
                yield result_queue.get(timeout=0.1)
            except Empty:
                continue
    if exit_event.is_set() and exception_holder:
        raise exception_holder[0]

def get_pid_by_path(
    client: P115Client,
    path: str | PathLike | Path,
    mkdir: bool = True,
    update_cache: bool = True,
    by_cache: bool = True,
) -> int:
    """
    通过文件夹路径获取 ID

    :param client: 115 客户端
    :param path: 文件夹路径
    :param mkdir: 不存在则创建文件夹
    :param update_cache: 更新文件路径 ID 到缓存中
    :param by_cache: 通过缓存获取

    :return int: 文件夹 ID，0 为根目录，-1 为获取失败
    """
    path = Path(path).as_posix()
    if path == "/":
        return 0
    if by_cache:
        pid = idpathcacher.get_id_by_dir(directory=path)
        if pid:
            return pid
    resp = client.fs_dir_getid(path)
    check_response(resp)
    pid = resp.get("id", -1)
    if pid == -1:
        return -1
    if pid == 0 and mkdir:
        resp = client.fs_makedirs_app(path, pid=0)
        check_response(resp)
        pid = resp["cid"]
        if update_cache:
            idpathcacher.add_cache(id=int(pid), directory=path)
        return pid
    if pid != 0:
        return pid
    return -1
