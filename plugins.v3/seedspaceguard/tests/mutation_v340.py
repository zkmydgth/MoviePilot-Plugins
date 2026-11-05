#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
变异测试（v3.4.0 专项）：验证「累计口径释放判定」的防护有效。

覆盖：
- 自适应等待放大（阶梯与上限）
- 零增长判据
- 达标轮清零连续计数
- 摘要分开报名义/实测
- 累计释放计算（退回单轮口径必须被捕获）

用法（在插件目录下）::

    python tests/mutation_v340.py
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(HERE)
TARGET = os.path.join(PLUGIN_DIR, "__init__.py")


# ① 自适应等待失去放大能力
ADAPTIVE_OLD = (
    "        scale = 1 + min(int(RELEASE_WAIT_SCALE_MAX) - 1, step)\n"
)
ADAPTIVE_NEW = "        scale = 1\n"

# ② 零增长判据被短路（低比率 + 零增长将永不停手）
NO_GROWTH_OLD = (
    "        no_growth = gain < min_gain\n"
)
NO_GROWTH_NEW = "        no_growth = False\n"

# ③ 达标轮不清零连续计数（连续判定被污染）
RESET_OLD = (
    "            return {\n"
    "                \"stalled\": False, \"stop\": False, \"gain\": gain,\n"
    "                \"stall_rounds\": 0, \"ratio\": ratio,\n"
    "            }\n"
)
RESET_NEW = (
    "            return {\n"
    "                \"stalled\": False, \"stop\": False, \"gain\": gain,\n"
    "                \"stall_rounds\": stall_rounds + 1, \"ratio\": ratio,\n"
    "            }\n"
)

# ④ 摘要退回「只报一个释放数字」
SUMMARY_OLD = (
    "                    suffix = (\n"
    "                        f\"，共处理 {deleted} 个资源，名义删除约 {released_gb}GB\"\n"
    "                        f\"（实测已回收 {actual_gb}GB）\"\n"
    "                    )\n"
)
SUMMARY_NEW = (
    "                    suffix = (\n"
    "                        f\"，共处理 {deleted} 个资源，释放约 {released_gb}GB\"\n"
    "                    )\n"
)

# ⑤ 文件级清理退回单轮口径
CUMULATIVE_OLD = (
    "                \"【保种空间守护】本轮已删除 %d 个文件，等待空间释放（最长 %d 秒）后复核…\",\n"
    "                len(plan), wait_seconds,\n"
    "            )\n"
    "            released_bytes = self._wait_for_release(free_now, round_nominal)\n"
    "            # 用「等待前剩余 + 实测释放」推回最新剩余，避免再读一次磁盘：\n"
    "            # 测试里的空间序列是定长的，多一次读取会越界（StopIteration）。\n"
    "            cum_released = (free_now + released_bytes) - free_base\n"
)
CUMULATIVE_NEW = (
    "                \"【保种空间守护】本轮已删除 %d 个文件，等待空间释放（最长 %d 秒）后复核…\",\n"
    "                len(plan), wait_seconds,\n"
    "            )\n"
    "            released_bytes = self._wait_for_release(free_now, round_nominal)\n"
    "            # 用「等待前剩余 + 实测释放」推回最新剩余，避免再读一次磁盘：\n"
    "            # 测试里的空间序列是定长的，多一次读取会越界（StopIteration）。\n"
    "            cum_released = released_bytes\n"
)

# ⑥ 等待窗口不再响应「停止」
WAIT_CANCEL_OLD = (
    "            if self._cancelled():\n"
    "                break\n"
    "            time.sleep(min(RELEASE_POLL_SECONDS, remaining))\n"
)
WAIT_CANCEL_NEW = (
    "            time.sleep(min(RELEASE_POLL_SECONDS, remaining))\n"
)

# ⑦ 等待期间取消仍走释放判定（取消被记成「空间未释放」）
FLOW_CANCEL_OLD = (
    "                \"【保种空间守护】本轮已删除 %d 个文件，等待空间释放（最长 %d 秒）后复核…\",\n"
    "                len(plan), wait_seconds,\n"
    "            )\n"
    "            released_bytes = self._wait_for_release(free_now, round_nominal)\n"
    "            # 用「等待前剩余 + 实测释放」推回最新剩余，避免再读一次磁盘：\n"
    "            # 测试里的空间序列是定长的，多一次读取会越界（StopIteration）。\n"
    "            cum_released = (free_now + released_bytes) - free_base\n"
    "            # 取消发生在等待期间：不再做释放判定（此时的「没释放」只是\n"
    "            # 等待被打断，不是硬信号），直接结束本轮。\n"
    "            if self._cancelled():\n"
    "                detail_lines.append(\"收到停止信号，本轮清理提前结束\")\n"
    "                self._clean_stats[\"cancelled\"] = True\n"
    "                break\n"
)
FLOW_CANCEL_NEW = (
    "                \"【保种空间守护】本轮已删除 %d 个文件，等待空间释放（最长 %d 秒）后复核…\",\n"
    "                len(plan), wait_seconds,\n"
    "            )\n"
    "            released_bytes = self._wait_for_release(free_now, round_nominal)\n"
    "            # 用「等待前剩余 + 实测释放」推回最新剩余，避免再读一次磁盘：\n"
    "            # 测试里的空间序列是定长的，多一次读取会越界（StopIteration）。\n"
    "            cum_released = (free_now + released_bytes) - free_base\n"
)

# ⑧ 释放基线沿用调用方旧快照（期间别人的释放被算成自己的）
BASE_SNAPSHOT_OLD = (
    "            if free_base is None:\n"
    "                free_base = free_now\n"
    "                self._clean_stats[\"free_base_bytes\"] = free_base\n"
    "            if free_now >= self._threshold_gb * GIB:\n"
    "                break\n"
    "            # 本轮按缺口预选文件：从最旧开始累计名义大小达到缺口即止\n"
)
BASE_SNAPSHOT_NEW = (
    "            if free_base is None:\n"
    "                free_base = free_bytes\n"
    "                self._clean_stats[\"free_base_bytes\"] = free_base\n"
    "            if free_now >= self._threshold_gb * GIB:\n"
    "                break\n"
    "            # 本轮按缺口预选文件：从最旧开始累计名义大小达到缺口即止\n"
)


MUTANTS = [
    ("自适应等待不再随规模放大", ADAPTIVE_OLD, ADAPTIVE_NEW,
     "应导致 test_scales_with_nominal / test_capped_by_max_seconds 失败"),
    ("零增长判据被短路", NO_GROWTH_OLD, NO_GROWTH_NEW,
     "应导致 test_stop_when_ratio_low_and_zero_growth / "
     "test_stop_when_zero_growth_twice 失败"),
    ("达标轮不清零连续计数", RESET_OLD, RESET_NEW,
     "应导致 test_healthy_resets_stalls 失败"),
    ("摘要退回单一释放数字", SUMMARY_OLD, SUMMARY_NEW,
     "应导致 test_multi_round_slow_reclaim_does_not_stop 失败"),
    ("文件级清理退回单轮口径", CUMULATIVE_OLD, CUMULATIVE_NEW,
     "应导致 test_multi_round_slow_reclaim_does_not_stop 失败"),
    ("等待窗口不再响应停止信号", WAIT_CANCEL_OLD, WAIT_CANCEL_NEW,
     "应导致 test_wait_returns_early_when_cancelled 失败"),
    ("取消仍走释放判定", FLOW_CANCEL_OLD, FLOW_CANCEL_NEW,
     "应导致 test_cancel_while_waiting_does_not_mark_stall 失败"),
    ("释放基线沿用旧快照", BASE_SNAPSHOT_OLD, BASE_SNAPSHOT_NEW,
     "应导致 test_stale_free_snapshot_not_counted_as_release 失败"),
]


def run_tests():
    """跑全部测试，返回 (是否全过, 尾部摘要)。"""
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

    caught = escaped = skipped = 0
    escaped_names = []
    try:
        for idx, (name, old, new, expect) in enumerate(MUTANTS, 1):
            if old not in original:
                print(f"[{idx:2d}] ⚠️  跳过（源码不匹配）：{name}")
                skipped += 1
                continue
            mutated = original.replace(old, new, 1)
            with open(TARGET, "w", encoding="utf-8") as handle:
                handle.write(mutated)
            ok, tail = run_tests()
            if ok:
                escaped += 1
                escaped_names.append(name)
                print(f"[{idx:2d}] ❌ 逃逸：{name}\n     期望：{expect}\n"
                      f"     实际：测试全部通过\n")
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
