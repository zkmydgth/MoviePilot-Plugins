# -*- coding: utf-8 -*-
"""
``app.core.module`` 替身：模块管理器。

插件有两种获取下载器的路径，替身都要覆盖：

1. ``ModuleManager().get_running_subtype_module(DownloaderType)`` → ``get_instances()``
   用于种子级候选收集（``_collect_seed_candidates``）。
2. ``ModuleManager().get_modules()`` 遍历全部模块，按「是否持有该 hash」定位下载器
   用于仅文件模式删种（``_get_downloader_for``）。

模块对象除 ``get_instances`` 外还需提供 ``name`` 与 ``remove_torrents``，
前者用于日志与「目标下载器范围」过滤，后者是真正下发删种动作的入口。
"""

from typing import Any, Dict, List


class _Module:
    """
    伪下载器模块。

    :param dl_type: 下载器类型（仅用于标识）
    :param mapping: {实例名: 伪下载器对象}
    """

    def __init__(self, dl_type: Any, mapping: Dict[str, Any]) -> None:
        self.type = dl_type
        self._mapping = dict(mapping)

    @property
    def name(self) -> str:
        """模块名：取首个实例名，与真实 MoviePilot 的「一名一模块」一致。"""
        return next(iter(self._mapping), "?")

    def get_instances(self) -> Dict[str, Any]:
        return dict(self._mapping)

    def list_torrents(self, hashs: Any = None, include_all_tags: bool = False,
                      **kwargs: Any) -> List[Any]:
        """
        按 hash 查询种子，供 ``_get_downloader_for`` 定位下载器。

        真实 MoviePilot 的下载器模块提供 ``list_torrents(hashs=...)``；替身转发给
        注入的伪下载器并**按 hashs 过滤**，未指定时返回全部，语义与真实实现一致。
        """
        wanted = None
        if hashs:
            wanted = {hashs} if isinstance(hashs, str) else set(hashs)
        results: List[Any] = []
        for server in self._mapping.values():
            for item in server.get_torrents() or []:
                hash_str = (
                    item.get("hash") if isinstance(item, dict)
                    else getattr(item, "hashString", None)
                )
                if wanted is None or hash_str in wanted:
                    results.append(item)
        return results

    def remove_torrents(self, hashs: Any = None, delete_file: bool = False,
                        downloader: Any = None, **kwargs: Any) -> bool:
        """
        把删种请求转发给注入的伪下载器实例。

        ``downloader`` 指定实例名时精确转发；未指定或名称不存在时转发给
        ``name`` 对应的实例，保证「按模块删种」的链路可被完整验证。
        """
        target = self._mapping.get(downloader) if downloader else None
        if target is None:
            target = self._mapping.get(self.name)
        if target is None:
            return False
        return bool(
            target.remove_torrents(
                hashs=hashs, delete_file=delete_file, downloader=downloader,
            )
        )


class ModuleManager:
    """
    模块管理器替身。

    测试通过 :meth:`register_downloader` 注入伪造下载器；未注入时返回空列表，
    使「无下载器」场景可被覆盖。
    """

    #: {DownloaderType: {实例名: 伪下载器对象}}，类级共享便于测试注入
    _downloaders: Dict[Any, Dict[str, Any]] = {}
    #: 记录 get_running_subtype_module 的调用参数
    calls: List[Any] = []

    def get_running_subtype_module(self, dl_type: Any) -> List[Any]:
        """
        获取指定类型的运行中模块。

        :param dl_type: 下载器类型
        :return: 模块列表（每项提供 get_instances）
        """
        ModuleManager.calls.append(dl_type)
        instances = ModuleManager._downloaders.get(dl_type) or {}
        if not instances:
            return []
        return [_Module(dl_type, instances)]

    def get_modules(self) -> List[Any]:
        """
        返回全部已注入的模块，供 ``_get_downloader_for`` 遍历定位。

        真实 MoviePilot 返回所有已加载模块（含站点、媒体库等），下载器模块在
        ``_query_torrents_by_hash`` 中通过「是否具备查询方法」被筛出；替身对每个
        已注册的下载器类型各构造一个模块对象。
        """
        modules: List[Any] = []
        for dl_type, instances in ModuleManager._downloaders.items():
            if instances:
                modules.append(_Module(dl_type, instances))
        return modules

    # ------------------------------------------------------------------
    # 测试辅助
    # ------------------------------------------------------------------
    @classmethod
    def register_downloader(cls, dl_type: Any, name: str, server: Any) -> None:
        """
        注入一个伪下载器实例。

        :param dl_type: 下载器类型
        :param name: 实例名
        :param server: 伪下载器对象（需提供 get_torrents / remove_torrents）
        """
        cls._downloaders.setdefault(dl_type, {})[name] = server

    @classmethod
    def reset(cls) -> None:
        """清空注入状态（测试隔离用）。"""
        cls._downloaders.clear()
        cls.calls.clear()


__all__ = ["ModuleManager"]
