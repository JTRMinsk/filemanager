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

from filemanager.core import scan_directory

# 右侧 GUI ScanThread 扫描数量上限（与 Agent tools.SCAN_CAP 独立配置）
GUI_SCAN_MAX = 500


class ScanThread(QThread):
    progress = Signal(int)
    finished_ok = Signal(list)
    failed = Signal(str)

    def __init__(self, root: Path, recursive: bool) -> None:
        super().__init__()
        self._root = root
        self._recursive = recursive

    def run(self) -> None:
        try:
            entries = scan_directory(
                self._root,
                self._recursive,
                progress_cb=self.progress.emit,
                max_files=GUI_SCAN_MAX,
            )
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))
            return
        self.finished_ok.emit(entries)
