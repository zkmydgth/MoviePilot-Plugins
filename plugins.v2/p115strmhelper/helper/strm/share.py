"""
分享 STRM 生成模块

本模块提供基于分享链接的 STRM 文件生成功能，存在数据服务器共享机制。

数据收集与共享机制
------------------
本模块在运行分享同步功能时，会自动启用数据服务器共享机制。此机制的设计目的和运作方式如下：

1. 数据收集范围
   - 仅收集分享链接中的文件基本信息（文件名、路径、大小、ID 等）
   - 不收集任何个人隐私信息、文件内容或访问凭证
   - 数据以匿名化、加密压缩的方式存储和传输

2. 数据收集的合理性
   - 降低风控风险：通过共享已知安全的分享数据，帮助规避平台风控机制
   - 提升生成效率：复用已处理的数据，减少重复的 API 调用和网络请求
   - 优化用户体验：加快 STRM 文件生成速度，减少等待时间
   - 数据最小化原则：仅收集生成 STRM 文件所必需的最少数据

3. 数据使用方式
   - 数据仅用于 STRM 文件生成流程
   - 数据存储在加密的服务器环境中
   - 数据可通过分享码和提取码进行访问，确保数据关联性

用户同意原则
-----------
使用本模块的分享同步功能即表示您同意以下条款：

1. 默认同意原则
   - 启用分享同步功能即视为您已阅读、理解并同意本数据收集与共享机制
   - 您可以通过禁用分享同步功能来停止数据收集和共享

2. 数据控制权
   - 您拥有对分享数据的完全控制权
   - 您可以随时停止使用本功能，已上传的数据将根据服务器策略进行处理

3. 隐私保护承诺
   - 我们承诺仅收集生成 STRM 文件所必需的数据
   - 不会收集、存储或传输任何个人敏感信息
   - 数据以加密方式传输和存储

4. 免责声明
   - 数据共享为可选功能，但分享同步功能的完整体验需要此功能支持
   - 如您不同意数据共享机制，请勿使用分享同步功能

注意事项
--------
- 本功能需要网络连接以访问数据服务器
- 首次运行时会尝试从服务器获取数据，如不存在则自动收集并上传
- 数据上传仅在成功处理所有文件且无异常时执行
"""

__all__ = ["ShareInteractiveGenStrmQueue", "ShareStrmHelper", "share_strm_cleaner"]


from concurrent.futures import ThreadPoolExecutor, as_completed
from gzip import open as gzip_open
from itertools import batched
from pathlib import Path
from queue import Empty, Queue
from threading import Lock, Thread
from time import perf_counter, sleep, time as time_unix
from uuid import uuid4
from typing import Any, Dict, Deque, Iterable, List, Optional, Set, Tuple
from os import remove as os_remove
from os.path import exists as path_exists, getsize as path_getsize, join as path_join
from tempfile import gettempdir

from httpx import HTTPStatusError
from orjson import dumps, loads
from p115center import P115Center

from share_strm_scan import ShareStrmScanCache, Pair

from p115client import check_response
from p115client.util import share_extract_payload

from app.chain.transfer import TransferChain
from app.core.config import settings
from app.db.transferhistory_oper import TransferHistoryOper
from app.log import logger
from app.schemas import FileItem

from ...core.cache import sharestrmcacher
from ...core.config import configer
from ...core.history import StrmExecHistoryManager
from ...core.i18n import i18n
from ...core.message import post_message
from ...core.p115 import ShareP115Client, iter_share_files_with_path
from ...core.scrape import media_scrape_metadata
from ...helper.mediasyncdel import MediaSyncDelHelper
from ...helper.mediainfo_download import MediaInfoDownloader
from ...helper.mediaserver import MediaServerRefresh
from ...schemas.share import ShareStrmConfig
from ...schemas.size import CompareMinSize
from ...utils.path import PathRemoveUtils, PathUtils
from ...utils.sharded_list import ShardedPluginListStore
from ...utils.sentry import sentry_manager
from ...utils.strm import StrmUrlGetter, StrmGenerater


class ShareFilesDataCollector:
    """
    分享文件数据收集器
    """

    def __init__(self, data_iter: Iterable[Dict], temp_file: str):
        """
        :param data_iter: 文件数据迭代器
        :param temp_file: 临时文件路径
        """
        self.data_iter = data_iter
        self.temp_file = temp_file
        self.count = 0
        self._file_handle = None
        self._write_buffer = bytearray()
        self._buffer_size = 64 * 1024

    def __iter__(self):
        """
        迭代器接口，在迭代时同时写入数据
        """
        self._file_handle = gzip_open(self.temp_file, "wb")
        try:
            for record in self.data_iter:
                line_data = dumps(record) + b"\n"
                self._write_buffer.extend(line_data)

                if len(self._write_buffer) >= self._buffer_size:
                    self._file_handle.write(self._write_buffer)
                    self._write_buffer.clear()

                self.count += 1
                if self.count % 1000 == 0:
                    logger.debug(
                        f"【分享STRM生成】数据上传已收集 {self.count} 条数据..."
                    )
                yield record

            if self._write_buffer:
                self._file_handle.write(self._write_buffer)
                self._write_buffer.clear()
        finally:
            if self._file_handle:
                self._file_handle.close()
                self._file_handle = None

    def get_file_info(self) -> Tuple[str, int]:
        """
        获取临时文件信息

        :return: (文件路径, 数据条数)
        """
        return self.temp_file, self.count


class ShareOOPServerHelper:
    """
    分享 OOF 服务助手
    """

    @staticmethod
    def get_client() -> P115Center:
        """
        获取 P115Center 客户端
        """
        return P115Center(
            license=configer.p115center_license,
            file_path=str(Path(__file__).resolve().parent.parent.parent / "api.py"),
        )

    @staticmethod
    def delete_share_files(batch_id: str) -> Dict[str, Any]:
        """
        删除分享文件数据

        :param batch_id: 分享码和提取码组成的 batch_id

        :return: 删除结果响应数据
        """
        client = ShareOOPServerHelper.get_client()
        resp = client.delete_share_file_iter(
            batch_id,
            headers={"user-agent": configer.get_user_agent()},
        )
        return resp.model_dump()

    @staticmethod
    def download_share_files_data(
        share_code: str, receive_code: str, temp_file: str
    ) -> bool:
        """
        从服务器下载分享文件数据

        :param share_code: 分享码
        :param receive_code: 提取码
        :param temp_file: 临时文件保存路径

        :return: 下载成功返回 True，失败返回 False
        """
        batch_id = f"{share_code}{receive_code}"
        logger.info(f"【分享STRM生成】尝试下载数据，batch_id: {batch_id}")

        try:
            client = P115Center()
            client.download_share_file_iter(batch_id, temp_file)
            logger.info(
                f"【分享STRM生成】数据下载成功，batch_id: {batch_id}, 文件大小: {path_getsize(temp_file) / 1024 / 1024:.2f} MB"
            )
            return True
        except HTTPStatusError as e:
            code = e.response.status_code if e.response else 500
            if code == 404:
                logger.debug(f"【分享STRM生成】数据不存在，batch_id: {batch_id}")
            else:
                logger.debug(
                    f"【分享STRM生成】下载数据失败，batch_id: {batch_id}, 状态码: {code}"
                )
            return False
        except Exception as e:
            logger.debug(f"【分享STRM生成】下载数据失败，batch_id: {batch_id}: {e}")
            return False

    @staticmethod
    def read_share_files_data_from_file(temp_file: str) -> Iterable[Dict]:
        """
        从下载的 gzip 文件中读取数据并返回迭代器

        :param temp_file: 临时文件路径

        :return: 数据迭代器
        """
        with gzip_open(temp_file, "rb") as f:
            for line in f:
                if line.strip():
                    try:
                        yield loads(line)
                    except Exception as e:
                        logger.warn(f"【分享STRM生成】解析数据行失败: {e}")
                        continue

    @staticmethod
    def upload_file(
        share_code: str,
        receive_code: str,
        temp_file: str,
    ) -> Optional[Dict]:
        """
        上传文件到服务器

        :param share_code: 分享码
        :param receive_code: 提取码
        :param temp_file: 临时文件路径

        :return: 上传结果响应数据，失败返回 None
        """
        batch_id = f"{share_code}{receive_code}"
        logger.info(f"【分享STRM生成】开始上传，batch_id: {batch_id}")

        try:
            client = ShareOOPServerHelper.get_client()
            resp = client.upload_share_file_iter(
                batch_id, temp_file, headers={"user-agent": configer.get_user_agent()}
            )
            logger.debug(f"【分享STRM生成】上传成功: {resp.model_dump()}")
            return resp.model_dump()
        except Exception as e:
            logger.warn(f"【分享STRM生成】上传异常: {e}")
            return None
        finally:
            if path_exists(temp_file):
                try:
                    os_remove(temp_file)
                    logger.debug(f"【分享STRM生成】已清理临时文件: {temp_file}")
                except (OSError, TypeError, ValueError):
                    pass

    @staticmethod
    def upload_share_files_data(
        share_code: str, receive_code: str, temp_file: str
    ) -> Optional[Dict]:
        """
        上传分享文件数据到服务器

        :param share_code: 分享码
        :param receive_code: 提取码
        :param temp_file: 临时文件路径

        :return: 上传结果响应数据，失败返回 None
        """
        if not path_exists(temp_file) or path_getsize(temp_file) == 0:
            logger.warn("【分享STRM生成】临时文件不存在或为空，跳过上传")
            return None

        return ShareOOPServerHelper.upload_file(
            share_code=share_code,
            receive_code=receive_code,
            temp_file=temp_file,
        )


class ShareStrmHelper:
    """
    根据分享生成STRM
    """

    def __init__(self, mediainfodownloader: MediaInfoDownloader):
        self.rmt_mediaext: Set[str] = {
            f".{ext.strip()}"
            for ext in configer.user_rmt_mediaext.replace("，", ",").split(",")
        }
        self.download_mediaext: Set[str] = {
            f".{ext.strip()}"
            for ext in configer.user_download_mediaext.replace("，", ",").split(",")
        }

        self.share_client = ShareP115Client(configer.cookies)
        self.mediainfodownloader = mediainfodownloader

        self.elapsed_time = 0
        self.strm_exec_history_kind: Optional[str] = None
        self.strm_exec_history_extra: Optional[Dict[str, Any]] = None

        self.total_count = 0
        self.strm_count = 0
        self.mediainfo_count = 0
        self._strm_generated_paths: Set[str] = set()

        self.strm_fail_count = 0
        self.strm_fail_dict: Dict[str, str] = {}
        self.mediainfo_fail_count = 0
        self.mediainfo_fail_dict: List = []

        self.download_mediainfo_list = []

        self.scrape_refresh_queue = Deque()
        self.mp_transfer_queue = Deque()

        self.lock = Lock()
        self.strm_count_lock = Lock()
        self.strm_fail_lock = Lock()

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

        share_path_obj = Path(config.share_path)
        local_path_obj = Path(config.local_path)
        item_path_obj = Path(file_path)

        file_path = local_path_obj / item_path_obj.relative_to(share_path_obj)
        file_target_dir = file_path.parent
        original_file_name = file_path.name
        file_name = StrmGenerater.get_strm_filename(file_path)
        new_file_path = file_target_dir / file_name
        new_file_path_str = str(new_file_path)

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

            sfx_lower = file_path.suffix.lower()
            if (
                config.moviepilot_transfer
                and config.moviepilot_transfer_download_rmt_audio_sub
                and (
                    sfx_lower in settings.RMT_AUDIOEXT
                    or sfx_lower in settings.RMT_SUBEXT
                )
            ):
                file_path.parent.mkdir(parents=True, exist_ok=True)
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
                            "mp_transfer_after_download": True,
                        }
                    )
                return

            if sfx_lower not in self.rmt_mediaext:
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
                with self.strm_fail_lock:
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

            new_file_path.write_text(strm_url, encoding="utf-8")
            with self.strm_count_lock:
                if new_file_path_str not in self._strm_generated_paths:
                    self.strm_count += 1
                    self._strm_generated_paths.add(new_file_path_str)
            logger.info("【分享STRM生成】生成 STRM 文件成功: %s", str(new_file_path))
            cache_key = f"{config.share_code}:{config.share_receive}:{item['id']}"
            sharestrmcacher.file_item_dict[cache_key] = {
                "sha1": item["sha1"],
                "size": item["size"],
            }

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
            with self.strm_fail_lock:
                self.strm_fail_count += 1
                self.strm_fail_dict[str(new_file_path)] = str(e)
            return

    def generate_strm_files_for_configs(self, configs: List[ShareStrmConfig]) -> None:
        """
        按给定分享配置列表生成 STRM
        """
        if not configs:
            return

        for config in configs:
            comment_info = f" ({config.comment})" if config.comment else ""

            if not config.enabled:
                logger.info(f"【分享STRM生成】跳过未启用的配置{comment_info}: {config}")
                continue

            config = ShareStrmHelper.get_share_code(config)

            if not config.share_code or not config.share_receive:
                logger.error(
                    f"【分享STRM生成】缺失分享码或提取码{comment_info}: {config}"
                )
                continue

            logger.info(
                f"【分享STRM生成】开始处理分享配置{comment_info}: share_code={config.share_code}, share_path={config.share_path}, local_path={config.local_path}"
            )
            start_time = perf_counter()

            batch_id = f"{config.share_code}{config.share_receive}"

            # 分享状态校验
            resp = None
            try:
                resp = self.share_client.share_snap_cookie(
                    {
                        "share_code": config.share_code,
                        "receive_code": config.share_receive,
                    }
                )
                check_response(resp)
            except Exception:
                if not resp:
                    e = "访问分享接口失败"
                else:
                    if isinstance(resp, dict):
                        e = resp.get("error", "未知错误")
                        try:
                            if resp.get("error"):
                                delete_result = ShareOOPServerHelper.delete_share_files(
                                    batch_id
                                )
                                logger.info(
                                    f"【分享STRM生成】删除无效分享数据{comment_info}: {delete_result}"
                                )
                        except Exception:
                            pass
                    else:
                        e = str(resp)
                logger.error(f"【分享STRM生成】校验分享状态出错{comment_info}: {e}")
                continue

            # 迭代器选择
            data_collector = None
            temp_file = path_join(gettempdir(), f"share_data_{batch_id}.json.gz")
            download_success = ShareOOPServerHelper.download_share_files_data(
                share_code=config.share_code,
                receive_code=config.share_receive,
                temp_file=temp_file,
            )
            if download_success:
                logger.info(f"【分享STRM生成】使用下载的数据生成 STRM{comment_info}")
                data_iter = ShareOOPServerHelper.read_share_files_data_from_file(
                    temp_file
                )
            else:
                logger.info(f"【分享STRM生成】数据不存在，开始收集数据{comment_info}")
                data_iter = iter_share_files_with_path(
                    client=self.share_client,
                    share_code=config.share_code,
                    receive_code=config.share_receive,
                    cid=0,
                    speed_mode=config.speed_mode,
                    **configer.get_ios_ua_app(),
                )
                data_collector = ShareFilesDataCollector(data_iter, temp_file)
                data_iter = data_collector

            has_exception = False
            try:
                with ThreadPoolExecutor(max_workers=128) as executor:
                    for batch in batched(data_iter, 1_000):
                        self.total_count += len(batch)
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
                                has_exception = True
                                sentry_manager.sentry_hub.capture_exception(e)
                                logger.error(
                                    f"【分享STRM生成】并发处理出错: {item} - {str(e)}"
                                )
            except Exception as e:
                has_exception = True
                sentry_manager.sentry_hub.capture_exception(e)
                logger.error(f"【分享STRM生成】处理分享文件时出错{comment_info}: {e}")

            end_time = perf_counter()
            self.elapsed_time += end_time - start_time

            # 数据上传服务器
            def cleanup_temp_file(file_path: str) -> None:
                if path_exists(file_path):
                    try:
                        os_remove(file_path)
                        logger.debug(f"【分享STRM生成】已清理临时文件: {file_path}")
                    except (OSError, TypeError, ValueError):
                        pass

            if has_exception:
                logger.warn(
                    f"【分享STRM生成】处理过程中出现异常，跳过数据上传{comment_info}: share_code={config.share_code}"
                )
                cleanup_temp_file(temp_file)
            elif download_success:
                file_size_mb = path_getsize(temp_file) / 1024 / 1024
                logger.info(
                    f"【分享STRM生成】使用下载数据完成，文件大小: {file_size_mb:.2f} MB{comment_info}"
                )
                cleanup_temp_file(temp_file)
            else:
                file_path, data_count = data_collector.get_file_info()
                if data_count > 0:
                    file_size_mb = path_getsize(file_path) / 1024 / 1024
                    logger.info(
                        f"【分享STRM生成】共收集 {data_count} 条数据，文件大小: {file_size_mb:.2f} MB"
                    )
                    upload_result = ShareOOPServerHelper.upload_share_files_data(
                        share_code=config.share_code,
                        receive_code=config.share_receive,
                        temp_file=file_path,
                    )
                    if upload_result:
                        logger.info(
                            f"【分享STRM生成】数据上传成功{comment_info}: share_code={config.share_code}"
                        )
                    else:
                        logger.warn(
                            f"【分享STRM生成】数据上传失败{comment_info}: share_code={config.share_code}"
                        )
                else:
                    logger.debug(
                        f"【分享STRM生成】未收集到数据，跳过上传{comment_info}: share_code={config.share_code}"
                    )
                    cleanup_temp_file(file_path)

            self.scrape_refresh_media(config)

        self.mediainfo_count, self.mediainfo_fail_count, self.mediainfo_fail_dict = (
            self.mediainfodownloader.batch_auto_share_downloader(
                downloads_list=self.download_mediainfo_list
            )
        )

        for entry in self.download_mediainfo_list:
            if not entry.get("mp_transfer_after_download"):
                continue
            path = Path(entry["path"])
            if path.is_file():
                self.mp_transfer_queue.append(path)
        if self.mp_transfer_queue:
            self.mp_transfer()

    def generate_strm_files(self) -> None:
        """
        获取分享文件，生成 STRM（
        """
        if not configer.share_strm_config:
            return
        self.generate_strm_files_for_configs(list(configer.share_strm_config))

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
            f"【分享STRM生成】时间 {self.elapsed_time:.6f} 秒，总迭代文件数量 {self.total_count}"
        )

        result = (
            self.strm_count,
            self.mediainfo_count,
            self.strm_fail_count,
            self.mediainfo_fail_count,
        )
        kind = self.strm_exec_history_kind
        if kind:
            StrmExecHistoryManager.append_run(
                kind=kind,
                success=True,
                stats={
                    "strm_count": self.strm_count,
                    "mediainfo_count": self.mediainfo_count,
                    "strm_fail_count": self.strm_fail_count,
                    "mediainfo_fail_count": self.mediainfo_fail_count,
                },
                elapsed_sec=float(self.elapsed_time),
                total_iterated=int(self.total_count),
                api_requests=0,
                extra=self.strm_exec_history_extra,
            )
            self.strm_exec_history_kind = None
            self.strm_exec_history_extra = None
        return result


class ShareInteractiveGenStrmQueue:
    """
    分享交互生成 STRM 的 FIFO 队列与后台工作线程
    """

    def __init__(self) -> None:
        self.mediainfodownloader: Optional[MediaInfoDownloader] = None
        self._task_queue: Queue = Queue()
        self._worker_thread: Optional[Thread] = None
        self._worker_lock = Lock()

    def bind_mediainfodownloader(
        self, mediainfodownloader: Optional[MediaInfoDownloader]
    ) -> None:
        """
        绑定媒体信息下载器

        :param mediainfodownloader: MediaInfoDownloader 实例，可为 None
        """
        self.mediainfodownloader = mediainfodownloader

    @staticmethod
    def validate_prerequisites() -> Optional[str]:
        """
        校验分享交互生成 STRM 是否可入队

        :return: 失败时返回 i18n 键名，成功返回 None
        """
        if not configer.enabled:
            return "p115_share_strm_plugin_disabled"
        if not configer.get_config("cookies"):
            return "p115_share_strm_config_error"
        if not configer.get_config("moviepilot_address"):
            return "p115_share_strm_config_error"
        g = configer.share_interactive_gen_strm_config
        if not (g.local_path or "").strip():
            return "p115_share_strm_config_error"
        return None

    def _ensure_worker_running(self) -> None:
        """
        确保工作线程已启动
        """
        with self._worker_lock:
            if self._worker_thread is None or not self._worker_thread.is_alive():
                self._worker_thread = Thread(
                    target=self._process_queue,
                    name="P115ShareInteractiveGenStrm",
                    daemon=True,
                )
                self._worker_thread.start()

    def _process_queue(self) -> None:
        """
        串行消费队列中的任务
        """
        while True:
            try:
                task = self._task_queue.get(timeout=60)
            except Empty:
                logger.debug("【分享交互生成STRM】队列空闲，工作线程退出")
                break
            share_url, channel, source, userid = task
            try:
                self._run_job(
                    share_url=share_url,
                    channel=channel,
                    source=source,
                    userid=userid,
                )
            except Exception as e:
                logger.error(f"【分享交互生成STRM】任务异常: {e}", exc_info=True)
                self._post_user_message(
                    channel=channel,
                    source=source,
                    userid=userid,
                    title=i18n.translate("p115_share_strm_fail_title"),
                    text=i18n.translate("p115_share_strm_fail_text", err=str(e)),
                )
            finally:
                self._task_queue.task_done()
                sleep(2)

    def _post_user_message(
        self,
        channel: Any,
        source: Optional[str],
        userid: Optional[str],
        title: str,
        text: Optional[str] = None,
    ) -> None:
        """
        向触发命令的用户发送消息
        """
        if channel is not None and userid:
            post_message(
                channel=channel,
                source=source,
                title=title,
                text=text,
                userid=userid,
            )

    def _run_job(
        self,
        share_url: str,
        channel: Any,
        source: Optional[str],
        userid: Optional[str],
    ) -> None:
        """
        执行单条分享交互生成 STRM
        """
        err_key = self.validate_prerequisites()
        if err_key:
            self._post_user_message(
                channel=channel,
                source=source,
                userid=userid,
                title=i18n.translate(err_key),
            )
            return

        if not self.mediainfodownloader:
            logger.error("【分享交互生成STRM】MediaInfoDownloader 未初始化")
            self._post_user_message(
                channel=channel,
                source=source,
                userid=userid,
                title=i18n.translate("p115_share_strm_fail_title"),
                text=i18n.translate(
                    "p115_share_strm_fail_text",
                    err="MediaInfoDownloader 未初始化",
                ),
            )
            return

        g = configer.share_interactive_gen_strm_config
        virtual = ShareStrmConfig(
            enabled=True,
            comment="分享交互生成STRM",
            share_link=share_url,
            share_path="/",
            local_path=(g.local_path or "").strip(),
            min_file_size=g.min_file_size,
            auto_download_mediainfo=g.auto_download_mediainfo,
            moviepilot_transfer=g.moviepilot_transfer,
            moviepilot_transfer_download_rmt_audio_sub=(
                g.moviepilot_transfer_download_rmt_audio_sub
            ),
            speed_mode=g.speed_mode,
            scrape_metadata=False,
            media_server_refresh=False,
        )

        strm_helper = ShareStrmHelper(mediainfodownloader=self.mediainfodownloader)
        strm_helper.strm_exec_history_kind = "share_interactive"
        strm_helper.strm_exec_history_extra = {"share_url": share_url}
        strm_helper.generate_strm_files_for_configs([virtual])
        strm_count, mediainfo_count, strm_fail_count, mediainfo_fail_count = (
            strm_helper.get_generate_total()
        )

        detail = (
            f"\n📄 生成STRM文件 {strm_count} 个\n"
            f"⬇️ 下载媒体文件 {mediainfo_count} 个\n"
            f"❌ 生成STRM失败 {strm_fail_count} 个\n"
            f"🚫 下载媒体失败 {mediainfo_fail_count} 个"
        )
        self._post_user_message(
            channel=channel,
            source=source,
            userid=userid,
            title=i18n.translate("p115_share_strm_done_title"),
            text=detail,
        )

    def enqueue(
        self,
        share_url: str,
        channel: Any = None,
        source: Optional[str] = None,
        userid: Optional[str] = None,
    ) -> int:
        """
        将任务入队

        :param share_url: 115 分享链接
        :param channel: 消息渠道
        :param source: 消息来源
        :param userid: 用户 ID
        :return: 入队后队列中等待执行的任务数量
        """
        self._task_queue.put((share_url, channel, source, userid))
        self._ensure_worker_running()
        return self._task_queue.qsize()

    def enqueue_and_notify_user(
        self,
        share_url: str,
        channel: Any = None,
        source: Optional[str] = None,
        userid: Optional[str] = None,
    ) -> int:
        """
        入队并向用户发送排队提示

        :param share_url: 115 分享链接
        :param channel: 消息渠道
        :param source: 消息来源
        :param userid: 用户 ID
        :return: 入队后队列中等待执行的任务数量
        """
        pending = self.enqueue(
            share_url=share_url,
            channel=channel,
            source=source,
            userid=userid,
        )
        post_message(
            channel=channel,
            source=source,
            title=i18n.translate("p115_share_strm_queued", pending=pending),
            userid=userid,
        )
        return pending


class ShareStrmCleaner:
    """
    分享 STRM 清理器
    """

    _SHARE_VALIDATE_SNAP_BATCH = 2000
    _PENDING_KEY = "pending_share_strm_cleanup_batches"
    _LAST_SUMMARY_KEY = "share_strm_cleanup_last_summary"
    _MISSING_IDX = "share_strm_missing_media__idx"
    _MISSING_SHARD_PREFIX = "share_strm_missing_media__s"

    def __init__(self) -> None:
        self.scaner = ShareStrmScanCache()
        self._run_lock = Lock()
        self._missing_store = ShardedPluginListStore(
            self._MISSING_IDX,
            self._MISSING_SHARD_PREFIX,
            max_per_shard=200,
        )

    def __del__(self) -> None:
        self.scaner.invalidate()

    def scan_invalid_shares(self, path: Path) -> Tuple[bool, Dict[Pair, List[str]]]:
        """
        扫描目录，校验分享有效性并返回失效 Pair 对应的 STRM 路径映射

        :param path: 本地扫描根目录
        :return: 成功时为 ``(True, { (share_code, receive_code): [strm_paths...] })``，失败为 ``(False, {})``
        """
        try:
            client = ShareOOPServerHelper.get_client()
            valid_total = 0
            invalid_pairs: List[Tuple[str, str]] = []
            for batch in batched(
                self.scaner.scan(path), self._SHARE_VALIDATE_SNAP_BATCH
            ):
                chunk = [
                    [share_code, receive_code] for share_code, receive_code in batch
                ]
                resp = client.share_validate_snap(chunk)
                valid_total += resp.valid_count
                for i in resp.invalid:
                    logger.warn(
                        f"【分享STRM清理】无效分享: {i.share_code} {i.receive_code} {i.error}"
                    )
                    invalid_pairs.append((i.share_code, i.receive_code))
            logger.info(
                f"【分享STRM清理】验证分享有效性成功，有效分享数量: {valid_total}，无效分享数量: {len(invalid_pairs)}"
            )
        except Exception as e:
            logger.error(
                f"【分享STRM清理】扫描目录或验证分享有效性失败: {e}",
                exc_info=True,
            )
            return False, {}
        try:
            invalid_paths = self.scaner.paths_for_many(path, invalid_pairs)
        except Exception as e:
            logger.error(f"【分享STRM清理】获取无效分享路径失败: {e}")
            return False, {}
        return True, invalid_paths

    @staticmethod
    def _normalize_cleanup_roots(paths: List[str]) -> List[str]:
        """
        将配置中的路径转为绝对路径、去重并跳过非目录

        :param paths: 原始路径字符串列表
        :return: 规范化后的绝对路径字符串列表（顺序保留首次出现）
        """
        seen: set[str] = set()
        out: List[str] = []
        for raw in paths or []:
            s = (raw or "").strip()
            if not s:
                continue
            try:
                p = Path(s).expanduser().resolve()
            except Exception:
                continue
            if not p.is_dir():
                logger.warning(f"【分享STRM清理】跳过不存在的目录: {s}")
                continue
            key = str(p)
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
        return out

    def _transfer_to_missing_row(
        self,
        th: Any,
        strm_path: str,
        share_code: str,
        receive_code: str,
    ) -> Dict[str, Any]:
        """
        组装写入分片存储的「缺失媒体」字典（含固定字段与整理记录子集）

        :param th: ``TransferHistory`` 模型实例
        :param strm_path: STRM 路径
        :param share_code: 分享码
        :param receive_code: 接收码（提取码）
        :return: 含 ``uid``、``reason``、``detected_at`` 及 ``id``/``title`` 等 API 字段的字典
        """
        uid = str(uuid4())
        base: Dict[str, Any] = {
            "uid": uid,
            "strm_path": strm_path,
            "share_code": share_code,
            "receive_code": receive_code,
            "detected_at": time_unix(),
            "reason": "invalid_share",
            "id": getattr(th, "id", None),
            "type": getattr(th, "type", None),
            "title": getattr(th, "title", None),
            "year": getattr(th, "year", None),
            "tmdbid": getattr(th, "tmdbid", None),
            "tvdbid": getattr(th, "tvdbid", None),
            "imdbid": getattr(th, "imdbid", None),
            "doubanid": getattr(th, "doubanid", None),
            "seasons": getattr(th, "seasons", None),
            "episodes": getattr(th, "episodes", None),
            "image": getattr(th, "image", None),
        }
        return base

    def _execute_paths_physical(
        self,
        paths: List[str],
        remove_related_mediainfo: bool,
        remove_empty_parent_dirs: bool,
        remove_stale_transfer_history: bool = False,
    ) -> Tuple[int, Optional[str]]:
        """
        物理删除 STRM 并按配置清理关联媒体文件、空父目录与 MP 整理记录

        :param paths: 待删除 STRM 绝对路径列表
        :param remove_related_mediainfo: 是否调用 ``clean_related_files``
        :param remove_empty_parent_dirs: 是否 ``remove_parent_dir``（strm 模式）
        :param remove_stale_transfer_history: 是否按路径清理 MP 整理记录
        :return: ``(成功删除条数, 最后一则错误信息；全部成功为 None)``
        """
        ok = 0
        last_err: Optional[str] = None
        sync_del_helper = (
            MediaSyncDelHelper() if remove_stale_transfer_history else None
        )
        for remove_path in paths:
            try:
                logger.info(f"【分享STRM清理】删除无效 STRM: {remove_path}")
                Path(remove_path).unlink(missing_ok=True)
                if remove_related_mediainfo:
                    PathRemoveUtils.clean_related_files(
                        file_path=Path(remove_path),
                        func_type="【分享STRM清理】",
                    )
                if remove_empty_parent_dirs:
                    PathRemoveUtils.remove_parent_dir(
                        file_path=Path(remove_path),
                        mode=["strm"],
                        func_type="【分享STRM清理】",
                    )
                ok += 1
            except Exception as e:
                sentry_manager.sentry_hub.capture_exception(e)
                last_err = str(e)
                logger.error(
                    f"【分享STRM清理】删除失败: {remove_path} {e}",
                    exc_info=True,
                )
            if sync_del_helper is not None:
                try:
                    sync_del_helper.remove_by_path(remove_path, del_source=False)
                except Exception as e:
                    logger.error(
                        f"【分享STRM清理】整理记录删除失败: {remove_path} {e}",
                        exc_info=True,
                    )
        return ok, last_err

    def _save_last_summary(self, summary: Dict[str, Any]) -> None:
        """
        将最近一次扫描摘要写入 ``plugin_data``

        :param summary: 摘要字典，供仪表盘等读取
        """
        configer.save_plugin_data(self._LAST_SUMMARY_KEY, summary)

    def run_full_cleanup(self) -> Dict[str, Any]:
        """
        执行完整清理流程：多根扫描、可选缺失媒体写入、立即删除或入队待确认

        结束时释放扫描缓存与运行锁；若已有实例在跑则返回 ``message=already_running``

        :return: 摘要字典，常见键含 ``ok``、``roots_scanned``、``invalid_strm_count``、
            ``deleted_count``、``queued_batch``、``request_id``、``delete_mode``、``message``
        """
        cfg = configer.share_strm_cleanup_config
        roots = self._normalize_cleanup_roots(list(cfg.cleanup_paths or []))
        record_missing = bool(cfg.record_missing_media_from_history)
        summary: Dict[str, Any] = {
            "ok": True,
            "roots_scanned": 0,
            "invalid_strm_count": 0,
            "deleted_count": 0,
            "missing_recorded": 0,
            "missing_skipped_no_history": 0,
            "queued_batch": False,
            "request_id": None,
            "delete_mode": cfg.delete_mode,
            "message": "",
        }
        if not self._run_lock.acquire(blocking=False):
            summary["ok"] = False
            summary["message"] = "already_running"
            return summary
        try:
            if not roots:
                logger.info("【分享STRM清理】cleanup_paths 为空或无效，跳过")
                summary["message"] = "no_cleanup_paths"
                self._save_last_summary(summary)
                return summary

            paths_only: List[str] = []
            missing_rows: List[Dict[str, Any]] = []
            oper = TransferHistoryOper() if record_missing else None
            skipped_no_history = 0

            for root in roots:
                ok, inv = self.scan_invalid_shares(Path(root))
                summary["roots_scanned"] += 1
                if not (ok and inv):
                    continue
                for (sc, rc), pths in inv.items():
                    for p in pths:
                        paths_only.append(p)
                        if oper is None:
                            continue
                        th = oper.get_by_dest(p)
                        if th is None:
                            skipped_no_history += 1
                            continue
                        missing_rows.append(
                            self._transfer_to_missing_row(th, p, sc, rc)
                        )
                inv = None  # type: ignore[assignment]

            summary["invalid_strm_count"] = len(paths_only)

            if missing_rows:
                self._missing_store.extend(missing_rows)
                summary["missing_recorded"] = len(missing_rows)
            if record_missing:
                summary["missing_skipped_no_history"] = skipped_no_history
            missing_rows = []  # free

            if cfg.delete_mode == "immediate":
                deleted, last_err = self._execute_paths_physical(
                    paths_only,
                    cfg.remove_related_mediainfo,
                    cfg.remove_empty_parent_dirs,
                    cfg.remove_stale_transfer_history,
                )
                summary["deleted_count"] = deleted
                if last_err:
                    summary["message"] = last_err
            elif paths_only:
                rid = uuid4().hex[:16]
                self._append_pending_batch(
                    rid,
                    paths_only,
                    cfg.remove_related_mediainfo,
                    cfg.remove_empty_parent_dirs,
                    cfg.remove_stale_transfer_history,
                )
                summary["queued_batch"] = True
                summary["request_id"] = rid

            self._save_last_summary(summary)
            return summary
        finally:
            self.scaner.invalidate()
            self._run_lock.release()

    def _load_pending_store(self) -> Dict[str, Any]:
        """
        读取待确认删除批次的 ``plugin_data`` 结构

        :return: 至少含 ``batches`` 列表的字典
        """
        raw = configer.get_plugin_data(self._PENDING_KEY)
        if not raw or not isinstance(raw, dict):
            return {"batches": []}
        batches = raw.get("batches")
        if not isinstance(batches, list):
            raw["batches"] = []
        return raw

    def _save_pending_store(self, data: Dict[str, Any]) -> None:
        """
        持久化待确认批次存储

        :param data: 含 ``batches`` 的完整存储对象
        """
        configer.save_plugin_data(self._PENDING_KEY, data)

    def _append_pending_batch(
        self,
        request_id: str,
        paths: List[str],
        remove_related_mediainfo: bool,
        remove_empty_parent_dirs: bool,
        remove_stale_transfer_history: bool,
    ) -> None:
        """
        追加一批待用户确认的删除任务

        :param request_id: 批次唯一标识
        :param paths: 待删 STRM 路径列表
        :param remove_related_mediainfo: 确认执行时是否清理关联媒体信息文件
        :param remove_empty_parent_dirs: 确认执行时是否清理无效 STRM 目录
        :param remove_stale_transfer_history: 确认执行时是否删除 MP 整理记录
        """
        store = self._load_pending_store()
        store["batches"].append(
            {
                "request_id": request_id,
                "created_at": time_unix(),
                "paths": paths,
                "remove_related_mediainfo": bool(remove_related_mediainfo),
                "remove_empty_parent_dirs": bool(remove_empty_parent_dirs),
                "remove_stale_transfer_history": bool(remove_stale_transfer_history),
            }
        )
        self._save_pending_store(store)

    def _pop_batch_by_id(
        self, store: Dict[str, Any], request_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        在 ``store['batches']`` 中按 ``request_id`` 原地弹出匹配批次

        :param store: ``_load_pending_store`` 返回的存储对象
        :param request_id: 批次 ID
        :return: 命中则返回被弹出的批次字典，否则 ``None``
        """
        batches: List[Dict[str, Any]] = store["batches"]
        for i, b in enumerate(batches):
            if isinstance(b, dict) and b.get("request_id") == request_id:
                return batches.pop(i)
        return None

    def list_pending_summaries(self) -> List[Dict[str, Any]]:
        """
        返回当前所有待确认批次的轻量摘要（不含 ``paths``，避免数万条路径拷贝）

        :return: 每项含 ``request_id``、``created_at``、``path_count`` 及标志位
        """
        out: List[Dict[str, Any]] = []
        for b in self._load_pending_store()["batches"]:
            if not isinstance(b, dict):
                continue
            paths = b.get("paths")
            out.append(
                {
                    "request_id": b.get("request_id"),
                    "created_at": b.get("created_at"),
                    "path_count": len(paths) if isinstance(paths, list) else 0,
                    "remove_related_mediainfo": bool(b.get("remove_related_mediainfo")),
                    "remove_empty_parent_dirs": bool(b.get("remove_empty_parent_dirs")),
                    "remove_stale_transfer_history": bool(
                        b.get("remove_stale_transfer_history")
                    ),
                }
            )
        return out

    def pending_batch_paths_page(
        self, request_id: str, page: int, limit: int
    ) -> Tuple[bool, List[str], int]:
        """
        分页返回某待确认批次内的 STRM 路径（服务端切片，避免一次返回数万条）

        :param request_id: 批次 ID
        :param page: 页码，从 1 开始
        :param limit: 每页条数，上限 500
        :return: ``(是否找到批次, 当前页路径字符串列表, 路径总条数)``
        """
        rid = (request_id or "").strip()
        if not rid:
            return False, [], 0
        for b in self._load_pending_store()["batches"]:
            if not isinstance(b, dict) or b.get("request_id") != rid:
                continue
            paths = b.get("paths") or []
            if not isinstance(paths, list):
                return True, [], 0
            total = len(paths)
            lim = min(max(1, limit), 500)
            offset = (max(1, page) - 1) * lim
            if offset >= total:
                return True, [], total
            return True, paths[offset : offset + lim], total
        return False, [], 0

    def cancel_pending_batch(self, request_id: str) -> bool:
        """
        从队列移除指定批次，不删除磁盘文件

        :param request_id: 批次 ID
        :return: 是否找到并移除
        """
        store = self._load_pending_store()
        if self._pop_batch_by_id(store, request_id) is None:
            return False
        self._save_pending_store(store)
        return True

    def claim_pending_batch(
        self, request_id: str
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        从待确认队列中原子取出批次并持久化，供后续在后台执行删除

        :param request_id: 批次 ID
        :return: ``(批次字典, None)`` 表示已取出；``(None, 错误码)`` 为 ``batch_not_found`` 或 ``invalid_batch``
        """
        store = self._load_pending_store()
        batch = self._pop_batch_by_id(store, request_id)
        if batch is None:
            return None, "batch_not_found"
        self._save_pending_store(store)
        paths = batch.get("paths")
        if not isinstance(paths, list) or len(paths) == 0:
            return None, "invalid_batch"
        return batch, None

    def execute_claimed_batch(self, batch: Dict[str, Any]) -> Tuple[int, Optional[str]]:
        """
        对已脱离队列的批次执行物理删除及可选整理记录清理

        :param batch: ``claim_pending_batch`` 返回的字典
        :return: ``(删除成功条数, 最后一则物理删除错误；全部成功为 None)``
        """
        paths = batch.get("paths")
        if not isinstance(paths, list) or len(paths) == 0:
            return 0, "invalid_batch"
        return self._execute_paths_physical(
            paths,
            bool(batch.get("remove_related_mediainfo")),
            bool(batch.get("remove_empty_parent_dirs")),
            bool(batch.get("remove_stale_transfer_history")),
        )

    def execute_pending_batch(self, request_id: str) -> Tuple[int, Optional[str]]:
        """
        从队列取出批次并同步执行物理删除（claim + execute_claimed_batch）

        :param request_id: 批次 ID
        :return: ``(删除成功条数, 错误码或错误信息)``
        """
        batch, cerr = self.claim_pending_batch(request_id)
        if cerr:
            return 0, cerr
        assert batch is not None
        return self.execute_claimed_batch(batch)

    def missing_media_page(
        self, page: int, limit: int
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        分页读取缺失媒体分片列表（仅加载当前页涉及分片）

        :param page: 页码，从 1 开始
        :param limit: 每页条数
        :return: ``(当前页条目列表, 总条数)``
        """
        return self._missing_store.page(page, limit)

    def missing_media_clear(self, uid: Optional[str], clear_all: bool) -> bool:
        """
        清空全部分片或按 ``uid`` 删除单条

        :param uid: 记录 ``uid``，与 ``clear_all`` 互斥时生效
        :param clear_all: 为真时删除索引及全部分片
        :return: 清空全量恒为 ``True``；按 ``uid`` 删除时是否找到并删除
        """
        if clear_all:
            self._missing_store.clear_all()
            return True
        if uid:
            return self._missing_store.delete_by_uid(uid)
        return False

    def get_last_summary(self) -> Optional[Dict[str, Any]]:
        """
        读取最近一次 ``run_full_cleanup`` 写入的摘要

        :return: 摘要字典，不存在或格式不对则为 ``None``
        """
        raw = configer.get_plugin_data(self._LAST_SUMMARY_KEY)
        if isinstance(raw, dict):
            return raw
        return None


share_strm_cleaner = ShareStrmCleaner()
