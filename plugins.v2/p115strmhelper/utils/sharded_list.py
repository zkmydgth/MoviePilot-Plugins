from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..core.config import configer


class ShardedPluginListStore:
    """
    分片列表：追加、按页读取、按 uid 删除、清空

    仅读取分页窗口涉及的分片，不一次性加载全部分片
    """

    def __init__(
        self,
        idx_key: str,
        shard_key_prefix: str,
        *,
        max_per_shard: int = 200,
        version: int = 1,
    ) -> None:
        self._idx_key = idx_key
        self._shard_prefix = shard_key_prefix
        self._max_per_shard = max(1, min(max_per_shard, 500))
        self._version = version

    def _load_idx(self) -> Optional[Dict[str, Any]]:
        raw = configer.get_plugin_data(self._idx_key)
        if not raw or not isinstance(raw, dict):
            return None
        return raw

    def _save_idx(self, idx: Dict[str, Any]) -> None:
        configer.save_plugin_data(self._idx_key, idx)

    def _new_shard_key(self, shard_index: int) -> str:
        return f"{self._shard_prefix}{shard_index}"

    def append(self, item: Dict[str, Any]) -> None:
        """
        追加一条记录（须含唯一 uid）
        """
        idx = self._load_idx()
        if idx is None:
            idx = {
                "v": self._version,
                "max_per_shard": self._max_per_shard,
                "shards": [],
                "total": 0,
            }
        shards_meta: List[Dict[str, Any]] = list(idx.get("shards") or [])
        max_ps = int(idx.get("max_per_shard") or self._max_per_shard)
        max_ps = max(1, min(max_ps, 500))

        if not shards_meta:
            k0 = self._new_shard_key(0)
            configer.save_plugin_data(k0, [item])
            shards_meta.append({"key": k0, "n": 1})
            idx["shards"] = shards_meta
            idx["total"] = 1
            self._save_idx(idx)
            return

        last = shards_meta[-1]
        last_key = str(last["key"])
        last_list = configer.get_plugin_data(last_key)
        if not isinstance(last_list, list):
            last_list = []
        if len(last_list) >= max_ps:
            new_i = len(shards_meta)
            nk = self._new_shard_key(new_i)
            configer.save_plugin_data(nk, [item])
            shards_meta.append({"key": nk, "n": 1})
        else:
            last_list = list(last_list)
            last_list.append(item)
            configer.save_plugin_data(last_key, last_list)
            last["n"] = len(last_list)
        idx["shards"] = shards_meta
        idx["total"] = int(idx.get("total") or 0) + 1
        self._save_idx(idx)

    def total(self) -> int:
        """
        返回总条数
        """
        idx = self._load_idx()
        if not idx:
            return 0
        return int(idx.get("total") or 0)

    def page(self, page: int, limit: int) -> Tuple[List[Dict[str, Any]], int]:
        """
        分页返回记录（page 从 1 开始）
        """
        idx = self._load_idx()
        if not idx:
            return [], 0
        total = int(idx.get("total") or 0)
        if total <= 0:
            return [], 0
        page = max(1, page)
        limit = min(max(1, limit), 500)
        offset = (page - 1) * limit
        if offset >= total:
            return [], total

        out: List[Dict[str, Any]] = []
        global_idx = 0
        for sm in idx.get("shards") or []:
            if not isinstance(sm, dict):
                continue
            key = str(sm.get("key") or "")
            if not key:
                continue
            lst = configer.get_plugin_data(key)
            if not isinstance(lst, list):
                continue
            for it in lst:
                if global_idx < offset:
                    global_idx += 1
                    continue
                if len(out) >= limit:
                    return out, total
                if isinstance(it, dict):
                    out.append(it)
                global_idx += 1
        return out, total

    def delete_by_uid(self, uid: str) -> bool:
        """
        按 uid 删除一条（线性扫描分片）
        """
        uid = (uid or "").strip()
        if not uid:
            return False
        idx = self._load_idx()
        if not idx:
            return False
        shards_meta: List[Dict[str, Any]] = list(idx.get("shards") or [])
        for si, sm in enumerate(shards_meta):
            key = str(sm["key"])
            lst = configer.get_plugin_data(key)
            if not isinstance(lst, list):
                continue
            for j, it in enumerate(lst):
                if isinstance(it, dict) and it.get("uid") == uid:
                    new_lst = list(lst)
                    new_lst.pop(j)
                    if new_lst:
                        configer.save_plugin_data(key, new_lst)
                        sm["n"] = len(new_lst)
                    else:
                        configer.del_plugin_data(key)
                        shards_meta.pop(si)
                    idx["shards"] = shards_meta
                    idx["total"] = max(0, int(idx.get("total") or 0) - 1)
                    self._save_idx(idx)
                    return True
        return False

    def clear_all(self) -> None:
        """
        删除索引与全部分片
        """
        idx = self._load_idx()
        if not idx:
            return
        for sm in idx.get("shards") or []:
            if isinstance(sm, dict) and sm.get("key"):
                configer.del_plugin_data(str(sm["key"]))
        configer.del_plugin_data(self._idx_key)
