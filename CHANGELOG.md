# Changelog

All notable changes to this project are documented in this file.

## [0.4.0] — 2026-06-04

### Added

- **Agent 对话面板**（窗口左侧）：自然语言驱动扫描、筛选、预览、复制、删除；支持 DeepSeek / OpenAI 兼容 API / Anthropic（可选依赖）。
- **LLM 抽象层**（`llm/`）、**工具层**（`tools.py`）、**Agent 主循环**（`agent.py`）；离线验收脚本 `tools/check_phase2.py` … `check_phase5_gui.py`。
- **写操作两段式确认**：黄色确认卡片（含跨线程 `AgentThread`）；`confirm_mode=all` 时只读工具亦需确认。
- **路径护栏**（`guard.py`）、**操作日志**（`oplog.py` → `%APPDATA%\filemanager\memory.db`）。
- **API 配置**（`api_store.py` + 设置对话框）：多 profile、`allowed_roots` 白名单。
- **长期记忆**（`memory.py` + 「记忆」对话框）：`remember` / `recall` 工具，写入需确认；记忆注入系统提示，**不能**绕过删除确认。
- **`core.py`**：扫描 / 筛选 / 预览纯函数，供 GUI 与 Agent 共用。

### Changed

- **主界面布局**：左栏 Agent，右栏保留原有扫描 / 筛选 / 表格 / 预览 / 画像 / 手动复制删除。
- **两条扫描管线独立**：
  - **右侧 GUI**（`ScanThread`）：上限 `GUI_SCAN_MAX = 500`（`scanner.py`）；默认**不勾选**「包含子目录」。
  - **Agent** `scan_directory`：上限 `SCAN_CAP = 500`（`tools.py`），与 GUI **分开配置**。
- **Agent 不再触发右侧 rescan**：`copy_files` / `delete_success` 后不再 `files_changed → _start_scan`；右栏仅在用户点「扫描」或手动复制/删除后刷新列表。
- **界面上下文**：Agent 消息附带右侧当前根目录，便于理解「现在目录」。
- 移除顶栏冗余 **「重新扫描」** 工具栏（与根目录行「扫描」重复）。

### Fixed

- Agent 纯文本回复（如追问目录）误触发右侧对用户主目录的递归全盘扫描。
- `MainWindow` 启动时 `_sync_chat_ui_context` 调用顺序（须在 `_path_edit` 创建之后）。

### Notes

- 用户数据（配置、记忆、日志）均在 `%APPDATA%\filemanager\`（安装版与便携版共享，不随 exe 目录走）。详见 `AGENTS.md`。
- 可选 LLM 依赖：`pip install -e ".[openai]"` 或 `".[anthropic]"`。

## [0.3.1] — 2026-05-11

### Fixed

- **外接卷删除说明与行为**：在 Windows 上对可移动盘、网络驱动器、光驱等卷不再按「移入回收站」处理；确认框明确提示将**永久删除**，并使用 `Path.unlink`。本地固定盘仍使用 `send2trash` 进回收站。混合选中时分别处理两类路径。主界面按钮文案改为「删除所选」。

### Notes

- 识别依据为 `GetDriveTypeW`：被系统标为「固定」的外接硬盘仍可能走回收站，与资源管理器行为一致。

### Documentation

- 各模块补充中文注释（模块说明、数据流、Qt 模型/线程约定等），不改变运行逻辑。

## [0.3.0] — 2026-05-11

### Added

- **单文件预览**：右侧上方新增预览区；仅在列表中**恰好选中一个文件**时生效。常见位图（png / jpg / jpeg / bmp / gif / webp）显示缩略图；内容按文本解码预览（UTF-8，最多约 512 KB）；否则显示十六进制摘录（前 4 KB 片段）。多选或未选时显示提示文案。

## [0.2.0] — 2026-05-11

### Added

- **修改时间范围筛选**：在「筛选」分组中可增加「从 / 至」修改时间（本地时区），需勾选对应复选框后生效；与扩展名、大小、名称条件组合使用。比较规则：`mtime >= 起始` 且 `mtime <= 结束`（与文件系统记录的修改时间一致）。

### Notes

- 若需覆盖一整天，请将「至」设为当日 23:59（或次日 00:00 之前一秒），因筛选按具体日期时间比较，而非仅日期。
