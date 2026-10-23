# -*- coding: utf-8 -*-
"""``app.runtime.config`` 替身：只提供插件用到的设置项。"""


class _Settings:
    """设置替身。"""

    API_TOKEN = "test-token"
    PLUGIN_DATA_PATH = "/tmp"


settings = _Settings()
