from pathlib import Path
from typing import Dict, Union

from app.chain.storage import StorageChain
from app.core.context import MediaInfo
from app.core.meta import MetaBase
from app.log import logger
from app.schemas import TransferInfo, FileItem
from app.schemas.types import EventType, ChainEventType

from ...core.config import configer
from ...core.scrape import media_scrape_metadata
from ...db_manager.oper import FileDbHelper
from ...helper.mediainfo_download import MediaInfoDownloader
from ...helper.mediaserver import MediaServerRefresh
from ...utils.path import PathUtils
from ...utils.sentry import sentry_manager
from ...utils.strm import StrmUrlGetter, StrmGenerater


class TransferStrmHelper:
    """
    处理事件事件STRM文件生成
    """

    @staticmethod
    def generate_strm_files(
        target_dir: str,
        pan_media_dir: str,
        item_dest_path: Path,
        url: str,
    ):
        """
        依据网盘路径生成 STRM 文件
        """
        try:
            pan_path = item_dest_path.parent.as_posix()
            if PathUtils.has_prefix(pan_path, pan_media_dir):
                pan_path = pan_path[len(pan_media_dir) :].lstrip("/").lstrip("\\")
            file_path = Path(target_dir) / pan_path
            file_name = StrmGenerater.get_strm_filename(Path(item_dest_path.name))
            new_file_path = file_path / file_name
            new_file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(new_file_path, "w", encoding="utf-8") as file:
                file.write(url)
            logger.info(
                "【监控整理STRM生成】生成 STRM 文件成功: %s", str(new_file_path)
            )
            return True, str(new_file_path)
        except Exception as e:
            sentry_manager.sentry_hub.capture_exception(e)
            logger.error(
                "【监控整理STRM生成】生成 %s 文件失败: %s",
                str(new_file_path),  # noqa
                e,
            )
            return False, None

    def do_generate(
        self,
        item: Dict,
        event_type: Union[EventType, ChainEventType],
        mediainfodownloader: MediaInfoDownloader,
    ):
        """
        生成 STRM 操作
        """
        _database_helper = FileDbHelper()
        _get_url = StrmUrlGetter()

        # 转移信息
        item_transfer: TransferInfo = item.get("transferinfo")
        if isinstance(item_transfer, dict):
            item_transfer: TransferInfo = TransferInfo(**item_transfer)
        # 媒体信息
        mediainfo: MediaInfo = item.get("mediainfo")
        # 元数据信息
        meta: MetaBase = item.get("meta")

        # 判断储存类型是否匹配
        if item_transfer.target_item.storage != configer.storage_module:
            return

        # 网盘目的地目录
        itemdir_dest_path: str = item_transfer.target_diritem.path
        # 网盘目的地路径（包含文件名称）
        item_dest_path: str = item_transfer.target_item.path
        # 网盘目的地文件名称
        item_dest_name: str = item_transfer.target_item.name
        # 网盘目的地文件 pickcode
        item_dest_pickcode: str = item_transfer.target_item.pickcode
        # 是否蓝光原盘
        item_bluray: bool = StorageChain().is_bluray_folder(item_transfer.target_item)

        status, local_media_dir, pan_media_dir = PathUtils.get_media_path(
            configer.get_config("transfer_monitor_paths"), itemdir_dest_path
        )
        if not status:
            logger.debug(
                f"【监控整理STRM生成】{item_dest_name} 路径匹配不符合，跳过整理"
            )
            return
        logger.debug("【监控整理STRM生成】匹配到网盘文件夹路径: %s", str(pan_media_dir))

        # 下载 音轨/字幕 文件
        if (
            event_type == EventType.AudioTransferComplete
            or event_type == EventType.SubtitleTransferComplete
        ):
            try:
                file_item: FileItem = item_transfer.target_item
                _database_helper.upsert_batch(
                    _database_helper.process_fileitem(file_item)
                )
                if not item_dest_pickcode:
                    logger.error(
                        f"【监控整理STRM生成】{item_dest_name} 不存在 pickcode 值，无法下载该文件"
                    )
                    return
                download_url = mediainfodownloader.get_download_url(
                    pickcode=item_dest_pickcode
                )
                if not download_url:
                    logger.error(
                        f"【监控整理STRM生成】{item_dest_name} 下载链接获取失败，无法下载该文件"
                    )
                _file_path = Path(local_media_dir) / Path(item_dest_path).relative_to(
                    pan_media_dir
                )
                mediainfodownloader.save_mediainfo_file(
                    file_path=Path(_file_path),
                    file_name=_file_path.name,
                    download_url=download_url,
                )
            except Exception as e:
                sentry_manager.sentry_hub.capture_exception(e)
                logger.error(f"【监控整理STRM生成】媒体信息文件下载出现未知错误: {e}")
            return

        # STRM 生成流程
        if item_bluray:
            logger.warning(
                f"【监控整理STRM生成】{item_dest_name} 为蓝光原盘，不支持生成 STRM 文件: {item_dest_path}"
            )
            return

        if not item_dest_pickcode:
            logger.error(
                f"【监控整理STRM生成】{item_dest_name} 不存在 pickcode 值，无法生成 STRM 文件"
            )
            return
        if not (len(item_dest_pickcode) == 17 and str(item_dest_pickcode).isalnum()):
            logger.error(
                f"【监控整理STRM生成】错误的 pickcode 值 {item_dest_name}，无法生成 STRM 文件"
            )
            return

        strm_url = _get_url.get_strm_url(
            item_dest_pickcode, item_dest_name, item_dest_path
        )

        _database_helper.upsert_batch(
            _database_helper.process_fileitem(fileitem=item_transfer.target_item)
        )

        status, strm_target_path = self.generate_strm_files(
            target_dir=local_media_dir,
            pan_media_dir=pan_media_dir,
            item_dest_path=Path(item_dest_path),
            url=strm_url,
        )
        if not status:
            return

        scrape_metadata = True
        if configer.get_config("transfer_monitor_scrape_metadata_enabled"):
            if configer.get_config("transfer_monitor_scrape_metadata_exclude_paths"):
                if PathUtils.get_scrape_metadata_exclude_path(
                    configer.get_config(
                        "transfer_monitor_scrape_metadata_exclude_paths"
                    ),
                    str(strm_target_path),
                ):
                    logger.debug(
                        f"【监控整理STRM生成】匹配到刮削排除目录，不进行刮削: {strm_target_path}"
                    )
                    scrape_metadata = False
            if scrape_metadata:
                media_scrape_metadata(
                    path=strm_target_path,
                    item_name=item_dest_name,
                    mediainfo=mediainfo,
                    meta=meta,
                )

        if configer.get_config("transfer_monitor_media_server_refresh_enabled"):
            mediaserver_helper = MediaServerRefresh(
                func_name="【监控整理STRM生成】",
                enabled=configer.transfer_monitor_media_server_refresh_enabled,
                mp_mediaserver=configer.transfer_mp_mediaserver_paths,
                mediaservers=configer.transfer_monitor_mediaservers,
            )
            mediaserver_helper.refresh_mediaserver(
                file_name=item_dest_name,
                file_path=strm_target_path,
                mediainfo=mediainfo,
            )
