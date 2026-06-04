# FileManager

Desktop file utility with an optional **AI Agent** side panel: scan a root folder, filter and sort files, multi-select batches, copy or delete (Recycle Bin on fixed local drives on Windows; permanent delete on removable/network/CD volumes with a warning), directory profile (heuristic), and natural-language file operations via configured LLM.

---

## English

### Overview

FileManager is a **PySide6 (Qt for Python)** app with two parallel entry points:

| Area | Role |
|------|------|
| **Left — Chat panel** | Agent: scan / filter / preview / copy / delete / remember preferences via tools + confirmation cards. |
| **Right — Classic UI** | Original file manager: root path, **Scan** button, filters, table, preview, profile, manual copy/delete. |

The two **do not share scan state**: Agent results live in session memory; the table shows only what the right panel has scanned.

### Scan behaviour (important)

Two independent pipelines, **each capped at 500 files** (separate constants):

| Pipeline | Constant | Where |
|----------|----------|--------|
| Right GUI `ScanThread` | `GUI_SCAN_MAX = 500` | `scanner.py` |
| Agent `scan_directory` tool | `SCAN_CAP = 500` | `tools.py` |

- **Default**: “Include subfolders” is **unchecked** on the right panel (single-level scan unless you enable recursion).
- **Agent copy/delete does not auto-rescan** the right table; use **Scan** manually if you need the table refreshed.
- When a scan hits the 500 limit, the status bar notes that more files may exist.

### Agent & configuration

- **Settings** (chat panel): add API profiles (DeepSeek / OpenAI-compatible / Anthropic / Ollama placeholder).
- **Memory** button: view/edit long-term notes in `%APPDATA%\filemanager\memory.md`.
- **Allowed roots** (optional): empty = user home subtree only for Agent writes; configurable in Settings.
- User data (config, memory, operation log) always under **`%APPDATA%\filemanager\`** — not next to the `.exe`.

Destructive Agent actions require **confirmation** in the chat panel. Memory helps the model understand preferences but **does not** skip delete/copy confirmation.

### Design principles (classic UI)

- **UI thread stays responsive**: filesystem walks run in `QThread` (`ScanThread`).
- **Model / view separation**: `FileTableModel` + `FileFilterProxy`.
- **Safe deletes**: Windows fixed drives → Recycle Bin; removable/network/CD → permanent delete with warning.
- **Directory profile is non-authoritative**: heuristic hints only.

### Core components

| Module | Responsibility |
|--------|----------------|
| `main.py` / `window.py` | Entry; split layout (chat + classic UI). |
| `chat_panel.py` / `agent_thread.py` | Agent UI and background LLM turns. |
| `agent.py` / `tools.py` / `llm/` | Agent loop, tool registry, LLM adapters. |
| `core.py` | Pure scan / filter / preview (shared by GUI and Agent). |
| `scanner.py` | `ScanThread` for the right panel (`GUI_SCAN_MAX`). |
| `guard.py` / `oplog.py` / `memory.py` | Write guards, operation log, MD memory. |
| `models.py` / `table_model.py` / `profile.py` / `fs_ops.py` | Unchanged core file ops and table model. |

See `AGENTS.md` for full agentization architecture.

### Repository layout

```text
filemanager/
  pyproject.toml
  CHANGELOG.md
  README.md
  AGENTS.md                 # Agent implementation spec
  src/filemanager/          # application package
  tools/
    check_phase2.py …       # offline acceptance (no API key for phase 2–4)
    check_phase5_gui.py     # GUI smoke (QT_QPA_PLATFORM=offscreen)
    cli_chat.py             # CLI Agent (needs API key)
```

### Run (development)

```bash
pip install -e .
# Optional LLM backend:
pip install -e ".[openai]"    # DeepSeek / OpenAI-compatible
# pip install -e ".[anthropic]"

python -m filemanager
```

Configure API key in **Settings** after first launch, or use `tools/check_phase2.py` without a key.

**Requirements:** Python ≥ 3.10, `PySide6`, `send2trash`.

### Packaging (PyInstaller)

```bash
pip install -e ".[pack]"
python -m PyInstaller filemanager.spec --noconfirm
```

Output: `dist/FileManager/` — distribute the **entire folder**, not only the `.exe`.

---

## 中文

### 概览

FileManager 是基于 **PySide6** 的桌面工具，左侧为 **Agent 对话栏**，右侧为**原有文件管理界面**（根目录、扫描、筛选、表格、预览、画像、手动复制/删除）。两侧**扫描结果互不自动同步**。

### 扫描说明

| 管线 | 上限常量 | 位置 |
|------|----------|------|
| 右侧 GUI | `GUI_SCAN_MAX = 500` | `scanner.py` |
| Agent 工具 | `SCAN_CAP = 500` | `tools.py` |

- 右侧默认**不勾选**「包含子目录」。
- Agent 复制/删除成功后**不会**自动触发右侧 rescan；需要时手动点「扫描」。
- 达到 500 条上限时，状态栏会提示可能还有更多文件。

### Agent 与配置

- **设置**：配置 DeepSeek / OpenAI 兼容 / Anthropic 等 API。
- **记忆**：编辑 `%APPDATA%\filemanager\memory.md` 中的长期记忆。
- **允许操作的目录**：留空则 Agent 写操作仅限用户主目录子树。
- 配置与记忆均在 **`%APPDATA%\filemanager\`**，不随 exe 目录迁移。

破坏性操作需在对话题确认；记忆仅帮助理解偏好，**不能**跳过删除/复制确认。

### 运行（开发）

```bash
pip install -e .
pip install -e ".[openai]"   # 若使用 DeepSeek / OpenAI
python -m filemanager
```

离线验收（无需 API key）：

```bash
python tools/check_phase2.py
python tools/check_phase3.py
python tools/check_phase4.py
set QT_QPA_PLATFORM=offscreen
python tools/check_phase5_gui.py
```

### 打包

同英文节；生成 `dist/FileManager/` 整目录分发。

### 版本与依赖

- 版本见 `pyproject.toml` / `CHANGELOG.md`。
- 运行时：`PySide6`、`send2trash`；可选：`openai`、`anthropic`、`pyinstaller`（`[pack]`）。
