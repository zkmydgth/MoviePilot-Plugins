import time
from typing import List

from p115client import P115Client
from p115client.tool.history import iter_history_once

from app.log import logger
from app.chain.transfer import TransferChain


class MonitorHistory:
    """
    监控历史事件

    {
        2: "offline_download",  离线下载
        3: "browse_video",      浏览视频 无操作
        4: "upload",            上传文件 无操作
        7: "receive",           接收文件 无操作
        8: "move",              移动文件 无操作
    }
    """

    def __init__(self, client: P115Client):
        self._client = client

    def once_pull(self, from_time, from_id):
        """
        单次拉取
        """
        while True:
            if not TransferChain().get_queue_tasks():
                break
            logger.debug(
                "【监控历史事件】MoviePilot 整理运行中，等待整理完成后继续监控历史事件..."
            )
            time.sleep(20)

        events_batch: List = []
        first_loop: bool = True
        for event in iter_history_once(
            self._client,
            from_time,
            from_id,
            app="android",
            cooldown=2,
        ):
            if first_loop:
                if "update_time" in event and "id" in event:
                    from_id = int(event["id"])
                    from_time = int(event["update_time"])
                else:
                    break
                first_loop = False
            events_batch.append(event)
        if not events_batch:
            time.sleep(20)
            return from_time, from_id, []
        return from_time, from_id, events_batch
