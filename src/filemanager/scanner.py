"""后台扫描目录：在 ``QThread`` 中遍历文件系统，通过信号把结果抛回主线程。

设计要点（勿在主线程里长时间 ``rglob``，否则界面冻结）：
- ``progress``：每累计一定数量文件发一次，供状态栏更新。
- ``finished_ok``：成功结束时携带 ``list[FileEntry]``，由 ``MainWindow`` 写入模型。
- ``failed``：权限错误、根路径无效等异常路径，以字符串描述失败原因。

注意：``run()`` 结束才会 ``emit(finished_ok)``；若需取消扫描，需另行实现 ``requestInterruption`` 等（当前版本未实现）。
"""

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
                # rglob("*") 会递归所有子路径；仅保留 is_file()，目录不作为表格行
                for p in root.rglob("*"):
                    if not p.is_file():
                        continue
                    try:
                        st = p.stat()
                        entries.append(FileEntry(path=p, size=st.st_size, mtime=st.st_mtime))
                    except OSError:
                        # 单个文件 stat 失败（被删、无权限）则跳过，继续扫其它文件
                        continue
                    if len(entries) % 500 == 0:
                        self.progress.emit(len(entries))
            else:
                # 仅一层：iterdir 不递归，适合快速浏览单层目录
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
            # 顶层异常（例如 rglob 过程中罕见错误）：整体失败并通知 UI
            self.failed.emit(str(e))
            return

        self.progress.emit(len(entries))
        self.finished_ok.emit(entries)
