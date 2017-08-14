# -*- coding: utf-8 -*-
"""
``app.core.module`` 替身：模块管理器。

插件通过 ``ModuleManager().get_running_subtype_module(DownloaderType)`` 获取
运行中的下载器模块，再经 ``get_instances()`` 拿到具体实例。替身支持测试注入
伪造的下载器，从而覆盖种子级与候选筛选逻辑。
"""

from typing import Any, Dict, List


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

        class _Module:
            """伪模块：仅提供插件用到的 get_instances。"""

            def __init__(self, mapping: Dict[str, Any]) -> None:
                self._mapping = mapping

            def get_instances(self) -> Dict[str, Any]:
                return dict(self._mapping)

        return [_Module(instances)]

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
