#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
变异测试（v1.3.5 专项）：验证新增测试能捕获「刮削残留/空壳回收」两类缺陷。

每个变异体把插件源码中的关键实现替换为**错误版本**，重跑测试：
- 若测试失败 → 变异被捕获 ✅（说明该逻辑有防护）
- 若测试全过 → 变异逃逸 ❌（说明测试不够，需补）

用法（在插件目录下）::

    python3 tests/mutation_v135.py
"""

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(HERE)
TARGET = os.path.join(PLUGIN_DIR, "seedspaceguard.py")
BACKUP = "/tmp/sg135_mutation_backup.py"


MUTANTS = [
    (
        "刮削兜底失效：精确匹配落空后不按目录反查（回归原始 BUG）",
        """        if exact:
            return exact
        # 精确匹配落空：多半是刮削残留，退化为按目录前缀归属
        return self._resolve_hash_by_parent_dir(path)""",
        """        return exact""",
        "应导致 test_scrape_file_resolved_via_parent_dir / test_md5_leftover_resolved "
        "以及 test_leftover_uses_dir_fallback_in_linkage 失败",
    ),
    (
        "刮削兜底失效：目录前缀匹配改成相等匹配（子目录残留漏判）",
        """            if parent == seed_path or parent.startswith(seed_path + os.sep):""",
        """            if parent == seed_path:""",
        "应导致 test_longest_prefix_wins 失败",
    ),
    (
        "反查失败静默：去掉 WARN 日志（回归原始 BUG）",
        """                else:
                    # 反查失败会让该文件所属种子彻底失去删种机会，必须留痕；
                    # 常见于未经 MoviePilot 登记的刮削残留或手动放入的文件。
                    logger.warning(
                        "【保种空间守护】无法按路径归属种子，跳过该文件的删种判定：%s",
                        path,
                    )""",
        """                else:
                    pass""",
        "应导致 test_unresolvable_path_logs_warning 失败",
    ),
    (
        "空壳回收失效：整体不执行（回归原始 BUG）",
        """            orphan_stats = self._reap_orphan_seeds(dry_run)""",
        """            orphan_stats = {"torrent": 0, "checked": 0, "_lines": []}""",
        "应导致 test_reap_runs_when_space_plenty / test_reap_result_in_message 失败",
    ),
    (
        "空壳回收失效：判定反转（有文件也回收，会误删在做种的资源）",
        """                if not self._seed_fully_removed(hash_str):
                    stats["alive"] += 1
                    continue""",
        """                if self._seed_fully_removed(hash_str):
                    stats["alive"] += 1
                    continue""",
        "应导致 test_orphan_seed_reaped 失败",
    ),
    (
        "空壳回收误删文件：delete_file 传 True（会连带删掉正片）",
        """            ok = module.remove_torrents(
                    hashs=[hash_str], delete_file=False, downloader=downloader,
                ) if module else False""",
        """            ok = module.remove_torrents(
                    hashs=[hash_str], delete_file=True, downloader=downloader,
                ) if module else False""",
        "应导致 test_orphan_seed_reaped 的 delete_file 断言失败",
    ),
    (
        "空壳回收无保护：试运行也真的删种",
        """        if dry_run or not self._downloader_available() or not self._delete_torrents:
            return stats""",
        """        if not self._downloader_available() or not self._delete_torrents:
            return stats""",
        "应导致 test_dry_run_does_not_remove 失败",
    ),
    (
        "空壳回收无保护：开关关闭仍执行",
        """        if dry_run or not self._downloader_available() or not self._delete_torrents:
            return stats""",
        """        if dry_run or not self._downloader_available():
            return stats""",
        "应导致 test_switch_off_noop 失败",
    ),
    (
        "空壳回收越界：不看配置目录范围（会删到监控目录外的种子）",
        """                        if cand and cand["path"] and self._path_under_any(cand["path"]):""",
        """                        if cand and cand["path"]:""",
        "应导致 test_orphan_outside_target_dirs_ignored 失败",
    ),
    (
        "空壳回收统计错误：不计数（用户看不到回收结果）",
        """            if ok:
                stats["torrent"] += 1
                line = f"已回收空壳种子（该种子文件已全部删除）：{title}\"""",
        """            if ok:
                line = f"已回收空壳种子（该种子文件已全部删除）：{title}\"""",
        "应导致 test_orphan_seed_reaped / test_reap_result_in_message 失败",
    ),
    (
        "空壳回收位置错误：挪到「空间充足」早退之后（回归原始 BUG）",
        """            orphan_stats = self._reap_orphan_seeds(dry_run)
            orphan_lines = orphan_stats.pop("_lines", [])
            orphan_note = \"\"""",
        """            orphan_stats = {"torrent": 0, "checked": 0, "_lines": []}
            orphan_lines = orphan_stats.pop("_lines", [])
            orphan_note = \"\"""",
        "应导致 test_reap_runs_when_space_plenty 失败",
    ),
    (
        "空壳回收抑制保护期（把保护期检查错误地加回来，导致空壳永远不回收）",
        """        for cand in candidates:
            hash_str = str(cand.get("hash") or "")
            if not hash_str:
                continue
            stats["checked"] += 1""",
        """        cutoff = __import__("time").time() - self._recent_skip_days * 86400
        for cand in candidates:
            hash_str = str(cand.get("hash") or "")
            if not hash_str:
                continue
            if int(cand.get("added") or 0) >= cutoff:
                continue
            stats["checked"] += 1""",
        "应导致 test_recent_orphan_also_reaped 失败（空壳不受保护期限制）",
    ),
    (
        "TR 命名兼容失效：只认 camelCase（回归原始 BUG，1765 个种子全丢）",
        """        percent = float(
            self._pick_attr(item, "percent_done", "percentDone", default=0) or 0
        )""",
        """        percent = float(
            self._pick_attr(item, "percentDone", default=0) or 0
        )""",
        "应导致 test_snake_case_parsed / test_snake_case_added_from_done_date 失败",
    ),
    (
        "TR 命名兼容失效：下载目录只认 camelCase（路径拼接为空）",
        """        dl_dir = self._pick_attr(item, "download_dir", "downloadDir", default="") or \"\"""",
        """        dl_dir = self._pick_attr(item, "downloadDir", default="") or \"\"""",
        "应导致 test_snake_case_parsed 的 path 断言失败",
    ),
    (
        "TR 命名兼容失效：hash 只认 camelCase",
        """            \"hash\": self._pick_attr(item, \"hash_string\", \"hashString\",
                                    \"id\", default=\"\") or \"\",""",
        """            \"hash\": self._pick_attr(item, \"hashString\", default=\"\") or \"\",""",
        "应导致 test_snake_case_parsed 的 hash 断言失败",
    ),
    (
        "TR 判定放松：未完成种子也被纳入（危险，会删在下载的资源）",
        """        if percent < 0.999:
            return None""",
        """        if percent < 0.0:
            return None""",
        "应导致 test_snake_case_incomplete_rejected 失败",
    ),
    (
        "取值辅助失效：遇 None 不继续尝试下一个名字",
        """        for name in names:
            if isinstance(item, dict):
                if name in item and item[name] is not None:
                    return item[name]
                continue
            value = getattr(item, name, None)
            if value is not None:
                return value
        return default""",
        """        return getattr(item, names[0], default)""",
        "应导致 test_first_non_none_wins / test_dict_lookup 失败",
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
    # 原文读入内存，全程以它为基准，结束再写回（不依赖 /tmp 残留文件）。
    # 旧版用固定路径 /tmp 备份，若该文件恰是历史遗留的旧版本，会把源码
    # 静默打回旧版，且残留文件会跨脚本互相干扰。
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
    try:
        for idx, (name, old, new, expect) in enumerate(MUTANTS, 1):
            if old not in original:
                print(f"[{idx:2d}] ⚠️  跳过（源码不匹配，需更新变异体定义）：{name}")
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
                print(f"     实际：测试全部通过，该缺陷未被捕获\n")
            else:
                caught += 1
                print(f"[{idx:2d}] ✅ 捕获：{name}")
    finally:
        # 无论中途异常还是正常结束，都必须还原原始源码
        with open(TARGET, "w", encoding="utf-8") as handle:
            handle.write(original)

    print()
    print("=" * 72)
    total = caught + escaped
    print(f"变异测试结果：{caught}/{total} 被捕获，{escaped} 个逃逸")
    if escaped_names:
        print("逃逸清单（需补充测试）：")
        for name in escaped_names:
            print(f"  - {name}")
        return 1
    print("🎉 全部变异被捕获，测试防护有效")
    return 0


if __name__ == "__main__":
    sys.exit(main())
