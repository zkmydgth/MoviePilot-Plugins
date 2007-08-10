import time
import threading
import hashlib
from typing import Optional, Union
from pathlib import Path
from datetime import datetime, timezone

import requests
import oss2
from oss2 import SizedFileAdapter, determine_part_size
from oss2.models import PartInfo
from tqdm import tqdm

from app import schemas
from app.log import logger
from app.helper.storage import StorageHelper
from app.chain.storage import StorageChain
from app.utils.string import StringUtils
from app.schemas import NotificationType

from ..core.config import configer
from ..core.message import post_message
from ..utils.oopserver import OOPServerRequest
from ..utils.sentry import sentry_manager


p115_open_lock = threading.Lock()


class U115NoCheckInException(Exception):
    """
    未登录
    """


@sentry_manager.capture_all_class_exceptions
class U115OpenHelper:
    """
    115 Open Api
    """

    _auth_state = {}

    base_url = "https://proapi.115.com"

    def __init__(self):
        super().__init__()
        self.session = requests.Session()
        self._init_session()

        self.fail_upload_count = 0

        self.oopserver_request = OOPServerRequest(max_retries=3, backoff_factor=1.0)

    def _init_session(self):
        """
        初始化带速率限制的会话
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
        访问token
        """
        with p115_open_lock:
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
            if expires_in and refresh_time + expires_in < int(time.time()):
                tokens = self.__refresh_access_token(refresh_token)
                if tokens:
                    storagehelper.set_storage(
                        storage="u115",
                        conf={"refresh_time": int(time.time()), **tokens},
                    )
            access_token = tokens.get("access_token")
            if access_token:
                self.session.headers.update({"Authorization": f"Bearer {access_token}"})
            return access_token

    def __refresh_access_token(self, refresh_token: str) -> Optional[dict]:
        """
        刷新access_token
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
        return result.get("data")

    def _request_api(
        self,
        method: str,
        endpoint: str,
        result_key: Optional[str] = None,
        headers: Optional[dict] = None,
        **kwargs,
    ) -> Optional[Union[dict, list]]:
        """
        带错误处理和速率限制的API请求
        """
        # 检查会话
        self._check_session()

        request_headers = self.session.headers.copy()
        if headers:
            request_headers.update(headers)
        kwargs["headers"] = request_headers

        resp = self.session.request(method, f"{self.base_url}{endpoint}", **kwargs)
        if resp is None:
            logger.warn(f"【P115Open】{method} 请求 {endpoint} 失败！")
            return None

        # 处理速率限制
        if resp.status_code == 429:
            reset_time = int(resp.headers.get("X-RateLimit-Reset", 60))
            time.sleep(reset_time + 5)
            return self._request_api(method, endpoint, result_key, **kwargs)

        # 处理请求错误
        resp.raise_for_status()

        # 返回数据
        ret_data = resp.json()
        if ret_data.get("code") != 0:
            logger.warn(
                f"【P115Open】{method} 请求 {endpoint} 出错：{ret_data.get('message')}！"
            )

        if result_key:
            return ret_data.get(result_key)
        return ret_data

    def _delay_get_item(self, path: Path) -> Optional[schemas.FileItem]:
        """
        自动延迟重试 get_item 模块
        """
        storagechain = StorageChain()
        for _ in range(2):
            time.sleep(2)
            fileitem = storagechain.get_file_item(storage="u115", path=Path(path))
            if fileitem:
                return fileitem
        return None

    def get_download_url(
        self,
        pickcode: str,
        user_agent: str,
    ) -> Optional[str]:
        """
        获取下载链接
        """
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
        return list(download_info.values())[0].get("url", {}).get("url")

    @staticmethod
    def _calc_sha1(filepath: Path, size: Optional[int] = None) -> str:
        """
        计算文件SHA1
        size: 前多少字节
        """
        sha1 = hashlib.sha1()
        with open(filepath, "rb") as f:
            if size:
                chunk = f.read(size)
                sha1.update(chunk)
            else:
                while chunk := f.read(8192):
                    sha1.update(chunk)
        return sha1.hexdigest()

    def upload_fail_count(self):
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
        实现带秒传、断点续传和二次认证的文件上传
        """

        def encode_callback(cb: str) -> str:
            return oss2.utils.b64encode_as_string(cb)

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
            if configer.notify:
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

        target_name = new_name or local_path.name
        target_path = Path(target_dir.path) / target_name
        # 计算文件特征值
        file_size = local_path.stat().st_size
        file_sha1 = self._calc_sha1(local_path)
        file_preid = self._calc_sha1(local_path, 128 * 1024 * 1024)

        # 获取目标目录CID
        target_cid = target_dir.fileid
        target_param = f"U_1_{target_cid}"

        wait_start_time = time.perf_counter()
        send_wait = False
        while True:
            start_time = time.perf_counter()
            # Step 1: 初始化上传
            init_data = {
                "file_name": target_name,
                "file_size": file_size,
                "target": target_param,
                "fileid": file_sha1,
                "preid": file_preid,
            }
            init_resp = self._request_api("POST", "/open/upload/init", data=init_data)
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
                    sign_val = hashlib.sha1(chunk).hexdigest().upper()
                second_sha1 = sign_val
                # 重新初始化请求
                # sign_key，sign_val(根据sign_check计算的值大写的sha1值)
                init_data.update(
                    {"pick_code": pick_code, "sign_key": sign_key, "sign_val": sign_val}
                )
                init_resp = self._request_api(
                    "POST", "/open/upload/init", data=init_data
                )
                if not init_resp:
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
                end_time = time.perf_counter()
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
                file_id = init_result.get("file_id", None)
                if file_id:
                    logger.debug(
                        f"【P115Open】{target_name} 使用秒传返回ID获取文件信息"
                    )
                    time.sleep(2)
                    info_resp = self._request_api(
                        "GET",
                        "/open/folder/get_info",
                        "data",
                        params={"file_id": int(file_id)},
                    )
                    if info_resp:
                        return schemas.FileItem(
                            storage="u115",
                            fileid=str(info_resp["file_id"]),
                            path=str(target_path)
                            + ("/" if info_resp["file_category"] == "0" else ""),
                            type="file" if info_resp["file_category"] == "1" else "dir",
                            name=info_resp["file_name"],
                            basename=Path(info_resp["file_name"]).stem,
                            extension=Path(info_resp["file_name"]).suffix[1:].lower()
                            if info_resp["file_category"] == "1"
                            else None,
                            pickcode=info_resp["pick_code"],
                            size=StringUtils.num_filesize(info_resp["size"])
                            if info_resp["file_category"] == "1"
                            else None,
                            modify_time=info_resp["utime"],
                        )
                return self._delay_get_item(target_path)

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

            if wait_start_time - time.perf_counter() > int(
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
                time.sleep(int(configer.get_config("upload_module_wait_time")))
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
                        logger.info(f"【P115Open】休眠，等待秒传: {target_name}")
                        if not send_wait:
                            send_upload_wait(target_name)
                            send_wait = True
                        time.sleep(int(configer.get_config("upload_module_wait_time")))
                    else:
                        logger.warn("【P115Open】获取用户上传速度错误，网络问题")
                        break
                except Exception as e:
                    logger.warn(f"【P115Open】获取用户上传速度错误: {e}")
                    break

        # Step 4: 获取上传凭证
        second_auth = False
        token_resp = self._request_api("GET", "/open/upload/get_token", "data")
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
        )
        if resume_resp:
            logger.debug(f"【P115Open】上传 Step 5 断点续传结果: {resume_resp}")
            if resume_resp.get("callback"):
                callback = resume_resp["callback"]

        # Step 6: 对象存储上传
        auth = oss2.StsAuth(
            access_key_id=access_key_id,
            access_key_secret=access_key_secret,
            security_token=security_token,
        )
        bucket = oss2.Bucket(auth, endpoint, bucket_name, connect_timeout=120)
        part_size = determine_part_size(file_size, preferred_size=50 * 1024 * 1024)

        # 初始化进度条
        logger.info(
            f"【P115Open】开始上传: {local_path} -> {target_path}，分片大小：{StringUtils.str_filesize(part_size)}"
        )
        progress_bar = tqdm(
            total=file_size, unit="B", unit_scale=True, desc="上传进度", ascii=True
        )

        try:
            # 初始化分片
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
                    time.sleep(2**attempt)

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
                        except oss2.exceptions.OssError as e:
                            # 判断是否为STS Token过期错误
                            if e.code == "SecurityTokenExpired":
                                logger.warn(
                                    f"【P115Open】上传凭证已过期，正在重新获取... (重试次数: {attempt + 1}/3)"
                                )
                                # Step 4: 重新获取上传凭证
                                token_resp = self._request_api(
                                    "GET", "/open/upload/get_token", "data"
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
                                auth = oss2.StsAuth(
                                    access_key_id=access_key_id,
                                    access_key_secret=access_key_secret,
                                    security_token=security_token,
                                )
                                bucket = oss2.Bucket(
                                    auth, endpoint, bucket_name, connect_timeout=120
                                )
                                logger.info(
                                    "【P115Open】上传凭证已刷新，将重试当前分片。"
                                )
                                continue
                            logger.warn(
                                f"【P115Open】上传分片 {part_number} 失败: {e}，正在重试... ({attempt + 1}/3)"
                            )
                            time.sleep(2**attempt)
                        except Exception as e:
                            logger.warn(
                                f"【P115Open】上传分片 {part_number} 发生未知错误: {e}，正在重试... ({attempt + 1}/3)"
                            )
                            time.sleep(2**attempt)
                    else:
                        logger.error(
                            f"【P115Open】{target_name} 分片 {part_number} 达到最大重试次数，上传终止。"
                        )
                        return None

                    offset += num_to_upload
                    part_number += 1
                    # 更新进度
                    progress_bar.update(num_to_upload)
        except Exception as e:
            logger.error(f"【P115Open】{target_name} 分块生成出现未知错误: {e}")
            return None
        finally:
            # 关闭进度条
            if progress_bar:
                progress_bar.close()

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
                        if self.upload_fail_count():
                            logger.warn(f"【P115Open】{target_name} 上传重试")
                            return self.upload(target_dir, local_path, new_name)
                        logger.error(f"【P115Open】{target_name} 上传失败")
                        return None
                except Exception:
                    logger.warn("【P115Open】上传 Step 6 回调无结果")
                logger.info(f"【P115Open】{target_name} 上传成功")
            else:
                logger.warn(
                    f"【P115Open】{target_name} 上传失败，错误码: {result.status}"
                )
                return None
        except oss2.exceptions.OssError as e:
            if e.code == "InvalidAccessKeyId":
                logger.warn(
                    f"【P115Open】上传凭证失效，将重新获取凭证并继续上传: {target_name}"
                )
                return self.upload(target_dir, local_path, new_name)

            if e.code == "FileAlreadyExists":
                logger.warn(f"【P115Open】{target_name} 已存在")
            else:
                logger.error(
                    f"【P115Open】{target_name} 上传失败: {e.status}, 错误码: {e.code}, 详情: {e.message}"
                )
                return None
        except Exception as e:
            logger.error(f"【P115Open】{target_name} 回调出现未知错误: {e}")
            return None

        end_time = time.perf_counter()
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
        return self._delay_get_item(target_path)
