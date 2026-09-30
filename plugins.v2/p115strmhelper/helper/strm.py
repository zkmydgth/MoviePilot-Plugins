import time
import threading
import shutil
from typing import List, Dict, Optional, Set, Tuple
from pathlib import Path
from itertools import batched
import concurrent.futures

from sqlalchemy.orm.exc import MultipleResultsFound
from orjson import dumps
from p115client import P115Client
from p115client.tool.export_dir import export_dir_parse_iter
from p115client.tool.fs_files import iter_fs_files
from p115client.tool.iterdir import (
    iter_files_with_path,
    iter_files_with_path_skim,
    share_iterdir,
)

from ..core.cache import idpathcacher
from ..core.config import configer
from ..utils.tree import DirectoryTree
from ..utils.sentry import sentry_manager
from ..core.scrape import media_scrape_metadata
from ..db_manager.oper import FileDbHelper
from ..utils.path import PathUtils
from ..utils.strm import StrmUrlGetter, StrmGenerater
from ..utils.mediainfo_download import MediainfoDownloadMiddleware
from ..utils.exception import PanPathNotFound, PanDataNotInDb, CanNotFindPathToCid
from ..utils.automaton import AutomatonUtils
from ..helper.mediainfo_download import MediaInfoDownloader

from app.log import logger
from app.core.meta import MetaBase
from app.core.context import MediaInfo
from app.core.metainfo import MetaInfoPath
from app.chain.storage import StorageChain
from app.schemas import RefreshMediaItem, ServiceInfo, TransferInfo
from app.chain.media import MediaChain
from app.helper.mediaserver import MediaServerHelper
from app.utils.system import SystemUtils


class IncrementSyncStrmHelper:
    """
    增量同步 STRM 文件
    """

    def __init__(self, client: P115Client, mediainfodownloader: MediaInfoDownloader):
        self.client = client
        self.mediainfodownloader = mediainfodownloader
        self.rmt_mediaext = [
            f".{ext.strip()}"
            for ext in configer.get_config("user_rmt_mediaext")
            .replace("，", ",")
            .split(",")
        ]
        self.download_mediaext = [
            f".{ext.strip()}"
            for ext in configer.get_config("user_download_mediaext")
            .replace("，", ",")
            .split(",")
        ]
        self.auto_download_mediainfo = configer.get_config(
            "increment_sync_auto_download_mediainfo_enabled"
        )
        self.mp_mediaserver_paths = configer.get_config(
            "increment_sync_mp_mediaserver_paths"
        )
        self.scrape_metadata_enabled = configer.get_config(
            "increment_sync_scrape_metadata_enabled"
        )
        self.scrape_metadata_exclude_paths = configer.get_config(
            "increment_sync_scrape_metadata_exclude_paths"
        )
        self.media_server_refresh_enabled = configer.get_config(
            "increment_sync_media_server_refresh_enabled"
        )
        self.mediaservers = configer.get_config("increment_sync_mediaservers")
        self.strm_count = 0
        self.mediainfo_count = 0
        self.strm_fail_count = 0
        self.mediainfo_fail_count = 0
        self.api_count = 0
        self.strm_fail_dict: Dict[str, str] = {}
        self.mediainfo_fail_dict: List = []
        self.server_address = configer.get_config("moviepilot_address").rstrip("/")
        self.pan_transfer_enabled = configer.get_config("pan_transfer_enabled")
        self.pan_transfer_paths = configer.get_config("pan_transfer_paths")
        self.strm_url_format = configer.get_config("strm_url_format")
        self.databasehelper = FileDbHelper()
        self.download_mediainfo_list = []

        self.strmurlgetter = StrmUrlGetter()

        # 临时文件配置
        self.local_tree = (
            configer.get_config("PLUGIN_TEMP_PATH") / "increment_local_tree.txt"
        )
        self.pan_tree = (
            configer.get_config("PLUGIN_TEMP_PATH") / "increment_pan_tree.txt"
        )
        self.pan_to_local_tree = (
            configer.get_config("PLUGIN_TEMP_PATH") / "increment_pan_to_local_tree.txt"
        )

    def __itertree(self, pan_path: str, local_path: str):
        """
        迭代目录树
        """
        relative_path = None
        if pan_path == "/":
            cid = 0
        else:
            cid = int(self.client.fs_dir_getid(pan_path).get("id", -1))
        if not cid or cid == -1:
            raise PanPathNotFound(f"网盘路径不存在: {pan_path}")
        self.api_count += 4
        cnt = 0
        for item in export_dir_parse_iter(
            client=self.client, export_file_ids=cid, delete=True
        ):
            if cnt < 1:
                cnt += 1
                continue
            elif cnt == 1:
                relative_path = item
                cnt += 1
                continue
            item_path = Path(pan_path) / Path(item).relative_to(relative_path)
            if item_path.name != item_path.stem:
                relative_item_path = item_path.relative_to(pan_path)
                local_item_path = Path(local_path) / relative_item_path
                if item_path.suffix.lower() in self.rmt_mediaext:
                    yield (
                        local_item_path.with_suffix(".strm").as_posix(),
                        item_path.as_posix(),
                    )
                elif (
                    item_path.suffix.lower() in self.download_mediaext
                    and self.auto_download_mediainfo
                ):
                    yield (
                        local_item_path.as_posix(),
                        item_path.as_posix(),
                    )

    def __iterdir(self, cid: int, path: str):
        """
        迭代网盘目录
        """
        logger.debug(f"【增量STRM生成】迭代网盘目录: {cid} {path}")
        for batch in iter_fs_files(self.client, cid, cooldown=2):
            self.api_count += 1
            for item in batch.get("data", []):
                item["path"] = path + "/" + item.get("n")
                yield item

    def __get_cid_by_path(self, path: str):
        """
        通过路径获取 cid
        先从缓存获取，再从数据库获取
        """
        cid = idpathcacher.get_id_by_dir(path)
        if not cid:
            # 这里如果有多条重复数据就不进行删除文件夹操作了，说明数据库重复过多，直接放弃
            data = self.databasehelper.get_by_path(path=path)
            if data:
                cid = data.get("id", None)
                if cid:
                    logger.debug(f"【增量STRM生成】获取 {path} cid（数据库）: {cid}")
                    idpathcacher.add_cache(id=int(cid), directory=path)
                    return int(cid)
            return None
        logger.debug(f"【增量STRM生成】获取 {path} cid（缓存）: {cid}")
        return int(cid)

    def __get_size(self, path: str) -> Optional[int]:
        """
        通过数据库获取文件大小
        """
        data = self.databasehelper.get_by_path(path=path)
        if data:
            size = data.get("size", 0)
            if size and size > 0:
                return size
        return None

    def __get_pickcode(self, path: str):
        """
        通过路径获取 pickcode
        """
        last_path = None
        processed = []
        while True:
            # 这里如果有多条重复数据直接删除文件重复信息，然后迭代重新获取
            try:
                file_item = self.databasehelper.get_by_path(path=path)
            except MultipleResultsFound:
                self.databasehelper.remove_by_path_batch(path=path, only_file=True)
                file_item = None
            if file_item:
                return file_item.get("pickcode")
            file_path = Path(path)
            temp_path = None
            cid = None
            for part in file_path.parents:
                cid = self.__get_cid_by_path(part.as_posix())
                if cid:
                    temp_path = part
                    break
            if not temp_path:
                raise PanDataNotInDb(f"数据库无数据，无法找到路径 {path} 对应的 cid")
            if last_path and last_path == temp_path:
                logger.debug(f"文件夹遍历错误：{last_path} {processed}")
                raise CanNotFindPathToCid(
                    f"文件夹遍历错误，无法找到路径 {path} 对应的 cid"
                )
            if not cid:
                raise CanNotFindPathToCid(f"无法找到路径 {path} 的 cid")
            for batch in batched(
                self.__iterdir(cid=cid, path=temp_path.as_posix()), 5_000
            ):
                processed: List = []
                for item in batch:
                    processed.extend(self.databasehelper.process_fs_files_item(item))
                self.databasehelper.upsert_batch(processed)
            last_path = temp_path
            time.sleep(2)

    @property
    def service_infos(self) -> Optional[Dict[str, ServiceInfo]]:
        """
        媒体服务器服务信息
        """
        if not self.mediaservers:
            logger.warning("【增量STRM生成】尚未配置媒体服务器，请检查配置")
            return None

        mediaserver_helper = MediaServerHelper()

        services = mediaserver_helper.get_services(name_filters=self.mediaservers)
        if not services:
            logger.warning("【增量STRM生成】获取媒体服务器实例失败，请检查配置")
            return None

        active_services = {}
        for service_name, service_info in services.items():
            if service_info.instance.is_inactive():
                logger.warning(
                    f"【增量STRM生成】媒体服务器 {service_name} 未连接，请检查配置"
                )
            else:
                active_services[service_name] = service_info

        if not active_services:
            logger.warning("【增量STRM生成】没有已连接的媒体服务器，请检查配置")
            return None

        return active_services

    def __refresh_mediaserver(self, file_path: str, file_name: str):
        """
        刷新媒体服务器
        """
        if self.media_server_refresh_enabled:
            if not self.service_infos:
                return
            logger.info(f"【增量STRM生成】{file_name} 开始刷新媒体服务器")
            if self.mp_mediaserver_paths:
                status, mediaserver_path, moviepilot_path = PathUtils.get_media_path(
                    self.mp_mediaserver_paths, file_path
                )
                if status:
                    logger.debug(
                        f"【增量STRM生成】{file_name} 刷新媒体服务器目录替换中..."
                    )
                    file_path = file_path.replace(
                        moviepilot_path, mediaserver_path
                    ).replace("\\", "/")
                    logger.debug(
                        f"【增量STRM生成】刷新媒体服务器目录替换: {moviepilot_path} --> {mediaserver_path}"
                    )
                    logger.info(f"【增量STRM生成】刷新媒体服务器目录: {file_path}")
            mediachain = MediaChain()
            meta = MetaInfoPath(path=Path(file_path))
            mediainfo = mediachain.recognize_media(meta=meta)
            if not mediainfo:
                logger.warning(f"【增量STRM生成】{file_name} 无法刷新媒体库")
                return
            items = [
                RefreshMediaItem(
                    title=mediainfo.title,
                    year=mediainfo.year,
                    type=mediainfo.type,
                    category=mediainfo.category,
                    target_path=Path(file_path),
                )
            ]
            for name, service in self.service_infos.items():
                if hasattr(service.instance, "refresh_library_by_items"):
                    service.instance.refresh_library_by_items(items)
                elif hasattr(service.instance, "refresh_root_library"):
                    service.instance.refresh_root_library()
                else:
                    logger.warning(f"【增量STRM生成】{file_name} {name} 不支持刷新")

    def __generate_local_tree(self, target_dir: str):
        """
        生成本地目录树
        """
        if Path(self.local_tree).exists():
            Path(self.local_tree).unlink(missing_ok=True)

        def background_task(target_dir, local_tree):
            """
            后台运行任务
            """
            logger.info(f"【增量STRM生成】开始扫描本地媒体库文件: {target_dir}")
            try:
                DirectoryTree().scan_directory_to_tree(
                    root_path=target_dir,
                    output_file=local_tree,
                    append=False,
                    use_posix=True,
                    extensions=[".strm"]
                    if not self.auto_download_mediainfo
                    else [".strm"] + self.download_mediaext,
                )
                logger.info(f"【增量STRM生成】扫描本地媒体库文件完成: {target_dir}")
            except Exception as e:
                logger.error(
                    f"【增量STRM生成】扫描本地媒体库文件 {target_dir} 错误: {e}"
                )

        local_tree_task_thread = threading.Thread(
            target=background_task,
            args=(
                target_dir,
                self.local_tree,
            ),
        )
        local_tree_task_thread.start()

        return local_tree_task_thread

    @staticmethod
    def __wait_generate_local_tree(thread):
        """
        等待生成本地目录树运行完成
        """
        while thread.is_alive():
            logger.info("【增量STRM生成】扫描本地媒体库运行中...")
            time.sleep(10)

    def __generate_pan_tree(self, pan_media_dir: str, target_dir: str):
        """
        生成网盘目录树
        """
        tree = DirectoryTree()

        if Path(self.pan_tree).exists():
            Path(self.pan_tree).unlink(missing_ok=True)
        if Path(self.pan_to_local_tree).exists():
            Path(self.pan_to_local_tree).unlink(missing_ok=True)

        logger.info(f"【增量STRM生成】开始生成网盘目录树: {pan_media_dir}")

        try:
            for path1, path2 in self.__itertree(
                pan_path=pan_media_dir, local_path=target_dir
            ):
                tree.generate_tree_from_list(
                    [path1], self.pan_to_local_tree, append=True
                )
                tree.generate_tree_from_list([path2], self.pan_tree, append=True)

            logger.info(f"【增量STRM生成】网盘目录树生成完成: {pan_media_dir}")
        except Exception as e:
            logger.error(f"【增量STRM生成】网盘目录树生成 {pan_media_dir} 错误: {e}")

    def __handle_addition_path(self, pan_path: str, local_path: str):
        """
        处理新增路径
        """
        pan_path_obj = Path(pan_path)
        new_file_path = Path(local_path)

        try:
            if self.pan_transfer_enabled and self.pan_transfer_paths:
                if PathUtils.get_run_transfer_path(
                    paths=self.pan_transfer_paths,
                    transfer_path=pan_path,
                ):
                    logger.debug(
                        f"【增量STRM生成】{pan_path} 为待整理目录下的路径，不做处理"
                    )
                    return

            if self.auto_download_mediainfo:
                if pan_path_obj.suffix.lower() in self.download_mediaext:
                    pickcode = self.__get_pickcode(pan_path)
                    if not pickcode:
                        logger.error(
                            f"【增量STRM生成】{pan_path_obj.name} 不存在 pickcode 值，无法下载该文件"
                        )
                        return
                    self.download_mediainfo_list.append(
                        {
                            "type": "local",
                            "pickcode": pickcode,
                            "path": local_path,
                        }
                    )
                    return

            if pan_path_obj.suffix.lower() not in self.rmt_mediaext:
                logger.warn(f"【增量STRM生成】跳过网盘路径: {pan_path}")
                return

            if not (
                result := StrmGenerater.should_generate_strm(
                    pan_path_obj.name, "increment", self.__get_size(pan_path)
                )
            )[1]:
                logger.warn(f"【增量STRM生成】{result[0]}，跳过网盘路径: {pan_path}")
                return

            pickcode = self.__get_pickcode(pan_path)

            if not (
                result := StrmGenerater.not_min_limit(
                    "increment", self.__get_size(pan_path)
                )
            )[1]:
                logger.warn(f"【增量STRM生成】{result[0]}，跳过网盘路径: {pan_path}")
                return

            new_file_path.parent.mkdir(parents=True, exist_ok=True)

            if not pickcode:
                self.strm_fail_count += 1
                self.strm_fail_dict[str(new_file_path)] = "不存在 pickcode 值"
                logger.error(
                    f"【增量STRM生成】{pan_path_obj.name} 不存在 pickcode 值，无法生成 STRM 文件"
                )
                return
            if not (len(pickcode) == 17 and str(pickcode).isalnum()):
                self.strm_fail_count += 1
                self.strm_fail_dict[str(new_file_path)] = (
                    f"错误的 pickcode 值 {pickcode}"
                )
                logger.error(
                    f"【增量STRM生成】错误的 pickcode 值 {pickcode}，无法生成 STRM 文件"
                )
                return

            strm_url = self.strmurlgetter.get_strm_url(pickcode, pan_path_obj.name)

            with open(new_file_path, "w", encoding="utf-8") as file:
                file.write(strm_url)
            self.strm_count += 1
            logger.info(
                "【增量STRM生成】生成 STRM 文件成功: %s",
                str(new_file_path),
            )
        except Exception as e:
            sentry_manager.sentry_hub.capture_exception(e)
            logger.error(
                "【增量STRM生成】生成 STRM 文件失败: %s  %s",
                str(new_file_path),
                e,
            )
            self.strm_fail_count += 1
            self.strm_fail_dict[str(new_file_path)] = str(e)
            return
        if self.scrape_metadata_enabled:
            scrape_metadata = True
            if self.scrape_metadata_exclude_paths:
                if PathUtils.get_scrape_metadata_exclude_path(
                    self.scrape_metadata_exclude_paths,
                    local_path,
                ):
                    logger.debug(
                        f"【增量STRM生成】匹配到刮削排除目录，不进行刮削: {local_path}"
                    )
                    scrape_metadata = False
            if scrape_metadata:
                media_scrape_metadata(
                    path=local_path,
                )
        self.__refresh_mediaserver(local_path, new_file_path.name)

    def generate_strm_files(self, sync_strm_paths):
        """
        生成 STRM 文件
        """
        media_paths = sync_strm_paths.split("\n")
        for path in media_paths:
            if not path:
                continue
            parts = path.split("#", 1)
            pan_media_dir = parts[1]
            target_dir = parts[0]

            if pan_media_dir == "/" or target_dir == "/":
                logger.error(
                    f"【增量STRM生成】网盘目录或本地生成目录不能为根目录: {path}"
                )

            pan_media_dir = pan_media_dir.rstrip("/")
            target_dir = target_dir.rstrip("/")

            try:
                # 生成本地目录树文件
                local_tree_task_thread = self.__generate_local_tree(
                    target_dir=target_dir
                )

                # 生成网盘目录树文件
                self.__generate_pan_tree(
                    pan_media_dir=pan_media_dir, target_dir=target_dir
                )

                # 等待生成本地目录树运行完成
                self.__wait_generate_local_tree(local_tree_task_thread)

                # 生成或者下载文件
                for line in DirectoryTree().compare_trees_lines(
                    self.pan_to_local_tree, self.local_tree
                ):
                    pan_path_str = DirectoryTree().get_path_by_line_number(
                        self.pan_tree, line
                    )
                    local_path_str = DirectoryTree().get_path_by_line_number(
                        self.pan_to_local_tree, line
                    )
                    if pan_path_str and local_path_str:
                        self.__handle_addition_path(
                            pan_path=pan_path_str,
                            local_path=local_path_str,
                        )
            except Exception as e:
                sentry_manager.sentry_hub.capture_exception(e)
                logger.error(f"【增量STRM生成】增量同步 STRM 文件失败: {e}")
                return

        # 下载媒体信息文件
        self.mediainfo_count, self.mediainfo_fail_count, self.mediainfo_fail_dict = (
            self.mediainfodownloader.auto_downloader(
                downloads_list=self.download_mediainfo_list
            )
        )

        # 日志输出
        if self.strm_fail_dict:
            for path, error in self.strm_fail_dict.items():
                logger.warn(f"【增量STRM生成】{path} 生成错误原因: {error}")
        if self.mediainfo_fail_dict:
            for path in self.mediainfo_fail_dict:
                logger.warn(f"【增量STRM生成】{path} 下载错误")
        logger.info(
            f"【增量STRM生成】增量生成 STRM 文件完成，总共生成 {self.strm_count} 个 STRM 文件，下载 {self.mediainfo_count} 个媒体数据文件"
        )
        if self.strm_fail_count != 0 or self.mediainfo_fail_count != 0:
            logger.warn(
                f"【增量STRM生成】{self.strm_fail_count} 个 STRM 文件生成失败，{self.mediainfo_fail_count} 个媒体数据文件下载失败"
            )
        logger.info(f"【增量STRM生成】API 请求次数 {self.api_count} 次")

    def get_generate_total(self):
        """
        输出总共生成文件个数
        """
        return (
            self.strm_count,
            self.mediainfo_count,
            self.strm_fail_count,
            self.mediainfo_fail_count,
        )


class FullSyncStrmHelper:
    """
    全量生成 STRM 文件
    """

    def __init__(
        self,
        client: P115Client,
        mediainfodownloader: MediaInfoDownloader,
    ):
        self.rmt_mediaext_set = {
            f".{ext.strip()}"
            for ext in configer.user_rmt_mediaext.replace("，", ",").split(",")
        }
        self.download_mediaext_set = {
            f".{ext.strip()}"
            for ext in configer.user_download_mediaext.replace("，", ",").split(",")
        }
        self.auto_download_mediainfo = (
            configer.full_sync_auto_download_mediainfo_enabled
        )
        self.client = client
        self.mediainfodownloader = mediainfodownloader
        self.total_count = 0
        self.elapsed_time = 0
        self.total_db_write_count = 0
        self.strm_count = 0
        self.mediainfo_count = 0
        self.strm_fail_count = 0
        self.mediainfo_fail_count = 0
        self.remove_unless_strm_count = 0
        self.strm_fail_dict: Dict[str, str] = {}
        self.mediainfo_fail_dict: List = []
        self.server_address = configer.moviepilot_address.rstrip("/")
        self.pan_transfer_enabled = configer.pan_transfer_enabled
        self.pan_transfer_paths = configer.pan_transfer_paths
        self.strm_url_format = configer.strm_url_format
        self.overwrite_mode = configer.full_sync_overwrite_mode
        self.remove_unless_strm = configer.full_sync_remove_unless_strm
        self.databasehelper = FileDbHelper()
        self.download_mediainfo_list = []

        self.strmurlgetter = StrmUrlGetter()
        self.sgab = AutomatonUtils.build_automaton(configer.strm_generate_blacklist)
        self.mdaw = AutomatonUtils.build_automaton(
            configer.mediainfo_download_whitelist
        )
        self.mdab = AutomatonUtils.build_automaton(
            configer.mediainfo_download_blacklist
        )

        self.local_tree = configer.PLUGIN_TEMP_PATH / "local_tree.txt"
        self.pan_tree = configer.PLUGIN_TEMP_PATH / "pan_tree.txt"

        if configer.full_sync_strm_log:
            self.__base_logger = self.__base_has_logger
        else:
            self.__base_logger = self.__base_no_logger

    @staticmethod
    def __base_no_logger(level, msg, *args):
        """
        空白日志输出器
        """
        pass

    @staticmethod
    def __base_has_logger(level, msg, *args):
        """
        由配置控制的日志输出器
        """
        log_method = getattr(logger, level)
        log_method(msg, *args)

    @staticmethod
    def __remove_parent_dir(file_path: Path):
        """
        删除父目录
        """
        # 删除空目录
        # 判断当前媒体父路径下是否有文件，如有则无需遍历父级
        if not any(file_path.parent.iterdir()):
            # 判断父目录是否为空, 为空则删除
            i = 0
            for parent_path in file_path.parents:
                i += 1
                if i > 3:
                    break
                if str(parent_path.parent) != str(file_path.root):
                    # 父目录非根目录，才删除父目录
                    if not any(parent_path.iterdir()):
                        # 当前路径下没有媒体文件则删除
                        shutil.rmtree(parent_path)
                        logger.warn(f"【全量STRM生成】本地空目录 {parent_path} 已删除")

    def __remove_unless_strm_local(self, target_dir: str) -> threading.Thread:
        """
        清理无效 STRM 本地扫描
        """
        if Path(self.local_tree).exists():
            Path(self.local_tree).unlink(missing_ok=True)
        if Path(self.pan_tree).exists():
            Path(self.pan_tree).unlink(missing_ok=True)

        def background_task(_target_dir, local_tree):
            """
            后台运行任务
            """
            logger.info(f"【全量STRM生成】开始扫描本地媒体库文件: {_target_dir}")
            try:
                DirectoryTree().scan_directory_to_tree(
                    root_path=_target_dir,
                    output_file=local_tree,
                    append=False,
                    extensions=[".strm"],
                )
                logger.info(f"【全量STRM生成】扫描本地媒体库文件完成: {_target_dir}")
            except Exception as e:
                logger.error(
                    f"【全量STRM生成】扫描本地媒体库文件 {_target_dir} 错误: {e}"
                )

        local_tree_task_thread = threading.Thread(
            target=background_task,
            args=(
                target_dir,
                self.local_tree,
            ),
        )
        local_tree_task_thread.start()

        return local_tree_task_thread

    def __process_db_item(
        self, batch, seen_folder_ids: Set[str], seen_file_ids: Set[str]
    ) -> Tuple[Set[str], Set[str]]:
        """
        处理写入数据库的内容
        """
        files_list: List = []
        folders_list: List = []

        for item in batch:
            ancestors = item.get("ancestors", [])
            path_parts = []
            for ancestor in ancestors[1:-1]:
                path_parts.append(ancestor["name"])
                path = "/" + "/".join(path_parts)
                ancestor_id = str(ancestor["id"])
                if ancestor_id not in seen_folder_ids:
                    folders_list.append(
                        {
                            "id": ancestor["id"],
                            "parent_id": ancestor["parent_id"],
                            "name": ancestor["name"],
                            "path": path,
                        }
                    )
                    seen_folder_ids.add(ancestor_id)
            file_id = str(item["id"])
            if file_id not in seen_file_ids:
                files_list.append(
                    {
                        "id": item["id"],
                        "parent_id": item["parent_id"],
                        "name": item["name"],
                        "sha1": item.get("sha1", ""),
                        "size": item.get("size", 0),
                        "pickcode": item.get("pickcode", item.get("pick_code", "")),
                        "ctime": item.get("ctime", 0),
                        "mtime": item.get("mtime", 0),
                        "path": item.get("path", ""),
                        "extra": dumps(item).decode("utf-8") if item else None,
                    }
                )
                seen_file_ids.add(file_id)

        self.databasehelper.upsert_batch_by_list("files", files_list)
        self.databasehelper.upsert_batch_by_list("folders", folders_list)

        return seen_folder_ids, seen_file_ids

    def __process_single_item(
        self, item: Dict, target_dir: Path, pan_media_dir: str
    ) -> Optional[str]:
        """
        处理单个项目
        """
        path_entry = None
        try:
            if item["is_dir"]:
                return path_entry
            file_path = item["path"]
            # 全量拉数据时可能混入无关路径
            if not PathUtils.has_prefix(file_path, pan_media_dir):
                return path_entry
            file_path = target_dir / Path(file_path).relative_to(pan_media_dir)
            file_target_dir = file_path.parent
            original_file_name = file_path.name
            file_name = file_path.stem + ".strm"
            new_file_path = file_target_dir / file_name
        except Exception as e:
            sentry_manager.sentry_hub.capture_exception(e)
            logger.error(
                "【全量STRM生成】生成 STRM 文件失败: %s  %s",
                str(item),
                e,
            )
            self.strm_fail_count += 1
            self.strm_fail_dict[str(item)] = str(e)
            return path_entry

        try:
            if self.pan_transfer_enabled and self.pan_transfer_paths:
                if PathUtils.get_run_transfer_path(
                    paths=self.pan_transfer_paths,
                    transfer_path=item["path"],
                ):
                    logger.debug(
                        "【全量STRM生成】%s 为待整理目录下的路径，不做处理",
                        item["path"],
                    )
                    return path_entry

            if self.auto_download_mediainfo:
                if file_path.suffix.lower() in self.download_mediaext_set:
                    if file_path.exists():
                        if self.overwrite_mode == "never":
                            self.__base_logger(
                                "warn",
                                "【全量STRM生成】%s 已存在，覆盖模式 %s，跳过此路径",
                                new_file_path,
                                self.overwrite_mode,
                            )
                            return path_entry
                        else:
                            self.__base_logger(
                                "warn",
                                "【全量STRM生成】%s 已存在，覆盖模式 %s",
                                new_file_path,
                                self.overwrite_mode,
                            )

                    if not (
                        result := MediainfoDownloadMiddleware.should_download(
                            filename=file_path.name,
                            blacklist_automaton=self.mdab,
                            whitelist_automaton=self.mdaw,
                        )
                    )[1]:
                        self.__base_logger(
                            "warn",
                            "【全量STRM生成】%s，跳过网盘路径: %s",
                            result[0],
                            item["path"],
                        )
                        return path_entry

                    pickcode = item["pickcode"]
                    if not pickcode:
                        logger.error(
                            f"【全量STRM生成】{original_file_name} 不存在 pickcode 值，无法下载该文件"
                        )
                        return path_entry
                    self.download_mediainfo_list.append(
                        {
                            "type": "local",
                            "pickcode": pickcode,
                            "path": file_path,
                            "sha1": item["sha1"],
                        }
                    )
                    return path_entry

            if file_path.suffix.lower() not in self.rmt_mediaext_set:
                self.__base_logger(
                    "warn",
                    "【全量STRM生成】跳过网盘路径: %s",
                    item["path"],
                )
                return path_entry

            if not (
                result := StrmGenerater.should_generate_strm(
                    filename=original_file_name,
                    mode="full",
                    filesize=item.get("size", None),
                    blacklist_automaton=self.sgab,
                )
            )[1]:
                self.__base_logger(
                    "warn",
                    "【全量STRM生成】%s，跳过网盘路径: %s",
                    result[0],
                    item["path"],
                )
                return path_entry

            if self.remove_unless_strm:
                path_entry = str(new_file_path)

            if new_file_path.exists():
                if self.overwrite_mode == "never":
                    self.__base_logger(
                        "warn",
                        f"【全量STRM生成】{new_file_path} 已存在，覆盖模式 {self.overwrite_mode}，跳过此路径",
                    )
                    return path_entry
                else:
                    self.__base_logger(
                        "warn",
                        f"【全量STRM生成】{new_file_path} 已存在，覆盖模式 {self.overwrite_mode}",
                    )

            pickcode = item["pickcode"]
            if not pickcode:
                pickcode = item["pick_code"]

            new_file_path.parent.mkdir(parents=True, exist_ok=True)

            if not pickcode:
                self.strm_fail_count += 1
                self.strm_fail_dict[str(new_file_path)] = "不存在 pickcode 值"
                logger.error(
                    f"【全量STRM生成】{original_file_name} 不存在 pickcode 值，无法生成 STRM 文件"
                )
                return path_entry
            if not (len(pickcode) == 17 and str(pickcode).isalnum()):
                self.strm_fail_count += 1
                self.strm_fail_dict[str(new_file_path)] = (
                    f"错误的 pickcode 值 {pickcode}"
                )
                logger.error(
                    f"【全量STRM生成】错误的 pickcode 值 {pickcode}，无法生成 STRM 文件"
                )
                return path_entry

            strm_url = self.strmurlgetter.get_strm_url(pickcode, original_file_name)

            with open(new_file_path, "w", encoding="utf-8") as file:
                file.write(strm_url)
            self.strm_count += 1
            self.__base_logger(
                "info",
                "【全量STRM生成】生成 STRM 文件成功: %s",
                str(new_file_path),
            )
            return path_entry
        except Exception as e:
            sentry_manager.sentry_hub.capture_exception(e)
            logger.error(
                "【全量STRM生成】生成 STRM 文件失败: %s  %s",
                str(new_file_path),
                e,
            )
            self.strm_fail_count += 1
            self.strm_fail_dict[str(new_file_path)] = str(e)
            return path_entry

    def generate_database(self, full_sync_strm_paths):
        """
        全量更新数据库
        """
        media_paths = full_sync_strm_paths.split("\n")
        for path in media_paths:
            if not path:
                continue
            parts = path.split("#", 1)
            pan_media_dir = parts[1]

            try:
                if pan_media_dir == "/":
                    parent_id = 0
                else:
                    parent_id = int(self.client.fs_dir_getid(pan_media_dir)["id"])
                logger.info(
                    f"【全量STRM生成】网盘媒体目录 ID 获取成功: {pan_media_dir} {parent_id}"
                )
            except Exception as e:
                sentry_manager.sentry_hub.capture_exception(e)
                logger.error(
                    f"【全量STRM生成】网盘媒体目录 ID 获取失败: {pan_media_dir} {e}"
                )
                return False

            try:
                if (
                    configer.get_config("full_sync_iter_function")
                    == "iter_files_with_path_skim"
                ):
                    iter_func = iter_files_with_path_skim
                    iter_kwargs = {"cid": parent_id, "with_ancestors": True}
                else:
                    iter_func = iter_files_with_path
                    iter_kwargs = {
                        "cid": parent_id,
                        "with_ancestors": True,
                        "cooldown": 1.5,
                    }
                logger.debug(
                    f"【全量STRM生成】迭代函数 {iter_func}; 参数 {iter_kwargs}"
                )
                start_time = time.perf_counter()
                seen_folder_ids: Set[str] = set()
                seen_file_ids: Set[str] = set()
                for batch in batched(
                    iter_func(self.client, **iter_kwargs),
                    int(configer.get_config("full_sync_batch_num")),
                ):
                    seen_folder_ids, seen_file_ids = self.__process_db_item(
                        batch,
                        seen_folder_ids,
                        seen_file_ids,
                    )
                end_time = time.perf_counter()
                self.elapsed_time += end_time - start_time
                self.total_db_write_count += len(seen_file_ids) + len(seen_folder_ids)
            except Exception as e:
                sentry_manager.sentry_hub.capture_exception(e)
                logger.error(
                    f"【全量STRM生成】全量更新数据库失败: {pan_media_dir} {e}",
                    exc_info=True,
                )
                return False

        logger.info("【全量STRM生成】全量更新数据库清空缓存数据...")
        idpathcacher.clear()

        logger.info(
            f"【全量STRM生成】全量更新数据库完成，时间 {self.elapsed_time:.6f} 秒，数据库写入量 {self.total_db_write_count} 条"
        )

    def generate_strm_files(self, full_sync_strm_paths):
        """
        生成 STRM 文件
        """
        media_paths = full_sync_strm_paths.split("\n")
        for path in media_paths:
            if not path:
                continue
            parts = path.split("#", 1)
            pan_media_dir = parts[1]
            target_dir = parts[0]

            if self.remove_unless_strm:
                local_tree_task_thread = self.__remove_unless_strm_local(target_dir)

            try:
                if pan_media_dir == "/":
                    parent_id = 0
                else:
                    parent_id = int(self.client.fs_dir_getid(pan_media_dir)["id"])
                logger.info(
                    f"【全量STRM生成】网盘媒体目录 ID 获取成功: {pan_media_dir} {parent_id}"
                )
            except Exception as e:
                sentry_manager.sentry_hub.capture_exception(e)
                logger.error(
                    f"【全量STRM生成】网盘媒体目录 ID 获取失败: {pan_media_dir} {e}"
                )
                return False

            try:
                if (
                    configer.get_config("full_sync_iter_function")
                    == "iter_files_with_path_skim"
                ):
                    iter_func = iter_files_with_path_skim
                    iter_kwargs = {"cid": parent_id, "with_ancestors": True}
                else:
                    iter_func = iter_files_with_path
                    iter_kwargs = {
                        "cid": parent_id,
                        "with_ancestors": True,
                        "cooldown": 1.5,
                    }
                logger.debug(
                    f"【全量STRM生成】迭代函数 {iter_func}; 参数 {iter_kwargs}"
                )
                start_time = time.perf_counter()
                seen_folder_ids: Set[str] = set()
                seen_file_ids: Set[str] = set()
                for batch in batched(
                    iter_func(self.client, **iter_kwargs),
                    int(configer.get_config("full_sync_batch_num")),
                ):
                    path_list: List = []

                    with concurrent.futures.ThreadPoolExecutor(
                        max_workers=int(configer.get_config("full_sync_process_num"))
                    ) as executor:
                        db_task_future = executor.submit(
                            self.__process_db_item,
                            batch,
                            seen_folder_ids,
                            seen_file_ids,
                        )

                        target_dir_path = Path(target_dir)

                        future_to_item = {
                            executor.submit(
                                self.__process_single_item,
                                item,
                                target_dir_path,
                                pan_media_dir,
                            ): item
                            for item in batch
                        }

                        self.total_count += len(future_to_item)

                        for future in concurrent.futures.as_completed(future_to_item):
                            item = future_to_item[future]
                            try:
                                item_path = future.result()
                                if item_path:
                                    path_list.append(item_path)
                            except Exception as e:
                                sentry_manager.sentry_hub.capture_exception(e)
                                logger.error(
                                    f"【全量STRM生成】并发处理出错: {item} - {str(e)}"
                                )

                        try:
                            seen_folder_ids, seen_file_ids = db_task_future.result()
                        except Exception as e:
                            sentry_manager.sentry_hub.capture_exception(e)
                            logger.error(
                                f"【全量STRM生成】数据库处理并发处理出错: {str(e)}"
                            )

                    if self.remove_unless_strm:
                        DirectoryTree().generate_tree_from_list(
                            path_list, self.pan_tree, append=True
                        )

                end_time = time.perf_counter()
                self.elapsed_time += end_time - start_time
                self.total_db_write_count += len(seen_file_ids) + len(seen_folder_ids)
            except Exception as e:
                sentry_manager.sentry_hub.capture_exception(e)
                logger.error(
                    f"【全量STRM生成】全量生成 STRM 文件失败: {pan_media_dir} {e}",
                    exc_info=True,
                )
                return False

            if self.remove_unless_strm:
                while local_tree_task_thread.is_alive():
                    logger.info("【全量STRM生成】扫描本地媒体库运行中...")
                    time.sleep(10)
                if not self.strm_fail_dict and Path(self.local_tree).exists():
                    try:
                        count = DirectoryTree().compare_file_lines(
                            self.local_tree, self.pan_tree
                        )
                        if count > 500:
                            logger.warn(
                                f"【全量STRM生成】本次将删除文件个数为 {count}，超过安全阈值不进行删除操作"
                            )
                            continue
                        for path in DirectoryTree().compare_trees(
                            self.local_tree, self.pan_tree
                        ):
                            logger.info(f"【全量STRM生成】清理无效 STRM 文件: {path}")
                            Path(path).unlink(missing_ok=True)
                            self.__remove_parent_dir(file_path=Path(path))
                            self.remove_unless_strm_count += 1
                    except Exception as e:
                        sentry_manager.sentry_hub.capture_exception(e)
                        logger.error(f"【全量STRM生成】清理无效 STRM 文件失败: {e}")
                else:
                    logger.warn(
                        "【全量STRM生成】存在生成失败的 STRM 文件或扫描本地文件出错，跳过清理无效 STRM 文件"
                    )

        self.mediainfo_count, self.mediainfo_fail_count, self.mediainfo_fail_dict = (
            self.mediainfodownloader.batch_auto_downloader(
                downloads_list=self.download_mediainfo_list
            )
        )

        self.result_print()

        logger.debug(
            f"【全量STRM生成】时间 {self.elapsed_time:.6f} 秒，总迭代文件数量 {self.total_count} 个，数据库写入量 {self.total_db_write_count} 条"
        )

        return True

    def result_print(self):
        """
        输出结果信息
        """
        if self.strm_fail_dict:
            for path, error in self.strm_fail_dict.items():
                logger.warn(f"【全量STRM生成】{path} 生成错误原因: {error}")
        if self.mediainfo_fail_dict:
            for path in self.mediainfo_fail_dict:
                logger.warn(f"【全量STRM生成】{path} 下载错误")
        logger.info(
            f"【全量STRM生成】全量生成 STRM 文件完成，总共生成 {self.strm_count} 个 STRM 文件，下载 {self.mediainfo_count} 个媒体数据文件"
        )
        if self.strm_fail_count != 0 or self.mediainfo_fail_count != 0:
            logger.warn(
                f"【全量STRM生成】{self.strm_fail_count} 个 STRM 文件生成失败，{self.mediainfo_fail_count} 个媒体数据文件下载失败"
            )
        if self.remove_unless_strm_count != 0:
            logger.warn(
                f"【全量STRM生成】清理 {self.remove_unless_strm_count} 个失效 STRM 文件"
            )

    def get_generate_total(self):
        """
        输出总共生成文件个数
        """
        return (
            self.strm_count,
            self.mediainfo_count,
            self.strm_fail_count,
            self.mediainfo_fail_count,
            self.remove_unless_strm_count,
        )


class ShareStrmHelper:
    """
    根据分享生成STRM
    """

    def __init__(self, client: P115Client, mediainfodownloader: MediaInfoDownloader):
        self.rmt_mediaext = [
            f".{ext.strip()}"
            for ext in configer.get_config("user_rmt_mediaext")
            .replace("，", ",")
            .split(",")
        ]
        self.download_mediaext = [
            f".{ext.strip()}"
            for ext in configer.get_config("user_download_mediaext")
            .replace("，", ",")
            .split(",")
        ]
        self.auto_download_mediainfo = configer.get_config(
            "share_strm_auto_download_mediainfo_enabled"
        )
        self.client = client
        self.mediainfodownloader = mediainfodownloader
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

    def generate_strm_files(
        self,
        share_code: str,
        receive_code: str,
        file_id: str,
        file_path: str,
        pan_file_name: str,
        file_size: Optional[str] = None,
    ):
        """
        生成 STRM 文件
        """
        if not PathUtils.has_prefix(file_path, self.share_media_path):
            logger.debug(
                "【分享STRM生成】此文件不在用户设置分享目录下，跳过网盘路径: %s",
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
                        }
                    )
                    return

            if file_path.suffix.lower() not in self.rmt_mediaext:
                logger.warn(
                    "【分享STRM生成】文件后缀不匹配，跳过网盘路径: %s",
                    str(file_path).replace(str(self.local_media_path), "", 1),
                )
                return

            if not (
                result := StrmGenerater.should_generate_strm(
                    original_file_name, "share", file_size
                )
            )[1]:
                logger.warn(
                    f"【分享STRM生成】{result[0]}，跳过网盘路径: {str(file_path).replace(str(self.local_media_path), '', 1)}"
                )
                return

            new_file_path.parent.mkdir(parents=True, exist_ok=True)

            if not file_id:
                logger.error(
                    f"【分享STRM生成】{original_file_name} 不存在 id 值，无法生成 STRM 文件"
                )
                self.strm_fail_dict[str(new_file_path)] = "不存在 id 值"
                self.strm_fail_count += 1
                return
            if not share_code:
                logger.error(
                    f"【分享STRM生成】{original_file_name} 不存在 share_code 值，无法生成 STRM 文件"
                )
                self.strm_fail_dict[str(new_file_path)] = "不存在 share_code 值"
                self.strm_fail_count += 1
                return
            if not receive_code:
                logger.error(
                    f"【分享STRM生成】{original_file_name} 不存在 receive_code 值，无法生成 STRM 文件"
                )
                self.strm_fail_dict[str(new_file_path)] = "不存在 receive_code 值"
                self.strm_fail_count += 1
                return

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

    def get_share_list_creata_strm(
        self,
        cid: int = 0,
        current_path: str = "",
        share_code: str = "",
        receive_code: str = "",
    ):
        """
        获取分享文件，生成 STRM
        """
        for item in share_iterdir(
            self.client, receive_code=receive_code, share_code=share_code, cid=int(cid)
        ):
            item_path = (
                f"{current_path}/{item['name']}" if current_path else "/" + item["name"]
            )

            if item["is_dir"]:
                if self.strm_count != 0 and self.strm_count % 100 == 0:
                    logger.info("【分享STRM生成】休眠 1s 后继续生成")
                    time.sleep(1)
                self.get_share_list_creata_strm(
                    cid=int(item["id"]),
                    current_path=item_path,
                    share_code=share_code,
                    receive_code=receive_code,
                )
            else:
                item_with_path = dict(item)
                item_with_path["path"] = item_path
                file_size = item_with_path.get("size", None)
                self.generate_strm_files(
                    share_code=share_code,
                    receive_code=receive_code,
                    file_id=item_with_path["id"],
                    file_path=item_with_path["path"],
                    pan_file_name=item_with_path["name"],
                    file_size=int(file_size) if file_size else None,
                )

    def download_mediainfo(self):
        """
        下载媒体信息文件
        """
        self.mediainfo_count, self.mediainfo_fail_count, self.mediainfo_fail_dict = (
            self.mediainfodownloader.auto_downloader(
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
        return (
            self.strm_count,
            self.mediainfo_count,
            self.strm_fail_count,
            self.mediainfo_fail_count,
        )


class TransferStrmHelper:
    """
    处理事件事件STRM文件生成
    """

    @property
    def transfer_service_infos(self) -> Optional[Dict[str, ServiceInfo]]:
        """
        监控MP整理 媒体服务器服务信息
        """
        if not configer.get_config("transfer_monitor_mediaservers"):
            logger.warning("尚未配置媒体服务器，请检查配置")
            return None

        mediaserver_helper = MediaServerHelper()

        services = mediaserver_helper.get_services(
            name_filters=configer.get_config("transfer_monitor_mediaservers")
        )
        if not services:
            logger.warning("获取媒体服务器实例失败，请检查配置")
            return None

        active_services = {}
        for service_name, service_info in services.items():
            if service_info.instance.is_inactive():
                logger.warning(f"媒体服务器 {service_name} 未连接，请检查配置")
            else:
                active_services[service_name] = service_info

        if not active_services:
            logger.warning("没有已连接的媒体服务器，请检查配置")
            return None

        return active_services

    def refresh_media_server(self, item_dest_name: str, strm_target_path, mediainfo):
        """
        刷新媒体服务器
        """
        if not self.transfer_service_infos:
            return

        logger.info(f"【监控整理STRM生成】 {item_dest_name} 开始刷新媒体服务器")

        if configer.get_config("transfer_mp_mediaserver_paths"):
            status, mediaserver_path, moviepilot_path = PathUtils.get_media_path(
                configer.get_config("transfer_mp_mediaserver_paths"),
                strm_target_path,
            )
            if status:
                logger.info(
                    f"【监控整理STRM生成】 {item_dest_name} 刷新媒体服务器目录替换中..."
                )
                strm_target_path = strm_target_path.replace(
                    moviepilot_path, mediaserver_path
                ).replace("\\", "/")
                logger.info(
                    f"【监控整理STRM生成】刷新媒体服务器目录替换: {moviepilot_path} --> {mediaserver_path}"
                )
                logger.info(
                    f"【监控整理STRM生成】刷新媒体服务器目录: {strm_target_path}"
                )
        items = [
            RefreshMediaItem(
                title=mediainfo.title,
                year=mediainfo.year,
                type=mediainfo.type,
                category=mediainfo.category,
                target_path=Path(strm_target_path),
            )
        ]
        for name, service in self.transfer_service_infos.items():
            if hasattr(service.instance, "refresh_library_by_items"):
                service.instance.refresh_library_by_items(items)
            elif hasattr(service.instance, "refresh_root_library"):
                service.instance.refresh_root_library()
            else:
                logger.warning(
                    f"【监控整理STRM生成】 {item_dest_name} {name} 不支持刷新"
                )

    def generate_strm_files(
        self,
        target_dir: str,
        pan_media_dir: str,
        item_dest_path: Path,
        basename: str,
        url: str,
    ):
        """
        依据网盘路径生成 STRM 文件
        """
        try:
            pan_media_dir = str(Path(pan_media_dir))
            pan_path = Path(item_dest_path).parent
            pan_path = str(Path(pan_path))
            if PathUtils.has_prefix(pan_path, pan_media_dir):
                pan_path = pan_path[len(pan_media_dir) :].lstrip("/").lstrip("\\")
            file_path = Path(target_dir) / pan_path
            file_name = basename + ".strm"
            new_file_path = file_path / file_name
            new_file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(new_file_path, "w", encoding="utf-8") as file:
                file.write(url)
            logger.info(
                "【监控整理STRM生成】生成 STRM 文件成功: %s", str(new_file_path)
            )
            return True, str(new_file_path)
        except Exception as e:  # noqa: F841
            sentry_manager.sentry_hub.capture_exception(e)
            logger.error(
                "【监控整理STRM生成】生成 %s 文件失败: %s", str(new_file_path), e
            )
            return False, None

    def do_generate(self, item, mediainfodownloader: MediaInfoDownloader):
        """
        生成 STRM 操作
        """
        _get_url = StrmUrlGetter()

        # 转移信息
        item_transfer = item.get("transferinfo")
        if isinstance(item_transfer, dict):
            item_transfer = TransferInfo(**item_transfer)
        # 媒体信息
        mediainfo: MediaInfo = item.get("mediainfo")
        # 元数据信息
        meta: MetaBase = item.get("meta")

        item_dest_storage = item_transfer.target_item.storage
        if item_dest_storage != "u115":
            return

        # 网盘目的地目录
        itemdir_dest_path = item_transfer.target_diritem.path
        # 网盘目的地路径（包含文件名称）
        item_dest_path = item_transfer.target_item.path
        # 网盘目的地文件名称
        item_dest_name = item_transfer.target_item.name
        # 网盘目的地文件名称（不包含后缀）
        item_dest_basename = item_transfer.target_item.basename
        # 网盘目的地文件 pickcode
        item_dest_pickcode = item_transfer.target_item.pickcode
        # 是否蓝光原盘
        item_bluray = SystemUtils.is_bluray_dir(Path(itemdir_dest_path))
        # 目标字幕文件清单
        subtitle_list = getattr(item_transfer, "subtitle_list_new", [])
        # 目标音频文件清单
        audio_list = getattr(item_transfer, "audio_list_new", [])

        __itemdir_dest_path, local_media_dir, pan_media_dir = PathUtils.get_media_path(
            configer.get_config("transfer_monitor_paths"), itemdir_dest_path
        )
        if not __itemdir_dest_path:
            logger.debug(
                f"【监控整理STRM生成】{item_dest_name} 路径匹配不符合，跳过整理"
            )
            return
        logger.debug("【监控整理STRM生成】匹配到网盘文件夹路径: %s", str(pan_media_dir))

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

        strm_url = _get_url.get_strm_url(item_dest_pickcode, item_dest_name)

        _databasehelper = FileDbHelper()
        _databasehelper.upsert_batch(
            _databasehelper.process_fileitem(fileitem=item_transfer.target_item)
        )

        status, strm_target_path = self.generate_strm_files(
            target_dir=local_media_dir,
            pan_media_dir=pan_media_dir,
            item_dest_path=Path(item_dest_path),
            basename=item_dest_basename,
            url=strm_url,
        )
        if not status:
            return

        try:
            storagechain = StorageChain()
            if subtitle_list:
                logger.info("【监控整理STRM生成】开始下载字幕文件")
                for _path in subtitle_list:
                    fileitem = storagechain.get_file_item(
                        storage="u115", path=Path(_path)
                    )
                    _databasehelper.upsert_batch(
                        _databasehelper.process_fileitem(fileitem)
                    )
                    download_url = mediainfodownloader.get_download_url(
                        pickcode=fileitem.pickcode
                    )
                    if not download_url:
                        logger.error(
                            f"【监控整理STRM生成】{Path(_path).name} 下载链接获取失败，无法下载该文件"
                        )
                        continue
                    _file_path = Path(local_media_dir) / Path(_path).relative_to(
                        pan_media_dir
                    )
                    mediainfodownloader.save_mediainfo_file(
                        file_path=Path(_file_path),
                        file_name=_file_path.name,
                        download_url=download_url,
                    )

            if audio_list:
                logger.info("【监控整理STRM生成】开始下载音频文件")
                for _path in audio_list:
                    fileitem = storagechain.get_file_item(
                        storage="u115", path=Path(_path)
                    )
                    _databasehelper.upsert_batch(
                        _databasehelper.process_fileitem(fileitem)
                    )
                    download_url = mediainfodownloader.get_download_url(
                        pickcode=fileitem.pickcode
                    )
                    if not download_url:
                        logger.error(
                            f"【监控整理STRM生成】{Path(_path).name} 下载链接获取失败，无法下载该文件"
                        )
                        continue
                    _file_path = Path(local_media_dir) / Path(_path).relative_to(
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
            self.refresh_media_server(
                item_dest_name=item_dest_name,
                strm_target_path=strm_target_path,
                mediainfo=mediainfo,
            )
