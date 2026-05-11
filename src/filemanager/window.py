"""主窗口：布局 UI、连接扫描线程与表格模型、处理筛选/预览/复制/删除。

数据流（便于断点调试时对照）：
1. 用户点「扫描」→ ``_start_scan`` 创建 ``ScanThread``，只负责磁盘遍历。
2. 线程结束 → ``_on_scan_finished`` 把 ``entries`` 写入 ``FileTableModel``，再 ``_apply_filters`` 刷新代理。
3. ``QTableView`` 的 model 是 ``FileFilterProxy``，选中的 QModelIndex 属于代理坐标；
   取真实路径必须 ``mapToSource`` 后用 ``ROLE_PATH``。

注意：复制/删除/预览读文件仍在 **GUI 主线程** 同步执行，大文件可能短暂卡顿（已知限制）。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QDateTime, Qt, QItemSelection
from PySide6.QtGui import QAction, QFontDatabase, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDateTimeEdit,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTableView,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from filemanager.fs_ops import (
    copy_paths,
    delete_paths_permanent,
    path_expects_recycle_bin,
    trash_paths,
)
from filemanager.profile import summarize_directory
from filemanager.scanner import ScanThread
from filemanager.table_model import ROLE_PATH, FileFilterProxy, FileTableModel

# 预览：限制读盘大小，避免超大文本一次性读入内存拖垮界面
_PREVIEW_MAX_TEXT_BYTES = 512 * 1024
_PREVIEW_HEX_BYTES = 4096
_PREVIEW_IMAGE_MAX_EDGE = 480
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"})


def _parse_mb(s: str) -> int | None:
    """将筛选框中的「MB」小数字符串转为字节数（int）；空或非数字返回 None。"""
    s = s.strip()
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return int(v * 1024 * 1024)


def _is_probably_text(sample: bytes) -> bool:
    """启发式判断字节块是否像文本：前 8KB 内 NUL 则判二进制；可打印 ASCII 比例 ≥ 85% 则判文本。

    用于决定预览区用 UTF-8 解码还是十六进制栅栏视图。"""
    if not sample:
        return True
    if b"\x00" in sample[:8192]:
        return False
    chunk = sample[:8192]
    printable = sum(1 for b in chunk if 32 <= b < 127 or b in (9, 10, 13))
    return printable / len(chunk) >= 0.85


def _format_hex_preview(data: bytes, limit: int) -> str:
    """经典 hex dump：偏移 + 十六进制 + ASCII 列，仅展示前 limit 字节。"""
    chunk = data[:limit]
    lines: list[str] = []
    for i in range(0, len(chunk), 16):
        part = chunk[i : i + 16]
        hx = " ".join(f"{b:02x}" for b in part)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in part)
        lines.append(f"{i:08x}  {hx:<47}  {asc}")
    return "\n".join(lines)


class MainWindow(QMainWindow):
    """应用程序主窗口（单实例）；内部组件通过 ``_build_ui`` 创建与布局。"""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("FileManager — 本地文件批量管理")
        self.resize(1200, 720)

        self._root = Path.home()
        # 源模型存绝对路径列表；代理模型包一层筛选/排序
        self._model = FileTableModel(self._root)
        self._proxy = FileFilterProxy()
        self._proxy.setSourceModel(self._model)
        self._scan_thread: ScanThread | None = None

        self._build_ui()

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)

        # ---------- 顶栏：根路径 + 是否递归 + 扫描 ----------
        dir_row = QHBoxLayout()
        self._path_edit = QLineEdit(str(self._root))
        self._path_edit.setPlaceholderText("要扫描的根目录…")
        btn_browse = QPushButton("浏览…")
        btn_browse.clicked.connect(self._pick_root)
        self._btn_scan = QPushButton("扫描")
        self._btn_scan.clicked.connect(self._start_scan)
        self._recursive = QCheckBox("包含子目录")
        self._recursive.setChecked(True)
        dir_row.addWidget(QLabel("根目录:"), 0)
        dir_row.addWidget(self._path_edit, 1)
        dir_row.addWidget(btn_browse, 0)
        dir_row.addWidget(self._recursive, 0)
        dir_row.addWidget(self._btn_scan, 0)
        root_layout.addLayout(dir_row)

        # ---------- 筛选：只影响代理层，不删源数据 ----------
        filt = QGroupBox("筛选（应用于当前扫描结果）")
        fl = QFormLayout(filt)
        self._filt_ext = QLineEdit()
        self._filt_ext.setPlaceholderText("例: .pdf,.txt 或 pdf,txt；留空表示不限")
        self._filt_min_mb = QLineEdit()
        self._filt_min_mb.setPlaceholderText("最小 MB，可选")
        self._filt_max_mb = QLineEdit()
        self._filt_max_mb.setPlaceholderText("最大 MB，可选")
        self._filt_name = QLineEdit()
        self._filt_name.setPlaceholderText("文件名包含…（可选）")
        btn_apply = QPushButton("应用筛选")
        btn_apply.clicked.connect(self._apply_filters)
        fl.addRow("扩展名:", self._filt_ext)
        h_sz = QHBoxLayout()
        h_sz.addWidget(self._filt_min_mb)
        h_sz.addWidget(QLabel("—"))
        h_sz.addWidget(self._filt_max_mb)
        fl.addRow("大小 (MB):", h_sz)
        fl.addRow("名称:", self._filt_name)
        # 修改时间：仅当勾选「从/至」时参与筛选；时间为本地 QDateTime，与 toSecsSinceEpoch 对齐
        h_mt = QHBoxLayout()
        self._filt_mtime_from_en = QCheckBox("从")
        self._filt_mtime_from = QDateTimeEdit(QDateTime.currentDateTime())
        self._filt_mtime_from.setCalendarPopup(True)
        self._filt_mtime_from.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._filt_mtime_from.setEnabled(False)
        self._filt_mtime_from_en.toggled.connect(self._filt_mtime_from.setEnabled)
        self._filt_mtime_to_en = QCheckBox("至")
        self._filt_mtime_to = QDateTimeEdit(QDateTime.currentDateTime())
        self._filt_mtime_to.setCalendarPopup(True)
        self._filt_mtime_to.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._filt_mtime_to.setEnabled(False)
        self._filt_mtime_to_en.toggled.connect(self._filt_mtime_to.setEnabled)
        h_mt.addWidget(self._filt_mtime_from_en)
        h_mt.addWidget(self._filt_mtime_from, 1)
        h_mt.addWidget(self._filt_mtime_to_en)
        h_mt.addWidget(self._filt_mtime_to, 1)
        fl.addRow("修改时间:", h_mt)
        fl.addRow(btn_apply)
        root_layout.addWidget(filt)

        # ---------- Body：左表右栏 ----------
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._table = QTableView()
        self._table.setModel(self._proxy)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setSortingEnabled(True)
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)
        # 选中变化 → 更新预览（单文件才有内容）
        sm = self._table.selectionModel()
        if sm:
            sm.selectionChanged.connect(self._on_table_selection_changed)
        splitter.addWidget(self._table)

        right = QWidget()
        rv = QVBoxLayout(right)
        # QStackedWidget：占位说明 | 文本预览 | 图片预览 三页互斥显示
        prev_box = QGroupBox("预览（需单选列表中的单个文件）")
        pv = QVBoxLayout(prev_box)
        self._preview_stack = QStackedWidget()
        self._preview_placeholder = QLabel(
            "在列表中选中单个文件后在此预览。\n"
            "图片（png/jpg/…）显示缩略图；文本显示内容；其它文件显示十六进制摘录。"
        )
        self._preview_placeholder.setWordWrap(True)
        self._preview_placeholder.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._preview_text = QPlainTextEdit()
        self._preview_text.setReadOnly(True)
        self._preview_text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self._preview_text.setPlaceholderText("")
        self._preview_image = QLabel()
        self._preview_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_image.setMinimumHeight(160)
        self._preview_image.setStyleSheet("QLabel { background: #f5f5f5; border: 1px solid #ccc; }")
        self._preview_stack.addWidget(self._preview_placeholder)
        self._preview_stack.addWidget(self._preview_text)
        self._preview_stack.addWidget(self._preview_image)
        pv.addWidget(self._preview_stack, 1)
        rv.addWidget(prev_box, 2)
        rv.addWidget(QLabel("目录画像（启发式，仅供参考）"))
        self._profile_view = QPlainTextEdit()
        self._profile_view.setReadOnly(True)
        rv.addWidget(self._profile_view, 1)
        sel_row = QHBoxLayout()
        self._btn_sel_all = QPushButton("全选当前列表")
        self._btn_sel_all.clicked.connect(self._select_all_visible)
        self._btn_sel_clear = QPushButton("清除选择")
        self._btn_sel_clear.clicked.connect(self._table.clearSelection)
        sel_row.addWidget(self._btn_sel_all)
        sel_row.addWidget(self._btn_sel_clear)
        rv.addLayout(sel_row)

        ops_row = QHBoxLayout()
        self._btn_copy = QPushButton("复制到…")
        self._btn_copy.clicked.connect(self._copy_selected)
        self._btn_trash = QPushButton("删除所选")
        self._btn_trash.clicked.connect(self._trash_selected)
        ops_row.addWidget(self._btn_copy)
        ops_row.addWidget(self._btn_trash)
        rv.addLayout(ops_row)
        splitter.addWidget(right)
        splitter.setSizes([780, 420])
        root_layout.addWidget(splitter, 1)

        # 工具栏 / 状态栏
        bar = QToolBar()
        act_rescan = QAction("重新扫描", self)
        act_rescan.triggered.connect(self._start_scan)
        bar.addAction(act_rescan)
        self.addToolBar(bar)

        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage("选择根目录后点击「扫描」。")

    def _pick_root(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择根目录", str(self._root))
        if d:
            self._root = Path(d)
            self._path_edit.setText(d)

    def _current_root(self) -> Path:
        """从输入框解析当前「要扫描的根」；空则当作当前目录 ``.``。"""
        return Path(self._path_edit.text().strip() or ".").expanduser()

    def _apply_filters(self) -> None:
        """把表单条件推入代理模型；扫描结束与手动点「应用筛选」都会调用。"""
        mn = _parse_mb(self._filt_min_mb.text())
        mx = _parse_mb(self._filt_max_mb.text())
        mt_min = (
            float(self._filt_mtime_from.dateTime().toSecsSinceEpoch())
            if self._filt_mtime_from_en.isChecked()
            else None
        )
        mt_max = (
            float(self._filt_mtime_to.dateTime().toSecsSinceEpoch())
            if self._filt_mtime_to_en.isChecked()
            else None
        )
        self._proxy.set_filters(
            self._filt_ext.text(),
            mn,
            mx,
            self._filt_name.text(),
            mt_min,
            mt_max,
        )
        self._status.showMessage(
            f"当前列表显示 {self._proxy.rowCount()} / {self._model.rowCount()} 行。"
        )

    def _start_scan(self) -> None:
        """防重入：若上一线程未结束则直接提示；否则禁用按钮、启动 ScanThread。"""
        if self._scan_thread and self._scan_thread.isRunning():
            QMessageBox.information(self, "扫描中", "已有扫描任务进行中。")
            return
        root = self._current_root()
        if not root.is_dir():
            QMessageBox.warning(self, "路径无效", f"不是有效目录：\n{root}")
            return
        self._root = root.resolve()
        self._model.set_root(self._root)
        self._btn_scan.setEnabled(False)
        self._status.showMessage("正在扫描…")

        th = ScanThread(self._root, self._recursive.isChecked())
        self._scan_thread = th
        th.progress.connect(lambda n: self._status.showMessage(f"已扫描 {n} 个文件…"))
        th.finished_ok.connect(self._on_scan_finished)
        th.failed.connect(self._on_scan_failed)
        th.finished.connect(self._on_thread_finished)
        th.start()

    def _on_thread_finished(self) -> None:
        """QThread 生命周期结束（无论成功失败），恢复扫描按钮。"""
        self._btn_scan.setEnabled(True)

    def _on_scan_failed(self, msg: str) -> None:
        QMessageBox.critical(self, "扫描失败", msg)
        self._status.showMessage("扫描失败。")

    def _on_scan_finished(self, entries: list) -> None:
        self._model.set_entries(entries)
        self._apply_filters()
        text = summarize_directory(self._root, entries)
        self._profile_view.setPlainText(text)
        self._status.showMessage(f"扫描完成：{len(entries)} 个文件。")
        self._update_file_preview()

    def _on_table_selection_changed(self, selected: QItemSelection, deselected: QItemSelection) -> None:
        # Qt 传入新旧选区；预览只关心「当前」选中行数，参数可忽略
        del selected, deselected
        self._update_file_preview()

    def _update_file_preview(self) -> None:
        """仅当选中行数恰好为 1 时加载预览；图片走 QPixmap，否则读前若干字节判断文本/十六进制。"""
        self._preview_image.clear()
        sel = self._table.selectionModel()
        if not sel:
            return
        rows = list(sel.selectedRows())
        if len(rows) != 1:
            self._preview_stack.setCurrentWidget(self._preview_placeholder)
            if len(rows) == 0:
                self._preview_placeholder.setText(
                    "在列表中选中单个文件后在此预览。\n"
                    "图片（png/jpg/…）显示缩略图；文本显示内容；其它文件显示十六进制摘录。"
                )
            else:
                self._preview_placeholder.setText("预览仅在选择单个文件时可用（当前已选中多个）。")
            return

        # 代理索引 → 源模型索引 → ROLE_PATH
        src = self._proxy.mapToSource(rows[0])
        path_s = self._model.data(src, ROLE_PATH)
        if not path_s:
            self._preview_placeholder.setText("无法解析所选文件路径。")
            self._preview_stack.setCurrentWidget(self._preview_placeholder)
            return

        path = Path(path_s)
        if not path.is_file():
            self._preview_placeholder.setText(f"不是可读文件或不存在：\n{path}")
            self._preview_stack.setCurrentWidget(self._preview_placeholder)
            return

        suffix = path.suffix.lower()
        if suffix in _IMAGE_SUFFIXES:
            pix = QPixmap(str(path))
            if pix.isNull():
                self._preview_placeholder.setText("无法作为位图加载（可能已损坏或缺少格式插件）。")
                self._preview_stack.setCurrentWidget(self._preview_placeholder)
                return
            pix = pix.scaled(
                _PREVIEW_IMAGE_MAX_EDGE,
                _PREVIEW_IMAGE_MAX_EDGE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._preview_image.setPixmap(pix)
            self._preview_stack.setCurrentWidget(self._preview_image)
            return

        try:
            size = path.stat().st_size
        except OSError as e:
            self._preview_placeholder.setText(f"无法读取文件：{e}")
            self._preview_stack.setCurrentWidget(self._preview_placeholder)
            return

        read_size = min(size, _PREVIEW_MAX_TEXT_BYTES)
        try:
            with path.open("rb") as f:
                raw = f.read(read_size)
        except OSError as e:
            self._preview_placeholder.setText(f"读取失败：{e}")
            self._preview_stack.setCurrentWidget(self._preview_placeholder)
            return

        note_truncate = ""
        if size > read_size:
            note_truncate = f"\n\n… 仅读取前 {read_size // 1024} KB 用于预览（共约 {size // 1024} KB）。"

        if _is_probably_text(raw):
            text = raw.decode("utf-8", errors="replace") + note_truncate
            self._preview_text.setPlainText(text)
        else:
            hex_part = _format_hex_preview(raw, _PREVIEW_HEX_BYTES)
            if len(raw) > _PREVIEW_HEX_BYTES:
                hex_part += "\n…（十六进制视图已截断）"
            self._preview_text.setPlainText(
                "（二进制/非文本推测）\n\n" + hex_part + note_truncate
            )
        self._preview_text.verticalScrollBar().setValue(0)
        self._preview_stack.setCurrentWidget(self._preview_text)

    def _selected_paths(self) -> list[Path]:
        """当前表格选中行对应的 **绝对路径** 列表（先 mapToSource）。"""
        paths: list[Path] = []
        for idx in self._table.selectionModel().selectedRows():
            src = self._proxy.mapToSource(idx)
            p = self._model.data(src, ROLE_PATH)
            if p:
                paths.append(Path(p))
        return paths

    def _select_all_visible(self) -> None:
        """全选 **代理后当前可见** 的所有行（已受筛选影响）。"""
        sel = self._table.selectionModel()
        if not sel:
            return
        top_left = self._proxy.index(0, 0)
        if not top_left.isValid():
            return
        bottom_right = self._proxy.index(self._proxy.rowCount() - 1, self._model.columnCount() - 1)
        selection = QItemSelection(top_left, bottom_right)
        sel.select(selection, sel.SelectionFlag.Select)

    def _copy_selected(self) -> None:
        paths = self._selected_paths()
        if not paths:
            QMessageBox.information(self, "复制", "请先选择文件。")
            return
        dest = QFileDialog.getExistingDirectory(self, "选择目标文件夹")
        if not dest:
            return
        ok, err = copy_paths(paths, Path(dest))
        msg = f"成功 {len(ok)} 个。"
        if err:
            msg += "\n\n错误:\n" + "\n".join(err[:20])
            if len(err) > 20:
                msg += f"\n… 共 {len(err)} 条错误"
        QMessageBox.information(self, "复制完成", msg)
        self._start_scan()

    def _trash_selected(self) -> None:
        """按卷拆分：可走回收站的用 send2trash；否则 unlink 并已在确认框中说明永久删除。"""
        paths = self._selected_paths()
        if not paths:
            QMessageBox.information(self, "删除", "请先选择文件。")
            return

        recycle_paths = [p for p in paths if path_expects_recycle_bin(p)]
        perm_paths = [p for p in paths if not path_expects_recycle_bin(p)]

        if perm_paths and recycle_paths:
            title = "确认删除"
            text = (
                f"已选中 {len(paths)} 个文件。\n\n"
                f"其中 {len(perm_paths)} 个位于可移动磁盘、网络驱动器或光驱等卷，通常无法按本机回收站方式恢复，"
                f"将直接永久删除。\n"
                f"其余 {len(recycle_paths)} 个将尝试移入回收站（可按系统回收站恢复）。\n\n"
                "是否继续？"
            )
        elif perm_paths:
            title = "确认永久删除"
            text = (
                f"已选中 {len(perm_paths)} 个文件，均在可移动/网络/光驱等卷上。\n"
                "此类位置删除后通常无法从本机回收站恢复，将直接永久删除。\n\n是否继续？"
            )
        else:
            title = "确认移入回收站"
            text = (
                f"将 {len(recycle_paths)} 个文件移入回收站？（可用系统回收站恢复）"
            )

        r = QMessageBox.question(
            self,
            title,
            text,
        )
        if r != QMessageBox.StandardButton.Yes:
            return

        ok: list[str] = []
        err: list[str] = []
        if recycle_paths:
            o, e = trash_paths(recycle_paths)
            ok.extend(o)
            err.extend(e)
        if perm_paths:
            o, e = delete_paths_permanent(perm_paths)
            ok.extend(o)
            err.extend(e)

        msg = f"已处理 {len(ok)} 个。"
        if perm_paths and recycle_paths:
            msg += f"\n（回收站：{len(recycle_paths)} 个；永久删除：{len(perm_paths)} 个）"
        elif perm_paths:
            msg += "\n（均为永久删除）"
        if err:
            msg += "\n\n错误:\n" + "\n".join(err[:20])
        QMessageBox.information(self, "完成", msg)
        self._start_scan()
