# -*- coding: utf-8 -*-
"""``app.chain.transfer.workflow`` 替身：整理任务构建入口。

插件包装的是 ``TransferWorkflowOwner._build_transfer_tasks``——宿主在
「下载器轮询 / 目录监控」下逐文件调用它，在目录整理时一次给出整批候选。
测试用 ``TransferWorkflowOwner()._build_transfer_tasks(...)`` 直接观察
插件是否在**建任务之前**摘掉了非正片，以及原方法的入参形态。
"""

from typing import Any, Dict, List, Optional, Tuple

#: 原方法被调用的次数
BUILD_CALLS: int = 0


def reset_calls() -> None:
    """重置调用计数。"""
    global BUILD_CALLS
    BUILD_CALLS = 0


class TransferWorkflowOwner:
    """整理流程替身：只保留插件需要包装的那个方法。"""

    def _build_transfer_tasks(
        self,
        *,
        file_items: Optional[List[Tuple[Any, bool]]] = None,
        mediainfo: Any = None,
        download_hash: Optional[str] = None,
        preview: bool = False,
        submission: Any = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """返回本次真正会被建任务的候选清单。"""
        global BUILD_CALLS
        BUILD_CALLS += 1
        return {
            "file_items": list(file_items or []),
            "mediainfo": mediainfo,
            "download_hash": download_hash,
            "preview": preview,
        }
