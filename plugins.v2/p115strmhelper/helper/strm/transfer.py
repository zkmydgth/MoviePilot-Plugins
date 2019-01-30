from pathlib import Path
from threading import Thread
from typing import Dict, Union, Set, Optional

from p115client import P115Client
from p115client.tool.attr import get_attr

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
from ...helper.mediaserver import MediaServerRefresh, emby_mediainfo_queue
from ...utils.path import PathRemoveUtils, PathUtils
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

    def _cleanup_stale_strm(
        self, item_transfer: TransferInfo, strm_target_path: str
    ) -> None:
        """
        重新整理场景下清理源路径对应的旧失效 STRM 文件及关联内容

        仅当 transfer_monitor_remove_stale_strm 开关开启、源路径命中媒体库配置
        且本地旧 STRM 文件实际存在时执行

        :param item_transfer: 转移信息
        :param strm_target_path: 本次新生成的 STRM 文件路径，用于防止误删
        :return: None
        """
        if not configer.transfer_monitor_remove_stale_strm:
            return

        source_item: Optional[FileItem] = item_transfer.fileitem
        if not source_item or not source_item.path:
            return

        source_path = source_item.path
        source_dir = Path(source_path).parent.as_posix()
        status, src_local_dir, src_pan_dir = PathUtils.get_media_path(
            configer.transfer_monitor_paths, source_dir
        )
        # 不属于媒体库路径
        if not status or not src_local_dir or not src_pan_dir:
            return

        old_strm_name = StrmGenerater.get_strm_filename(Path(source_path))
        old_strm_path = (
            Path(src_local_dir)
            / Path(source_path).relative_to(src_pan_dir).parent
            / old_strm_name
        )
        # 旧 STRM 不存在
        if not old_strm_path.exists():
            return

        # 防止误删：旧路径与新生成路径相同（原地 move 场景）
        if old_strm_path.resolve() == Path(strm_target_path).resolve():
            logger.debug(
                "【监控整理STRM生成】重新整理清理：旧 STRM 与新生成路径相同，跳过: %s",
                old_strm_path,
            )
            return

        logger.info(
            "【监控整理STRM生成】检测到重新整理，清理旧 STRM 文件: %s", old_strm_path
        )
        try:
            old_strm_path.unlink(missing_ok=True)
            logger.info("【监控整理STRM生成】已删除旧 STRM 文件: %s", old_strm_path)
        except Exception as e:
            logger.error(
                "【监控整理STRM生成】删除旧 STRM 文件失败: %s, %s", old_strm_path, e
            )
            return

        if configer.transfer_monitor_remove_stale_strm_file:
            PathRemoveUtils.clean_related_files(
                file_path=old_strm_path, func_type="【监控整理STRM生成】"
            )
        if configer.transfer_monitor_remove_stale_strm_dir:
            PathRemoveUtils.remove_parent_dir(
                file_path=old_strm_path, mode="mixed", func_type="【监控整理STRM生成】"
            )

    def do_generate(
        self,
        client: P115Client,
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
        item_transfer: Optional[TransferInfo] = item.get("transferinfo")
        if isinstance(item_transfer, dict):
            item_transfer: TransferInfo = TransferInfo(**item_transfer)
        # 媒体信息
        mediainfo: Optional[MediaInfo] = item.get("mediainfo")
        # 元数据信息
        meta: Optional[MetaBase] = item.get("meta")

        # 判断储存类型是否匹配
        allowed_storages: Set[str] = {"u115", "115网盘Plus"}
        if configer.transfer_monitor_clouddrive2_enabled:
            allowed_storages.add("CloudDrive储存")
        if item_transfer.target_item.storage not in allowed_storages:
            return

        # 网盘目的地目录
        itemdir_dest_path: str = item_transfer.target_diritem.path
        # 网盘目的地路径（包含文件名称）
        item_dest_path: str = item_transfer.target_item.path
        # 网盘目的地文件名称
        item_dest_name: str = item_transfer.target_item.name
        # 网盘目的地文件 pickcode
        item_dest_pickcode: str = item_transfer.target_item.pickcode
        if (
            not item_dest_pickcode
            and item_transfer.target_item.storage == "CloudDrive储存"
        ):
            try:
                item_dest_pickcode = client.to_pickcode(
                    int(item_transfer.target_item.fileid)
                )
            except Exception as e:
                logger.error(
                    f"【监控整理STRM生成】CloudDrive2 储存 {item_dest_name} 无法转换获取 PickCode 值: {e}"
                )
                return
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
                if item_transfer.target_item.storage != "CloudDrive储存":
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

        if item_transfer.target_item.storage != "CloudDrive储存":
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

        # 重新整理场景：移动整理时清理源路径对应的旧失效 STRM
        if item_transfer.transfer_type == "move":
            self._cleanup_stale_strm(
                item_transfer=item_transfer, strm_target_path=strm_target_path
            )

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

        if configer.transfer_monitor_emby_mediainfo_enabled:
            if configer.native_emby_mediainfo_enabled:
                try:
                    emby_mediainfo_queue.enqueue(
                        func_name="【监控整理STRM生成】",
                        path=Path(strm_target_path),
                        mp_mediaserver=configer.transfer_mp_mediaserver_paths,
                        mediaservers=configer.transfer_monitor_mediaservers,
                    )
                except Exception as e:
                    logger.error(f"【监控整理STRM生成】入队媒体信息提取失败: {e}")
            else:

                def _enqueue_emby_mediainfo() -> None:
                    try:
                        item = get_attr(
                            client=client,
                            id=item_dest_pickcode,
                            skim=True,
                            **configer.get_ios_ua_app(app=False),
                        )
                        emby_mediainfo_queue.enqueue(
                            func_name="【监控整理STRM生成】",
                            path=Path(strm_target_path),
                            sha1=item["sha1"],
                            mp_mediaserver=configer.transfer_mp_mediaserver_paths,
                            mediaservers=configer.transfer_monitor_mediaservers,
                            size=item["size"],
                        )
                    except Exception as e:
                        logger.error(f"【监控整理STRM生成】入队媒体信息提取失败: {e}")

                Thread(target=_enqueue_emby_mediainfo, daemon=True).start()
