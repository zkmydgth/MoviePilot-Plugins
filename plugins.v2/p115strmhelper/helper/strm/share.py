from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import batched
from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import List, Dict, Set, Deque, Tuple

from p115client import P115Client
from p115client.util import share_extract_payload

from app.chain.transfer import TransferChain
from app.log import logger
from app.schemas import FileItem

from ...core.config import configer
from ...core.p115 import iter_share_files_with_path
from ...core.scrape import media_scrape_metadata
from ...helper.mediainfo_download import MediaInfoDownloader
from ...helper.mediaserver import MediaServerRefresh
from ...schemas.share import ShareStrmConfig
from ...schemas.size import CompareMinSize
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
            for ext in configer.user_rmt_mediaext.replace("，", ",").split(",")
        }
        self.download_mediaext: Set[str] = {
            f".{ext.strip()}"
            for ext in configer.user_download_mediaext.replace("，", ",").split(",")
        }

        self.client = client
        self.mediainfodownloader = mediainfodownloader

        self.elapsed_time = 0

        self.total_count = 0
        self.strm_count = 0
        self.mediainfo_count = 0

        self.strm_fail_count = 0
        self.strm_fail_dict: Dict[str, str] = {}
        self.mediainfo_fail_count = 0
        self.mediainfo_fail_dict: List = []

        self.download_mediainfo_list = []

        self.scrape_refresh_queue = Deque()
        self.mp_transfer_queue = Deque()

        self.lock = Lock()

        self.strmurlgetter = StrmUrlGetter()

    @staticmethod
    def get_share_code(config: ShareStrmConfig) -> ShareStrmConfig:
        """
        解析分享配置，获取分享码和提取码
        """
        if config.share_link:
            data = share_extract_payload(config.share_link)
            share_code = data["share_code"]
            receive_code = data["receive_code"]
            logger.info(
                f"【分享STRM生成】解析分享链接 share_code={share_code} receive_code={receive_code}"
            )
        else:
            if not config.share_code or not config.share_receive:
                return config
            share_code = config.share_code
            receive_code = config.share_receive
        config.share_code = share_code
        config.share_receive = receive_code
        return config

    def scrape_refresh_media(self, config: ShareStrmConfig) -> None:
        """
        刮削媒体 & 刷新媒体服务器

        :param config: 分享 STRM 生成配置
        """
        media_server_refresh = MediaServerRefresh(
            func_name="【分享STRM生成】",
            enabled=config.media_server_refresh,
            mp_mediaserver=configer.share_strm_mp_mediaserver_paths,
            mediaservers=configer.share_strm_mediaservers,
        )

        def _refresh_media_server(file_path: Path) -> None:
            media_server_refresh.refresh_mediaserver(
                file_path=file_path.as_posix(), file_name=file_path.name
            )

        def _scrape_media_data(file_path: Path) -> None:
            logger.info(f"【分享STRM生成】{file_path.as_posix()} 开始刮削...")
            media_scrape_metadata(file_path.as_posix())

        def _scrape_and_refresh(file_path: Path) -> None:
            logger.info(f"【分享STRM生成】{file_path.as_posix()} 开始刮削...")
            _scrape_media_data(file_path)
            _refresh_media_server(file_path)

        if config.scrape_metadata and config.media_server_refresh:
            func = _scrape_and_refresh
        elif config.scrape_metadata:
            func = _scrape_media_data
        elif config.media_server_refresh:
            func = _refresh_media_server
        else:
            return

        while len(self.scrape_refresh_queue) != 0:
            path = self.scrape_refresh_queue.popleft()
            func(Path(path))

    def mp_transfer(self) -> None:
        """
        交由 MoviePilot 整理文件
        """
        transfer_chain = TransferChain()
        while len(self.mp_transfer_queue) != 0:
            path = Path(self.mp_transfer_queue.popleft())
            transfer_chain.do_transfer(
                fileitem=FileItem(
                    storage="local",
                    type="file",
                    path=path.as_posix(),
                    name=path.name,
                    basename=path.stem,
                    extension=path.suffix[1:].lower(),
                    size=path.stat().st_size,
                    modify_time=path.stat().st_mtime,
                )
            )

    def __process_single_item(
        self,
        item: Dict,
        config: ShareStrmConfig,
    ) -> None:
        """
        处理单个 STRM 文件

        :param item: 网盘文件信息
        :param config: 分享 STRM 生成配置
        """
        file_path = item["path"]

        if not PathUtils.has_prefix(file_path, config.share_path):
            logger.debug(
                "【分享STRM生成】此文件不在用户设置分享目录下，跳过分享路径: %s",
                str(file_path).replace(config.local_path, "", 1),
            )
            return

        file_path = Path(config.local_path) / Path(file_path).relative_to(
            config.share_path
        )
        file_target_dir = file_path.parent
        original_file_name = file_path.name
        file_name = StrmGenerater.get_strm_filename(file_path)
        new_file_path = file_target_dir / file_name

        try:
            if config.auto_download_mediainfo:
                if file_path.suffix.lower() in self.download_mediaext:
                    with self.lock:
                        self.download_mediainfo_list.append(
                            {
                                "type": "share",
                                "share_code": config.share_code,
                                "receive_code": config.share_receive,
                                "file_id": item["id"],
                                "path": file_path,
                                "thumb": item.get("thumb", None),
                                "sha1": item["sha1"],
                            }
                        )
                    return

            if file_path.suffix.lower() not in self.rmt_mediaext:
                logger.warn(
                    "【分享STRM生成】文件后缀不匹配，跳过分享路径: %s",
                    str(file_path).replace(config.local_path, "", 1),
                )
                return

            if not (
                result := StrmGenerater.should_generate_strm(
                    original_file_name,
                    mode="share",
                    filesize=CompareMinSize(
                        min_size=config.min_file_size, file_size=item["size"]
                    ),
                )
            )[1]:
                logger.warn(
                    f"【分享STRM生成】{result[0]}，跳过分享路径: {str(file_path).replace(config.local_path, '', 1)}"
                )
                return

            if not item["id"]:
                logger.error(
                    f"【分享STRM生成】{original_file_name} 不存在 id 值，无法生成 STRM 文件"
                )
                self.strm_fail_dict[str(new_file_path)] = "不存在 id 值"
                self.strm_fail_count += 1
                return

            new_file_path.parent.mkdir(parents=True, exist_ok=True)

            strm_url = self.strmurlgetter.get_share_strm_url(
                config.share_code,
                config.share_receive,
                item["id"],
                item["name"],
                item["path"],
            )

            with open(new_file_path, "w", encoding="utf-8") as file:
                file.write(strm_url)
            self.strm_count += 1
            logger.info("【分享STRM生成】生成 STRM 文件成功: %s", str(new_file_path))

            if config.moviepilot_transfer:
                self.mp_transfer_queue.append(new_file_path)

            if config.media_server_refresh or config.scrape_metadata:
                self.scrape_refresh_queue.append(new_file_path)
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

    def generate_strm_files(self) -> None:
        """
        获取分享文件，生成 STRM
        """
        if not configer.share_strm_config:
            return

        for config in configer.share_strm_config:
            config = ShareStrmHelper.get_share_code(config)

            if not config.share_code or not config.share_receive:
                logger.error(f"【分享STRM生成】缺失分享码或提取码: {config}")
                continue

            start_time = perf_counter()
            for batch in batched(
                iter_share_files_with_path(
                    client=self.client,
                    share_code=config.share_code,
                    receive_code=config.share_receive,
                    cid=0,
                    speed_mode=config.speed_mode,
                ),
                1_000,
            ):
                self.total_count += len(batch)
                with ThreadPoolExecutor(max_workers=128) as executor:
                    future_to_item = {
                        executor.submit(
                            self.__process_single_item,
                            item=item,
                            config=config,
                        ): item
                        for item in batch
                    }

                    for future in as_completed(future_to_item):
                        item = future_to_item[future]
                        try:
                            future.result()
                        except Exception as e:
                            sentry_manager.sentry_hub.capture_exception(e)
                            logger.error(
                                f"【分享STRM生成】并发处理出错: {item} - {str(e)}"
                            )

            end_time = perf_counter()
            self.elapsed_time += end_time - start_time

            self.scrape_refresh_media(config)

            if config.moviepilot_transfer:
                self.mp_transfer()

        self.mediainfo_count, self.mediainfo_fail_count, self.mediainfo_fail_dict = (
            self.mediainfodownloader.batch_auto_share_downloader(
                downloads_list=self.download_mediainfo_list
            )
        )

    def get_generate_total(self) -> Tuple[int, int, int, int]:
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
