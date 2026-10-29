#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
变异测试（v3.1.0「保护目录」专项）。

每个变异体把一条保护判定「改回去」，必须至少让 tests/test_protect_dirs.py
中的一个用例失败；否则说明该判定没有测试守护。

为控制耗时，本脚本只跑「保护目录」测试模块（其余测试已由各自变异脚本覆盖）。

用法（在插件目录下）::

    python3 tests/mutation_protect_dirs.py
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(HERE)
TARGET = os.path.join(PLUGIN_DIR, "__init__.py")
TESTS = "tests/test_protect_dirs.py"


MUTANTS = [
    (
        "P1 保护目录接受根目录（等于全盘保护却不报错）",
        """            if norm == os.sep:
                logger.error(
                    "【保种空间守护】拒绝把根目录作为保护目录（会等于全盘保护）：%s",
                    path,
                )
                continue""",
        """            if False:
                continue""",
        "应导致 test_parse_protect_dirs 失败（根目录未被过滤）",
    ),
    (
        "P2 路径保护判定恒为假（所有链路失去保护）",
        """        if not path:
            return False
        return any(self._path_under(path, d) for d in self._protect_dirs)""",
        """        if not path:
            return False
        return False""",
        "应导致候选索引 / 删除 / 收尾 / 空壳多组用例失败",
    ),
    (
        "P3 「目录内部含保护目录」判定恒为假（整目录删除会连带删保护内容）",
        """        norm = os.path.normpath(path)
        return any(self._path_under(d, norm) for d in self._protect_dirs)""",
        """        norm = os.path.normpath(path)
        return False""",
        "应导致 test_contains_protected / test_is_protected_seed / "
        "test_seed_with_protected_subdir_excluded 失败",
    ),
    (
        "P4 种子只判「内容在保护目录内」，不再判「内容目录包含保护目录」",
        """        return self._is_protected(path) or self._contains_protected(path)""",
        """        return self._is_protected(path)""",
        "应导致 test_is_protected_seed 失败",
    ),
    (
        "P5 种子候选不再排除保护目录（种子级会删除受保护资源）",
        """                if self._is_protected_seed(cand):
                    counted["protected"] += 1""",
        """                if False:
                    counted["protected"] += 1""",
        "应导致 test_protected_seed_excluded_from_both_scopes / "
        "test_seed_with_protected_subdir_excluded 失败",
    ),
    (
        "P6 保护目录内的文件被纳入清理候选",
        """                    if self._is_protected(fpath):
                        continue""",
        """                    if False:
                        continue""",
        "应导致 test_protected_dir_as_target_root_not_indexed 失败",
    ),
    (
        "P7 保护目录内的 inode 不再整组剔除（只删另一侧硬链接）",
        """        if protected_inodes and files:
            kept = [item for item in files if item[3] not in protected_inodes]""",
        """        if False and files:
            kept = [item for item in files if item[3] not in protected_inodes]""",
        "应导致 test_hardlink_group_skipped_when_any_link_protected 失败",
    ),
    (
        "P8 保护目录内的 inode 不再被收集",
        """        for base in self._protect_dirs:
            if not os.path.isdir(base):""",
        """        for base in []:
            if not os.path.isdir(base):""",
        "应导致 test_hardlink_group_skipped_when_any_link_protected 失败",
    ),
    (
        "P9 单文件删除不再拦截保护目录（硬链接清理会误删）",
        """            if self._is_protected(path):
                logger.warning("【保种空间守护】跳过保护目录内的路径：%s", path)
                continue""",
        """            if False:
                continue""",
        "应导致 test_delete_one_refuses_protected_path / "
        "test_clean_hardlinks_refuses_protected 失败",
    ),
    (
        "P10 inode 索引整体不再排除保护目录（种子级删种会清掉保护路径）",
        """            if not p or not self._path_under_any(p) or self._is_protected(p):
                continue
            if os.path.isdir(p):
                for root, dirs, fnames in os.walk(p):
                    # 与 _index_files 一致：跳过 DSM 系统目录与回收站
                    # 以及保护目录：不递归进去，其内部路径不会进入待删清单
                    dirs[:] = [
                        d for d in dirs
                        if not d.startswith("@") and d != "#recycle"
                        and not self._is_protected(os.path.join(root, d))
                    ]
                    for fname in fnames:
                        fpath = os.path.join(root, fname)
                        if self._is_protected(fpath):
                            continue
                        _record(fpath)""",
        """            if not p or not self._path_under_any(p):
                continue
            if os.path.isdir(p):
                for root, dirs, fnames in os.walk(p):
                    # 与 _index_files 一致：跳过 DSM 系统目录与回收站
                    dirs[:] = [
                        d for d in dirs
                        if not d.startswith("@") and d != "#recycle"
                    ]
                    for fname in fnames:
                        _record(os.path.join(root, fname))""",
        "应导致 test_build_inode_index_skips_protected / "
        "test_build_inode_index_skips_nested_protected 失败",
    ),
    (
        "P11 空目录收尾不再识别保护目录（会 rmdir 受保护目录）",
        """        if self._is_protected(start):
            # 保护目录及其内部不允许被改动：既不清理索引残片，也不尝试 rmdir
            return
        current = start
        while current and current != owner and current.startswith(owner + os.sep):
            if self._is_protected(current):
                # 向上收尾遇到保护目录即停：删除它会违背用户显式保留的要求
                break""",
        """        current = start
        while current and current != owner and current.startswith(owner + os.sep):""",
        "应导致 test_prune_returns_for_protected_start 失败",
    ),
    (
        "P12 空壳巡检不再把保护目录视为内容（保护目录会被清空）",
        """                    if self._is_protected(entry.path):
                        # 保护目录及其内容不参与空壳判定：视为「有内容」，
                        # 连它的父目录也不会被当成空壳删掉
                        empty = False
                        continue""",
        """                    if False:
                        continue""",
        "应导致 test_sweep_keeps_protected_shell_and_parent 失败",
    ),
]


def run_tests():
    """跑「保护目录」测试模块，返回 (是否全过, 摘要)。"""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", TESTS, "-q", "--no-header", "-p",
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
            if original.count(old) != 1:
                print(f"[{idx:2d}] ⚠️  跳过（锚点匹配 {original.count(old)} 次）：{name}")
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
          + (f"，{skipped} 个跳过（防护未生效！）" if skipped else ""))
    if escaped_names:
        print("逃逸清单（需补充测试）：")
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
