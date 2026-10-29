# -*- coding: utf-8 -*-
"""``app.schemas.query`` 替身：只保留插件用到的查询筛选项。"""


class SubscriptionHistoryFilter:
    """订阅完成历史的筛选条件替身，仅保存字段。"""

    def __init__(
        self,
        media_source=None,
        media_id=None,
        season=None,
        episode_group=None,
        **kwargs,
    ):
        """记录筛选条件，忽略插件未使用的可选字段。"""
        del kwargs
        self.media_source = media_source
        self.media_id = media_id
        self.season = season
        self.episode_group = episode_group


class QueryPageRequest:
    """分页请求替身，仅保存页码与条数。"""

    def __init__(self, page=1, count=50):
        """记录分页参数。"""
        self.page = page
        self.count = count
