#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
变异测试：「分享STRM清理 → 缺失媒体」行的旧键兼容（2026-10-09）。

为什么必须做
------------
后端只输出 ``media_source`` / ``media_id``，而前端仍按 ``tmdbid`` / ``tvdbid`` /
``imdbid`` / ``doubanid`` 判断渲染；补键逻辑一旦写错（方向错、漏补空键、只补部分
路径），单测若写得松就会全绿，用户侧表现为「那栏一片空白」——不报错、不崩，最难发现。

``tests/test_share_strm_missing_media_compat.py`` 是唯一守着这层兼容的东西，本脚本
逐个拆掉它的前提，验证测试确实会失败。

⚠️ 铁律（主记忆 §3.3）：变异体「跳过」必须等同于失败 —— 片段失配时源码根本没被改动，
不报错就等于假防护。收尾统一 ``return 1 if escaped_names or skipped else 0``。

⚠️ 规约：变异一步一跑（单独命令）、每步带 timeout、跑完立刻 ``sha256sum`` 核对源码哈希；
本脚本运行期间**禁止编辑源码**。

运行
----
    cd plugins.v3/p115strmhelper
    PYTHONPATH="$PWD/tests/_stub_host:/opt/venv/lib/python3.14/site-packages" \\
        python3 tests/mutation_missing_media_compat.py

人为制造失配自检（必须变红）::

    P115_MUTATION_SELFTEST=1 python3 tests/mutation_missing_media_compat.py; echo "exit=$?"  # 期望 1
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(HERE)

CLEANER_TARGET = os.path.join(PLUGIN_DIR, "helper", "strm", "share", "cleaner.py")
STUB_TYPES_TARGET = os.path.join(
    HERE, "_stub_host", "app", "schemas", "types.py"
)

BACKUPS = {
    CLEANER_TARGET: "/tmp/p115_mut_missing_media_cleaner_backup.py",
    STUB_TYPES_TARGET: "/tmp/p115_mut_missing_media_stub_types_backup.py",
}

#: 单跑目标：只跑本修复的契约回归。
TEST_TARGET = "tests/test_share_strm_missing_media_compat.py"

PYTHON = os.environ.get("P115_TEST_PYTHON", sys.executable)


def _pythonpath():
    """
    组装子进程 PYTHONPATH：桩宿主优先，其次插件根目录，最后补三方依赖

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
    (
        CLEANER_TARGET,
        "反填方向错：themoviedb 填到 doubanid（前端 TMDB 栏仍空）",
        '    "themoviedb": "tmdbid",',
        '    "themoviedb": "doubanid",',
        "应导致 test_themoviedb_fills_tmdbid_only / test_row_outputs_legacy_keys 失败",
    ),
    (
        CLEANER_TARGET,
        "不再补空旧键：只填命中来源的那一个（前端读不到其余三个键）",
        """    for key in _LEGACY_MEDIA_ID_KEYS.values():
        out.setdefault(key, None)
""",
        "",
        "应导致 test_unknown_source_leaves_all_legacy_keys_empty / "
        "test_row_without_source_does_not_raise 失败",
    ),
    (
        CLEANER_TARGET,
        "直接改入参：不复制行，污染已落盘/上游持有的字典",
        "    out = dict(row)",
        "    out = row",
        "应导致 test_input_is_not_mutated 失败",
    ),
    (
        CLEANER_TARGET,
        "media_id 为空也反填：凭空给出媒体 ID",
        "    if legacy_key and media_id:",
        "    if legacy_key:",
        "应导致 test_missing_media_id_keeps_legacy_keys_empty 失败",
    ),
    (
        CLEANER_TARGET,
        "行构造不再补键：新写入的记录只带新键（前端立刻空栏）",
        "        # 兼容旧前端：按 media_source 反填 tmdbid / tvdbid / imdbid / doubanid\n"
        "        return with_legacy_media_id_keys(base)",
        "        return base",
        "应导致 TestRowFromTransferHistory 两项失败",
    ),
    (
        CLEANER_TARGET,
        "读取侧不再补键：补键前落盘的历史记录仍无法渲染",
        "        items, total = self._store.page(page, limit)\n"
        "        # 落盘数据可能来自补兼容键之前的版本，读取时再补一次\n"
        "        return [with_legacy_media_id_keys(item) for item in items], total",
        "        return self._store.page(page, limit)",
        "应导致 test_page_backfills_legacy_keys 失败",
    ),
    (
        STUB_TYPES_TARGET,
        "桩宿主 MediaSource.TMDB 改回错值 \"tmdb\"（与真机 \"themoviedb\" 不一致）",
        '    TMDB = "themoviedb"',
        '    TMDB = "tmdb"',
        "应导致 TestHostContractFidelity::test_stub_media_source_matches_real_host_values 失败",
    ),
]

#: 人为制造失配用：锚点必然不存在，用于证明「跳过 = 失败」。
SELFTEST_MUTANT = (
    CLEANER_TARGET,
    "【自检】锚点不存在，必须被判为跳过并让脚本变红",
    "    def this_anchor_never_exists_in_source(self):",
    "    def mutated(self):",
    "自检用，若脚本仍返回 0 说明「跳过」没有等同于失败",
)


def run_tests():
    """
    跑本修复的契约回归测试

    :return tuple: ``(是否通过, 末行摘要)``
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
    print(f"变异测试（缺失媒体旧键兼容，共 {len(mutants)} 个变异体）")
    print(f"{'=' * 72}")

    for idx, (target, name, old, new, expect) in enumerate(mutants, 1):
        source = sources[target]
        if old not in source:
            skipped += 1
            skipped_names.append(name)
            print(f"[{idx:>2}] ⚠️  跳过（防护未生效！）：源码中未找到锚点 —— {name}")
            continue

        Path(target).write_text(source.replace(old, new, 1), encoding="utf-8")

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
