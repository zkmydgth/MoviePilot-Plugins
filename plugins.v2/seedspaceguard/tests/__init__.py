# -*- coding: utf-8 -*-
"""
测试自举：把宿主桩与插件目录注入 sys.path。

在任何测试模块导入插件之前，先 ``import tests`` 即可；若直接运行单个测试文件，
模块内部的 ``_bootstrap()`` 也会兜底完成注入。

用法::

    # 方式一（推荐，在插件目录下）
    python -m unittest discover -s tests -t . -v

    # 方式二
    PYTHONPATH=..:. python -m unittest tests.test_config_and_form -v
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
