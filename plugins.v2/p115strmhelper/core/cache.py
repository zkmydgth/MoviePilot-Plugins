from typing import List, Dict, MutableMapping
from time import time

from cachetools import LRUCache, TTLCache


class IdPathCache:
    """
    文件路径ID缓存
    """

    def __init__(self, maxsize=128):
        self.id_to_dir = LRUCache(maxsize=maxsize)
        self.dir_to_id = LRUCache(maxsize=maxsize)

    def add_cache(self, id: int, directory: str):
        """
        添加缓存
        """
        self.id_to_dir[id] = directory
        self.dir_to_id[directory] = id

    def get_dir_by_id(self, id: int):
        """
        通过 ID 获取路径
        """
        return self.id_to_dir.get(id)

    def get_id_by_dir(self, directory: str):
        """
        通过路径获取 ID
        """
        return self.dir_to_id.get(directory)

    def clear(self):
        """
        清空所有缓存
        """
        self.id_to_dir.clear()
        self.dir_to_id.clear()


class PanTransferCache:
    """
    网盘整理缓存
    """

    def __init__(self):
        self.delete_pan_transfer_list = []
        self.creata_pan_transfer_list = []
        self.top_delete_pan_transfer_list: Dict[str, List] = {}


class LifeEventCache:
    """
    生活事件监控缓存
    """

    def __init__(self):
        self.create_strm_file_dict: MutableMapping[str, List] = TTLCache(
            maxsize=1_000_000, ttl=600
        )


class R302Cache:
    """
    302 跳转缓存
    """

    def __init__(self, maxsize=8096):
        """
        初始化缓存

        参数:
        maxsize (int): 缓存可以容纳的最大条目数
        """
        self._cache = LRUCache(maxsize=maxsize)

    def set(self, pick_code, ua_code, url, expires_time):
        """
        向缓存中添加一个URL，并为其设置独立的过期时间。

        参数:
        pick_code (str): 第一层键
        ua_code (str): 第二层键
        url (str): 需要缓存的URL
        expires_time (int): 过期时间
        """
        key = (pick_code, ua_code)

        self._cache[key] = {"url": url, "expires_at": expires_time}

    def get(self, pick_code, ua_code):
        """
        从缓存中获取一个URL，如果它存在且未过期

        参数:
        pick_code (str): 第一层键
        ua_code (str): 第二层键

        返回:
        str: 如果URL存在且未过期，则返回该URL
        None: 如果URL不存在或已过期
        """
        key = (pick_code, ua_code)

        item = self._cache.get(key)

        if item is None:
            return None

        if time() > item["expires_at"]:
            del self._cache[key]
            return None

        return item["url"]

    def count_by_pick_code(self, pick_code):
        """
        计算与指定 pick_code 匹配的缓存条目数量。

        参数:
        pick_code (str): 要匹配的第一层键

        返回:
        int: 匹配的缓存条目数量
        """
        count = 0
        for key in self._cache.keys():
            if key[0] == pick_code:
                count += 1
        return count

    def __str__(self):
        """
        返回底层缓存当前状态的字符串表示
        """
        return str(self._cache)


idpathcacher = IdPathCache()
pantransfercacher = PanTransferCache()
lifeeventcacher = LifeEventCache()
r302cacher = R302Cache(maxsize=8096)
