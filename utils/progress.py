"""进度输出工具 —— 支持 CLI 和 SSE 推送"""

import json
import queue
import sys
import time

# 全局事件队列，供 SSE 推送使用
_event_queue: queue.Queue | None = None


def set_event_queue(q: queue.Queue) -> None:
    global _event_queue
    _event_queue = q


def _push_event(event_type: str, step: str, message: str, **extra):
    """向队列推送一个事件"""
    if _event_queue is not None:
        data = {"type": event_type, "step": step, "message": message, **extra}
        _event_queue.put(data)


class ProgressReporter:
    def __init__(self, step_name: str):
        self.step_name = step_name
        self.start_time = None

    def start(self, message: str = "") -> None:
        self.start_time = time.time()
        msg = f"[{self.step_name}] 开始"
        if message:
            msg += f" - {message}"
        print(msg)
        sys.stdout.flush()
        _push_event("step_start", self.step_name, message)

    def update(self, message: str) -> None:
        elapsed = time.time() - self.start_time if self.start_time else 0
        print(f"[{self.step_name}] [{elapsed:.1f}s] {message}")
        sys.stdout.flush()
        _push_event("step_update", self.step_name, message)

    def done(self, message: str = "") -> None:
        elapsed = time.time() - self.start_time if self.start_time else 0
        msg = f"[{self.step_name}] 完成 ({elapsed:.1f}s)"
        if message:
            msg += f" - {message}"
        print(msg)
        sys.stdout.flush()
        _push_event("step_done", self.step_name, message, elapsed=round(elapsed, 1))
