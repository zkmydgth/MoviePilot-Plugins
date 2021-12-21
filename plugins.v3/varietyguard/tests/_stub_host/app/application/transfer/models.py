# -*- coding: utf-8 -*-
"""``app.application.transfer.models`` 替身：整理计划模型。

只保留插件真正依赖的字段与语义：``checkpoint.items`` 是叶子文件计划项、
``skip_reason`` 是空候选时的静默跳过原因；二者均为 dataclass，可被
``dataclasses.replace`` 重建（与宿主同款契约）。
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class TransferPlanItem:
    """单条叶子文件计划项替身。"""

    sequence: int
    source_fileitem: Dict[str, Any]
    target_storage: str = "local"
    target_path: str = ""
    action: str = "transfer"


@dataclass
class TransferPlanCheckpoint:
    """整理计划检查点替身。"""

    planning_input: Any = None
    target_storage: str = "local"
    root_target_path: str = ""
    final_target_path: str = ""
    resolved_transfer_type: str = "link"
    items: Tuple[TransferPlanItem, ...] = ()
    skip_reason: Optional[str] = None
    resolved_meta: Optional[Dict[str, Any]] = None
    need_notify: bool = True
    preview: bool = False
    schema_version: int = 1
    extra: Dict[str, Any] = field(default_factory=dict)
