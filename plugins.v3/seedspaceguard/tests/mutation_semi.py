#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
变异测试（半残种子专项）：验证「半残种子能否正常清理」的测试防护有效。

背景：用户提问「已被部分删除文件的种子，种子级删除时能否正常运行」。
为此新增了 `test_semideleted_seed.py` 与 `test_semi_e2e.py` 两个测试文件。
本脚本用「替换源码 → 跑测试 → 必须失败」的方式，验证这两个文件**真的**
能拦住半残场景下的缺陷，而不是空过。

覆盖的变异：
  ① 建索引时不再跳过不存在的路径（应对缺失目录不崩）
  ② 索引不做存在性判断，缺失路径直接抛异常（应被捕获）
  ③ 硬链接清理跳过「已不存在」的路径时改判为失败（应被捕获）
  ④ `_delete_one` 去掉 inode 复核（重建文件会被误删）
  ⑤ `_delete_one` 去掉 realpath 边界校验
  ⑥ 关联路径不再补「媒体库侧」来源（孤儿硬链接清不掉）
  ⑦ 空壳判定去掉物理复核（误判已删空）
  ⑧ 清理统计用赋值而非累加（半残多轮场景少报）

用法（在插件目录下）::

    python3 tests/mutation_semi.py
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

# ① 建索引：缺失路径应被静默跳过（改为不捕获 OSError → 直接崩）
INDEX_TOLERANT_OLD = (
    "        def _record(fpath: str) -> None:\n"
    "            try:\n"
    "                st = os.lstat(fpath)\n"
    "            except OSError:\n"
    "                return\n"
)
INDEX_TOLERANT_NEW = (
    "        def _record(fpath: str) -> None:\n"
    "            st = os.lstat(fpath)\n"
)

# ② 索引前不判 isdir：对缺失目录 os.walk 会静默返回 → 改为强制 listdir 崩
INDEX_ISDIR_OLD = (
    "            if os.path.isdir(p):\n"
    "                for root, dirs, fnames in os.walk(p):\n"
)
INDEX_ISDIR_NEW = (
    "            if True:\n"
    "                for root, dirs, fnames in os.walk(p):\n"
)

# ③ 硬链接清理：跳过不存在路径改判为计数（应导致 removed 虚高 / 语义错）
LINKS_SKIP_OLD = (
    "                if not os.path.exists(path):\n"
    "                    # 下载侧已被下载器删除，属预期\n"
    "                    continue\n"
)
LINKS_SKIP_NEW = (
    "                if not os.path.exists(path):\n"
    "                    removed += 1\n"
    "                    continue\n"
)

# ④ _delete_one 去掉 inode 复核（重建文件会被误删）
DELETE_INODE_OLD = (
    "            if (st.st_dev, st.st_ino) != key:\n"
    "                logger.warning(\"【保种空间守护】inode 已变化，跳过（文件可能被重建）：%s\", path)\n"
    "                continue\n"
)
DELETE_INODE_NEW = (
    "            pass\n"
)

# ⑤ _delete_one 去掉 realpath 越界校验
DELETE_REALPATH_OLD = (
    "            if not self._path_under_any(os.path.realpath(path)):\n"
    "                logger.warning(\"【保种空间守护】realpath 越界，跳过：%s\", path)\n"
    "                continue\n"
)
DELETE_REALPATH_NEW = (
    "            pass\n"
)

# ⑥ 关联路径不再补「媒体库侧」（孤儿硬链接清不掉）
RELATED_LIB_OLD = (
    "        title = str(cand.get(\"title\") or \"\").strip()\n"
    "        if title:\n"
    "            for base in (self._active_dirs or self._target_dirs):\n"
    "                _add(os.path.join(base, title))\n"
)
RELATED_LIB_NEW = (
    "        pass\n"
)

# ⑦ 空壳判定去掉物理复核（第 2 级）
FULLY_REMOVED_OLD = (
    "        if cand is not None:\n"
    "            content_path = str(cand.get(\"path\") or \"\").strip()\n"
    "            if content_path and os.path.exists(content_path):\n"
)
FULLY_REMOVED_NEW = (
    "        if False:\n"
    "            content_path = str(cand.get(\"path\") or \"\").strip()\n"
    "            if content_path and os.path.exists(content_path):\n"
)

# ⑧ 统计用赋值而非累加（半残多轮场景少报）
STATS_ACC_OLD = (
    "        self._clean_stats[\"seeds\"] = (\n"
    "            (self._clean_stats.get(\"seeds\") or 0) + deleted\n"
    "        )\n"
    "        return deleted, round(released_gb, 1), detail_lines\n"
)
STATS_ACC_NEW = (
    "        self._clean_stats[\"seeds\"] = deleted\n"
    "        return deleted, round(released_gb, 1), detail_lines\n"
)


MUTANTS = [
    ("索引不捕获 lstat 异常（缺失路径直接崩）", INDEX_TOLERANT_OLD,
     INDEX_TOLERANT_NEW, "缺失路径应被静默跳过，不得抛异常"),
    ("索引不判 isdir（对文件也走 walk）", INDEX_ISDIR_OLD,
     INDEX_ISDIR_NEW, "应区分文件与目录，避免语义混淆"),
    ("硬链接清理把「已不存在」计为已删", LINKS_SKIP_OLD,
     LINKS_SKIP_NEW, "已不存在的路径属预期跳过，不应计入删除条数"),
    ("_delete_one 去掉 inode 复核", DELETE_INODE_OLD,
     DELETE_INODE_NEW, "inode 变化时必须拒绝删除（防误删重建的新文件）"),
    ("_delete_one 去掉 realpath 边界校验", DELETE_REALPATH_OLD,
     DELETE_REALPATH_NEW, "realpath 越界必须拒绝（防链接逃逸）"),
    ("关联路径不补媒体库侧", RELATED_LIB_OLD,
     RELATED_LIB_NEW, "缺了媒体库侧来源，孤儿硬链接清不掉、空间不释放"),
    ("空壳判定去掉物理复核", FULLY_REMOVED_OLD,
     FULLY_REMOVED_NEW, "仅靠失效的记录会误判「已删空」而误删完好种子"),
    ("清理统计用赋值而非累加", STATS_ACC_OLD,
     STATS_ACC_NEW, "多轮/空壳回收场景会少报删除数"),
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
    print("🎉 全部变异被捕获，测试防护有效")
    return 0


if __name__ == "__main__":
    sys.exit(main())
