from typing import List
from pydantic import BaseModel


class PluginStatusData(BaseModel):
    enabled: bool
    has_client: bool
    running: bool


class LifeEventCheckSummary(BaseModel):
    """
    生活事件检查摘要
    """

    plugin_enabled: bool
    client_initialized: bool
    monitorlife_initialized: bool
    thread_running: bool
    config_valid: bool


class LifeEventCheckData(BaseModel):
    """
    生活事件检查数据
    """

    success: bool
    error_messages: List[str]
    debug_info: str
    summary: LifeEventCheckSummary
