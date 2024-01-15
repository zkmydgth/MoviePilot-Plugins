"""``app.foundation.singleton`` 替身：复刻宿主单例元类语义。"""

from typing import Any, Dict

__all__ = ["Singleton"]


class Singleton(type):
    """单例元类：同一类永远返回同一个实例。"""

    _instances: Dict[type, Any] = {}

    def __call__(cls, *args: Any, **kwargs: Any) -> Any:
        if cls not in cls._instances:
            cls._instances[cls] = super().__call__(*args, **kwargs)
        return cls._instances[cls]

    def get_existing_instance(cls, *args: Any, **kwargs: Any) -> Any:
        """按相同参数返回已创建实例，**不触发初始化**（与宿主一致）。"""
        return cls._instances.get(cls)

    def reset(cls) -> None:
        """测试辅助：清除已缓存实例。"""
        cls._instances.pop(cls, None)
