# -*- coding: utf-8 -*-
"""
还原按钮 UI 的变异测试。

为什么需要
----------
``test_restore_flow.py`` 全绿只能说明"当前代码满足断言"，
不能说明"断言真的能挡住错误"。本脚本故意把插件代码改坏，
再跑同一套测试：

- 测试**失败** → 变异体被捕 ✅ 说明用例有效
- 测试**仍通过** → 变异体逃逸 ❌ 说明用例是绿色装饰

用法
----
在 ``plugins.v2`` 目录下执行::

    python3 configbackup/tests/mutation_restore_ui.py

退出码：0 = 零逃逸；1 = 存在逃逸（需补测试）。
"""

import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PLUGIN_DIR = os.path.dirname(_HERE)
_PLUGIN_SRC = os.path.join(_PLUGIN_DIR, "__init__.py")
# 以 plugins.v2 为工作目录，使 "configbackup" 可作为顶层包导入
_WORKDIR = os.path.dirname(_PLUGIN_DIR)

# (说明, 原文, 变异后, 预期)
MUTANTS = [
    (
        "按钮回退：确认还原重新变为无条件显示",
        "        # 顶部操作按钮\n        actions = [",
        '        # 顶部操作按钮\n        actions = [\n'
        '            {\n'
        '                "component": "VBtn",\n'
        '                "props": {"color": "error", "size": "small"},\n'
        '                "text": "确认还原",\n'
        '                "events": {"click": {"api": "plugin/ConfigBackup/restore",\n'
        '                                     "method": "get",\n'
        '                                     "params": {"apikey": settings.API_TOKEN,\n'
        '                                                "confirm": "1"}}},\n'
        '            },',
        "应导致 test_no_confirm_button_without_selection 失败",
    ),
    (
        "行内按钮回退：还原按钮去掉文字，退回纯图标",
        '"text": "还原",',
        '"text": "",',
        "应导致 test_row_restore_button_has_text 失败",
    ),
    (
        "引导文案丢失：未选中时不再提示两步流程",
        "还原操作分两步：先在下表点击目标备份行的【还原】按钮选中它",
        "还原操作说明",
        "应导致 test_guidance_when_nothing_selected 失败",
    ),
    (
        "选中提示丢失文件名：用户不知道选中了哪一份",
        "f\"已选中待还原备份：{pending.get('filename', '')}\"",
        'f"已选中待还原备份"',
        "应导致 test_guidance_shows_selected_filename 失败",
    ),
    (
        "取消按钮丢失：选中后无法放弃本次还原",
        '"text": "取消还原",',
        '"text": "取消",',
        "应导致 test_confirm_button_appears_after_selection 失败",
    ),
]

_TEST_RUNNER = (
    "import sys, os, unittest\n"
    "sys.path.insert(0, os.path.abspath('configbackup/tests/_stub_host'))\n"
    "sys.path.insert(0, os.path.abspath('configbackup'))\n"
    "s = unittest.TestLoader().discover('configbackup/tests',\n"
    "                                   top_level_dir='configbackup')\n"
    "r = unittest.TextTestRunner(verbosity=0).run(s)\n"
    "sys.exit(0 if r.wasSuccessful() else 1)\n"
)


def _run_tests(verbose: bool = False) -> int:
    """返回 0 表示测试通过。"""
    result = subprocess.run(
        [sys.executable, "-c", _TEST_RUNNER],
        cwd=_WORKDIR,
        capture_output=True,
        text=True,
    )
    if verbose and result.returncode != 0:
        sys.stderr.write(result.stderr[-2000:])
    return result.returncode


def main() -> int:
    with open(_PLUGIN_SRC, encoding="utf-8") as handle:
        original = handle.read()

    print("=" * 66)
    print("ConfigBackup 还原 UI 变异测试")
    print("=" * 66)

    if _run_tests() != 0:
        print("❌ 基线未通过（未变异时测试就失败），先修复测试本身")
        return 1
    print("✅ 基线：全部用例通过\n")

    escaped = []
    try:
        for name, old, new, expect in MUTANTS:
            if old not in original:
                print(f"⚠️  跳过：{name}\n    锚点未在源码中找到，需同步更新本脚本")
                continue
            with open(_PLUGIN_SRC, "w", encoding="utf-8") as handle:
                handle.write(original.replace(old, new, 1))
            if _run_tests() == 0:
                escaped.append(name)
                print(f"❌ 逃逸：{name}\n    预期：{expect}")
            else:
                print(f"✅ 被捕：{name}")
    finally:
        with open(_PLUGIN_SRC, "w", encoding="utf-8") as handle:
            handle.write(original)
        print("\n源码已还原")

    print("-" * 66)
    total = len(MUTANTS)
    print(f"变异体 {total} 个，逃逸 {len(escaped)} 个")
    if escaped:
        print("存在逃逸，需补充测试用例：")
        for name in escaped:
            print(f"  - {name}")
        return 1
    print("零逃逸：现有用例能有效拦截本次涉及的所有回退。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
