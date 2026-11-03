#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
变异测试（空壳扫描性能治理）：验证新增性能分支被测试真正锁住。

覆盖 v3.2.0 新增的六类行为：批量预取、清单缓存、熔断、预算、空间充足
降频、增量缓存、轮内快照、删种批量复核。每个变异体只跑
``tests/test_perf_optimization.py``，单项秒级。

用法（在插件目录下）::

    python3 tests/mutation_perf.py
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

BATCH_COND_OLD = (
    "            if client is not None and hasattr(client, \"get_torrents\"):\n"
    "                self._prefetch_seed_files_batch(name, client, items)\n"
)
BATCH_COND_NEW = (
    "            if False:\n"
    "                self._prefetch_seed_files_batch(name, client, items)\n"
)

BATCH_FIELDS_OLD = (
    "            torrents = client.get_torrents(\n"
    "                ids=list(hashes), arguments=[\"hashString\", \"files\"],\n"
    "                timeout=(3, 20),\n"
    "            )\n"
)
BATCH_FIELDS_NEW = (
    "            torrents = client.get_torrents(\n"
    "                ids=list(hashes), arguments=[\"hashString\"],\n"
    "                timeout=(3, 20),\n"
    "            )\n"
)

CACHE_HIT_OLD = (
    "        cache = self.__dict__.get(\"_seed_files_cache\")\n"
    "        if isinstance(cache, dict) and hash_str in cache:\n"
)
CACHE_HIT_NEW = (
    "        cache = self.__dict__.get(\"_seed_files_cache\")\n"
    "        if False:\n"
)

BREAKER_OPEN_OLD = (
    "            if (self._rpc_failures >= ORPHAN_RPC_FAILURE_LIMIT\n"
    "                    and not self._rpc_open):\n"
    "                self._rpc_open = True\n"
    "                opened_at = self._rpc_failures\n"
)
BREAKER_OPEN_NEW = (
    "            if False:\n"
    "                self._rpc_open = True\n"
    "                opened_at = self._rpc_failures\n"
)

BREAKER_LOOP_OLD = (
    "            if self._rpc_open:\n"
    "                remaining = max(0, len(candidates) - stats[\"checked\"])\n"
    "                stats[\"skipped\"] += remaining\n"
)
BREAKER_LOOP_NEW = (
    "            if False:\n"
    "                remaining = max(0, len(candidates) - stats[\"checked\"])\n"
    "                stats[\"skipped\"] += remaining\n"
)

BUDGET_OLD = (
    "            if self._scan_deadline and time.monotonic() > self._scan_deadline:\n"
    "                remaining = max(0, len(candidates) - stats[\"checked\"])\n"
)
BUDGET_NEW = (
    "            if False:\n"
    "                remaining = max(0, len(candidates) - stats[\"checked\"])\n"
)

QUIET_OLD = "        if not space_tight and not dry_run and not self._orphan_scan_due():\n"
QUIET_NEW = "        if False:\n"

PREVIEW_OLD = "        if not space_tight and not dry_run and not self._orphan_scan_due():\n"
PREVIEW_NEW = "        if not space_tight and not self._orphan_scan_due():\n"

MTIME_OLD = (
    "                if os.stat(path).st_mtime > stamp:\n"
    "                    cache.pop(hash_str, None)\n"
    "                    return False\n"
)
MTIME_NEW = (
    "                if False:\n"
    "                    cache.pop(hash_str, None)\n"
    "                    return False\n"
)

PREFETCH_BUDGET_OLD = (
    "            if self._prefetch_blocked():\n"
    "                return\n"
)
PREFETCH_BUDGET_NEW = "            if False:\n                return\n"

ALIVE_SIG_OLD = (
    "        return str(item.get(\"sig\") or \"\") == self._orphan_alive_signature(cand)\n"
)
ALIVE_SIG_NEW = "        return False\n"

DRY_FLUSH_OLD = (
    "        if not dry_run:\n"
    "            self._orphan_alive_flush()\n"
)
DRY_FLUSH_NEW = "        self._orphan_alive_flush()\n"

VERIFY_BATCH_OLD = (
    "                found = method(hashs=list(hashes), include_all_tags=True)\n"
)
VERIFY_BATCH_NEW = (
    "                found = method(hashs=list(hashes)[:1], include_all_tags=True)\n"
)

SNAPSHOT_OLD = (
    "        if hit and hit.get(\"module\") is not None:\n"
    "            self._resolved_downloader_name = str(hit.get(\"downloader\") or \"\")\n"
    "            return hit.get(\"module\")\n"
)
SNAPSHOT_NEW = (
    "        if False:\n"
    "            self._resolved_downloader_name = str(hit.get(\"downloader\") or \"\")\n"
    "            return hit.get(\"module\")\n"
)

GUARD_OLD = (
    "            hash_str = str(cand.get(\"hash\") or \"\")\n"
    "            if not hash_str:\n"
    "                continue\n"
    "            stats[\"checked\"] += 1\n"
)
GUARD_NEW = (
    "            hash_str = str(cand.get(\"hash\") or \"\")\n"
    "            if not hash_str:\n"
    "                continue\n"
    "            if int(cand.get(\"added\") or 0) >= time.time() - self._recent_skip_days * 86400:\n"
    "                continue\n"
    "            stats[\"checked\"] += 1\n"
)


MUTANTS = [
    ("transmission 不走批量接口", BATCH_COND_OLD, BATCH_COND_NEW,
     "整批种子退化为并发逐条查询 → 批量请求断言失败"),
    ("批量请求不带 files 字段", BATCH_FIELDS_OLD, BATCH_FIELDS_NEW,
     "拿不到文件清单 → 空壳判定无法回收 → 回收数断言失败"),
    ("批量清单缓存不生效", CACHE_HIT_OLD, CACHE_HIT_NEW,
     "预取结果被忽略 → 每个种子再查一次 → 调用次数断言失败"),
    ("熔断阈值判断被移除", BREAKER_OPEN_OLD, BREAKER_OPEN_NEW,
     "连续失败后不熔断 → 断言 _rpc_open 失败"),
    ("熔断后仍继续判定", BREAKER_LOOP_OLD, BREAKER_LOOP_NEW,
     "剩余候选不会被跳过 → skipped 断言失败"),
    ("单轮预算判断被移除", BUDGET_OLD, BUDGET_NEW,
     "预算过期仍继续判定 → checked 断言失败"),
    ("空间充足降频判断被移除", QUIET_OLD, QUIET_NEW,
     "空间充足时仍全量扫描 → 枚举次数断言失败"),
    ("增量缓存签名校验失效", ALIVE_SIG_OLD, ALIVE_SIG_NEW,
     "缓存永不命中 → cached 断言失败"),
    ("试运行也写增量缓存", DRY_FLUSH_OLD, DRY_FLUSH_NEW,
     "试运行污染缓存 → 断言 get_data 为空失败"),
    ("删种复核退化为单 hash", VERIFY_BATCH_OLD, VERIFY_BATCH_NEW,
     "复核只查第一个 hash → 批量查询断言失败"),
    ("删种定位不走轮内快照", SNAPSHOT_OLD, SNAPSHOT_NEW,
     "重新逐个下载器探测 → 枚举次数断言失败"),
    ("空壳回收被保护期拦住", GUARD_OLD, GUARD_NEW,
     "刚添加的空壳种子被跳过 → 回收数断言失败（空壳不受保护期限制）"),
    ("试运行被降频拦截", PREVIEW_OLD, PREVIEW_NEW,
     "试运行返回 0 个 → 预告失真，checked 断言失败"),
    ("缓存不随目录变化失效", MTIME_OLD, MTIME_NEW,
     "文件已删仍命中缓存 → cached 断言失败（空壳回收被推迟）"),
    ("预取不受预算约束", PREFETCH_BUDGET_OLD, PREFETCH_BUDGET_NEW,
     "预算过期仍发起远程调用 → 调用记录非空、断言失败"),
    # ---- v3.3.0 审计修复 ----
    (
        "缓存失效条件被移除：目录已消失仍命中缓存",
        "        path = str(cand.get(\"path\") or \"\")\n"
        "        if path:\n",
        "        path = str(cand.get(\"path\") or \"\")\n"
        "        if False:\n",
        "目录消失后仍命中缓存 → 空壳不被回收、cached 断言失败",
    ),
    (
        "空间不足时仍复用缓存",
        "            if not dry_run and not space_tight and self._orphan_alive_cached(cand):\n",
        "            if not dry_run and self._orphan_alive_cached(cand):\n",
        "空间紧张时不复测 → cached 断言失败（应强制复核）",
    ),
    (
        "TR 批量请求不分批",
        "        for start in range(0, len(hashes), ORPHAN_TR_BATCH_SIZE):\n",
        "        for start in range(0, len(hashes), max(len(hashes), 1)):\n",
        "一次请求带上千个 hash → 分批数断言失败",
    ),
    (
        "整批失败时不拆批重试",
        "            if len(chunk) <= 1:\n"
        "                self._note_rpc_failure()\n"
        "                continue\n",
        "            if True:\n"
        "                self._note_rpc_failure()\n"
        "                continue\n",
        "整批失败即放弃 → 拆批次数断言失败（优化整轮失效）",
    ),
    (
        "预取慢失败不计入熔断",
        "                if time.monotonic() - started >= ORPHAN_SLOW_CALL_SECONDS:\n"
        "                    self._note_rpc_failure()\n"
        "                return\n",
        "                if False:\n"
        "                    self._note_rpc_failure()\n"
        "                return\n",
        "熔断永不打开 → _rpc_open 断言失败",
    ),
    (
        "单轮预算忽略配置值",
        "        self._scan_deadline = time.monotonic() + self._orphan_budget()\n",
        "        self._scan_deadline = time.monotonic() + ORPHAN_SCAN_BUDGET_SECONDS\n",
        "配置的预算不生效 → 预算耗尽用例的 checked 断言失败",
    ),
    (
        "断点续扫被移除（每轮都从头开始）",
        "        cursor = str(self.get_data(\"orphan_scan_cursor\") or \"\")\n",
        "        cursor = \"\"\n",
        "断点不生效 → 旋转顺序断言失败（尾部候选可能饿死）",
    ),
    (
        "回收后清缓存不先载入",
        "        cache = self._orphan_alive_cache()\n"
        "        cache.pop(str(hash_str or \"\"), None)\n",
        "        cache = self.__dict__.get(\"_orphan_alive_state\")\n"
        "        if isinstance(cache, dict):\n"
        "            cache.pop(str(hash_str or \"\"), None)\n",
        "未载入时删除是空操作 → 缓存条目仍在，断言失败",
    ),
    (
        "删种复核不在收尾执行",
        "            try:\n"
        "                self._verify_deleted_hashes()\n"
        "            except Exception as err:\n"
        "                logger.warning(\"【保种空间守护】删种复核异常：%s\", err)\n",
        "            try:\n"
        "                pass\n"
        "            except Exception as err:\n"
        "                logger.warning(\"【保种空间守护】删种复核异常：%s\", err)\n",
        "删除链路抛异常后复核丢失 → 调用记录为空、断言失败",
    ),
    (
        "预取并发不看下载器类型",
        "                parallel = (\n"
        "                    self._downloader_types.get(name)\n"
        "                    == DownloaderType.Qbittorrent\n"
        "                )\n",
        "                parallel = True\n",
        "长连接适配器也被并发 → 串行断言失败（max_workers 应为 1）",
    ),
    (
        "耗时起点写错（统计失真）",
        "        candidates = self._rotate_orphan_candidates(candidates)\n"
        "        started = time.monotonic()\n",
        "        candidates = self._rotate_orphan_candidates(candidates)\n"
        "        started = 0.0\n",
        "耗时为天文数字 → 上界断言失败",
    ),
]


def run_tests():
    """跑性能专项测试，返回 (是否全过, 摘要)。"""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_perf_optimization.py",
         "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=PLUGIN_DIR, capture_output=True, text=True,
    )
    output = proc.stdout + proc.stderr
    tail = "\n".join(output.strip().splitlines()[-3:])
    return proc.returncode == 0, tail


def main() -> int:
    """逐个应用变异体并核对测试是否变红。"""
    if not os.path.exists(TARGET):
        print(f"找不到目标源码：{TARGET}")
        return 1
    original = open(TARGET, encoding="utf-8").read()

    base_ok, base_tail = run_tests()
    print(f"基线：{'全部通过' if base_ok else '基线即失败'}")
    if not base_ok:
        print(base_tail)
        return 1
    print()

    caught = escaped = skipped = 0
    escaped_names = []
    try:
        for idx, (name, old, new, expect) in enumerate(MUTANTS, 1):
            count = original.count(old)
            if count == 0:
                print(f"[{idx:2d}] 跳过（锚点失配）：{name}")
                skipped += 1
                continue
            if count > 1:
                print(f"[{idx:2d}] 跳过（锚点命中 {count} 处，需收窄）：{name}")
                skipped += 1
                continue
            mutated = original.replace(old, new, 1)
            with open(TARGET, "w", encoding="utf-8") as handle:
                handle.write(mutated)
            ok, tail = run_tests()
            if ok:
                escaped += 1
                escaped_names.append(name)
                print(f"[{idx:2d}] 逃逸：{name}")
                print(f"     期望：{expect}")
                print("     实际：测试全部通过，该缺陷未被捕获\n")
            else:
                caught += 1
                print(f"[{idx:2d}] 捕获：{name}")
    finally:
        with open(TARGET, "w", encoding="utf-8") as handle:
            handle.write(original)

    print()
    print("=" * 72)
    total = caught + escaped
    print(f"变异测试结果：{caught}/{total} 被捕获，{escaped} 个逃逸"
          + (f"，{skipped} 个跳过（防护未生效！）" if skipped else ""))
    if escaped_names:
        print("逃逸清单：")
        for name in escaped_names:
            print(f"  - {name}")
    return 0 if escaped == 0 and skipped == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
