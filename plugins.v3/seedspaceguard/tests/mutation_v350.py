#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
变异测试（v3.5.0 专项）：验证审计修复的防护有效。

覆盖：
- 等待时长/目标取本轮名义量（回退累计必须被捕获）
- 零增长按本轮比例判定（回退绝对阈值必须被捕获）
- 小批量豁免看本轮名义量（回退累计必须被捕获）
- 互斥锁实例级（回退类级必须被捕获）
- 并发回执可见（回退静默必须被捕获）
- 取消与停手文案互斥（优先级反转必须被捕获）
- API 跳过必须回报失败
- @eaDir 收尾失败必须留痕
- 计时状态必须清零

用法（在插件目录下）::

    python tests/mutation_v350.py
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(HERE)
TARGET = os.path.join(PLUGIN_DIR, "__init__.py")


# ① 文件级等待窗口退回累计名义量
ROUND_WAIT_OLD = (
    "            # 等待时长取「本轮新增名义量」（理由同种子级清理）\n"
    "            round_nominal = int(cum_deleted) - prev_cum_deleted\n"
)
ROUND_WAIT_NEW = (
    "            # 等待时长取「本轮新增名义量」（理由同种子级清理）\n"
    "            round_nominal = int(cum_deleted)\n"
)

# ② 零增长回退成绝对阈值（删 100GB 只回收 1GB 会被当成有进展）
NO_GROWTH_OLD = (
    "        min_gain = int(RELEASE_STALL_MIN_BYTES)\n"
    "        if round_nominal:\n"
    "            min_gain = max(\n"
    "                min_gain, int(round_nominal * RELEASE_STALL_MIN_RATIO)\n"
    "            )\n"
)
NO_GROWTH_NEW = "        min_gain = int(RELEASE_STALL_MIN_BYTES)\n"

# ③ 小批量豁免回退成累计口径（等于永不触发）
EXEMPT_OLD = "        current = nominal if round_nominal is None else round_nominal\n"
EXEMPT_NEW = "        current = nominal\n"

# ④ 互斥锁回退到类级（分身互相阻塞）
LOCK_OLD = "        lock = self.__dict__.get(\"_instance_run_lock\")\n"
LOCK_NEW = "        lock = self.__class__.__dict__.get(\"_instance_run_lock\")\n"

# ⑤ 并发回执退回静默固定文案
BUSY_OLD = (
    "        if not self._lock.acquire(blocking=False):\n"
    "            return self._busy_result()\n"
)
BUSY_NEW = (
    "        if not self._lock.acquire(blocking=False):\n"
    "            return \"已有清理任务正在运行，本次跳过\"\n"
)

# ⑥ 文案优先级反转（取消时同时报故障）
WORDING_OLD = (
    "        if self._clean_stats.get(\"cancelled\"):\n"
    "            return \"（已取消，本轮提前结束）\"\n"
    "        if self._clean_stats.get(\"stalled\"):\n"
)
WORDING_NEW = (
    "        if self._clean_stats.get(\"stalled\"):\n"
    "            return \"（已提前停止：空间未按预期释放）\"\n"
    "        if self._clean_stats.get(\"cancelled\"):\n"
)

# ⑦ API 把「仍在运行」当成执行成功
API_SKIP_OLD = (
    "            or result.startswith(\"上一轮清理仍在运行\")\n"
)
API_SKIP_NEW = (
    "            or result == \"上一轮清理仍在运行\"\n"
)

# ⑧ @eaDir 收尾失败重新静默
EA_LOG_OLD = (
    "        except OSError as err:\n"
    "            # 静默吞错会让「目录明明已空却删不掉」无从追查；这里只影响清理\n"
    "            # 彻底性（父目录会因残留而保留），不影响任何数据安全。\n"
    "            logger.warning(\"【保种空间守护】清理空的 @eaDir 目录失败：%s\", err)\n"
)
EA_LOG_NEW = (
    "        except OSError:\n"
    "            pass\n"
)

# ⑨ 计时状态收尾不清零（下一次回执会报错误的已运行时长）
RESET_OLD = (
    "        finally:\n"
    "            self._running = False\n"
    "            self._run_started_at = 0.0\n"
)
RESET_NEW = (
    "        finally:\n"
    "            self._running = False\n"
)


MUTANTS = [
    ("等待窗口退回累计名义量", ROUND_WAIT_OLD, ROUND_WAIT_NEW,
     "应导致 test_later_round_waits_on_its_own_nominal 失败"),
    ("零增长回退绝对阈值", NO_GROWTH_OLD, NO_GROWTH_NEW,
     "应导致 test_large_round_with_tiny_gain_is_zero_growth 失败"),
    ("小批量豁免回退累计", EXEMPT_OLD, EXEMPT_NEW,
     "应导致 test_small_round_is_exempt 失败"),
    ("互斥锁回退类级", LOCK_OLD, LOCK_NEW,
     "应导致 test_lock_is_instance_scoped 失败"),
    ("并发回执退回静默", BUSY_OLD, BUSY_NEW,
     "应导致 test_busy_message_states_elapsed 失败"),
    ("取消与停手文案优先级反转", WORDING_OLD, WORDING_NEW,
     "应导致 test_both_flags_render_cancel_only 失败"),
    ("API 把跳过当成成功", API_SKIP_OLD, API_SKIP_NEW,
     "应导致 test_busy_api_call_is_not_reported_as_success 失败"),
    ("@eaDir 收尾失败重新静默", EA_LOG_OLD, EA_LOG_NEW,
     "应导致 test_rmdir_failure_logs_warning 失败"),
    ("计时状态收尾不清零", RESET_OLD, RESET_NEW,
     "应导致 test_started_at_cleared_after_real_run 失败"),
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
                print(f"[{index:2d}] ❌ 逃逸：{name}")
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
