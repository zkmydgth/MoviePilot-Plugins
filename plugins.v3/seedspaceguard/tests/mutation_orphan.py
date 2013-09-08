#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
变异测试（无主文件专项）：验证「孤儿硬链接清扫」的测试防护有效。

背景：`_clean_orphan_files` 会主动删除用户文件，判定错的代价是数据丢失。
本脚本用「替换源码 → 跑测试 → 必须失败」的方式，验证测试**真的**能拦住
这些危险缺陷，而不是空过。

重点防护的最高危风险：**误删仍在做种的文件**。

用法（在插件目录下）::

    python3 tests/mutation_orphan.py
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

# ① 「有主」集合只用候选收集（会丢掉监控范围外的活跃种子）→ 误删做种文件
OWNED_SCOPE_OLD = (
    "            try:\n"
    "                ret = server.get_torrents()\n"
    "            except Exception as err:\n"
    "                # 单个下载器读不到 → 整轮判定不可信，直接放弃\n"
    "                logger.error(\n"
    "                    \"【保种空间守护】无主判定：读取下载器 %s 种子列表失败：%s\",\n"
    "                    name, err,\n"
    "                )\n"
    "                return None\n"
    "            items = ret[0] if isinstance(ret, tuple) else ret\n"
    "            for item in items or []:\n"
)
OWNED_SCOPE_NEW = (
    "            items = []\n"
    "            for item in items or []:\n"
)

# ② 跳过未完成种子（progress<1 仍占磁盘文件）→ 误删正在下载的文件
OWNED_UNFINISHED_OLD = (
    "            for item in items or []:\n"
    "                probed += 1\n"
)
OWNED_UNFINISHED_NEW = (
    "            for item in items or []:\n"
    "                probed += 1\n"
    "                _prog = float(self._pick_attr(item, \"progress\", default=0) or 0)\n"
    "                if _prog < 0.999:\n"
    "                    continue\n"
)

# ③ 判定粒度改成「按路径」而非「按 inode 组」→ 一侧有主也照删
OWNED_GROUP_OLD = (
    "            plist = ino_paths.get(key, [])\n"
    "            if any(self._is_owned_path(p, owned) for p in plist):\n"
    "                continue\n"
)
OWNED_GROUP_NEW = (
    "            plist = ino_paths.get(key, [])\n"
    "            if plist and self._is_owned_path(plist[0], owned):\n"
    "                pass\n"
)

# ④ 前缀匹配退化为全等 → 种子目录下的各集文件会被误判无主
OWNED_PREFIX_OLD = (
    "        for owner in owned:\n"
    "            if norm.startswith(owner + os.sep):\n"
    "                return True\n"
    "        return False\n"
)
OWNED_PREFIX_NEW = (
    "        return False\n"
)

# ⑤ 枚举失败时不放弃（返回空集合继续扫）→ 会把所有文件当无主删光
OWNED_FAIL_OLD = (
    "        if not services:\n"
    "            logger.warning(\"【保种空间守护】无主判定：无可用下载器，放弃本轮判定\")\n"
    "            return None\n"
)
OWNED_FAIL_NEW = (
    "        if not services:\n"
    "            return set()\n"
)

# ⑥ 调用方拿到 None 仍继续清扫 → 上面 ⑤ 的保护在调用侧失效
CALLER_NONE_OLD = (
    "        owned = self._collect_all_torrent_refs()\n"
    "        if owned is None:\n"
)
CALLER_NONE_NEW = (
    "        owned = self._collect_all_torrent_refs()\n"
    "        if False:\n"
)

# ⑦ 开关默认值反转（默认关 → 默认开）
SWITCH_DEFAULT_OLD = (
    "        self._orphan_cleanup = bool(config.get(\"orphan_cleanup\"))\n"
)
SWITCH_DEFAULT_NEW = (
    "        self._orphan_cleanup = config.get(\"orphan_cleanup\", True) is not False\n"
)

# ⑧ 保护期过滤被移除 → 刚入库的文件会被删
PROTECT_WINDOW_OLD = (
    "        recent_secs = self._recent_skip_days * 86400\n"
    "        files, ino_paths = self._index_files(patterns, recent_secs)\n"
)
PROTECT_WINDOW_NEW = (
    "        files, ino_paths = self._index_files(patterns, 0)\n"
)


MUTANTS = [
    ("有主集合忽略「范围外活跃种子」", OWNED_SCOPE_OLD,
     OWNED_SCOPE_NEW, "范围外做种的种子其文件会被误判无主 → 删掉正在做种的文件"),
    ("有主集合跳过「未完成种子」", OWNED_UNFINISHED_OLD,
     OWNED_UNFINISHED_NEW, "未完成种子仍占磁盘文件 → 会被误删"),
    ("判定粒度改为「按路径」而非按 inode 组", OWNED_GROUP_OLD,
     OWNED_GROUP_NEW, "同 inode 一侧有主时另一侧会被误删"),
    ("有主前缀匹配退化为全等", OWNED_PREFIX_OLD,
     OWNED_PREFIX_NEW, "种子目录下的各集文件会被误判无主"),
    ("枚举失败时返回空集合而非 None", OWNED_FAIL_OLD,
     OWNED_FAIL_NEW, "无下载器时会把范围内文件全部当无主删光"),
    ("调用方忽略 None（不放弃清扫）", CALLER_NONE_OLD,
     CALLER_NONE_NEW, "拿不到种子清单仍继续清扫 → 误删"),
    ("开关默认值反转为开启", SWITCH_DEFAULT_OLD,
     SWITCH_DEFAULT_NEW, "未配置时默认开启会误删用户文件"),
    ("保护期过滤被移除", PROTECT_WINDOW_OLD,
     PROTECT_WINDOW_NEW, "刚入库的文件会被当无主删除"),
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
