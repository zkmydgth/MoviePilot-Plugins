"""
``app.chain.transfer.facade`` 替身：组合出可被插件补丁打桩的 ``TransferChain``。

补丁（``patch/transfer_chain.py``）会向该类注入/替换以下成员，替身必须都提供：

* ``_TransferChain__handle_transfer``（V2 起保留名字改写的整理入口）
* ``_finish_scrape_batch_task``（V3 单下划线公开名，见宿主 ``app/chain/transfer/contract.py``）
* ``transfer`` / ``do_transfer``
* ``jobview``（``JobManager`` 实例，**挂在实例上**——与宿主一致，类上取不到）
* ``_request_durable_transfer_retry``（V3 失败重试入口，替代 V2 的 ``retry_scheduler``）
* ``_success_target_files``
* ``_TransferChain__checkpoint_planning_rejection``（V3 规划拒绝收口：未识别媒体、
  未识别集数等必须冻结为零文件副作用的 durable 终态）
* ``_TransferChain__record_uncheckpointed_failure``（V3 checkpoint 前失败登记）
* ``_transfer_admissions.abandon_unstarted``（插件接管后注销宿主恢复责任）

同时用 ``Singleton`` 元类复刻宿主的单例语义。

⚠️ 替身必须与**真机宿主**的形状一致：曾因这里把刮削批次方法写成双下划线私有名
（宿主 V3 已改为单下划线）而让回归测试全绿、真机补丁却整体放弃。
"""

from types import SimpleNamespace
from typing import Any, Dict, Optional, Tuple

from ...application.transfer.workflow import JobManager
from ...foundation.singleton import Singleton
from ..base import ChainBase

__all__ = ["TransferChain"]


class _StubAdmissions:
    """宿主 ``TransferChain._transfer_admissions`` 替身（只暴露补丁用到的注销入口）。"""

    #: ``abandon_unstarted`` 的返回值，测试可改为 False 模拟注销被 CAS 拒绝
    abandon_result: bool = True

    def abandon_unstarted(self, *, task_id: str, lease_token: str) -> bool:
        """记录注销请求并返回预设结果。"""
        TransferChain.last_abandoned_admission = {
            "task_id": task_id,
            "lease_token": lease_token,
        }
        return bool(self.abandon_result)


class TransferChain(ChainBase, metaclass=Singleton):
    """文件整理链替身（稳定类型身份 + 可打桩的方法面）。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """与宿主一致：``jobview`` 是**实例**属性，类上取不到。"""
        super().__init__(*args, **kwargs)
        self.jobview = JobManager()
        self._transfer_admissions = _StubAdmissions()

    #: 补丁读取该私有属性判断目标文件是否已成功整理
    _success_target_files: Dict[str, Any] = {}

    #: 记录最近一次 transfer 调用，便于测试断言
    last_transfer_args: Tuple = ()
    last_transfer_kwargs: Dict[str, Any] = {}

    #: 记录最近一次刮削批次收尾调用，便于测试断言
    last_finished_scrape_task: Any = None

    #: 记录最近一次 durable 重试登记，便于测试断言
    last_retry_request: Optional[Dict[str, Any]] = None

    #: 记录最近一次「规划拒绝收口」调用，便于测试断言
    last_planning_rejection: Optional[Dict[str, Any]] = None

    #: 记录最近一次「checkpoint 前失败登记」调用，便于测试断言
    last_uncheckpointed_failure: Optional[Dict[str, Any]] = None

    #: 记录最近一次宿主准入注销调用，便于测试断言
    last_abandoned_admission: Optional[Dict[str, Any]] = None

    def transfer(self, *args: Any, **kwargs: Any) -> Tuple[bool, str]:
        """整理入口。替身记录调用后返回失败占位。"""
        TransferChain.last_transfer_args = args
        TransferChain.last_transfer_kwargs = kwargs
        return False, "stub: transfer not implemented"

    def do_transfer(self, *args: Any, **kwargs: Any) -> Tuple[bool, str]:
        """兼容别名。"""
        return self.transfer(*args, **kwargs)

    # ------------------------------------------------------------------
    # 补丁目标（宿主私有方法，替身提供等价实现）
    # ------------------------------------------------------------------
    def _TransferChain__handle_transfer(
        self,
        task: Any,
        callback: Any = None,
    ) -> Tuple[bool, str]:
        """处理单个整理任务（宿主私有方法替身）。"""
        return False, "stub: handle_transfer"

    def _finish_scrape_batch_task(self, task: Any) -> None:
        """结束刮削批次任务（宿主 V3 单下划线公开名替身），记录调用便于断言。"""
        TransferChain.last_finished_scrape_task = task

    def _TransferChain__checkpoint_planning_rejection(
        self,
        task: Any,
        error: str,
    ) -> Any:
        """规划拒绝收口替身：记录并返回失败的整理结果占位。"""
        TransferChain.last_planning_rejection = {"task": task, "error": error}
        return SimpleNamespace(success=False, message=error)

    def _TransferChain__record_uncheckpointed_failure(
        self,
        task: Any,
        error: Any,
    ) -> None:
        """checkpoint 前失败登记替身：记录调用便于断言。"""
        TransferChain.last_uncheckpointed_failure = {"task": task, "error": error}

    def _TransferChain__rename_subtitles(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Tuple[bool, str]:
        """重命名字幕（宿主私有方法替身）。"""
        return True, "stub"

    def _request_durable_transfer_retry(
        self,
        history: Any,
        *,
        requested_by: str,
    ) -> Tuple[bool, str]:
        """V3 durable 重试入口替身：记录调用并返回受理。"""
        TransferChain.last_retry_request = {
            "history": history,
            "requested_by": requested_by,
        }
        return True, "stub: retry accepted"
