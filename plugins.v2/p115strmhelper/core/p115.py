__author__ = "DDSRem <https://ddsrem.com>"
__all__ = [
    "ShareP115Client",
    "iter_share_files_with_path",
    "get_pid_by_path",
    "get_pickcode_by_path",
]


from dataclasses import dataclass
from itertools import cycle
from os import PathLike
from pathlib import Path
from typing import (
    Iterator,
    Literal,
    List,
    Tuple,
    Dict,
    Any,
    Set,
    Optional,
    Callable,
    Coroutine,
)
from concurrent.futures import ThreadPoolExecutor, Future, as_completed

from p115client import P115Client, check_response
from p115client.util import complete_url, posix_escape_name
from p115client.tool.attr import normalize_attr, get_id

from ..core.cache import idpathcacher
from ..db_manager.oper import FileDbHelper
from ..utils.limiter import ApiEndpointCooldown


class ShareP115Client(P115Client):
    """
    分享同步专用 Client
    """

    def share_snap_cookie(
        self,
        payload: dict,
        /,
        base_url: str | Callable[[], str] = "https://webapi.115.com",
        *,
        async_: Literal[False, True] = False,
        **request_kwargs,
    ) -> dict | Coroutine[Any, Any, dict]:
        """
        获取分享链接的某个目录中的文件和子目录的列表（包含详细信息）

        GET https://webapi.115.com/share/snap

        :payload:
            - share_code: str
            - receive_code: str
            - cid: int | str = 0
            - limit: int = 32
            - offset: int = 0
            - asc: 0 | 1 = <default> 💡 是否升序排列
            - o: str = <default> 💡 用某字段排序

                - "file_name": 文件名
                - "file_size": 文件大小
                - "user_ptime": 创建时间/修改时间
        """
        api = complete_url("/share/snap", base_url=base_url)
        payload = {"cid": 0, "limit": 32, "offset": 0, **payload}
        return self.request(url=api, params=payload, async_=async_, **request_kwargs)


@dataclass
class ApiEndpointInfo:
    """
    API 端点信息
    """

    endpoint: ApiEndpointCooldown
    api_name: str
    base_url: Optional[str] = None


def iter_share_files_with_path(
    client: str | PathLike | ShareP115Client,
    share_code: str,
    receive_code: str = "",
    cid: int = 0,
    order: Literal[
        "file_name", "file_size", "file_type", "user_utime", "user_ptime", "user_otime"
    ] = "user_ptime",
    asc: Literal[0, 1] = 1,
    max_workers: int = 25,
    speed_mode: Literal[0, 1, 2, 3] = 3,
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
    :param speed_mode: 运行速度模式
        0: 最快 (0.25s, 0.25s, 0.75s)
        1: 快 (0.5s, 0.5s, 1.5s)
        2: 慢 (1s, 1s, 2s)
        3: 最慢 (1.5s, 1.5s, 2s)

    :return: 迭代器，返回此分享链接下的（所有文件）文件信息
    """
    if isinstance(client, (str, PathLike)):
        client = ShareP115Client(client, check_for_relogin=True)
    speed_configs = {
        0: (0.25, 0.25, 0.75),
        1: (0.5, 0.5, 1.5),
        2: (1.0, 1.0, 2.0),
        3: (1.5, 1.5, 2.0),
    }
    app_http_cooldown, app_https_cooldown, api_cooldown = speed_configs.get(
        speed_mode, speed_configs[1]
    )
    snap_app_http_info = ApiEndpointInfo(
        endpoint=ApiEndpointCooldown(
            api_callable=lambda p: client.share_snap_app(
                p, app="android", base_url="http://pro.api.115.com", **request_kwargs
            ),
            cooldown=app_http_cooldown,
        ),
        api_name="share_snap_app_http",
        base_url="http://pro.api.115.com",
    )
    snap_app_https_info = ApiEndpointInfo(
        endpoint=ApiEndpointCooldown(
            api_callable=lambda p: client.share_snap_app(
                p, app="android", base_url="https://proapi.115.com", **request_kwargs
            ),
            cooldown=app_https_cooldown,
        ),
        api_name="share_snap_app_https",
        base_url="https://proapi.115.com",
    )
    snap_api_info = ApiEndpointInfo(
        endpoint=ApiEndpointCooldown(
            api_callable=lambda p: client.share_snap_cookie(p, **request_kwargs),
            cooldown=api_cooldown,
        ),
        api_name="share_snap",
        base_url=None,
    )
    repeating_pair = [snap_app_http_info, snap_app_https_info]
    first_page_api_pool = repeating_pair * 6
    first_page_api_pool.insert(6, snap_api_info)
    first_page_api_cycler = cycle(repeating_pair)

    def _job(
        api_info: ApiEndpointInfo,
        _cid: int,
        path_prefix: str,
        offset: int,
    ) -> Tuple[List[Dict[str, Any]], List[Tuple[int, str, int]]]:
        limit = 1_000
        if offset != 0:
            limit = 7_000
        payload = {
            "share_code": share_code,
            "receive_code": receive_code,
            "cid": _cid,
            "limit": limit,
            "offset": offset,
            "asc": asc,
            "o": order,
        }
        try:
            resp = api_info.endpoint(payload)
            check_response(resp)
        except Exception as e:
            api_info_str = f"API: {api_info.api_name}"
            if api_info.base_url:
                api_info_str += f", Base URL: {api_info.base_url}"
            api_info_str += f", Payload: {payload}"
            error_msg = f"{str(e)} | {api_info_str}"
            try:
                if e.args:
                    e.args = (error_msg,) + e.args[1:]
                else:
                    e.args = (error_msg,)
            except (TypeError, AttributeError):
                wrapper_msg = f"Exception occurred: {error_msg}"
                wrapper_e = RuntimeError(wrapper_msg)
                wrapper_e.__cause__ = e
                raise wrapper_e from e
            raise
        data = resp.get("data", {})
        count = data.get("count", 0)
        items = data.get("list", [])
        files_found = []
        subdirs_to_scan = []
        for attr in items:
            attr["share_code"] = share_code
            attr["receive_code"] = receive_code
            attr = normalize_attr(attr)
            name = posix_escape_name(attr["name"], repl="|")
            attr["name"] = name
            path = f"{path_prefix}/{name}" if path_prefix else f"/{name}"
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
                            api_to_use = snap_api_info
                        else:
                            api_to_use = next(first_page_api_cycler)
                        new_future = executor.submit(_job, api_to_use, *task_args)
                        pending_futures.add(new_future)
                except Exception:
                    for f in pending_futures:
                        f.cancel()
                    executor.shutdown(wait=False, cancel_futures=True)
                    raise
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


def get_pickcode_by_path(
    client: P115Client,
    path: str | PathLike | Path,
) -> Optional[str]:
    """
    通过文件（夹）路径获取 pick_code
    """
    db_helper = FileDbHelper()
    path = Path(path).as_posix()
    if path == "/":
        return None
    db_item = db_helper.get_by_path(path)
    if db_item:
        try:
            return db_item["pickcode"]
        except ValueError:
            return client.to_pickcode(db_item["id"])
    try:
        file_id = get_id(client=client, path=path)
        if file_id:
            return client.to_pickcode(file_id)
        return None
    except Exception:
        return None
