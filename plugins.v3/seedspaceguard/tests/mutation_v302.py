#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
变异测试（v3.0.2 专项）：验证「种子级连带清理」的测试防护有效。

覆盖：
- 索引顺序约束（删种前建索引）
- 硬链接清理
- 辅种连带删除
- 合集多集保护（同目录不同种子名不得连带）
- 监控目录外种子保护
- 开关默认值语义

用法（在插件目录下）::

    python3 tests/mutation_v302.py
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(HERE)
TARGET = os.path.join(PLUGIN_DIR, "__init__.py")


# ---------------------------------------------------------------------------
# 源码片段定义（用拼接构造，避免三引号嵌套问题）
# ---------------------------------------------------------------------------

# ① 索引建立（删种前）
BUILD_INDEX_OLD = (
    "                ino_index: Dict[Tuple[int, int], List[str]] = {}\n"
    "                attr_inodes: Set[Tuple[int, int]] = set()\n"
    "                if self._companion_cleanup:\n"
    "                    related = self._seed_related_paths(cand)\n"
)
BUILD_INDEX_NEW_EMPTY = (
    "                ino_index: Dict[Tuple[int, int], List[str]] = {}\n"
    "                attr_inodes: Set[Tuple[int, int]] = set()\n"
)

# ② 硬链接清理调用
CLEAN_LINK_OLD = (
    "                if self._companion_cleanup and ino_index:\n"
    "                    link_removed = self._clean_hardlinks_for(\n"
    "                        ino_index, cand[\"title\"], attr_inodes, related\n"
    "                    )\n"
)
CLEAN_LINK_NEW_SKIP = (
    "                if False and ino_index:\n"
    "                    link_removed = self._clean_hardlinks_for(\n"
    "                        ino_index, cand[\"title\"], attr_inodes, related\n"
    "                    )\n"
)

# ③ 辅种连带调用
COMPANION_OLD = (
    "                if self._companion_cleanup and companion_map:\n"
    "                    peers = [\n"
    "                        h for h in companion_map.get(\n"
    "                            self._content_key(cand), []) if h != cand[\"hash\"]\n"
    "                    ]\n"
)
COMPANION_NEW_SKIP = (
    "                if False and companion_map:\n"
    "                    peers = [\n"
    "                        h for h in companion_map.get(\n"
    "                            self._content_key(cand), []) if h != cand[\"hash\"]\n"
    "                    ]\n"
)

# ④ 内容键：只用路径（丢掉种子名）→ 会把合集多集误判为辅种
CONTENT_KEY_OLD = (
    "        path = os.path.normpath(str(cand.get(\"path\") or \"\"))\n"
    "        title = str(cand.get(\"title\") or \"\").strip().casefold()\n"
    "        return path, title"
)
CONTENT_KEY_NEW_PATH_ONLY = (
    "        path = os.path.normpath(str(cand.get(\"path\") or \"\"))\n"
    "        title = \"\"\n"
    "        return path, title"
)
# 反向：只用种子名（丢掉路径）
CONTENT_KEY_NEW_TITLE_ONLY = (
    "        path = \"\"\n"
    "        title = str(cand.get(\"title\") or \"\").strip().casefold()\n"
    "        return path, title"
)

# ⑤ 关联路径：不补媒体库侧（去掉「配置目录 + 种子名」推断）
RELATED_OLD = (
    "        # ② 各配置目录 + 种子名：媒体库侧硬链接的落点\n"
    "        title = str(cand.get(\"title\") or \"\").strip()\n"
    "        if title:\n"
    "            for base in (self._active_dirs or self._target_dirs):\n"
    "                _add(os.path.join(base, title))\n"
)
RELATED_NEW_SKIP = (
    "        # ② 各配置目录 + 种子名：媒体库侧硬链接的落点\n"
    "        title = str(cand.get(\"title\") or \"\").strip()\n"
    "        if False and title:\n"
    "            for base in (self._active_dirs or self._target_dirs):\n"
    "                _add(os.path.join(base, title))\n"
)

# ⑥ 开关默认值：改为「默认关」（config.get 无默认 → None → 关）
DEFAULT_OLD = (
    "        self._companion_cleanup = config.get(\"companion_cleanup\", True) is not False"
)
DEFAULT_NEW_BOOL = (
    "        self._companion_cleanup = bool(config.get(\"companion_cleanup\"))"
)

# ⑦ 索引越界保护：允许纳入配置目录外路径
INODE_SCOPE_OLD = (
    "        for p in paths:\n"
    "            if not p or not self._path_under_any(p):\n"
    "                continue\n"
    "            if os.path.isdir(p):"
)
INODE_SCOPE_NEW_NOCHECK = (
    "        for p in paths:\n"
    "            if not p:\n"
    "                continue\n"
    "            if os.path.isdir(p):"
)

# ⑧ 硬链接清理时不做存在性判断（尝试删掉已消失的下载侧，无副作用但削弱语义）
CLEAN_LINK_BODY_OLD = (
    "        for key, plist in ino_index.items():\n"
    "            for path in plist:\n"
    "                if not os.path.exists(path):\n"
    "                    # 下载侧已被下载器删除，属预期\n"
    "                    continue\n"
    "                removed += self._delete_one(path, key)\n"
)
CLEAN_LINK_BODY_NEW = (
    "        for key, plist in ino_index.items():\n"
    "            for path in plist:\n"
    "                removed += self._delete_one(path, key)\n"
)


MUTANTS = [
    (
        "顺序回归：删种前不建索引（索引为空 → 硬链接漏删）",
        BUILD_INDEX_OLD,
        BUILD_INDEX_NEW_EMPTY,
        "应导致 test_hardlinks_removed_after_seed_deleted / "
        "test_index_built_before_torrent_removal 失败",
    ),
    (
        "硬链接清理被跳过（空间不释放的能力回归）",
        CLEAN_LINK_OLD,
        CLEAN_LINK_NEW_SKIP,
        "应导致 test_hardlinks_removed_after_seed_deleted 失败",
    ),
    (
        "辅种连带被跳过",
        COMPANION_OLD,
        COMPANION_NEW_SKIP,
        "应导致 test_companion_seeds_removed 失败",
    ),
    (
        "内容键只用路径（丢掉种子名）→ 合集多集被误判为辅种",
        CONTENT_KEY_OLD,
        CONTENT_KEY_NEW_PATH_ONLY,
        "应导致 test_multi_episode_torrents_not_treated_as_companions 失败"
        "（最高危缺陷）",
    ),
    (
        "内容键只用种子名（丢掉路径）",
        CONTENT_KEY_OLD,
        CONTENT_KEY_NEW_TITLE_ONLY,
        "应导致 test_content_key_requires_both_path_and_title 失败",
    ),
    (
        "关联路径不补媒体库侧（硬链接找不到）",
        RELATED_OLD,
        RELATED_NEW_SKIP,
        "应导致 test_seed_related_paths_covers_both_sides / "
        "test_hardlinks_removed_after_seed_deleted 失败",
    ),
    (
        "开关默认值语义反转（默认关，而非默认开）",
        DEFAULT_OLD,
        DEFAULT_NEW_BOOL,
        "应导致 test_hardlinks_removed_after_seed_deleted 等默认开用例失败",
    ),
    (
        "索引不做配置目录边界校验（越界风险）",
        INODE_SCOPE_OLD,
        INODE_SCOPE_NEW_NOCHECK,
        "应导致 test_inode_index_skips_outside_paths 失败",
    ),
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
