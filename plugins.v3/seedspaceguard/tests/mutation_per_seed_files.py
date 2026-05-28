#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
变异测试（种子自身文件清单专项）：验证 v3.0.5 改造的测试防护有效。

背景：v3.0.5 把空壳判定的第 2 级从「扫种子所在目录」改为「按该种子**自身**
的文件清单复核」。改造动机是实测出的静默失效——同一目录下 19 个种子各管一集，
只要兄弟种子的文件还在，扫目录就永远判「未删空」，该种子从此回收不掉。

改造带来两类新风险，本脚本逐一用「替换源码 → 跑测试 → 必须失败」验证测试
**真的**能拦住它们，而不是空过：

  1. **误删方向**（数据丢失级）：清单取错/解析错 → 把自己的文件当成「已删空」
     → 误回收仍在做种的种子。空壳回收虽然传 `delete_file=False` 只摘种子，
     但摘掉活跃种子本身即是事故。
  2. **漏回收方向**（功能静默失效）：清单取不到又不退回、或漏改第二处调用点
     （仅文件模式联动删种）→ 修复等于没做，且日志上毫无异常痕迹。

用法（在插件目录下）::

    python3 tests/mutation_per_seed_files.py
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

# ① 清单取不到时不退回扫目录、直接判「已删空」→ 保守垫被打穿
NO_FALLBACK_OLD = (
    "        if own_files:\n"
    "            if any(os.path.exists(p) for p in own_files):\n"
    "                # 自己的文件还在 → 保留（与旧逻辑一致）\n"
    "                return False\n"
    "            # 自己的文件全没了 → 落到下方「确凿无文件」判定\n"
    "        elif cand is not None:\n"
)
NO_FALLBACK_NEW = (
    "        if own_files:\n"
    "            if any(os.path.exists(p) for p in own_files):\n"
    "                return False\n"
    "        elif False and cand is not None:\n"
)

# ② own_files 混入同目录全部文件 → 退化成「扫目录」的相反面（清单永不判空）
MIX_SIBLINGS_OLD = (
    "        own_files: List[str] = []\n"
    "        if cand is not None:\n"
    "            own_files = self._get_seed_files(hash_str, cand)\n"
)
MIX_SIBLINGS_NEW = (
    "        own_files: List[str] = []\n"
    "        if cand is not None:\n"
    "            own_files = self._get_seed_files(hash_str, cand)\n"
    "            _cp = str(cand.get(\"path\") or \"\")\n"
    "            if _cp and os.path.isdir(_cp):\n"
    "                for _n in os.listdir(_cp):\n"
    "                    own_files.append(os.path.join(_cp, _n))\n"
)

# ③ 判定方向反义：改成「全部文件都不存在才算删空」的相反逻辑
INVERT_OLD = (
    "            if any(os.path.exists(p) for p in own_files):\n"
    "                # 自己的文件还在 → 保留（与旧逻辑一致）\n"
    "                return False\n"
)
INVERT_NEW = (
    "            if any(os.path.exists(p) for p in own_files):\n"
    "                pass\n"
    "            else:\n"
    "                return False\n"
)

# ④ ⚠️ 最高危：空壳回收删种改传 delete_file=True（摘种子变成连带头删文件）
DELETE_FILE_OLD = (
    "                ok = module.remove_torrents(\n"
    "                    hashs=[hash_str], delete_file=False, downloader=downloader,\n"
    "                ) if module else False\n"
)
DELETE_FILE_NEW = (
    "                ok = module.remove_torrents(\n"
    "                    hashs=[hash_str], delete_file=True, downloader=downloader,\n"
    "                ) if module else False\n"
)

# ⑤ 清单路径拼接错误（只取文件名，没拼基准目录）→ 路径指向错误位置
JOIN_OLD = (
    "        base = self._seed_download_base(cand)\n"
    "        if not base:\n"
    "            return []\n"
    "        return [os.path.normpath(os.path.join(base, name)) for name in names]\n"
)
JOIN_NEW = (
    "        base = self._seed_download_base(cand)\n"
    "        if not base:\n"
    "            return []\n"
    "        return [os.path.normpath(name) for name in names]\n"
)

# ⑥ 异常被吞成「无文件」：取清单抛异常时返回空列表以外的「已删空」信号
#
#    这里模拟：调用方把「取清单失败」误当成「确实没有文件」——
#    直接跳过第 2 级整体判定。
SWALLOW_OLD = (
    "        own_files: List[str] = []\n"
    "        if cand is not None:\n"
    "            own_files = self._get_seed_files(hash_str, cand)\n"
    "        if own_files:\n"
)
SWALLOW_NEW = (
    "        own_files: List[str] = []\n"
    "        if cand is not None:\n"
    "            own_files = self._get_seed_files(hash_str, cand)\n"
    "        if True:\n"
)

# ⑦ 仅文件模式联动删种退回只传 path（丢掉 module）→ 该链路修复半失效
#
#    v3.0.5 补漏前就是这个写法；本变异体锁死「不得退回旧写法」。
LINKAGE_PATH_ONLY_OLD = (
    "            cand_by_hash: Dict[str, Dict[str, Any]] = {}\n"
    "            try:\n"
    "                for cand in self._collect_seed_candidates():\n"
    "                    h = str(cand.get(\"hash\") or \"\")\n"
    "                    if h and h not in cand_by_hash:\n"
    "                        cand_by_hash[h] = cand\n"
    "            except Exception as err:\n"
    "                logger.error(\"【保种空间守护】联动删种前获取种子信息失败：%s\", err)\n"
    "\n"
    "            for hash_str, sample_path in pending_hashes.items():\n"
    "                # 取不到候选（多为范围外/解析失败）→ probe=None，\n"
    "                # 退化为「仅有第 1 级记录复核」，与改造前行为一致，不误删\n"
    "                probe = cand_by_hash.get(hash_str)\n"
)
LINKAGE_PATH_ONLY_NEW = (
    "            cand_by_hash: Dict[str, Dict[str, Any]] = {}\n"
    "            try:\n"
    "                for cand in self._collect_seed_candidates():\n"
    "                    h = str(cand.get(\"hash\") or \"\")\n"
    "                    if h and h not in cand_by_hash:\n"
    "                        cand_by_hash[h] = cand\n"
    "            except Exception as err:\n"
    "                logger.error(\"【保种空间守护】联动删种前获取种子信息失败：%s\", err)\n"
    "\n"
    "            for hash_str, sample_path in pending_hashes.items():\n"
    "                _c = cand_by_hash.get(hash_str)\n"
    "                probe = {\"path\": str(_c.get(\"path\") or \"\")} if _c else None\n"
)


MUTANTS = [
    (
        "清单取不到时不退回保守逻辑",
        NO_FALLBACK_OLD, NO_FALLBACK_NEW,
        "保守垫被打穿 → 取不到清单就判空壳，可能误摘活跃种子",
    ),
    (
        "own_files 混入同目录全部文件",
        MIX_SIBLINGS_OLD, MIX_SIBLINGS_NEW,
        "退化成扫目录的相反面 → 清单永不为空，修复静默失效",
    ),
    (
        "空壳判定方向反义",
        INVERT_OLD, INVERT_NEW,
        "「有文件」被判成「未删空的反面」→ 判定完全失控",
    ),
    (
        "⚠️ 空壳回收删种改传 delete_file=True",
        DELETE_FILE_OLD, DELETE_FILE_NEW,
        "摘种子变成连带头删文件 → 数据丢失级",
    ),
    (
        "清单路径拼接错误（未拼基准目录）",
        JOIN_OLD, JOIN_NEW,
        "相对路径拿去 os.path.exists → 判定依据错位",
    ),
    (
        "第 2 级整体被跳过（异常吞成「无文件」）",
        SWALLOW_OLD, SWALLOW_NEW,
        "承认「取清单失败 = 没有文件」→ 保守原则被破坏",
    ),
    (
        "仅文件模式联动删种退回只传 path",
        LINKAGE_PATH_ONLY_OLD, LINKAGE_PATH_ONLY_NEW,
        "该链路的精确清单修复半失效（module 丢失 → 退回扫目录）",
    ),
]


def run_tests():
    """跑全部测试，返回 (是否全过, 摘要)。"""
    args = [sys.executable, "-m", "pytest", "tests/",
            "-q", "--no-header", "-p", "no:cacheprovider"]
    proc = subprocess.run(
        args, cwd=PLUGIN_DIR, capture_output=True, text=True,
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
    if skipped:
        # 跳过意味着变异体定义与源码脱节，防护可能已悄悄失效 —— 视为失败
        print("❌ 存在被跳过的变异体：定义与源码不匹配即等于该缺陷无人守护")
        return 1
    print("🎉 全部变异被捕获，测试防护有效")
    return 0


if __name__ == "__main__":
    sys.exit(main())
