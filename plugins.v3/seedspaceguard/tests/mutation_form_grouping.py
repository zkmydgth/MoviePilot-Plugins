#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
变异测试（配置界面分组与条件显示专项）：验证 v3.0.6 UI 改造的测试防护有效。

背景：v3.0.6 把配置表单按「全局 / 种子级 / 仅文件 / 种子联动」四组重排，
并给模式专属项加了 ``show`` 条件显示（旧版前端不认则退化为全部显示，安全）。

本改造有**两个方向**的风险，都必须由测试守住：

  1. **误藏方向**（功能静默失效，最危险）：给通用/全局项误加 ``show``，
     用户在另一种模式下**完全看不到**该设置，界面毫无提示 —— 比报错更难排查。
  2. **误显方向**（回到改造前的老问题）：模式专属项漏加 ``show``，
     于是"选模式后才能看到设置"的诉求落空，误会照旧。

另外还要守住**文案**：``orphan_seed_scope`` 从前被误冠「种子级：」前缀，
而它实为两模式通用（执行体 ``_reap_orphan_seeds`` 的调用点在模式分派之外）。
一旦有人把前缀改回来，误会就回归了。

以及**说明文字常驻**：Vuetify 的 ``persistentHint`` 默认 ``false``，而
MoviePilot 的 ``FormRender.vue`` 未替插件控件传 ``persistent-hint``，
于是带 ``hint`` 的控件在桌面端要「点开/关闭开关」才显示说明（用户报过这个问题）。
插件侧显式传 ``"persistent-hint": True`` 解决之 —— 少传、传成字符串、传成
``False`` 都会让说明又退回「需交互才显示」。

用法（在插件目录下）::

    python3 tests/mutation_form_grouping.py
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(HERE)
TARGET = os.path.join(PLUGIN_DIR, "__init__.py")


# ---------------------------------------------------------------------------
# 变异体定义：(名称, 原始片段, 变异片段, 期望说明)
# ---------------------------------------------------------------------------

# ① 给全局项（试运行开关）误加 show → 另一种模式下"试运行"彻底消失
HIDE_GLOBAL_OLD = (
    '                        "model": "dry_run",\n'
    '                            "label": "试运行（只列不删）",\n'
    '                            "class": "mt-4",\n'
    '                            "hint": "开启后仅输出将清理的清单，不实际删除，'
    '建议首次先试运行",\n'
)
HIDE_GLOBAL_NEW = (
    '                        "model": "dry_run",\n'
    '                            "label": "试运行（只列不删）",\n'
    '                            "class": "mt-4",\n'
    '                            "hint": "开启后仅输出将清理的清单，不实际删除，'
    '建议首次先试运行",\n'
    '                            "show": "mode === \'seed\'",\n'
)

# ② 模式专属项漏掉 show（仅文件组的保护后缀）→ 诉求落空
# 锚点刻意只取「persistent-hint + show」两行：hint 文案会随版本改写，
# 一旦把 hint 正文纳入锚点，改文案就会让本变异体静默变成「跳过」＝防护失效。
# （该组合在源码中唯一：仅「保护文件后缀」输入框同时具备这两行。）
MISSING_SHOW_OLD = (
    '                            "persistent-hint": True,\n'
    '                            "show": "mode === \'file\'",\n'
)
MISSING_SHOW_NEW = (
    '                            "persistent-hint": True,\n'
)

# ③ 把种子级项的 show 写成 file（条件写反）→ 种子级模式下看不到自己的设置
WRONG_CONDITION_OLD = (
    '                            "items": downloader_items,\n'
    '                            "show": "mode === \'seed\'",\n'
)
WRONG_CONDITION_NEW = (
    '                            "items": downloader_items,\n'
    '                            "show": "mode === \'file\'",\n'
)

# ④ 给「种子联动」通用组标题加 show → 该组在某种模式下整块消失
COMMON_HEADER_SHOW_OLD = (
    '                    self._group_header(\n'
    '                        "种子联动设置（两种模式通用）",\n'
    '                        "以下开关在两种模式下均生效",\n'
    '                    ),\n'
)
COMMON_HEADER_SHOW_NEW = (
    '                    self._group_header(\n'
    '                        "种子联动设置（两种模式通用）",\n'
    '                        "以下开关在两种模式下均生效",\n'
    '                        show="mode === \'seed\'",\n'
    '                    ),\n'
)

# ⑤ 把 orphan_seed_scope 的「种子级：」前缀改回来 → 误会回归
REGRESS_PREFIX_OLD = (
    '                            "model": "orphan_seed_scope",\n'
    '                            "label": "空壳回收覆盖监控目录外的种子",\n'
)
REGRESS_PREFIX_NEW = (
    '                            "model": "orphan_seed_scope",\n'
    '                            "label": "种子级：空壳回收覆盖监控目录外的种子",\n'
)

# ⑥ 通用开关 hint 抹掉「两种模式」说明 → 用户又以为它只对某模式有效
DROP_MODE_HINT_OLD = (
    '                            "hint": "**两种模式通用**。删除文件后，'
    '顺带删除 MoviePilot 中对应的"\n'
)
DROP_MODE_HINT_NEW = (
    '                            "hint": "删除文件后，顺带删除 MoviePilot 中对应的"\n'
)

# ⑦ 分组标题文案里塞入「使用说明」四字 → 污染顶部说明的专项断言
POLLUTE_NOTICE_OLD = (
    '        props: Dict[str, Any] = {\n'
    '            "type": "info",\n'
    '            "variant": "tonal",\n'
    '            "class": "mt-6",\n'
    '            "text": f"▼ {title}　—　{desc}" if desc else f"▼ {title}",\n'
    '        }\n'
)
POLLUTE_NOTICE_NEW = (
    '        props: Dict[str, Any] = {\n'
    '            "type": "info",\n'
    '            "variant": "tonal",\n'
    '            "class": "mt-6",\n'
    '            "text": f"使用说明 ▼ {title}　—　{desc}" if desc else f"▼ {title}",\n'
    '        }\n'
)


# ⑧ 删掉「启用插件」控件的 persistent-hint → 说明文字又退回「点开关才显示」
DROP_PERSISTENT_OLD = (
    '                            "hint": "启用后按下方定时规则检查空间，'
    '不足时自动清理保种最久的资源",\n'
    '                            "persistent-hint": True,\n'
)
DROP_PERSISTENT_NEW = (
    '                            "hint": "启用后按下方定时规则检查空间，'
    '不足时自动清理保种最久的资源",\n'
)

# ⑨ 把 persistent-hint 的值写成字符串 "true" → 可能被 parseProps 当配置 key 取值
STRINGY_PERSISTENT_OLD = (
    '                            "hint": "df 对应的卷路径，插件读取其剩余空间",\n'
    '                            "persistent-hint": True,\n'
)
STRINGY_PERSISTENT_NEW = (
    '                            "hint": "df 对应的卷路径，插件读取其剩余空间",\n'
    '                            "persistent-hint": "true",\n'
)

# ⑩ 把 persistent-hint 的值改成 False（等价于没加）→ 说明又变成需交互才显示
FALSE_PERSISTENT_OLD = (
    '                            "hint": "清理完成后发送站内消息通知",\n'
    '                            "persistent-hint": True,\n'
)
FALSE_PERSISTENT_NEW = (
    '                            "hint": "清理完成后发送站内消息通知",\n'
    '                            "persistent-hint": False,\n'
)


MUTANTS = [
    ("给全局项（试运行）误加 show，导致另一种模式下被误藏",
     HIDE_GLOBAL_OLD, HIDE_GLOBAL_NEW,
     "全局项带 show 后，用户在另一模式看不到该设置 → 功能性静默失效"),
    ("模式专属项（保护后缀）漏加 show，选模式后仍显示另一种模式的设置",
     MISSING_SHOW_OLD, MISSING_SHOW_NEW,
     "专属项缺 show → 「选模式后才显示」的诉求落空"),
    ("种子级项的 show 条件写反为 file",
     WRONG_CONDITION_OLD, WRONG_CONDITION_NEW,
     "条件写反 → 种子级模式下看不到自己的设置"),
    ("给「种子联动」通用组标题加 show，导致该组整块消失",
     COMMON_HEADER_SHOW_OLD, COMMON_HEADER_SHOW_NEW,
     "通用组被隐藏 → 用户找不到通用设置"),
    ("把 orphan_seed_scope 的「种子级：」前缀改回来（误会回归）",
     REGRESS_PREFIX_OLD, REGRESS_PREFIX_NEW,
     "两模式通用的项被误冠模式前缀 → 用户误会其生效范围"),
    ("通用开关 hint 删去「两种模式」说明",
     DROP_MODE_HINT_OLD, DROP_MODE_HINT_NEW,
     "用户又会以为该开关只对某一模式有效"),
    ("分组标题文案塞入「使用说明」，污染顶部说明专项断言",
     POLLUTE_NOTICE_OLD, POLLUTE_NOTICE_NEW,
     "组标题被误当成顶部说明 → 长度/关键词断言取错对象"),
    ("删掉某控件的 persistent-hint，说明文字退回「点开关才显示」",
     DROP_PERSISTENT_OLD, DROP_PERSISTENT_NEW,
     "缺 persistent-hint → 桌面端 hint 又要在聚焦时才出现"),
    ("把 persistent-hint 的值写成字符串 \"true\"",
     STRINGY_PERSISTENT_OLD, STRINGY_PERSISTENT_NEW,
     "字符串可能被 parseProps 当成配置 key 取值 → 属性失效"),
    ("把 persistent-hint 的值改成 False（等价于未设置）",
     FALSE_PERSISTENT_OLD, FALSE_PERSISTENT_NEW,
     "值为假 → hint 仍只在聚焦时显示，等同于没加"),
]


def run_tests():
    """跑全部测试，返回 (是否全过, 摘要)。"""
    args = [sys.executable, "-m", "pytest", "tests/",
            "-q", "--no-header", "-p", "no:cacheprovider"]
    proc = subprocess.run(
        args, cwd=PLUGIN_DIR, capture_output=True, text=True,
    )
    output = proc.stdout + proc.stderr
    tail = "\n".join(output.strip().splitlines()[-3:])
    return proc.returncode == 0, tail


def main() -> int:
    if not os.path.exists(TARGET):
        print(f"❌ 找不到目标源码：{TARGET}")
        return 1
    original = open(TARGET, encoding="utf-8").read()

    base_ok, base_tail = run_tests()
    print(f"基线：{'✅ 全部通过' if base_ok else '❌ 基线即失败'}")
    if not base_ok:
        print(base_tail)
        return 1
    print()

    caught = escaped = 0
    escaped_names = []
    skipped = 0
    try:
        for idx, (name, old, new, expect) in enumerate(MUTANTS, 1):
            if old not in original:
                print(f"[{idx:2d}] ⚠️  跳过（源码不匹配，需更新变异体定义）：{name}")
                skipped += 1
                continue
            mutated = original.replace(old, new, 1)
            with open(TARGET, "w", encoding="utf-8") as handle:
                handle.write(mutated)
            ok, tail = run_tests()
            if ok:
                escaped += 1
                escaped_names.append(name)
                print(f"[{idx:2d}] ❌ 逃逸：{name}")
                print(f"     期望：{expect}")
                print("     实际：测试全部通过，该缺陷未被捕获\n")
            else:
                caught += 1
                print(f"[{idx:2d}] ✅ 捕获：{name}")
    finally:
        with open(TARGET, "w", encoding="utf-8") as handle:
            handle.write(original)

    print()
    print("=" * 72)
    total = caught + escaped
    print(f"变异测试结果：{caught}/{total} 被捕获，{escaped} 个逃逸"
          + (f"，{skipped} 个跳过" if skipped else ""))
    if escaped_names:
        print("逃逸清单（需补充测试）：")
        for name in escaped_names:
            print(f"  - {name}")
        return 1
    if skipped:
        # 跳过意味着变异体定义与源码脱节，防护可能已悄悄失效 —— 视为失败
        print("❌ 存在被跳过的变异体：定义与源码不匹配即等于该缺陷无人守护")
        return 1
    print("🎉 全部变异被捕获，测试防护有效")
    return 0


if __name__ == "__main__":
    sys.exit(main())
