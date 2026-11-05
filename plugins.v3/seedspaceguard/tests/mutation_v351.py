#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
变异测试（v3.5.1 专项）：验证「宽限一轮」与「最终快照数字」的防护有效。

覆盖：
- 摘要实测回收量回退成窗口推演值（与停手时剩余无法对账）
- 停手前宽限等待被删掉（判定未释放即刻停手）
- 宽限后不复测（等了也白等，仍然直接停手）

用法（在插件目录下）::

    python tests/mutation_v351.py
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(HERE)
TARGET = os.path.join(PLUGIN_DIR, "__init__.py")


# ① 摘要实测回收量退回窗口推演值
FINAL_READ_OLD = (
    "                    if free_base_bytes is not None and free_end is not None:\n"
    "                        actual_gb = round(\n"
    "                            max(0, free_end - free_base_bytes) / GIB, 1\n"
    "                        )\n"
)
FINAL_READ_NEW = (
    "                    if False:\n"
    "                        actual_gb = round(\n"
    "                            max(0, free_end - free_base_bytes) / GIB, 1\n"
    "                        )\n"
)

# ② 停手前的宽限等待整段删掉（文件级链路）
GRACE_WAIT_OLD = (
    "            if verdict[\"stop\"]:\n"
    "                # 停手前宽限一轮（理由同种子级清理）：只等待、不再删文件。\n"
    "                grace_gain = self._wait_for_release(\n"
    "                    free_base + cum_released, round_nominal\n"
    "                )\n"
    "                if grace_gain > 0:\n"
    "                    cum_released += grace_gain\n"
    "                    logger.info(\n"
    "                        \"【保种空间守护】判定未达标，宽限一轮后复测：再回收 %.1fGB\",\n"
    "                        grace_gain / GIB,\n"
    "                    )\n"
    "                    verdict = self._release_verdict(\n"
    "                        cum_released, int(cum_deleted), prev_cum_released,\n"
    "                        verdict[\"stall_rounds\"], round_nominal,\n"
    "                    )\n"
    "                    stall = verdict[\"stall_rounds\"]\n"
    "            if verdict[\"stop\"]:\n"
)
GRACE_WAIT_NEW = (
    "            if verdict[\"stop\"]:\n"
    "                pass\n"
    "            if verdict[\"stop\"]:\n"
)

# ③ 宽限后不复测（等了但仍按原判定停手）
GRACE_RECHECK_OLD = GRACE_WAIT_OLD
GRACE_RECHECK_NEW = (
    "            if verdict[\"stop\"]:\n"
    "                # 停手前宽限一轮（理由同种子级清理）：只等待、不再删文件。\n"
    "                grace_gain = self._wait_for_release(\n"
    "                    free_base + cum_released, round_nominal\n"
    "                )\n"
    "                if grace_gain > 0:\n"
    "                    cum_released += grace_gain\n"
    "            if verdict[\"stop\"]:\n"
)


MUTANTS = [
    (
        "摘要实测回收量回退成窗口推演值（停手时剩余无法对账）",
        FINAL_READ_OLD,
        FINAL_READ_NEW,
        "第 1 个用例断言「实测已回收 3.0GB」",
    ),
    (
        "删掉停手前的宽限等待（判定未释放即刻停手）",
        GRACE_WAIT_OLD,
        GRACE_WAIT_NEW,
        "等待调用次数必须为 2",
    ),
    (
        "宽限后不复测（等了仍按原判定停手）",
        GRACE_RECHECK_OLD,
        GRACE_RECHECK_NEW,
        "宽限内有回收必须继续清理",
    ),
]


def run_tests():
    """跑全部测试，返回 (是否全过, 尾部摘要)。"""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--no-header"],
        cwd=PLUGIN_DIR, capture_output=True, text=True,
    )
    tail = (proc.stdout or proc.stderr or "").strip().splitlines()
    return proc.returncode == 0, (tail[-1] if tail else "")


def main() -> int:
    original = open(TARGET, encoding="utf-8").read()
    baseline_ok, baseline_tail = run_tests()
    if not baseline_ok:
        print(f"基线测试未通过，终止：{baseline_tail}")
        return 1
    print("基线：✅ 全部通过")

    caught = escaped = skipped = 0
    escaped_names = []
    try:
        for index, (name, old, new, expect) in enumerate(MUTANTS, 1):
            if original.count(old) != 1:
                skipped += 1
                print(f"[{index:2d}] ⚠️ 跳过（锚点失配 {original.count(old)} 处）：{name}")
                continue
            mutated = original.replace(old, new, 1)
            open(TARGET, "w", encoding="utf-8").write(mutated)
            ok, _tail = run_tests()
            open(TARGET, "w", encoding="utf-8").write(original)
            if ok:
                escaped += 1
                escaped_names.append(name)
                print(f"[{index:2d}] ❌ 逃逸：{name}（期望：{expect}）")
            else:
                caught += 1
                print(f"[{index:2d}] ✅ 捕获：{name}")
    finally:
        open(TARGET, "w", encoding="utf-8").write(original)
        print("已还原原始源码。")

    print()
    print("=" * 72)
    total = caught + escaped
    print(f"变异测试结果：{caught}/{total} 被捕获，{escaped} 个逃逸"
          + (f"，{skipped} 个跳过" if skipped else ""))
    if escaped_names:
        print("逃逸清单：")
        for name in escaped_names:
            print(f"  - {name}")
        return 1
    if skipped:
        print("❌ 存在被跳过的变异体：定义与源码不匹配即等于该缺陷无人守护")
        return 1
    print("🎉 全部变异被捕获，测试防护有效")
    return 0


if __name__ == "__main__":
    sys.exit(main())
