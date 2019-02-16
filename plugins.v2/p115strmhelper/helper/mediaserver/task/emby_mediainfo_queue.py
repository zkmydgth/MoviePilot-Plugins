from pathlib import Path
from queue import Queue
from threading import Thread
from typing import List, Optional, Union

from app.log import logger

from ....schemas.emby_mediainfo import EmbyMediainfoTask
from ..emby import EmbyMediaInfoOperate


class EmbyMediainfoQueue:
    """
    Emby 媒体信息提取全局队列
    """

    _SENTINEL = object()

    def __init__(self) -> None:
        self._queue: Optional[Queue] = None
        self._worker_thread: Optional[Thread] = None

    def _worker(self) -> None:
        """
        队列 worker
        """
        if self._queue is None:
            return
        while True:
            try:
                task = self._queue.get()
            except Exception as e:
                logger.error(
                    f"【Emby 媒体信息队列】worker 取任务异常: {e}", exc_info=True
                )
                continue
            if task is self._SENTINEL:
                if self._queue is not None:
                    self._queue.task_done()
                break
            try:
                path = task.path if isinstance(task.path, Path) else Path(task.path)
                helper = EmbyMediaInfoOperate(
                    func_name=task.func_name,
                    mp_mediaserver=task.mp_mediaserver,
                    mediaservers=task.mediaservers,
                )
                helper.get_mediainfo(task.sha1, path, size=task.size)
            except Exception as e:
                logger.error(
                    f"{task.func_name} Emby 媒体信息提取失败: {e}",
                    exc_info=True,
                )
            finally:
                if self._queue is not None:
                    self._queue.task_done()

    def start(self) -> None:
        """
        启动 Emby 媒体信息提取队列 worker 线程
        """
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return
        self._queue = Queue(maxsize=4096)
        self._worker_thread = Thread(
            target=self._worker,
            name="P115StrmHelper-EmbyMediainfoQueue",
            daemon=False,
        )
        self._worker_thread.start()
        logger.debug("【Emby 媒体信息队列】worker 已启动")

    def stop(self) -> None:
        """
        停止 Emby 媒体信息提取队列 worker 线程
        """
        if (
            self._queue is None
            or self._worker_thread is None
            or not self._worker_thread.is_alive()
        ):
            return
        try:
            self._queue.put(self._SENTINEL)
            self._worker_thread.join(timeout=30)
            if self._worker_thread.is_alive():
                logger.warning("【Emby 媒体信息队列】worker 未在 30 秒内退出")
        except Exception as e:
            logger.error(f"【Emby 媒体信息队列】停止 worker 异常: {e}", exc_info=True)
        finally:
            self._worker_thread = None
            self._queue = None

    def enqueue(
        self,
        func_name: str,
        sha1: str,
        path: Union[str, Path],
        mp_mediaserver: Optional[str] = None,
        mediaservers: Optional[List[str]] = None,
        size: Optional[int] = None,
    ) -> None:
        """
        将一条 Emby 媒体信息提取任务加入全局队列

        :param func_name: 调用方标识，用于日志
        :param sha1: 媒体文件 sha1
        :param path: 媒体路径
        :param mp_mediaserver: MoviePilot 媒体服务器路径配置，可选
        :param mediaservers: 媒体服务器名称列表，可选
        :param size: 文件大小（字节），可选
        """
        if self._queue is None:
            logger.warning(
                "【Emby 媒体信息队列】队列未初始化，请先启动 worker，跳过入队"
            )
            return
        try:
            self._queue.put(
                EmbyMediainfoTask(
                    func_name=func_name,
                    mp_mediaserver=mp_mediaserver,
                    mediaservers=mediaservers,
                    sha1=sha1,
                    path=path,
                    size=size,
                )
            )
        except Exception as e:
            logger.error(f"【Emby 媒体信息队列】入队失败: {e}", exc_info=True)


emby_mediainfo_queue = EmbyMediainfoQueue()
