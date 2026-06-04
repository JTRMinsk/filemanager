"""记忆查看/编辑窗口:让用户随时看到 Agent 记了什么，可手动改或清空。

记忆是纯文本 MD，这里直接展示全文供编辑（保存即覆盖）。透明可控是设计目标:
用户始终能审查长期记忆的全部内容。
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from filemanager import memory


class MemoryDialog(QDialog):
    """查看 / 编辑 / 清空长期记忆。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("长期记忆")
        self.resize(560, 480)
        self._build_ui()
        self._load()

    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        hint = QLabel("这是助手跨会话记住的内容（纯文本，可直接编辑）。删除/复制等操作不会因这里的内容跳过确认。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray; font-size: 11px;")
        v.addWidget(hint)

        self._edit = QPlainTextEdit()
        v.addWidget(self._edit, 1)

        row = QHBoxLayout()
        self._btn_clear = QPushButton("清空记忆")
        self._btn_clear.clicked.connect(self._on_clear)
        row.addWidget(self._btn_clear)
        row.addStretch(1)
        self._btn_save = QPushButton("保存")
        self._btn_save.clicked.connect(self._on_save)
        self._btn_close = QPushButton("关闭")
        self._btn_close.clicked.connect(self.reject)
        row.addWidget(self._btn_save)
        row.addWidget(self._btn_close)
        v.addLayout(row)

    def _load(self) -> None:
        content = memory.read_all()
        self._edit.setPlainText(content or "（暂无记忆）")

    def _on_save(self) -> None:
        text = self._edit.toPlainText().strip()
        if text == "（暂无记忆）":
            text = ""
        memory.overwrite(text)
        QMessageBox.information(self, "已保存", "记忆已更新。")
        self.accept()

    def _on_clear(self) -> None:
        if QMessageBox.question(self, "清空记忆", "确定清空全部长期记忆？此操作不可恢复。") == QMessageBox.StandardButton.Yes:
            memory.clear()
            self._edit.setPlainText("（暂无记忆）")
            QMessageBox.information(self, "已清空", "长期记忆已清空。")
