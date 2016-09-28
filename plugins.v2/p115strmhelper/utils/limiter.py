from threading import Lock
from time import monotonic, sleep
from typing import Callable


class RateLimiter:
    """
    速率控制器，精确控制 QPS
    """

    def __init__(self, qps: float):
        if qps <= 0:
            qps = float("inf")
        self.interval = 1.0 / qps
        self.lock = Lock()
        self.next_call_time = monotonic()

    def acquire(self):
        """
        获取调用许可，阻塞直到满足速率限制
        """
        with self.lock:
            now = monotonic()
            wait_time = self.next_call_time - now
            if wait_time > 0:
                sleep(wait_time)
            self.next_call_time = max(now, self.next_call_time) + self.interval


class ApiEndpointCooldown:
    """
    独立冷却时间和线程锁的 API 端点
    """

    def __init__(self, api_callable: Callable, cooldown: float | int):
        self.api_callable = api_callable
        self.cooldown = cooldown
        self.lock = Lock()
        self.last_call_time = monotonic() - cooldown

    def __call__(self, payload: dict) -> dict:
        """
        执行 API 调用，处理冷却逻辑
        """
        if self.cooldown > 0:
            with self.lock:
                now = monotonic()
                elapsed = now - self.last_call_time
                if elapsed < self.cooldown:
                    sleep(self.cooldown - elapsed)
                self.last_call_time = monotonic()
        return self.api_callable(payload)
