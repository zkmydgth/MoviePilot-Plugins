#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
变异测试：对联动清理逻辑注入缺陷，验证回归测试能否捕获。

思路：逐个替换插件源码中的关键判定为「错误实现」，重跑测试。
若某个变异体**全部测试通过**，说明该逻辑缺乏防护（逃逸），需要补测试。

变异体设计针对三类高风险错误：
1. 删种判定被放宽（不该删的种子被删）——最危险的业务风险
2. 刮削匹配被放宽（误删续集/其它媒体）
3. 安全边界被移除（删到配置目录外）
"""

import os
import re
import shutil
import subprocess
import sys

PLUGIN_DIR = "/root/.codebuddy/artifact/user-repo/plugins.v2/seedspaceguard"
PLUGINS_V2 = "/root/.codebuddy/artifact/user-repo/plugins.v2"
TARGET = os.path.join(PLUGIN_DIR, "__init__.py")
BACKUP = "/tmp/ssg_backup.py"


# (名称, 原文, 变异后, 期望被捕获的说明)
MUTANTS = [
    (
        "删种判定放宽：忽略文件存在性，直接删种",
        """        for record in records:
            fullpath = str(getattr(record, "fullpath", "") or "")
            if not fullpath:
                continue
            if os.path.exists(fullpath):
                return False
        return True""",
        """        return True""",
        "应导致「仍有文件却删种」的测试失败",
    ),
    (
        "删种判定放宽：把存在性判断反转",
        """            if os.path.exists(fullpath):
                return False""",
        """            if not os.path.exists(fullpath):
                return False""",
        "应导致 all_files_gone / one_file_remains 测试失败",
    ),
    (
        "删种判定反向：无记录时返回 True（危险的乐观默认）",
        """        if not records:
            # 无文件记录：无从判定，保守起见不删种
            return False""",
        """        if not records:
            return True""",
        "应导致 empty_records_returns_false 失败",
    ),
    (
        "转移记录：跳过 dest 直接只查 src",
        """        for query in (self._transferhis.get_by_dest,
                      self._transferhis.get_by_src):""",
        """        for query in (self._transferhis.get_by_src,):""",
        "应导致 delete_by_dest 失败",
    ),
    (
        "转移记录：开关失效（关闭也执行）",
        """        if not self._delete_history or not self._transferhis:
            return False""",
        """        if not self._transferhis:
            return False""",
        "应导致 disabled_switch_noop 失败",
    ),
    (
        "试运行保护失效：dry_run 仍执行联动",
        """        if dry_run or not deleted_paths or not self._linkage_enabled():
            return stats""",
        """        if not deleted_paths or not self._linkage_enabled():
            return stats""",
        "应导致 dry_run_no_side_effect 失败",
    ),
    (
        "种子级模式误做删种判定（跨越模式边界）",
        """        if self._delete_torrents and self._mode == "file":""",
        """        if self._delete_torrents:""",
        "应导致 seed_mode_skips_delete_judgement 失败",
    ),
    (
        "删种时误连带删除文件（delete_file=True）",
        """            ok = module.remove_torrents(
                hashs=[hash_str] if isinstance(hash_str, str) else hash_str,
                delete_file=False,
            )""",
        """            ok = module.remove_torrents(
                hashs=[hash_str] if isinstance(hash_str, str) else hash_str,
                delete_file=True,
            )""",
        "应导致 remove_torrents_called_without_file_deletion 失败",
    ),
    (
        "下载器范围过滤失效（忽略目标下载器配置）",
        """            if self._downloaders and name not in self._downloaders:
                continue""",
        """            pass""",
        "应导致 target_downloader_filter 失败",
    ),
    (
        "保护后缀失效：protect_pattern 被忽略（危险，会删下载中文件）",
        """                    if any(self._match_pattern(fname, pat) for pat in patterns):
                        continue""",
        """                    pass""",
        "应导致 protect_pattern_excludes 失败",
    ),
    (
        "保护后缀失效：只匹配第一个 pattern（多后缀保护不全）",
        """        patterns = [p.strip() for p in re.split(r"[,|，]", self._protect_pattern) if p.strip()]""",
        """        patterns = [p.strip() for p in re.split(r"[,|，]", self._protect_pattern) if p.strip()][:1]""",
        "应导致 protect_pattern_excludes 失败（多个后缀时只挡第一个）",
    ),
    (
        "系统目录未排除（@eaDir 被当清理候选）",
        """                dirs[:] = [d for d in dirs if not d.startswith("@") and d != "#recycle"]""",
        """                pass""",
        "应导致 system_dirs_still_excluded 失败",
    ),
    (
        "最近文件保护失效（recent_skip_days 被忽略）",
        """                    if now - st.st_mtime < recent_secs:
                        continue""",
        """                    pass""",
        "应导致 recent_file_respected 失败",
    ),
    (
        "硬链接文案回退：改回歧义的「处硬链接」",
        """                    f"，含 {len(linked)} 条路径（硬链接，共占 {round(size / GIB, 1)}GB）"
""",
        """                    f"，含 {len(linked)} 处硬链接"
""",
        "应导致 test_dry_run_wording_two_paths / no_legacy_wording 失败",
    ),
    (
        "硬链接文案：路径计数减 1（漏报路径条数）",
        """                    f"，含 {len(linked)} 条路径（硬链接，共占 {round(size / GIB, 1)}GB）"
""",
        """                    f"，含 {len(linked) - 1} 条路径（硬链接，共占 {round(size / GIB, 1)}GB）"
""",
        "应导致 test_dry_run_wording_two_paths / three_paths 失败",
    ),
    (
        "硬链接文案：占用空间翻倍（误导用户以为释放双倍）",
        """                    f"，含 {len(linked)} 条路径（硬链接，共占 {round(size / GIB, 1)}GB）"
""",
        """                    f"，含 {len(linked)} 条路径（硬链接，共占 {round(len(linked) * size / GIB, 1)}GB）"
""",
        "应导致 test_dry_run_wording_two_paths / three_paths 失败",
    ),
    (
        "硬链接文案：单路径也提示（多余噪音）",
        """                side_note = (
                    f"，含 {len(linked)} 条路径（硬链接，共占 {round(size / GIB, 1)}GB）"
                    if len(linked) > 1 else ""
                )""",
        """                side_note = f"，含 {len(linked)} 条路径（硬链接，共占 {round(size / GIB, 1)}GB）"
""",
        "应导致 test_single_path_has_no_notice 失败",
    ),
    (
        "真实删除文案回退：改回「连同 N 处硬链接」",
        """                    f"，连同其余 {len(linked) - 1} 条路径一并删除"
""",
        """                    f"，连同 {len(linked) - 1} 处硬链接一并删除"
""",
        "应导致 test_real_delete_wording_other_paths 失败",
    ),
]


def run_tests():
    """跑全部测试，返回 (通过, 摘要)。"""
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover",
         "-s", "seedspaceguard/tests", "-t", "seedspaceguard"],
        cwd=PLUGINS_V2, capture_output=True, text=True,
    )
    output = proc.stdout + proc.stderr
    tail = "\\n".join([ln for ln in output.strip().splitlines()[-4:]])
    return proc.returncode == 0, tail


def main():
    shutil.copy(BACKUP, TARGET)
    base_ok, base_tail = run_tests()
    print(f"基线：{'✅ 全部通过' if base_ok else '❌ 基线即失败'}")
    if not base_ok:
        print(base_tail)
        return 1

    source = open(BACKUP, encoding="utf-8").read()
    caught = escaped = 0
    escaped_names = []

    print(f"\\n{'='*70}\\n变异测试（共 {len(MUTANTS)} 个变异体）\\n{'='*70}")
    for idx, (name, old, new, expect) in enumerate(MUTANTS, 1):
        if old not in source:
            print(f"[{idx:2d}] ⚠️  跳过（定位失败）：{name}")
            continue
        mutated = source.replace(old, new, 1)
        with open(TARGET, "w", encoding="utf-8") as handle:
            handle.write(mutated)
        ok, tail = run_tests()
        if ok:
            escaped += 1
            escaped_names.append(name)
            print(f"[{idx:2d}] ❌ 逃逸：{name}\\n       （{expect}）")
        else:
            caught += 1
            fails = re.search(r"FAILED \\(.*?\\)", tail)
            detail = fails.group(0) if fails else "有失败"
            print(f"[{idx:2d}] ✅ 捕获：{name} → {detail}")

    shutil.copy(BACKUP, TARGET)
    print(f"\\n{'='*70}")
    print(f"结果：捕获 {caught} / 逃逸 {escaped} / 合计 {caught + escaped}")
    if escaped_names:
        print("逃逸清单：")
        for name in escaped_names:
            print(f"  - {name}")
    print("已还原原始源码。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
