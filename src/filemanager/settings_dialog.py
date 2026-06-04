"""设置窗口:管理多个 API 配置，可新增/编辑/删除，并选择当前使用哪个。

存储委托给 ``api_store``（当前明文，换 keyring 只动 api_store）。
界面只负责增删改与选中，不直接碰文件。
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from filemanager import api_store
from filemanager.api_store import ApiProfile
from filemanager.llm.openai_client import DEEPSEEK_BASE_URL, DEEPSEEK_DEFAULT_MODEL

BACKENDS = ["anthropic", "openai", "deepseek", "ollama"]


class SettingsDialog(QDialog):
    """多 API 配置对话框。关闭后可用 ``api_store.get_active()`` 取当前配置。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置 — API 配置")
        self.resize(640, 460)
        self._build_ui()
        self._reload_list()

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)

        # 左:配置列表
        left = QVBoxLayout()
        left.addWidget(QLabel("已配置的 API（★ 为当前使用）:"))
        self._list = QListWidget()
        self._list.currentItemChanged.connect(self._on_select)
        left.addWidget(self._list, 1)
        row = QHBoxLayout()
        self._btn_new = QPushButton("新增")
        self._btn_new.clicked.connect(self._on_new)
        self._btn_del = QPushButton("删除")
        self._btn_del.clicked.connect(self._on_delete)
        self._btn_use = QPushButton("设为当前")
        self._btn_use.clicked.connect(self._on_set_active)
        row.addWidget(self._btn_new)
        row.addWidget(self._btn_del)
        row.addWidget(self._btn_use)
        left.addLayout(row)
        root.addLayout(left, 1)

        # 右:编辑表单
        right = QVBoxLayout()
        form = QFormLayout()
        self._f_name = QLineEdit()
        self._f_name.setPlaceholderText("给这个配置起个名字，如 我的Claude")
        self._f_backend = QComboBox()
        self._f_backend.addItems(BACKENDS)
        self._f_backend.currentTextChanged.connect(self._on_backend_changed)
        self._f_model = QLineEdit()
        self._f_model.setPlaceholderText("模型名，留空用后端默认")
        self._f_base_url = QLineEdit()
        self._f_base_url.setPlaceholderText("https://api.deepseek.com（DeepSeek；OpenAI 可留空）")
        self._f_key = QLineEdit()
        self._f_key.setPlaceholderText("API key")
        self._f_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._show_key = QPushButton("显示")
        self._show_key.setCheckable(True)
        self._show_key.toggled.connect(
            lambda on: self._f_key.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
            )
        )
        key_row = QHBoxLayout()
        key_row.addWidget(self._f_key, 1)
        key_row.addWidget(self._show_key)
        key_w = QWidget()
        key_w.setLayout(key_row)

        form.addRow("名称:", self._f_name)
        form.addRow("后端:", self._f_backend)
        form.addRow("模型:", self._f_model)
        form.addRow("Base URL:", self._f_base_url)
        form.addRow("Key:", key_w)
        right.addLayout(form)

        note = QLabel(
            "提示:Anthropic / OpenAI / DeepSeek 已可用；Ollama 尚未接入。"
            "DeepSeek 选后端 deepseek 即可，Base URL 可留空用默认。"
            "Key 当前以明文保存于用户目录。"
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: gray; font-size: 11px;")
        right.addWidget(note)

        self._btn_save = QPushButton("保存此配置")
        self._btn_save.clicked.connect(self._on_save)
        right.addWidget(self._btn_save)
        right.addStretch(1)

        self._btn_close = QPushButton("关闭")
        self._btn_close.clicked.connect(self.accept)
        right.addWidget(self._btn_close)
        root.addLayout(right, 1)

    # ---- 列表与表单联动 ----
    def _reload_list(self) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        active = api_store.get_active()
        active_name = active.name if active else ""
        for prof in api_store.list_profiles():
            label = ("★ " if prof.name == active_name else "   ") + prof.name + f"  ({prof.backend})"
            item = QListWidgetItem(label)
            item.setData(256, prof.name)  # Qt.UserRole=256，存真实别名
            self._list.addItem(item)
        self._list.blockSignals(False)

    def _selected_name(self) -> str:
        item = self._list.currentItem()
        return item.data(256) if item else ""

    def _on_backend_changed(self, backend: str) -> None:
        """切换后端时预填 DeepSeek 默认项（仅当对应字段为空）。"""
        if backend == "deepseek":
            if not self._f_base_url.text().strip():
                self._f_base_url.setText(DEEPSEEK_BASE_URL)
            if not self._f_model.text().strip():
                self._f_model.setText(DEEPSEEK_DEFAULT_MODEL)

    def _on_select(self, current, _prev) -> None:
        if not current:
            return
        name = current.data(256)
        for prof in api_store.list_profiles():
            if prof.name == name:
                self._f_name.setText(prof.name)
                idx = BACKENDS.index(prof.backend) if prof.backend in BACKENDS else 0
                self._f_backend.setCurrentIndex(idx)
                self._f_model.setText(prof.model)
                self._f_base_url.setText(prof.base_url)
                self._f_key.setText(prof.key)
                break

    def _on_new(self) -> None:
        self._list.setCurrentItem = None
        self._list.clearSelection()
        self._f_name.clear()
        self._f_backend.setCurrentIndex(0)
        self._f_model.clear()
        self._f_base_url.clear()
        self._f_key.clear()
        self._f_name.setFocus()

    def _on_save(self) -> None:
        name = self._f_name.text().strip()
        if not name:
            QMessageBox.warning(self, "缺少名称", "请先给这个配置起个名字。")
            return
        prof = ApiProfile(
            name=name,
            backend=self._f_backend.currentText(),
            model=self._f_model.text().strip(),
            key=self._f_key.text().strip(),
            base_url=self._f_base_url.text().strip(),
        )
        api_store.upsert_profile(prof)
        self._reload_list()
        QMessageBox.information(self, "已保存", f"配置「{name}」已保存。")

    def _on_delete(self) -> None:
        name = self._selected_name()
        if not name:
            return
        if QMessageBox.question(self, "删除", f"删除配置「{name}」？") == QMessageBox.StandardButton.Yes:
            api_store.delete_profile(name)
            self._reload_list()
            self._on_new()

    def _on_set_active(self) -> None:
        name = self._selected_name()
        if not name:
            QMessageBox.information(self, "设为当前", "请先在列表中选择一个配置。")
            return
        api_store.set_active(name)
        self._reload_list()
