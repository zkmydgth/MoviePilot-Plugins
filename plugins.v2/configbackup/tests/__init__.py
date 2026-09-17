# -*- coding: utf-8 -*-
"""
测试自举：把宿主桩与插件目录注入 sys.path。

在任何测试模块导入插件之前，先 ``import tests`` 即可。

用法::

    # 在 plugins.v2 目录下
    python -m unittest discover -s configbackup/tests -t configbackup -v
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_STUB_HOST = os.path.join(_HERE, "_stub_host")
_PLUGIN_PARENT = os.path.dirname(_HERE)


def _bootstrap() -> None:
    """把宿主桩与插件目录加入 sys.path（幂等）。"""
    for path in (_STUB_HOST, _PLUGIN_PARENT):
        if path not in sys.path:
            sys.path.insert(0, path)


_bootstrap()
