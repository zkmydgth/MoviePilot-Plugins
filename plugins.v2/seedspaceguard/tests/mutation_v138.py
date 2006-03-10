#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
变异测试（v1.3.8 专项）：验证三项待办改动的测试防护有效。

覆盖：
- 待办 1：手动/命令触发不再静默（空间充足分支的通知判定）
- 待办 2：配置表单顶部说明精简（文案长度）
- 待办 3：空壳回收数计入 seeds 统计（口径统一）

用法（在插件目录下）::

    python3 tests/mutation_v138.py
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(HERE)
TARGET = os.path.join(PLUGIN_DIR, "seedspaceguard.py")

# 各片段用显式拼接构造，避免三引号内嵌三引号导致的转义问题
NEED_NOTIFY_OLD = (
    '                need_notify = bool(orphan_lines) or source != "定时"'
)
NEED_NOTIFY_NEW_FALSE = (
    "                need_notify = True if orphan_lines else False"
)
NEED_NOTIFY_NEW_TRUE = "                need_notify = True"
NEED_NOTIFY_NEW_REVERSED = (
    '                need_notify = bool(orphan_lines) or source == "定时"'
)

STATS_OLD = (
    '                self._clean_stats["seeds"] = (\n'
    '                    (self._clean_stats.get("seeds") or 0)\n'
    '                    + orphan_stats["torrent"]\n'
    '                )'
)

SEED_ACC_OLD = (
    "            # 累加而非覆盖：本轮可能在进入本方法前就已回收过空壳种子，\n"
    "            # 直接赋值会把那部分计数冲掉，导致摘要少报。\n"
    '            self._clean_stats["seeds"] = (\n'
    '                (self._clean_stats.get("seeds") or 0) + deleted\n'
    "            )\n"
    "            return deleted, round(released_gb, 1), detail_lines"
)
SEED_ACC_NEW = (
    '            self._clean_stats["seeds"] = deleted\n'
    "            return deleted, round(released_gb, 1), detail_lines"
)

NOTIFY_LINE_OLD = (
    '        lines.append(f"{prefix}删除种子：{s.get(\'seeds\') or 0} 个")'
)

NOTICE_NEW = (
    '                                            "text": "使用说明：空间低于阈值时，按「保种最久」优先清理，"\n'
    '                                                    "直到恢复到阈值以上。删除后会实测真实释放量，"\n'
    '                                                    "若几乎未释放则立即停止并告警——"\n'
    '                                                    "宁可空间不足，也不过量删除。"\n'
    '                                                    "首次使用请先用「立即试运行一次」预览将删内容，"\n'
    '                                                    "确认无误后再正式清理。",'
)
NOTICE_OLD = (
    '                                            "text": "使用说明：卷剩余空间低于阈值时，按「保种最久」优先自动清理配置目录中的资源，"\n'
    '                                                    "直到空间恢复到阈值以上。支持多个目录：把「下载目录」与「媒体库目录」"\n'
    '                                                    "都填进来，插件会按 inode 自动识别硬链接并两侧一并删除，无需依赖其它插件联动。"\n'
    '                                                    "每轮删除后实测空间释放量，若空间几乎未释放（如硬链接仍有残留引用、"\n'
    '                                                    "快照占用），会立即停止并告警，宁可空间不足也不过量删除。"\n'
    '                                                    "种子级=删除下载器中最旧的已完成种子（连带文件，"\n'
    '                                                    "可用下方「目标下载器」限定范围，留空=全部）；"\n'
    '                                                    "仅文件=只删文件。建议先试运行预览将删内容，确认后再正式启用；"\n'
    '                                                    "清理目录本身不会被删除。删除后还会顺带清理 Synology 在 @eaDir 下遗留的"\n'
    '                                                    "媒体索引残片，避免出现「仅剩 @eaDir」的空壳目录。"\n'
    '                                                    "清理范围为「除保护文件后缀命中的文件外，目录下所有文件」，"\n'
    '                                                    "不区分文件类型；需要保留的文件请填入「保护文件后缀」。"\n'
    '                                                    "底部两项「联动清理」可选开启，分别联动删除种子、删除转移记录；"\n'
    '                                                    "其中仅文件模式的删种有严格前置条件："\n'
    '                                                    "该种子的所有文件都已删除才会删种。",'
)


MUTANTS = [
    (
        "待办1回归：手动触发仍硬编码 notify=False（顶掉用户配置）",
        NEED_NOTIFY_OLD,
        NEED_NOTIFY_NEW_FALSE,
        "应导致 test_manual_trigger_notifies_when_space_sufficient / "
        "test_command_trigger_notifies_when_space_sufficient 失败",
    ),
    (
        "待办1回归：定时触发也无条件通知（噪音回归）",
        NEED_NOTIFY_OLD,
        NEED_NOTIFY_NEW_TRUE,
        "应导致 test_timed_trigger_stays_silent_when_space_sufficient 失败",
    ),
    (
        "待办1回归：手动触发被误判为定时（判定方向反转）",
        NEED_NOTIFY_OLD,
        NEED_NOTIFY_NEW_REVERSED,
        "应导致 test_manual_trigger_notifies_when_space_sufficient 失败",
    ),
    (
        "待办3回归：空壳回收数不计入 seeds 统计（口径矛盾回归）",
        STATS_OLD,
        "                pass",
        "应导致 test_orphan_count_folded_into_seed_stat 失败",
    ),
    (
        "待办3回归：_clean_by_seed 覆盖而非累加空壳计数",
        SEED_ACC_OLD,
        SEED_ACC_NEW,
        "应导致种子级模式下空壳计数被覆盖（由 test_orphan_count_folded_into_seed_stat 覆盖）",
    ),
    (
        "待办3回归：通知摘要不渲染删除种子数",
        NOTIFY_LINE_OLD,
        "        pass",
        "应导致 test_orphan_count_folded_into_seed_stat（断言摘要含「删除种子：1 个」）失败",
    ),
    (
        "待办2回归：表单顶部说明恢复为超长文案（447 字）",
        NOTICE_NEW,
        NOTICE_OLD,
        "应导致 test_form_top_notice_is_concise 失败",
    ),
]


def run_tests():
    """跑全部测试，返回 (是否全过, 摘要)。"""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--no-header", "-p",
         "no:cacheprovider"],
        cwd=PLUGIN_DIR, capture_output=True, text=True,
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
    try:
        for idx, (name, old, new, expect) in enumerate(MUTANTS, 1):
            if old not in original:
                print(f"[{idx:2d}] ⚠️  跳过（源码不匹配，需更新变异体定义）：{name}")
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
                print(f"     实际：测试全部通过，该缺陷未被捕获\n")
            else:
                caught += 1
                print(f"[{idx:2d}] ✅ 捕获：{name}")
    finally:
        with open(TARGET, "w", encoding="utf-8") as handle:
            handle.write(original)

    print()
    print("=" * 72)
    total = caught + escaped
    print(f"变异测试结果：{caught}/{total} 被捕获，{escaped} 个逃逸")
    if escaped_names:
        print("逃逸清单（需补充测试）：")
        for name in escaped_names:
            print(f"  - {name}")
        return 1
    print("🎉 全部变异被捕获，测试防护有效")
    return 0


if __name__ == "__main__":
    sys.exit(main())
