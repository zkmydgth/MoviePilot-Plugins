from concurrent.futures import ThreadPoolExecutor, Future, as_completed
from itertools import batched
from os import PathLike
from random import randint
from datetime import datetime, timezone
from pathlib import Path
from shutil import rmtree
from threading import Lock
from time import sleep, time, perf_counter
from typing import (
    Optional,
    Union,
    Literal,
    List,
    Tuple,
    Dict,
    Set,
    Any,
    Iterator,
    Iterable,
)

from httpx import Client, RequestError, HTTPStatusError
from sqlalchemy.orm.exc import MultipleResultsFound
from oss2 import StsAuth, Bucket, SizedFileAdapter, determine_part_size
from oss2.models import PartInfo
from oss2.utils import b64encode_as_string
from oss2.exceptions import OssError
from p115client import P115Client
from p115client.tool.attr import normalize_attr
from p115client.type import DirNode
from cryptography.hazmat.primitives import hashes
from diskcache import Deque

from app import schemas
from app.log import logger
from app.core.config import global_vars
from app.helper.storage import StorageHelper
from app.chain.storage import StorageChain
from app.modules.filemanager.storages import transfer_process
from app.schemas import NotificationType
from app.utils.string import StringUtils

from ..core.config import configer
from ..core.i18n import i18n
from ..core.message import post_message
from ..core.cache import idpathcacher
from ..db_manager.oper import FileDbHelper, OpenFileOper
from ..utils.oopserver import OOPServerRequest
from ..utils.sentry import sentry_manager
from ..utils.exception import U115NoCheckInException, CanNotFindPathToCid
from ..utils.path import PathUtils
from ..utils.limiter import RateLimiter


p115_open_lock = Lock()


@sentry_manager.capture_all_class_exceptions
class U115OpenHelper:
    """
    115 Open Api
    """

    _auth_state = {}

    base_url = "https://proapi.115.com"

    chunk_size = 10 * 1024 * 1024

    retry_delay = 70

    _get_download_url_limiter = RateLimiter(qps=1.0)

    def __init__(self):
        super().__init__()
        self.session = Client(follow_redirects=True, timeout=20.0)
        self._init_session()

        self.fail_upload_count = 0

        self.oopserver_request = OOPServerRequest(max_retries=3, backoff_factor=1.0)
        self.databasehelper = FileDbHelper()
        self.cookie_client = P115Client(configer.cookies)

    def _init_session(self):
        """
        初始化
        """
        self.session.headers.update(
            {
                "User-Agent": "W115Storage/2.0",
                "Accept-Encoding": "gzip, deflate",
                "Content-Type": "application/x-www-form-urlencoded",
            }
        )

    def _check_session(self):
        """
        检查会话是否过期
        """
        if not self.access_token:
            raise U115NoCheckInException("【P115Open】请先扫码登录！")

    @property
    def access_token(self) -> Optional[str]:
        """
        访问 token
        """
        with p115_open_lock:
            try:
                storagehelper = StorageHelper()
                u115_info = storagehelper.get_storage(storage="u115")
                if not u115_info:
                    return None
                tokens = u115_info.config
                refresh_token = tokens.get("refresh_token")
                if not refresh_token:
                    return None
                expires_in = tokens.get("expires_in", 0)
                refresh_time = tokens.get("refresh_time", 0)
                if expires_in and refresh_time + expires_in < int(time()):
                    tokens = self.__refresh_access_token(refresh_token)
                    if tokens:
                        storagehelper.set_storage(
                            storage="u115",
                            conf={"refresh_time": int(time()), **tokens},
                        )
                    else:
                        return None
                access_token = tokens.get("access_token")
                if access_token:
                    self.session.headers.update(
                        {"Authorization": f"Bearer {access_token}"}
                    )
                return access_token
            except Exception as e:
                logger.error(f"【P115Open】获取访问 Token 出现未知错误: {e}")
                return None

    def __refresh_access_token(self, refresh_token: str) -> Optional[dict]:
        """
        刷新 access_token

        :param refresh_token: 刷新令牌

        :return: 数据字典
        """
        resp = self.session.post(
            "https://passportapi.115.com/open/refreshToken",
            data={"refresh_token": refresh_token},
        )
        if resp is None:
            logger.error(
                f"【P115Open】刷新 access_token 失败：refresh_token={refresh_token}"
            )
            return None
        result = resp.json()
        if result.get("code") != 0:
            logger.warn(
                f"【P115Open】刷新 access_token 失败：{result.get('code')} - {result.get('message')}！"
            )
            return None
        return result.get("data")

    def _request_api(
        self,
        method: str,
        endpoint: str,
        result_key: Optional[str] = None,
        **kwargs,
    ) -> Optional[Union[dict, list]]:
        """
        API 请求函数
        """
        # 检查会话
        self._check_session()

        # 错误日志标志
        no_error_log = kwargs.pop("no_error_log", False)
        # 重试次数
        retry_times = kwargs.pop("retry_limit", 5)

        try:
            resp = self.session.request(method, f"{self.base_url}{endpoint}", **kwargs)
        except RequestError as e:
            logger.error(f"【P115Open】{method} 请求 {endpoint} 网络错误: {str(e)}")
            return None

        if resp is None:
            logger.warn(f"【P115Open】{method} 请求 {endpoint} 失败！")
            return None

        kwargs["retry_limit"] = retry_times

        # 处理速率限制
        if resp.status_code == 429:
            reset_time = 5 + int(resp.headers.get("X-RateLimit-Reset", 60))
            logger.debug(
                f"【P115Open】{method} 请求 {endpoint} 限流，等待{reset_time}秒后重试"
            )
            sleep(reset_time)
            return self._request_api(method, endpoint, result_key, **kwargs)

        # 处理请求错误
        try:
            resp.raise_for_status()
        except HTTPStatusError as e:
            if retry_times <= 0:
                logger.error(
                    f"【P115Open】{method} 请求 {endpoint} 错误 {e}，重试次数用尽！"
                )
                return None
            kwargs["retry_limit"] = retry_times - 1
            sleep_duration = 2 ** (5 - retry_times + 1)
            logger.info(
                f"【P115Open】{method} 请求 {endpoint} 错误 {e}，等待 {sleep_duration} 秒后重试..."
            )
            sleep(sleep_duration)
            return self._request_api(method, endpoint, result_key, **kwargs)

        # 返回数据
        ret_data = resp.json()
        if ret_data.get("code") != 0:
            error_msg = ret_data.get("message")
            if not no_error_log:
                logger.warn(f"【P115Open】{method} 请求 {endpoint} 出错：{error_msg}")
            if "已达到当前访问上限" in error_msg:
                if retry_times <= 0:
                    logger.error(
                        f"【P115Open】{method} 请求 {endpoint} 达到访问上限，重试次数用尽！"
                    )
                    return None
                kwargs["retry_limit"] = retry_times - 1
                logger.info(
                    f"【P115Open】{method} 请求 {endpoint} 达到访问上限，等待 {self.retry_delay} 秒后重试..."
                )
                sleep(self.retry_delay)
                return self._request_api(method, endpoint, result_key, **kwargs)
            return None

        if result_key:
            return ret_data.get(result_key)
        return ret_data

    @staticmethod
    def _delay_get_item(path: Path) -> Optional[schemas.FileItem]:
        """
        自动延迟重试 get_item 模块

        :param path: 路径
        """
        storage_chain = StorageChain()
        for i in range(1, 4):
            sleep(2**i)
            file_item = storage_chain.get_file_item(storage="u115", path=Path(path))
            if file_item:
                return file_item
        return None

    def get_download_url(
        self,
        pickcode: str,
        user_agent: str,
    ) -> Optional[str]:
        """
        获取下载链接

        :param pickcode: 提取码
        :param user_agent: 请求 UA 头
        """
        self._get_download_url_limiter.acquire()
        download_info = self._request_api(
            "POST",
            "/open/ufile/downurl",
            "data",
            data={"pick_code": pickcode},
            headers={"User-Agent": user_agent},
        )
        if not download_info:
            return None
        logger.debug(f"【P115Open】获取到下载信息: {download_info}")
        try:
            return list(download_info.values())[0].get("url", {}).get("url")
        except Exception as e:
            logger.error(f"【P115Open】解析下载链接失败: {e}")
            return None

    @staticmethod
    def _calc_sha1(filepath: Path, size: Optional[int] = None) -> str:
        """
        计算文件 SHA1

        :param filepath: 文件路径
        :param size: 前多少字节
        """
        sha1 = hashes.Hash(hashes.SHA1())
        with open(filepath, "rb") as f:
            if size:
                chunk = f.read(size)
                sha1.update(chunk)
            else:
                while chunk := f.read(8192):
                    sha1.update(chunk)
        return sha1.finalize().hex()

    @staticmethod
    def _can_write_db(path: Path) -> bool:
        """
        判断目录是否能写入数据库

        :param path: 路径
        """
        # 存在待整理目录时，判断非待整理目录才写入，不存在待整理目录直接写入数据库
        if configer.pan_transfer_paths:
            if not PathUtils.get_run_transfer_path(
                configer.pan_transfer_paths, path.as_posix()
            ):
                return True
        else:
            return True
        return False

    def _upload_fail_count(self) -> bool:
        """
        上传重试判断
        """
        if self.fail_upload_count == 0:
            self.fail_upload_count += 1
            return True
        else:
            self.fail_upload_count = 0
            return False

    def upload(
        self,
        target_dir: schemas.FileItem,
        local_path: Path,
        new_name: Optional[str] = None,
    ) -> Optional[schemas.FileItem]:
        """
        文件上传
        """

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
                        f"【P115Open】上传信息报告服务器成功: {response.json()}"
                    )
                else:
                    logger.warn("【P115Open】上传信息报告服务器失败，网络问题")
            except Exception as e:
                logger.warn(f"【P115Open】上传信息报告服务器失败: {e}")

        def send_upload_wait(target_name):
            """
            发送上传等待
            """
            if configer.notify and configer.upload_module_notify:
                post_message(
                    mtype=NotificationType.Plugin,
                    title=i18n.translate("upload_module_title"),
                    text=f"\n{i18n.translate('upload_wait_text', name=target_name)}\n",
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
                    title=i18n.translate("upload_success_title"),
                    text=f"\n{i18n.translate('upload_success_text', name=target_name, size=size_str, time=time_str)}\n",
                )
            else:
                size_str = StringUtils.str_filesize(file_size)
                error_text = f"\n{i18n.translate('upload_fail_text', name=target_name, size=size_str)}\n"
                if error_msg:
                    error_text += f"{i18n.translate('upload_fail_error', error=error_msg)}\n"
                post_message(
                    mtype=NotificationType.Plugin,
                    title=i18n.translate("upload_fail_title"),
                    text=error_text,
                )

        target_name = new_name or local_path.name
        target_path = Path(target_dir.path) / target_name
        # 计算文件特征值
        file_size = local_path.stat().st_size
        file_sha1 = self._calc_sha1(local_path)
        file_preid = self._calc_sha1(local_path, 128 * 1024 * 1024)

        # 获取目标目录CID
        target_cid = target_dir.fileid
        target_param = f"U_1_{target_cid}"

        wait_start_time = perf_counter()
        send_wait = False
        while True:
            start_time = perf_counter()
            # Step 1: 初始化上传
            init_data = {
                "file_name": target_name,
                "file_size": file_size,
                "target": target_param,
                "fileid": file_sha1,
                "preid": file_preid,
            }
            init_resp = self._request_api(
                "POST", "/open/upload/init", data=init_data, timeout=120.0
            )
            if not init_resp:
                return None
            if not init_resp.get("state"):
                logger.warn(f"【P115Open】初始化上传失败: {init_resp.get('error')}")
                return None
            # 结果
            init_result = init_resp.get("data")
            logger.debug(f"【P115Open】上传 Step 1 初始化结果: {init_result}")
            # 回调信息
            bucket_name = init_result.get("bucket")
            object_name = init_result.get("object")
            callback = init_result.get("callback")
            # 二次认证信息
            sign_check = init_result.get("sign_check")
            pick_code = init_result.get("pick_code")
            sign_key = init_result.get("sign_key")

            # Step 2: 处理二次认证
            second_auth = False
            second_sha1 = ""
            if init_result.get("code") in [700, 701] and sign_check:
                second_auth = True
                sign_checks = sign_check.split("-")
                start = int(sign_checks[0])
                end = int(sign_checks[1])
                # 计算指定区间的SHA1
                # sign_check （用下划线隔开,截取上传文内容的sha1）(单位是byte): "2392148-2392298"
                with open(local_path, "rb") as f:
                    # 取2392148-2392298之间的内容(包含2392148、2392298)的sha1
                    f.seek(start)
                    chunk = f.read(end - start + 1)
                    sha1 = hashes.Hash(hashes.SHA1())
                    sha1.update(chunk)
                    sign_val = sha1.finalize().hex().upper()
                second_sha1 = sign_val
                # 重新初始化请求
                # sign_key，sign_val(根据sign_check计算的值大写的sha1值)
                init_data.update(
                    {"pick_code": pick_code, "sign_key": sign_key, "sign_val": sign_val}
                )
                init_resp = self._request_api(
                    "POST", "/open/upload/init", data=init_data, timeout=120.0
                )
                if not init_resp:
                    return None
                if not init_resp.get("state"):
                    logger.warn(
                        f"【P115Open】处理二次认证失败: {init_resp.get('error')}"
                    )
                    return None
                # 二次认证结果
                init_result = init_resp.get("data")
                logger.debug(f"【P115Open】上传 Step 2 二次认证结果: {init_result}")
                if not pick_code:
                    pick_code = init_result.get("pick_code")
                if not bucket_name:
                    bucket_name = init_result.get("bucket")
                if not object_name:
                    object_name = init_result.get("object")
                if not callback:
                    callback = init_result.get("callback")

            # Step 3: 秒传
            if init_result.get("status") == 2:
                logger.info(f"【P115Open】{target_name} 秒传成功")
                end_time = perf_counter()
                elapsed_time = end_time - start_time
                send_upload_info(
                    file_sha1,
                    file_preid,
                    second_auth,
                    second_sha1,
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
                file_id = init_result.get("file_id", None)
                if file_id:
                    logger.debug(
                        f"【P115Open】{target_name} 使用秒传返回ID获取文件信息"
                    )
                    sleep(2)
                    info_resp = self._request_api(
                        "GET",
                        "/open/folder/get_info",
                        "data",
                        params={"file_id": int(file_id)},
                        timeout=120.0,
                    )
                    if info_resp:
                        try:
                            return schemas.FileItem(
                                storage="u115",
                                fileid=str(info_resp["file_id"]),
                                path=str(target_path)
                                + ("/" if info_resp["file_category"] == "0" else ""),
                                type="file"
                                if info_resp["file_category"] == "1"
                                else "dir",
                                name=info_resp["file_name"],
                                basename=Path(info_resp["file_name"]).stem,
                                extension=Path(info_resp["file_name"])
                                .suffix[1:]
                                .lower()
                                if info_resp["file_category"] == "1"
                                else None,
                                pickcode=info_resp["pick_code"],
                                size=StringUtils.num_filesize(info_resp["size"])
                                if info_resp["file_category"] == "1"
                                else None,
                                modify_time=info_resp["utime"],
                            )
                        except Exception as e:
                            logger.error(f"【P115Open】处理返回信息失败: {e}")
                            return None
                return U115OpenHelper._delay_get_item(target_path)

            # 判断是等待秒传还是直接上传
            upload_module_skip_upload_wait_size = int(
                configer.get_config("upload_module_skip_upload_wait_size") or 0
            )
            if (
                upload_module_skip_upload_wait_size != 0
                and file_size <= upload_module_skip_upload_wait_size
            ):
                logger.info(
                    f"【P115Open】文件大小 {file_size} 小于最低阈值，跳过等待流程: {target_name}"
                )
                break

            if perf_counter() - wait_start_time > int(
                configer.get_config("upload_module_wait_timeout")
            ):
                logger.warn(
                    f"【P115Open】等待秒传超时，自动进行上传流程: {target_name}"
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
                    f"【P115Open】文件大小 {file_size} 大于最高阈值，强制等待流程: {target_name}"
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
                                f"【P115Open】上传速度状态 {resp.get('status')}，跳过秒传等待: {target_name}"
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
                            f"【P115Open】休眠 {sleep_time} 秒，等待秒传: {target_name}"
                        )
                        if not send_wait:
                            send_upload_wait(target_name)
                            send_wait = True
                        sleep(sleep_time)
                    else:
                        logger.warn("【P115Open】获取用户上传速度错误，网络问题")
                        break
                except Exception as e:
                    logger.warn(f"【P115Open】获取用户上传速度错误: {e}")
                    break

        if configer.upload_module_skip_slow_upload:
            skip_upload_size = configer.get_config(
                "upload_module_skip_slow_upload_size"
            )
            if skip_upload_size and skip_upload_size > 0:
                if file_size >= skip_upload_size:
                    logger.warn(
                        f"【P115Open】{target_name} 无法秒传，文件大小 {file_size} 大于等于阈值 {skip_upload_size}，跳过上传"
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
                        f"【P115Open】{target_name} 无法秒传，但文件大小 {file_size} 小于阈值 {skip_upload_size}，继续执行上传"
                    )
            else:
                logger.warn(f"【P115Open】{target_name} 无法秒传，跳过上传")
                send_upload_result_notify(
                    success=False,
                    target_name=target_name,
                    file_size=file_size,
                    error_msg="秒传失败，已跳过上传",
                )
                return None

        # Step 4: 获取上传凭证
        second_auth = False
        token_resp = self._request_api(
            "GET", "/open/upload/get_token", "data", timeout=120.0
        )
        if not token_resp:
            logger.warn("【P115Open】获取上传凭证失败")
            return None
        logger.debug(f"【P115Open】上传 Step 4 获取上传凭证结果: {token_resp}")
        # 上传凭证
        endpoint = token_resp.get("endpoint")
        access_key_id = token_resp.get("AccessKeyId")
        access_key_secret = token_resp.get("AccessKeySecret")
        security_token = token_resp.get("SecurityToken")

        # Step 5: 断点续传
        resume_resp = self._request_api(
            "POST",
            "/open/upload/resume",
            "data",
            data={
                "file_size": file_size,
                "target": target_param,
                "fileid": file_sha1,
                "pick_code": pick_code,
            },
            timeout=120.0,
        )
        if resume_resp:
            logger.debug(f"【P115Open】上传 Step 5 断点续传结果: {resume_resp}")
            if resume_resp.get("callback"):
                callback = resume_resp["callback"]

        # Step 6: 对象存储上传
        auth = StsAuth(
            access_key_id=access_key_id,
            access_key_secret=access_key_secret,
            security_token=security_token,
        )
        bucket = Bucket(auth, endpoint, bucket_name, connect_timeout=120)  # noqa
        part_size = determine_part_size(file_size, preferred_size=self.chunk_size)

        # 初始化进度条
        logger.info(
            f"【P115Open】开始上传: {local_path} -> {target_path}，分片大小：{StringUtils.str_filesize(part_size)}"
        )
        progress_callback = transfer_process(local_path.as_posix())

        try:
            # 初始化分片
            upload_id = None
            for attempt in range(3):
                try:
                    upload_id = bucket.init_multipart_upload(
                        object_name, params={"encoding-type": "url", "sequential": ""}
                    ).upload_id
                    break
                except Exception as e:
                    logger.warn(
                        f"【P115Open】初始化分片上传失败: {e}，正在重试... ({attempt + 1}/3)"
                    )
                    sleep(2**attempt)

            if not upload_id:
                logger.error(
                    f"【P115Open】{target_name} 初始化分片上传最终失败，上传终止。"
                )
                return None

            parts = []
            # 逐个上传分片
            with open(local_path, "rb") as fileobj:
                part_number = 1
                offset = 0
                while offset < file_size:
                    if global_vars.is_transfer_stopped(local_path.as_posix()):
                        logger.info(f"【P115Open】{local_path} 上传已取消！")
                        return None
                    num_to_upload = min(part_size, file_size - offset)
                    for attempt in range(3):
                        try:
                            # 每次重试时，都需要将文件指针移到当前分片的起始位置
                            fileobj.seek(offset)

                            logger.info(
                                f"【P115Open】开始上传 {target_name} 分片 {part_number}: {offset} -> {offset + num_to_upload}"
                            )
                            result = bucket.upload_part(
                                object_name,
                                upload_id,
                                part_number,
                                data=SizedFileAdapter(fileobj, num_to_upload),
                            )
                            parts.append(PartInfo(part_number, result.etag))
                            logger.info(
                                f"【P115Open】{target_name} 分片 {part_number} 上传完成"
                            )
                            break
                        except OssError as e:
                            # 判断是否为STS Token过期错误
                            if e.code == "SecurityTokenExpired":
                                logger.warn(
                                    f"【P115Open】上传凭证已过期，正在重新获取... (重试次数: {attempt + 1}/3)"
                                )
                                # Step 4: 重新获取上传凭证
                                token_resp = self._request_api(
                                    "GET",
                                    "/open/upload/get_token",
                                    "data",
                                    timeout=120.0,
                                )
                                if not token_resp:
                                    logger.error(
                                        "【P115Open】重新获取上传凭证失败，上传终止。"
                                    )
                                    return None

                                # 更新OSS客户端的认证信息
                                access_key_id = token_resp.get("AccessKeyId")
                                access_key_secret = token_resp.get("AccessKeySecret")
                                security_token = token_resp.get("SecurityToken")
                                auth = StsAuth(
                                    access_key_id=access_key_id,
                                    access_key_secret=access_key_secret,
                                    security_token=security_token,
                                )
                                bucket = Bucket(
                                    auth,  # noqa
                                    endpoint,
                                    bucket_name,
                                    connect_timeout=120,
                                )
                                logger.info(
                                    "【P115Open】上传凭证已刷新，将重试当前分片。"
                                )
                                continue
                            logger.warn(
                                f"【P115Open】上传分片 {part_number} 失败: {e}，正在重试... ({attempt + 1}/3)"
                            )
                            sleep(2**attempt)
                        except Exception as e:
                            logger.warn(
                                f"【P115Open】上传分片 {part_number} 发生未知错误: {e}，正在重试... ({attempt + 1}/3)"
                            )
                            sleep(2**attempt)
                    else:
                        logger.error(
                            f"【P115Open】{target_name} 分片 {part_number} 达到最大重试次数，上传终止。"
                        )
                        return None

                    offset += num_to_upload
                    part_number += 1
                    # 更新进度
                    progress = (offset * 100) / file_size
                    progress_callback(progress)
        except Exception as e:
            logger.error(f"【P115Open】{target_name} 分块生成出现未知错误: {e}")
            return None
        else:
            # 完成上传
            progress_callback(100)

        # 请求头
        headers = {
            "X-oss-callback": encode_callback(callback["callback"]),
            "x-oss-callback-var": encode_callback(callback["callback_var"]),
            "x-oss-forbid-overwrite": "false",
        }
        try:
            result = bucket.complete_multipart_upload(
                object_name, upload_id, parts, headers=headers
            )
            if result.status == 200:
                try:
                    data = result.resp.response.json()
                    logger.debug(f"【P115Open】上传 Step 6 回调结果：{data}")
                    if data.get("state") is False:
                        if self._upload_fail_count():
                            logger.warn(f"【P115Open】{target_name} 上传重试")
                            return self.upload(target_dir, local_path, new_name)
                        error_msg = data.get("error", "上传失败")
                        logger.error(f"【P115Open】{target_name} 上传失败")
                        send_upload_result_notify(
                            success=False,
                            target_name=target_name,
                            file_size=file_size,
                            error_msg=error_msg,
                        )
                        return None
                except Exception:
                    logger.warn("【P115Open】上传 Step 6 回调无结果")
                logger.info(f"【P115Open】{target_name} 上传成功")
                end_time = perf_counter()
                elapsed_time = end_time - start_time
                send_upload_result_notify(
                    success=True,
                    target_name=target_name,
                    file_size=file_size,
                    elapsed_time=elapsed_time,
                )
            else:
                error_msg = f"错误码: {result.status}"
                logger.warn(
                    f"【P115Open】{target_name} 上传失败，错误码: {result.status}"
                )
                send_upload_result_notify(
                    success=False,
                    target_name=target_name,
                    file_size=file_size,
                    error_msg=error_msg,
                )
                return None
        except OssError as e:
            if e.code == "InvalidAccessKeyId":
                logger.warn(
                    f"【P115Open】上传凭证失效，将重新获取凭证并继续上传: {target_name}"
                )
                return self.upload(target_dir, local_path, new_name)

            if e.code == "FileAlreadyExists":
                logger.warn(f"【P115Open】{target_name} 已存在")
            else:
                error_msg = f"错误码: {e.code}, 详情: {e.message}"
                logger.error(
                    f"【P115Open】{target_name} 上传失败: {e.status}, 错误码: {e.code}, 详情: {e.message}"
                )
                send_upload_result_notify(
                    success=False,
                    target_name=target_name,
                    file_size=file_size,
                    error_msg=error_msg,
                )
                return None
        except Exception as e:
            error_msg = f"未知错误: {str(e)}"
            logger.error(f"【P115Open】{target_name} 回调出现未知错误: {e}")
            send_upload_result_notify(
                success=False,
                target_name=target_name,
                file_size=file_size,
                error_msg=error_msg,
            )
            return None

        end_time = perf_counter()
        elapsed_time = end_time - start_time
        send_upload_info(
            file_sha1,
            file_preid,
            second_auth,
            second_sha1,
            str(file_size),
            target_name,
            int(elapsed_time),
        )
        # 返回结果
        return U115OpenHelper._delay_get_item(target_path)

    def create_folder(
        self, parent_item: schemas.FileItem, name: str
    ) -> Optional[schemas.FileItem]:
        """
        创建目录

        Cookie / OpenAPI 随机轮换
        """
        new_path = Path(parent_item.path) / name

        if randint(0, 1) == 0:
            resp = self._request_api(
                "POST",
                "/open/folder/add",
                data={"pid": int(parent_item.fileid or "0"), "file_name": name},
            )
            if not resp:
                return None
            if not resp.get("state"):
                if resp.get("code") == 20004:
                    # 目录已存在
                    return self.get_item(new_path)
                logger.warn(f"【P115Open】创建目录失败: {resp.get('error')}")
                return None
            logger.debug(f"【P115Open】OpenAPI 创建目录: {new_path}")
            try:
                return schemas.FileItem(
                    storage="u115",
                    fileid=str(resp["data"]["file_id"]),
                    path=new_path.as_posix() + "/",
                    name=name,
                    basename=name,
                    type="dir",
                    modify_time=int(time()),
                )
            except Exception as e:
                logger.error(f"【P115Open】处理返回信息失败: {e}")
                return None
        else:
            resp = self.cookie_client.fs_mkdir(name, pid=int(parent_item.fileid or "0"))
            if not resp.get("state"):
                if resp.get("errno") == 20004:
                    return self.get_item(new_path)
                logger.warn(f"【P115Open】创建目录失败: {resp}")
                return None
            logger.debug(f"【P115Open】Cookie 创建目录: {new_path}")
            try:
                return schemas.FileItem(
                    storage="u115",
                    fileid=str(resp["cid"]),
                    path=new_path.as_posix() + "/",
                    name=name,
                    basename=name,
                    type="dir",
                    modify_time=int(time()),
                )
            except Exception as e:
                logger.error(f"【P115Open】处理返回信息失败: {e}")
                return None

    def open_get_item(self, path: Path) -> Optional[schemas.FileItem]:
        """
        获取指定路径的文件/目录项 OpenAPI
        """
        try:
            resp = self._request_api(
                "POST",
                "/open/folder/get_info",
                "data",
                data={"path": path.as_posix()},
                no_error_log=True,
            )
            if not resp:
                return None
            logger.debug(f"【P115Open】OpenAPI 获取文件信息 {path} {resp['file_id']}")
            file_item = schemas.FileItem(
                storage="u115",
                fileid=str(resp["file_id"]),
                path=path.as_posix() + ("/" if resp["file_category"] == "0" else ""),
                type="file" if resp["file_category"] == "1" else "dir",
                name=resp["file_name"],
                basename=Path(resp["file_name"]).stem,
                extension=Path(resp["file_name"]).suffix[1:]
                if resp["file_category"] == "1"
                else None,
                pickcode=resp["pick_code"],
                size=resp["size_byte"] if resp["file_category"] == "1" else None,
                modify_time=resp["utime"],
            )
            if self._can_write_db(path):
                self.databasehelper.upsert_batch(
                    self.databasehelper.process_fileitem(file_item)
                )
            return file_item
        except Exception as e:
            logger.debug(f"【P115Open】OpenAPI 获取文件信息失败: {str(e)}")
            return None

    def database_get_item(self, path: Path) -> Optional[schemas.FileItem]:
        """
        获取指定路径的文件/目录项 DataBase
        """
        try:
            try:
                data = self.databasehelper.get_by_path(path=path.as_posix())
            except MultipleResultsFound:
                return None
            if data:
                if data.get("id", None):
                    logger.debug(
                        f"【P115Open】DataBase 获取文件信息 {path} {data.get('id')}"
                    )
                    return schemas.FileItem(
                        storage="u115",
                        fileid=str(data.get("id")),
                        path=path.as_posix()
                        + ("/" if data.get("type") == "folder" else ""),
                        type="file" if data.get("type") == "file" else "dir",
                        name=data.get("name"),
                        basename=Path(data.get("name")).stem,
                        extension=Path(data.get("name")).suffix[1:]
                        if data.get("type") == "file"
                        else None,
                        pickcode=data.get("pickcode", ""),
                        size=data.get("size") if data.get("type") == "file" else None,
                        modify_time=data.get("mtime", 0),
                    )
            return None
        except Exception as e:
            logger.debug(f"【P115Open】DataBase 获取文件信息失败: {str(e)}")
            return None

    def cookie_get_item(self, path: Path) -> Optional[schemas.FileItem]:
        """
        获取指定路径的目录项 Cookie

        1. 缓存读取 ｜ Cookie 接口获取目录 ID
        2. 获取文件详细信息
        """
        try:
            if path.name != path.stem:
                return None
            if path.as_posix() == "/":
                folder_id = 0
            else:
                cache_id = idpathcacher.get_id_by_dir(directory=path.as_posix())
                if cache_id:
                    folder_id = cache_id
                else:
                    payload = {
                        "path": path.as_posix(),
                    }
                    resp = self.cookie_client.fs_dir_getid(payload)
                    if not resp.get("state", None):
                        return None
                    folder_id = resp.get("id", None)
                    if not folder_id or folder_id == 0:
                        return None
            sleep(1)
            resp = self.cookie_client.fs_file(folder_id)
            if not resp.get("state", None):
                return None
            data: list = resp.get("data", [])
            if not data:
                return None
            data: dict = data[0]
            data["path"] = path.as_posix()
            if self._can_write_db(path):
                self.databasehelper.upsert_batch(
                    self.databasehelper.process_fs_files_item(data)
                )
            logger.debug(f"【P115Open】Cookie 获取文件信息 {path} {data.get('cid')}")
            return schemas.FileItem(
                storage="u115",
                fileid=str(data.get("cid")),
                path=path.as_posix() + "/",
                type="dir",
                name=data.get("n"),
                basename=Path(data.get("n")).stem,
                extension=None,
                pickcode=data.get("pc"),
                size=None,
                modify_time=data.get("t"),
            )
        except Exception as e:
            logger.debug(f"【P115Open】Cookie 获取文件夹信息失败: {str(e)}")
            return None

    def get_item(self, path: Path) -> Optional[schemas.FileItem]:
        """
        获取指定路径的文件/目录项

        1. 数据库获取
        2. OpenAPI 接口获取
        """
        db_item = self.database_get_item(path)
        if db_item:
            return db_item
        return self.open_get_item(path)

    def get_folder(self, path: Path) -> Optional[schemas.FileItem]:
        """
        获取指定路径的文件夹，如不存在则创建
        """
        folder = self.get_item(path)
        if folder:
            return folder

        try:
            resp = self.cookie_client.fs_makedirs_app(
                path.as_posix(), pid=0, **configer.get_ios_ua_app()
            )
            if not resp.get("state"):
                logger.error(f"【P115Open】{path} 目录创建失败：{resp}")
                return None
            idpathcacher.add_cache(id=int(resp["cid"]), directory=path.as_posix())
            return self.cookie_get_item(path)
        except Exception as e:
            logger.error(f"【P115Open】{path} 目录创建出现未知错误：{e}")
            return None

    def rename(self, fileitem: schemas.FileItem, name: str) -> bool:
        """
        重命名文件/目录

        Cookie / OpenAPI 随机轮换
        """
        r = randint(0, 2)
        if r == 0:
            resp = self._request_api(
                "POST",
                "/open/ufile/update",
                data={"file_id": int(fileitem.fileid), "file_name": name},
            )
        elif r == 1:
            resp = self.cookie_client.fs_rename((int(fileitem.fileid), name))
        else:
            resp = self.cookie_client.fs_rename_app(
                (int(fileitem.fileid), name), **configer.get_ios_ua_app()
            )
        if not resp:
            return False
        if resp.get("state"):
            return True
        return False

    def recyclebin_clean(self, payload: int | str | Iterable[int | str] | dict = "", /):
        """
        清理回收站

        .. note::
            只要不指定 `tid`，就会清空回收站

        :payload:
            - tid: int | str = "" 💡 多个用逗号 "," 隔开
        """
        if isinstance(payload, (int, str)):
            payload = {"tid": payload}
        elif not isinstance(payload, dict):
            payload = {"tid": ",".join(map(str, payload))}
        return self._request_api(
            "POST",
            "/open/rb/del",
            data=payload,
        )

    def get_item_info(self, payload, /):
        """
        获取文件/文件夹信息

        :param payload: 路径 或 ID
        """
        if isinstance(payload, int):
            resp = self._request_api(
                "GET",
                "/open/folder/get_info",
                "data",
                params={"file_id": payload},
            )
        else:
            resp = self._request_api(
                "POST",
                "/open/folder/get_info",
                "data",
                data={"path": payload},
            )
        path = ""
        paths: List[Dict] = resp.get("paths")
        if len(paths) > 1:
            for p in paths[1:]:
                path += f"/{p.get('file_name')}"
        path += f"/{resp.get('file_name')}"
        resp["path"] = path
        return resp

    def iter_files_with_path_simple(
        self,
        path: str | int | Path | PathLike,
        /,
        qps: int = 5,
        page_size: int = 1150,
        max_workers: int = 10,
        type: Optional[int] = None,
        suffix: Optional[str] = None,
        asc: int = 1,
        order: Literal[
            "file_name", "file_size", "user_utime", "file_type"
        ] = "file_name",
    ) -> Iterator[Dict]:
        """
        遍历目录树迭代文件信息对象

        .. attention::
            此方法具有局限性
            网盘目录格式必须是文件层级前都是文件夹，且文件层级内不包含文件夹
            适用于标准电影库，音乐库等

        :param path: 迭代目录（可选目录路径，cid）
        :param qps: 每秒请求数
        :param page_size: 分页大小
        :param max_workers: 最大工作线程数
        :param type: 文件类型；1.文档；2.图片；3.音乐；4.视频；5.压缩；6.应用；7.书籍
        :param suffix: 匹配文件后缀名
        :param asc: 升序排列。0: 否，1: 是
        :param order: 排序

            - "file_name": 文件名
            - "file_size": 文件大小
            - "file_type": 文件种类
            - "user_utime": 修改时间

        :return: 迭代器，文件或文件夹信息
        """
        rate_limiter = RateLimiter(qps)
        dir_nodes: Dict[int, DirNode] = {}
        need_parent_id_set: Set[int] = set()
        files_info_path = configer.PLUGIN_TEMP_PATH / "u115_iter_files_simple"
        if files_info_path.exists():
            rmtree(files_info_path)
        files_info: Deque = Deque(directory=files_info_path)

        def _job(
            cid: int, offset: int, show_dir: bool | None
        ) -> List[Tuple[int, str, int]]:
            rate_limiter.acquire()
            payload = {
                "cid": cid,
                "limit": page_size,
                "o": order,
                "asc": asc,
                "type": type,
                "suffix": suffix,
                "offset": offset,
            }
            if show_dir is not None:
                payload["show_dir"] = 1 if show_dir else 0
            resp = self._request_api(
                "GET",
                "/open/ufile/files",
                params=payload,
            )
            items = resp.get("data", [])
            count = resp.get("count", 0)
            sub_dirs_to_scan = []
            pre_sub_dirs_to_scan = []
            for attr in items:
                attr = normalize_attr(attr)
                files_info.append(attr)
                if attr.get("is_dir"):
                    dir_nodes[attr["id"]] = DirNode(
                        name=attr["name"], parent_id=attr["parent_id"]
                    )
                    if attr["id"] not in need_parent_id_set:
                        pre_sub_dirs_to_scan.append(attr["id"])
                    else:
                        try:
                            need_parent_id_set.remove(attr["id"])
                        except KeyError:
                            pass
                if show_dir is None:
                    need_parent_id_set.add(attr["parent_id"])
            if need_parent_id_set and pre_sub_dirs_to_scan:
                for _id in pre_sub_dirs_to_scan:
                    sub_dirs_to_scan.append((_id, 0, True))
            if show_dir is None:
                if (offset // page_size) % 5 == 0:
                    for i in range(1, 6):
                        if offset + page_size * i < count:
                            sub_dirs_to_scan.append(
                                (cid, offset + page_size * i, show_dir)
                            )
            else:
                new_offset = offset + len(items)
                if new_offset < count and len(items) > 0:
                    sub_dirs_to_scan.append((cid, new_offset, show_dir))
            return sub_dirs_to_scan

        if isinstance(path, (Path, PathLike)):
            storage_chain = StorageChain()
            file_item = storage_chain.get_file_item(storage="u115", path=Path(path))
            if not file_item:
                raise CanNotFindPathToCid("无法获取目录信息")
            path = file_item.fileid
        initial_cid = int(path)
        full_path = ""
        if initial_cid != 0:
            resp = self._request_api(
                "GET",
                "/open/folder/get_info",
                params={"file_id": initial_cid},
            )
            if not resp:
                raise OSError("获取目录信息失败")
            data: Dict = resp.get("data")
            paths: List[Dict] = data.get("paths")
            if len(paths) > 1:
                for p in paths[1:]:
                    full_path += f"/{p.get('file_name')}"
            full_path += f"/{data.get('file_name')}"
        if page_size <= 0 or page_size > 1_150:
            page_size = 1_150

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            pending_futures: Set[Future] = set()
            # 文件列表拉取
            initial_future = executor.submit(_job, initial_cid, 0, None)
            pending_futures.add(initial_future)
            while pending_futures:
                for future in as_completed(pending_futures):
                    pending_futures.remove(future)
                    try:
                        sub_dirs = future.result()
                        for task_args in sub_dirs:
                            new_future = executor.submit(_job, *task_args)
                            pending_futures.add(new_future)
                    except Exception as e:
                        for f in pending_futures:
                            f.cancel()
                        executor.shutdown(wait=False, cancel_futures=True)
                        raise e
                    break
            # 文件夹列表拉取
            try:
                need_parent_id_set.remove(initial_cid)
            except KeyError:
                pass
            initial_future = executor.submit(_job, initial_cid, 0, True)
            pending_futures.add(initial_future)
            while pending_futures:
                for future in as_completed(pending_futures):
                    pending_futures.remove(future)
                    try:
                        sub_dirs = future.result()
                        for task_args in sub_dirs:
                            new_future = executor.submit(_job, *task_args)
                            pending_futures.add(new_future)
                    except Exception as e:
                        for f in pending_futures:
                            f.cancel()
                        executor.shutdown(wait=False, cancel_futures=True)
                        raise e
                    break
        if need_parent_id_set:
            raise OSError("拉取信息不完整，此目录结构无法使用该函数")
        for file in files_info:
            pid = file["parent_id"]
            path = f"/{file['name']}"
            while pid != initial_cid:
                path = f"/{dir_nodes[pid].name}" + path
                pid = dir_nodes[pid].parent_id
            file["relpath"] = path
            file["path"] = full_path + path
            yield file
        if files_info_path.exists():
            rmtree(files_info_path)

    def iter_files_with_path(
        self,
        path: str | int | Path | PathLike,
        /,
        qps: int = 5,
        page_size: int = 1150,
        max_workers: int = 10,
        type: Optional[int] = None,
        suffix: Optional[str] = None,
        asc: int = 1,
        order: Literal[
            "file_name", "file_size", "user_utime", "file_type"
        ] = "file_name",
    ) -> Iterator[Dict]:
        """
        遍历目录树迭代文件信息对象

        .. attention::
            此方法通过递归拉取所有文件信息
            拉取速度与文件夹数成正比

        :param path: 迭代目录（可选目录路径，cid）
        :param qps: 每秒请求数
        :param page_size: 分页大小
        :param max_workers: 最大工作线程数
        :param type: 文件类型；1.文档；2.图片；3.音乐；4.视频；5.压缩；6.应用；7.书籍
        :param suffix: 匹配文件后缀名
        :param asc: 升序排列。0: 否，1: 是
        :param order: 排序

            - "file_name": 文件名
            - "file_size": 文件大小
            - "file_type": 文件种类
            - "user_utime": 修改时间

        :return: 迭代器，文件或文件夹信息
        """
        rate_limiter = RateLimiter(qps)

        def _job(
            cid: int,
            path_prefix: str,
            offset: int,
        ) -> Tuple[List[Dict[str, Any]], List[Tuple[int, str, int]]]:
            rate_limiter.acquire()
            payload = {
                "cid": cid,
                "limit": page_size,
                "o": order,
                "asc": asc,
                "type": type,
                "suffix": suffix,
                "offset": offset,
                "show_dir": 1,
            }
            resp = self._request_api(
                "GET",
                "/open/ufile/files",
                params=payload,
            )
            items = resp.get("data", [])
            count = resp.get("count", 0)
            files_found = []
            sub_dirs_to_scan = []
            for attr in items:
                attr = normalize_attr(attr)
                path = (
                    f"{path_prefix}/{attr['name']}"
                    if path_prefix
                    else f"/{attr['name']}"
                )
                attr["relpath"] = path
                attr["path"] = full_path + path
                files_found.append(attr)
                if attr.get("is_dir"):
                    sub_dirs_to_scan.append((int(attr["id"]), path, 0))
            new_offset = offset + len(items)
            if new_offset < count and len(items) > 0:
                sub_dirs_to_scan.append((cid, path_prefix, new_offset))
            return files_found, sub_dirs_to_scan

        if isinstance(path, (Path, PathLike)):
            storage_chain = StorageChain()
            file_item = storage_chain.get_file_item(storage="u115", path=Path(path))
            if not file_item:
                raise CanNotFindPathToCid("无法获取目录信息")
            path = file_item.fileid
        initial_cid = int(path)
        full_path = ""
        if initial_cid != 0:
            resp = self._request_api(
                "GET",
                "/open/folder/get_info",
                params={"file_id": initial_cid},
            )
            if not resp:
                raise OSError("获取目录信息失败")
            data: Dict = resp.get("data")
            paths: List[Dict] = data.get("paths")
            if len(paths) > 1:
                for p in paths[1:]:
                    full_path += f"/{p.get('file_name')}"
            full_path += f"/{data.get('file_name')}"
        if page_size <= 0 or page_size > 1_150:
            page_size = 1_150

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            pending_futures: Set[Future] = set()
            initial_future = executor.submit(_job, initial_cid, "", 0)
            pending_futures.add(initial_future)
            while pending_futures:
                for future in as_completed(pending_futures):
                    pending_futures.remove(future)
                    try:
                        files, sub_dirs = future.result()
                        for file_info in files:
                            yield file_info
                        for task_args in sub_dirs:
                            new_future = executor.submit(_job, *task_args)
                            pending_futures.add(new_future)
                    except Exception as e:
                        for f in pending_futures:
                            f.cancel()
                        executor.shutdown(wait=False, cancel_futures=True)
                        raise e
                    break

    def iter_files_with_path_inc(
        self,
        path: str | int | Path | PathLike,
        /,
        db: Optional[OpenFileOper] = None,
        cache_path: Optional[Path | str | PathLike] = None,
        mode: Literal["add", "remove"] = "add",
        qps: int = 5,
        page_size: int = 1150,
        max_workers: int = 10,
        type: Optional[int] = None,
        suffix: Optional[str] = None,
        asc: int = 1,
        order: Literal[
            "file_name", "file_size", "user_utime", "file_type"
        ] = "file_name",
    ) -> Iterator[Dict]:
        """
        依据本地媒体文件数据增量迭代文件信息对象

        :param path: 迭代目录（可选目录路径，cid）
        :param db: 数据库操作类
        :param cache_path: 信息缓存路径，如果传入路径存在则跳过拉取数据，不存在则自动拉取数据
        :param mode: 数据处理模式
        :param qps: 每秒请求数
        :param page_size: 分页大小
        :param max_workers: 最大工作线程数
        :param type: 文件类型；1.文档；2.图片；3.音乐；4.视频；5.压缩；6.应用；7.书籍
        :param suffix: 匹配文件后缀名
        :param asc: 升序排列。0: 否，1: 是
        :param order: 排序

            - "file_name": 文件名
            - "file_size": 文件大小
            - "file_type": 文件种类
            - "user_utime": 修改时间

        :return: 迭代器，文件或文件夹信息
        """
        rate_limiter = RateLimiter(qps)
        lock = Lock()

        if isinstance(path, (Path, PathLike)):
            storage_chain = StorageChain()
            file_item = storage_chain.get_file_item(storage="u115", path=Path(path))
            if not file_item:
                raise CanNotFindPathToCid("无法获取目录信息")
            path = file_item.fileid
        initial_cid = int(path)

        full_path = ""
        if initial_cid != 0:
            resp = self._request_api(
                "GET",
                "/open/folder/get_info",
                params={"file_id": initial_cid},
            )
            if not resp:
                raise OSError("获取目录信息失败")
            data: Dict = resp.get("data")
            paths: List[Dict] = data.get("paths")
            if len(paths) > 1:
                for p in paths[1:]:
                    full_path += f"/{p.get('file_name')}"
            full_path += f"/{data.get('file_name')}"

        if page_size <= 0 or page_size > 1_150:
            page_size = 1_150

        if not db:
            db = OpenFileOper()

        if not cache_path:
            cache_path = configer.PLUGIN_TEMP_PATH / "u115_iter_files_inc"
        cache: Deque = Deque(directory=cache_path)

        if len(cache) == 0:
            """
            cache 磁盘缓存 与 file_ids 列表 index 相对应
            cache[-2] 储存为 file_ids 列表
            cache[-1] 储存 parent_id_paths 键值对
            """
            need_parent_id_set: Set[int] = set()
            db_parent_id_set: Set[int] = db.get_all_id("folder")
            file_ids: List[int] = []
            parent_id_paths: Dict[int, str] = {}

            def _pull_files_job(
                cid: int,
                offset: int,
            ) -> List[Tuple[int, str, int]]:
                rate_limiter.acquire()
                payload = {
                    "cid": cid,
                    "limit": page_size,
                    "offset": offset,
                    "o": order,
                    "asc": asc,
                    "type": type,
                    "suffix": suffix,
                }
                resp = self._request_api(
                    "GET",
                    "/open/ufile/files",
                    params=payload,
                )
                items = resp.get("data", [])
                count = resp.get("count", 0)
                sub_dirs_to_scan = []
                for attr in items:
                    attr = normalize_attr(attr)
                    with lock:
                        cache.append(attr)
                        file_ids.append(attr["id"])
                        need_parent_id_set.add(attr["parent_id"])
                if (offset // page_size) % 5 == 0:
                    for i in range(1, 6):
                        if offset + page_size * i < count:
                            sub_dirs_to_scan.append((cid, offset + page_size * i))
                return sub_dirs_to_scan

            def _get_path_job(file_id: int) -> List[Dict]:
                if file_id not in find_parent_id_set:
                    return []
                rate_limiter.acquire()
                return_paths = []
                resp = self._request_api(
                    "GET",
                    "/open/folder/get_info",
                    params={
                        "file_id": file_id,
                    },
                )
                data = resp.get("data")
                path = ""
                paths: List[Dict] = data.get("paths")
                if len(paths) > 1:
                    for i in range(1, len(paths)):
                        p = paths[i]
                        path += f"/{p.get('file_name')}"
                        if p.get("file_id") not in db_parent_id_set:
                            return_paths.append(
                                {
                                    "id": p.get("file_id"),
                                    "parent_id": paths[i - 1].get("file_id"),
                                    "name": p.get("file_name"),
                                    "path": path,
                                }
                            )
                            if p.get("file_id") in find_parent_id_set:
                                try:
                                    find_parent_id_set.remove(p.get("file_id"))
                                except KeyError:
                                    pass
                path += f"/{data.get('file_name')}"
                return_paths.append(
                    {
                        "id": file_id,
                        "parent_id": paths[-1].get("file_id"),
                        "name": data.get("file_name"),
                        "path": path,
                    }
                )
                parent_id_paths[file_id] = path
                try:
                    find_parent_id_set.remove(file_id)
                except KeyError:
                    pass
                return return_paths

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                pending_futures: Set[Future] = set()
                initial_future = executor.submit(_pull_files_job, initial_cid, 0)
                pending_futures.add(initial_future)
                while pending_futures:
                    for future in as_completed(pending_futures):
                        pending_futures.remove(future)
                        try:
                            sub_dirs = future.result()
                            for task_args in sub_dirs:
                                new_future = executor.submit(
                                    _pull_files_job, *task_args
                                )
                                pending_futures.add(new_future)
                        except Exception as e:
                            for f in pending_futures:
                                f.cancel()
                            executor.shutdown(wait=False, cancel_futures=True)
                            raise e
                        break
                cache.append(file_ids)  # cache[-2]

                find_parent_id_set: Set[int] = need_parent_id_set - db_parent_id_set

                if find_parent_id_set:
                    results = executor.map(_get_path_job, find_parent_id_set)
                    for items in batched(results, 8_000):
                        datas: List[Dict] = []
                        for item in items:
                            datas.extend(item)
                        db.upsert_batch(datas, "folder")
                cache.append(parent_id_paths)  # cache[-1]

        file_ids_list: List[int] = cache[-2]
        parent_id_paths_dict: Dict[int, str] = cache[-1]
        db_file_ids: Set[int] = db.get_all_id("file")
        if mode == "add":
            find_file_ids: Set[int] = set(file_ids_list) - db_file_ids
            if find_file_ids:
                for file_id in find_file_ids:
                    index = file_ids_list.index(file_id)
                    item = cache[index]
                    try:
                        path = (
                            f"{parent_id_paths_dict[item['parent_id']]}/{item['name']}"
                        )
                    except KeyError:
                        path = f"{db.get_parent_path_by_id(item['parent_id'])}/{item['name']}"
                    item["path"] = path
                    yield item
        else:
            find_file_ids: Set[int] = db_file_ids - set(file_ids_list)
            if find_file_ids:
                yield from db.get_files_info_by_id(find_file_ids)
