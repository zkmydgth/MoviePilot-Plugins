#!/usr/bin/env python3
"""
插件仓库 Python 代码质量检查脚本

检查项：
- 语法错误（py_compile + ast.parse）
- Tab 字符混入
- 非 4 空格倍数缩进（排除字符串内容）
- 空行含空格/Tab

用法：
    python scripts/lint.py <file1.py> [file2.py] ...
    python scripts/lint.py --all
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from py_compile import PyCompileError, compile
from typing import Iterable, List, Tuple

_error_count = 0
_warning_count = 0


def _error(path: Path, lineno: int, msg: str) -> None:
    global _error_count
    _error_count += 1
    print(f"  ERROR {path}:{lineno}: {msg}")


def _warn(path: Path, lineno: int, msg: str) -> None:
    global _warning_count
    _warning_count += 1
    print(f"  WARN  {path}:{lineno}: {msg}")


def _get_python_files(root: Path) -> List[Path]:
    files: List[Path] = []
    for p in root.rglob("*.py"):
        parts = p.parts
        if any(part.startswith(".") for part in parts):
            if ".github" not in parts:
                continue
        skip_dirs = {"venv", "node_modules", "__pycache__", ".git"}
        if any(part in skip_dirs for part in parts):
            continue
        if ".github" in parts and "scripts" in parts:
            continue
        files.append(p)
    return sorted(files)


def _check_syntax(path: Path) -> bool:
    ok = True
    try:
        compile(str(path), doraise=True)
    except PyCompileError as e:
        lineno = getattr(e, "lineno", 0) or 0
        _error(path, lineno, f"py_compile: {e}")
        ok = False

    try:
        with open(path, "r", encoding="utf-8") as f:
            ast.parse(f.read(), filename=str(path))
    except SyntaxError as e:
        if ok:
            _error(path, e.lineno or 0, f"ast.parse: {e.msg}")
            ok = False
    except UnicodeDecodeError as e:
        _error(path, 0, f"编码错误: {e}")
        ok = False

    return ok


def _count_triple_quotes(s: str, quote: str) -> int:
    count = 0
    i = 0
    while True:
        idx = s.find(quote, i)
        if idx == -1:
            break
        count += 1
        i = idx + len(quote)
    return count


def _check_indentation(path: Path) -> None:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    in_multiline = False
    ml_quote = ""

    for lineno, raw_line in enumerate(lines, 1):
        line = raw_line.rstrip("\n\r")
        stripped = line.lstrip()

        if "\t" in line:
            _error(path, lineno, "发现 Tab 字符，请统一使用 4 空格缩进")

        if not stripped and line != line.rstrip():
            _warn(path, lineno, "空行包含尾部空格")

        if not in_multiline:
            triple_double = _count_triple_quotes(stripped, '"""')
            triple_single = _count_triple_quotes(stripped, "'''")
            if triple_double % 2 == 1:
                in_multiline = True
                ml_quote = '"""'
            elif triple_single % 2 == 1:
                in_multiline = True
                ml_quote = "'''"
        else:
            if ml_quote in stripped:
                in_multiline = False
                ml_quote = ""
            continue

        if not stripped or stripped.startswith("#"):
            continue

        leading_spaces = len(line) - len(stripped)
        if leading_spaces > 0 and leading_spaces % 4 != 0:
            _warn(path, lineno, f"缩进 {leading_spaces} 空格，不是 4 的倍数")


def lint_file(path: Path) -> bool:
    print(f"Checking {path}")
    ok = _check_syntax(path)
    _check_indentation(path)
    return ok


def lint_files(paths: Iterable[Path]) -> Tuple[int, int]:
    global _error_count, _warning_count
    _error_count = 0
    _warning_count = 0
    checked = 0

    for p in paths:
        if not p.exists():
            _error(p, 0, "文件不存在")
            continue
        lint_file(p)
        checked += 1

    print()
    print(
        f"Checked {checked} file(s), {_error_count} error(s), {_warning_count} warning(s)"
    )
    return _error_count, _warning_count


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python scripts/lint.py <file.py> [file2.py] ...")
        print("       python scripts/lint.py --all")
        return 1

    if sys.argv[1] == "--all":
        repo_root = Path(__file__).resolve().parent.parent
        files = _get_python_files(repo_root)
        errors, warnings = lint_files(files)
    else:
        files = [Path(p) for p in sys.argv[1:]]
        errors, warnings = lint_files(files)

    return 1 if errors > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
