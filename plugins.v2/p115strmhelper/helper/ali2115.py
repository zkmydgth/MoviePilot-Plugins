from hashlib import sha1
from time import sleep
from typing import List
from urllib.parse import urlparse

import requests
from p115client import P115Client

from app.log import logger
from app.core.metainfo import MetaInfo
from app.chain.media import MediaChain

from ..core.aliyunpan import BAligo
from ..core.i18n import i18n
from ..core.config import configer
from ..core.message import post_message
from ..core.cache import idpathcacher
from ..utils.sentry import sentry_manager


@sentry_manager.capture_all_class_exceptions
class Ali2115Helper:
    """
    阿里云盘分享资源秒传 115
    """

    def __init__(self, u115_client: P115Client, aligo_client: BAligo):
        self.u115_client = u115_client
        self.ali_client = aligo_client

        self.ali_download_url = None
        self.folder_id = None
        self.file_name_list = None

    @staticmethod
    def calculate_sha1_range(url: str, start: int, length: int):
        """
        计算 sha1
        """
        end = start + length - 1
        headers = {"Range": f"bytes={start}-{end}"}
        with requests.get(url, headers=headers, stream=True) as r:
            r.raise_for_status()
            _sha1 = sha1()
            for chunk in r.iter_content(chunk_size=8192):
                _sha1.update(chunk)
            return _sha1.hexdigest().upper()

    def double_sha1_range(self, sign_check: str):
        """
        二次 sha1 校验
        """
        start_str, end_str = sign_check.split("-")
        start, end = int(start_str), int(end_str)
        length = end - start + 1
        return self.calculate_sha1_range(self.ali_download_url, start, length)

    def upload_115(
        self, file_name, file_size, m115_dir_id, full_sha1, ali_download_url
    ):
        """
        上传 115
        """
        self.ali_download_url = ali_download_url
        return self.u115_client.upload_file_init(
            filename=file_name,
            filesize=file_size,
            filesha1=full_sha1,
            pid=m115_dir_id,
            read_range_bytes_or_hash=self.double_sha1_range,
        )

    def get_ali_folder_id(self):
        """
        获取转存文件夹 ID
        """
        if self.folder_id:
            return self.folder_id
        folder_info = self.ali_client.get_folder_by_path(path="秒传转存")
        if not folder_info:
            folder_id = self.ali_client.create_folder(
                name="秒传转存", check_name_mode="overwrite"
            ).file_id
        else:
            folder_id = folder_info.file_id
        self.folder_id = folder_id
        return folder_id

    def ali_tree_share(self, share_token, parent_file_id="root"):
        """
        递归分享文件
        """
        file_list = self.ali_client.get_share_file_list(
            share_token, parent_file_id=parent_file_id
        )
        for file in file_list:
            if file.type == "folder":
                yield from self.ali_tree_share(share_token, file.file_id)
            else:
                yield file

    def share_recognize_mediainfo(self, share_token):
        """
        分享转存识别媒体信息
        """
        file_list = self.ali_client.get_share_file_list(
            share_token, parent_file_id="root"
        )
        for file in file_list:
            item_name = file.name
            break
        mediachain = MediaChain()
        file_meta = MetaInfo(title=item_name)
        file_mediainfo = mediachain.recognize_by_meta(file_meta)
        return file_mediainfo

    def get_ali_download_url(self, file_id: str):
        """
        获取阿里云盘文件下载链接
        """
        return self.ali_client.get_download_url(file_id=file_id)

    def get_ali_share_list(self, share_token):
        """
        获取所有分享文件信息
        """
        return self.ali_tree_share(share_token)

    def save_ali_share_to_pan(self, share_token: str):
        """
        保存分享文件到阿里云盘
        """
        self.ali_client.share_file_save_all_to_drive(
            share_token=share_token,
            to_parent_file_id=self.get_ali_folder_id(),
        )

    def get_ali_share_token(self, share_id: str):
        """
        获取分享 Token
        """
        return self.ali_client.get_share_token(share_id)

    def share_upload(self, share_token: str, parent_id: int):
        """
        运行分享秒传
        """

        download_url_list: List = []
        remove_list: List = []

        def get_download_and_remove(path, info):
            """
            获取下载链接并删除
            """
            if not path and info.file_id not in remove_list:
                remove_list.append(info.file_id)

            if info.type == "file":
                if info.name not in self.file_name_list:
                    return
                url_info = self.get_ali_download_url(info.file_id)
                info_list = [
                    url_info.url,
                    url_info.size,
                    info.name,
                    str(url_info.content_hash).upper(),
                ]
                download_url_list.append(info_list)
                self.file_name_list = [
                    item for item in self.file_name_list if item != info.name
                ]

        self.save_ali_share_to_pan(share_token)
        sleep(2)
        self.file_name_list = [item.name for item in self.ali_tree_share(share_token)]

        while self.file_name_list:
            self.ali_client.walk_files(
                callback=get_download_and_remove,
                parent_file_id=self.get_ali_folder_id(),
            )
            sleep(3)

        self.ali_client.batch_delete_files(file_id_list=remove_list)
        logger.debug(
            f"【Ali2115】秒传文件列表: {[lst[2] for lst in download_url_list]}"
        )

        for url in download_url_list:
            self.upload_115(
                file_name=url[2],
                file_size=url[1],
                m115_dir_id=parent_id,
                full_sha1=url[3],
                ali_download_url=url[0],
            )

        logger.info(f"【Ali2115】秒传文件成功: {[lst[2] for lst in download_url_list]}")

    @staticmethod
    def extract_share_code_from_url(url: str) -> str | None:
        """
        通过解析URL来稳定地提取阿里云盘分享码。
        """
        try:
            parsed_url = urlparse(url)
            path_parts = parsed_url.path.split("/")
            if len(path_parts) >= 3 and path_parts[-2] == "s":
                share_code = path_parts[-1]
                if share_code:
                    return share_code
        except Exception as e:
            raise e
        return None

    def add_share(self, url, channel, userid):
        """
        添加分享任务
        """
        share_id = self.extract_share_code_from_url(url)
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
            parent_id = self.u115_client.fs_dir_getid(parent_path)["id"]
            logger.info(f"【Ali2115】获取到转存目录 ID：{parent_id}")
            idpathcacher.add_cache(id=int(parent_id), directory=str(parent_path))

        share_token = self.get_ali_share_token(share_id)

        # 尝试识别媒体信息
        file_mediainfo = self.share_recognize_mediainfo(share_token)

        self.share_upload(share_token, parent_id)

        logger.info(f"【Ali2115】秒传 {share_id} 到 {parent_path} 成功！")
        if not file_mediainfo:
            post_message(
                channel=channel,
                title=i18n.translate("share_add_success"),
                text=f"""
分享链接：https://www.alipan.com/s/{share_id}
秒传目录：{parent_path}
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
简介：{file_mediainfo.overview}
""",
                image=file_mediainfo.poster_path,
                userid=userid,
            )
