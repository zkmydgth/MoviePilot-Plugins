"""宿主桩：``app.plugins._PluginBase`` 的最小实现。"""

import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class _PluginBase:
    """插件基类桩：只实现本插件用到的方法。"""

    # 由插件类覆盖
    plugin_name = "stub"
    plugin_config_prefix = "stub_"
    plugin_version = "0.0.0"

    def __init__(self) -> None:
        """初始化：为每个实例分配一个临时数据目录。"""
        self._stub_data_dir = Path(tempfile.mkdtemp(prefix="tgsignin_stub_"))
        # 记录 update_config 的调用，便于断言
        self.config_updates: List[Dict[str, Any]] = []
        # 记录 post_message 的调用
        self.messages: List[Dict[str, Any]] = []

    def init_plugin(self, config: dict = None) -> None:
        """子类覆盖。"""

    def get_state(self) -> bool:
        """子类覆盖。"""
        return False

    def stop_service(self) -> None:
        """子类覆盖。"""
        return None

    def get_form(self) -> Tuple[Optional[List[dict]], Dict[str, Any]]:
        """子类覆盖。"""
        return None, {}

    def get_page(self) -> Optional[List[dict]]:
        """子类覆盖。"""
        return None

    def get_api(self) -> List[Dict[str, Any]]:
        """子类覆盖。"""
        return []

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """子类覆盖。"""
        return []

    def get_service(self) -> List[Dict[str, Any]]:
        """子类覆盖。"""
        return []

    def update_config(self, config: Dict[str, Any], plugin_id: Optional[str] = None) -> bool:
        """
        记录一次配置更新（桩实现）。

        :param config: 配置字典
        :param plugin_id: 插件 ID（桩忽略）
        :return bool: 恒为 True
        """
        del plugin_id
        self.config_updates.append(dict(config))
        return True

    def get_data_path(self, plugin_id: Optional[str] = None) -> Path:
        """
        返回实例的临时数据目录。

        :param plugin_id: 插件 ID（桩忽略）
        :return Path: 数据目录
        """
        del plugin_id
        return self._stub_data_dir

    def post_message(self, **kwargs: Any) -> None:
        """
        记录一次通知（桩实现）。

        :param kwargs: 通知参数
        :return None
        """
        self.messages.append(dict(kwargs))
