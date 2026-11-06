#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
变异测试（备份数量兜底）：攻击 v3.4.0 新增的自停与确认逻辑。

与其它 mutation_*.py 的分工
---------------------------
- ``mutation_boundary.py``：路径穿越、误删防护、损坏输入
- ``mutation_restore_ui.py``：两阶段交互 UI
- ``mutation_webdav.py``：远端备份
- 本文件：备份数量兜底（阈值判定、暂停闸门、确认决策、硬上限止血）

框架差异
--------
本脚本覆盖 ``guard.py`` 与 ``__init__.py`` 两个文件，因此条目写成
``(目标, name, pairs, expect)``，``目标`` 取 ``guard`` / ``init``；两个文件
在开始时各备份一份，结束时无条件还原。锚点必须是**文件中唯一**的一段，
否则同一处被改两次会静默指向错误的代码。
"""

import os
import re
import shutil
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(_HERE)
PLUGIN_PARENT = os.path.dirname(PLUGIN_DIR)

TARGETS = {
    "guard": os.path.join(PLUGIN_DIR, "guard.py"),
    "init": os.path.join(PLUGIN_DIR, "__init__.py"),
}
BACKUPS = {key: f"/tmp/cb_guard_{key}.bak" for key in TARGETS}


MUTANTS = [
    # ---------------- 阈值 ----------------
    (
        "guard",
        "上限去掉绝对下限：保留个数很小时不再兜底",
        [("return max(int(self.keep_count) * CEILING_FACTOR, CEILING_FLOOR)",
          "return int(self.keep_count) * CEILING_FACTOR")],
        "应导致「保留 1 份时上限仍为 30」用例失败",
    ),
    (
        "guard",
        "上限倍数从 3 改为 2：阈值口径偏移",
        [("CEILING_FACTOR = 3", "CEILING_FACTOR = 2")],
        "应导致「保留 20 份时上限 60」用例失败",
    ),
    (
        "guard",
        "超量判定放宽为「大于等于」：正好等于上限就停",
        [("return _as_int(count) > self.ceiling()",
          "return _as_int(count) >= self.ceiling()")],
        "应导致边界用例（30 份不触发）失败",
    ),
    # ---------------- 状态机 ----------------
    (
        "guard",
        "触发不再幂等：每次运行都重写状态与通知时间",
        [("""        with self._lock:
            if self.state.paused:
                return False""",
          """        with self._lock:
            if not self.state.paused:
                return False""")],
        "应导致「重复触发返回 False」用例失败",
    ),
    (
        "guard",
        "确认「正常」时不再抬升上限：恢复后立刻再次触发",
        [("raised = max(int(target * ACK_GROWTH), target + 1)", "raised = target")],
        "应导致「上限抬到 63 以上」用例失败",
    ),
    (
        "guard",
        "定时变更判定失效：cron 没变也解除",
        [("""        if not current or not self.state.cron or current == self.state.cron:
            return False""",
          """        if not current or not self.state.cron:
            return False""")],
        "应导致「cron 未变不解除」用例失败",
    ),
    (
        "guard",
        "自愈只改标记不真正解除：份数回落仍保持暂停",
        [("""        self.resume(reset_acknowledged=False)
        return True""",
          """        return True""")],
        "应导致「份数回落后自动解除」用例失败",
    ),
    (
        "guard",
        "提醒间隔放宽为严格大于：满 24 小时不提醒",
        [("return (timestamp - float(self.state.notified_at or 0.0)) >= REMIND_INTERVAL",
          "return (timestamp - float(self.state.notified_at or 0.0)) > REMIND_INTERVAL")],
        "应导致「整 24 小时应提醒」用例失败",
    ),
    (
        "guard",
        "状态文件不读盘：重启后暂停丢失",
        [("""            if self.path.exists():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self.state = GuardState.from_dict(raw)""",
          """            if False:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self.state = GuardState.from_dict(raw)""")],
        "应导致「触发后重新加载仍是暂停」用例失败",
    ),
    # ---------------- 运行期闸门 ----------------
    (
        "init",
        "暂停闸门失效：暂停期间照常自动备份",
        [("if not manual and self.__guard_paused():", "if not manual and False:")],
        "应导致「暂停拦截定时备份」用例失败",
    ),
    (
        "init",
        "备份成功后不判数量：兜底永不触发",
        [("""        if success and not manual:
            self.__check_backup_overflow()""",
          """        if False:
            self.__check_backup_overflow()""")],
        "应导致「定时备份成功后触发暂停」用例失败",
    ),
    (
        "init",
        "超量判定被短路：任何份数都判定超限",
        [("""        if not guard.exceeded(count):
            return False""",
          """        if False:
            return False""")],
        "应导致「正好 30 份不触发」用例失败",
    ),
    (
        "init",
        "超量判定不落盘：状态机永不进入暂停",
        [("""        if not guard.trip(count, str(self._cron or "")):
            return False""",
          """        if False:
            return False""")],
        "应导致「超量后 paused 为真」用例失败",
    ),
    (
        "init",
        "暂停期间不节流提醒：每次运行都推送",
        [("""        if not guard or not guard.should_remind():
            return""",
          """        if not guard:
            return""")],
        "应导致「暂停后不发通知」用例失败",
    ),
    (
        "init",
        "手动备份也受闸门限制：救急通道被堵死",
        [("success, msg = self.__backup(manual=True)", "success, msg = self.__backup()")],
        "应导致「手动备份放行」用例失败",
    ),
    # ---------------- 交互 ----------------
    (
        "init",
        "回调不校验插件归属：吃掉其它插件的按钮",
        [("""            if plugin_id and plugin_id != own:
                return""",
          """            if False:
                return""")],
        "应导致「忽略其它插件的回调」用例失败",
    ),
    (
        "init",
        "回调前缀不校验：任意载荷都当成本插件指令",
        [('        if len(parts) != 2 or parts[0] != "cbg":', "        if len(parts) != 2:")],
        "应导致「other|ok 应被忽略」用例失败",
    ),
    (
        "init",
        "通知按钮回调值写错：点了没反应",
        [('"callback_data": f"[PLUGIN]{self.__class__.__name__}|cbg|ok",',
          '"callback_data": f"[PLUGIN]{self.__class__.__name__}|cbg|yes",')],
        "应导致「按钮回调值断言」用例失败",
    ),
    # ---------------- 初始化与清理 ----------------
    (
        "init",
        "初始化不校验兜底状态：改了 cron 也不恢复",
        [("        self.__init_guard()\n\n        if self._onlyonce:",
          "        if self._onlyonce:")],
        "应导致「改过 cron 自动恢复」用例失败",
    ),
    (
        "init",
        "初始化不认「定时已变更」：仍依赖份数自愈",
        [('        if guard.sync_schedule(str(self._cron or "")):',
          "        if False:")],
        "应导致「份数仍超限时靠 cron 变更解除」用例失败",
    ),
    (
        "init",
        "清理硬上限失效：天数保护把份数推到无上限",
        [("over_hard_cap = len(files) > hard_cap", "over_hard_cap = False")],
        "应导致「45 份时清理到 40 份」用例失败",
    ),
    (
        "init",
        "硬上限越过天数保护时仍按天数过滤：止血无效",
        [("        if keep_days > 0 and not over_hard_cap:", "        if keep_days > 0:")],
        "应导致「超硬上限仍清理」用例失败",
    ),
]


def run_tests():
    """跑 ConfigBackup 全部测试，返回 (通过, 摘要)。"""
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover",
         "-s", "configbackup/tests", "-t", "configbackup"],
        cwd=PLUGIN_PARENT, capture_output=True, text=True,
    )
    output = proc.stdout + proc.stderr
    tail = "\n".join(output.strip().splitlines()[-4:])
    return proc.returncode == 0, tail


def _restore():
    """无条件还原全部被改写的源码。"""
    for key, target in TARGETS.items():
        if os.path.exists(BACKUPS[key]):
            shutil.copy(BACKUPS[key], target)


def main():
    for key, target in TARGETS.items():
        shutil.copy(target, BACKUPS[key])
    try:
        return _run()
    finally:
        # 中途异常不还原会让源码永久停在变异态，后续全部假失败
        _restore()


def _run():
    sources = {key: open(path, encoding="utf-8").read() for key, path in TARGETS.items()}
    for key in TARGETS:
        if not os.path.exists(BACKUPS[key]):
            print(f"❌ 缺少备份：{key}")
            return 1

    base_ok, base_tail = run_tests()
    print(f"基线：{'✅ 全部通过' if base_ok else '❌ 基线即失败'}")
    if not base_ok:
        print(base_tail)
        return 1

    caught = escaped = skipped = 0
    escaped_names = []

    print(f"\n{'='*70}\n变异测试（备份数量兜底，共 {len(MUTANTS)} 个变异体）\n{'='*70}")
    for idx, (target_key, name, pairs, expect) in enumerate(MUTANTS, 1):
        source = sources[target_key]
        missing = [old for old, _ in pairs if old not in source]
        if missing:
            skipped += 1
            print(f"[{idx:2d}] ⚠️  跳过（定位失败，{len(missing)} 段未匹配）：{name}")
            continue
        mutated = source
        for old, new in pairs:
            mutated = mutated.replace(old, new, 1)
        with open(TARGETS[target_key], "w", encoding="utf-8") as handle:
            handle.write(mutated)
        ok, tail = run_tests()
        # 无论结果如何先还原，避免下一条变异体叠加上一条的改动
        _restore()
        if ok:
            escaped += 1
            escaped_names.append(name)
            print(f"[{idx:2d}] ❌ 逃逸：{name}\n       （{expect}）")
        else:
            caught += 1
            fails = re.search(r"FAILED \((.*?)\)", tail)
            detail = fails.group(0) if fails else "有失败"
            print(f"[{idx:2d}] ✅ 捕获：{name} → {detail}")

    print(f"\n{'='*70}")
    print(f"结果：捕获 {caught} / 逃逸 {escaped} / 跳过 {skipped} / 合计 {caught + escaped + skipped}")
    if escaped_names:
        print("逃逸清单（对应测试存在盲区，必须补强）：")
        for name in escaped_names:
            print(f"  - {name}")
    if skipped:
        print(f"⚠️ {skipped} 个变异体定位失败（防护未生效！）——按规约等同于失败，必须同步锚点")
    print("已还原原始源码。")
    if escaped_names or skipped:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
