__author__ = "DDSRem <https://ddsrem.com>"
__all__ = [
    "ShareP115Client",
    "iter_share_files_with_path",
    "get_pid_by_path",
    "get_pickcode_by_path",
    "P115DiskHelper",
]


from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import cycle
from os import PathLike
from pathlib import Path
from time import perf_counter, sleep
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
    TYPE_CHECKING,
)
from concurrent.futures import ThreadPoolExecutor, Future, as_completed

from cryptography.hazmat.primitives import hashes
from oss2 import StsAuth, Bucket, determine_part_size
from oss2.exceptions import ServerError
from oss2.models import PartInfo
from oss2.utils import b64encode_as_string, SizedFileAdapter
from p115client import P115Client, check_response
from p115client.util import complete_url, posix_escape_name
from p115client.tool.attr import normalize_attr, get_id

from app.core.config import global_vars
from app.log import logger
from app.modules.filemanager.storages import transfer_process
from app.schemas import FileItem, NotificationType
from app.utils.string import StringUtils

from ..core.cache import idpathcacher
from ..core.config import configer
from ..core.message import post_message
from ..db_manager.oper import FileDbHelper
from ..utils.limiter import ApiEndpointCooldown
from ..utils.oopserver import OOPServerRequest


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


class P115DiskHelper:
    """
    模拟 P115Disk 插件接口
    """

    def __init__(self, client: P115Client):
        if TYPE_CHECKING:
            from ...p115disk.p115_api import P115Api
        else:
            P115Api = Any

        try:
            from app.plugins.p115disk.p115_api import P115Api  # noqa: F401

            P115_API_AVAILABLE = True
        except (ImportError, Exception):
            P115_API_AVAILABLE = False

        if P115_API_AVAILABLE:
            self._p115_api = P115Api(client=client, disk_name="115网盘Plus")

        self.oopserver_request = OOPServerRequest(max_retries=3, backoff_factor=1.0)

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

        def read_range_hash(range_str: str) -> str:
            start, end = map(int, range_str.split("-"))
            with open(local_path, "rb") as f:
                f.seek(start)
                chunk = f.read(end - start + 1)
                sha1 = hashes.Hash(hashes.SHA1())
                sha1.update(chunk)
                return sha1.finalize().hex().upper()

        def encode_callback(cb: str) -> str:
            return b64encode_as_string(cb)

        def send_upload_info(
            file_sha1: Optional[str],
            first_sha1: Optional[str],
            second_auth: bool,
            second_sha1: Optional[str],
            file_size: Optional[str],
            file_name: Optional[str],
            upload_time: Optional[int],
        ):
            """
            发送上传信息
            """
            path = "/upload/info"
            headers = {"x-machine-id": configer.get_config("MACHINE_ID")}
            json_data = {
                "file_sha1": file_sha1,
                "first_sha1": first_sha1,
                "second_auth": second_auth,
                "second_sha1": second_sha1,
                "file_size": file_size,
                "file_name": file_name,
                "time": upload_time,
                "postime": datetime.now(timezone.utc)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z"),
            }
            try:
                response = self.oopserver_request.make_request(
                    path=path,
                    method="POST",
                    headers=headers,
                    json_data=json_data,
                    timeout=10.0,
                )

                if response is not None and response.status_code == 201:
                    logger.info(
                        f"【P115Disk】上传信息报告服务器成功: {response.json()}"
                    )
                else:
                    logger.warn("【P115Disk】上传信息报告服务器失败，网络问题")
            except Exception as e:
                logger.warn(f"【P115Disk】上传信息报告服务器失败: {e}")

        def send_upload_wait(target_name):
            """
            发送上传等待
            """
            if configer.notify and configer.upload_module_notify:
                post_message(
                    mtype=NotificationType.Plugin,
                    title="【115网盘】上传模块增强",
                    text=f"\n触发秒传等待：{target_name}\n",
                )

            try:
                self.oopserver_request.make_request(
                    path="/upload/wait",
                    method="POST",
                    headers={"x-machine-id": configer.get_config("MACHINE_ID")},
                    timeout=10.0,
                )
            except Exception:
                pass

        def send_upload_result_notify(
            success: bool,
            target_name: str,
            file_size: int,
            elapsed_time: Optional[float] = None,
            error_msg: Optional[str] = None,
        ):
            """
            发送上传结果通知

            :param success: 是否成功
            :param target_name: 文件名
            :param file_size: 文件大小
            :param elapsed_time: 耗时（秒）
            :param error_msg: 错误信息
            """
            if not configer.notify or not configer.upload_open_result_notify:
                return

            if success:
                size_str = StringUtils.str_filesize(file_size)
                time_str = f"{elapsed_time:.1f}秒" if elapsed_time else "未知"
                post_message(
                    mtype=NotificationType.Plugin,
                    title="【115网盘】上传成功",
                    text=f"\n文件名：{target_name}\n文件大小：{size_str}\n耗时：{time_str}\n",
                )
            else:
                size_str = StringUtils.str_filesize(file_size)
                error_text = f"\n文件名：{target_name}\n文件大小：{size_str}\n"
                if error_msg:
                    error_text += f"错误信息：{error_msg}\n"
                post_message(
                    mtype=NotificationType.Plugin,
                    title="【115网盘】上传失败",
                    text=error_text,
                )

        if not local_path.exists():
            logger.error(f"【P115Disk】本地文件不存在: {local_path}")
            return None

        target_name = new_name or local_path.name
        target_path = Path(target_dir.path) / target_name

        # 获取目标目录ID
        target_pid = target_dir.fileid

        # 计算文件特征值
        file_size = local_path.stat().st_size
        file_sha1 = self._p115_api._calc_sha1(local_path)

        # 清理缓存
        cache_id = self._p115_api._id_cache.get_id_by_dir(target_path.as_posix())
        if cache_id:
            self._p115_api._id_cache.remove(id=cache_id)
            self._p115_api._id_item_cache.remove(id=cache_id)

        # 初始化进度条
        logger.info(f"【P115Disk】开始上传: {local_path} -> {target_path}")
        progress_callback = transfer_process(local_path.as_posix())

        try:
            wait_start_time = perf_counter()
            send_wait = False
            while True:
                start_time = perf_counter()
                # Step 1: 初始化上传
                init_resp = self._p115_api.client.upload_file_init(
                    filename=target_name,
                    filesize=file_size,
                    filesha1=file_sha1,
                    pid=target_pid,
                    read_range_bytes_or_hash=read_range_hash,
                )
                check_response(init_resp)

                logger.debug(f"【P115Disk】上传初始化结果: {init_resp}")

                if not init_resp.get("state"):
                    logger.error(
                        f"【P115Disk】初始化上传失败: {init_resp.get('error')}"
                    )
                    return None

                # 检查是否秒传成功
                if init_resp.get("reuse"):
                    logger.info(f"【P115Disk】{target_name} 秒传成功")
                    progress_callback(100)
                    end_time = perf_counter()
                    elapsed_time = end_time - start_time
                    send_upload_info(
                        file_sha1,
                        None,
                        True,
                        None,
                        str(file_size),
                        target_name,
                        int(elapsed_time),
                    )
                    send_upload_result_notify(
                        success=True,
                        target_name=target_name,
                        file_size=file_size,
                        elapsed_time=elapsed_time,
                    )
                    return self._p115_api.get_item(target_path)

                # 判断是等待秒传还是直接上传
                upload_module_skip_upload_wait_size = int(
                    configer.get_config("upload_module_skip_upload_wait_size") or 0
                )
                if (
                    upload_module_skip_upload_wait_size != 0
                    and file_size <= upload_module_skip_upload_wait_size
                ):
                    logger.info(
                        f"【P115Disk】文件大小 {file_size} 小于最低阈值，跳过等待流程: {target_name}"
                    )
                    break

                if perf_counter() - wait_start_time > int(
                    configer.get_config("upload_module_wait_timeout")
                ):
                    logger.warn(
                        f"【P115Disk】等待秒传超时，自动进行上传流程: {target_name}"
                    )
                    break

                upload_module_force_upload_wait_size = int(
                    configer.get_config("upload_module_force_upload_wait_size") or 0
                )
                if (
                    upload_module_force_upload_wait_size != 0
                    and file_size >= upload_module_force_upload_wait_size
                ):
                    logger.info(
                        f"【P115Disk】文件大小 {file_size} 大于最高阈值，强制等待流程: {target_name}"
                    )
                    sleep(int(configer.get_config("upload_module_wait_time")))
                else:
                    try:
                        response = self.oopserver_request.make_request(
                            path="/speed/user_status/me",
                            method="GET",
                            headers={"x-machine-id": configer.get_config("MACHINE_ID")},
                            timeout=10.0,
                        )

                        if response is not None and response.status_code == 200:
                            resp = response.json()
                            if resp.get("status") != "slow":
                                logger.warn(
                                    f"【P115Disk】上传速度状态 {resp.get('status')}，跳过秒传等待: {target_name}"
                                )
                                break

                            # 计算等待时间
                            default_wait_time = int(
                                configer.get_config("upload_module_wait_time")
                            )
                            sleep_time = default_wait_time
                            fastest_speed = resp.get("fastest_user_speed_mbps", None)
                            user_speed = resp.get("user_average_speed_mbps", None)
                            if fastest_speed and user_speed:
                                bs = user_speed * 0.2 + fastest_speed * 0.8
                                wt = file_size / (1024 * 1024) / bs
                                if wt > 10 * 60:
                                    wt = wt / (wt // (10 * 60) + 1)
                                if wt <= default_wait_time // 2:
                                    wt += default_wait_time // 2
                                sleep_time = int(wt)

                            logger.info(
                                f"【P115Disk】休眠 {sleep_time} 秒，等待秒传: {target_name}"
                            )
                            if not send_wait:
                                send_upload_wait(target_name)
                                send_wait = True
                            sleep(sleep_time)
                        else:
                            logger.warn("【P115Disk】获取用户上传速度错误，网络问题")
                            break
                    except Exception as e:
                        logger.warn(f"【P115Disk】获取用户上传速度错误: {e}")
                        break

            if configer.upload_module_skip_slow_upload:
                skip_upload_size = configer.get_config(
                    "upload_module_skip_slow_upload_size"
                )
                if skip_upload_size and skip_upload_size > 0:
                    if file_size >= skip_upload_size:
                        logger.warn(
                            f"【P115Disk】{target_name} 无法秒传，文件大小 {file_size} 大于等于阈值 {skip_upload_size}，跳过上传"
                        )
                        send_upload_result_notify(
                            success=False,
                            target_name=target_name,
                            file_size=file_size,
                            error_msg=f"秒传失败，文件大小 {file_size} 大于等于阈值 {skip_upload_size}，已跳过上传",
                        )
                        return None
                    else:
                        logger.info(
                            f"【P115Disk】{target_name} 无法秒传，但文件大小 {file_size} 小于阈值 {skip_upload_size}，继续执行上传"
                        )
                else:
                    logger.warn(f"【P115Disk】{target_name} 无法秒传，跳过上传")
                    send_upload_result_notify(
                        success=False,
                        target_name=target_name,
                        file_size=file_size,
                        error_msg="秒传失败，已跳过上传",
                    )
                    return None

            # 获取上传信息
            bucket_name = init_resp.get("bucket")
            object_name = init_resp.get("object")
            callback_info = init_resp.get("callback")

            if not all([bucket_name, object_name, callback_info]):
                logger.error(f"【P115Disk】上传信息不完整: {init_resp}")
                return None

            # Step 2: 获取OSS上传凭证
            (
                endpoint,
                access_key_id,
                access_key_secret,
                security_token,
                token_expiration,
            ) = self._p115_api._get_oss_token()
            logger.info(
                f"【P115Disk】OSS Token 过期时间: {token_expiration.strftime('%Y-%m-%d %H:%M:%S UTC')}"
            )

            # Step 3: OSS分片上传
            auth = StsAuth(
                access_key_id=access_key_id,
                access_key_secret=access_key_secret,
                security_token=security_token,
            )
            bucket = Bucket(auth, endpoint, bucket_name)  # noqa
            part_size = determine_part_size(file_size, preferred_size=10 * 1024 * 1024)

            logger.info(
                f"【P115Disk】开始分片上传，分片大小: {part_size // 1024 // 1024}MB"
            )

            # 初始化分片上传
            upload_id = bucket.init_multipart_upload(
                object_name, params={"encoding-type": "url", "sequential": ""}
            ).upload_id
            parts = []

            # 逐个上传分片并更新进度
            with open(local_path, "rb") as fileobj:
                part_number = 1
                offset = 0
                while offset < file_size:
                    # 检查是否取消上传
                    if global_vars.is_transfer_stopped(local_path.as_posix()):
                        logger.info(f"【P115Disk】{local_path} 上传已取消！")
                        bucket.abort_multipart_upload(object_name, upload_id)
                        return None

                    # 检查 token 是否即将过期（提前 5 分钟刷新）
                    if self._p115_api._is_token_expiring(
                        token_expiration, threshold_minutes=5
                    ):
                        logger.info("【P115Disk】Token 即将过期，正在刷新...")
                        try:
                            (
                                endpoint,
                                access_key_id,
                                access_key_secret,
                                security_token,
                                token_expiration,
                            ) = self._p115_api._get_oss_token()
                            # 重新创建认证和 bucket 对象
                            auth = StsAuth(
                                access_key_id=access_key_id,
                                access_key_secret=access_key_secret,
                                security_token=security_token,
                            )
                            bucket = Bucket(auth, endpoint, bucket_name)  # noqa
                            logger.info(
                                f"【P115Disk】Token 刷新成功，新的过期时间: "
                                f"{token_expiration.strftime('%Y-%m-%d %H:%M:%S UTC')}"
                            )
                        except Exception as e:
                            logger.error(f"【P115Disk】刷新 Token 失败: {str(e)}")
                            bucket.abort_multipart_upload(object_name, upload_id)
                            return None

                    num_to_upload = min(part_size, file_size - offset)

                    # 上传分片，带重试机制处理 token 过期错误
                    max_retries = 2
                    for retry in range(max_retries):
                        try:
                            result = bucket.upload_part(
                                object_name,
                                upload_id,
                                part_number,
                                data=SizedFileAdapter(fileobj, num_to_upload),
                            )
                            parts.append(PartInfo(part_number, result.etag))
                            break  # 上传成功，跳出重试循环
                        except ServerError as e:
                            # 检查是否是 token 过期错误
                            error_code = getattr(e, "code", "")
                            if (
                                error_code
                                in ("InvalidAccessKeyId", "SecurityTokenExpired")
                                and retry < max_retries - 1
                            ):
                                logger.warn(
                                    f"【P115Disk】检测到 Token 过期错误 ({error_code})，"
                                    f"正在刷新并重试..."
                                )
                                # 刷新 token
                                (
                                    endpoint,
                                    access_key_id,
                                    access_key_secret,
                                    security_token,
                                    token_expiration,
                                ) = self._p115_api._get_oss_token()
                                auth = StsAuth(
                                    access_key_id=access_key_id,
                                    access_key_secret=access_key_secret,
                                    security_token=security_token,
                                )
                                bucket = Bucket(auth, endpoint, bucket_name)  # noqa
                                # 需要重新定位文件指针
                                fileobj.seek(offset)
                                continue
                            else:
                                # 其他错误或重试次数用尽，放弃上传
                                logger.error(f"【P115Disk】上传分片失败: {str(e)}")
                                bucket.abort_multipart_upload(object_name, upload_id)
                                raise

                    # 更新偏移和分片号
                    offset += num_to_upload
                    part_number += 1

                    # 实时更新进度
                    progress = (offset * 100) / file_size
                    progress_callback(progress)
                    logger.debug(f"【P115Disk】上传进度: {progress:.1f}%")

            # 完成上传
            progress_callback(100)

            # Step 4: 完成OSS上传并回调115服务器
            headers = {
                "X-oss-callback": encode_callback(callback_info["callback"]),
                "x-oss-callback-var": encode_callback(callback_info["callback_var"]),
                "x-oss-forbid-overwrite": "false",
            }

            result = bucket.complete_multipart_upload(
                object_name, upload_id, parts, headers=headers
            )

            if result.status == 200:
                logger.info(f"【P115Disk】{target_name} 上传成功")
                end_time = perf_counter()
                elapsed_time = end_time - start_time
                send_upload_result_notify(
                    success=True,
                    target_name=target_name,
                    file_size=file_size,
                    elapsed_time=elapsed_time,
                )
                end_time = perf_counter()
                elapsed_time = end_time - start_time
                send_upload_info(
                    file_sha1,
                    None,
                    False,
                    None,
                    str(file_size),
                    target_name,
                    int(elapsed_time),
                )
                return self._p115_api.get_item(target_path)
            else:
                logger.error(
                    f"【P115Disk】{target_name} 上传失败，状态码: {result.status}"
                )
                send_upload_result_notify(
                    success=False,
                    target_name=target_name,
                    file_size=file_size,
                    error_msg=f"错误码: {result.status}",
                )
                return None

        except Exception as e:
            logger.error(f"【P115Disk】上传失败: {local_path} - {str(e)}")
            send_upload_result_notify(
                success=False,
                target_name=target_name,
                file_size=file_size,
                error_msg=f"未知错误: {str(e)}",
            )
            return None
