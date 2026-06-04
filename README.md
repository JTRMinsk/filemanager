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

### Quick start — GitHub to running

**Prerequisites:** Git, **Python 3.10+** (`python --version`), network for `pip`.

#### 1. Get the code

```bash
git clone https://github.com/JTRMinsk/filemanager.git
cd filemanager
git checkout agentization   # Agent features; use main/master if that is your default branch
```

#### 2. Virtual environment (recommended)

Windows (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

#### 3. Install dependencies

**Right panel only** (scan / filter / copy / delete — no Agent chat):

```bash
pip install -e .
```

**With Agent** (pick one LLM backend):

```bash
pip install -e ".[openai]"      # DeepSeek / OpenAI-compatible API
# pip install -e ".[anthropic]" # Claude
```

#### 4. Launch

```bash
python -m filemanager
```

Or, if `Scripts` is on `PATH`: `filemanager`

#### 5. First-time UI workflow

| Step | Where | Action |
|------|--------|--------|
| 1 | Left — **Settings** | Add a profile (name, backend, model, API key). DeepSeek: backend `deepseek`, Base URL can stay default. Click save; pick as active. |
| 2 | Right — **Root path** | Enter or browse the folder to manage (e.g. `D:\Projects`). |
| 3 | Right — **Scan** | Click **Scan** (optionally check “Include subfolders”; default is off). Table fills up to **500** files. |
| 4 | Right | Use filters, preview (single select), **Copy to…** / **Delete selected** as needed. |
| 5 | Left — chat | Describe tasks in plain language; yellow **confirm** cards appear before writes (copy/delete/remember). |
| 6 | Left — **Memory** | Optional: view/edit long-term notes (`%APPDATA%\filemanager\memory.md`). |

Config, memory, and logs live under **`%APPDATA%\filemanager\`** on Windows (or the platform equivalent) — **not** beside the `.exe`.

#### 6. Verify (optional, no API key)

From repo root:

```bash
python tools/check_phase2.py
python tools/check_phase3.py
python tools/check_phase4.py
```

GUI smoke (offscreen):

```bash
# Windows PowerShell:
$env:QT_QPA_PLATFORM = "offscreen"; python tools/check_phase5_gui.py

# macOS / Linux:
QT_QPA_PLATFORM=offscreen python tools/check_phase5_gui.py
```

#### 7. Package for distribution (optional)

Install pack tooling **and** LLM extra if the built app should use Agent:

```bash
pip install -e ".[pack,openai]"
# or: pip install -e ".[pack,anthropic]"
```

From repo root:

```bash
python -m PyInstaller filemanager.spec --noconfirm
```

Output: **`dist/FileManager/`** — ship the **whole folder** (includes `FileManager.exe` and Qt runtime).

**End user:** copy `dist/FileManager/` to target PC → run `FileManager.exe` → configure API in Settings (same `%APPDATA%` config as dev runs).

---

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

**Requirements:** Python ≥ 3.10, `PySide6`, `send2trash`. Install and run steps: see **Quick start** above.

---

## 中文

### 概览

FileManager 是基于 **PySide6** 的桌面工具，左侧为 **Agent 对话栏**，右侧为**原有文件管理界面**（根目录、扫描、筛选、表格、预览、画像、手动复制/删除）。两侧**扫描结果互不自动同步**。

### 快速上手 — 从 GitHub 到运行

**前置条件：** 已安装 Git、**Python 3.10+**（`python --version`）、可访问网络以下载依赖。

#### 1. 获取代码

```bash
git clone https://github.com/JTRMinsk/filemanager.git
cd filemanager
git checkout agentization   # Agent 功能在此分支；若默认分支已合并可省略
```

#### 2. 虚拟环境（建议）

Windows（PowerShell）：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

macOS / Linux：

```bash
python3 -m venv .venv
source .venv/bin/activate
```

#### 3. 安装依赖

**仅右侧传统文件管理**（扫描 / 筛选 / 复制 / 删除，不用 Agent）：

```bash
pip install -e .
```

**需要 Agent 对话**（任选一种 LLM 后端）：

```bash
pip install -e ".[openai]"      # DeepSeek / OpenAI 兼容 API
# pip install -e ".[anthropic]" # Claude
```

#### 4. 启动

```bash
python -m filemanager
```

若已将 Python 的 `Scripts` 加入 PATH，也可直接：`filemanager`

#### 5. 首次使用流程

| 步骤 | 位置 | 操作 |
|------|------|------|
| 1 | 左侧 **设置** | 新增配置（名称、后端、模型、API Key）。DeepSeek 选后端 `deepseek`，Base URL 可留空。保存并设为当前。 |
| 2 | 右侧 **根目录** | 输入或浏览要管理的文件夹。 |
| 3 | 右侧 **扫描** | 点「扫描」（「包含子目录」默认不勾选；最多 **500** 条）。 |
| 4 | 右侧 | 筛选、单选预览、复制到… / 删除所选。 |
| 5 | 左侧对话 | 用自然语言描述任务；复制/删除/写入记忆前会出现黄色 **确认** 卡片。 |
| 6 | 左侧 **记忆** | 可选：查看/编辑长期记忆（`%APPDATA%\filemanager\memory.md`）。 |

配置、记忆、操作日志均在 **`%APPDATA%\filemanager\`**，**不会**写在 exe 同目录。

#### 6. 离线验收（可选，无需 API Key）

在仓库根目录：

```bash
python tools/check_phase2.py
python tools/check_phase3.py
python tools/check_phase4.py
```

GUI 离屏冒烟：

```powershell
# Windows PowerShell:
$env:QT_QPA_PLATFORM = "offscreen"; python tools/check_phase5_gui.py
```

#### 7. 打包分发（可选）

若打包后的 exe 也要用 Agent，安装时需带上 LLM 可选依赖：

```bash
pip install -e ".[pack,openai]"
# 或: pip install -e ".[pack,anthropic]"
```

在仓库根目录执行：

```bash
python -m PyInstaller filemanager.spec --noconfirm
```

产物：**`dist/FileManager/`** 整个文件夹（含 `FileManager.exe` 与 Qt 运行库），**不要只拷贝单个 exe**。

**最终用户：** 拷贝整个 `FileManager` 文件夹 → 运行 `FileManager.exe` → 在设置里配置 API（与开发运行共用 `%APPDATA%` 下的配置）。

---

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

### 版本与依赖

- 版本见 `pyproject.toml` / `CHANGELOG.md`。
- 运行时：`PySide6`、`send2trash`；可选：`openai`、`anthropic`、`pyinstaller`（`[pack]`）。
- 安装与运行步骤见上文 **快速上手 — 从 GitHub 到运行**。
