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

# ⑦ 空壳判定去掉物理复核（第 2 级整体失效）
#
# v3.0.5 起第 2 级有两段：①按自身文件清单复核 ②取不到清单时退回扫目录。
# 要让「物理复核」整体失效，必须两段都打掉——任一段保留都仍能拦住误删。
FULLY_REMOVED_OLD = (
    "        own_files: List[str] = []\n"
    "        if cand is not None:\n"
    "            own_files = self._get_seed_files(hash_str, cand)\n"
    "        if own_files:\n"
    "            if any(os.path.exists(p) for p in own_files):\n"
    "                # 自己的文件还在 → 保留（与旧逻辑一致）\n"
    "                return False\n"
    "            # 自己的文件全没了 → 落到下方「确凿无文件」判定\n"
    "        elif cand is not None:\n"
    "            # 取不到清单 → 退回旧的「扫内容目录」逻辑（保守垫）\n"
    "            content_path = str(cand.get(\"path\") or \"\").strip()\n"
    "            if content_path and os.path.exists(content_path):\n"
    "                # 路径存在：目录要确认其中确无文件，文件则直接算「存在」\n"
    "                if os.path.isdir(content_path):\n"
    "                    if self._dir_has_any_file(content_path):\n"
    "                        return False\n"
    "                else:\n"
    "                    return False\n"
)
FULLY_REMOVED_NEW = (
    "        own_files: List[str] = []\n"
    "        if cand is not None:\n"
    "            own_files = self._get_seed_files(hash_str, cand)\n"
    "        if False:\n"
    "            return False\n"
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

# ⑨ 文件级索引：不登记「路径映射」（孤儿文件将无法被双侧识别）
INDEX_REGISTER_OLD = (
    "                    key = (st.st_dev, st.st_ino)\n"
    "                    # 先登记路径映射（不受保护期影响），确保双侧都能被识别到\n"
    "                    paths = ino_paths.setdefault(key, [])\n"
    "                    if fpath not in paths:\n"
    "                        paths.append(fpath)\n"
)
INDEX_REGISTER_NEW = (
    "                    key = (st.st_dev, st.st_ino)\n"
)

# ⑩ 文件级索引：把「只在单侧的孤儿文件」也按保护期过滤（应仍纳入）
INDEX_ORPHAN_FILTER_OLD = (
    "                    if key in seen_ino:\n"
    "                        continue\n"
    "                    if now - st.st_mtime < recent_secs:\n"
    "                        continue\n"
    "                    seen_ino.add(key)\n"
    "                    files.append((st.st_mtime, fpath, st.st_size, key))\n"
)
INDEX_ORPHAN_FILTER_NEW = (
    "                    if key in seen_ino:\n"
    "                        continue\n"
    "                    seen_ino.add(key)\n"
    "                    files.append((st.st_mtime, fpath, st.st_size, key))\n"
)

# ⑪ 文件级索引不跳过 DSM 系统目录（会把 @eaDir 等纳入清理）
INDEX_SKIP_SYS_OLD = (
    "                dirs[:] = [d for d in dirs if not d.startswith(\"@\") and d != \"#recycle\"]\n"
)
INDEX_SKIP_SYS_NEW = (
    "                pass\n"
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
    ("文件级索引不登记路径映射", INDEX_REGISTER_OLD,
     INDEX_REGISTER_NEW, "孤儿文件的路径映射缺失，双侧识别与清理会失效"),
    ("文件级索引把孤儿文件按保护期过滤", INDEX_ORPHAN_FILTER_OLD,
     INDEX_ORPHAN_FILTER_NEW, "保护期过滤不得把合法候选整体丢掉"),
    ("文件级索引不跳过 DSM 系统目录", INDEX_SKIP_SYS_OLD,
     INDEX_SKIP_SYS_NEW, "会把 @eaDir/#recycle 等系统目录纳入清理"),
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
          + (f"，{skipped} 个跳过（防护未生效！）" if skipped else ""))
    if escaped_names:
        print("逃逸清单（需补充测试）：")
        for name in escaped_names:
            print(f"  - {name}")
        return 1
    if skipped:
        # 跳过意味着变异体定义与源码脱节，防护可能已悄悄失效 —— 视为失败
        print("❌ 存在被跳过的变异体：定义与源码不匹配即等于该缺陷无人守护")
        return 1
    print("🎉 全部变异被捕获，测试防护有效")
    return 0


if __name__ == "__main__":
    sys.exit(main())
