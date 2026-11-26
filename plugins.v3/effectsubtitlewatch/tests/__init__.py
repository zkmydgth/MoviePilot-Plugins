# -*- coding: utf-8 -*-
"""测试包：把宿主桩与插件父目录加入路径。"""

import sys
from pathlib import Path

_STUB_HOST = Path(__file__).parent / "_stub_host"
_PLUGIN_PARENT = Path(__file__).parent.parent.parent

if str(_STUB_HOST) not in sys.path:
    sys.path.insert(0, str(_STUB_HOST))
if str(_PLUGIN_PARENT) not in sys.path:
    sys.path.append(str(_PLUGIN_PARENT))
