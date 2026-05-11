"""表格数据模型与筛选代理：连接「扫描得到的 FileEntry 列表」与 QTableView。

架构说明：
- ``FileTableModel``（源模型）：持有 ``list[FileEntry]``，提供行列数据与自定义 Role。
- ``FileFilterProxy``（``QSortFilterProxyModel``）：在不改源数据的前提下做行过滤与排序；
  视图中 ``setModel(proxy)``，用户看到的是代理后的行号，取路径时需 ``mapToSource``。

自定义 Role（.Qt.UserRole 起）：便于代理在 ``filterAcceptsRow`` / ``lessThan`` 里读取
原始数值（大小、时间戳），避免解析格式化后的显示字符串。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QSortFilterProxyModel

from filemanager.models import FileEntry
from filemanager.profile import _format_size

# 与 QTableView 约定：DisplayRole 给人类可读字符串；以下为机器友好字段
ROLE_PATH = Qt.ItemDataRole.UserRole
ROLE_SIZE = Qt.ItemDataRole.UserRole + 1
ROLE_SUFFIX = Qt.ItemDataRole.UserRole + 2
ROLE_MTIME = Qt.ItemDataRole.UserRole + 3


class FileTableModel(QAbstractTableModel):
    """扫描结果表格的源模型：每一行对应一个 ``FileEntry``。"""

    HEADERS = ["名称", "相对路径", "扩展名", "大小", "修改时间"]

    def __init__(self, root: Path) -> None:
        super().__init__()
        self._root = root.resolve()
        self._entries: list[FileEntry] = []

    def set_root(self, root: Path) -> None:
        """扫描根目录变更时更新，用于「相对路径」列与画像统计根路径一致。"""
        self._root = root.resolve()

    def set_entries(self, entries: list[FileEntry]) -> None:
        """一次性替换全部行数据；``beginResetModel/endResetModel`` 通知视图整表刷新。"""
        self.beginResetModel()
        self._entries = entries
        self.endResetModel()

    def entries(self) -> list[FileEntry]:
        return self._entries

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        # Qt 约定：parent 有效时表示子节点行数；本模型为平面表，仅根层有数据
        return 0 if parent.isValid() else len(self._entries)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: PLR0911
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        if row < 0 or row >= len(self._entries):
            return None
        e = self._entries[row]
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return e.name
            if col == 1:
                return e.relative_display(self._root)
            if col == 2:
                return e.suffix or "—"
            if col == 3:
                return _format_size(e.size)
            if col == 4:
                return e.modified_dt().strftime("%Y-%m-%d %H:%M")
        if role == ROLE_PATH:
            return str(e.path)
        if role == ROLE_SIZE:
            return e.size
        if role == ROLE_SUFFIX:
            return e.suffix
        if role == ROLE_MTIME:
            return e.mtime
        return None

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section]
        return None

    def entry_at_row(self, row: int) -> FileEntry | None:
        """调试或扩展用：按源模型行号取 ``FileEntry``。"""
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None


def _parse_ext_filter(text: str) -> set[str] | None:
    """将输入框中的扩展名列表解析为小写带点的集合；空输入表示「不限制扩展名」返回 None。

    支持中文逗号、忽略空白；未写前导点时自动补上 ``.``。"""
    t = text.strip()
    if not t:
        return None
    parts = [p.strip().lower() for p in t.replace("，", ",").split(",") if p.strip()]
    if not parts:
        return None
    out: set[str] = set()
    for p in parts:
        if not p.startswith("."):
            p = "." + p
        out.add(p)
    return out


class FileFilterProxy(QSortFilterProxyModel):
    """按扩展名、大小、名称子串、修改时间范围筛选当前扫描结果。

    ``set_filters`` 会 ``invalidateFilter``，触发 ``filterAcceptsRow`` 重新评估每一行。
    排序：第 3、4 列（大小、时间）在 ``lessThan`` 中按数值比较，避免字典序比较字符串出错。
    """

    def __init__(self) -> None:
        super().__init__()
        self._exts: set[str] | None = None
        self._min_size: int | None = None
        self._max_size: int | None = None
        self._name_sub: str = ""
        self._min_mtime: float | None = None
        self._max_mtime: float | None = None

    def set_filters(
        self,
        ext_text: str,
        min_size: int | None,
        max_size: int | None,
        name_sub: str,
        min_mtime: float | None = None,
        max_mtime: float | None = None,
    ) -> None:
        self._exts = _parse_ext_filter(ext_text)
        self._min_size = min_size
        self._max_size = max_size
        self._name_sub = name_sub.strip().lower()
        self._min_mtime = min_mtime
        self._max_mtime = max_mtime
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:  # noqa: N802
        # 非顶层索引直接放行（平面表无子项，防御性写法）
        if source_parent.isValid():
            return True
        model = self.sourceModel()
        if not isinstance(model, FileTableModel):
            return True
        idx = model.index(source_row, 0)
        path_s = model.data(idx, ROLE_PATH) or ""
        suffix = (model.data(idx, ROLE_SUFFIX) or "").lower()
        size = int(model.data(idx, ROLE_SIZE) or 0)
        name = Path(path_s).name.lower()

        if self._exts is not None:
            key = suffix if suffix else ""
            if key not in self._exts:
                return False
        if self._min_size is not None and size < self._min_size:
            return False
        if self._max_size is not None and size > self._max_size:
            return False
        if self._name_sub and self._name_sub not in name:
            return False
        mtime = float(model.data(idx, ROLE_MTIME) or 0)
        # 时间与 QDateTimeEdit 使用同一套「本地」Unix 秒级时间戳，可与 stat.st_mtime 比较
        if self._min_mtime is not None and mtime < self._min_mtime:
            return False
        if self._max_mtime is not None and mtime > self._max_mtime:
            return False
        return True

    def lessThan(self, source_left: QModelIndex, source_right: QModelIndex) -> bool:  # noqa: N802
        col = source_left.column()
        model = self.sourceModel()
        if not model:
            return super().lessThan(source_left, source_right)
        if col == 3:
            ls = int(model.data(source_left, ROLE_SIZE) or 0)
            rs = int(model.data(source_right, ROLE_SIZE) or 0)
            return ls < rs
        if col == 4:
            lm = float(model.data(source_left, ROLE_MTIME) or 0)
            rm = float(model.data(source_right, ROLE_MTIME) or 0)
            return lm < rm
        lv = model.data(source_left, Qt.ItemDataRole.DisplayRole) or ""
        rv = model.data(source_right, Qt.ItemDataRole.DisplayRole) or ""
        return str(lv).lower() < str(rv).lower()
