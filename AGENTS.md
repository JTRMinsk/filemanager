# FileManager Agent 化技术方案

> 目标:把现有 PySide6 桌面文件管理器改造为「可对话、可拖文件、有记忆」的 Agent 应用。
> 本文档供 Cursor 实现使用。**核心原则:复用现有逻辑,新增能力全部解耦,GUI 改动最小化。**

---

## 0. 现状摘要(实现前必读)

现有代码分层很干净,绝大部分业务逻辑已与 Qt 无关。改造时**严禁重写下列已验证可直接复用的模块**:

| 文件 | Qt 依赖 | 处理方式 |
|---|---|---|
| `models.py` | 无 | **原样复用**(`FileEntry` 数据类) |
| `fs_ops.py` | 无 | **原样复用**(复制/删除/卷类型判断) |
| `profile.py` | 无 | **原样复用**(目录画像) |
| `scanner.py` | 仅 `QThread` 外壳 | 抽出纯函数,外壳改为调用它 |
| `table_model.py` | 筛选逻辑在 proxy 内 | 抽出纯函数,proxy 改为调用它 |
| `window.py` | 全 Qt | 加对话面板 + 拖拽;预览逻辑抽出 |

可直接复用的现有函数签名(实现工具层时调用这些,不要另写):

```python
# fs_ops.py —— 原样使用
copy_paths(paths: list[Path], dest_dir: Path) -> tuple[list[str], list[str]]  # (成功路径, 错误信息)
trash_paths(paths: list[Path]) -> tuple[list[str], list[str]]                  # 回收站
delete_paths_permanent(paths: list[Path]) -> tuple[list[str], list[str]]       # 永久删除
path_expects_recycle_bin(path: Path) -> bool                                   # True=可回收站,False=永久删除

# profile.py —— 原样使用
summarize_directory(root: Path, entries: list[FileEntry]) -> str

# models.py —— 原样使用
FileEntry(path: Path, size: int, mtime: float)
  .name -> str            # 文件名
  .suffix -> str          # 小写扩展名含点,无则空串
  .relative_display(root) -> str
  .modified_dt() -> datetime
```

---

## 1. 目标架构

```
┌──────────────────────────── GUI 层 (PySide6) ────────────────────────────┐
│  window.py (改)                                                          │
│  ┌────────────────────────────┬──────────────────────────────────────┐  │
│  │ 现有左区:                    │ chat_panel.py (新)                     │  │
│  │  顶栏/筛选/文件表格/预览/画像  │  消息流 + 拖文件区 + 输入框 + 新会话按钮 │  │
│  └────────────────────────────┴──────────────────────────────────────┘  │
│  scanner.py(改 run)   table_model.py(改 filter)   agent_thread.py (新)    │
└────────────────────────────────┬─────────────────────────────────────────┘
                                  │ 调用 / 信号
┌─────────────────────────────── 逻辑层 (无 Qt 依赖) ───────────────────────┐
│  agent.py (新)        Agent 循环:LLM ↔ 工具调用 ↔ 记忆                     │
│  llm/ (新)            可切换 LLM 抽象层 + 各家适配器                        │
│  tools.py (新)        工具注册表(把 core/fs_ops 包成 LLM 可调 schema)      │
│  memory.py (新)       记忆系统(MD 为主 + SQLite 为辅 + 向量可选)           │
│  core.py (新)         纯函数:scan_directory / filter_entries / preview_file│
│  models.py fs_ops.py profile.py (原样)                                    │
└───────────────────────────────────────────────────────────────────────────┘
```

目标目录结构:

```
src/filemanager/
  # ── 现有,原样 ──
  __init__.py  __main__.py  main.py
  models.py  fs_ops.py  profile.py
  # ── 现有,小改 ──
  scanner.py          # run() 改为调用 core.scan_directory
  table_model.py      # filterAcceptsRow 改为调用 core.filter_entries
  window.py           # 嵌入 chat_panel;现有功能不动
  # ── 新增:逻辑层(无 Qt)──
  core.py             # 扫描/筛选/预览的纯函数
  tools.py            # 工具定义与分发
  agent.py            # Agent 主循环 + 上下文管理
  memory.py           # 长期记忆
  config.py           # 配置(模型选择、API key、路径约束、阈值)
  llm/
    __init__.py
    base.py           # LLMClient 抽象基类 + 统一消息/工具/响应格式
    anthropic_client.py
    openai_client.py
    ollama_client.py
  # ── 新增:GUI 组件 ──
  chat_panel.py       # 对话面板 widget(拖拽 + 消息流)
  agent_thread.py     # QThread 跑 Agent,避免阻塞界面
```

---

## 2. 核心层 `core.py`(第一步,不碰 GUI)

把分散在 `scanner.py` / `table_model.py` / `window.py` 里的逻辑抽成无 Qt 依赖的纯函数。**逻辑从现有代码原样搬,不要重新设计算法。**

```python
from dataclasses import dataclass
from pathlib import Path
from filemanager.models import FileEntry

# ---- 扫描:逻辑搬自 scanner.ScanThread.run ----
def scan_directory(
    root: Path,
    recursive: bool,
    progress_cb: callable | None = None,   # 可选回调 progress_cb(count)
) -> list[FileEntry]:
    """递归 rglob 或单层 iterdir,仅保留 is_file,逐个 stat 构造 FileEntry。
    单文件 stat 失败跳过;根路径错误抛 OSError 由调用方处理。"""

# ---- 筛选:逻辑搬自 FileFilterProxy.filterAcceptsRow ----
def filter_entries(
    entries: list[FileEntry],
    exts: set[str] | None = None,      # 小写带点,如 {'.pdf', '.txt'};None=不限
    min_size: int | None = None,       # 字节
    max_size: int | None = None,
    name_sub: str = "",                # 小写子串匹配
    min_mtime: float | None = None,    # unix 秒
    max_mtime: float | None = None,
) -> list[FileEntry]:
    """按条件过滤,返回新列表。规则与现有 proxy 完全一致。"""

# ---- 预览:逻辑搬自 window._update_file_preview + 三个 helper ----
@dataclass
class PreviewResult:
    kind: str          # "image" | "text" | "hex" | "error"
    text: str = ""     # text/hex 时的内容;error 时的错误信息
    image_path: str = ""  # image 时的路径(GUI 用 QPixmap 加载;Agent 只读 kind)
    truncated: bool = False

def preview_file(path: Path) -> PreviewResult:
    """图片→image;否则读前 512KB 判文本/二进制→text/hex。
    复用 window.py 的 _is_probably_text / _format_hex_preview 逻辑(一并搬到 core)。"""

# ---- 解析 helper:搬自 table_model._parse_ext_filter / window._parse_mb ----
def parse_ext_filter(text: str) -> set[str] | None: ...
def parse_mb(text: str) -> int | None: ...
```

**改动 `scanner.py`**:`ScanThread.run()` 内部改为 `entries = scan_directory(self._root, self._recursive, self.progress.emit)`,信号 emit 逻辑保留。
**改动 `table_model.py`**:`FileFilterProxy` 改为持有当前筛选条件,`filterAcceptsRow` 调用 `core.filter_entries` 对单条判断(或保留逐行判断但复用同一套规则常量)。
**验收**:`python -m filemanager` GUI 行为与改造前**完全一致**。

---

## 3. 可切换 LLM 抽象层 `llm/`

不同厂商工具调用格式不同,用抽象层统一。Agent 只依赖 `LLMClient`,不依赖具体厂商。

### 3.1 统一格式 `llm/base.py`

```python
from dataclasses import dataclass
from abc import ABC, abstractmethod

# 统一的内部消息格式(各适配器负责与厂商格式互转)
@dataclass
class Message:
    role: str          # "system" | "user" | "assistant" | "tool"
    content: str = ""
    tool_calls: list["ToolCall"] | None = None   # assistant 发起的调用
    tool_call_id: str = ""                        # role=tool 时,对应哪个调用
    tool_name: str = ""

@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict

@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict   # JSON Schema

@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall]      # 空列表表示模型不想调工具,对话结束
    usage_tokens: int = 0

class LLMClient(ABC):
    @abstractmethod
    def chat(self, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        """单轮调用。各适配器内部:统一格式→厂商格式→请求→厂商响应→统一格式。"""

    @abstractmethod
    def count_tokens(self, messages: list[Message]) -> int:
        """估算 token 数,供上下文压缩判断。可用厂商 API 或近似(字符数/4)。"""
```

### 3.2 适配器

- `anthropic_client.py`:用 `anthropic` SDK。工具调用走 `tool_use` / `tool_result` content block;system 走顶层 `system` 参数。
- `openai_client.py`:用 `openai` SDK。工具走 `tools` + `tool_calls`;system 是一条 message。
- `ollama_client.py`:本地模型,走 HTTP(`/api/chat`),工具调用能力依模型而定,需在 prompt 里做兜底。

各 SDK 作为**可选依赖**(`pyproject.toml` extras),按选用的后端安装。`config.py` 决定实例化哪个。

```python
# config.py
def make_llm_client() -> LLMClient:
    backend = CONFIG.llm_backend   # "anthropic" | "openai" | "ollama"
    # 读 API key(环境变量优先,其次配置文件),返回对应 client
```

### 3.3 存储策略(硬规则,不提供任何开关)

**长期记忆与短期会话产物都是用户个人数据,永远只存用户数据目录,与软件本体(.exe)位置无关。** 安装版、便携版一视同仁:便携版拷走只带工具,不带记忆;同一台机器上安装版与便携版共享同一份记忆。**不实现 portable / 跟随 .exe 的选项。**

```python
# config.py
import os, sys
from pathlib import Path

def user_data_dir() -> Path:
    """跨平台用户数据目录,所有记忆/配置/日志的根。绝不解析到 .exe 所在目录。"""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    d = base / "filemanager"
    d.mkdir(parents=True, exist_ok=True)
    return d

# 全部派生自 user_data_dir(),不接受外部覆盖到 .exe 旁边
MEMORY_MD   = user_data_dir() / "memory.md"
MEMORY_DB   = user_data_dir() / "memory.db"
CONFIG_FILE = user_data_dir() / "config.json"
```

> 迁移记忆 = 用户自行拷贝该目录,显式操作,绝不随软件文件夹误带。

---

## 4. 工具层 `tools.py`

把核心能力包成 LLM 可调用的工具。每个工具 = ToolSpec(给模型看) + handler(实际执行)。

### 4.1 工具清单

| 工具名 | 功能 | 底层调用 | 风险 |
|---|---|---|---|
| `scan_directory` | 扫描目录得文件列表 | `core.scan_directory` | 只读 |
| `filter_files` | 在已扫描结果上筛选 | `core.filter_entries` | 只读 |
| `preview_file` | 预览单个文件 | `core.preview_file` | 只读 |
| `profile_directory` | 目录画像 | `profile.summarize_directory` | 只读 |
| `copy_files` | 复制到目标目录 | `fs_ops.copy_paths` | **写,需确认** |
| `delete_files` | 删除(自动区分回收站/永久) | `fs_ops.trash_paths` + `delete_paths_permanent` | **破坏性,强确认** |
| `remember` | 写入一条长期记忆 | `memory.add` | 写记忆 |
| `recall` | 检索长期记忆 | `memory.search` | 只读 |

### 4.2 关键设计:工具结果必须可控大小

扫描可能返回上万文件,**绝不能把完整列表塞回 LLM**。约定:

- `scan_directory` / `filter_files` 的工具结果返回**摘要 + 截断样本**:总数、总大小、扩展名分布、前 N 条(如 50)文件名。完整结果存在 Agent 的**会话状态**里(见 §5.3),供后续工具(复制/删除)用索引或筛选条件引用,而不是让模型逐个列举。
- 工具结果统一结构:

```python
@dataclass
class ToolResult:
    summary: str            # 给模型读的简短文本(已截断)
    full_data: object = None  # 完整数据,留在本地会话状态,不进 LLM 上下文
    needs_confirmation: bool = False  # 破坏性操作:先返回预览,等用户确认
```

### 4.3 安全护栏(Agent 化的重点,务必实现)

1. **破坏性操作两段式**:`delete_files` / `copy_files` 被调用时,handler **不立即执行**,而是:
   - 计算受影响文件清单(删除时用 `path_expects_recycle_bin` 区分回收站/永久);
   - 返回 `needs_confirmation=True` + 清单摘要;
   - GUI 弹出确认框(**复用 `window._trash_selected` 现有的回收站/永久删除警告文案**),用户点"是"后才真正执行。
2. **路径白名单**:`config.py` 配 `allowed_roots`。所有写操作的目标路径必须在白名单子树内,否则 handler 直接拒绝,防止模型被诱导删系统盘。
3. **永久删除显式标注**:清单里明确标出哪些是不可恢复的永久删除。
4. **操作日志**:每次写操作(成功/失败)落 SQLite `operations` 表,可回溯。
5. **dry-run 模式**:`config.dry_run=True` 时,写操作只记录不执行,用于调试。

---

## 5. Agent 主循环 `agent.py`(无 Qt)

### 5.1 循环逻辑

```python
class Agent:
    def __init__(self, llm: LLMClient, tools: ToolRegistry, memory: Memory, config): ...

    def run_turn(self, user_text: str, attached_paths: list[Path],
                 confirm_cb: callable, emit_cb: callable) -> None:
        """
        confirm_cb(plan) -> bool : 破坏性操作时回调 GUI 弹确认框,返回用户是否同意
        emit_cb(event)           : 流式把中间步骤/文本推给 GUI 显示
        """
        # 1. 组装上下文:system(含 MD 记忆) + 历史(可能已压缩) + 本轮 user(含附件路径)
        # 2. 循环:
        #    resp = llm.chat(messages, tools.specs())
        #    若 resp.tool_calls 为空 -> 输出 resp.text,结束本轮
        #    否则逐个执行工具:
        #       - 破坏性且 needs_confirmation -> confirm_cb 询问,拒绝则回填"用户取消"
        #       - 执行 handler,full_data 存会话状态,summary 作为 tool 消息回填
        #    把工具结果加入 messages,继续循环
        # 3. 每轮结束检查上下文长度 -> 必要时压缩(§5.4)
```

### 5.2 拖进来的文件如何进上下文

用户拖文件 + 发消息时,`attached_paths` 一并传入。Agent 把它们作为本轮 user 消息的结构化附件:列出路径、大小、类型;**不自动读全部内容**(可能很大),而是让模型按需调 `preview_file`。

### 5.3 会话状态(短期记忆)

```python
@dataclass
class SessionState:
    messages: list[Message]              # 对话历史(压缩后的)
    last_scan: list[FileEntry] | None    # 最近一次扫描完整结果(供工具引用,不进 LLM)
    last_filter: list[FileEntry] | None
    created_at: float
```

- **存内存**,不落盘。
- 完整扫描/筛选结果存这里,工具用它,LLM 上下文里只有摘要。

### 5.4 上下文过长应对(你要求的机制)

```python
def maybe_compact(self, state: SessionState) -> None:
    tokens = self.llm.count_tokens(state.messages)
    if tokens < self.config.compact_threshold:   # 如 模型上限的 70%
        return
    # 保留:system + 最近 K 轮原文(如 K=4)
    # 压缩:更早的轮次 -> 调 LLM 生成一段"对话摘要",替换原始消息
    # 可选:压缩前把关键事实写入长期记忆(MD/SQLite)
```

要点:工具的大结果本来就不在 messages 里(在 SessionState),所以压缩主要针对对话文本;额外对单条超大 tool 消息做硬截断兜底。

### 5.5 重开 session(你要求的机制)

GUI"新会话"按钮 → `agent.new_session()` → 丢弃 `SessionState`(清空 messages 和缓存的扫描结果),**长期记忆 MD/SQLite 不动**。新 session 开始时重新读 MD 记忆进 system。

---

## 6. 记忆系统 `memory.py`(无 Qt)

三层,按需启用。**第一阶段只做 MD 层**。

### 6.1 第一阶段:Markdown 记忆(主,优先做)

模仿 `CLAUDE.md` 模式:一个人类可读、Agent 可读写的 markdown 文件。

- 位置:`config.MEMORY_MD`(即 `user_data_dir()/memory.md`,见 §3.3;Windows 上落 `%APPDATA%\filemanager\`)。可选每目录 `<dir>/.fm-notes.md`(目录级备注,这个本就属于被管理目录,随目录走)。
- **读**:每个 session 开始时,把 `memory.md` 全文注入 system prompt。
- **写**:`remember` 工具 → 追加/编辑 `memory.md`。Agent 在判断"这条信息值得长期记住"时主动调用(用户偏好、命名约定、对某目录的说明等)。
- 结构示例(Agent 自行维护,纯文本):

```markdown
# 用户偏好
- 下载的 PDF 习惯归到 D:\Docs\PDF
- 删除前总是希望先看清单

# 目录备注
- E:\Projects\old-react :已废弃,node_modules 可清理

# 约定
- "整理"默认指:找出重复/超大/长期未访问文件并建议归档
```

接口:

```python
class Memory:
    def load_markdown(self) -> str: ...           # 读全文进 system
    def add(self, text: str, section: str = "") -> None: ...   # remember 工具
    def search(self, query: str) -> list[str]: ...  # recall;第一阶段=关键词匹配 MD 段落
```

### 6.2 第二阶段:SQLite(辅,结构化数据)

适合 MD 装不下的可查询数据。标准库 `sqlite3`,零额外依赖,单文件 `config.MEMORY_DB`(`user_data_dir()/memory.db`,见 §3.3)。

```sql
CREATE TABLE operations (         -- 操作日志(护栏 §4.3 写这里)
    id INTEGER PRIMARY KEY, ts REAL, kind TEXT,    -- copy/trash/delete
    src TEXT, dest TEXT, result TEXT, error TEXT);
CREATE TABLE dir_profiles (       -- 目录历史画像
    root TEXT PRIMARY KEY, ts REAL, file_count INT, total_size INT, summary TEXT);
CREATE TABLE tags (               -- 用户给文件/目录打的标签
    path TEXT, tag TEXT, note TEXT, ts REAL);
```

### 6.3 第三阶段:向量检索(可选,记忆量大时再加)

> **回答你的问题:sqlite-vec 随软件一起装,用户无需单独安装。** 它是 pip 包,二进制扩展打包在 wheel 内;PyInstaller 会一起收进 dist。仅需注意:依赖 stdlib sqlite3 的 `load_extension` 支持(Windows 官方 Python 默认支持)。

仅当 MD/SQLite 记忆多到无法整体塞进上下文时启用:把记忆条目用 embedding 编码存入 sqlite-vec,`recall` 改为按语义相似度召回 top-k。轻量、无需独立向量数据库服务。embedding 可用所选 LLM 厂商的 embedding 接口或本地小模型。

---

## 7. GUI 改造 `chat_panel.py` + `agent_thread.py`

### 7.1 对话面板 `chat_panel.py`

一个 `QWidget`,内含:消息流(`QListWidget` 或 `QTextBrowser`)+ 拖文件区 + 输入框 + 发送按钮 + **新会话按钮**。

**拖拽**(Qt 原生):

```python
class ChatPanel(QWidget):
    def __init__(self):
        self.setAcceptDrops(True)
        self._pending_attachments: list[Path] = []

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            p = Path(url.toLocalFile())
            if p.exists():
                self._pending_attachments.append(p)
        # 在 UI 上显示已附加的文件芯片(可点 x 移除)
```

> 从**外部资源管理器**拖、或从**现有文件表格**拖都应支持(表格已是 ExtendedSelection,可另配 `setDragEnabled(True)`)。

### 7.2 不阻塞界面 `agent_thread.py`

调 LLM 是网络 IO,**必须放 QThread**(同现有 `ScanThread` 套路),否则界面冻结。

```python
class AgentThread(QThread):
    step = Signal(object)        # 中间步骤/流式文本 -> 追加到消息流
    need_confirm = Signal(object)  # 破坏性操作 -> 主线程弹 QMessageBox
    finished_ok = Signal(str)
    failed = Signal(str)

    def run(self):
        # 调 agent.run_turn,把 emit_cb 接到 self.step.emit
        # confirm_cb 的实现:emit need_confirm 后阻塞等主线程返回(用 QWaitCondition
        #   或事件循环 + 信号回传);用户在主线程点确认框
```

**确认框跨线程**:Agent 线程里需要用户确认时,emit `need_confirm` 信号 → 主线程弹**复用 `window` 现有的回收站/永久删除警告**的 `QMessageBox` → 把结果回传给等待中的 Agent 线程。

### 7.3 嵌入 `window.py`

在现有 `QSplitter` 右侧或新加一栏放 `ChatPanel`。**现有的扫描/筛选/预览/复制/删除功能完全不动**,Agent 是并列的新入口。可选:Agent 操作完文件后,触发现有 `_start_scan` 刷新表格,让两个入口状态一致。

---

## 8. 依赖变更 `pyproject.toml`

```toml
dependencies = [
    "PySide6>=6.6.0",
    "send2trash>=1.8.0",
    # 新增:HTTP(给 ollama / 兜底用)
    "httpx>=0.27",
]

[project.optional-dependencies]
pack = ["pyinstaller>=6.0,<8"]
# 按选用的后端装其一
anthropic = ["anthropic>=0.40"]
openai = ["openai>=1.50"]
# 第三阶段向量检索才需要
vector = ["sqlite-vec>=0.1"]
```

`sqlite3` 是标准库,SQLite 层无需新依赖。

---

## 9. 实施顺序(建议 Cursor 按阶段做,每阶段可独立验收)

**阶段 1 — 解耦(不碰功能,风险最低)**
1. 新建 `core.py`,从 `scanner` / `table_model` / `window` 搬出 `scan_directory` / `filter_entries` / `preview_file` 及 helper。
2. 改 `scanner.ScanThread.run` 和 `table_model.FileFilterProxy` 调用 core。
3. 验收:`python -m filemanager` 行为与改造前完全一致。

**阶段 2 — LLM 抽象 + 工具层(无 GUI,可写脚本测)**
4. 实现 `llm/base.py` + 至少一个适配器(先随便选一家把链路跑通)。
5. 实现 `tools.py`(只读工具先行:scan/filter/preview/profile)。
6. 实现 `agent.py` 主循环 + SessionState + 上下文压缩 + 新会话。
7. 验收:命令行里跟 Agent 多轮对话,能扫描/筛选/画像;能触发压缩;能重开 session。

**阶段 3 — 写操作 + 护栏**
8. 加 `copy_files` / `delete_files` 工具 + 两段式确认 + 路径白名单 + 操作日志。
9. 验收:命令行模拟确认流程,dry-run 下不误删。

**阶段 4 — MD 记忆**
10. 实现 `memory.py` MD 层 + `remember` / `recall` 工具 + session 启动注入。
11. 验收:Agent 能记住偏好,重开 session 后仍生效。

**阶段 5 — GUI 集成**
12. `chat_panel.py`(拖拽 + 消息流 + 新会话按钮)+ `agent_thread.py`(不阻塞 + 跨线程确认)。
13. 嵌入 `window.py`,接好确认框复用。
14. 验收:拖文件进对话框,自然语言驱动扫描/筛选/复制/删除,确认框正常,界面不卡。

**阶段 6(可选,后期)— SQLite 结构化 + 向量检索**
15. 加 SQLite 表(画像历史/标签),`recall` 升级为关键词+结构化查询。
16. 记忆量大时再接 sqlite-vec 做语义检索。

---

## 10. 给 Cursor 的硬约束

- **不要重写** `models.py` / `fs_ops.py` / `profile.py` 的算法;工具层调用其现有函数。
- **不要让 LLM 上下文承载完整文件列表**;大结果走 SessionState + 摘要。
- **所有破坏性操作必须经用户确认**;复用 `window._trash_selected` 的回收站/永久删除警告文案。
- **所有网络/LLM 调用必须在 QThread**,主线程只做 UI。
- **逻辑层(core/agent/tools/memory/llm)保持零 Qt 依赖**,可被 GUI 之外的入口(命令行/测试)复用。
- 阶段 1 完成后,GUI 行为必须与改造前**逐项一致**,作为回归基线。
- **记忆(MD/SQLite)永远只写 `user_data_dir()`(`%APPDATA%` 等用户目录),严禁写到 .exe 所在目录;不实现 portable/跟随软件的任何开关。** 它是用户个人数据,便携版拷走不带记忆。
