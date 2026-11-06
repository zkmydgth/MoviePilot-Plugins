"""
备份数量兜底：超量暂停、用户确认与状态持久化。

为什么需要
----------
定时表达式很容易被设成高频形式（例如 ``* 3 * * *`` 表示「3 点这一小时内
每分钟」，一天就是 60 份，而不是每天 1 份）。插件自身无法判断这是不是用户
的意图，所以这里只做「停 + 问」：数量明显超出预期就暂停自动备份、发通知
让用户确认，确认「正常」才继续，确认「异常」则引导改定时。

设计取舍
--------
- **只停自动备份**：手动触发与还原前的安全备份不受闸门限制，否则用户连
  手动救急的通道都没了。
- **状态落盘**：暂停必须在重启后依然有效，否则一次部署就把兜底绕过去。
- **fail-open**：状态文件读写异常时退化为「不启用兜底」，坏状态文件不该
  让备份整个停摆。
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from apscheduler.triggers.cron import CronTrigger

#: 状态文件名（落在插件数据目录）
STATE_FILENAME = "backup_guard.json"
#: 正常上限 = 保留个数 × 该倍数（给「刚改小保留数、存量还没清完」留缓冲）
CEILING_FACTOR = 3
#: 正常上限的绝对下限：保留设置很小时也允许这么多份
CEILING_FLOOR = 30
#: 用户确认「数量正常」后，阈值在此基础上抬升的倍数
ACK_GROWTH = 1.5
#: 暂停期间的重复提醒间隔（秒）：24 小时
REMIND_INTERVAL = 24 * 3600
#: 暂停原因：备份数量超过预期上限
REASON_COUNT = "count_exceeded"


def _as_int(value: Any, default: int = 0) -> int:
    """把任意输入转成非负整数，非法值回退默认。"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number >= 0 else default


def _as_float(value: Any, default: float = 0.0) -> float:
    """把任意输入转成非负浮点数，非法值回退默认。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number >= 0 else default


@dataclass
class GuardState:
    """备份数量兜底的持久化状态。"""

    #: 是否处于暂停状态
    paused: bool = False
    #: 暂停原因（当前只有 count_exceeded）
    reason: str = ""
    #: 触发时的备份份数
    count: int = 0
    #: 触发时的生效上限
    ceiling: int = 0
    #: 触发时的定时表达式，用于判断用户是否已改过定时
    cron: str = ""
    #: 触发时间戳
    detected_at: float = 0.0
    #: 最近一次通知时间戳（用于 24 小时提醒节流）
    notified_at: float = 0.0
    #: 已提醒次数
    remind_count: int = 0
    #: 用户确认「数量正常」后抬升的上限；0 表示没有确认过
    acknowledged_ceiling: int = 0
    #: 用户已确认「数量异常」，等待其修改定时表达式
    needs_schedule_fix: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """转换为可序列化字典。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Any) -> "GuardState":
        """
        从任意 JSON 还原状态；字段类型不可信时逐项回退默认值。

        :param raw: 状态文件解析出的对象
        :return: 状态实例
        """
        state = cls()
        if not isinstance(raw, dict):
            return state
        state.paused = bool(raw.get("paused"))
        state.reason = str(raw.get("reason") or "")
        state.count = _as_int(raw.get("count"))
        state.ceiling = _as_int(raw.get("ceiling"))
        state.cron = str(raw.get("cron") or "")
        state.detected_at = _as_float(raw.get("detected_at"))
        state.notified_at = _as_float(raw.get("notified_at"))
        state.remind_count = _as_int(raw.get("remind_count"))
        state.acknowledged_ceiling = _as_int(raw.get("acknowledged_ceiling"))
        state.needs_schedule_fix = bool(raw.get("needs_schedule_fix"))
        return state


def count_daily_runs(cron: str, limit: int = 200) -> int:
    """
    统计一个 cron 表达式在 24 小时内会触发多少次（用于给用户解释频率）。

    :param cron: 5 位 cron 表达式
    :param limit: 迭代上限，超过即认为「非常频繁」，避免高频表达式把界面拖慢
    :return: 触发次数；表达式非法或无法解析时返回 0
    """
    expression = str(cron or "").strip()
    if not expression:
        return 0
    try:
        trigger = CronTrigger.from_crontab(expression)
    except Exception:
        return 0
    # from_crontab 生成的触发器带时区，naive 的 now() 与它比较会抛 TypeError
    timezone = getattr(trigger, "timezone", None)
    cursor = datetime.now(timezone)
    deadline = cursor + timedelta(days=1)
    fires = 0
    for _ in range(max(int(limit), 1)):
        try:
            nxt = trigger.get_next_fire_time(None, cursor)
        except Exception:
            return fires
        if nxt is None or nxt > deadline:
            break
        fires += 1
        cursor = nxt + timedelta(seconds=1)
    return fires


class BackupGuard:
    """备份数量兜底的状态机：判定超量、暂停、用户确认与自动解除。"""

    def __init__(self, path: Path, keep_count: int = 10):
        """
        初始化兜底状态机。

        :param path: 状态文件路径（插件数据目录内）
        :param keep_count: 当前「保留备份个数」配置
        """
        self.path = Path(path)
        self.keep_count = _as_int(keep_count, 10) or 10
        self.state = GuardState()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 阈值
    # ------------------------------------------------------------------
    def base_ceiling(self) -> int:
        """按保留个数计算的正常上限（不含用户确认后的抬升）。"""
        return max(int(self.keep_count) * CEILING_FACTOR, CEILING_FLOOR)

    def ceiling(self) -> int:
        """当前生效上限：基础上限与用户确认抬升取较大值。"""
        return max(self.base_ceiling(), int(self.state.acknowledged_ceiling or 0))

    def exceeded(self, count: int) -> bool:
        """备份份数是否已明显超出预期。"""
        return _as_int(count) > self.ceiling()

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    def load(self) -> GuardState:
        """读取状态文件；文件缺失或损坏时回退为初始状态。"""
        try:
            if self.path.exists():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self.state = GuardState.from_dict(raw)
        except Exception:
            self.state = GuardState()
        return self.state

    def save(self) -> None:
        """原子写入状态文件（tmp + os.replace），权限收紧到 0600。"""
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(
                json.dumps(self.state.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, self.path)

    # ------------------------------------------------------------------
    # 判定与决策
    # ------------------------------------------------------------------
    def trip(self, count: int, cron: str, now: Optional[float] = None) -> bool:
        """
        触发暂停。

        :param count: 当前备份份数
        :param cron: 当前定时表达式
        :param now: 当前时间戳（便于测试注入）
        :return: 本次是否真的由「未暂停」变为「暂停」
        """
        timestamp = time.time() if now is None else float(now)
        with self._lock:
            if self.state.paused:
                return False
            self.state.paused = True
            self.state.reason = REASON_COUNT
            self.state.count = _as_int(count)
            self.state.ceiling = self.ceiling()
            self.state.cron = str(cron or "")
            self.state.detected_at = timestamp
            self.state.notified_at = timestamp
            self.state.remind_count = 0
            self.state.needs_schedule_fix = False
        self.save()
        return True

    def acknowledge_ok(self) -> int:
        """
        用户确认「数量正常」：恢复备份，并把上限抬升到当前份数之上。

        :return: 抬升后的生效上限
        """
        with self._lock:
            target = max(_as_int(self.state.count), self.ceiling())
            raised = max(int(target * ACK_GROWTH), target + 1)
            self.state.acknowledged_ceiling = raised
            self._clear_pause()
            ceiling = self.ceiling()
        self.save()
        return ceiling

    def mark_schedule_fix(self) -> None:
        """用户确认「数量异常」：保持暂停，标记等待修改定时表达式。"""
        with self._lock:
            self.state.needs_schedule_fix = True
        self.save()

    def resume(self, reset_acknowledged: bool = True) -> None:
        """
        解除暂停。

        :param reset_acknowledged: 是否同时清除用户确认抬升的上限
        """
        with self._lock:
            if reset_acknowledged:
                self.state.acknowledged_ceiling = 0
            self._clear_pause()
        self.save()

    def _clear_pause(self) -> None:
        """清除暂停标记（调用方需已持锁）。"""
        self.state.paused = False
        self.state.reason = ""
        self.state.needs_schedule_fix = False
        self.state.detected_at = 0.0
        self.state.notified_at = 0.0
        self.state.remind_count = 0

    def sync_schedule(self, cron: str) -> bool:
        """
        定时表达式变了就视为用户已处理：自动解除暂停。

        :param cron: 当前生效的 cron 表达式
        :return: 本次是否自动解除了暂停
        """
        current = str(cron or "").strip()
        if not self.state.paused or self.state.reason != REASON_COUNT:
            return False
        if not current or not self.state.cron or current == self.state.cron:
            return False
        self.resume(reset_acknowledged=True)
        return True

    def self_heal(self, count: int) -> bool:
        """
        份数已回到上限以内（例如用户删了旧包或调大了保留个数）时自动解除。

        :param count: 当前备份份数
        :return: 本次是否自动解除了暂停
        """
        if not self.state.paused or self.state.reason != REASON_COUNT:
            return False
        if _as_int(count) > self.ceiling():
            return False
        self.resume(reset_acknowledged=False)
        return True

    def should_remind(self, now: Optional[float] = None) -> bool:
        """暂停期间是否到了下一次提醒时间（24 小时节流）。"""
        if not self.state.paused:
            return False
        timestamp = time.time() if now is None else float(now)
        return (timestamp - float(self.state.notified_at or 0.0)) >= REMIND_INTERVAL

    def note_reminded(self, now: Optional[float] = None) -> None:
        """记录一次提醒，避免同一时段重复推送。"""
        timestamp = time.time() if now is None else float(now)
        with self._lock:
            self.state.notified_at = timestamp
            self.state.remind_count = _as_int(self.state.remind_count) + 1
        self.save()

    def snapshot(self) -> Dict[str, Any]:
        """供 API 与详情页展示的状态快照。"""
        data = self.state.to_dict()
        data["ceiling"] = self.ceiling()
        data["base_ceiling"] = self.base_ceiling()
        data["keep_count"] = self.keep_count
        return data
