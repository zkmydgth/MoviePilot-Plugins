#!/usr/bin/env python3
"""
变异测试：往核心逻辑里注入缺陷，要求每一条都被现有测试捕获。

按本仓库约定：**跳过即失败** —— 变异锚点与源码失配时说明该变异体守护的
缺陷此刻已无人看守，脚本必须报红，不能静默放过。

用法（插件目录下）：:

    python tests/mutation_core.py
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent

# (文件, 原片段, 变异片段, 说明)
MUTANTS = [
    (
        "core/config.py",
        'return SIGN_TYPE_COMMAND if "命令" in value else SIGN_TYPE_BUTTON',
        "return SIGN_TYPE_BUTTON",
        "命令别名失效：所有方式都被当成按钮",
    ),
    (
        "core/config.py",
        "        wait_seconds = 15\n        if len(fields) > 4 and fields[4]:",
        "        wait_seconds = 5\n        if len(fields) > 4 and fields[4]:",
        "等待秒数默认值被改小",
    ),
    (
        "core/config.py",
        'if not bot_username.startswith("@"):',
        "if False:",
        "bot 用户名不再补 @",
    ),
    (
        "core/config.py",
        "        key = f\"acc{index}\"",
        "        key = \"\"",
        "槽位账号标识不再自动生成（账号列表变空）",
    ),
    (
        "core/config.py",
        "        if action not in (LOGIN_ACTION_SEND, LOGIN_ACTION_CONFIRM):",
        "        if False:",
        "「不操作」也被当成待执行登录动作",
    ),
    (
        "core/config.py",
        "        if len(fields) < 4:\n            continue",
        "        if len(fields) < 3:\n            continue",
        "字段数下限放宽，残缺行被误收",
    ),
    (
        "core/signin.py",
        '            result["ok"] = clicked',
        '            result["ok"] = True',
        "按钮没点到也判成功",
    ),
    (
        "core/signin.py",
        "            result[\"ok\"] = bool(reply)",
        '            result["ok"] = True',
        "命令式没回复也判成功",
    ),
    (
        "core/store.py",
        '    state["history"] = list(reversed(keep))',
        "    state[\"history\"] = list(keep)",
        "历史顺序反了（最近结果排序失效）",
    ),
    (
        "core/login.py",
        "    if not phone_code_hash:",
        "    if False:",
        "未发码也能进入确认登录",
    ),
    (
        "core/login.py",
        "    if not code:",
        "    if False:",
        "空验证码不再拦截",
    ),
    (
        "core/login.py",
        "    if time.time() - float(pending.get(\"ts\") or 0) > PENDING_TTL_SECONDS:",
        "    if False:",
        "过期验证码不再失效",
    ),
]


def run_suite(workdir: Path) -> int:
    """
    在变异后的副本里跑全部单测。

    :param workdir: 变异副本目录
    :return int: 子进程退出码（非 0 表示有测试失败 = 变异被捕获）
    """

    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode


def main() -> int:
    """
    逐个注入变异并判定捕获情况。

    :return int: 0 = 全部捕获；1 = 存在逃逸或跳过
    """

    caught = 0
    escaped = 0
    skipped = 0
    for relative, old, new, desc in MUTANTS:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "tgsignin"
            shutil.copytree(
                PLUGIN_DIR,
                work,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
            )
            target = work / relative
            text = target.read_text(encoding="utf-8")
            if old not in text:
                print(f"SKIP      {desc}（锚点失配：防护未生效！）")
                skipped += 1
                continue
            target.write_text(text.replace(old, new, 1), encoding="utf-8")
            code = run_suite(work)
            if code != 0:
                print(f"CAUGHT    {desc}")
                caught += 1
            else:
                print(f"ESCAPED   {desc}")
                escaped += 1

    total = len(MUTANTS)
    print("-" * 60)
    print(f"变异体 {total}：捕获 {caught} / 逃逸 {escaped} / 跳过 {skipped}")
    if escaped or skipped:
        print("有逃逸或有跳过（防护未生效！）")
        return 1
    print("全部捕获，0 逃逸 0 跳过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
