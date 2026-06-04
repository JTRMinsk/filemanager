"""在 QThread 中运行 Agent 的一轮对话，避免调模型时界面冻结。

设计与现有 ``scanner.ScanThread`` 同套路:后台线程干活，通过信号把过程与结果抛回主线程。

信号:
- ``step``:Agent 的中间事件（assistant_text / tool_call / tool_result / final），主线程追加到消息流。
- ``finished_ok``:本轮最终文本回复。
- ``failed``:异常（网络错误、key 无效等）以字符串描述。

注意:Agent 本身无 Qt 依赖;本类是它在 GUI 侧的薄封装。每轮对话起一个线程实例。
确认回调（阶段 3 的破坏性操作确认）将来在此用信号 + 阻塞等待主线程实现，阶段 5 暂不涉及。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from filemanager.agent import Agent


class AgentThread(QThread):
    step = Signal(object)        # dict 事件 -> 消息流
    finished_ok = Signal(str)    # 最终回复文本
    failed = Signal(str)         # 错误描述

    def __init__(self, agent: Agent, user_text: str, attached_paths: list[Path] | None = None) -> None:
        super().__init__()
        self._agent = agent
        self._user_text = user_text
        self._attached = attached_paths or []

    def run(self) -> None:
        try:
            reply = self._agent.run_turn(
                self._user_text,
                attached_paths=self._attached,
                emit_cb=self.step.emit,   # 线程内回调直接转成 Qt 信号
            )
        except Exception as e:  # noqa: BLE001 —— 网络/凭据等任意异常都转 failed 信号
            self.failed.emit(str(e))
            return
        self.finished_ok.emit(reply)
