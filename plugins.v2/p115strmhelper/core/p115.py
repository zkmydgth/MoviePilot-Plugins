__author__ = "DDSRem <https://ddsrem.com>"
__all__ = ["iter_share_files_with_path", "get_pid_by_path"]


from itertools import cycle
from os import PathLike
from pathlib import Path
from typing import Iterator, Literal, List, Tuple, Dict, Any, Set
from concurrent.futures import ThreadPoolExecutor, Future, as_completed

from p115client import P115Client, check_response
from p115client.tool.attr import normalize_attr

from ..core.cache import idpathcacher
from ..utils.limiter import ApiEndpointCooldown


def iter_share_files_with_path(
    client: str | PathLike | P115Client,
    share_code: str,
    receive_code: str = "",
    cid: int = 0,
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
    if isinstance(client, (str, PathLike)):
        client = P115Client(client, check_for_relogin=True)
    snap_app_http = ApiEndpointCooldown(
        api_callable=lambda p: client.share_snap_app(
            p, app="android", base_url="http://pro.api.115.com", **request_kwargs
        ),
        cooldown=0.05,
    )
    snap_app_https = ApiEndpointCooldown(
        api_callable=lambda p: client.share_snap_app(
            p, app="android", base_url="https://proapi.115.com", **request_kwargs
        ),
        cooldown=0.05,
    )
    snap_api = ApiEndpointCooldown(
        api_callable=lambda p: P115Client.share_snap(p, **request_kwargs),
        cooldown=0.15,
    )

    repeating_pair = [snap_app_http, snap_app_https]
    first_page_api_pool = repeating_pair * 6
    first_page_api_pool.insert(6, snap_api)
    first_page_api_cycler = cycle(first_page_api_pool)

    def _job(
        api_endpoint: ApiEndpointCooldown,
        _cid: int,
        path_prefix: str,
        offset: int,
    ) -> Tuple[List[Dict[str, Any]], List[Tuple[int, str, int]]]:
        payload = {
            "share_code": share_code,
            "receive_code": receive_code,
            "cid": _cid,
            "limit": 1000,
            "offset": offset,
            "asc": asc,
            "o": order,
        }
        resp = api_endpoint(payload)
        check_response(resp)
        data = resp.get("data", {})
        count = data.get("count", 0)
        items = data.get("list", [])
        files_found = []
        subdirs_to_scan = []
        for attr in items:
            attr["share_code"] = share_code
            attr["receive_code"] = receive_code
            attr = normalize_attr(attr)
            path = (
                f"{path_prefix}/{attr['name']}" if path_prefix else f"/{attr['name']}"
            )
            if attr["is_dir"]:
                subdirs_to_scan.append((int(attr["id"]), path, 0))
            else:
                attr["path"] = path
                files_found.append(attr)
        new_offset = offset + len(items)
        if new_offset < count and len(items) > 0:
            subdirs_to_scan.append((_cid, path_prefix, new_offset))
        return files_found, subdirs_to_scan

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        pending_futures: Set[Future] = set()
        initial_future = executor.submit(_job, next(first_page_api_cycler), cid, "", 0)
        pending_futures.add(initial_future)
        while pending_futures:
            for future in as_completed(pending_futures):
                pending_futures.remove(future)
                try:
                    files, subdirs = future.result()
                    for file_info in files:
                        yield file_info
                    for task_args in subdirs:
                        task_offset = task_args[2]
                        if task_offset > 0:
                            api_to_use = snap_api
                        else:
                            api_to_use = next(first_page_api_cycler)
                        new_future = executor.submit(_job, api_to_use, *task_args)
                        pending_futures.add(new_future)
                except Exception as e:
                    for f in pending_futures:
                        f.cancel()
                    executor.shutdown(wait=False, cancel_futures=True)
                    raise e
                break


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
