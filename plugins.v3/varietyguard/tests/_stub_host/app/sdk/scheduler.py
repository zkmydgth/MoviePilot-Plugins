# -*- coding: utf-8 -*-
"""``app.sdk.scheduler`` 替身：插件一次性任务注册桩。"""

from typing import Any, Callable, Dict, List, Optional

# 测试可写：已注册的一次性任务；AVAILABLE=False 模拟调度器未运行。
JOBS: List[Dict[str, Any]] = []
AVAILABLE = True


def add_plugin_once_job(
    pid: str,
    job_id: str,
    func: Callable[..., Any],
    name: str,
    delay_seconds: float = 0,
    func_kwargs: Optional[dict] = None,
) -> bool:
    """记录一次性任务注册；调度器不可用时返回 False（与宿主语义一致）。"""
    if not AVAILABLE:
        return False
    JOBS.append(
        {
            "pid": pid,
            "job_id": job_id,
            "func": func,
            "name": name,
            "delay": delay_seconds,
            "kwargs": func_kwargs or {},
        }
    )
    return True
