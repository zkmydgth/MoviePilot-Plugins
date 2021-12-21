#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
变异测试：攻击综艺正片守卫的六处关键判定，验证回归测试是否真能拦住。

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
        """        if not self._in_scope(mediainfo):
            return checkpoint""",
        """        if False:
            return checkpoint""",
        "应导致 test_other_media_types_untouched / test_movie_media_type_untouched 失败",
    ),
    (
        "白名单失效：不再优先保留命中白名单的文件",
        """            allowed = self._match_first(self._allow_keywords, text)
            if allowed:
                kept.append(item)
                continue""",
        """            allowed = None
            if allowed:
                kept.append(item)
                continue""",
        "应导致 test_allowlist_wins_over_exclude 失败",
    ),
    (
        "排除判定反转：命中关键词的文件反被保留",
        """            hit = self._match_first(self._exclude_keywords, text)
            if hit:
                skipped.append((text, hit))
            else:
                kept.append(item)""",
        """            hit = self._match_first(self._exclude_keywords, text)
            if hit:
                kept.append(item)
            else:
                skipped.append((text, hit or ""))""",
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
            self._record(title, skipped, applied=False)""",
        """        if False:
            self._record(title, skipped, applied=False)""",
        "应导致 test_dry_run_records_but_keeps_items 失败",
    ),
    (
        "默认词表退化：Plus 去掉词边界（会误伤 MAXPLUS 等发行组名）",
        """    r"(?<![A-Za-z])Plus(?![A-Za-z])",""",
        """    "Plus",""",
        "应导致 test_default_plus_keyword_does_not_hit_release_group 失败",
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
