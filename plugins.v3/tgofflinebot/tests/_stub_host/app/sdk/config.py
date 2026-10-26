# -*- coding: utf-8 -*-
"""
``app.sdk.config`` 替身：提供插件读取宿主设置所需的最小对象。
"""

from types import SimpleNamespace


settings = SimpleNamespace(PORT=3001, API_TOKEN="test-token", PROXY="")
