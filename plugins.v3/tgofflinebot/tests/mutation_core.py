#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
变异测试：验证关键逻辑的单元测试真的能拦住缺陷。

做法：逐个把源码里的关键判断改成「失效版本」，跑对应测试，必须失败；
任何「变异后测试仍通过（逃逸）」或「锚点找不到（跳过）」都判为失败。

用法（插件目录下）::

    python tests/mutation_core.py
"""

import hashlib
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(HERE)
TARGET = os.path.join(PLUGIN_DIR, "__init__.py")

# (名称, 原始片段, 变异片段, 目标测试节点)
MUTATIONS = [
    (
        "白名单清空保护",
        "if len(self._allowed_users) <= 1 and user_id in self._allowed_users:",
        "if False:",
        "tests/test_tgofflinebot.py::test_api_whitelist_refuses_empty",
    ),
    (
        "来源隔离",
        "            if len(key) >= 12 and key[:12] in path_key:\n"
        "                return True\n"
        "        return False",
        "            if len(key) >= 12 and key[:12] in path_key:\n"
        "                return True\n"
        "        return True",
        "tests/test_tgofflinebot.py::test_non_owned_task_skips_precheck",
    ),
    (
        "残留扫描节流",
        "if last and now - last < RESIDUE_SCAN_INTERVAL:",
        "if False:",
        "tests/test_tgofflinebot.py::test_cleanup_residue_throttles_same_directory",
    ),
    (
        "未送审文件跳过",
        "if name not in verdict:",
        "if False:",
        "tests/test_tgofflinebot.py::test_residue_skips_files_beyond_review_limit",
    ),
    (
        "识别词持久化",
        'self.save_data("identifier_rules", snapshot)',
        "pass",
        "tests/test_tgofflinebot.py::test_identifier_rule_is_persisted",
    ),
    (
        "离线任务来源过滤",
        'if not self._is_owned_text(str(task.get("name") or "")):',
        "if False:",
        "tests/test_tgofflinebot.py::test_cleanup_skips_non_owned_offline_tasks",
    ),
    (
        "目录型源不删除",
        'if file_type and file_type != "file":',
        "if False:",
        "tests/test_tgofflinebot.py::test_directory_source_is_not_deleted",
    ),
    (
        "通知饿死保护",
        'and now - float(item.get("first_ts") or now) >= NOTIFY_MAX_HOLD_SECONDS',
        "and False",
        "tests/test_tgofflinebot.py::test_notify_forces_flush_after_long_window",
    ),
    (
        "通知静默默认窗口",
        "DEFAULT_NOTIFY_WINDOW = 300",
        "DEFAULT_NOTIFY_WINDOW = 60",
        "tests/test_tgofflinebot.py::test_notify_defaults_merge_quietly_and_skip_strm",
    ),
    (
        "STRM 通知开关",
        "if self._notify_strm and dests:",
        "if dests:",
        "tests/test_tgofflinebot.py::test_flush_notify_skips_strm_when_disabled",
    ),
    (
        "通知聚合键含年份与类型",
        'key = f"{title}|{year}|{season}|{type_key}"',
        'key = f"{title}|{season}"',
        "tests/test_tgofflinebot.py::test_notify_keys_isolate_season_show_and_year",
    ),
    (
        "待决策去重",
        'if source_path not in group["paths"]:',
        "if True:",
        "tests/test_tgofflinebot.py::test_pending_group_deduplicates_same_path",
    ),
    (
        "重复提交过滤",
        "if key and any(key in name or name in key for name in existing):",
        "if False:",
        "tests/test_tgofflinebot.py::test_filter_existing_links_skips_known_tasks",
    ),
    (
        "空目录清理",
        "if not self._cleanup_empty_dirs or self._is_scope_root(parent):",
        "if True:",
        "tests/test_tgofflinebot.py::test_remove_if_empty_deletes_directory",
    ),
    (
        "无任务时跳过巡检",
        "if not has_owned:",
        "if False:",
        "tests/test_tgofflinebot.py::test_sweep_skips_without_owned_tasks",
    ),
    (
        "落盘名插入广告词时的前缀匹配",
        "if len(key) >= 12 and key[:12] in path_key:",
        "if False:",
        "tests/test_tgofflinebot.py::test_owned_prefix_match_tolerates_ads_in_path",
    ),
    (
        "按历史 ID 反查源路径",
        'return str(getattr(record, "src", "") or "")',
        'return ""',
        "tests/test_tgofflinebot.py::test_history_source_fallback",
    ),
    (
        "记录清理的来源过滤",
        "                # 只处理名称能对上本插件来源标记的条目，避免误删同目录其它来源\n"
        "                if not self._is_owned_text(path):\n"
        "                    continue",
        "                if False:\n"
        "                    continue",
        "tests/test_tgofflinebot.py::test_purge_stale_records_only_touches_prefix",
    ),
    (
        "兼容字典事件载荷",
        "        if isinstance(data, dict):\n            return data.get(name, default)",
        "        if False:\n            return data.get(name, default)",
        "tests/test_tgofflinebot.py::test_complete_handles_dict_payload",
    ),
    (
        "空载荷按时间窗反查历史",
        "            fallback_record = self._recent_owned_history()",
        "            fallback_record = None",
        "tests/test_tgofflinebot.py::test_complete_uses_recent_history_when_payload_empty",
    ),
    (
        "补发通知去重",
        "            if key in self._notified_histories:\n                return",
        "            if False:\n                return",
        "tests/test_tgofflinebot.py::test_notify_from_recent_history_sends_and_dedups",
    ),
    (
        "巡检补发去重",
        "                if key in self._notified_histories:\n                    continue",
        "                if False:\n                    continue",
        "tests/test_tgofflinebot.py::test_sweep_recent_histories_sends_once",
    ),
    (
        "失败历史不当成入库",
        '            if getattr(record, "status", True) in (False, 0):\n                continue',
        "            if False:\n                continue",
        "tests/test_tgofflinebot.py::test_owned_histories_skip_failed_records",
    ),
    (
        "只删失败历史",
        '                if (record or {}).get("status") not in (False, 0):\n                    continue',
        "                if False:\n                    continue",
        "tests/test_tgofflinebot.py::test_purge_stale_records_only_touches_prefix",
    ),
    (
        "只补发最近一次提交之后",
        "        if self._last_submit_ts:\n            # 只补发本插件最近一次提交之后的记录，避免重启后把历史记录重发一遍\n            cutoff = max(cutoff, self._last_submit_ts - 60)",
        "        if False:\n            cutoff = max(cutoff, 0)",
        "tests/test_tgofflinebot.py::test_owned_histories_ignore_before_last_submit",
    ),
    (
        "群成员可点选项",
        '        if chat_type not in ("group", "supergroup") and user_id not in self._allowed_users:',
        "        if user_id not in self._allowed_users:",
        "tests/test_tgofflinebot.py::test_choice_callback_allows_group_member",
    ),
    (
        "STRM 未配置不猜路径",
        "        if not local_root or not pan_root:\n            # 未配置目录映射时不猜路径，避免误报「未检测到 STRM」\n            return None",
        "        if False:\n            return None",
        "tests/test_tgofflinebot.py::test_strm_target_requires_mapping",
    ),
    (
        "残留巡检空即休眠",
        "        if found_owned:\n"
        '            self._wake_residue_sweep("待整理目录里仍有本插件任务")\n'
        "            return\n"
        "        self._sleep_residue_sweep()",
        "        if found_owned:\n"
        '            self._wake_residue_sweep("待整理目录里仍有本插件任务")\n'
        "            return\n"
        "        pass",
        "tests/test_tgofflinebot.py::test_sweep_sleeps_when_no_owned_dir",
    ),
    (
        "在途任务时不休眠",
        "        if self._residue_in_flight():\n"
        "            return\n"
        "        with self._choice_lock:\n"
        "            was_idle = self._residue_idle",
        "        if False:\n"
        "            return\n"
        "        with self._choice_lock:\n"
        "            was_idle = self._residue_idle",
        "tests/test_tgofflinebot.py::test_sweep_stays_awake_while_offline_task_running",
    ),
    (
        "休眠期跳过定时巡检",
        "        if not self._residue_idle:\n"
        "            return True\n"
        "        if self._residue_idle_recheck <= 0:",
        "        if True:\n"
        "            return True\n"
        "        if self._residue_idle_recheck <= 0:",
        "tests/test_tgofflinebot.py::test_residue_sweep_recheck_interval",
    ),
    (
        "唤醒解除休眠",
        "            self._residue_idle = False\n"
        "            self._residue_idle_since = 0.0",
        "            pass\n"
        "            self._residue_idle_since = 0.0",
        "tests/test_tgofflinebot.py::test_sweep_wakes_on_new_submit",
    ),
    (
        "复查刷新休眠起点",
        "            self._residue_idle = True\n"
        "            self._residue_idle_since = time.time()",
        "            self._residue_idle = True\n"
        "            pass",
        "tests/test_tgofflinebot.py::test_residue_recheck_refreshes_idle_start",
    ),
]


def digest(path: str) -> str:
    """返回文件 SHA-256，用于确认恢复成功。"""
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def main() -> int:
    """逐个执行变异并汇总结果。"""
    original = open(TARGET, encoding="utf-8").read()
    baseline = digest(TARGET)
    caught = 0
    escaped = []
    skipped = []

    for name, old, new, node in MUTATIONS:
        count = original.count(old)
        if count != 1:
            print(f"[SKIP] {name}：锚点命中 {count} 次，应为 1 次")
            skipped.append(name)
            continue
        mutated = original.replace(old, new, 1)
        try:
            with open(TARGET, "w", encoding="utf-8") as handle:
                handle.write(mutated)
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-x", node],
                cwd=PLUGIN_DIR,
                capture_output=True,
                text=True,
                timeout=180,
            )
            if result.returncode != 0:
                print(f"[CAUGHT] {name}")
                caught += 1
            else:
                print(f"[ESCAPED] {name}：变异后测试仍通过")
                escaped.append(name)
        finally:
            with open(TARGET, "w", encoding="utf-8") as handle:
                handle.write(original)

    if digest(TARGET) != baseline:
        print("[FAIL] 源码恢复后与原始内容不一致")
        return 2

    total = len(MUTATIONS)
    print(
        f"\n变异 {total} 个：捕获 {caught}，逃逸 {len(escaped)}，跳过 {len(skipped)}"
    )
    if escaped:
        print("逃逸：" + "、".join(escaped))
    if skipped:
        print("跳过（按失败处理）：" + "、".join(skipped))
    return 0 if caught == total else 1


if __name__ == "__main__":
    sys.exit(main())
