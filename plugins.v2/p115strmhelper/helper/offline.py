from typing import List
from time import sleep
from pathlib import Path

from p115client import P115Client
from p115client.tool.offline import offline_iter
from p115client.tool.attr import get_attr

from app.log import logger

from ..core.config import configer
from ..core.cache import idpathcacher
from ..helper.life import MonitorLife


class OfflineDownloadHelper:
    """
    离线下载
    """

    def __init__(self, client: P115Client, monitorlife: MonitorLife):
        self.client = client
        self.monitorlife = monitorlife

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
        return offline_iter(self.client, cooldown=2)

    def get_tasks_status(self, info_hash: List):
        """
        获取一组任务的状态和信息
        """
        for item in self.get_tasks():
            item_hash = item.get("info_hash", None)
            if item_hash in info_hash:
                # 0 进行中、1 下载失败、2 下载成功、3 重试中
                if int(item.get("status", -1)) == 2:
                    yield [item_hash, {"status": True, "data": item}]
                elif int(item.get("status", -1)) == 1:
                    yield [item_hash, {"status": True, "data": ""}]
                else:
                    yield [item_hash, {"status": False, "data": item}]

    def add_urls_to_transfer(self, url_list: List):
        """
        添加一组任务并进行网盘整理
        """
        try:
            parent_path = configer.get_config("pan_transfer_paths").split("\n")[0]
            parent_id = idpathcacher.get_id_by_dir(directory=str(parent_path))
            if not parent_id:
                parent_id = self.client.fs_dir_getid(parent_path)["id"]
                logger.info(f"【离线下载】获取到转存目录 ID：{parent_id}")
                idpathcacher.add_cache(id=int(parent_id), directory=str(parent_path))

            resp = self.add_urls(url_list=url_list, cid=parent_id)
            if not resp.get("state", None):
                logger.error(f"【离线下载】下载任务添加失败: {url_list} {resp}")
                return False

            # 获取所有任务的 hash
            hash_list: List = []
            for item in resp.get("data", {}).get("result"):
                hash_list.append(str(item.get("info_hash")))

            sleep(10)

            # 等待下载完成并添加到网盘整理
            while hash_list:
                for item in self.get_tasks_status(hash_list):
                    if item[1].get("status"):
                        hash_list.remove(item[0])
                        if not item[1].get("data", None):
                            logger.error(f"【离线下载】{item[0]} 下载失败")
                        logger.info(f"【离线下载】{item[0]} 下载完成")
                        data = get_attr(
                            self.client, id=int(item[1].get("data").get("file_id"))
                        )
                        event = {
                            "file_id": int(data["id"]),
                            "file_category": 0 if data["is_dir"] else 1,
                            "parent_id": int(data["parent_id"]),
                            "file_size": int(data["size"]),
                            "pick_code": data["pickcode"],
                            "update_time": data["user_utime"],
                        }
                        file_path = Path(parent_path) / str(
                            item[1].get("data").get("name")
                        )
                        rmt_mediaext = [
                            f".{ext.strip()}"
                            for ext in configer.get_config("user_rmt_mediaext")
                            .replace("，", ",")
                            .split(",")
                        ]
                        # 直接传入网盘整理
                        self.monitorlife.media_transfer(
                            event=event,
                            file_path=file_path,
                            rmt_mediaext=rmt_mediaext,
                        )
                if hash_list:
                    logger.info(f"【离线下载】等待任务下载完成：{hash_list}")
                    sleep(60)
            return True
        except Exception as e:
            logger.error(f"【离线下载】未知错误：{e}")
            return False
