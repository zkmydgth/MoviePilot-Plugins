# -*- coding: utf-8 -*-
"""
``app.helper.service`` 替身：服务配置助手。

插件通过 ``ServiceConfigHelper.get_downloader_configs()`` 生成「目标下载器」
下拉选项。替身支持测试注入下载器配置，覆盖表单选项生成逻辑。
"""

from typing import Any, List, Optional


class _DownloaderConfig:
    """下载器配置项。"""

    def __init__(self, name: str = "", type: str = "", enabled: bool = True) -> None:
        self.name = name
        self.type = type
        self.enabled = enabled


class ServiceConfigHelper:
    """服务配置助手替身。"""

    #: 测试注入的下载器配置
    _downloader_configs: List[_DownloaderConfig] = []

    @classmethod
    def get_downloader_configs(cls) -> List[_DownloaderConfig]:
        """
        获取下载器配置列表。

        :return: 下载器配置列表
        """
        return list(cls._downloader_configs)

    # ------------------------------------------------------------------
    # 测试辅助
    # ------------------------------------------------------------------
    @classmethod
    def set_downloaders(cls, configs: Optional[List[dict]] = None) -> None:
        """
        注入下载器配置。

        :param configs: 形如 [{"name": "qb", "type": "qbittorrent", "enabled": True}]
        """
        cls._downloader_configs = [
            _DownloaderConfig(
                name=str(item.get("name") or ""),
                type=str(item.get("type") or ""),
                enabled=bool(item.get("enabled", True)),
            )
            for item in (configs or [])
        ]

    @classmethod
    def reset(cls) -> None:
        """清空注入状态（测试隔离用）。"""
        cls._downloader_configs = []


__all__ = ["ServiceConfigHelper", "_DownloaderConfig"]

_ = Any
