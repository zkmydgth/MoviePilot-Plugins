import re
from collections import defaultdict
from pathlib import Path
from time import time
from typing import List, Dict, Tuple, Optional

from p115client import P115Client, check_response
from p115client.tool.edit import update_name

from app.chain.storage import StorageChain
from app.chain.transfer import TransferChain, task_lock
from app.core.config import settings
from app.core.event import eventmanager
from app.core.meta import MetaBase
from app.core.metainfo import MetaInfoPath
from app.db.systemconfig_oper import SystemConfigOper
from app.db.transferhistory_oper import TransferHistoryOper
from app.helper.directory import DirectoryHelper
from app.log import logger
from app.schemas import FileItem, Notification, TransferInfo
from app.schemas import TransferTask as MPTransferTask
from app.schemas.types import EventType, MediaType, NotificationType, SystemConfigKey
from app.utils.string import StringUtils

from ...schemas.transfer import TransferTask, RelatedFile
from .cache_updater import CacheUpdater


class TransferHandler:
    """
    115 整理执行器
    """

    def __init__(self, client: P115Client, storage_name: str = "115网盘Plus"):
        """
        初始化整理执行器

        :param client: 115 客户端实例
        :param storage_name: 存储名称
        """
        self.client = client
        self.storage_name = storage_name
        self.storage_chain = StorageChain()
        self.history_oper = TransferHistoryOper()

        self.cache_updater = CacheUpdater.create(
            client=client, storage_name=storage_name
        )

        logger.info(f"【整理接管】初始化整理执行器，存储: {storage_name}")

    def _get_folder(self, path: Path) -> Optional[FileItem]:
        """
        获取目录，如目录不存在则创建

        :param path: 目录路径

        :return: 目录文件项，如果创建失败则返回None
        """
        folder_item = self.storage_chain.get_file_item(
            storage=self.storage_name, path=path
        )
        if folder_item and folder_item.type == "dir":
            return folder_item

        try:
            resp = self.client.fs_makedirs_app(path.as_posix(), pid=0)
            check_response(resp)
            logger.debug(f"【整理接管】get_folder 创建目录: {path} (ID: {resp['cid']})")
            modify_time = int(time())
            folder_item = FileItem(
                storage=self.storage_name,
                fileid=str(resp["cid"]),
                path=path.as_posix() + "/",
                name=path.name,
                basename=path.name,
                type="dir",
                modify_time=modify_time,
                pickcode=self.client.to_pickcode(resp["cid"]),
            )
            self.cache_updater.update_folder_cache(folder_item)
            return folder_item
        except Exception as e:
            logger.error(f"【整理接管】创建目录失败 ({path}): {e}", exc_info=True)
            return None

    def process_batch(self, tasks: List[TransferTask]) -> None:
        """
        批量处理整理任务

        :param tasks: 任务列表
        """
        if not tasks:
            logger.warn("【整理接管】任务列表为空，跳过处理")
            return

        logger.info(f"【整理接管】开始批量处理 {len(tasks)} 个任务")

        try:
            # 发现关联文件
            self._discover_related_files(tasks)

            # 批量创建目标目录
            self._batch_create_directories(tasks)

            # 批量移动/复制文件
            self._batch_move_or_copy(tasks)

            # 批量重命名文件
            self._batch_rename_files(tasks)

            # 记录历史
            self._record_history(tasks)

            logger.info(f"【整理接管】批量处理完成，共处理 {len(tasks)} 个任务")
        except Exception as e:
            logger.error(f"【整理接管】批量处理异常: {e}", exc_info=True)
            for task in tasks:
                self._record_fail(task, str(e), is_batch_failure=True)

    def _discover_related_files(self, tasks: List[TransferTask]) -> None:
        """
        发现关联文件（字幕、音轨）

        :param tasks: 任务列表
        """
        logger.info("【整理接管】开始发现关联文件")

        # 按源目录分组任务
        tasks_by_dir: Dict[Path, List[TransferTask]] = defaultdict(list)
        for task in tasks:
            source_dir = Path(task.fileitem.path).parent
            tasks_by_dir[source_dir].append(task)

        # 对每个源目录，列出文件并匹配关联文件
        for source_dir, dir_tasks in tasks_by_dir.items():
            try:
                source_fileitem = FileItem(
                    storage=self.storage_name,
                    path=str(source_dir) + "/",
                    type="dir",
                )
                files = self.storage_chain.list_files(
                    fileitem=source_fileitem, recursion=False
                )

                if not files:
                    logger.debug(
                        f"【整理接管】源目录 {source_dir} 为空，跳过关联文件发现"
                    )
                    continue

                # 为每个任务匹配关联文件
                for task in dir_tasks:
                    main_video_path = Path(task.fileitem.path)
                    main_video_metainfo = MetaInfoPath(main_video_path)

                    # 匹配字幕文件
                    TransferHandler._match_subtitle_files(
                        task, main_video_path, main_video_metainfo, files
                    )

                    # 匹配音轨文件
                    TransferHandler._match_audio_track_files(
                        task, main_video_path, files
                    )

            except Exception as e:
                logger.error(
                    f"【整理接管】发现关联文件失败 (目录: {source_dir}): {e}",
                    exc_info=True,
                )

        # 统计关联文件数量
        total_related = sum(len(task.related_files) for task in tasks)
        logger.info(f"【整理接管】关联文件发现完成，共发现 {total_related} 个关联文件")

    @staticmethod
    def _match_subtitle_files(
        task: TransferTask,
        main_video_path: Path,
        main_video_metainfo: MetaBase,
        files: List[FileItem],
    ) -> None:
        """
        匹配字幕文件

        :param task: 任务
        :param main_video_path: 主视频路径
        :param main_video_metainfo: 主视频元信息
        :param files: 目录文件列表
        """
        _zhcn_sub_re = (
            r"([.\[(](((zh[-_])?(cn|ch[si]|sg|sc))|zho?"
            r"|chinese|(cn|ch[si]|sg|zho?|eng)[-_&]?(cn|ch[si]|sg|zho?|eng)"
            r"|简[体中]?)[.\])])"
            r"|([\u4e00-\u9fa5]{0,3}[中双][\u4e00-\u9fa5]{0,2}[字文语][\u4e00-\u9fa5]{0,3})"
            r"|简体|简中|JPSC|sc_jp"
            r"|(?<![a-z0-9])gb(?![a-z0-9])"
        )
        _zhtw_sub_re = (
            r"([.\[(](((zh[-_])?(hk|tw|cht|tc))"
            r"|(cht|eng)[-_&]?(cht|eng)"
            r"|繁[体中]?)[.\])])"
            r"|繁体中[文字]|中[文字]繁体|繁体|JPTC|tc_jp"
            r"|(?<![a-z0-9])big5(?![a-z0-9])"
        )
        _eng_sub_re = r"[.\[(]eng[.\])]"

        # 筛选字幕文件
        subtitle_files = [
            f
            for f in files
            if f.path != task.fileitem.path
            and f.type == "file"
            and f.extension
            and f".{f.extension.lower()}" in settings.RMT_SUBEXT
        ]

        if not subtitle_files:
            logger.debug(f"【整理接管】{main_video_path.parent} 目录下没有找到字幕文件")
            return

        logger.debug(f"【整理接管】字幕文件清单：{[f.name for f in subtitle_files]}")

        # 匹配字幕文件
        for sub_item in subtitle_files:
            # 识别字幕文件名（去除语言标识）
            sub_file_name = re.sub(
                _zhtw_sub_re,
                ".",
                re.sub(_zhcn_sub_re, ".", sub_item.name, flags=re.I),
                flags=re.I,
            )
            sub_file_name = re.sub(_eng_sub_re, ".", sub_file_name, flags=re.I)
            sub_metainfo = MetaInfoPath(Path(sub_item.path))

            # 匹配字幕文件名
            if (
                main_video_path.stem == Path(sub_file_name).stem
                or (
                    sub_metainfo.cn_name
                    and sub_metainfo.cn_name == main_video_metainfo.cn_name
                )
                or (
                    sub_metainfo.en_name
                    and sub_metainfo.en_name == main_video_metainfo.en_name
                )
            ):
                # 检查 part、season、episode 是否匹配
                if (
                    main_video_metainfo.part
                    and main_video_metainfo.part != sub_metainfo.part
                ):
                    continue
                if (
                    main_video_metainfo.season
                    and main_video_metainfo.season != sub_metainfo.season
                ):
                    continue
                if (
                    main_video_metainfo.episode
                    and main_video_metainfo.episode != sub_metainfo.episode
                ):
                    continue

                # 确定字幕语言类型
                new_file_type = ""
                if re.search(_zhcn_sub_re, sub_item.name, re.I):
                    new_file_type = ".chi.zh-cn"
                elif re.search(_zhtw_sub_re, sub_item.name, re.I):
                    new_file_type = ".zh-tw"
                elif re.search(_eng_sub_re, sub_item.name, re.I):
                    new_file_type = ".eng"

                # 生成字幕文件目标路径
                file_ext = f".{sub_item.extension}"
                new_sub_tag_dict = {
                    ".eng": ".英文",
                    ".chi.zh-cn": ".简体中文",
                    ".zh-tw": ".繁体中文",
                }
                new_sub_tag_list = [
                    (
                        (
                            ".default" + new_file_type
                            if (
                                (
                                    settings.DEFAULT_SUB == "zh-cn"
                                    and new_file_type == ".chi.zh-cn"
                                )
                                or (
                                    settings.DEFAULT_SUB == "zh-tw"
                                    and new_file_type == ".zh-tw"
                                )
                                or (
                                    settings.DEFAULT_SUB == "eng"
                                    and new_file_type == ".eng"
                                )
                            )
                            else new_file_type
                        )
                        if t == 0
                        else f"{new_file_type}{new_sub_tag_dict.get(new_file_type, '')}({t})"
                    )
                    for t in range(6)
                ]

                # 尝试找到可用的目标文件名
                for new_sub_tag in new_sub_tag_list:
                    target_path = task.target_path.with_name(
                        task.target_path.stem + new_sub_tag + file_ext
                    )

                    # 添加到关联文件列表（只添加第一个匹配的）
                    related_file = RelatedFile(
                        fileitem=sub_item,
                        target_path=target_path,
                        file_type="subtitle",
                    )
                    task.related_files.append(related_file)

                    logger.debug(
                        f"【整理接管】发现字幕文件: {sub_item.name} -> {target_path.name}"
                    )
                    break

    @staticmethod
    def _match_audio_track_files(
        task: TransferTask,
        main_video_path: Path,
        files: List[FileItem],
    ) -> None:
        """
        匹配音轨文件

        :param task: 任务
        :param main_video_path: 主视频路径
        :param files: 目录文件列表
        """
        audio_track_files = [
            file
            for file in files
            if file.path != task.fileitem.path
            and Path(file.name).stem == main_video_path.stem
            and file.type == "file"
            and file.extension
            and f".{file.extension.lower()}" in settings.RMT_AUDIOEXT
        ]

        if not audio_track_files:
            logger.debug(
                f"【整理接管】{main_video_path.parent} 目录下没有找到匹配的音轨文件"
            )
            return

        logger.debug(f"【整理接管】音轨文件清单：{[f.name for f in audio_track_files]}")

        # 添加音轨文件
        for track_file in audio_track_files:
            track_ext = f".{track_file.extension}"
            target_path = task.target_path.with_name(task.target_path.stem + track_ext)

            related_file = RelatedFile(
                fileitem=track_file,
                target_path=target_path,
                file_type="audio_track",
            )
            task.related_files.append(related_file)

            logger.debug(
                f"【整理接管】发现音轨文件: {track_file.name} -> {target_path.name}"
            )

    def _batch_create_directories(self, tasks: List[TransferTask]) -> None:
        """
        批量创建目标目录

        :param tasks: 任务列表
        """
        logger.info("【整理接管】开始批量创建目标目录")

        # 收集所有目标目录
        target_dirs: set[Path] = set()
        for task in tasks:
            target_dir = task.target_dir
            target_dirs.add(target_dir)
            # 关联文件的目标目录
            for related_file in task.related_files:
                related_dir = related_file.target_path.parent
                target_dirs.add(related_dir)

        # 搜集子目录
        leaf_dirs: set[Path] = set()
        for target_dir in target_dirs:
            is_parent = any(
                target_dir != other_dir and target_dir in other_dir.parents
                for other_dir in target_dirs
            )
            if not is_parent:
                leaf_dirs.add(target_dir)

        # 批量创建子目录（自动递归）
        created_count = 0
        for target_dir in leaf_dirs:
            try:
                folder_item = self._get_folder(target_dir)
                if folder_item:
                    created_count += 1
                    logger.debug(
                        f"【整理接管】创建目录: {target_dir} (ID: {folder_item.fileid if folder_item.fileid else 'N/A'})"
                    )
                else:
                    logger.warn(f"【整理接管】创建目录失败: {target_dir}")

            except Exception as e:
                logger.error(
                    f"【整理接管】创建目录失败 ({target_dir}): {e}",
                    exc_info=True,
                )

        logger.info(f"【整理接管】目录创建完成，共创建 {created_count} 个目录")

    def _batch_move_or_copy(self, tasks: List[TransferTask]) -> None:
        """
        批量移动/复制文件（按目标目录分组）

        :param tasks: 任务列表
        """
        logger.info("【整理接管】开始批量移动/复制文件")

        # 按目标目录和操作类型分组
        operations: Dict[
            Tuple[Path, str],
            List[Tuple[FileItem, str, TransferTask, bool, Optional[RelatedFile]]],
        ] = defaultdict(list)

        for task in tasks:
            target_dir = task.target_dir
            transfer_type = task.transfer_type

            # 主视频 (is_main=True, related_file=None)
            operations[(target_dir, transfer_type)].append(
                (task.fileitem, task.target_name, task, True, None)
            )

            # 关联文件 (is_main=False, related_file=related_file)
            for related_file in task.related_files:
                related_dir = related_file.target_path.parent
                operations[(related_dir, transfer_type)].append(
                    (
                        related_file.fileitem,
                        related_file.target_path.name,
                        task,
                        False,
                        related_file,
                    )
                )

        # 批量执行移动/复制
        for (target_dir, transfer_type), files in operations.items():
            try:
                # 获取目标目录的 fileid
                try:
                    folder_item = self._get_folder(target_dir)
                    if not folder_item or not folder_item.fileid:
                        logger.error(
                            f"【整理接管】无法获取或创建目标目录: {target_dir}"
                        )
                        continue
                    target_dir_id = int(folder_item.fileid)
                except Exception as e:
                    logger.error(
                        f"【整理接管】无法获取目标目录ID: {target_dir}, 错误: {e}"
                    )
                    continue

                # 批量检查目标文件是否已存在
                existing_files_map: Dict[str, FileItem] = {}
                try:
                    existing_files = self.storage_chain.list_files(
                        fileitem=folder_item,
                    )
                    if existing_files:
                        # 创建文件名到 FileItem 的映射
                        for existing_file in existing_files:
                            if existing_file.type == "file":
                                existing_files_map[existing_file.name] = existing_file
                except Exception as list_error:
                    logger.warn(
                        f"【整理接管】批量列出目标目录文件失败 ({target_dir}): {list_error}，将跳过文件存在性检查"
                    )

                # 收集 文件 ID 和 文件信息 映射，并处理目标文件已存在的情况
                file_ids = []
                file_mapping: Dict[
                    int, Tuple[TransferTask, bool, str, Optional[RelatedFile]]
                ] = {}
                files_to_delete: List[FileItem] = []

                for fileitem, target_name, task, is_main, related_file in files:
                    if not fileitem.fileid:
                        logger.warn(f"【整理接管】文件缺少 fileid: {fileitem.path}")
                        continue

                    # 检查目标文件是否已存在
                    existing_item = existing_files_map.get(target_name)

                    # 判断是否为附加文件（字幕、音轨）
                    is_extra_file = False
                    if related_file:
                        # 关联文件（字幕、音轨）强制覆盖
                        is_extra_file = True
                    elif fileitem.extension:
                        # 检查文件扩展名是否为附加文件类型
                        file_ext = f".{fileitem.extension.lower()}"
                        is_extra_file = file_ext in (
                            settings.RMT_SUBEXT + settings.RMT_AUDIOEXT
                        )

                    if existing_item:
                        # 目标文件已存在
                        if is_extra_file:
                            # 附加文件强制覆盖
                            over_flag = True
                            logger.info(
                                f"【整理接管】目标文件已存在，附加文件强制覆盖: {target_dir / target_name}"
                            )
                            files_to_delete.append(existing_item)
                            existing_files_map.pop(target_name, None)
                        else:
                            # 主视频文件，根据 overwrite_mode 决定是否覆盖
                            overwrite_mode = task.overwrite_mode or "never"
                            over_flag = False
                            should_skip = False

                            if overwrite_mode == "always":
                                # 总是覆盖同名文件
                                over_flag = True
                                logger.info(
                                    f"【整理接管】目标文件已存在，覆盖模式=always，将覆盖: {target_dir / target_name}"
                                )
                            elif overwrite_mode == "size":
                                # 存在时大覆盖小
                                source_size = fileitem.size or 0
                                target_size = existing_item.size or 0
                                if source_size > target_size:
                                    over_flag = True
                                    logger.info(
                                        f"【整理接管】目标文件已存在，覆盖模式=size，源文件更大 ({source_size} > {target_size})，将覆盖: {target_dir / target_name}"
                                    )
                                else:
                                    # 目标文件质量更好，跳过
                                    should_skip = True
                                    logger.info(
                                        f"【整理接管】目标文件已存在，覆盖模式=size，目标文件质量更好 ({target_size} >= {source_size})，跳过: {target_dir / target_name}"
                                    )
                            elif overwrite_mode == "latest":
                                # 仅保留最新版本
                                over_flag = True
                                logger.info(
                                    f"【整理接管】目标文件已存在，覆盖模式=latest，将覆盖: {target_dir / target_name}"
                                )
                            else:  # overwrite_mode == "never" or None
                                # 存在不覆盖
                                should_skip = True
                                logger.info(
                                    f"【整理接管】目标文件已存在，覆盖模式=never，跳过: {target_dir / target_name}"
                                )

                            if should_skip:
                                # 不覆盖，跳过此文件
                                if is_main:
                                    task.fileitem.fileid = existing_item.fileid
                                elif related_file:
                                    related_file.fileitem.fileid = existing_item.fileid
                                continue
                            elif over_flag:
                                # 覆盖模式，收集需要删除的文件
                                files_to_delete.append(existing_item)
                                # 从映射中移除，避免重复处理
                                existing_files_map.pop(target_name, None)
                    else:
                        # 目标文件不存在，但如果是 latest 模式，需要删除其他版本文件
                        if not is_extra_file and task.overwrite_mode == "latest":
                            # 文件不存在，但仅保留最新版本，需要删除目录下其他版本的文件
                            self._delete_version_files(
                                target_dir=target_dir,
                                target_path=target_dir / target_name,
                                task=task,
                            )

                    file_id = int(fileitem.fileid)
                    file_ids.append(file_id)
                    file_mapping[file_id] = (task, is_main, target_name, related_file)

                # 批量删除已存在的文件
                if files_to_delete:
                    logger.info(
                        f"【整理接管】批量删除 {len(files_to_delete)} 个已存在的目标文件"
                    )
                    # 收集需要删除的文件 ID
                    delete_file_ids = []
                    delete_file_mapping: Dict[int, FileItem] = {}  # file_id -> FileItem
                    for existing_item in files_to_delete:
                        if existing_item.fileid:
                            file_id = int(existing_item.fileid)
                            delete_file_ids.append(file_id)
                            delete_file_mapping[file_id] = existing_item

                    if delete_file_ids:
                        try:
                            # 批量删除
                            resp = self.client.fs_delete(delete_file_ids)
                            check_response(resp)
                            for file_id in delete_file_ids:
                                self.cache_updater.remove_cache(file_id)
                            logger.info(
                                f"【整理接管】批量删除成功: {len(delete_file_ids)} 个文件"
                            )
                        except Exception as batch_delete_error:
                            logger.warn(
                                f"【整理接管】批量删除失败，尝试逐个删除: {batch_delete_error}"
                            )
                            # 回退到逐个删除
                            failed_delete_ids = []
                            for file_id in delete_file_ids:
                                try:
                                    resp = self.client.fs_delete(file_id)
                                    check_response(resp)
                                    self.cache_updater.remove_cache(file_id)
                                except Exception as single_delete_error:
                                    logger.error(
                                        f"【整理接管】删除文件失败 (file_id: {file_id}): {single_delete_error}"
                                    )
                                    failed_delete_ids.append(file_id)

                            # 如果删除失败，从待处理列表中移除对应的文件
                            for failed_file_id in failed_delete_ids:
                                existing_item = delete_file_mapping.get(failed_file_id)
                                if existing_item:
                                    # 找到对应的 file_id 并移除
                                    file_id_to_remove = None
                                    for fid, (
                                        t,
                                        is_m,
                                        t_name,
                                        rf,
                                    ) in file_mapping.items():
                                        if t_name == existing_item.name:
                                            file_id_to_remove = fid
                                            break
                                    if file_id_to_remove:
                                        file_ids.remove(file_id_to_remove)
                                        file_mapping.pop(file_id_to_remove, None)

                if not file_ids:
                    continue

                # 执行批量 移动 /复制
                if transfer_type == "move":
                    try:
                        resp = self.client.fs_move(file_ids, pid=target_dir_id)
                        check_response(resp)
                        for file_id, (
                            task,
                            is_main,
                            target_name,
                            related_file,
                        ) in file_mapping.items():
                            try:
                                target_path = target_dir / target_name
                                new_fileitem = FileItem(
                                    storage=self.storage_name,
                                    path=str(target_path),
                                    name=target_name,
                                    fileid=str(file_id),
                                    type="file",
                                    size=task.fileitem.size
                                    if is_main
                                    else (
                                        related_file.fileitem.size
                                        if related_file
                                        else 0
                                    ),
                                    modify_time=task.fileitem.modify_time
                                    if is_main
                                    else (
                                        related_file.fileitem.modify_time
                                        if related_file
                                        else 0
                                    ),
                                    pickcode=task.fileitem.pickcode
                                    if is_main
                                    else (
                                        related_file.fileitem.pickcode
                                        if related_file
                                        else None
                                    ),
                                )
                                self.cache_updater.update_file_cache(new_fileitem)
                            except Exception as cache_error:
                                logger.debug(
                                    f"【整理接管】更新移动文件缓存失败 (file_id: {file_id}): {cache_error}"
                                )
                        logger.info(
                            f"【整理接管】批量移动 {len(file_ids)} 个文件到 {target_dir}"
                        )
                    except Exception as batch_error:
                        logger.warn(
                            f"【整理接管】批量移动失败，尝试逐个移动: {batch_error}"
                        )
                        for file_id in file_ids:
                            try:
                                resp = self.client.fs_move(file_id, pid=target_dir_id)
                                check_response(resp)
                                task, is_main, target_name, related_file = (
                                    file_mapping.get(file_id, (None, False, "", None))
                                )
                                if task:
                                    try:
                                        target_path = target_dir / target_name
                                        new_fileitem = FileItem(
                                            storage=self.storage_name,
                                            path=str(target_path),
                                            name=target_name,
                                            fileid=str(file_id),
                                            type="file",
                                            size=task.fileitem.size
                                            if is_main
                                            else (
                                                related_file.fileitem.size
                                                if related_file
                                                else 0
                                            ),
                                            modify_time=task.fileitem.modify_time
                                            if is_main
                                            else (
                                                related_file.fileitem.modify_time
                                                if related_file
                                                else 0
                                            ),
                                            pickcode=task.fileitem.pickcode
                                            if is_main
                                            else (
                                                related_file.fileitem.pickcode
                                                if related_file
                                                else None
                                            ),
                                        )
                                        self.cache_updater.update_file_cache(
                                            new_fileitem
                                        )
                                    except Exception as cache_error:
                                        logger.debug(
                                            f"【整理接管】更新移动文件缓存失败 (file_id: {file_id}): {cache_error}"
                                        )
                            except Exception as single_error:
                                logger.error(
                                    f"【整理接管】移动文件失败 (file_id: {file_id}): {single_error}"
                                )
                elif transfer_type == "copy":
                    try:
                        resp = self.client.fs_copy(file_ids, pid=target_dir_id)
                        check_response(resp)
                        logger.info(
                            f"【整理接管】批量复制 {len(file_ids)} 个文件到 {target_dir}"
                        )
                        self._update_file_ids_after_copy(target_dir, file_mapping)
                        for file_id, (
                            task,
                            is_main,
                            target_name,
                            related_file,
                        ) in file_mapping.items():
                            try:
                                actual_fileid = (
                                    task.fileitem.fileid
                                    if is_main
                                    else (
                                        related_file.fileitem.fileid
                                        if related_file
                                        else None
                                    )
                                )
                                if actual_fileid:
                                    target_path = target_dir / target_name
                                    new_fileitem = FileItem(
                                        storage=self.storage_name,
                                        path=str(target_path),
                                        name=target_name,
                                        fileid=actual_fileid,
                                        type="file",
                                        size=task.fileitem.size
                                        if is_main
                                        else (
                                            related_file.fileitem.size
                                            if related_file
                                            else 0
                                        ),
                                        modify_time=task.fileitem.modify_time
                                        if is_main
                                        else (
                                            related_file.fileitem.modify_time
                                            if related_file
                                            else 0
                                        ),
                                        pickcode=task.fileitem.pickcode
                                        if is_main
                                        else (
                                            related_file.fileitem.pickcode
                                            if related_file
                                            else None
                                        ),
                                    )
                                    self.cache_updater.update_file_cache(new_fileitem)
                            except Exception as cache_error:
                                logger.debug(
                                    f"【整理接管】更新复制文件缓存失败 (file_id: {file_id}): {cache_error}"
                                )
                    except Exception as batch_error:
                        logger.warn(
                            f"【整理接管】批量复制失败，尝试逐个复制: {batch_error}"
                        )
                        for file_id in file_ids:
                            try:
                                resp = self.client.fs_copy(file_id, pid=target_dir_id)
                                check_response(resp)
                                task, is_main, target_name, related_file = file_mapping[
                                    file_id
                                ]
                                target_path = target_dir / target_name
                                self._update_single_file_id_after_copy(
                                    task, is_main, target_path, related_file
                                )
                                try:
                                    actual_fileid = (
                                        task.fileitem.fileid
                                        if is_main
                                        else (
                                            related_file.fileitem.fileid
                                            if related_file
                                            else None
                                        )
                                    )
                                    if actual_fileid:
                                        new_fileitem = FileItem(
                                            storage=self.storage_name,
                                            path=str(target_path),
                                            name=target_name,
                                            fileid=actual_fileid,
                                            type="file",
                                            size=task.fileitem.size
                                            if is_main
                                            else (
                                                related_file.fileitem.size
                                                if related_file
                                                else 0
                                            ),
                                            modify_time=task.fileitem.modify_time
                                            if is_main
                                            else (
                                                related_file.fileitem.modify_time
                                                if related_file
                                                else 0
                                            ),
                                            pickcode=task.fileitem.pickcode
                                            if is_main
                                            else (
                                                related_file.fileitem.pickcode
                                                if related_file
                                                else None
                                            ),
                                        )
                                        self.cache_updater.update_file_cache(
                                            new_fileitem
                                        )
                                except Exception as cache_error:
                                    logger.debug(
                                        f"【整理接管】更新复制文件缓存失败 (file_id: {file_id}): {cache_error}"
                                    )
                            except Exception as single_error:
                                logger.error(
                                    f"【整理接管】复制文件失败 (file_id: {file_id}): {single_error}"
                                )

            except Exception as e:
                logger.error(
                    f"【整理接管】批量移动/复制失败 (目录: {target_dir}, 类型: {transfer_type}): {e}",
                    exc_info=True,
                )

        logger.info("【整理接管】批量移动/复制完成")

    def _delete_version_files(
        self,
        target_dir: Path,
        target_path: Path,
        task: TransferTask,
    ) -> None:
        """
        删除目录下的所有版本文件（仅保留最新版本）

        :param target_dir: 目标目录
        :param target_path: 目标文件路径
        :param task: 任务
        """
        try:
            # 识别文件中的季集信息
            meta = MetaInfoPath(target_path)
            season = meta.season
            episode = meta.episode

            if season is None and episode is None:
                # 没有季集信息，无法判断版本，跳过
                logger.debug(
                    f"【整理接管】目标文件无季集信息，跳过版本删除: {target_path}"
                )
                return

            logger.info(
                f"【整理接管】覆盖模式=latest，正在删除目标目录中其它版本的文件: {target_dir}"
            )

            # 获取目标目录
            folder_item = self._get_folder(target_dir)
            if not folder_item:
                logger.warn(f"【整理接管】无法获取目标目录: {target_dir}")
                return

            # 列出目录下所有文件
            files = self.storage_chain.list_files(fileitem=folder_item)
            if not files:
                logger.debug(f"【整理接管】目录 {target_dir} 中没有文件")
                return

            # 收集需要删除的文件
            files_to_delete = []
            for file in files:
                if file.type != "file":
                    continue
                if not file.extension:
                    continue
                # 只处理媒体文件
                file_ext = f".{file.extension.lower()}"
                if file_ext not in settings.RMT_MEDIAEXT:
                    continue
                # 跳过目标文件本身
                if Path(file.path) == target_path:
                    continue
                # 识别文件中的季集信息
                file_meta = MetaInfoPath(Path(file.path))
                # 相同季集的文件才删除
                if file_meta.season == season and file_meta.episode == episode:
                    files_to_delete.append(file)
                    logger.info(f"【整理接管】发现同版本文件，将删除: {file.name}")

            if not files_to_delete:
                logger.debug(
                    f"【整理接管】目录 {target_dir} 中没有找到同版本的其他文件"
                )
                return

            # 批量删除
            delete_file_ids = []
            for file in files_to_delete:
                if file.fileid:
                    delete_file_ids.append(int(file.fileid))

            if delete_file_ids:
                try:
                    resp = self.client.fs_delete(delete_file_ids)
                    check_response(resp)
                    for file_id in delete_file_ids:
                        self.cache_updater.remove_cache(file_id)
                    logger.info(
                        f"【整理接管】批量删除版本文件成功: {len(delete_file_ids)} 个文件"
                    )
                except Exception as e:
                    logger.error(
                        f"【整理接管】批量删除版本文件失败: {e}", exc_info=True
                    )

        except Exception as e:
            logger.error(f"【整理接管】删除版本文件异常: {e}", exc_info=True)

    def _update_file_ids_after_copy(
        self,
        target_dir: Path,
        file_mapping: Dict[int, Tuple[TransferTask, bool, str, Optional[RelatedFile]]],
    ) -> None:
        """
        批量更新复制后的 文件 ID

        :param target_dir: 目标目录
        :param file_mapping: 文件ID到任务信息的映射
        """
        try:
            # 获取目标目录的文件列表
            target_dir_fileitem = FileItem(
                storage=self.storage_name,
                path=str(target_dir) + "/",
                type="dir",
            )
            files = self.storage_chain.list_files(
                fileitem=target_dir_fileitem, recursion=False
            )

            if not files:
                logger.warn(f"【整理接管】目标目录 {target_dir} 为空，无法更新文件ID")
                return

            # 创建文件名到文件项的映射
            file_map: Dict[str, FileItem] = {
                f.name: f for f in files if f.type == "file"
            }

            # 更新每个文件的任务信息
            for file_id, (
                task,
                is_main,
                target_name,
                related_file,
            ) in file_mapping.items():
                if target_name in file_map:
                    new_fileitem = file_map[target_name]
                    if new_fileitem.fileid:
                        if is_main:
                            # 更新主视频的 fileid
                            task.fileitem.fileid = new_fileitem.fileid
                            logger.debug(
                                f"【整理接管】更新主视频文件ID: {target_name} -> {new_fileitem.fileid}"
                            )
                        elif related_file:
                            # 更新关联文件的 fileid
                            related_file.fileitem.fileid = new_fileitem.fileid
                            logger.debug(
                                f"【整理接管】更新关联文件ID: {target_name} -> {new_fileitem.fileid}"
                            )
                else:
                    logger.warn(
                        f"【整理接管】未找到复制后的文件: {target_name} (目录: {target_dir})"
                    )

        except Exception as e:
            logger.error(
                f"【整理接管】批量更新文件ID失败 (目录: {target_dir}): {e}",
                exc_info=True,
            )

    def _update_single_file_id_after_copy(
        self,
        task: TransferTask,
        is_main: bool,
        target_path: Path,
        related_file: Optional[RelatedFile],
    ) -> None:
        """
        单个文件复制后更新 文件 ID

        :param task: 任务
        :param is_main: 是否为主视频
        :param target_path: 目标路径
        :param related_file: 关联文件（如果不是主视频）
        """
        try:
            file_item = self.storage_chain.get_file_item(
                storage=self.storage_name, path=target_path
            )
            if file_item and file_item.fileid:
                if is_main:
                    task.fileitem.fileid = file_item.fileid
                    logger.debug(
                        f"【整理接管】更新主视频文件ID: {target_path.name} -> {file_item.fileid}"
                    )
                elif related_file:
                    related_file.fileitem.fileid = file_item.fileid
                    logger.debug(
                        f"【整理接管】更新关联文件ID: {target_path.name} -> {file_item.fileid}"
                    )
            else:
                logger.warn(f"【整理接管】未找到复制后的文件: {target_path}")
        except Exception as e:
            logger.warn(f"【整理接管】更新文件ID失败 ({target_path}): {e}")

    def _batch_rename_files(self, tasks: List[TransferTask]) -> None:
        """
        批量重命名文件

        :param tasks: 任务列表
        """
        logger.info("【整理接管】开始批量重命名文件")

        # 收集需要重命名的文件（file_id, new_name）
        rename_items: List[Tuple[int, str]] = []

        for task in tasks:
            # 检查主视频是否需要重命名
            source_name = Path(task.fileitem.path).name
            target_name = task.target_name
            if source_name != target_name and task.fileitem.fileid:
                rename_items.append((int(task.fileitem.fileid), target_name))

            # 检查关联文件是否需要重命名
            for related_file in task.related_files:
                source_name = Path(related_file.fileitem.path).name
                target_name = related_file.target_path.name
                if source_name != target_name and related_file.fileitem.fileid:
                    rename_items.append(
                        (int(related_file.fileitem.fileid), target_name)
                    )

        if not rename_items:
            logger.info("【整理接管】没有需要重命名的文件")
            return

        try:
            update_name(self.client, rename_items)
            for file_id, new_name in rename_items:
                self.cache_updater.update_rename_cache(file_id, new_name)
            logger.info(
                f"【整理接管】批量重命名完成，共重命名 {len(rename_items)} 个文件"
            )
        except Exception as e:
            logger.error(f"【整理接管】批量重命名失败: {e}", exc_info=True)
            logger.info("【整理接管】尝试逐个重命名...")
            for file_id, new_name in rename_items:
                try:
                    update_name(self.client, [(file_id, new_name)])
                except Exception as rename_error:
                    logger.error(
                        f"【整理接管】重命名失败 (file_id: {file_id}, name: {new_name}): {rename_error}"
                    )

    def _record_history(self, tasks: List[TransferTask]) -> None:
        """
        记录转移历史

        :param tasks: 任务列表
        """
        logger.info("【整理接管】开始记录转移历史")

        for task in tasks:
            try:
                # 构造目标文件项
                target_fileitem = FileItem(
                    storage=self.storage_name,
                    path=str(task.target_path),
                    name=task.target_name,
                    fileid=task.fileitem.fileid,
                    type="file",
                    size=task.fileitem.size,
                    modify_time=task.fileitem.modify_time,
                    pickcode=task.fileitem.pickcode,
                )

                # 构造目标目录项
                target_diritem = FileItem(
                    storage=self.storage_name,
                    path=str(task.target_dir) + "/",
                    name=task.target_dir.name,
                    type="dir",
                )

                # 构造 TransferInfo
                transferinfo = TransferInfo(
                    success=True,
                    fileitem=task.fileitem,
                    target_item=target_fileitem,
                    target_diritem=target_diritem,
                    transfer_type=task.transfer_type,
                    file_list=[task.fileitem.path],
                    file_list_new=[target_fileitem.path],
                    need_scrape=task.scrape or False,
                    need_notify=task.need_notify
                    if task.need_notify is not None
                    else True,
                )

                # 添加关联文件到文件列表
                for related_file in task.related_files:
                    transferinfo.file_list.append(related_file.fileitem.path)
                    transferinfo.file_list_new.append(str(related_file.target_path))
                    if related_file.file_type == "subtitle":
                        transferinfo.subtitle_list.append(related_file.fileitem.path)
                        transferinfo.subtitle_list_new.append(
                            str(related_file.target_path)
                        )
                    elif related_file.file_type == "audio_track":
                        transferinfo.audio_list.append(related_file.fileitem.path)
                        transferinfo.audio_list_new.append(
                            str(related_file.target_path)
                        )

                # 记录成功历史（文件数量和大小在 is_finished 时更新）
                self.history_oper.add_success(
                    fileitem=task.fileitem,
                    mode=task.transfer_type,
                    meta=task.meta,
                    mediainfo=task.mediainfo,
                    transferinfo=transferinfo,
                    downloader=task.downloader,
                    download_hash=task.download_hash,
                )

                # 标记任务完成（必须在检查 is_success 之前调用）
                try:
                    chain = TransferChain()
                    if hasattr(chain, "jobview"):
                        mp_task = MPTransferTask(
                            fileitem=task.fileitem,
                            mediainfo=task.mediainfo,
                            meta=task.meta,
                        )
                        chain.jobview.finish_task(mp_task)
                        logger.debug(f"【整理接管】标记任务完成: {task.fileitem.path}")
                except Exception as e:
                    logger.warn(f"【整理接管】标记任务完成失败: {e}", exc_info=True)

                # 发送整理完成事件
                eventmanager.send_event(
                    EventType.TransferComplete,
                    {
                        "fileitem": task.fileitem,
                        "meta": task.meta,
                        "mediainfo": task.mediainfo,
                        "transferinfo": transferinfo,
                        "downloader": task.downloader,
                        "download_hash": task.download_hash,
                    },
                )

                # 登记转移成功文件清单到 _success_target_files
                try:
                    chain = TransferChain()
                    with task_lock:
                        # 登记转移成功文件清单
                        target_dir_path = transferinfo.target_diritem.path
                        target_files = transferinfo.file_list_new
                        if hasattr(chain, "_success_target_files"):
                            if chain._success_target_files.get(target_dir_path):
                                chain._success_target_files[target_dir_path].extend(
                                    target_files
                                )
                            else:
                                chain._success_target_files[target_dir_path] = (
                                    target_files
                                )
                except Exception as e:
                    logger.debug(
                        f"【整理接管】登记文件清单到 _success_target_files 失败: {e}"
                    )

                # 整理完成且有成功的任务时，执行 __do_finished 逻辑
                try:
                    chain = TransferChain()
                    if hasattr(chain, "jobview") and chain.jobview.is_finished(task):
                        with task_lock:
                            # 更新文件数量和大小
                            transferinfo.file_count = (
                                chain.jobview.count(
                                    task.mediainfo, task.meta.begin_season
                                )
                                or 1
                            )
                            transferinfo.total_size = (
                                chain.jobview.size(
                                    task.mediainfo, task.meta.begin_season
                                )
                                or task.fileitem.size
                                or 0
                            )

                            # 从 _success_target_files pop 文件清单
                            if hasattr(chain, "_success_target_files"):
                                popped_files = chain._success_target_files.pop(
                                    transferinfo.target_diritem.path, []
                                )
                                if popped_files:
                                    transferinfo.file_list_new = popped_files

                            # 发送通知
                            if transferinfo.need_notify and (
                                task.background or not task.manual
                            ):
                                try:
                                    se_str = None
                                    if task.mediainfo.type == MediaType.TV:
                                        season_episodes = chain.jobview.season_episodes(
                                            task.mediainfo, task.meta.begin_season
                                        )
                                        if season_episodes:
                                            se_str = f"{task.meta.season} {StringUtils.format_ep(season_episodes)}"
                                        else:
                                            se_str = f"{task.meta.season}"
                                    chain.send_transfer_message(
                                        meta=task.meta,
                                        mediainfo=task.mediainfo,
                                        transferinfo=transferinfo,
                                        season_episode=se_str,
                                        username=task.username,
                                    )
                                except Exception as e:
                                    logger.warn(
                                        f"【整理接管】发送通知失败: {e}", exc_info=True
                                    )

                            # 发送刮削事件
                            if transferinfo.need_scrape:
                                try:
                                    eventmanager.send_event(
                                        EventType.MetadataScrape,
                                        {
                                            "meta": task.meta,
                                            "mediainfo": task.mediainfo,
                                            "fileitem": target_diritem,
                                            "file_list": transferinfo.file_list_new,
                                            "overwrite": False,
                                        },
                                    )
                                except Exception as e:
                                    logger.warn(
                                        f"【整理接管】发送刮削事件失败: {e}",
                                        exc_info=True,
                                    )
                except Exception as e:
                    logger.debug(f"【整理接管】执行完成逻辑失败: {e}", exc_info=True)

                logger.debug(
                    f"【整理接管】记录成功历史: {task.fileitem.name} -> {task.target_name}"
                )

            except Exception as e:
                logger.error(
                    f"【整理接管】记录历史失败 (任务: {task.fileitem.name}): {e}",
                    exc_info=True,
                )
                self._record_fail(task, f"记录历史失败: {e}")

        # 所有任务处理完成后，统一批量删除空目录和种子
        self._batch_delete_empty_dirs_and_torrents(tasks)

        try:
            chain = TransferChain()
            if hasattr(chain, "jobview"):
                tasks_by_media: Dict[Tuple, List[TransferTask]] = defaultdict(list)
                for task in tasks:
                    if task.mediainfo:
                        key = (
                            task.mediainfo.tmdb_id or task.mediainfo.douban_id,
                            task.meta.begin_season,
                        )
                        tasks_by_media[key].append(task)
                for (media_id, season), group_tasks in tasks_by_media.items():
                    sample_task = group_tasks[0]
                    mp_sample_task = MPTransferTask(
                        fileitem=sample_task.fileitem,
                        mediainfo=sample_task.mediainfo,
                        meta=sample_task.meta,
                    )
                    if chain.jobview.is_finished(mp_sample_task):
                        chain.jobview.remove_job(mp_sample_task)
                        logger.debug(
                            f"【整理接管】移除已完成的任务 (media_id={media_id}, season={season})"
                        )
        except Exception as e:
            logger.debug(f"【整理接管】移除任务失败: {e}", exc_info=True)

        logger.info("【整理接管】历史记录完成")

    def _batch_delete_empty_dirs_and_torrents(self, tasks: List[TransferTask]) -> None:
        """
        批量删除空目录和种子

        :param tasks: 任务列表
        """
        try:
            chain = TransferChain()
            if not hasattr(chain, "jobview"):
                logger.debug("【整理接管】jobview 不存在，跳过删除空目录")
                return

            # 按媒体信息分组任务
            tasks_by_media: Dict[Tuple, List[TransferTask]] = defaultdict(list)
            move_tasks = [task for task in tasks if task.transfer_type == "move"]
            if not move_tasks:
                logger.debug("【整理接管】没有移动模式的任务，跳过删除空目录")
                return

            for task in move_tasks:
                if task.mediainfo:
                    key = (
                        task.mediainfo.tmdb_id or task.mediainfo.douban_id,
                        task.meta.begin_season,
                    )
                    tasks_by_media[key].append(task)

            if not tasks_by_media:
                logger.debug("【整理接管】没有有效的媒体任务，跳过删除空目录")
                return

            logger.info(
                f"【整理接管】开始批量删除空目录和种子，共 {len(tasks_by_media)} 个媒体组"
            )

            # 获取整理屏蔽词
            transfer_exclude_words = SystemConfigOper().get(
                SystemConfigKey.TransferExcludeWords
            )

            # 收集所有需要删除的目录和种子
            all_dir_items_to_delete: List[FileItem] = []
            checked_parent_dirs: Dict[str, List[FileItem]] = {}

            # 遍历每个媒体组
            for (media_id, season), group_tasks in tasks_by_media.items():
                # 使用第一个任务检查 is_success（同一组的所有任务应该有相同的 mediainfo）
                sample_task = group_tasks[0]
                mp_sample_task = MPTransferTask(
                    fileitem=sample_task.fileitem,
                    mediainfo=sample_task.mediainfo,
                    meta=sample_task.meta,
                )
                is_success = chain.jobview.is_success(mp_sample_task)
                logger.debug(
                    f"【整理接管】检查媒体组 (media_id={media_id}, season={season}) is_success={is_success}"
                )

                # 如果 is_success 为 False，检查任务状态详情
                if not is_success:
                    try:
                        # 使用 JobManager 的内部方法获取任务状态
                        __mediaid__ = chain.jobview._JobManager__get_media_id(
                            media=sample_task.mediainfo,
                            season=sample_task.meta.begin_season,
                        )
                        if __mediaid__ in chain.jobview._job_view:
                            job = chain.jobview._job_view[__mediaid__]
                            task_states = [
                                f"{t.fileitem.name if t.fileitem else 'None'}: {t.state}"
                                for t in job.tasks
                            ]
                            logger.warn(
                                f"【整理接管】任务状态详情 (media_id={media_id}, season={season}): {task_states}"
                            )
                        else:
                            logger.warn(
                                f"【整理接管】媒体组 (media_id={media_id}, season={season}) 不在 job_view 中，可能已被移除"
                            )
                            logger.info(
                                "【整理接管】任务不在 job_view 中，使用传入的任务列表删除空目录"
                            )
                            # 使用传入的 group_tasks 来收集需要删除的目录
                            for t in group_tasks:
                                if t.fileitem:
                                    parent_dir_item = (
                                        t.fileitem
                                        if t.fileitem.type == "dir"
                                        else self.storage_chain.get_parent_item(
                                            t.fileitem
                                        )
                                    )
                                    if parent_dir_item and parent_dir_item.path:
                                        parent_dir_path = parent_dir_item.path
                                        if parent_dir_path not in checked_parent_dirs:
                                            collected_dirs = (
                                                self._collect_dirs_to_delete(t.fileitem)
                                            )
                                            checked_parent_dirs[parent_dir_path] = (
                                                collected_dirs
                                            )
                                            all_dir_items_to_delete.extend(
                                                collected_dirs
                                            )
                            continue
                    except Exception as e:
                        logger.debug(
                            f"【整理接管】获取任务状态详情失败: {e}", exc_info=True
                        )

                if not is_success:
                    logger.warn(
                        f"【整理接管】媒体组 (media_id={media_id}, season={season}) 未全部成功，跳过删除空目录"
                    )
                    continue

                # 获取所有成功的任务
                success_tasks = chain.jobview.success_tasks(
                    sample_task.mediainfo, sample_task.meta.begin_season
                )
                logger.debug(
                    f"【整理接管】媒体组 (media_id={media_id}, season={season}) 有 {len(success_tasks)} 个成功任务"
                )

                for t in success_tasks:
                    # 删除种子（如果满足条件）
                    if t.download_hash and hasattr(chain, "_can_delete_torrent"):
                        if chain._can_delete_torrent(
                            t.download_hash,
                            t.downloader,
                            transfer_exclude_words,
                        ):
                            if hasattr(chain, "remove_torrents"):
                                if chain.remove_torrents(
                                    t.download_hash, downloader=t.downloader
                                ):
                                    logger.info(
                                        f"【整理接管】移动模式删除种子成功：{t.download_hash}"
                                    )

                    # 收集需要删除的空目录（避免重复检查）
                    if t.fileitem:
                        logger.debug(
                            f"【整理接管】处理任务文件: {t.fileitem.path} (type: {t.fileitem.type})"
                        )
                        # 获取源文件的父目录作为检查键
                        parent_dir_item = (
                            t.fileitem
                            if t.fileitem.type == "dir"
                            else self.storage_chain.get_parent_item(t.fileitem)
                        )
                        if parent_dir_item and parent_dir_item.path:
                            parent_dir_path = parent_dir_item.path
                            logger.debug(f"【整理接管】检查父目录: {parent_dir_path}")
                            # 如果这个父目录已经检查过，直接使用之前的结果
                            if parent_dir_path in checked_parent_dirs:
                                cached_dirs = checked_parent_dirs[parent_dir_path]
                                logger.debug(
                                    f"【整理接管】使用缓存的目录列表: {len(cached_dirs)} 个目录"
                                )
                                all_dir_items_to_delete.extend(cached_dirs)
                            else:
                                # 首次检查，收集所有需要删除的目录
                                logger.debug(
                                    f"【整理接管】首次检查目录: {parent_dir_path}"
                                )
                                collected_dirs = self._collect_dirs_to_delete(
                                    t.fileitem
                                )
                                logger.debug(
                                    f"【整理接管】收集到 {len(collected_dirs)} 个需要删除的目录: {[d.path for d in collected_dirs]}"
                                )
                                # 缓存结果
                                checked_parent_dirs[parent_dir_path] = collected_dirs
                                all_dir_items_to_delete.extend(collected_dirs)
                        else:
                            logger.debug(
                                f"【整理接管】无法获取父目录 (fileitem: {t.fileitem.path})"
                            )
                    else:
                        logger.debug(f"【整理接管】任务没有 fileitem (task: {t})")

            # 去重（使用 fileid 作为唯一标识）
            unique_dir_items = {}
            for dir_item in all_dir_items_to_delete:
                if dir_item.fileid:
                    unique_dir_items[int(dir_item.fileid)] = dir_item

            logger.info(
                f"【整理接管】收集到 {len(unique_dir_items)} 个需要删除的空目录"
            )

            # 批量删除空目录
            if unique_dir_items:
                # 按路径深度排序（深度大的先删除）
                sorted_dir_items = sorted(
                    unique_dir_items.items(),
                    key=lambda x: len(Path(x[1].path).parts),
                    reverse=True,
                )
                dir_paths = [
                    unique_dir_items[dir_id].path for dir_id, _ in sorted_dir_items
                ]
                logger.info(
                    f"【整理接管】准备删除 {len(sorted_dir_items)} 个空目录（按深度排序）: {dir_paths[:5]}..."
                )

                # 按深度分批删除
                deleted_count = 0
                remaining_dirs = sorted_dir_items.copy()

                while remaining_dirs:
                    # 收集当前可以删除的目录
                    dirs_to_delete_this_round = []
                    dirs_to_skip = []

                    for dir_id, dir_item in remaining_dirs:
                        try:
                            # 获取当前目录状态
                            current_dir = self.storage_chain.get_file_item(
                                storage=self.storage_name,
                                path=Path(dir_item.path),
                            )
                            if not current_dir:
                                # 目录已被删除，跳过
                                dirs_to_skip.append((dir_id, dir_item))
                                continue

                            # 检查目录是否为空
                            dir_files = self.storage_chain.list_files(
                                current_dir, recursion=False
                            )
                            if not dir_files:
                                # 目录为空，可以删除
                                dirs_to_delete_this_round.append((dir_id, dir_item))
                            else:
                                pass
                        except Exception as e:
                            logger.debug(
                                f"【整理接管】检查目录状态失败 ({dir_item.path}): {e}"
                            )
                            dirs_to_delete_this_round.append((dir_id, dir_item))

                    if not dirs_to_delete_this_round:
                        break

                    # 批量删除当前轮次的目录
                    dir_ids_this_round = [
                        dir_id for dir_id, _ in dirs_to_delete_this_round
                    ]
                    try:
                        resp = self.client.fs_delete(dir_ids_this_round)
                        check_response(resp)
                        for dir_id in dir_ids_this_round:
                            self.cache_updater.remove_cache(dir_id)
                        deleted_count += len(dir_ids_this_round)
                        logger.info(
                            f"【整理接管】批量删除空目录成功: {len(dir_ids_this_round)} 个目录"
                        )
                    except Exception as batch_delete_error:
                        logger.warn(
                            f"【整理接管】批量删除失败，尝试逐个删除: {batch_delete_error}",
                            exc_info=True,
                        )
                        for dir_id, dir_item in dirs_to_delete_this_round:
                            try:
                                resp = self.client.fs_delete(dir_id)
                                check_response(resp)
                                self.cache_updater.remove_cache(dir_id)
                                deleted_count += 1
                                logger.debug(
                                    f"【整理接管】删除空目录成功 (path: {dir_item.path})"
                                )
                            except Exception as single_delete_error:
                                logger.warn(
                                    f"【整理接管】删除空目录失败 (path: {dir_item.path}): {single_delete_error}",
                                    exc_info=True,
                                )

                    deleted_ids = {dir_id for dir_id, _ in dirs_to_delete_this_round}
                    deleted_ids.update({dir_id for dir_id, _ in dirs_to_skip})
                    remaining_dirs = [
                        (dir_id, dir_item)
                        for dir_id, dir_item in remaining_dirs
                        if dir_id not in deleted_ids
                    ]

                logger.info(
                    f"【整理接管】删除空目录完成: {deleted_count}/{len(sorted_dir_items)} 个目录"
                )
            else:
                logger.debug("【整理接管】没有需要删除的空目录")

        except Exception as e:
            logger.debug(f"【整理接管】批量删除空目录和种子失败: {e}", exc_info=True)

    def _collect_dirs_to_delete(self, fileitem: FileItem) -> List[FileItem]:
        """
        收集需要删除的空目录

        :param fileitem: 文件项

        :return: 需要删除的目录列表
        """
        dirs_to_delete: List[FileItem] = []
        media_exts = settings.RMT_MEDIAEXT + settings.DOWNLOAD_TMPEXT
        fileitem_path = Path(fileitem.path) if fileitem.path else Path("")

        # 检查路径深度（不能删除根目录或一级目录）
        if len(fileitem_path.parts) <= 2:
            logger.debug(f"【整理接管】{fileitem.path} 根目录或一级目录不允许删除")
            return dirs_to_delete

        # 如果是目录类型且是蓝光原盘，需要特殊处理
        if fileitem.type == "dir":
            if hasattr(self.storage_chain, "is_bluray_folder"):
                if self.storage_chain.is_bluray_folder(fileitem):
                    # 蓝光原盘目录，直接返回（需要删除整个目录）
                    dirs_to_delete.append(fileitem)
                    return dirs_to_delete

        # 获取起始目录（文件则从父目录开始，目录则从自身开始）
        dir_item = (
            fileitem
            if fileitem.type == "dir"
            else self.storage_chain.get_parent_item(fileitem)
        )
        if not dir_item:
            logger.debug(f"【整理接管】{fileitem.path} 上级目录不存在")
            return dirs_to_delete

        # 查找操作文件项匹配的配置目录 (资源目录、媒体库目录)
        try:
            associated_dir = max(
                (
                    Path(p)
                    for d in DirectoryHelper().get_dirs()
                    for p in (d.download_path, d.library_path)
                    if p and fileitem_path.is_relative_to(Path(p))
                ),
                key=lambda path: len(path.parts),
                default=None,
            )
        except Exception as e:
            logger.debug(f"【整理接管】获取关联目录失败: {e}")
            associated_dir = None

        # 递归检查父目录
        while dir_item and len(Path(dir_item.path).parts) > 2:
            dir_path = Path(dir_item.path)

            if associated_dir and associated_dir.is_relative_to(dir_path):
                logger.debug(
                    f"【整理接管】{dir_item.path} 位于资源或媒体库目录结构中，不删除"
                )
                break

            if not associated_dir:
                try:
                    dir_files = self.storage_chain.list_files(dir_item, recursion=False)
                    if dir_files:
                        # 检查这些文件是否都是目录（子目录）
                        all_are_dirs = all(f.type == "dir" for f in dir_files)
                        if all_are_dirs:
                            logger.debug(
                                f"【整理接管】{dir_item.path} 只包含子目录（{len(dir_files)} 个），继续检查父目录"
                            )
                            dir_item = self.storage_chain.get_parent_item(dir_item)
                            continue
                        else:
                            logger.debug(
                                f"【整理接管】{dir_item.path} 不是空目录（有 {len(dir_files)} 个文件，包含非目录文件），不删除"
                            )
                            break
                except Exception as e:
                    logger.debug(
                        f"【整理接管】检查目录文件列表失败 ({dir_item.path}): {e}",
                        exc_info=True,
                    )
                    break

            # 检查：目录存在媒体文件，则不删除
            try:
                if hasattr(self.storage_chain, "any_files"):
                    has_media = self.storage_chain.any_files(
                        dir_item, extensions=media_exts
                    )
                    if has_media is not False:
                        logger.debug(
                            f"【整理接管】{dir_item.path} 存在媒体文件，不删除"
                        )
                        break
            except Exception as e:
                logger.debug(
                    f"【整理接管】检查媒体文件失败 ({dir_item.path}): {e}",
                    exc_info=True,
                )
                break

            # 所有检查通过，可以删除此目录
            logger.debug(f"【整理接管】收集到需要删除的空目录: {dir_item.path}")
            dirs_to_delete.append(dir_item)

            # 继续检查父目录
            dir_item = self.storage_chain.get_parent_item(dir_item)

        return dirs_to_delete

    def _record_fail(
        self, task: TransferTask, message: str, is_batch_failure: bool = False
    ) -> None:
        """
        记录失败历史并处理失败任务

        :param task: 任务
        :param message: 失败原因
        :param is_batch_failure: 是否为批量处理失败
        """
        try:
            # 构造 TransferInfo
            transferinfo = TransferInfo(
                success=False,
                fileitem=task.fileitem,
                transfer_type=task.transfer_type,
                message=message,
            )

            # 记录失败历史
            self.history_oper.add_fail(
                fileitem=task.fileitem,
                mode=task.transfer_type,
                meta=task.meta,
                mediainfo=task.mediainfo,
                transferinfo=transferinfo,
            )

            # 发送失败通知
            try:
                chain = TransferChain()
                if hasattr(chain, "post_message") and task.mediainfo:
                    chain.post_message(
                        Notification(
                            mtype=NotificationType.Manual,
                            title=f"{task.mediainfo.title_year} {task.meta.season_episode} 入库失败！",
                            text=f"原因：{message or '未知'}",
                            image=task.mediainfo.get_message_image()
                            if hasattr(task.mediainfo, "get_message_image")
                            else None,
                            username=task.username,
                            link=settings.MP_DOMAIN("#/history"),
                        )
                    )
            except Exception as e:
                logger.debug(f"【整理接管】发送失败通知失败: {e}", exc_info=True)

            # 标记任务失败
            try:
                chain = TransferChain()
                if hasattr(chain, "jobview") and hasattr(chain.jobview, "fail_task"):
                    chain.jobview.fail_task(task)
            except Exception as e:
                logger.debug(f"【整理接管】标记任务失败失败: {e}", exc_info=True)

            try:
                chain = TransferChain()
                if hasattr(chain, "jobview") and chain.jobview.is_finished(task):
                    with task_lock:
                        chain.jobview.remove_job(task)
            except Exception as e:
                logger.debug(f"【整理接管】执行完成逻辑失败: {e}", exc_info=True)

            logger.debug(f"【整理接管】记录失败历史: {task.fileitem.name} - {message}")
        except Exception as e:
            logger.error(f"【整理接管】记录失败历史异常: {e}", exc_info=True)
