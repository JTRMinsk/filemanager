"""在 QThread 中运行 Agent 的一轮对话，并支持**跨线程确认**。

与现有 ``scanner.ScanThread`` 同套路;额外解决阶段 3 的核心难点:
后台线程算出"将要做什么"的清单后，需要**暂停**等待主线程的用户点击（确认/取消），
拿到结果再继续。用 ``QMutex`` + ``QWaitCondition`` 实现工作线程阻塞等待。

信号（都在主线程接收）:
- ``step``:Agent 中间事件 -> 消息流。
- ``confirm_request``:需要用户确认时发出，携带 {name, description, is_write}。
  主线程据此弹确认卡片，用户点击后调用 ``provide_confirm(approved)`` 回传结果。
- ``finished_ok`` / ``failed``:本轮结束。

注意:确认期间工作线程是阻塞的（在 ``_confirm`` 里 wait），主线程照常响应点击，
点击后 ``provide_confirm`` 唤醒工作线程。每轮对话起一个线程实例。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QMutex, QThread, QWaitCondition, Signal

from filemanager.agent import Agent


class AgentThread(QThread):
    step = Signal(object)            # dict 事件 -> 消息流
    confirm_request = Signal(object) # dict {name, description, is_write} -> 主线程弹卡片
    finished_ok = Signal(str, bool)  # (最终回复, 是否发生写盘变更)
    failed = Signal(str)             # 错误

    def __init__(self, agent: Agent, user_text: str, attached_paths: list[Path] | None = None) -> None:
        super().__init__()
        self._agent = agent
        self._user_text = user_text
        self._attached = attached_paths or []
        self._mutex = QMutex()
        self._cond = QWaitCondition()
        self._answer: bool | None = None

    # ---- 工作线程内:发出确认请求并阻塞等待主线程回应 ----
    def _confirm(self, info: dict) -> bool:
        self._mutex.lock()
        try:
            self._answer = None
            self.confirm_request.emit(info)      # 通知主线程弹卡片
            while self._answer is None:
                self._cond.wait(self._mutex)     # 阻塞，直到 provide_confirm 唤醒
            return bool(self._answer)
        finally:
            self._mutex.unlock()

    # ---- 主线程内:用户点击确认/取消后调用，唤醒工作线程 ----
    def provide_confirm(self, approved: bool) -> None:
        self._mutex.lock()
        self._answer = bool(approved)
        self._cond.wakeAll()
        self._mutex.unlock()

    def run(self) -> None:
        try:
            reply, filesystem_changed = self._agent.run_turn(
                self._user_text,
                attached_paths=self._attached,
                emit_cb=self.step.emit,
                confirm_cb=self._confirm,        # 把跨线程确认接进 Agent
            )
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))
            return
        self.finished_ok.emit(reply, filesystem_changed)
