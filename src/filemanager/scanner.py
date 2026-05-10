from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from filemanager.models import FileEntry


class ScanThread(QThread):
    progress = Signal(int)
    finished_ok = Signal(list)
    failed = Signal(str)

    def __init__(self, root: Path, recursive: bool) -> None:
        super().__init__()
        self._root = root
        self._recursive = recursive

    def run(self) -> None:
        root = self._root.resolve()
        entries: list[FileEntry] = []
        try:
            if self._recursive:
                for p in root.rglob("*"):
                    if not p.is_file():
                        continue
                    try:
                        st = p.stat()
                        entries.append(FileEntry(path=p, size=st.st_size, mtime=st.st_mtime))
                    except OSError:
                        continue
                    if len(entries) % 500 == 0:
                        self.progress.emit(len(entries))
            else:
                try:
                    it = root.iterdir()
                except OSError as e:
                    self.failed.emit(str(e))
                    return
                for p in it:
                    if not p.is_file():
                        continue
                    try:
                        st = p.stat()
                        entries.append(FileEntry(path=p, size=st.st_size, mtime=st.st_mtime))
                    except OSError:
                        continue
                    if len(entries) % 500 == 0:
                        self.progress.emit(len(entries))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))
            return

        self.progress.emit(len(entries))
        self.finished_ok.emit(entries)
