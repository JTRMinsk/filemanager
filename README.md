# FileManager

Desktop file utility: scan a root folder, filter and sort files, multi-select batches, copy or delete (Recycle Bin on fixed local drives on Windows; permanent delete on removable/network/CD volumes with a warning), and show a lightweight “directory profile” (heuristic).

---

## English

### Overview

FileManager is a **PySide6 (Qt for Python)** GUI. You pick a directory, optionally include subfolders, then the app walks the tree and lists **files only** (directories are not listed as rows). Filters narrow the list; sorting uses the table headers. When **exactly one file** is selected, a side panel shows a quick preview (common images, text up to a size cap, or a hex snippet). Batch actions operate on the current selection (or “select all visible” after filtering).

### Design principles

- **UI thread stays responsive**: filesystem walks run in a `QThread` (`ScanThread`); results are applied when the scan finishes.
- **Model / view separation**: `FileTableModel` (`QAbstractTableModel`) holds scanned entries; `FileFilterProxy` (`QSortFilterProxyModel`) handles filtering and stable sorting for size/time columns via `lessThan`.
- **Safe deletes**: On **Windows**, fixed local drives use `send2trash` (Recycle Bin). **Removable, network, and CD-ROM volumes** are treated as “no reliable Recycle Bin”: the app warns and **permanently deletes** with `unlink`. Other platforms still use `send2trash` for all paths (subject to OS rules).
- **Directory profile is non-authoritative**: rules + extension statistics only—useful hints, not classification ground truth.

### Core components

| Module | Responsibility |
|--------|----------------|
| `main.py` | Application entry: `QApplication`, `MainWindow`, `sys.exit(app.exec())`. |
| `window.py` | `MainWindow`: path input, scan controls, filter form, `QTableView`, single-file preview panel, profile panel, copy/trash actions. |
| `models.py` | `FileEntry` dataclass: absolute `path`, `size`, `mtime`; helpers for name/suffix/relative path. |
| `table_model.py` | `FileTableModel` columns and `Qt.UserRole` fields for path/size/suffix/time; `FileFilterProxy` for extensions, size range, name substring, mtime range. |
| `scanner.py` | `ScanThread`: recursive `rglob` or single-level `iterdir`, emits file list or errors. |
| `profile.py` | `summarize_directory`: extension histogram, coarse “topic” guesses, root markers (`package.json`, etc.). |
| `fs_ops.py` | `copy_paths`; `trash_paths` (`send2trash`) for fixed local volumes on Windows; `delete_paths_permanent`; `path_expects_recycle_bin` (drive-type heuristic for removable/network/CD). |
| `__main__.py` | Allows `python -m filemanager`. |

### Repository layout

```text
filemanager/
  pyproject.toml          # deps + optional [pack] for PyInstaller
  filemanager.spec        # PyInstaller spec (folder bundle layout)
  CHANGELOG.md            # version history
  README.md               # this document
  src/
    filemanager/
      __init__.py
      __main__.py
      main.py
      window.py
      models.py
      table_model.py
      scanner.py
      profile.py
      fs_ops.py
  build/                  # PyInstaller intermediates (generated)
  dist/                   # packaged output (generated)
```

### Run (development)

From the repository root:

```bash
pip install -e .
python -m filemanager
```

Or, after install, the console script `filemanager` if your Python `Scripts` directory is on `PATH`.

**Requirements:** Python ≥ 3.10, `PySide6`, `send2trash`.

### Packaging (PyInstaller)

Install optional tooling and build:

```bash
pip install -e ".[pack]"
python -m PyInstaller filemanager.spec --noconfirm
```

Output: `dist/FileManager/` containing `FileManager.exe` (Windows) plus `_internal` and Qt plugins. **Distribute the entire folder**, not only the `.exe`.

The spec uses `collect_all("PySide6")` for reliability; the bundle is large. Optional SQL/Web plugin DLL warnings during build are usually harmless for this app.

---

## 中文

### 概览

FileManager 是用 **PySide6** 写的桌面小工具：选定**根目录**，可选择是否**递归子目录**，枚举树上所有**文件**并在表格中展示；通过筛选与排序缩小范围，支持**多选**，**单选时可在侧栏预览**（图片/文本/十六进制摘录）；批量**复制到指定文件夹**或**删除所选**（Windows 下本地固定盘尽量进回收站，外接可移动/网络/光驱等卷为永久删除并会提示）；右侧文本区给出基于扩展名与根目录标记的**目录画像**（启发式说明，仅供参考）。

### 设计理念

- **界面不阻塞**：目录遍历在 `ScanThread`（`QThread`）中执行，扫完后一次性更新模型。
- **模型与视图分离**：底层 `FileTableModel` 存放扫描结果；`FileFilterProxy` 在代理层做筛选与排序，数值列（大小、时间）在 `lessThan` 中按真实数值比较。
- **删除与卷类型**：Windows 上根据卷类型区分：本地固定盘优先 `send2trash` 进回收站；可移动盘、网络盘、光驱等走永久删除并在确认框说明。其它系统仍统一使用 `send2trash`（以系统行为为准）。
- **画像非判定**：只做统计与规则匹配，不充当严格“分类器”。

### 核心模块说明

与上表相同，中文简述：

- **`main` / `window`**：程序入口与主界面布局、信号槽连接；`window` 含单文件预览（图片 / 文本 / 十六进制摘录）。
- **`models`**：单条文件记录 `FileEntry`。
- **`table_model`**：表格模型与筛选代理，扩展名/大小/文件名/修改时间范围在 `set_filters` + `filterAcceptsRow` 中实现。
- **`scanner`**：后台扫描，递归用 `Path.rglob`，仅当前层用 `iterdir`。
- **`profile`**：`summarize_directory` 汇总扩展名占比、内容倾向与常见工程文件/目录标记。
- **`fs_ops`**：复制；按卷类型选择回收站删除或永久删除。

### 代码目录结构

见上文英文部分的树状说明；源代码均在 `src/filemanager/` 下，符合 `src` 布局，由 `pyproject.toml` 中 `package-dir` 指向 `src`。

### 运行方式（开发）

在仓库根目录：

```bash
pip install -e .
python -m filemanager
```

若已将 Python 的 `Scripts` 加入环境变量，也可直接执行 `filemanager`。

### 打包方式（PyInstaller）

```bash
pip install -e ".[pack]"
python -m PyInstaller filemanager.spec --noconfirm
```

生成目录：`dist/FileManager/`。请将**整个 `FileManager` 文件夹**复制到目标机器运行其中的 `FileManager.exe`；勿只拷贝单个 exe。

打包过程中若出现与数据库/Web 等可选 Qt 插件相关的 DLL 缺失告警，一般**不影响**本工具运行。若需减小体积，可后续改为仅收集 `PySide6` 的必要子集（需自行验证无缺件）。

### 版本与依赖

- 版本见 `pyproject.toml` 中 `version`。
- 运行时依赖：`PySide6`、`send2trash`；打包额外可选：`pyinstaller`（`[pack]`）。
