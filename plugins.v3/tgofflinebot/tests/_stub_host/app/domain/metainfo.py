# -*- coding: utf-8 -*-
"""
``app.domain.metainfo`` 替身：解析季集的最小实现。
"""

import re
from typing import List, Optional


class MetaInfo:
    """文件名解析替身。"""

    def __init__(self, title: str = "") -> None:
        """解析标题中的季集编号。"""
        self.title = title
        self.name = title
        self.year: Optional[str] = None
        self.begin_season: Optional[int] = None
        self.begin_episode: Optional[int] = None
        match = re.search(r"[Ss](\d{1,2})[Ee](\d{1,3})", title or "")
        if match:
            self.begin_season = int(match.group(1))
            self.begin_episode = int(match.group(2))

    @property
    def episode_list(self) -> List[int]:
        """返回解析到的集号列表。"""
        return [self.begin_episode] if self.begin_episode else []
