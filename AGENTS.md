This file provides guidance to AI coding agents when working with code in this repository.

# MoviePilot-Plugins Project Conventions

## 1. Commit Conventions

Use **Conventional Commits** format for easier Changelog generation and semantic versioning.

### Format

```
<type>(<scope>): <subject>

[optional body]
[optional footer]
```

- **type** (required): Commit type; see table below.
- **scope** (required): Affected scope, e.g. plugin name `p115strmhelper`, `migudiscover`, or area like `ci`, `deps`.
- **subject** (required): Short description, ~50 chars; no period at the end.

### Type Reference

| type     | Description                          |
|----------|--------------------------------------|
| feat     | New feature                          |
| fix      | Bug fix                              |
| docs     | Documentation only (README, comments)|
| style    | Code style (no logic change)         |
| refactor | Refactor (not new feature/fix)       |
| perf     | Performance improvement              |
| test     | Tests                                |
| chore    | Build/tooling/deps, etc.             |

### Examples

```
feat(p115strmhelper): support MCP tools/list and tools/call
fix(p115strmhelper): fix offline task list pagination params
```

### Rules

- Commit messages must be in English; keep language consistent across the repo.
- One logical change per commit; split unrelated changes into separate commits.
- For breaking changes, describe in body or footer; use `BREAKING CHANGE:` when needed.

---

## 2. Python Coding Conventions

### 1. Style and Format

- Follow **PEP 8**: 4-space indent, line length ~88–120 chars, spaces around operators, etc.
- Strings: prefer double quotes `"`; use single quotes when embedding double quotes.
- Trailing commas: allowed at end of multi-line structures (lists, dicts, args) for cleaner diffs.

### 2. Type Annotations

- Public functions and methods must have type annotations; internal helpers are encouraged.
- Use `typing`: `List`, `Dict`, `Optional`, `Any`, `Union`, `Tuple`, etc.
- Add docstring notes for complex or ambiguous parameters and return values.

```python
from typing import Any, Dict, List, Optional

async def run_tool(api: Any, name: str, arguments: Dict[str, Any]) -> str:
    ...
```

### 3. Docstrings

- Module: brief description at top of file (one line or short paragraph).
- Class/function/method: docstring for purpose; required for public API.
- Format: this repo uses **Chinese** + **reStructuredText** style (`:param`, `:return`, `:raises`).

```python
def _dump(obj: Any) -> str:
    """
    将对象序列化为 JSON 字符串。

    :param obj: 支持 model_dump()、dict() 或普通可序列化对象。
    :return: UTF-8 JSON 字符串。
    """
```

### 4. Import Order

1. Standard library (alphabetical)
2. Blank line
3. Third-party packages (alphabetical)
4. Blank line
5. Project/plugin relative imports (alphabetical)

```python
from pathlib import Path
from re import match as re_match
from time import sleep

from fastapi import Request
from orjson import dumps as orjson_dumps

from .api import Api
from .mcp import MCPManager
from .version import VERSION
```

### 5. Naming

- Modules/packages: lowercase with hyphens or underscores, e.g. `db_manager`, `mcp`.
- Classes: `CapWords`.
- Functions, methods, variables, parameters: `snake_case`.
- Constants: `UPPER_SNAKE_CASE`.
- Private implementation: single leading underscore `_internal_func`; module-level “private” may use `_`.

### 6. Exceptions and Logging

- Avoid bare `except:`; use at least `except Exception` and handle at an appropriate level.
- Use the project’s shared `logger` (e.g. `from app.log import logger`) for errors and important info; use `logger.error(..., exc_info=True)` at exception sites for debugging.
- User-facing messages should be clear and actionable; internal logs may include more technical detail.

### 7. Async Code

- Use `async def` for async functions; call other async APIs with `await`. Avoid blocking calls inside async functions; wrap with `asyncio.to_thread` when needed.
- Keep the async call chain consistent; do not forget to await coroutines.

### 8. Integration with MoviePilot

- Plugin entry must inherit `_PluginBase` and implement `plugin_name`, `plugin_version`, etc. as required.
- Use the project’s config prefix (e.g. `plugin_config_prefix`), events, and message channels so the interface matches the main app.
- Put dependencies in the plugin’s `requirements.txt`; prefer `~=` for minor-version pinning.

---

## 3. Quick Checklist

**Before committing:**

- [ ] Message follows type(scope): subject format
- [ ] New/changed public API has type annotations and docstrings
- [ ] Imports are ordered correctly; no unused imports
- [ ] Exception handling and logging are appropriate

**During review:**

- [ ] Logic is correct; edge cases and error paths considered
- [ ] No hardcoded secrets (keys, tokens)
- [ ] Consistent with existing plugin style and MoviePilot conventions
