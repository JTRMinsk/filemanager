"""包入口：在仓库根执行 ``python -m filemanager`` 时，Python 会运行本模块。

原理：``python -m 包名`` 会查找 ``包/__main__.py`` 并执行；
这里仅转调 ``main()``，与直接运行 ``main.py`` 效果一致，便于开发时统一入口。
"""

from filemanager.main import main

if __name__ == "__main__":
    main()
