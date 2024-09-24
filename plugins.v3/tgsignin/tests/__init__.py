"""
TgSignin 测试包：把宿主桩与插件上级目录注入 sys.path。

这样测试里可以直接 ``from tgsignin import TgSignin`` 与
``from tgsignin.core.config import parse_accounts``，
不再依赖显式 PYTHONPATH（pytest.ini 里也配了同样两份路径）。
"""

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
# 宿主桩：app.plugins / app.runtime.* / app.schemas.* / app.sdk.events
_STUB_HOST = _HERE / "_stub_host"
# 插件目录的上一级：使 `import tgsignin` 可用
_SOURCE_PARENT = _HERE.parent.parent

for _path in (str(_STUB_HOST), str(_SOURCE_PARENT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)
