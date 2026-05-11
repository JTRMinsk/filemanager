"""扫描结果中的「单文件」数据结构，与 UI 表格一行对应（不包含目录作为单独行）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(slots=True)
class FileEntry:
    """单条文件记录（不含目录节点）。

    - ``path``：绝对或规范化路径，表中通过代理取 ``ROLE_PATH`` 字符串使用。
    - ``size``：字节数，来自 ``os.stat_result.st_size``。
    - ``mtime``：修改时间，Unix 时间戳（秒，浮点），与 ``stat.st_mtime`` 一致，便于排序与筛选。
    """

    path: Path
    size: int
    mtime: float

    @property
    def name(self) -> str:
        """文件名（含扩展名），用于展示第一列。"""
        return self.path.name

    @property
    def suffix(self) -> str:
        """扩展名小写形式（含前导点）；无扩展名则为空字符串，与 profile 中「无扩展名」统计一致。"""
        s = self.path.suffix
        return s.lower() if s else ""

    def relative_display(self, root: Path) -> str:
        """相对 ``root`` 的显示路径；若无法相对（例如不同盘符），则退回绝对路径字符串。"""
        try:
            return str(self.path.resolve().relative_to(root.resolve()))
        except ValueError:
            return str(self.path)

    def modified_dt(self) -> datetime:
        """将 ``mtime`` 转为本地 ``datetime``，仅用于界面格式化显示。"""
        return datetime.fromtimestamp(self.mtime)
