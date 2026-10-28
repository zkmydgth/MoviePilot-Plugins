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
        "空目录清理开关",
        "if not self._cleanup_empty_dirs:",
        "if True:",
        "tests/test_tgofflinebot.py::test_remove_if_empty_deletes_directory",
    ),
    (
        "空目录清理保护",
        "if self._is_scope_root(parent) or self._is_target_root(parent):"
        "\n            return False\n        dir_item = self._get_existing_dir(parent)",
        "if False:\n            return False\n        dir_item = self._get_existing_dir(parent)",
        "tests/test_tgofflinebot.py::test_target_root_skips_empty_dir_cleanup",
    ),
    (
        "无任务时跳过巡检",
        "if not has_owned and not target:",
        "if False:",
        "tests/test_tgofflinebot.py::test_sweep_skips_without_owned_tasks",
    ),
    (
        "专属目录前缀判定",
        'if target and str(path).startswith(target + "/"):',
        "if False:",
        "tests/test_tgofflinebot.py::test_owned_path_uses_target_prefix",
    ),
    (
        "专属目录子目录视为本插件",
        "if not (in_target or self._is_owned_text(name)):",
        "if not self._is_owned_text(name):",
        "tests/test_tgofflinebot.py::test_sweep_cleans_target_subdirs_without_name_marker",
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
    (
        "存储兜底候选过滤本地",
        'return [item for item in ordered if item and item != "local"]',
        "return [item for item in ordered if item]",
        "tests/test_tgofflinebot.py::test_storage_candidates_never_fall_back_to_local",
    ),
    (
        "存储兜底遍历",
        "                item = chain.get_folder(storage, Path(path))\n"
        "                if item:\n"
        "                    self._note_storage_fallback(storage)\n"
        "                    return item\n",
        "                item = chain.get_folder(storage, Path(path))\n"
        "                if False:\n"
        "                    self._note_storage_fallback(storage)\n"
        "                    return item\n",
        "tests/test_tgofflinebot.py::test_get_dir_item_falls_back_to_supported_storage",
    ),
    (
        "只整理已完成任务",
        "            if status != 2:",
        "            if False:",
        "tests/test_tgofflinebot.py::test_incomplete_task_not_organized",
    ),
    (
        "单文件独占才整理根目录",
        "            elif path and len(children) == 1:",
        "            elif path:",
        "tests/test_tgofflinebot.py::test_single_file_keeps_pending_when_others_exist",
    ),
    (
        "整理成功后注销登记",
        "                self._pending_transfer.pop(info_hash, None)",
        "                self._pending_transfer.get(info_hash)",
        "tests/test_tgofflinebot.py::test_multi_file_task_organizes_subdir",
    ),
    (
        "文件判定用真实类型",
        '        if str(getattr(item, "type", "") or "") != "file":\n            return ""',
        '        if False:\n            return ""',
        "tests/test_tgofflinebot.py::test_parent_if_file_only_for_files",
    ),
    (
        "空目录清理不创建目录",
        "        dir_item = self._get_existing_dir(parent)\n"
        "        if not dir_item:\n"
        "            # 已经不在了，视为完成\n"
        "            return True",
        "        dir_item = self._get_dir_item(parent)\n"
        "        if not dir_item:\n"
        "            # 已经不在了，视为完成\n"
        "            return True",
        "tests/test_tgofflinebot.py::test_remove_if_empty_does_not_create_missing_dir",
    ),
    (
        "只读目录判定需校验类型",
        '        if str(getattr(item, "type", "") or "") != "dir":\n            return None\n        return item',
        "        if False:\n            return None\n        return item",
        "tests/test_tgofflinebot.py::test_get_existing_dir_never_creates",
    ),
    (
        "目标目录不可用拒绝提交",
        "        if not ready:\n            return False, note",
        "        if False:\n            return False, note",
        "tests/test_tgofflinebot.py::test_submit_rejects_when_target_unavailable",
    ),
    (
        "目录缺失先清缓存",
        "        self._clear_path_caches()\n        self._ensure_target_dir()",
        "        self._ensure_target_dir()",
        "tests/test_tgofflinebot.py::test_ensure_submit_target_clears_cache_and_rebuilds",
    ),
    (
        "手动整理提醒合并发送",
        "            self._organize_notices.append(name)",
        "            self._send_notify(name)",
        "tests/test_tgofflinebot.py::test_organize_notices_merge_into_one_message",
    ),
    (
        "选择消息失败后重发",
        "        if message_id is None:\n            # 首条消息没发出去",
        "        if False:\n            # 首条消息没发出去",
        "tests/test_tgofflinebot.py::test_choice_message_resent_after_send_failure",
    ),
    (
        "AI 缓存键含集号",
        "f\"{snapshot['season']}|{snapshot['episode']}\"",
        "f\"{snapshot['season']}\"",
        "tests/test_tgofflinebot.py::test_ai_cache_key_includes_episode",
    ),
    (
        "失败任务纳入清理",
        "            if status in (1, 3):",
        "            if False:",
        "tests/test_tgofflinebot.py::test_cleanup_deletes_failed_task_after_threshold",
    ),
    (
        "失败任务按阈值判定",
        "                if self._offline_failed_timeout <= 0 or age < failed_threshold:",
        "                if self._offline_failed_timeout <= 0:",
        "tests/test_tgofflinebot.py::test_cleanup_keeps_failed_task_before_threshold",
    ),
    (
        "有待整理登记时继续巡检",
        "        if expired and not has_pending:",
        "        if expired:",
        "tests/test_tgofflinebot.py::test_cleanup_polls_while_pending_even_after_window",
    ),
    (
        "未完成任务续期登记",
        "                        self._pending_transfer[info_hash] = (\n"
        "                            now + PENDING_TRANSFER_TTL_SECONDS\n"
        "                        )",
        "                        self._pending_transfer.get(info_hash)",
        "tests/test_tgofflinebot.py::test_cleanup_extends_pending_deadline_for_unfinished",
    ),
    (
        "删除离线任务去重",
        "            if not info_hash or self._recently_deleted(info_hash):",
        "            if not info_hash:",
        "tests/test_tgofflinebot.py::test_offline_cleanup_merges_and_dedupes",
    ),
    (
        "删除通知合并",
        "        if removed:",
        "        if False:",
        "tests/test_tgofflinebot.py::test_offline_cleanup_merges_and_dedupes",
    ),
    (
        "删除后注销待整理登记",
        "                if self._pending_transfer.pop(info_hash.lower(), None) is not None:",
        "                if False:",
        "tests/test_tgofflinebot.py::test_offline_cleanup_drops_pending_registration",
    ),
    (
        "残留扫描进入子目录",
        '                    elif kind == "dir" and depth + 1 < RESIDUE_SCAN_MAX_DEPTH:',
        "                    elif False:",
        "tests/test_tgofflinebot.py::test_collect_residue_entries_walks_subdirs",
    ),
    (
        "目标缺失登记宽限期",
        "            if now - first < ORGANIZE_GIVEUP_SECONDS:",
        "            if False:",
        "tests/test_tgofflinebot.py::test_organize_gives_up_when_target_missing",
    ),
    (
        "空目录重试出队",
        "            if self._remove_if_empty(path):\n                finished.append(path)",
        "            if False:\n                finished.append(path)",
        "tests/test_tgofflinebot.py::test_retry_dir_removals_deletes_when_empty",
    ),
    (
        "空目录重试期限",
        "            if deadline <= now:",
        "            if False:",
        "tests/test_tgofflinebot.py::test_retry_dir_removals_drops_after_deadline",
    ),
    (
        "清理后入队重试",
        "        self._enqueue_dir_removal(ordered_dirs + [parent])",
        "        self._enqueue_dir_removal([])",
        "tests/test_tgofflinebot.py::test_cleanup_residue_enqueues_dir_removal",
    ),
    (
        "通知失败重试一次",
        "        if message_id is None:\n            # Telegram 抖动时常一次性失败",
        "        if False:\n            # Telegram 抖动时常一次性失败",
        "tests/test_tgofflinebot.py::test_send_notify_retries_once",
    ),
    (
        "体积后缀回填归一化",
        '        normalized = {\n'
        '            re.sub(r"[（(][^（()）]*[)）]\\s*$", "", item).strip()\n'
        "            for item in delete_set\n"
        "        }",
        "        normalized = set()",
        "tests/test_tgofflinebot.py::test_ai_residue_verdict_matches_sized_entries",
    ),
    (
        "提交后仍登记来源",
        "        if target and not self._get_existing_dir(target):",
        "        if False:",
        "tests/test_tgofflinebot.py::test_submit_warns_when_target_missing_after_success",
    ),
    (
        "重跑拒绝整目录",
        "            if children is None or len(children) != 1:",
        "            if False:",
        "tests/test_tgofflinebot.py::test_retry_transfer_refuses_whole_root",
    ),
    (
        "含正片不入队",
        "        if deleted or not files:",
        "        if True:",
        "tests/test_tgofflinebot.py::test_cleanup_residue_skips_enqueue_when_files_remain",
    ),
    (
        "容器目录不入队",
        "                if key in roots or (target and key == target):",
        "                if False:",
        "tests/test_tgofflinebot.py::test_enqueue_dir_removal_skips_container_roots",
    ),
    (
        "停止时结束巡检线程",
        "        if watch and watch.is_alive() and watch is not threading.current_thread():",
        "        if False:",
        "tests/test_tgofflinebot.py::test_stop_service_joins_watch_thread",
    ),
    (
        "任务消失也注销登记",
        "        for info_hash in [item for item in pending if item not in present]:",
        "        for info_hash in []:",
        "tests/test_tgofflinebot.py::test_pending_dropped_when_missing_from_list",
    ),
    (
        "预检范围包含目标目录",
        "        target = self._effective_target_path()\n"
        '        if target and (path == target or path.startswith(target + "/")):',
        "        target = self._effective_target_path()\n"
        "        if False:",
        "tests/test_tgofflinebot.py::test_precheck_scope_includes_target",
    ),
    (
        "重复过滤单向匹配",
        "            if key and any(key == name or name.startswith(key) for name in existing):",
        "            if key and any(key in name or name in key for name in existing):",
        "tests/test_tgofflinebot.py::test_filter_existing_links_ignores_reverse_substring",
    ),
    (
        "按钮校验群授权",
        '        if (\n            chat_type in ("group", "supergroup")\n'
        '            and str(chat_id or "") not in self._allowed_users',
        '        if (\n            False\n'
        '            and str(chat_id or "") not in self._allowed_users',
        "tests/test_tgofflinebot.py::test_choice_callback_rejects_unauthorized_group",
    ),
    (
        "空目录开关关闭视为完成",
        "            # 不清理空目录：视为完成，避免重试队列白占一小时\n            return True",
        "            # 不清理空目录：视为完成，避免重试队列白占一小时\n            return False",
        "tests/test_tgofflinebot.py::test_remove_if_empty_counts_done_when_switch_off",
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
