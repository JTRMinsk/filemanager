from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(slots=True)
class FileEntry:
    """单条文件记录（不含目录节点）。"""

    path: Path
    size: int
    mtime: float

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def suffix(self) -> str:
        s = self.path.suffix
        return s.lower() if s else ""

    def relative_display(self, root: Path) -> str:
        try:
            return str(self.path.resolve().relative_to(root.resolve()))
        except ValueError:
            return str(self.path)

    def modified_dt(self) -> datetime:
        return datetime.fromtimestamp(self.mtime)
