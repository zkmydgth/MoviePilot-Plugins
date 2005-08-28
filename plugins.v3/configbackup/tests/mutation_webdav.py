#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WebDAV 客户端变异测试（v3.2.0 模块 B）：逐个拆掉客户端里的关键防护，确认测试真能变红。

为什么单独一套
--------------
``mutation_boundary.py`` 的 ``TARGET`` 写死插件主文件 ``__init__.py``，而模块 B 的核心
在 ``webdav_client.py`` —— 不另开一套，这层防护就**无人守护**（正是主记忆 §3.3「静默失效」的形状）。

与主套件**同规约**：
- **跳过即失败**：锚点失配等同于失败（退出码 1），不许"静默报平安"；
- 跑完在 ``finally`` 里**无条件还原**源码；
- 改完锚点后必须**人为制造一次失配**验证脚本真能变红（主记忆 §3.2）。

用法（两个变异脚本必须**串行**跑）::

    cd plugins.v3
    python3 configbackup/tests/mutation_webdav.py
"""

import os
import re
import shutil
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(_HERE)
PLUGINS_V3 = os.path.dirname(PLUGIN_DIR)
TARGET = os.path.join(PLUGIN_DIR, "webdav_client.py")
BACKUP = "/tmp/cb_webdav_client_backup.py"

MUTANTS = [
    # ---------------- 上传：Content-Length ----------------
    (
        "上传不带 Content-Length：urllib 改走 chunked，部分服务端拒收",
        """                    headers={"Content-Length": str(size)},""",
        """                    headers={},""",
        "应导致 test_upload_uses_content_length_not_chunked 失败（包内容送不全）",
    ),
    (
        "上传不自动建父目录：多级远端目录直接 409",
        """        if ensure_parent:
            parent = str(Path(remote_path).parent)
            if parent not in (".", "", "/"):
                self.mkdir_p(parent)""",
        """        if False and ensure_parent:
            parent = str(Path(remote_path).parent)
            if parent not in (".", "", "/"):
                self.mkdir_p(parent)""",
        "应导致 test_upload_creates_parent_dirs 失败（没有 MKCOL 请求）",
    ),
    (
        "mkdir_p 幂等放宽：已存在当失败（405 不再吞掉）",
        """                allow_status=(200, 201, 405, 301, 302),""",
        """                allow_status=(201,),""",
        "应导致 test_mkdir_p_is_idempotent 失败（第二次建目录报错）",
    ),

    # ---------------- PROPFIND / 解析 ----------------
    (
        "PROPFIND 不校验 207：填成网页地址也当成功",
        """                if expect_multi and status != MULTI_STATUS:""",
        """                if False and expect_multi and status != MULTI_STATUS:""",
        "应导致 test_non_webdav_endpoint_detected 失败（错误文案变成 XML 解析失败）",
    ),
    (
        "列表退回 Element.iter 通配：静默返回空列表",
        """        for node in root.findall(".//{*}response"):""",
        """        for node in root.iter("{*}response"):""",
        "应导致 test_list_dir_parses_foreign_namespace 失败（列表永远为空）",
    ),
    (
        "解析写死 D 前缀：换前缀的网盘解析不到字段",
        """        child = node.find(f".//{{*}}{tag}")""",
        """        child = node.find(f".//D:{tag}")""",
        "应导致 test_list_dir_parses_foreign_namespace 失败（名字/大小为 N/A）",
    ),

    # ---------------- 错误分类 ----------------
    (
        "超时不翻译：英文 TimeoutError 直接甩给用户",
        """            raise self._timeout_error(action) from e""",
        """            raise e""",
        "应导致 test_timeout_reported_in_chinese 失败",
    ),
    (
        "401 不单独分类：认证失败退化成通用 HTTP 错误",
        """        if code == 401:
            return WebDAVError(f"{action}失败：认证失败（401），请检查用户名与密码/应用密码")""",
        """        if code == 1401:
            return WebDAVError(f"{action}失败：认证失败（401），请检查用户名与密码/应用密码")""",
        "应导致 test_wrong_password_is_reported 失败（用户看不懂错在哪）",
    ),
]


def run_tests():
    """跑 ConfigBackup 全部测试，返回 (通过, 摘要)。"""
    proc = subprocess.run(
        [
            sys.executable, "-m", "unittest", "discover",
            "-s", "configbackup/tests", "-t", "configbackup",
        ],
        cwd=PLUGINS_V3, capture_output=True, text=True,
    )
    output = proc.stdout + proc.stderr
    tail = "\n".join([ln for ln in output.strip().splitlines()[-4:]])
    return proc.returncode == 0, tail


def main():
    """入口：备份源码 → 逐个种变异 → 跑测试 → 无条件还原。"""
    shutil.copy(TARGET, BACKUP)
    try:
        return _run()
    finally:
        # 无条件还原（2026-10-04 实测踩过：长链路超时中断会把变异留在源码里，
        # 之后所有测试都假失败。务必在 finally 里还原，别依赖正常路径。）
        shutil.copy(BACKUP, TARGET)


def _run():
    """执行全部变异体，返回退出码。"""
    base_ok, base_tail = run_tests()
    print(f"基线：{'✅ 全部通过' if base_ok else '❌ 基线即失败'}")
    if not base_ok:
        print(base_tail)
        return 1

    source = open(BACKUP, encoding="utf-8").read()
    caught = escaped = skipped = 0
    escaped_names = []

    print(f"\n{'='*70}\n变异测试（WebDAV 客户端，共 {len(MUTANTS)} 个变异体）\n{'='*70}")
    for idx, entry in enumerate(MUTANTS, 1):
        name, old, new, expect = entry
        if old not in source:
            skipped += 1
            print(f"[{idx:2d}] ⚠️  跳过（锚点未匹配）：{name}")
            continue
        mutated = source.replace(old, new, 1)
        with open(TARGET, "w", encoding="utf-8") as handle:
            handle.write(mutated)
        ok, tail = run_tests()
        if ok:
            escaped += 1
            escaped_names.append(name)
            print(f"[{idx:2d}] ❌ 逃逸：{name}\n       （{expect}）")
        else:
            caught += 1
            fails = re.search(r"FAILED \((.*?)\)", tail)
            detail = fails.group(0) if fails else "有失败"
            print(f"[{idx:2d}] ✅ 捕获：{name} → {detail}")

    print(f"\n{'='*70}")
    print(f"结果：捕获 {caught} / 逃逸 {escaped} / 跳过 {skipped} / 合计 {caught + escaped + skipped}")
    if escaped_names:
        print("逃逸清单（对应测试存在盲区，必须补强）：")
        for name in escaped_names:
            print(f"  - {name}")
    if skipped:
        print(f"⚠️ {skipped} 个变异体锚点失配（防护未生效！）——按规约等同于失败")
    print("已还原原始源码。")
    # 跳过即失败：锚点失配 = 该防护此刻无人守护，不能报平安
    if escaped_names or skipped:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
