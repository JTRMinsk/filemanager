"""应用程序入口：创建 QApplication、主窗口并进入 Qt 事件循环。

说明：
- ``QApplication`` 管理全局 Qt 状态（字体、样式、事件分发）；整个进程通常只需一个实例。
- ``app.exec()`` 阻塞直到所有窗口关闭，返回值可作进程退出码。
- ``MainWindow`` 内处理具体业务逻辑；此处保持瘦入口，便于测试或以后替换为其它窗口。
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from filemanager.window import MainWindow


def main() -> None:
    # sys.argv 传给 QApplication，以便支持部分 Qt 平台插件参数（一般桌面应用可照惯例传入）
    app = QApplication(sys.argv)
    app.setApplicationName("FileManager")
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
