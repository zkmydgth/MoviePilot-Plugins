"""``app.chain.media.facade`` 替身：媒体链实现。"""

from typing import Any, Optional

from ..base import ChainBase

__all__ = ["MediaChain"]


class MediaChain(ChainBase):
    """媒体处理链替身。

    ``recognize_by_meta`` 默认返回 ``None``（即「未识别到媒体」），
    这正是插件在无媒体库环境下应有的降级路径。
    """

    def recognize_by_meta(self, meta: Any, **_kwargs: Any) -> Optional[Any]:
        """按 meta 识别；替身环境恒为未识别（宿主签名为 ``(meta, obtain_images=...)``）。"""
        return None

    def recognize_media(self, mediainfo: Any = None, **kwargs: Any) -> Optional[Any]:
        return mediainfo
