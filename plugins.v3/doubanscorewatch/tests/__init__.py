# -*- coding: utf-8 -*-
"""测试包：把宿主桩与插件目录注入 sys.path。"""

import sys
from pathlib import Path

_STUB_HOST = Path(__file__).parent / "_stub_host"
_PLUGIN_PARENT = Path(__file__).parent.parent

for path in (str(_STUB_HOST), str(_PLUGIN_PARENT)):
    if path not in sys.path:
        sys.path.insert(0, path)
