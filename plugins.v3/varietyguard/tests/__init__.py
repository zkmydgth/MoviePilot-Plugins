# -*- coding: utf-8 -*-
"""
测试自举：把宿主桩与插件目录注入 sys.path。

在任何测试模块导入插件之前，先 ``import tests`` 即可；直接运行单个测试文件时，
``_bootstrap()`` 也会兜底完成注入。
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_STUB_HOST = os.path.join(_HERE, "_stub_host")
_PLUGIN_DIR = os.path.dirname(_HERE)
_REPO_DIR = os.path.dirname(_PLUGIN_DIR)


def _bootstrap() -> None:
    """把宿主桩、插件目录与其父目录加入 sys.path（幂等）。"""
    for path in (_STUB_HOST, _PLUGIN_DIR, _REPO_DIR):
        if path not in sys.path:
            sys.path.insert(0, path)


_bootstrap()
