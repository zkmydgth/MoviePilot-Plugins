#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
变异测试（空壳回收范围专项）：验证「覆盖监控目录外空壳」的测试防护有效。

背景：v3.0.4 把空壳回收的候选来源从「只扫监控目录内」扩展为「可选覆盖
监控目录外」。扩范围本身不删文件（`remove_torrents(delete_file=False)`），
但**误删仍在做种的种子**的风险随之上升——范围外种子同样在做种。

本脚本用「替换源码 → 跑测试 → 必须失败」的方式，验证测试**真的**能拦住
这些危险缺陷，而不是空过。

用法（在插件目录下）::

    python3 tests/mutation_seed_scope.py
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

# ① 全量候选仍过滤 out_of_scope → 范围外空壳又漏了（本次改造白做）
STILL_SCOPE_OLD = (
    "                in_scope = bool(cand[\"path\"]) and self._path_under_any(cand[\"path\"])\n"
    "                if not in_scope:\n"
    "                    counted[\"out_of_scope\"] += 1\n"
    "                    # 主链路：范围外直接丢弃；空壳回收：保留（见上方方法说明）\n"
    "                    if scope_only:\n"
    "                        continue\n"
)
STILL_SCOPE_NEW = (
    "                in_scope = bool(cand[\"path\"]) and self._path_under_any(cand[\"path\"])\n"
    "                if not in_scope:\n"
    "                    counted[\"out_of_scope\"] += 1\n"
    "                    continue\n"
)

# ② 开关关闭时仍走全量候选 → 默认关形同虚设，行为边界被无声扩大
SWITCH_BYPASS_OLD = (
    "            if self._orphan_seed_scope:\n"
    "                candidates = self._collect_all_seed_candidates()\n"
    "            else:\n"
    "                candidates = self._collect_seed_candidates()\n"
)
SWITCH_BYPASS_NEW = (
    "            candidates = self._collect_all_seed_candidates()\n"
)

# ③ 开关读取改成「默认开」（is not False）→ 未配置即扩大边界
SWITCH_DEFAULT_OLD = (
    "        self._orphan_seed_scope = bool(config.get(\"orphan_seed_scope\"))\n"
)
SWITCH_DEFAULT_NEW = (
    "        self._orphan_seed_scope = (\n"
    "            config.get(\"orphan_seed_scope\", True) is not False\n"
    "        )\n"
)

# ④ ⚠️ 最高危：范围外种子跳过物理复核（只凭记录判定）→ 误删做种种子
#    模拟手法：把「候选是否参与复核」的判据改成「只看范围」，范围外直接
#    认为已删空。等价于放弃 _seed_fully_removed 的第 2 级复核。
NO_RECHECK_OLD = (
    "                # 传入候选对象：判定需要种子的真实内容路径做物理复核，\n"
    "                # 仅凭 hash 查 DownloadFiles 在双下载器/路径迁移场景会误判\n"
    "                if not self._seed_fully_removed(hash_str, cand):\n"
    "                    stats[\"alive\"] += 1\n"
    "                    continue\n"
)
NO_RECHECK_NEW = (
    "                # 变异：范围外种子不传候选 → 丢掉物理复核，只凭记录判定\n"
    "                _in_scope = self._path_under_any(str(cand.get(\"path\") or \"\"))\n"
    "                _probe = cand if _in_scope else None\n"
    "                if not self._seed_fully_removed(hash_str, _probe):\n"
    "                    stats[\"alive\"] += 1\n"
    "                    continue\n"
)

# ⑤ 目录扫描上限失效（扫不完也判「无文件」）→ 超大种子目录被当空壳回收
SCAN_LIMIT_OLD = (
    "                    if count > max_scan:\n"
    "                        # 条目过多时保守判定「有文件」，绝不因扫不完而误删\n"
    "                        return True\n"
)
SCAN_LIMIT_NEW = (
    "                    if False and count > max_scan:\n"
    "                        return True\n"
)

# ⑥ 全量候选顺手放过未完成种子 → 越界改动（决策点 2 明确本轮不做）
UNFINISHED_LEAK_OLD = (
    "                cand = self._parse_torrent(dl_type, item)\n"
    "                if not cand:\n"
    "                    # 未完成（下载中/暂停/校验）的种子一律排除。\n"
    "                    # 两个入口在此**行为一致**——空壳回收暂不覆盖未完成种子。\n"
    "                    counted[\"unfinished\"] += 1\n"
    "                    continue\n"
)
UNFINISHED_LEAK_NEW = (
    "                cand = self._parse_torrent(dl_type, item)\n"
    "                if not cand:\n"
    "                    counted[\"unfinished\"] += 1\n"
    "                    if scope_only:\n"
    "                        continue\n"
    "                    _p = str(self._pick_attr(\n"
    "                        item, \"content_path\", \"contentPath\",\n"
    "                        \"save_path\", \"savePath\", default=\"\") or \"\")\n"
    "                    cand = {\"path\": _p, \"hash\": str(\n"
    "                        self._pick_attr(item, \"hash\", default=\"\") or \"\"),\n"
    "                        \"title\": str(self._pick_attr(item, \"name\", default=\"\") or \"\"),\n"
    "                        \"added\": 0, \"size_gb\": 0.0}\n"
)


MUTANTS = [
    (
        "全量候选仍过滤范围外种子",
        STILL_SCOPE_OLD, STILL_SCOPE_NEW,
        "范围外空壳又会漏扫，本次改造白做",
    ),
    (
        "开关关闭时仍走全量候选",
        SWITCH_BYPASS_OLD, SWITCH_BYPASS_NEW,
        "默认关形同虚设，行为边界被无声扩大",
    ),
    (
        "开关默认值反转（未配置即开启）",
        SWITCH_DEFAULT_OLD, SWITCH_DEFAULT_NEW,
        "未配置就扩大边界，用户预期外",
    ),
    (
        "⚠️ 范围外种子跳过物理复核",
        NO_RECHECK_OLD, NO_RECHECK_NEW,
        "只凭失效记录判定 → 误删仍在做种的文件（数据丢失级）",
    ),
    (
        "目录扫描上限失效",
        SCAN_LIMIT_OLD, SCAN_LIMIT_NEW,
        "超大目录扫不完仍判「无文件」→ 误删完整种子",
    ),
    (
        "全量候选顺手放过未完成种子",
        UNFINISHED_LEAK_OLD, UNFINISHED_LEAK_NEW,
        "越界改动：本轮明确不覆盖未完成种子",
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
    skip_note = f"，{skipped} 个跳过" if skipped else ""
    print(f"变异测试结果：{caught}/{total} 被捕获，{escaped} 个逃逸{skip_note}")
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
