import time
from typing import List
from datetime import datetime, timezone

from p115client import P115Client
from p115client.tool.offline import offline_iter

from app.log import logger

from ..core.config import configer
from ..core.cache import idpathcacher
from ..utils.string import StringUtils
from ..utils.sentry import sentry_manager
from ..utils.oopserver import OOPServerRequest


@sentry_manager.capture_all_class_exceptions
class OfflineDownloadHelper:
    """
    离线下载
    """

    def __init__(self, client: P115Client):
        self.client = client

        self.offline_list_cache = {"data": None, "timestamp": 0}

    @staticmethod
    def build_offline_urls_payload(urls, savepath=None, wp_path_id=None):
        """
        构建添加下载列表参数
        """
        payload = {}
        for i, url in enumerate(urls):
            payload[f"url[{i}]"] = url.strip()

        if savepath:
            payload["savepath"] = savepath
        if wp_path_id:
            payload["wp_path_id"] = wp_path_id

        return payload

    def add_urls(self, url_list: List, cid: int):
        """
        添加一组任务
        """
        payload = self.build_offline_urls_payload(urls=url_list, wp_path_id=cid)
        return self.client.offline_add_urls(payload)

    def get_tasks(self):
        """
        获取当前所有任务
        """
        return offline_iter(self.client, cooldown=2, type="web")

    def add_urls_to_transfer(self, url_list: List) -> bool:
        """
        添加一组任务并进行网盘整理
        """
        try:
            parent_path = configer.get_config("pan_transfer_paths").split("\n")[0]
            parent_id = idpathcacher.get_id_by_dir(directory=str(parent_path))
            if not parent_id:
                parent_id = self.client.fs_dir_getid(parent_path)["id"]
                logger.debug(f"【离线下载】获取到转存目录 ID：{parent_id}")
                idpathcacher.add_cache(id=int(parent_id), directory=str(parent_path))

            resp = self.add_urls(url_list=url_list, cid=parent_id)
            if not resp.get("state", None):
                logger.error(f"【离线下载】下载任务添加失败: {url_list} {resp}")
                return False

            for url in url_list:
                self.post_offline_info(url)
            logger.debug(f"【离线下载】下载任务添加完成: {url_list}")
            return True
        except Exception as e:
            logger.error(f"【离线下载】未知错误：{e}")
            return False

    def add_urls_to_path(self, url_list: List, path: str) -> bool:
        """
        添加一组任务下载到指定路径
        """
        try:
            parent_id = idpathcacher.get_id_by_dir(directory=path)
            if not parent_id:
                parent_id = self.client.fs_dir_getid(path)["id"]
                logger.debug(f"【离线下载】获取到下载目录 {path} ID：{parent_id}")
                idpathcacher.add_cache(id=int(parent_id), directory=path)

            resp = self.add_urls(url_list=url_list, cid=parent_id)
            if not resp.get("state", None):
                logger.error(f"【离线下载】下载任务添加失败: {url_list} {resp}")
                return False

            for url in url_list:
                self.post_offline_info(url)
            logger.debug(f"【离线下载】下载任务添加完成: {url_list}")
            return True
        except Exception as e:
            logger.error(f"【离线下载】未知错误：{e}")
            return False

    def get_cached_data(self):
        """
        获取缓存离线下载列表
        """
        status_mapping = {0: "下载中", 1: "下载失败", 2: "已完成", 3: "重试中"}

        now = time.time()
        if (
            self.offline_list_cache["data"] is None
            or (now - self.offline_list_cache["timestamp"]) > 120
        ):
            raw_tasks = self.get_tasks()
            formatted_tasks = []

            for task in raw_tasks:
                formatted_task = {
                    "info_hash": task.get("info_hash", ""),
                    "name": task.get("name", ""),
                    "size": task.get("size", 0),
                    "size_text": StringUtils.format_size(task.get("size", 0)),
                    "status": task.get("status", 0),
                    "status_text": status_mapping.get(
                        task.get("status", 4), "未知状态"
                    ),
                    "percent": task.get("percentDone", 0),
                    "add_time": task.get("add_time", 0),
                }
                formatted_tasks.append(formatted_task)

            self.offline_list_cache = {"data": formatted_tasks, "timestamp": now}

        return self.offline_list_cache["data"]

    @staticmethod
    def post_offline_info(url: str):
        """
        上传离线下载信息
        """
        if not configer.get_config("upload_offline_info"):
            return

        oopserver_request = OOPServerRequest(max_retries=3, backoff_factor=1.0)
        json_data = {
            "url": url,
            "postime": datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
        }

        try:
            response = oopserver_request.make_request(
                path="/offline/info",
                method="POST",
                headers={"x-machine-id": configer.get_config("MACHINE_ID")},
                json_data=json_data,
                timeout=10.0,
            )

            if response is not None and response.status_code == 201:
                logger.info(
                    f"【离线下载】离线下载信息报告服务器成功: {response.json()}"
                )
            else:
                logger.debug("【离线下载】离线下载报告服务器失败，网络问题")
        except Exception as e:
            logger.debug(f"【离线下载】离线下载报告服务器失败: {e}")
