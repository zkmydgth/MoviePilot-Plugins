#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
变异测试：「接管网盘整理」补丁的 durable 收口（工单 F）。

为什么必须做
------------
2026-10-09 实测病根：补丁 ``_patched_handle_transfer`` 复制的是宿主旧版规划前流程，
失败/提前返回时既不提交 durable 终态、也不注销宿主准入。宿主
``__claim_recovery_batch`` 因此认定任务未结算，**每 ~15 秒把同一文件重投一次**
（实测 18:45–18:47 每轮新增/更新一条失败历史；copy 模式下源文件不消失，永不自愈）。

``tests/test_transfer_patch_contract.py::TestDurableSettlementContract`` 是唯一守着
「每条提前/失败返回路径都必须收口」的东西。若断言写松（例如只看返回值、不看宿主是否
真被调用），把收口整段删掉照样全绿，用户侧才会以「整理队列无限回放」的形式发现。

本脚本逐个拆掉收口逻辑的关键判断，验证测试确实会失败。

⚠️ 铁律（主记忆 §3.3）：变异体「跳过」必须等同于失败 —— 片段失配时源码根本没被改动，
不报错就等于假防护。收尾统一 ``return 1 if escaped_names or skipped else 0``，
并打印「N 个跳过（防护未生效！）」。

⚠️ 规约：变异一步一跑（单独命令）、每步带 timeout、跑完立刻 ``sha256sum`` 核对源码哈希；
本脚本运行期间**禁止编辑源码**（会撞出假 flaky）。

运行
----
    cd plugins.v3/p115strmhelper
    PYTHONPATH="$PWD/tests/_stub_host:/opt/venv/lib/python3.14/site-packages" \
        python3 tests/mutation_transfer_settlement.py

人为制造失配自检（必须变红才能证明收口有效）::

    P115_MUTATION_SELFTEST=1 python3 tests/mutation_transfer_settlement.py; echo "exit=$?"  # 期望 1

无桩宿主时会整体跳过（此时基线即失败，脚本直接报红，不会假装通过）。
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(HERE)

PATCH_TARGET = os.path.join(PLUGIN_DIR, "patch", "transfer_chain.py")
COMPAT_TARGET = os.path.join(PLUGIN_DIR, "utils", "transfer_compat.py")

BACKUPS = {
    PATCH_TARGET: "/tmp/p115_mut_settlement_patch_backup.py",
    COMPAT_TARGET: "/tmp/p115_mut_settlement_compat_backup.py",
}

#: 单跑目标：只跑收口契约回归，避免整仓测试把单次变异时间拉长到超时。
TEST_TARGET = "tests/test_transfer_patch_contract.py"

PYTHON = os.environ.get("P115_TEST_PYTHON", sys.executable)


def _pythonpath() -> str:
    """
    组装子进程 PYTHONPATH：桩宿主优先，其次插件根目录，最后补三方依赖。

    :return str: ``os.pathsep`` 连接后的 PYTHONPATH
    """
    entries = [os.path.join(HERE, "_stub_host"), PLUGIN_DIR]
    extra = os.environ.get("P115_EXTRA_PYTHONPATH")
    if extra is None:
        candidate = "/opt/venv/lib/python3.14/site-packages"
        extra = candidate if os.path.isdir(candidate) else ""
    if extra:
        entries.append(extra)
    return os.pathsep.join(entries)


# (目标文件, 变异体名称, 原文, 变异后, 期望被捕获的说明)
MUTANTS = [
    # ============ 一、patch/transfer_chain.py：收口调用点 ============
    (
        PATCH_TARGET,
        "规划拒绝不再调用宿主收口（未识别/缺集数任务悬空 → 每 ~15 秒回放）",
        "        result = checkpoint_planning_rejection(chain_self, task, message, callback)",
        "        result = None",
        "应导致 test_unrecognized_media_is_settled_by_host_rejection / "
        "test_missing_episode_is_settled_by_host_rejection 失败",
    ),
    (
        PATCH_TARGET,
        "规划拒绝不交回宿主 callback（durable 终态结算被吞掉）",
        """                    settled = cls._settle_planning_rejection(
                        chain_self, task, callback, "未识别到媒体信息"
                    )""",
        """                    settled = cls._settle_planning_rejection(
                        chain_self, task, None, "未识别到媒体信息"
                    )""",
        "应导致 test_unrecognized_media_returns_through_callback 失败",
    ),
    (
        PATCH_TARGET,
        "接管路径不注销宿主准入（同一文件被恢复调度反复接管、重复整理）",
        """                if not abandon_taken_over_admission(
                    chain_self, task, reason=f"插件接管 115→115 整理：{target_path}"
                ):
                    return cls._call_original(chain_self, task, callback)
""",
        "",
        "应导致 test_take_over_abandons_host_admission 与静态守卫 "
        "test_take_over_paths_abandon_host_admission 失败",
    ),
    (
        PATCH_TARGET,
        "重复投递不登记 checkpoint 前失败原因（恢复调度只看到无因的 accepted）",
        """                    record_uncheckpointed_failure(
                        chain_self, task, f"{task.fileitem.name} 已在整理队列中"
                    )""",
        "                    pass",
        "应导致 test_duplicate_delivery_records_uncheckpointed_failure 失败",
    ),
    (
        PATCH_TARGET,
        "已冻结计划的恢复任务被插件重新接管（与 durable 计划漂移）",
        '            if getattr(task, "plan_checkpoint", None) is not None:',
        "            if False:",
        "应导致 test_planned_task_is_delegated_to_host 失败",
    ),
    (
        PATCH_TARGET,
        "不再校验宿主 durable 收口入口（宿主改名后静默假接管）",
        "        for name in _DURABLE_SETTLEMENT_TARGETS:",
        "        for name in ():",
        "应导致静态守卫 test_verifies_durable_settlement_targets 失败",
    ),
    (
        PATCH_TARGET,
        "旧版「只调 transfer 片段」回退复活（回调因缺执行检查点必然抛错）",
        """        if cls._original_handle_transfer:
            return cls._original_handle_transfer(chain_self, task, callback)
        return None""",
        "        return cls._call_original_transfer_part(chain_self, task, callback)",
        "应导致 test_no_legacy_transfer_part_helper 失败（运行期也会 AttributeError）",
    ),
    # ============ 二、utils/transfer_compat.py：收口封装 ============
    (
        COMPAT_TARGET,
        "收口封装不交回 callback（只返回布尔，宿主拿不到终态）",
        """    if callback:
        return callback(task, transferinfo)
    return bool(transferinfo.success), transferinfo.message or \"\"""",
        """    return bool(transferinfo.success), transferinfo.message or \"\"""",
        "应导致 test_unrecognized_media_returns_through_callback 失败",
    ),
    (
        COMPAT_TARGET,
        "准入注销门禁放宽：缺 durable 身份也报「已注销」（接管后登记永远摘不掉）",
        """    if not task_id or not lease_token:
        logger.warning(f"【整理接管】整理任务缺少 durable 身份，无法注销宿主准入：{reason}")
        return False""",
        """    if not task_id or not lease_token:
        logger.warning(f"【整理接管】整理任务缺少 durable 身份，无法注销宿主准入：{reason}")
        return True""",
        "应导致 test_take_over_falls_back_without_durable_identity 失败",
    ),
]

#: 人为制造失配用：锚点必然不存在，用于证明「跳过 = 失败」。
SELFTEST_MUTANT = (
    PATCH_TARGET,
    "【自检】锚点不存在，必须被判为跳过并让脚本变红",
    "    def this_anchor_never_exists_in_source(self):",
    "    def mutated(self):",
    "自检用，若脚本仍返回 0 说明「跳过」没有等同于失败",
)


def run_tests():
    """
    跑收口契约回归测试。

    :return: ``(是否通过, 末行摘要)``
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = _pythonpath()
    proc = subprocess.run(
        [PYTHON, "-m", "pytest", TEST_TARGET, "-q"],
        cwd=PLUGIN_DIR,
        capture_output=True,
        text=True,
        env=env,
    )
    output = proc.stdout + proc.stderr
    tail = output.strip().splitlines()[-1] if output.strip() else "(无输出)"
    return proc.returncode == 0, tail


def main():
    for target, backup in BACKUPS.items():
        shutil.copy(target, backup)
    try:
        return _run()
    finally:
        # 无条件还原：本脚本直接覆写源码，中途异常若不还原，
        # 源码会永久停留在变异态，后续测试全在污染代码上跑。
        for target, backup in BACKUPS.items():
            shutil.copy(backup, target)


def _run():
    mutants = list(MUTANTS)
    if os.environ.get("P115_MUTATION_SELFTEST") == "1":
        mutants.append(SELFTEST_MUTANT)

    base_ok, base_tail = run_tests()
    print(f"解释器：{PYTHON}")
    print(f"目标：{TEST_TARGET}")
    print(f"基线：{'✅ 全部通过' if base_ok else '❌ 基线即失败'}")
    if not base_ok:
        print(base_tail)
        return 1

    sources = {
        target: Path(backup).read_text(encoding="utf-8")
        for target, backup in BACKUPS.items()
    }

    caught = escaped = skipped = 0
    escaped_names = []
    skipped_names = []

    print(f"\n{'=' * 72}")
    print(f"变异测试（接管整理的 durable 收口，共 {len(mutants)} 个变异体）")
    print(f"{'=' * 72}")

    for idx, (target, name, old, new, expect) in enumerate(mutants, 1):
        source = sources[target]
        if old not in source:
            skipped += 1
            skipped_names.append(name)
            print(f"[{idx:>2}] ⚠️  跳过（防护未生效！）：源码中未找到锚点 —— {name}")
            continue

        with open(target, "w", encoding="utf-8") as handle:
            handle.write(source.replace(old, new, 1))

        ok, tail = run_tests()
        if ok:
            escaped += 1
            escaped_names.append(name)
            print(f"[{idx:>2}] ❌ 逃逸：{name}")
            print(f"       → 变异体全绿，说明测试没守住！{expect}")
        else:
            caught += 1
            print(f"[{idx:>2}] ✅ 捕获：{name}")
            print(f"       → {tail}")

        # 单个变异体跑完立即还原，避免影响下一个
        shutil.copy(BACKUPS[target], target)

    print(f"\n{'=' * 72}")
    print(
        f"结果：捕获 {caught} / 逃逸 {escaped} / 跳过 {skipped} / 合计 {len(mutants)}"
    )
    if escaped_names:
        print("\n逃逸明细（必须补测试）：")
        for name in escaped_names:
            print(f"  - {name}")
    if skipped_names:
        print(f"\n⚠️  {skipped} 个跳过（防护未生效！锚点与源码失配，必须同步修正）：")
        for name in skipped_names:
            print(f"  - {name}")
    print("已还原原始源码。")
    return 1 if (escaped_names or skipped) else 0


if __name__ == "__main__":
    sys.exit(main())
