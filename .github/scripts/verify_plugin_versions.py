#!/usr/bin/env python3
"""
插件版本声明一致性校验。

背景（2026-09-28 事故）：
    ConfigBackup / SeedSpaceGuard 的 version.py 已升到 3.0.1，
    但 __init__.py 里的 plugin_version 仍硬编码 "3.0.0"。
    发布流程只读 version.py 做校验，因此"校验通过"却打出了错位的安装包，
    导致用户端反复提示更新、点更新无效。

本脚本用于在 CI 中提前拦截这类不一致。

校验规则（针对每个插件）：
    1. version.py 必须存在，且含字符串常量 VERSION
    2. package.json 中该插件的 version 必须与 VERSION 一致
    3. __init__.py 中 plugin_version 的正确写法是引用 VERSION，
       即存在 `plugin_version = VERSION`，且不得出现硬编码的 plugin_version 字符串赋值
    4. 若 __init__.py 同时导入 VERSION，则 import 语句必须存在

退出码：0 全部通过；1 存在不一致。
"""

from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# 只校验声明了 release: true 的 V3 插件
SOURCE_DIR = "plugins.v3"
PACKAGE_FILE = "package.v3.json"


def log(*args: object) -> None:
    print(*args, file=sys.stderr)


def read_string_const(path: Path, name: str) -> tuple[str | None, str | None]:
    """用 AST 读取模块级/类级字符串常量赋值，返回 (值, 错误)。"""
    if not path.exists():
        return None, f"文件不存在：{path}"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except Exception as error:  # noqa: BLE001
        return None, f"解析失败：{error}"

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                if isinstance(node.value, ast.Constant):
                    return str(node.value.value), None
    return None, f"未找到 {name}"


def find_plugin_version_assignments(path: Path) -> list[ast.Assign]:
    """找出 __init__.py 中所有 plugin_version 赋值节点。"""
    if not path.exists():
        return []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except Exception:  # noqa: BLE001
        return []

    result: list[ast.Assign] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "plugin_version":
                result.append(node)
    return result


def check_plugin(plugin_id: str, plugin_dir: Path, declared_version: str) -> list[str]:
    """返回该插件的问题列表（空列表表示通过）。"""
    problems: list[str] = []
    init_py = plugin_dir / "__init__.py"
    version_py = plugin_dir / "version.py"

    # 规则 1：version.py 必须有 VERSION
    version_const, err = read_string_const(version_py, "VERSION")
    if err or not version_const:
        problems.append(f"version.py 缺少 VERSION 常量（{err}）")
        version_const = None

    # 规则 2：VERSION 必须与清单一致
    if version_const and version_const != declared_version:
        problems.append(
            f"版本不一致：version.py VERSION={version_const!r} "
            f"vs 清单 version={declared_version!r}"
        )

    # 规则 3/4：plugin_version 必须引用 VERSION，不得硬编码字符串
    assignments = find_plugin_version_assignments(init_py)
    if not assignments:
        problems.append("__init__.py 中未找到 plugin_version 赋值")
    for node in assignments:
        value = node.value
        if isinstance(value, ast.Constant):
            problems.append(
                f"__init__.py 第 {node.lineno} 行 plugin_version 硬编码为 "
                f"{value.value!r}，应改为 plugin_version = VERSION"
            )
        elif isinstance(value, ast.Name) and value.id == "VERSION":
            source = init_py.read_text(encoding="utf-8")
            if "from .version import VERSION" not in source.replace(" ", " "):
                # 允许其他等价导入写法，宽松放行但提示
                if "import VERSION" not in source:
                    problems.append(
                        "__init__.py 使用了 VERSION 但未见 `from .version import VERSION`"
                    )
        else:
            problems.append(
                f"__init__.py 第 {node.lineno} 行 plugin_version 赋值形式无法识别，"
                f"请使用 plugin_version = VERSION"
            )

    return problems


def main() -> int:
    package_path = REPO_ROOT / PACKAGE_FILE
    if not package_path.exists():
        log(f"[Fatal] 未找到清单文件：{package_path}")
        return 1

    try:
        package_data = json.loads(package_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        log(f"[Fatal] 清单 JSON 解析失败：{error}")
        return 1

    source_root = REPO_ROOT / SOURCE_DIR
    if not source_root.is_dir():
        log(f"[Fatal] 未找到源码目录：{source_root}")
        return 1

    checked = 0
    failed: list[str] = []

    for plugin_id, info in package_data.items():
        if not isinstance(info, dict):
            continue
        # 只校验声明为可发布的插件
        if not info.get("release", False):
            log(f"[Skip] {plugin_id} 未声明 release: true，跳过")
            continue

        declared_version = info.get("version")
        if not declared_version:
            failed.append(f"{plugin_id}: 清单缺少 version 字段")
            continue

        plugin_dir = source_root / plugin_id.lower()
        if not plugin_dir.is_dir():
            failed.append(f"{plugin_id}: 源码目录不存在 {plugin_dir}")
            continue

        problems = check_plugin(plugin_id, plugin_dir, str(declared_version))
        checked += 1
        if problems:
            log(f"[Fail] {plugin_id}（清单版本 {declared_version}）")
            for item in problems:
                log(f"       - {item}")
            failed.append(plugin_id)
        else:
            log(f"[OK]   {plugin_id}  版本 {declared_version}")

    log("")
    log(f"已校验 {checked} 个插件，失败 {len(failed)} 个。")

    if failed:
        log("")
        log("[Fatal] 版本声明不一致，请修正后再发布：")
        for plugin_id in failed:
            log(f"  · {plugin_id}")
        log("")
        log("修正方式：")
        log("  1. version.py 中 VERSION 与 package.v3.json 的 version 保持一致")
        log("  2. __init__.py 中使用  from .version import VERSION")
        log("     并写  plugin_version = VERSION（不要硬编码字符串）")
        return 1

    log("[Success] 全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
