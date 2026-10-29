#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
变异测试（v3.0.13 审计整改专项）。

每个变异体对应一条整改点：把该整改「改回去」，必须至少有一个用例失败，
否则说明这条整改没有测试守护。

用法（在插件目录下）::

    python3 tests/mutation_v3013.py
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(HERE)
TARGET = os.path.join(PLUGIN_DIR, "__init__.py")


MUTANTS = [
    (
        "A1 共用目录改用「已过滤」的候选集（看不见保护期内的新种子）",
        "                    shared_dirs = self._shared_content_dirs(cand, all_candidates)",
        "                    shared_dirs = self._shared_content_dirs(cand, candidates)",
        "应导致 test_protected_sibling_files_survive 失败（新种子文件被连带删除）",
    ),
    (
        "A2 删种不再传下载器实例名",
        '            if owner:\n                kwargs["downloader"] = owner',
        '            if False:\n                kwargs["downloader"] = owner',
        "应导致 test_downloader_name_is_forwarded 失败",
    ),
    (
        "A3 配置里的模式多了个非法白名单项",
        '        if raw_mode not in ("seed", "file"):',
        '        if raw_mode not in ("seed", "file", "files"):',
        "应导致 test_invalid_mode_disables_plugin 失败（插件被启用）",
    ),
    (
        "A4 手动触发不再检查启用状态",
        '        if manual_action in ("dry", "clean") and self._enabled:',
        '        if manual_action in ("dry", "clean") and True:',
        "应导致 test_disabled_init_does_not_start_manual_run 失败",
    ),
    (
        "A5 哨兵不归一化，直接参与后续算术",
        "            if no_candidates and deleted <= 0:",
        "            if no_candidates:",
        "应导致 test_sentinel_not_added_to_orphan_count 失败（数量少报）",
    ),
    (
        "B1 等待 0 秒仍判「未释放」",
        '        if self._sync_wait_seconds <= 0:\n'
        '            # 用户明确选择「不等待」时无法测量释放量，不能据此判失败\n'
        '            return True',
        '        if False:\n            return True',
        "应导致 test_no_wait_no_stall 失败",
    ),
    (
        "B2 保存目录可见时不再判空壳（漏回收）",
        "            if base and os.path.isdir(base):\n                return True",
        "            if base and os.path.isdir(base):\n                return False",
        "应导致 test_missing_content_with_visible_save_dir_reaped 失败",
    ),
    (
        "B3 「有主」判定重新整体采信保存目录",
        "                # ② 种子名 → 各配置目录下的推断路径",
        '                save_path = str(\n'
        '                    self._pick_attr(item, "save_path", "savePath",\n'
        '                                    default="") or ""\n'
        '                ).strip()\n'
        "                if save_path:\n"
        "                    refs.add(os.path.normpath(save_path))\n"
        "                # ② 种子名 → 各配置目录下的推断路径",
        "应导致 test_monitored_dir_as_save_path_does_not_own_everything 失败",
    ),
    (
        "B4 联动判种回读配置里的模式（而非本次实际模式）",
        '        active_mode = getattr(self, "_active_mode", "") or self._mode',
        "        active_mode = self._mode",
        "应导致 test_file_override_in_seed_config_still_deletes_torrent 失败",
    ),
    (
        "B5 时间未知的种子重新当成「最老」参与删除",
        '            if int(c.get("added") or 0) > 0 and int(c["added"]) < cutoff',
        '            if int(c.get("added") or 0) >= 0 and int(c["added"]) < cutoff',
        "应导致 test_zero_added_not_selected 失败",
    ),
    (
        "B6 阈值解析去掉兜底（非法值直接抛异常）",
        "        try:\n"
        '            self._threshold_gb = int(config.get("threshold_gb") or 500)\n'
        "        except (TypeError, ValueError):\n"
        "            self._threshold_gb = 500\n"
        '            self._config_error = "剩余空间阈值不是有效数字，已回退默认 500GB"\n'
        '            logger.error("【保种空间守护】%s", self._config_error)',
        '        self._threshold_gb = int(config.get("threshold_gb") or 500)',
        "应导致 test_bad_numbers_and_cron_fall_back 抛错失败",
    ),
    (
        "B7a 目录扫描不再把符号链接当内容",
        "                        if entry.is_symlink():\n"
        "                            # 符号链接也占着目录项，按「仍有内容」保守处理\n"
        "                            return True",
        "                        pass",
        "应导致 test_symlink_counts_as_content 失败",
    ),
    (
        "B7b 目录扫描重新把 DSM 索引目录当内容",
        '                    if entry.name.startswith("@") or entry.name == "#recycle":\n'
        "                        continue",
        "                    pass",
        "应导致 test_syno_index_dir_not_counted_as_file 失败",
    ),
    (
        "B8 不再跳过「整理任务托管」的转移记录",
        '            if getattr(record, "transfer_task_id", None) is not None:',
        "            if False:",
        "应导致 test_task_managed_record_not_deleted 失败",
    ),
    (
        "B9 不再拒绝根目录作为监控目录",
        "            if norm == os.sep:\n"
        "                logger.error(\"【保种空间守护】拒绝把根目录作为监控目录：%s\", path)",
        "            if False:\n"
        "                logger.error(\"【保种空间守护】拒绝把根目录作为监控目录：%s\", path)",
        "应导致 test_root_and_volume_root_rejected 失败",
    ),
    (
        "C1 辅种永不受保护期约束",
        "            if not self._companion_protect:\n                return True",
        "            if True:\n                return True",
        "应导致 test_protected_by_default 失败（保护期内的辅种被删）",
    ),
    (
        "D1a 摘要不再标注「已提前停止」",
        '            if self._clean_stats.get("stalled"):\n'
        '                suffix += "（已提前停止：空间未按预期释放）"',
        "            pass",
        "应导致 test_stall_marks_summary_and_notification 失败",
    ),
    (
        "D1b 上次结果不再落盘",
        '            self.save_data("last_result", msg)',
        "            pass",
        "应导致 test_result_persisted 失败",
    ),
    (
        "D1c 路径映射退化为直通（跨容器路径对不上）",
        "        norm = os.path.normpath(str(path))",
        "        return path",
        "应导致 test_returns_local_path 失败",
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
