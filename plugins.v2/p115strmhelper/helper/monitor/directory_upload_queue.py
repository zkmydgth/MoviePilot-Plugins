from dataclasses import dataclass
from queue import Queue
from threading import Lock, Thread
from typing import Optional

from p115client import P115Client

from app.log import logger


@dataclass
class DirectoryUploadTask:
    """
    单条目录上传处理任务

    :param client: 115 客户端
    :param file_path: 事件文件路径
    :param mon_path: 监控根目录
    """

    client: P115Client
    file_path: str
    mon_path: str


class DirectoryUploadQueue:
    """
    目录上传全局处理队列
    """

    _SENTINEL = object()

    def __init__(self) -> None:
        self._queue: Optional[Queue] = None
        self._worker_thread: Optional[Thread] = None
        self._lock = Lock()

    def _worker(self) -> None:
        """
        从队列取任务并调用 process_file_change
        """
        if self._queue is None:
            return
        while True:
            try:
                task = self._queue.get()
            except Exception as e:
                logger.error(
                    f"【目录上传】worker 取任务异常: {e}",
                    exc_info=True,
                )
                continue
            if task is self._SENTINEL:
                if self._queue is not None:
                    self._queue.task_done()
                break
            try:
                # 延迟导入，避免与 monitor 包初始化循环依赖
                from . import process_file_change

                process_file_change(
                    task.client,
                    task.file_path,
                    task.mon_path,
                )
            except Exception as e:
                logger.error(
                    f"【目录上传】处理任务失败: {e}",
                    exc_info=True,
                )
            finally:
                if self._queue is not None:
                    self._queue.task_done()

    def start(self) -> None:
        """
        启动 worker 线程（幂等）
        """
        with self._lock:
            if self._worker_thread is not None and self._worker_thread.is_alive():
                return
            self._queue = Queue()
            self._worker_thread = Thread(
                target=self._worker,
                name="P115StrmHelper-DirectoryUploadQueue",
                daemon=False,
            )
            self._worker_thread.start()
            logger.debug("【目录上传】worker 已启动")

    def stop(self) -> None:
        """
        发送哨兵并 join worker
        """
        with self._lock:
            q = self._queue
            th = self._worker_thread
            if q is None or th is None:
                return
            if not th.is_alive():
                self._queue = None
                self._worker_thread = None
                return
        try:
            q.put(self._SENTINEL)
            th.join(timeout=30)
            if th.is_alive():
                logger.warning("【目录上传】worker 未在 30 秒内退出")
        except Exception as e:
            logger.error(
                f"【目录上传】停止 worker 异常: {e}",
                exc_info=True,
            )
        finally:
            with self._lock:
                if self._worker_thread is th and self._queue is q:
                    self._worker_thread = None
                    self._queue = None

    def enqueue(self, task: DirectoryUploadTask) -> None:
        """
        将一条处理任务加入队列

        :param task: 任务参数
        """
        with self._lock:
            q = self._queue
        if q is None:
            logger.warning("【目录上传】队列未就绪，跳过入队")
            return
        try:
            q.put_nowait(task)
        except Exception as e:
            logger.error(f"【目录上传】入队失败: {e}", exc_info=True)


directory_upload_queue = DirectoryUploadQueue()
