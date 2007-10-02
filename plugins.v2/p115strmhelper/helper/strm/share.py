from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import batched
from pathlib import Path
from time import perf_counter
from typing import List, Dict, Optional, Set

from p115client import P115Client

from app.log import logger

from ...core.config import configer
from ...core.p115 import iter_share_files_with_path
from ...helper.mediainfo_download import MediaInfoDownloader
from ...utils.path import PathUtils
from ...utils.sentry import sentry_manager
from ...utils.strm import StrmUrlGetter, StrmGenerater


class ShareStrmHelper:
    """
    根据分享生成STRM
    """

    def __init__(self, client: P115Client, mediainfodownloader: MediaInfoDownloader):
        self.rmt_mediaext: Set[str] = {
            f".{ext.strip()}"
            for ext in configer.get_config("user_rmt_mediaext")
            .replace("，", ",")
            .split(",")
        }
        self.download_mediaext: Set[str] = {
            f".{ext.strip()}"
            for ext in configer.get_config("user_download_mediaext")
            .replace("，", ",")
            .split(",")
        }
        self.auto_download_mediainfo = configer.get_config(
            "share_strm_auto_download_mediainfo_enabled"
        )
        self.client = client
        self.mediainfodownloader = mediainfodownloader
        self.elapsed_time = 0
        self.total_count = 0
        self.strm_count = 0
        self.strm_fail_count = 0
        self.mediainfo_count = 0
        self.mediainfo_fail_count = 0
        self.strm_fail_dict: Dict[str, str] = {}
        self.mediainfo_fail_dict: List = []
        self.share_media_path = configer.get_config("user_share_pan_path")
        self.local_media_path = configer.get_config("user_share_local_path")
        self.server_address = configer.get_config("moviepilot_address").rstrip("/")
        self.strm_url_format = configer.get_config("strm_url_format")
        self.download_mediainfo_list = []

        self.strmurlgetter = StrmUrlGetter()

    def __process_single_item(
        self,
        share_code: str,
        receive_code: str,
        file_id: str,
        file_path: str,
        pan_file_name: str,
        file_size: int,
        file_sha1: str,
        thumb: Optional[str],
    ):
        """
        处理单个 STRM 文件
        """
        if not PathUtils.has_prefix(file_path, self.share_media_path):
            logger.debug(
                "【分享STRM生成】此文件不在用户设置分享目录下，跳过分享路径: %s",
                str(file_path).replace(str(self.local_media_path), "", 1),
            )
            return
        file_path = Path(self.local_media_path) / Path(file_path).relative_to(
            self.share_media_path
        )
        file_target_dir = file_path.parent
        original_file_name = file_path.name
        file_name = file_path.stem + ".strm"
        new_file_path = file_target_dir / file_name
        try:
            if self.auto_download_mediainfo:
                if file_path.suffix.lower() in self.download_mediaext:
                    self.download_mediainfo_list.append(
                        {
                            "type": "share",
                            "share_code": share_code,
                            "receive_code": receive_code,
                            "file_id": file_id,
                            "path": file_path,
                            "thumb": thumb,
                            "sha1": file_sha1,
                        }
                    )
                    return

            if file_path.suffix.lower() not in self.rmt_mediaext:
                logger.warn(
                    "【分享STRM生成】文件后缀不匹配，跳过分享路径: %s",
                    str(file_path).replace(str(self.local_media_path), "", 1),
                )
                return

            if not (
                result := StrmGenerater.should_generate_strm(
                    original_file_name, "share", file_size
                )
            )[1]:
                logger.warn(
                    f"【分享STRM生成】{result[0]}，跳过分享路径: {str(file_path).replace(str(self.local_media_path), '', 1)}"
                )
                return

            if not file_id:
                logger.error(
                    f"【分享STRM生成】{original_file_name} 不存在 id 值，无法生成 STRM 文件"
                )
                self.strm_fail_dict[str(new_file_path)] = "不存在 id 值"
                self.strm_fail_count += 1
                return

            new_file_path.parent.mkdir(parents=True, exist_ok=True)

            strm_url = self.strmurlgetter.get_share_strm_url(
                share_code, receive_code, file_id, pan_file_name
            )

            with open(new_file_path, "w", encoding="utf-8") as file:
                file.write(strm_url)
            self.strm_count += 1
            logger.info("【分享STRM生成】生成 STRM 文件成功: %s", str(new_file_path))
        except Exception as e:
            sentry_manager.sentry_hub.capture_exception(e)
            logger.error(
                "【分享STRM生成】生成 STRM 文件失败: %s  %s",
                str(new_file_path),
                e,
            )
            self.strm_fail_count += 1
            self.strm_fail_dict[str(new_file_path)] = str(e)
            return

    def generate_strm_files(
        self,
        cid: int = 0,
        share_code: str = "",
        receive_code: str = "",
    ):
        """
        获取分享文件，生成 STRM
        """
        start_time = perf_counter()
        for batch in batched(
            iter_share_files_with_path(
                client=self.client,
                share_code=share_code,
                receive_code=receive_code,
                cid=cid,
            ),
            1_000,
        ):
            self.total_count += len(batch)
            with ThreadPoolExecutor(max_workers=128) as executor:
                future_to_item = {
                    executor.submit(
                        self.__process_single_item,
                        share_code=share_code,
                        receive_code=receive_code,
                        file_id=item["id"],
                        file_path=item["path"],
                        pan_file_name=item["name"],
                        file_size=item["size"],
                        file_sha1=item["sha1"],
                        thumb=item.get("thumb", None),
                    ): item
                    for item in batch
                }

                for future in as_completed(future_to_item):
                    item = future_to_item[future]
                    try:
                        future.result()
                    except Exception as e:
                        sentry_manager.sentry_hub.capture_exception(e)
                        logger.error(f"【分享STRM生成】并发处理出错: {item} - {str(e)}")

        end_time = perf_counter()
        self.elapsed_time = end_time - start_time

        self.mediainfo_count, self.mediainfo_fail_count, self.mediainfo_fail_dict = (
            self.mediainfodownloader.batch_auto_share_downloader(
                downloads_list=self.download_mediainfo_list
            )
        )

    def get_generate_total(self):
        """
        输出总共生成文件个数
        """
        if self.strm_fail_dict:
            for path, error in self.strm_fail_dict.items():
                logger.warn(f"【分享STRM生成】{path} 生成错误原因: {error}")
        if self.mediainfo_fail_dict:
            for path in self.mediainfo_fail_dict:
                logger.warn(f"【分享STRM生成】{path} 下载错误")
        logger.info(
            f"【分享STRM生成】分享生成 STRM 文件完成，总共生成 {self.strm_count} 个 STRM 文件，下载 {self.mediainfo_count} 个媒体数据文件"
        )
        if self.strm_fail_count != 0 or self.mediainfo_fail_count != 0:
            logger.warn(
                f"【分享STRM生成】{self.strm_fail_count} 个 STRM 文件生成失败，{self.mediainfo_fail_count} 个媒体数据文件下载失败"
            )
        logger.debug(
            f"【全量STRM生成】时间 {self.elapsed_time:.6f} 秒，总迭代文件数量 {self.total_count}"
        )
        return (
            self.strm_count,
            self.mediainfo_count,
            self.strm_fail_count,
            self.mediainfo_fail_count,
        )
