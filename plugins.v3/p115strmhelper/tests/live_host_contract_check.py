#!/usr/bin/env python3
"""
真机宿主契约探针（不经 pytest，直接对运行中的 MoviePilot 宿主实测）。

**为什么要它**：桩宿主（``tests/_stub_host``）是人写的，一旦与真机漂移就会假绿。
2026-10-09 实测事故：宿主 V3 把「刮削批次收尾」方法从 name-mangled 私有名
``_TransferChain__finish_scrape_batch_task`` 改成单下划线公开名
``_finish_scrape_batch_task``，而桩宿主仍按旧名提供 → 299 个回归测试全绿，
真机补丁却因目标缺失被整体放弃（表现为「批量整理不能用」）。

本探针在真机宿主上直接验证：目标成员存在、目标校验通过、补丁可替换且可还原。

用法（容器内）::

    cd /app && python <本地插件源>/plugins.v3/p115strmhelper/tests/live_host_contract_check.py

退出码：0 = 无失败项；1 = 存在失败项。依赖宿主链实例的检查在实例尚未创建时记为
SKIP —— 探针**不主动构造**宿主链（宿主单例在"运行上下文未就绪"时构造会抛异常，
且失败实例会被 metaclass 缓存），运行期由 MP 启动流程创建后按首单复核。

⚠️ 探针只在**自己进程**里替换/还原宿主方法（进程结束即消失），不会影响运行中的
MoviePilot；也不会写入任何配置或数据库。
"""

from __future__ import annotations

import importlib
import inspect
import sys
import types
from pathlib import Path
from typing import Any, Callable, List, Tuple

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PACKAGE = "p115_live_probe_under_test"

#: 补丁会读写的作业队列方法（宿主 JobManager 的公开面）
_JOBVIEW_METHODS = (
    "add_task",
    "migrate_task",
    "running_task",
    "finish_task",
    "remove_task",
    "remove_job",
    "try_remove_job",
    "is_done",
)

_FAILURES: List[Tuple[str, str]] = []
_SKIPPED: List[Tuple[str, str]] = []
_PASSED: List[str] = []


def _load_plugin_module(relative: str):
    """
    以合成包形式加载插件源码模块（不改动运行目录，直接测源码树）。

    :param relative: 相对插件根目录的模块路径
    :return: 模块对象
    """
    if _PACKAGE not in sys.modules:
        root_pkg = types.ModuleType(_PACKAGE)
        root_pkg.__path__ = [str(PLUGIN_ROOT)]
        sys.modules[_PACKAGE] = root_pkg
    for name, sub in (("patch", PLUGIN_ROOT / "patch"), ("utils", PLUGIN_ROOT / "utils")):
        full = f"{_PACKAGE}.{name}"
        if full not in sys.modules:
            module = types.ModuleType(full)
            module.__path__ = [str(sub)]
            sys.modules[full] = module
    return importlib.import_module(f"{_PACKAGE}.{relative}")


def _check(name: str, func: Callable[[], Any]) -> Any:
    """执行单项检查，失败只记录不中断后续检查。"""
    try:
        result = func()
    except Exception as err:  # noqa: BLE001 - 探针要把失败项收集齐
        _FAILURES.append((name, f"{type(err).__name__}: {err}"))
        print(f"  FAIL  {name} -> {type(err).__name__}: {err}")
        return None
    _PASSED.append(name)
    print(f"  PASS  {name}")
    return result


def _require(value: Any, message: str) -> Any:
    """断言帮助函数：值为假即抛 AssertionError，供 ``_check`` 收集。"""
    if not value:
        raise AssertionError(message)
    return value


def _skip(name: str, reason: str) -> None:
    """记录一条 SKIP（环境限制，非缺陷）。"""
    _SKIPPED.append((name, reason))
    print(f"  SKIP  {name}（{reason}）")


def _check_host_members(transfer_chain_cls: Any, transfer_compat: Any) -> None:
    """检查补丁依赖的宿主成员是否存在、签名与候选名能否解析。"""
    handle = _check(
        "TransferChain._TransferChain__handle_transfer 存在",
        lambda: transfer_chain_cls._TransferChain__handle_transfer,
    )
    if handle is not None:
        params = list(inspect.signature(handle).parameters)[1:]
        _check(
            "handle_transfer 形参含 task/callback",
            lambda: _require(
                "task" in params and "callback" in params, f"实际形参: {params}"
            ),
        )

    finish_scrape_batch = _check(
        "刮削批次收尾方法可解析（V3 单下划线 / V2 私有名任一）",
        lambda: _require(
            transfer_compat.resolve_scrape_batch_finish(transfer_chain_cls),
            f"候选名 {transfer_compat.scrape_batch_finish_names()} 都不存在",
        ),
    )
    if finish_scrape_batch is not None:
        print(
            "        解析到: "
            f"{finish_scrape_batch.__qualname__} @ {finish_scrape_batch.__module__}"
        )

    _check("补丁依赖 transfer 回退入口", lambda: transfer_chain_cls.transfer)


def _check_durable_settlement(transfer_chain_cls: Any, transfer_compat: Any) -> None:
    """工单 F：durable 收口入口必须存在且签名匹配，缺入口时按契约降级。

    补丁复制的是宿主旧版规划前流程；未识别媒体、缺集数、重复投递等提前返回路径
    必须走 ``__checkpoint_planning_rejection`` / ``__record_uncheckpointed_failure``
    结算，缺任一个就会「接管却永不结算」——宿主恢复调度每 ~15 秒回放同一文件
    （2026-10-09 实测 18:45–18:47 每轮新增一条失败历史）。
    """
    for name in (
        "_TransferChain__checkpoint_planning_rejection",
        "_TransferChain__record_uncheckpointed_failure",
    ):
        _check_settlement_entry(transfer_chain_cls, name)

    _check(
        "缺收口入口时规划拒绝按契约降级（返回 None）",
        lambda: _check_rejection_degradation(transfer_compat),
    )
    _check(
        "缺登记入口时失败登记按契约降级（返回 False）",
        lambda: _check_record_degradation(transfer_compat),
    )
    _check("整理历史可按 durable 任务查询", _check_history_lookup_entry)
    _check(
        "组合根准入仓储提供 abandon_unstarted(task_id, lease_token)",
        _check_admission_repository,
    )


class _BareChain:
    """既无收口入口也无准入仓储的空壳链对象（用于验证降级契约）。"""


def _check_settlement_entry(transfer_chain_cls: Any, name: str) -> None:
    """检查单个 durable 收口入口存在、可调用且形参含 task/error。"""
    target = getattr(transfer_chain_cls, name, None)
    _check(
        f"durable 收口入口 {name} 可调用",
        lambda t=target, n=name: _require(callable(t), n),
    )
    if not callable(target):
        return
    params = list(inspect.signature(target).parameters)[1:]
    _check(
        f"{name} 形参含 task/error",
        lambda p=params, n=name: _require(
            "task" in p and "error" in p, f"{n} 实际形参: {p}"
        ),
    )


def _check_rejection_degradation(transfer_compat: Any) -> None:
    """宿主缺收口入口时 compat 层必须返回 None，让调用方回退宿主原生整理。"""
    result = transfer_compat.checkpoint_planning_rejection(
        _BareChain(), object(), "no-entry"
    )
    if result is not None:
        raise AssertionError(f"缺入口时应返回 None，实际 {result!r}")


def _check_record_degradation(transfer_compat: Any) -> None:
    """宿主缺登记入口时必须报「未登记」，不能假装失败原因已记下。"""
    if transfer_compat.record_uncheckpointed_failure(_BareChain(), object(), "no-entry"):
        raise AssertionError("缺入口时应返回 False")


def _check_history_lookup_entry() -> None:
    """失败结算后按 durable 任务查历史的入口（AI 重试登记依赖）。"""
    from app.db.oper.transferhistory import TransferHistoryOper  # noqa: PLC0415

    if not callable(getattr(TransferHistoryOper, "get_by_transfer_task_id", None)):
        raise AssertionError("TransferHistoryOper.get_by_transfer_task_id 不可调用")


def _check_admission_repository() -> None:
    """组合根实际注入的准入仓储必须提供 abandon_unstarted（接管注销依赖）。"""
    from app.db.adapters.transfer.admission import (  # noqa: PLC0415
        TransactionalTransferAdmissionRepository,
    )

    method = getattr(
        TransactionalTransferAdmissionRepository, "abandon_unstarted", None
    )
    if not callable(method):
        raise AssertionError(
            "TransactionalTransferAdmissionRepository.abandon_unstarted 不可调用"
        )
    params = inspect.signature(method).parameters
    if not {"task_id", "lease_token"} <= set(params):
        raise AssertionError(f"abandon_unstarted 实际形参: {list(params)}")


def _check_class_view_verification(transfer_chain_cls: Any, patcher: Any) -> None:
    """真实插件服务初始化时走类视角校验，必须在真机宿主上通过。"""
    _check(
        "补丁目标校验通过（类视角，jobview 按实例视角回退）",
        lambda: patcher._verify_targets(transfer_chain_cls),
    )


def _check_jobview_methods(jobview: Any) -> None:
    """检查作业队列的公开方法是否可用（不可用的由 JobViewAdapter 逐方法降级）。"""
    if jobview is None:
        return
    for name in _JOBVIEW_METHODS:
        _check(
            f"jobview.{name} 可调用",
            lambda n=name: _require(callable(getattr(jobview, n, None)), n),
        )


def _check_instance_scope(transfer_chain_cls: Any, patcher: Any) -> None:
    """
    实例视角检查。

    只查宿主**已存在**的实例（不主动构造）；探针进程里通常还没有实例，记为 SKIP。
    """
    getter = getattr(transfer_chain_cls, "get_existing_instance", None)
    instance = getter() if callable(getter) else None
    if instance is None:
        _skip(
            "宿主链实例视角检查",
            "探针进程内宿主链尚未创建（运行时由 MP 启动流程创建，故在 MP 进程内复核）",
        )
        return

    _check_jobview_methods(
        _check(
            "实例提供 jobview",
            lambda: _require(getattr(instance, "jobview", None), "实例上没有 jobview"),
        )
    )
    _check(
        "实例提供 durable 重试入口 _request_durable_transfer_retry",
        lambda: _require(
            callable(getattr(instance, "_request_durable_transfer_retry", None)),
            "_request_durable_transfer_retry 不可调用",
        ),
    )
    admissions = getattr(instance, "_transfer_admissions", None)
    abandon = _check(
        "实例提供准入注销入口 _transfer_admissions.abandon_unstarted",
        lambda: _require(
            callable(getattr(admissions, "abandon_unstarted", None)),
            "_transfer_admissions.abandon_unstarted 不可调用",
        ),
    )
    if abandon is not None:
        _check(
            "abandon_unstarted 形参含 task_id/lease_token",
            lambda: _require(
                {"task_id", "lease_token"} <= set(inspect.signature(abandon).parameters),
                f"实际形参: {list(inspect.signature(abandon).parameters)}",
            ),
        )
    _check(
        "补丁目标校验通过（实例视角）",
        lambda: patcher._verify_targets(instance),
    )


def _check_enable_cycle(transfer_chain_cls: Any, patcher: Any) -> None:
    """
    enable/disable 真正替换与还原。

    注意：宿主的该方法定义在 mixin 上，``TransferChain.__dict__`` 里原本没有它，
    打补丁只是在其上"遮蔽"；因此比较对象要用属性取值，不能用 ``__dict__``。
    """
    original = transfer_chain_cls._TransferChain__handle_transfer

    def _enable_cycle() -> None:
        patcher.enable(
            task_manager=object(), handler=object(), storage_module="115网盘Plus"
        )
        if not patcher.is_enabled():
            raise AssertionError("enable() 后 is_enabled() 仍为 False（补丁未生效）")
        if transfer_chain_cls._TransferChain__handle_transfer is original:
            raise AssertionError("enable() 后宿主方法未被替换")

    def _disable_cycle() -> None:
        patcher.disable()
        if patcher.is_enabled():
            raise AssertionError("disable() 后 is_enabled() 仍为 True")
        if transfer_chain_cls._TransferChain__handle_transfer is not original:
            raise AssertionError("disable() 后宿主方法未还原")

    try:
        _check("enable() 替换宿主方法并置位已启用", _enable_cycle)
        _check("disable() 还原宿主方法并清空已启用", _disable_cycle)
    finally:
        patcher.disable()


def _print_summary() -> int:
    """打印汇总并返回退出码。"""
    print()
    print(f"通过 {len(_PASSED)} 项，失败 {len(_FAILURES)} 项，跳过 {len(_SKIPPED)} 项")
    if _SKIPPED:
        print("跳过明细（环境限制，非缺陷）：")
        for name, reason in _SKIPPED:
            print(f"  - {name}: {reason}")
    if _FAILURES:
        print("失败明细：")
        for name, reason in _FAILURES:
            print(f"  - {name}: {reason}")
        return 1
    print("结论：真机宿主契约与插件补丁期望一致。")
    return 0


def main() -> int:
    """执行全部契约检查。"""
    print("== 真机宿主契约探针 ==")
    print(f"插件源码: {PLUGIN_ROOT}")

    from app.chain.transfer import TransferChain  # noqa: PLC0415 - 探针入口才开始依赖宿主

    transfer_compat = _load_plugin_module("utils.transfer_compat")
    patcher = _load_plugin_module("patch.transfer_chain").TransferChainPatcher

    _check_host_members(TransferChain, transfer_compat)
    _check_durable_settlement(TransferChain, transfer_compat)
    _check_class_view_verification(TransferChain, patcher)
    _check_instance_scope(TransferChain, patcher)
    _check_enable_cycle(TransferChain, patcher)

    return _print_summary()


if __name__ == "__main__":
    sys.exit(main())
