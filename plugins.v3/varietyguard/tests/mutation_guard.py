#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
变异测试：攻击综艺正片守卫的关键判定，验证回归测试是否真能拦住。

设计原则：每个变异体都对应一处"真实可能写错"的防护，而不是随机改字符。
退出码约定与仓库其它插件一致：**有逃逸或有跳过即返回 1**。
"""

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(HERE)
TARGET = os.path.join(PLUGIN_DIR, "__init__.py")
BACKUP = "/tmp/varietyguard_backup.py"


# (名称, 原文, 变异后, 期望被捕获的说明)
MUTANTS = [
    (
        "作用域守卫失效：去掉媒体类型/分类判定（非综艺也被过滤）",
        """        if not self._in_scope(mediainfo):""",
        """        if False:""",
        "应导致 test_other_media_types_untouched / test_movie_media_type_untouched 失败",
    ),
    (
        "白名单失效：不再优先保留命中白名单的文件",
        """        allowed = self._match_first(self._allow_keywords, text)
        if allowed:
            return ACTION_ALLOW, allowed""",
        """        allowed = None
        if allowed:
            return ACTION_ALLOW, allowed""",
        "应导致 test_allowlist_wins_over_exclude 失败",
    ),
    (
        "强词判定反转：命中强词的文件反被保留",
        """        strong = self._match_first(self._exclude_keywords, text)
        if strong:
            return ACTION_SKIP, strong""",
        """        strong = self._match_first(self._exclude_keywords, text)
        if strong:
            return ACTION_KEEP, strong""",
        "应导致 test_variety_skips_non_main_episode 失败",
    ),
    (
        "静默语义丢失：全为非正片时不再改写为无候选跳过",
        """            new_checkpoint = dataclasses.replace(
                checkpoint, items=(), skip_reason=f"综艺非正片已全部跳过（{len(skipped)} 个文件）"
            )""",
        """            new_checkpoint = dataclasses.replace(checkpoint, items=())""",
        "应导致 test_all_non_main_rewrites_to_skip_reason 失败",
    ),
    (
        "试运行失效：试运行模式下真的执行过滤",
        """        if self._dry_run:
            self._record(
                title, skipped, applied=False, numbered=kept_numbered, full_skip=full_skip
            )""",
        """        if False:
            self._record(
                title, skipped, applied=False, numbered=kept_numbered, full_skip=full_skip
            )""",
        "应导致 test_dry_run_records_but_keeps_items 失败",
    ),
    (
        "试运行与正式统计混算：试运行命中计入「累计跳过」",
        """                counter_key = "total_skipped" if applied else "total_dry_run_hits\"""",
        """                counter_key = "total_skipped\"""",
        "应导致 test_dry_run_counted_separately 失败",
    ),
    (
        "整季跳过标记丢失：记录不再标记 scope=all",
        """                scope = "all" if full_skip else "partial\"""",
        """                scope = "partial\"""",
        "应导致 test_stats_and_records_written / test_full_skip_notifies_even_when_switch_off 失败",
    ),
    (
        "整季跳过不再强制通知：通知开关关闭时静默",
        """        if not skipped or not (self._notify or full_skip):""",
        """        if not skipped or not self._notify:""",
        "应导致 test_full_skip_notifies_even_when_switch_off 失败",
    ),
    (
        "词边界机制失效：包装函数直接返回裸关键词（MAXPLUS / StartUp 会被误伤）",
        """    parts = [re.escape(part) for part in re.split(r"[ ._-]+", keyword) if part]
    body = r"[ ._-]*".join(parts)
    return rf"(?<![A-Za-z]){body}(?![A-Za-z])"
""",
        """    return keyword
""",
        "应导致 test_default_boundary_does_not_hit_word_containing_strings 失败",
    ),
    (
        "零集号标记退化：收回无边界 E00（四位集号 S04E0012 会被误杀）",
        """    r"(?<![A-Za-z])E0+(?![0-9])",""",
        """    r"E00",""",
        "应导致 test_zero_episode_markers_only_match_zero_episode 失败",
    ),
    (
        "旧配置字面量不再迁移：E00 / EP00 原样留在词表里",
        """            replacement = _LEGACY_ZERO_EPISODE.get(value.strip().casefold())
            migrated.append(replacement or value)""",
        """            migrated.append(value)""",
        "应导致 test_legacy_zero_episode_literal_migrated 失败",
    ),
    (
        "歧义词降档失效：旧配置里的 Live / 巅峰 仍按强词处理",
        """            if canonical in ambiguous_canonical:
                continue""",
        """            if False:
                continue""",
        "应导致 test_legacy_ambiguous_word_downgraded_on_load 失败",
    ),
    (
        "歧义词不再参考集号证据：命中即跳过（回到 v1.0.x 的误杀行为）",
        """        if self._keep_numbered and self._has_episode_evidence(text):
            return ACTION_KEEP_NUMBERED, ambiguous""",
        """        if False:
            return ACTION_KEEP_NUMBERED, ambiguous""",
        "应导致 test_ambiguous_live_keeps_numbered_episode 失败",
    ),
    (
        "集号证据判定恒假：任何歧义词都被当成衍生内容",
        """        return any(pattern.search(text) for pattern in _EPISODE_EVIDENCE)""",
        """        return False""",
        "应导致 test_ambiguous_live_keeps_numbered_episode 失败",
    ),
    (
        "停用开关失效：补丁层残留时仍然过滤",
        """        if not self._enabled:
            # 停用开关优先于补丁链：补丁层因外部原因没摘干净时也不再过滤。
            return checkpoint""",
        """        if False:
            return checkpoint""",
        "应导致 test_patch_does_not_strip_foreign_wrapper 失败",
    ),
    (
        "补丁所有权判定失效：没有本插件层也当成已还原",
        """                if not layers:""",
        """                if False:""",
        "应导致 test_patch_does_not_strip_foreign_wrapper 失败",
    ),
    (
        "补丁多层残留只摘一层：旧实例的包装留在调用链上",
        """                while (
                    getattr(current, PATCH_TAG, None) == PATCH_TAG_NAME
                    and layers < PATCH_MAX_LAYERS
                ):""",
        """                while (
                    getattr(current, PATCH_TAG, None) == PATCH_TAG_NAME
                    and layers < 1
                ):""",
        "应导致 test_stacked_own_layers_unwound_together 失败",
    ),
    (
        "外部条目不再保留：整条覆盖用户手加的词",
        """            foreign = self._foreign_rule_entries(current, desired)
            merged = [item for item in foreign + desired.split("|") if item]""",
        """            foreign = self._foreign_rule_entries(current, desired)
            merged = [item for item in desired.split("|") if item]""",
        "应导致 test_rule_sync_mirrors_keywords_into_rule 失败",
    ),
    (
        "歧义词误写进规则：搜索阶段就会拒掉可能正确的正片",
        """            desired = "|".join(self._exclude_keywords)""",
        """            desired = "|".join(self._exclude_keywords + self._ambiguous_keywords)""",
        "应导致 test_rule_sync_only_writes_strong_words 失败",
    ),
    (
        "规则原文不再留档：覆盖后无法回滚",
        """            self._save_rule_snapshot(current)""",
        """            pass""",
        "应导致 test_rule_sync_saves_snapshot_before_overwrite 失败",
    ),
    (
        "合并后正则不再校验：坏词会让整条规则失效",
        """            try:
                re.compile(final)
            except re.error as err:
                logger.error("【综艺正片守卫】词表同步已跳过：合并后的正则不合法（%s）", err)
                return""",
        """            pass""",
        "应导致 test_rule_sync_skips_when_merged_regex_invalid 失败",
    ),
    (
        "异常不再放行：内部异常被抛出（会中断整理链）",
        """        except Exception as err:  # pragma: no cover - 兜底防御，定向测试覆盖
            logger.error("【综艺正片守卫】过滤失败，按原计划放行：%s", err, exc_info=True)
            return checkpoint""",
        """        except Exception as err:  # pragma: no cover
            logger.error("【综艺正片守卫】过滤失败：%s", err, exc_info=True)
            raise""",
        "应导致 test_fail_open_on_internal_error 失败",
    ),
    (
        "词表同步安全闸失效：词表异常（项数越界/含 | 或换行/正则非法）也照写目标规则",
        """        if not RULE_SYNC_MIN_ITEMS <= len(items) <= RULE_SYNC_MAX_ITEMS:""",
        """        if False:""",
        "应导致 test_rule_sync_guard_rejects_abnormal_keywords 失败",
    ),
    (
        "旧版零集号字面量不再清理：规则里残留 E00 / EP00 继续误杀四位集号",
        """        owned |= set(_LEGACY_ZERO_EPISODE)""",
        """        pass""",
        "应导致 test_rule_sync_replaces_legacy_zero_episode_literals 失败",
    ),
]


def run_tests() -> int:
    """用 unittest 跑插件回归测试并返回退出码。"""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [
            os.path.join(PLUGIN_DIR, "tests", "_stub_host"),
            PLUGIN_DIR,
            env.get("PYTHONPATH", ""),
        ]
    )
    completed = subprocess.run(
        [sys.executable, "-m", "unittest", "tests.test_varietyguard", "-q"],
        cwd=PLUGIN_DIR,
        env=env,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "").strip().splitlines()
        print("    " + "\n    ".join(tail[-4:]))
    return completed.returncode


def main() -> int:
    """逐个注入变异体并统计捕获情况；跳过等同于失败。"""
    with open(TARGET, encoding="utf-8") as handle:
        source = handle.read()
    shutil.copyfile(TARGET, BACKUP)

    caught = escaped = skipped = 0
    escaped_names = []
    try:
        for index, mutant in enumerate(MUTANTS, 1):
            if len(mutant) == 4:
                name, old, new, expect = mutant
                pairs = [(old, new)]
            else:
                name, pairs, expect = mutant
            missing = [old for old, _ in pairs if old not in source]
            if missing:
                skipped += 1
                print(f"[{index:2d}] ⚠️  跳过（定位失败，{len(missing)} 段未匹配）：{name}")
                continue
            mutated = source
            for old, new in pairs:
                mutated = mutated.replace(old, new, 1)
            with open(TARGET, "w", encoding="utf-8") as handle:
                handle.write(mutated)
            code = run_tests()
            if code != 0:
                caught += 1
                print(f"[{index:2d}] ✅ 已捕获：{name}")
            else:
                escaped += 1
                escaped_names.append(name)
                print(f"[{index:2d}] ❌ 逃逸：{name} —— {expect}")
    finally:
        with open(TARGET, "w", encoding="utf-8") as handle:
            handle.write(source)
        shutil.copyfile(BACKUP, TARGET)

    print(
        f"\n结果：捕获 {caught} / 逃逸 {escaped} / 跳过 {skipped} / 合计 {caught + escaped + skipped}"
    )
    if escaped_names:
        print("逃逸清单：" + "；".join(escaped_names))
    if skipped:
        print(f"⚠️  {skipped} 个跳过（防护未生效！）")
    if escaped_names or skipped:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
