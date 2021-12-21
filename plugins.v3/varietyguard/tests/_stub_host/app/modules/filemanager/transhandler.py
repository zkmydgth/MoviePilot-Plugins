# -*- coding: utf-8 -*-
"""``app.modules.filemanager.transhandler`` 替身：整理处理器。

``TransHandler.plan_transfer`` 的签名与宿主一致（第一个位置参数是计划输入，
其余为关键字参数，其中 ``mediainfo`` 是插件判定作用域的唯一依据）。
测试用 ``set_pending_items`` 注入候选文件，用 ``ORIGINAL_PLAN_CALLS`` 观察
原方法是否被真正调用。
"""

from typing import Any, List, Optional, Tuple

from app.application.transfer.models import TransferPlanCheckpoint, TransferPlanItem

#: 待生成的候选文件：(文件名, 完整路径)
PENDING_ITEMS: List[Tuple[str, str]] = []
#: 原方法被调用次数
ORIGINAL_PLAN_CALLS: int = 0


def set_pending_items(items: List[Tuple[str, str]]) -> None:
    """注入下一次计划生成的候选文件。"""
    global PENDING_ITEMS
    PENDING_ITEMS = list(items)


class TransHandler:
    """整理处理器替身。"""

    def plan_transfer(
        self,
        planning_input: Any = None,
        *,
        meta: Any = None,
        mediainfo: Any = None,
        source_oper: Any = None,
        target_storage: str = "local",
        target_path: Any = None,
        transfer_type: str = "link",
        need_scrape: bool = False,
        need_rename: bool = True,
        need_notify: bool = True,
        overwrite_mode: Optional[str] = None,
        episodes_info: Optional[List[Any]] = None,
        preview: bool = False,
    ) -> TransferPlanCheckpoint:
        """按注入的候选文件生成计划检查点。"""
        global ORIGINAL_PLAN_CALLS
        ORIGINAL_PLAN_CALLS += 1
        items = tuple(
            TransferPlanItem(
                sequence=index,
                source_fileitem={
                    "storage": target_storage,
                    "path": path,
                    "name": name,
                    "type": "file",
                    "size": 1024,
                },
                target_storage=target_storage,
                target_path=f"/library/{name}",
            )
            for index, (name, path) in enumerate(PENDING_ITEMS)
        )
        return TransferPlanCheckpoint(
            planning_input=planning_input,
            target_storage=target_storage,
            root_target_path="/download",
            final_target_path="/library",
            resolved_transfer_type=transfer_type,
            items=items,
            skip_reason=None if (items or preview) else "源目录中没有可整理文件",
            need_notify=need_notify,
            preview=preview,
        )
