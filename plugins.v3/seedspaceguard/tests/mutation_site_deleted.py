#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
变异测试（Transmission 清单预取修复 + 站点删种回收专项，v3.6.0）。

背景：2026-11-06 实测 tr 里有 26 个「No data found」种子——文件被空间清理
删掉了，种子却一直留在下载器里。根因是批量预取用 ``getattr(item, "files")``
取清单，而 transmission_rpc 的 Torrent 没有该属性（字段在 ``fields`` 字典里），
于是每个种子都被写成空清单 → 判定退回「扫整个内容目录」→ 同目录兄弟种子的
文件把空壳永久挡住。

本脚本逐项验证新逻辑的测试防护**真的**能拦住回归，而不是空过：

  1. 空清单进缓存 → 空壳判定退回扫目录（本轮 bug 本身）；
  2. 字段字典读取失效 → 批量预取再次拿不到清单；
  3. 站点删种开关失效 → 用户关掉开关仍然摘种；
  4. ⚠️ 站点删种连带删文件 → 数据丢失级（本该只摘种子）；
  5. tracker 公告成功仍被判删种 → 多站辅种被误摘；
  6. 本地错误（error=3）被当成站点删种 → 空壳链路被抢跑；
  7. 没有任何 tracker 公告时的保守垫被打穿 → 单凭一段本地错误串就摘种。

用法（在插件目录下）::

    python3 tests/mutation_site_deleted.py
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(HERE)
TARGET = os.path.join(PLUGIN_DIR, "__init__.py")


MUTANTS = [
    (
        "空清单写入预取缓存（退回扫目录，空壳永久卡住）",
        """            if not names:
                # ⚠️ 空清单**不进缓存**。批量接口没返回 files 字段（宿主没请求、""",
        """            if False:
                # ⚠️ 空清单**不进缓存**。批量接口没返回 files 字段（宿主没请求、""",
        "应被 test_2_empty_file_list_not_cached / test_4_e2e... 捕获",
    ),
    (
        "批量预取不再读 fields 字段字典",
        """        fields = getattr(item, "fields", None)
        if isinstance(fields, dict) and name in fields:
            return fields.get(name)""",
        """        fields = getattr(item, "fields", None)
        if False:
            return fields.get(name)""",
        "应被 test_1_prefetch_reads_fields_dict / test_4_e2e... 捕获",
    ),
    (
        "站点删种开关失效（关闭后仍然摘种）",
        """            site_reason = (
                str(cand.get("site_deleted") or "")
                if self._reap_site_deleted else ""
            )""",
        """            site_reason = (
                str(cand.get("site_deleted") or "")
                if True else ""
            )""",
        "应被 test_11_switch_off_keeps_seed 捕获",
    ),
    (
        "⚠️ 站点删种连带删除文件（数据丢失级）",
        """                    ok = site_module.remove_torrents(
                        hashs=[hash_str],
                        delete_file=False,
                        downloader=site_downloader,
                    ) if site_module else False""",
        """                    ok = site_module.remove_torrents(
                        hashs=[hash_str],
                        delete_file=True,
                        downloader=site_downloader,
                    ) if site_module else False""",
        "应被 test_10_site_deleted_reaped_files_kept 捕获",
    ),
    (
        "任一 tracker 公告成功不再放行（多站辅种被误摘）",
        """        if any(text.casefold() == "success" for text in results):
            return \"\"""",
        """        if False:
            return \"\"""",
        "应被 test_6_success_announce_keeps_seed 捕获",
    ),
    (
        "本地错误（error=3）被当成站点删种",
        """        if code not in (0, 2):
            return \"\"""",
        """        if False:
            return \"\"""",
        "应被 test_7_local_error_is_not_site_deleted 捕获",
    ),
    (
        "无 tracker 公告时的保守垫被打穿",
        """                    if not results and code != 2:
                        return \"\"""",
        """                    if False:
                        return \"\"""",
        "应被 test_9_no_announce_result_keeps_conservative 捕获",
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
    """逐项注入缺陷并确认测试会失败。"""
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
        print("❌ 存在被跳过的变异体：定义与源码不匹配即等于该缺陷无人守护")
        return 1
    print("🎉 全部变异被捕获，测试防护有效")
    return 0


if __name__ == "__main__":
    sys.exit(main())
