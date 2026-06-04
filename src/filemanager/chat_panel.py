"""对话面板（界面左侧单独一栏）:消息流 + 输入框 + 新会话 + 设置入口。

职责:
- 持有一个 ``Agent``（按当前选中的 API 配置构造）。
- 每轮对话起一个 ``AgentThread`` 在后台调模型，避免界面冻结。
- 把 Agent 的中间事件渲染到消息流;对话进行中禁用输入。

阶段 5 不做拖拽（按用户决定）;``Message`` 的 images / 附件留待后续叠加。
"""

from __future__ import annotations

from pathlib import Path

import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from filemanager import api_store
from filemanager.agent import Agent
from filemanager.agent_thread import AgentThread
from filemanager.config import make_llm_client_from_profile


class ChatPanel(QWidget):
    """自带 Agent 的对话栏。可独立嵌入任何布局。"""

    # 当 Agent 完成文件类操作后发出，供主窗口刷新文件表格（阶段 3/5 联动用）
    files_changed = Signal()

    def __init__(self, parent: QWidget | None = None, allowed_roots: list[Path] | None = None) -> None:
        super().__init__(parent)
        self._allowed_roots = list(allowed_roots or [])
        self._agent: Agent | None = None
        self._thread: AgentThread | None = None
        self._ui_root: Path | None = None
        self._ui_recursive = True
        self._build_ui()
        self._refresh_status()

    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)

        # 顶栏:当前模型 + 设置 + 新会话
        top = QHBoxLayout()
        self._status = QLabel("未配置模型")
        self._status.setStyleSheet("color: gray; font-size: 11px;")
        btn_settings = QPushButton("设置")
        btn_settings.clicked.connect(self._open_settings)
        btn_memory = QPushButton("记忆")
        btn_memory.clicked.connect(self._open_memory)
        btn_new = QPushButton("新会话")
        btn_new.clicked.connect(self._new_session)
        top.addWidget(QLabel("助手"))
        top.addStretch(1)
        top.addWidget(self._status)
        top.addWidget(btn_settings)
        top.addWidget(btn_memory)
        top.addWidget(btn_new)
        v.addLayout(top)

        # 消息流
        self._stream = QTextBrowser()
        self._stream.setOpenExternalLinks(False)
        v.addWidget(self._stream, 1)

        # 确认卡片（默认隐藏;Agent 要动手前在此显示清单 + 确认/取消）
        self._confirm_bar = QFrame()
        self._confirm_bar.setFrameShape(QFrame.Shape.StyledPanel)
        self._confirm_bar.setStyleSheet(
            "QFrame {"
            "  background: #2a2a2a;"
            "  border: 1px solid #6b5a1e;"
            "  border-radius: 6px;"
            "  padding: 6px;"
            "}"
            "QFrame QLabel {"
            "  color: #e0e0e0;"
            "  background: transparent;"
            "}"
            "QFrame QPushButton {"
            "  background: #3c3c3c;"
            "  color: #e0e0e0;"
            "  border: 1px solid #555;"
            "  border-radius: 4px;"
            "  padding: 4px 12px;"
            "}"
            "QFrame QPushButton:hover { background: #4a4a4a; }"
            "QFrame QPushButton#confirm_approve {"
            "  background: #4a4220;"
            "  border-color: #8a7a30;"
            "  font-weight: bold;"
            "}"
            "QFrame QPushButton#confirm_approve:hover { background: #5c5228; }"
        )
        cb = QVBoxLayout(self._confirm_bar)
        self._confirm_label = QLabel()
        self._confirm_label.setWordWrap(True)
        self._confirm_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        cb.addWidget(self._confirm_label)
        crow = QHBoxLayout()
        crow.addStretch(1)
        self._btn_cancel = QPushButton("取消")
        self._btn_cancel.clicked.connect(lambda: self._answer_confirm(False))
        self._btn_approve = QPushButton("确认执行")
        self._btn_approve.setObjectName("confirm_approve")
        self._btn_approve.clicked.connect(lambda: self._answer_confirm(True))
        crow.addWidget(self._btn_cancel)
        crow.addWidget(self._btn_approve)
        cb.addLayout(crow)
        self._confirm_bar.setVisible(False)
        v.addWidget(self._confirm_bar)

        # 输入行
        row = QHBoxLayout()
        self._input = QLineEdit()
        self._input.setPlaceholderText("用大白话描述你想做的事，回车发送…")
        self._input.returnPressed.connect(self._send)
        self._btn_send = QPushButton("发送")
        self._btn_send.clicked.connect(self._send)
        row.addWidget(self._input, 1)
        row.addWidget(self._btn_send)
        v.addLayout(row)

    # ---- 状态与 Agent 构造 ----
    def _refresh_status(self) -> None:
        prof = api_store.get_active()
        if prof is None:
            self._status.setText("未配置模型 — 点「设置」")
        else:
            model = prof.model or "(默认)"
            self._status.setText(f"{prof.name} · {prof.backend} · {model}")

    def _ensure_agent(self) -> Agent | None:
        """按当前选中的 API 配置构造 Agent（缓存）。无配置/无 key 返回 None。"""
        if self._agent is not None:
            return self._agent
        prof = api_store.get_active()
        if prof is None or not prof.key:
            return None
        try:
            llm = make_llm_client_from_profile(prof)
        except Exception as e:  # noqa: BLE001
            self._append_system(f"无法初始化模型:{e}")
            return None
        self._agent = Agent(llm, allowed_roots=self._allowed_roots)
        return self._agent

    def set_allowed_roots(self, roots: list[Path]) -> None:
        """更新写操作白名单并重建 Agent。"""
        self._allowed_roots = list(roots)
        self._reset_agent()

    def set_ui_context(self, root: Path, recursive: bool) -> None:
        """同步右侧文件面板的根目录，供 Agent 理解「当前目录」。"""
        self._ui_root = root.resolve()
        self._ui_recursive = recursive

    def _reset_agent(self) -> None:
        """配置变化时丢弃已构造的 Agent，下次发送重建。"""
        self._agent = None

    # ---- 操作 ----
    def _open_settings(self) -> None:
        from filemanager.settings_dialog import SettingsDialog

        dlg = SettingsDialog(self)
        dlg.exec()
        self.set_allowed_roots(api_store.get_allowed_roots())
        self._refresh_status()

    def _open_memory(self) -> None:
        from filemanager.memory_dialog import MemoryDialog

        MemoryDialog(self).exec()

    def _new_session(self) -> None:
        if self._agent is not None:
            self._agent.new_session()
        self._stream.clear()
        self._append_system("（已重开会话）")

    def _send(self) -> None:
        text = self._input.text().strip()
        if not text:
            return
        agent = self._ensure_agent()
        if agent is None:
            self._append_system("还没有可用的模型配置。点右上角「设置」添加一个 API 并填入 key。")
            return
        if self._thread is not None and self._thread.isRunning():
            return  # 上一轮还没结束

        self._append_user(text)
        self._input.clear()
        self._set_busy(True)

        th = AgentThread(agent, self._message_with_context(text))
        self._thread = th
        th.step.connect(self._on_step)
        th.confirm_request.connect(self._on_confirm_request)
        th.finished_ok.connect(self._on_finished)
        th.failed.connect(self._on_failed)
        th.start()

    def _message_with_context(self, text: str) -> str:
        if self._ui_root is None:
            return text
        rec = "是" if self._ui_recursive else "否"
        return (
            f"{text}\n"
            f"\n[界面上下文]\n"
            f"右侧文件面板当前根目录: {self._ui_root}\n"
            f"包含子目录: {rec}\n"
            f"说明: 用户说「当前目录」「现在目录」「这个目录」时，默认指此路径，无需再问。"
        )

    # ---- 确认卡片 ----
    def _on_confirm_request(self, info: dict) -> None:
        """收到确认请求:展示清单 + 确认/取消按钮，等待用户点击。"""
        kind = "⚠️ 写操作" if info.get("is_write") else "操作"
        self._confirm_label.setText(f"{kind} · {info.get('name','')}\n\n{info.get('description','')}")
        self._confirm_bar.setVisible(True)
        # 确认期间输入保持禁用（已在 busy 状态）

    def _answer_confirm(self, approved: bool) -> None:
        """用户点击后回传给工作线程，隐藏卡片。"""
        self._confirm_bar.setVisible(False)
        if self._thread is not None:
            self._thread.provide_confirm(approved)
        if not approved:
            self._append_system("（已取消该操作）")

    # ---- 线程信号处理（主线程）----
    def _on_step(self, event: dict) -> None:
        t = event.get("type")
        if t == "tool_call":
            args = ", ".join(f"{k}={v}" for k, v in event.get("args", {}).items())
            self._append_tool(f"→ 调用 {event['name']}({args})")
        elif t == "tool_result":
            first = (event.get("summary") or "").splitlines()[0] if event.get("summary") else ""
            self._append_tool(f"  ↳ {first}")
        # assistant_text / final 在 finished 时统一呈现，避免重复

    def _on_finished(self, reply: str, filesystem_changed: bool) -> None:
        if reply:
            self._append_assistant(reply)
        self._set_busy(False)
        # if filesystem_changed:
        #     self.files_changed.emit()  # 原：驱动右栏 ScanThread；现 Agent 与右栏解耦

    def _on_failed(self, msg: str) -> None:
        self._append_system(f"出错:{msg}")
        self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        self._input.setEnabled(not busy)
        self._btn_send.setEnabled(not busy)
        self._btn_send.setText("…" if busy else "发送")

    # ---- 消息流渲染 ----
    def _append(self, who: str, text: str, color: str) -> None:
        safe = html.escape(text).replace("\n", "<br>")
        self._stream.append(f'<div style="margin:4px 0;"><b style="color:{color}">{who}</b><br>{safe}</div>')
        self._stream.verticalScrollBar().setValue(self._stream.verticalScrollBar().maximum())

    def _append_user(self, text: str) -> None:
        self._append("你", text, "#1565c0")

    def _append_assistant(self, text: str) -> None:
        self._append("助手", text, "#2e7d32")

    def _append_tool(self, text: str) -> None:
        safe = html.escape(text).replace("\n", "<br>")
        self._stream.append(f'<div style="color:#888; font-size:11px; margin:2px 0 2px 12px;">{safe}</div>')
        self._stream.verticalScrollBar().setValue(self._stream.verticalScrollBar().maximum())

    def _append_system(self, text: str) -> None:
        self._append("·", text, "#999")
