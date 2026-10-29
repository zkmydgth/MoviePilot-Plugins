#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
变异测试：验证订阅清理、完结判定、匹配收敛、通知队列与页面提示的测试能拦住缺陷。

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
DEFAULT_TARGET = "__init__.py"
TEST_NODE = "tests/test_doubanscorewatch.py::DoubanScoreWatchTestCase::{}"

# (名称, 原始片段, 变异片段, 目标测试节点[, 目标文件])
MUTATIONS = [
    (
        "退订不删条目",
        "            entries.pop(key, None)\n            removed += 1",
        "            removed += 0",
        TEST_NODE.format("test_deleted_subscription_entry_is_removed"),
    ),
    (
        "完结条目也删",
        "            if self._is_subscription_completed(entry):\n"
        "                entry[\"sub_completed\"] = True",
        "            if False:\n"
        "                entry[\"sub_completed\"] = True",
        TEST_NODE.format("test_completed_subscription_entry_is_kept"),
    ),
    (
        "完结标记不跳过历史查询",
        "        if entry.get(\"sub_completed\"):\n            return True",
        "        if False:\n            return True",
        TEST_NODE.format("test_completed_entry_does_not_query_history_again"),
    ),
    (
        "历史读取失败时删除条目",
        "            logger.warning(f\"【豆瓣开分】读取订阅完成历史失败，保留 {entry.get('title')}：{error}\")\n"
        "            return True",
        "            logger.warning(f\"【豆瓣开分】读取订阅完成历史失败，保留 {entry.get('title')}：{error}\")\n"
        "            return False",
        TEST_NODE.format("test_history_failure_keeps_entry"),
    ),
    (
        "重新订阅不清完结标记",
        "            entry[\"sub_completed\"] = False",
        "            entry[\"sub_completed\"] = True",
        TEST_NODE.format("test_resubscribe_clears_completed_flag"),
    ),
    (
        "完结时间早于订阅起始仍保留",
        "        return finished_at >= seen_at",
        "        return True",
        TEST_NODE.format("test_stale_completion_history_does_not_keep_entry"),
    ),
    (
        "缺订阅起始时间按删除处理",
        "        if seen_at is None:\n            return True",
        "        if seen_at is None:\n            return False",
        TEST_NODE.format("test_missing_seen_timestamp_keeps_entry"),
    ),
    (
        "完结判定只查无季",
        "        for season in seasons or [None]:",
        "        for season in [None]:",
        TEST_NODE.format("test_completion_finished_at_queries_each_season"),
    ),
    (
        "完结判定丢剧集组",
        "                    episode_group=entry.get(\"episode_group\") or None,",
        "                    episode_group=None,",
        TEST_NODE.format("test_completion_finished_at_queries_each_season"),
    ),
    (
        "不记录订阅出现时间",
        "            entry[\"sub_seen_at\"] = seen_at",
        "            pass",
        TEST_NODE.format("test_sync_stamps_seen_timestamp"),
    ),
    (
        "时间解析只认秒级格式",
        "        for pattern in (\"%Y-%m-%d %H:%M:%S\", \"%Y-%m-%d %H:%M\", \"%Y-%m-%d\"):",
        "        for pattern in (\"%Y-%m-%d %H:%M:%S\",):",
        TEST_NODE.format("test_parse_timestamp_accepts_host_formats"),
    ),
    (
        "条目之间不画分隔线",
        "            group[-1] = self._with_divider(group[-1])",
        "            pass",
        TEST_NODE.format("test_page_draws_divider_between_entries"),
    ),
    (
        "摘要删除数写死 0",
        "                f\"同步 {synced} 条订阅，删除 {removed} 条已退订，\"",
        "                f\"同步 {synced} 条订阅，删除 0 条已退订，\"",
        TEST_NODE.format("test_deleted_entry_not_checked_in_round"),
    ),
    (
        "候选不校验标题相关",
        "    related = [item for item in base if title_related(item.get(\"title\"), title)]",
        "    related = list(base)",
        TEST_NODE.format("test_same_year_unrelated_title_not_bound"),
        "matcher.py",
    ),
    (
        "季号只增不减",
        "            entries[key][\"seasons\"] = sorted(seasons)",
        "            pass",
        TEST_NODE.format("test_seasons_rebuilt_from_subscriptions"),
    ),
    (
        "退避时间不作为判断",
        "        return (now or datetime.now()) >= planned",
        "        return True",
        TEST_NODE.format("test_match_backoff_skips_next_round"),
    ),
    (
        "匹配失败不写退避时间",
        "        entry[\"next_try\"] = moment.strftime(\"%Y-%m-%d %H:%M:%S\")",
        "        entry[\"next_try\"] = \"\"",
        TEST_NODE.format("test_match_failure_defers_next_try"),
    ),
    (
        "已闭合绑定仍查豆瓣",
        "            if not self._keep_tracking and binding.get(\"closed\"):\n                continue",
        "            if False:\n                continue",
        TEST_NODE.format("test_closed_binding_not_checked"),
    ),
    (
        "超期绑定仍查豆瓣",
        "            if self._keep_tracking and self._expired(binding):\n                continue",
        "            if False:\n                continue",
        TEST_NODE.format("test_expired_binding_not_checked"),
    ),
    (
        "基线确认不清理标记",
        "                # 当前没有有效评分：基线确认完成，之后出现评分就按开分通知\n"
        "                binding.pop(\"baseline_pending\", None)",
        "                # 当前没有有效评分：基线确认完成，之后出现评分就按开分通知\n"
        "                pass",
        TEST_NODE.format("test_baseline_pending_confirms_open_score"),
    ),
    (
        "基线待确认不抑制补发",
        "            if binding.get(\"baseline_pending\"):",
        "            if False:",
        TEST_NODE.format("test_baseline_pending_suppresses_notice_when_scored"),
    ),
    (
        "冲刷覆盖新入队通知",
        "            state[\"pending_notices\"] = (failed + pending)[-100:]",
        "            state[\"pending_notices\"] = failed[-100:]",
        TEST_NODE.format("test_notice_enqueued_during_send_is_kept"),
    ),
    (
        "通知失败不设上限",
        "                if attempts >= self._NOTICE_MAX_ATTEMPTS:",
        "                if False:",
        TEST_NODE.format("test_notice_retry_backoff_and_limit"),
    ),
    (
        "静默单边填写视为关闭",
        "        return self._in_window(now, self._quiet_start or \"00:00\", self._quiet_end or \"23:59\")",
        "        return False",
        TEST_NODE.format("test_quiet_single_side_uses_defaults"),
    ),
    (
        "配置删行不解绑",
        "                if not binding.get(\"config_key\") or str(binding.get(\"douban_id\")) in wanted",
        "                if True",
        TEST_NODE.format("test_manual_binding_removed_from_config_unbinds"),
    ),
    (
        "同步失败不记录",
        "            self._record_sync_error(f\"读取订阅失败：{error}\")",
        "            pass",
        TEST_NODE.format("test_sync_error_recorded"),
    ),
    (
        "连续失败不熔断",
        "                if failures >= self._FAILURE_BREAKER:",
        "                if False:",
        TEST_NODE.format("test_failure_breaker_stops_round"),
    ),
    (
        "旧删除态不下线",
        "            if (state[\"entries\"][key] or {}).get(\"status\") == \"removed\":",
        "            if False:",
        TEST_NODE.format("test_legacy_removed_entries_dropped"),
    ),
    (
        "零匹配不计数",
        "                state[\"zero_match_rounds\"] = int(state.get(\"zero_match_rounds\") or 0) + 1",
        "                state[\"zero_match_rounds\"] = 0",
        TEST_NODE.format("test_zero_match_rounds_counted"),
    ),
]


def digest(path: str) -> str:
    """返回文件 SHA-256，用于确认恢复成功。"""
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def main() -> int:
    """逐个执行变异并汇总结果。"""
    originals = {}
    baselines = {}
    for entry in MUTATIONS:
        relative = entry[4] if len(entry) > 4 else DEFAULT_TARGET
        path = os.path.join(PLUGIN_DIR, relative)
        if relative not in originals:
            with open(path, encoding="utf-8") as handle:
                originals[relative] = handle.read()
            baselines[relative] = digest(path)
    caught = 0
    escaped = []
    skipped = []

    for entry in MUTATIONS:
        name, old, new, node = entry[:4]
        relative = entry[4] if len(entry) > 4 else DEFAULT_TARGET
        path = os.path.join(PLUGIN_DIR, relative)
        original = originals[relative]
        count = original.count(old)
        if count != 1:
            print(f"[SKIP] {name}：锚点命中 {count} 次，应为 1 次")
            skipped.append(name)
            continue
        mutated = original.replace(old, new, 1)
        try:
            with open(path, "w", encoding="utf-8") as handle:
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
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(original)

    for relative, baseline in baselines.items():
        if digest(os.path.join(PLUGIN_DIR, relative)) != baseline:
            print(f"[FAIL] {relative} 恢复后与原始内容不一致")
            return 2

    total = len(MUTATIONS)
    print(f"\n变异 {total} 个：捕获 {caught}，逃逸 {len(escaped)}，跳过 {len(skipped)}")
    if escaped:
        print("逃逸：" + "、".join(escaped))
    if skipped:
        print("跳过（按失败处理）：" + "、".join(skipped))
    return 0 if caught == total else 1


if __name__ == "__main__":
    sys.exit(main())
