import threading
import time
import re
from queue import Queue, Empty
from enum import Enum
from datetime import datetime, timezone
from typing import Optional

from p115client import P115Client
from p115client.tool.iterdir import share_iterdir
from p115client.tool.util import share_extract_payload

from app.log import logger
from app.core.metainfo import MetaInfo
from app.core.context import MediaInfo
from app.chain.media import MediaChain

from ..core.config import configer
from ..core.message import post_message
from ..core.cache import idpathcacher
from ..core.i18n import i18n
from ..core.aliyunpan import BAligo
from ..helper.ali2115 import Ali2115Helper
from ..utils.sentry import sentry_manager
from ..utils.oopserver import OOPServerRequest


@sentry_manager.capture_all_class_exceptions
class ShareTransferHelper:
    """
    分享链接转存
    """

    def __init__(self, client: P115Client, aligo: Optional[BAligo]):
        self.client = client
        self.aligo = aligo
        self._add_share_queue = Queue()
        self._add_share_worker_thread = None
        self._add_share_worker_lock = threading.Lock()

    def _ensure_add_share_worker_running(self):
        """
        确保工作线程正在运行
        """
        with self._add_share_worker_lock:
            if (
                self._add_share_worker_thread is None
                or not self._add_share_worker_thread.is_alive()
            ):
                self._add_share_worker_thread = threading.Thread(
                    target=self._process_add_share_queue, daemon=True
                )
                self._add_share_worker_thread.start()

    def _process_add_share_queue(self):
        """
        处理队列中的任务
        """
        while True:
            try:
                # 获取任务，设置超时避免永久阻塞
                task = self._add_share_queue.get(timeout=60)  # 60秒无任务则退出
                url, channel, userid = task

                # 执行任务
                self.__add_share(url, channel, userid)

                # 任务间隔
                time.sleep(3)

                # 标记任务完成
                self._add_share_queue.task_done()

            except Empty:
                logger.debug("【分享转存】释放分享转存队列进程")
                break
            except Exception as e:
                logger.error(f"【分享转存】任务处理异常: {e}")
                time.sleep(5)

    def add_share_recognize_mediainfo(self, share_code: str, receive_code: str):
        """
        分享转存识别媒体信息
        """
        file_num = 0
        file_mediainfo = None
        for item in share_iterdir(
            self.client,
            receive_code=receive_code,
            share_code=share_code,
            cid=0,
        ):
            if file_num == 1:
                file_num = 2
                break
            item_name = item["name"]
            file_num += 1
        if file_num == 1:
            mediachain = MediaChain()
            file_meta = MetaInfo(title=item_name)
            file_mediainfo = mediachain.recognize_by_meta(file_meta)
        return file_mediainfo

    @staticmethod
    def share_url_extract(url: str):
        """
        解析分享链接
        """
        return share_extract_payload(url)

    def __add_share_aliyunpan(self, url, channel, userid):
        """
        添加阿里云盘分享任务
        """
        ali2115 = Ali2115Helper(self.client, self.aligo)
        share_id = ali2115.extract_share_code_from_url(url)
        logger.info(f"【Ali2115】解析分享链接 share_id={share_id}")
        if not share_id:
            logger.error(f"【Ali2115】解析分享链接失败：{url}")
            post_message(
                channel=channel,
                title=f"{i18n.translate('share_url_extract_error', url=url)}",
                userid=userid,
            )
            return
        parent_path = configer.get_config("pan_transfer_paths").split("\n")[0]
        parent_id = idpathcacher.get_id_by_dir(directory=str(parent_path))
        if not parent_id:
            parent_id = self.client.fs_dir_getid(parent_path)["id"]
            logger.info(f"【Ali2115】获取到转存目录 ID：{parent_id}")
            idpathcacher.add_cache(id=int(parent_id), directory=str(parent_path))

        unrecognized_path = configer.pan_transfer_unrecognized_path
        unrecognized_id = idpathcacher.get_id_by_dir(directory=str(unrecognized_path))
        if not unrecognized_id:
            unrecognized_id = self.client.fs_dir_getid(unrecognized_path)["id"]
            logger.info(f"【Ali2115】获取到未识别目录 ID：{unrecognized_id}")
            idpathcacher.add_cache(
                id=int(unrecognized_id), directory=str(unrecognized_path)
            )

        share_token = ali2115.get_ali_share_token(share_id)

        # 尝试识别媒体信息
        file_mediainfo = ali2115.share_recognize_mediainfo(share_token)

        status, msg, success_upload, fail_upload = ali2115.share_upload(
            share_token=share_token,
            parent_id=int(parent_id),
            unrecognized_id=int(unrecognized_id),
            unrecognized_path=unrecognized_path,
            rmt_mediaext=[
                f".{ext.strip()}"
                for ext in configer.get_config("user_rmt_mediaext")
                .replace("，", ",")
                .split(",")
            ],
            file_mediainfo=file_mediainfo,
        )

        if status:
            logger.info(f"【Ali2115】秒传 {share_id} 完成")
            if not file_mediainfo:
                post_message(
                    channel=channel,
                    title=i18n.translate("share_add_success"),
                    text=f"""
分享链接：https://www.alipan.com/s/{share_id}
秒传目录：{unrecognized_path}
秒传信息：成功 {success_upload} 个，失败 {fail_upload} 个

注意：无法识别媒体信息，请手动整理
""",
                    userid=userid,
                )
            else:
                post_message(
                    channel=channel,
                    title=i18n.translate(
                        "share_add_success_2",
                        title=file_mediainfo.title,
                        year=file_mediainfo.year,
                    ),
                    text=f"""
链接：https://www.alipan.com/s/{share_id}
信息：秒传成功 {success_upload} 个，失败 {fail_upload} 个
简介：{file_mediainfo.overview}
""",
                    image=file_mediainfo.poster_path,
                    userid=userid,
                )
            self.post_share_info("aliyun", share_id, None, file_mediainfo)

        else:
            logger.info(f"【分享转存】秒传失败：{msg}")
            post_message(
                channel=channel,
                title=i18n.translate("share_add_fail"),
                text=f"""
分享链接：https://www.alipan.com/s/{share_id}
失败原因：{msg}
""",
                userid=userid,
            )

    def __add_share(self, url, channel, userid):
        """
        分享转存
        """
        if not configer.get_config("pan_transfer_enabled") or not configer.get_config(
            "pan_transfer_paths"
        ):
            post_message(
                channel=channel,
                title=i18n.translate("add_share_config_error"),
                userid=userid,
            )
            return
        try:
            if bool(
                re.match(
                    r"^https?://(.*\.)?(alipan|aliyundrive)\.[a-zA-Z]{2,}(?:\/|$)", url
                )
            ):
                if not configer.pan_transfer_unrecognized_path:
                    post_message(
                        channel=channel,
                        title=i18n.translate("add_share_config_error"),
                        userid=userid,
                    )
                    return
                if not self.aligo:
                    post_message(
                        channel=channel,
                        title=f"{i18n.translate('share_url_aligo_error', url=url)}",
                        userid=userid,
                    )
                    return
                self.__add_share_aliyunpan(url, channel, userid)
                return

            data = self.share_url_extract(url)
            share_code = data["share_code"]
            receive_code = data["receive_code"]
            logger.info(
                f"【分享转存】解析分享链接 share_code={share_code} receive_code={receive_code}"
            )
            if not share_code or not receive_code:
                logger.error(f"【分享转存】解析分享链接失败：{url}")
                post_message(
                    channel=channel,
                    title=f"{i18n.translate('share_url_extract_error', url=url)}",
                    userid=userid,
                )
                return
            parent_path = configer.get_config("pan_transfer_paths").split("\n")[0]
            parent_id = idpathcacher.get_id_by_dir(directory=str(parent_path))
            if not parent_id:
                parent_id = self.client.fs_dir_getid(parent_path)["id"]
                logger.info(f"【分享转存】获取到转存目录 ID：{parent_id}")
                idpathcacher.add_cache(id=int(parent_id), directory=str(parent_path))
            payload = {
                "share_code": share_code,
                "receive_code": receive_code,
                "file_id": 0,
                "cid": int(parent_id),
                "is_check": 0,
            }

            # 尝试识别媒体信息
            file_mediainfo = self.add_share_recognize_mediainfo(
                share_code=share_code, receive_code=receive_code
            )

            resp = self.client.share_receive(payload)
            if resp["state"]:
                logger.info(f"【分享转存】转存 {share_code} 到 {parent_path} 成功！")
                if not file_mediainfo:
                    post_message(
                        channel=channel,
                        title=i18n.translate("share_add_success"),
                        text=f"""
分享链接：https://115cdn.com/s/{share_code}?password={receive_code}
转存目录：{parent_path}
""",
                        userid=userid,
                    )
                else:
                    post_message(
                        channel=channel,
                        title=i18n.translate(
                            "share_add_success_2",
                            title=file_mediainfo.title,
                            year=file_mediainfo.year,
                        ),
                        text=f"""
链接：https://115cdn.com/s/{share_code}?password={receive_code}
简介：{file_mediainfo.overview}
""",
                        image=file_mediainfo.poster_path,
                        userid=userid,
                    )
                self.post_share_info("115", share_code, receive_code, file_mediainfo)
            else:
                logger.info(f"【分享转存】转存 {share_code} 失败：{resp['error']}")
                post_message(
                    channel=channel,
                    title=i18n.translate("share_add_fail"),
                    text=f"""
分享链接：https://115cdn.com/s/{share_code}?password={receive_code}
转存目录：{parent_path}
失败原因：{resp["error"]}
""",
                    userid=userid,
                )
            return
        except Exception as e:
            logger.error(f"【分享转存】运行失败: {e}")
            post_message(
                channel=channel,
                title=i18n.translate("share_add_fail_2", e=e),
                userid=userid,
            )
            return

    def add_share(self, url, channel, userid):
        """
        将分享任务加入队列
        """
        self._add_share_queue.put((url, channel, userid))
        logger.info(
            f"【分享转存】{url} 任务已加入分享转存队列，当前队列大小：{self._add_share_queue.qsize()}"
        )
        self._ensure_add_share_worker_running()

    @staticmethod
    def post_share_info(
        type: str,
        share_code: str,
        receive_code: Optional[str],
        file_mediainfo: Optional[MediaInfo] = None,
    ):
        """
        上传分享信息
        """
        if not configer.get_config("upload_share_info"):
            return

        oopserver_request = OOPServerRequest(max_retries=3, backoff_factor=1.0)

        desired_keys = [
            "source",
            "type",
            "title",
            "en_title",
            "hk_title",
            "tw_title",
            "sg_title",
            "year",
            "season",
            "tmdb_id",
            "imdb_id",
            "tvdb_id",
            "douban_id",
            "bangumi_id",
            "collection_id",
        ]
        json_data = {}
        if file_mediainfo:
            for key in desired_keys:
                value = getattr(file_mediainfo, key)
                if isinstance(value, Enum):
                    json_data[key] = value.value
                else:
                    json_data[key] = value

        if type == "115":
            json_data["url"] = (
                f"https://115cdn.com/s/{share_code}?password={receive_code}"
            )
            path = "/share/info"
        else:
            json_data["url"] = f"https://www.alipan.com/s/{share_code}"
            path = "/ali_share/info"
        json_data["postime"] = (
            datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        try:
            response = oopserver_request.make_request(
                path=path,
                method="POST",
                headers={"x-machine-id": configer.get_config("MACHINE_ID")},
                json_data=json_data,
                timeout=10.0,
            )

            if response is not None and response.status_code == 201:
                logger.info(
                    f"【分享转存】分享转存信息报告服务器成功: {response.json()}"
                )
            else:
                logger.debug("【分享转存】分享转存报告服务器失败，网络问题")
        except Exception as e:
            logger.debug(f"【分享转存】分享转存报告服务器失败: {e}")
