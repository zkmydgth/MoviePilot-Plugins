# -*- coding: utf-8 -*-
"""
``app.schemas.system`` 替身：下载器配置与服务信息数据模型。

对齐 MoviePilot V3 真实定义：

- ``DownloaderConf``：name / type / default / config / enabled / path_mapping，
  其中 ``type`` 是**普通字符串**（如 ``"qbittorrent"``），不是枚举。
- ``ServiceInfo``：name / instance / module / type / config，
  ``instance`` 为具体客户端实例，``module`` 为下载器模块对象。
"""

from typing import Any, Optional


class DownloaderConf:
    """下载器配置替身（对齐 V3 ``DownloaderConf``）。"""

    def __init__(
        self,
        name: Optional[str] = None,
        type: Optional[str] = None,
        default: bool = False,
        config: Optional[dict] = None,
        enabled: bool = False,
        path_mapping: Optional[list] = None,
    ) -> None:
        self.name = name
        self.type = type
        self.default = default
        self.config = config if config is not None else {}
        self.enabled = enabled
        self.path_mapping = path_mapping if path_mapping is not None else []

    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        return f"<DownloaderConf {self.name!r} type={self.type!r} enabled={self.enabled}>"


class ServiceInfo:
    """服务信息替身（对齐 V3 ``ServiceInfo``）。"""

    def __init__(
        self,
        name: Optional[str] = None,
        instance: Any = None,
        module: Any = None,
        type: Optional[str] = None,
        config: Any = None,
    ) -> None:
        self.name = name
        self.instance = instance
        self.module = module
        self.type = type
        self.config = config

    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        return f"<ServiceInfo {self.name!r} type={self.type!r}>"


__all__ = ["DownloaderConf", "ServiceInfo"]
