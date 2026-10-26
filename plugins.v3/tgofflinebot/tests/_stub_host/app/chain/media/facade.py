# -*- coding: utf-8 -*-
"""
``MediaChain`` 替身：识别方法默认返回 None，测试按需替换。
"""


class MediaChain:
    """媒体链替身。"""

    def recognize_by_meta(self, meta, media_source=None, episode_group=None):
        """
        识别媒体；替身固定返回 None。

        :param meta: 解析对象
        :param media_source: 数据源
        :param episode_group: 剧集组
        :return: None
        """
        return None
