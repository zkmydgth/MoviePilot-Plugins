# -*- coding: utf-8 -*-
"""
``app.application.downloader`` 替身：V3 的下载器目录端口。

MoviePilot V3 彻底移除了 ``app.core.module.ModuleManager`` 与
``app.helper.service.ServiceConfigHelper``，改由本模块的 ``DownloaderHelper``
提供能力（真实实现为 ``ServiceBaseHelper[DownloaderConf]`` 子类）：

- ``get_configs()``      → Dict[str, DownloaderConf]（替代 get_downloader_configs）
- ``get_services()``     → Dict[str, ServiceInfo]（替代 ModuleManager 取模块/实例）

**ServiceInfo 的字段分工**（改写插件的关键）：

- ``instance``：具体客户端实例，负责枚举种子（``get_torrents``）
- ``module``  ：下载器模块对象，负责删种（``remove_torrents``）与种子查询

为复用既有 9 个测试文件，替身沿用原来的注入方式：
``ModuleManager.register_downloader(dl_type, name, server)`` 与
``ServiceConfigHelper.set_downloaders([...])``，在此合成为 V3 语义。
"""

from typing import Any, Dict, List, Optional

from app.core.module import ModuleManager, _Module
from app.helper.service import ServiceConfigHelper
from app.schemas.system import DownloaderConf, ServiceInfo


def _type_to_str(dl_type: Any) -> str:
    """把 DownloaderType 枚举或字符串统一转成配置用的类型字符串。"""
    if isinstance(dl_type, str):
        return dl_type
    value = getattr(dl_type, "value", None)
    return str(value if value is not None else dl_type)


class DownloaderHelper:
    """下载器目录替身（对齐 V3 ``DownloaderHelper`` 的公开方法）。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    # ------------------------------------------------------------------
    # 配置
    # ------------------------------------------------------------------
    def get_configs(self, include_disabled: bool = False) -> Dict[str, DownloaderConf]:
        """
        返回按名称索引的下载器配置（默认只含启用项）。
        """
        out: Dict[str, DownloaderConf] = {}
        injected = ServiceConfigHelper.get_downloader_configs()
        if injected:
            for conf in injected:
                if not conf.name or not conf.type:
                    continue
                if not (conf.enabled or include_disabled):
                    continue
                out[conf.name] = DownloaderConf(
                    name=conf.name,
                    type=_type_to_str(conf.type),
                    enabled=bool(conf.enabled),
                    default=bool(getattr(conf, "default", False)),
                )
            return out
        # 未显式注入配置时，由已注册的下载器实例合成（一律视为启用），
        # 保证只注入 ModuleManager 的测试也能拿到有效配置
        for dl_type, instances in ModuleManager._downloaders.items():
            for name in instances:
                out[name] = DownloaderConf(
                    name=name, type=_type_to_str(dl_type), enabled=True
                )
        return out

    def get_config(self, name: str) -> Optional[DownloaderConf]:
        """按名称返回单个启用的下载器配置。"""
        return self.get_configs().get(name) if name else None

    # ------------------------------------------------------------------
    # 运行实例
    # ------------------------------------------------------------------
    def iterate_module_instances(self):
        """迭代运行中的下载器实例（每个实例一个 ServiceInfo）。"""
        configs = self.get_configs()
        for dl_type, instances in ModuleManager._downloaders.items():
            type_str = _type_to_str(dl_type)
            for name, server in instances.items():
                conf = configs.get(name) or DownloaderConf(
                    name=name, type=type_str, enabled=True
                )
                if not conf.enabled:
                    continue
                # 每个实例独立成环：module 只包裹该实例，
                # 使 module.name / list_torrents / remove_torrents 都精确对应
                yield ServiceInfo(
                    name=name,
                    instance=server,
                    module=_Module(dl_type, {name: server}),
                    type=conf.type if conf.type else type_str,
                    config=conf,
                )

    def get_services(
        self,
        type_filter: Optional[str] = None,
        name_filters: Optional[List[str]] = None,
    ) -> Dict[str, ServiceInfo]:
        """按类型/名称过滤运行中的下载器实例。"""
        names = set(name_filters) if name_filters else None
        out: Dict[str, ServiceInfo] = {}
        for service in self.iterate_module_instances():
            if not service.config:
                continue
            if type_filter is not None and service.type != type_filter:
                continue
            if names is not None and service.name not in names:
                continue
            out[service.name] = service
        return out

    def get_service(
        self,
        name: str,
        type_filter: Optional[str] = None,
    ) -> Optional[ServiceInfo]:
        """按名称返回单个运行中的下载器服务。"""
        if not name:
            return None
        return self.get_services(type_filter=type_filter).get(name)


__all__ = ["DownloaderHelper"]
