from pathlib import Path
from time import localtime, time
from multiprocessing import Queue as ProcessQueue
from typing import Dict, Any, Optional

from app.chain.storage import StorageChain
from app.chain.transfer import TransferChain
from app.log import logger
from app.schemas import NotificationType, FileItem

from ...core.config import configer
from ...core.i18n import i18n
from ...core.message import post_message
from ...core.scrape import media_scrape_metadata
from ...helper.life.client import MonitorLife
from ...helper.mediaserver import MediaServerRefresh
from ...helper.mediasyncdel import MediaSyncDelHelper


class MonitorLifeTool:
    """
    生活事件工具类
    """

    @staticmethod
    def process_life_queue(
        task_data: Any, life_response_queue: ProcessQueue, monitor_life: MonitorLife
    ):
        """
        处理生活事件队列

        :param task_data: 任务数据
        :param life_response_queue: 生活事件响应队列
        :param monitor_life: 监控生活事件实例

        :return: None
        """
        if isinstance(task_data, dict):
            request_type = task_data.get("type")

            if request_type == "query_queue_status":
                try:
                    MonitorLifeTool.process_queue_status_query(life_response_queue)
                except Exception as e:
                    logger.error(f"【监控生活事件】处理队列状态查询失败: {e}")
                    try:
                        life_response_queue.put({"has_tasks": False})
                    except Exception:
                        pass
                return

            elif request_type == "check_file_exists":
                try:
                    MonitorLifeTool.process_check_file_exists(
                        task_data, life_response_queue
                    )
                except Exception as e:
                    logger.error(f"【监控生活事件】处理文件存在性查询失败: {e}")
                    try:
                        life_response_queue.put({"exists": False})
                    except Exception:
                        pass

            elif request_type == "remove_mp_history":
                try:
                    MonitorLifeTool.process_remove_mp_history(task_data)
                except Exception as e:
                    logger.error(f"【监控生活事件】处理删除历史记录失败: {e}")
                return

            elif request_type == "scrape_metadata":
                try:
                    MonitorLifeTool.process_scrape_metadata(task_data)
                except Exception as e:
                    logger.error(f"【监控生活事件】处理媒体刮削失败: {e}")
                return

            elif request_type == "refresh_mediaserver":
                try:
                    mediaserver_helper = getattr(
                        monitor_life, "mediaserver_helper", None
                    )
                    MonitorLifeTool.process_refresh_mediaserver(
                        task_data, mediaserver_helper
                    )
                except Exception as e:
                    logger.error(f"【监控生活事件】处理媒体服务器刷新失败: {e}")
                return

            elif request_type == "post_message":
                try:
                    MonitorLifeTool.process_post_message(task_data)
                except Exception as e:
                    logger.error(f"【监控生活事件】处理发送通知失败: {e}")
                return

            try:
                MonitorLifeTool.process_transfer_task(task_data)
            except Exception as e:
                logger.error(f"【监控生活事件】处理整理队列任务失败: {e}")
            return
        return

    @staticmethod
    def process_transfer_task(task_data: Dict[str, Any]):
        """
        处理从队列接收的 do_transfer 任务（在主进程中调用）

        :param task_data: FileItem 的字典数据

        :return: None
        """
        try:
            fileitem = FileItem(**task_data)
            transferchain = TransferChain()
            transferchain.do_transfer(fileitem=fileitem)
            logger.debug(f"【网盘整理】任务已添加到整理队列: {fileitem.path}")
        except Exception as e:
            logger.error(f"【网盘整理】处理队列任务失败: {e}")
            raise

    @staticmethod
    def process_queue_status_query(response_queue: ProcessQueue):
        """
        处理队列状态查询请求（在主进程中调用）

        :param response_queue: 响应队列，用于返回查询结果

        :return: None
        """
        try:
            queue_tasks = TransferChain().get_queue_tasks()
            has_tasks = len(queue_tasks) > 0 if queue_tasks else False
            response_queue.put({"has_tasks": has_tasks})
        except Exception as e:
            logger.error(f"【监控生活事件】查询队列状态失败: {e}")
            response_queue.put({"has_tasks": False})

    @staticmethod
    def process_check_file_exists(
        task_data: Dict[str, Any], response_queue: ProcessQueue
    ):
        """
        处理文件存在性查询请求（在主进程中调用）

        :param task_data: 查询请求数据，包含 storage 和 path
        :param response_queue: 响应队列，用于返回查询结果

        :return: None
        """
        try:
            storage = task_data.get("storage")
            path = task_data.get("path")
            storagechain = StorageChain()
            fileitem = storagechain.get_file_item(storage=storage, path=Path(path))
            if fileitem:
                response_queue.put({"exists": True, "fileitem": fileitem.model_dump()})
            else:
                response_queue.put({"exists": False})
        except Exception as e:
            logger.error(f"【监控生活事件】查询文件是否存在失败: {e}")
            response_queue.put({"exists": False})

    @staticmethod
    def process_remove_mp_history(task_data: Dict[str, Any]):
        """
        处理删除MoviePilot历史记录请求（在主进程中调用）

        :param task_data: 请求数据，包含 path 和 del_source

        :return: None
        """
        try:
            path = task_data.get("path")
            del_source = task_data.get("del_source", False)

            mediasyncdel = MediaSyncDelHelper()
            del_torrent_hashs, stop_torrent_hashs, error_cnt, transfer_history = (
                mediasyncdel.remove_by_path(
                    path=path,
                    del_source=del_source,
                )
            )
            if configer.get_config("notify") and transfer_history:
                torrent_cnt_msg = ""
                if del_torrent_hashs:
                    torrent_cnt_msg += f"删除种子 {len(set(del_torrent_hashs))} 个\n"
                if stop_torrent_hashs:
                    stop_cnt = 0
                    for stop_hash in set(stop_torrent_hashs):
                        if stop_hash not in set(del_torrent_hashs):
                            stop_cnt += 1
                    if stop_cnt > 0:
                        torrent_cnt_msg += f"暂停种子 {stop_cnt} 个\n"
                if error_cnt:
                    torrent_cnt_msg += f"删种失败 {error_cnt} 个\n"
                from time import strftime

                post_message(
                    mtype=NotificationType.Plugin,
                    title=i18n.translate("life_sync_media_del_title"),
                    text=f"\n删除 {path}\n"
                    f"删除记录{len(transfer_history) if transfer_history else '0'}个\n"
                    f"{torrent_cnt_msg}"
                    f"时间 {strftime('%Y-%m-%d %H:%M:%S', localtime(time()))}\n",
                )
        except Exception as e:
            logger.error(f"【监控生活事件】删除历史记录失败: {e}")

    @staticmethod
    def process_scrape_metadata(task_data: Dict[str, Any]):
        """
        处理媒体刮削请求（在主进程中调用）

        :param task_data: 请求数据，包含 path

        :return: None
        """
        try:
            path = task_data.get("path")
            if path:
                media_scrape_metadata(path=path)
        except Exception as e:
            logger.error(f"【监控生活事件】媒体刮削失败: {e}")

    @staticmethod
    def process_refresh_mediaserver(
        task_data: Dict[str, Any],
        mediaserver_helper: Optional[MediaServerRefresh] = None,
    ):
        """
        处理媒体服务器刷新请求（在主进程中调用）

        :param task_data: 请求数据，包含 file_path 和 file_name
        :param mediaserver_helper: MediaServerRefresh 实例，如果为None则创建新实例

        :return: None
        """
        try:
            file_path = task_data.get("file_path")
            file_name = task_data.get("file_name")
            if file_path and file_name:
                if mediaserver_helper is None:
                    mediaserver_helper = MediaServerRefresh(
                        func_name="【监控生活事件】",
                        enabled=configer.monitor_life_media_server_refresh_enabled,
                        mp_mediaserver=configer.monitor_life_mp_mediaserver_paths,
                        mediaservers=configer.monitor_life_mediaservers,
                    )
                mediaserver_helper.refresh_mediaserver(
                    file_path=file_path,
                    file_name=file_name,
                )
        except Exception as e:
            logger.error(f"【监控生活事件】媒体服务器刷新失败: {e}")

    @staticmethod
    def process_post_message(task_data: Dict[str, Any]):
        """
        处理发送通知请求（在主进程中调用）

        :param task_data: 请求数据，包含 mtype, title, text 等

        :return: None
        """
        try:
            mtype = task_data.get("mtype")
            title = task_data.get("title")
            text = task_data.get("text")
            channel = task_data.get("channel")
            image = task_data.get("image")
            link = task_data.get("link")
            userid = task_data.get("userid")
            username = task_data.get("username")

            # 如果mtype是字符串，需要转换为NotificationType枚举
            if isinstance(mtype, str):
                # 尝试从NotificationType中找到对应的值
                for nt in NotificationType:
                    if nt.value == mtype:
                        mtype = nt
                        break
                # 如果没找到，使用默认值
                if isinstance(mtype, str):
                    mtype = NotificationType.Plugin

            post_message(
                channel=channel,
                mtype=mtype
                if isinstance(mtype, NotificationType)
                else NotificationType.Plugin,
                title=title,
                text=text,
                image=image,
                link=link,
                userid=userid,
                username=username,
            )
        except Exception as e:
            logger.error(f"【监控生活事件】发送通知失败: {e}")
