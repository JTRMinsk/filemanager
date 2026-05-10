from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from filemanager.window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("FileManager")
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
