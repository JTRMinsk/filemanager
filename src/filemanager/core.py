"""核心逻辑层(无 Qt 依赖):扫描、筛选、预览的纯函数。

本模块是 Agent 化改造「阶段 1（解耦）」的产物。其中的逻辑**逐行搬自**:
- ``scan_directory``        ← ``scanner.ScanThread.run``
- ``filter_entries``        ← ``table_model.FileFilterProxy.filterAcceptsRow``
- ``parse_ext_filter``      ← ``table_model._parse_ext_filter``
- ``parse_mb``              ← ``window._parse_mb``
- ``preview_file`` 及两个 helper ← ``window._update_file_preview`` / ``_is_probably_text`` / ``_format_hex_preview``

设计约束:
- 不导入任何 PySide6 / Qt 符号;可被 GUI 之外的入口(命令行、测试、Agent 工具层)复用。
- 行为与改造前的 GUI 完全一致(阶段 1 回归基线)。
- ``scan_directory`` 对**单文件**错误（stat 失败）静默跳过;对**致命**错误（根路径无效、
  ``iterdir`` 失败等）让异常向上抛出,由调用方（``ScanThread`` 包一层 try/except 转 ``failed`` 信号）处理。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from filemanager.models import FileEntry

# ---------------------------------------------------------------------------
# 预览相关常量（原在 window.py;集中到 core 作为单一来源，window 改为从此处导入）
# ---------------------------------------------------------------------------
PREVIEW_MAX_TEXT_BYTES = 512 * 1024  # 文本预览最多读盘字节数，避免超大文件拖垮
PREVIEW_HEX_BYTES = 4096             # 十六进制预览展示的字节数
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"})


# ===========================================================================
# 扫描（搬自 scanner.ScanThread.run）
# ===========================================================================
def scan_directory(
    root: Path,
    recursive: bool,
    progress_cb=None,
) -> list[FileEntry]:
    """遍历 ``root`` 下的文件并构造 ``FileEntry`` 列表（仅文件，目录不作为条目）。

    参数:
        root:        要扫描的根目录。
        recursive:   True 用 ``rglob`` 递归全部子目录;False 用 ``iterdir`` 仅当前层。
        progress_cb: 可选回调 ``progress_cb(count: int)``，每累计 500 个文件调用一次，
                     结束时再调用一次（对应原 ``ScanThread`` 的 ``progress`` 信号节奏）。

    返回:
        ``list[FileEntry]``。

    异常:
        致命错误（根路径无法访问、``iterdir`` 失败、``rglob`` 过程中的顶层异常等）
        以 ``OSError`` / 其它异常形式向上抛出;单个文件 ``stat`` 失败则跳过该文件。
    """
    root = root.resolve()
    entries: list[FileEntry] = []

    if recursive:
        # rglob("*") 递归所有子路径;仅保留 is_file()，目录不作为条目
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            try:
                st = p.stat()
                entries.append(FileEntry(path=p, size=st.st_size, mtime=st.st_mtime))
            except OSError:
                # 单个文件 stat 失败（被删、无权限）则跳过，继续扫其它文件
                continue
            if progress_cb is not None and len(entries) % 500 == 0:
                progress_cb(len(entries))
    else:
        # 仅一层:iterdir 不递归。若 root 无法打开，OSError 向上抛出由调用方处理
        for p in root.iterdir():
            if not p.is_file():
                continue
            try:
                st = p.stat()
                entries.append(FileEntry(path=p, size=st.st_size, mtime=st.st_mtime))
            except OSError:
                continue
            if progress_cb is not None and len(entries) % 500 == 0:
                progress_cb(len(entries))

    if progress_cb is not None:
        progress_cb(len(entries))
    return entries


# ===========================================================================
# 扩展名解析（搬自 table_model._parse_ext_filter）
# ===========================================================================
def parse_ext_filter(text: str) -> set[str] | None:
    """将输入框中的扩展名列表解析为小写带点的集合;空输入返回 None（表示不限制）。

    支持中文逗号、忽略空白;未写前导点时自动补上 ``.``。
    """
    t = text.strip()
    if not t:
        return None
    parts = [p.strip().lower() for p in t.replace("，", ",").split(",") if p.strip()]
    if not parts:
        return None
    out: set[str] = set()
    for p in parts:
        if not p.startswith("."):
            p = "." + p
        out.add(p)
    return out


# ===========================================================================
# 大小解析（搬自 window._parse_mb）
# ===========================================================================
def parse_mb(s: str) -> int | None:
    """将「MB」小数字符串转为字节数(int);空或非数字返回 None。"""
    s = s.strip()
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return int(v * 1024 * 1024)


# ===========================================================================
# 筛选（搬自 table_model.FileFilterProxy.filterAcceptsRow）
# ===========================================================================
def entry_matches(
    e: FileEntry,
    exts: set[str] | None = None,
    min_size: int | None = None,
    max_size: int | None = None,
    name_sub: str = "",
    min_mtime: float | None = None,
    max_mtime: float | None = None,
) -> bool:
    """单条 ``FileEntry`` 是否满足筛选条件。

    这是筛选规则的**唯一来源**:``filter_entries``（列表）与 Qt 代理的
    ``FileFilterProxy.filterAcceptsRow``（逐行）都应调用本函数，避免两份规则漂移。
    规则与改造前的 ``filterAcceptsRow`` 完全一致。

    参数:
        exts:      小写带点的扩展名集合，如 ``{".pdf", ".txt"}``;None 表示不限。
        min_size/max_size: 字节数;None 表示该端不限。
        name_sub:  文件名子串（大小写不敏感，内部会 strip+lower，可传未归一化的原串）。
        min_mtime/max_mtime: Unix 秒级时间戳;None 表示该端不限。
    """
    # FileEntry.suffix 已是小写带点（无扩展名为空串），与原 ROLE_SUFFIX 语义一致
    if exts is not None:
        key = e.suffix if e.suffix else ""
        if key not in exts:
            return False
    if min_size is not None and e.size < min_size:
        return False
    if max_size is not None and e.size > max_size:
        return False
    ns = name_sub.strip().lower()
    if ns and ns not in e.name.lower():
        return False
    if min_mtime is not None and e.mtime < min_mtime:
        return False
    if max_mtime is not None and e.mtime > max_mtime:
        return False
    return True


def filter_entries(
    entries: list[FileEntry],
    exts: set[str] | None = None,
    min_size: int | None = None,
    max_size: int | None = None,
    name_sub: str = "",
    min_mtime: float | None = None,
    max_mtime: float | None = None,
) -> list[FileEntry]:
    """按条件过滤 ``entries``，返回新列表（不修改入参）。逐条委托给 ``entry_matches``。"""
    return [
        e
        for e in entries
        if entry_matches(e, exts, min_size, max_size, name_sub, min_mtime, max_mtime)
    ]


# ===========================================================================
# 预览（搬自 window._update_file_preview + 两个 helper）
# ===========================================================================
def _is_probably_text(sample: bytes) -> bool:
    """启发式判断字节块是否像文本:前 8KB 内有 NUL 判二进制;可打印 ASCII 比例 ≥ 85% 判文本。"""
    if not sample:
        return True
    if b"\x00" in sample[:8192]:
        return False
    chunk = sample[:8192]
    printable = sum(1 for b in chunk if 32 <= b < 127 or b in (9, 10, 13))
    return printable / len(chunk) >= 0.85


def _format_hex_preview(data: bytes, limit: int) -> str:
    """经典 hex dump:偏移 + 十六进制 + ASCII 列，仅展示前 ``limit`` 字节。"""
    chunk = data[:limit]
    lines: list[str] = []
    for i in range(0, len(chunk), 16):
        part = chunk[i : i + 16]
        hx = " ".join(f"{b:02x}" for b in part)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in part)
        lines.append(f"{i:08x}  {hx:<47}  {asc}")
    return "\n".join(lines)


@dataclass
class PreviewResult:
    """预览结果。GUI 据 ``kind`` 决定渲染方式;Agent 工具层通常只读 ``kind`` 与 ``text``。

    - kind == "image": 图片文件，``image_path`` 为路径（GUI 用 QPixmap 加载;
                       注意 core 不验证位图能否解码，加载失败的兜底由 GUI 层处理）。
    - kind == "text":  文本内容在 ``text``。
    - kind == "hex":   非文本，``text`` 为十六进制摘录（已含「二进制/非文本推测」前缀）。
    - kind == "error": ``text`` 为错误说明。
    - truncated:       text/hex 时，是否因超出读盘上限而被截断。
    """

    kind: str
    text: str = ""
    image_path: str = ""
    truncated: bool = False


def preview_file(path: Path) -> PreviewResult:
    """生成单个文件的预览结果（读盘逻辑与原 ``window._update_file_preview`` 一致）。

    图片→kind="image";否则读前 ``PREVIEW_MAX_TEXT_BYTES`` 字节，按文本/二进制判定为
    kind="text" 或 kind="hex"。任何读盘错误返回 kind="error"。
    """
    if not path.is_file():
        return PreviewResult(kind="error", text=f"不是可读文件或不存在：\n{path}")

    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        # core 无 Qt，无法在此校验位图是否可解码;交由 GUI 层 QPixmap 兜底
        return PreviewResult(kind="image", image_path=str(path))

    try:
        size = path.stat().st_size
    except OSError as e:
        return PreviewResult(kind="error", text=f"无法读取文件：{e}")

    read_size = min(size, PREVIEW_MAX_TEXT_BYTES)
    try:
        with path.open("rb") as f:
            raw = f.read(read_size)
    except OSError as e:
        return PreviewResult(kind="error", text=f"读取失败：{e}")

    truncated = size > read_size
    note_truncate = ""
    if truncated:
        note_truncate = (
            f"\n\n… 仅读取前 {read_size // 1024} KB 用于预览"
            f"（共约 {size // 1024} KB）。"
        )

    if _is_probably_text(raw):
        text = raw.decode("utf-8", errors="replace") + note_truncate
        return PreviewResult(kind="text", text=text, truncated=truncated)

    hex_part = _format_hex_preview(raw, PREVIEW_HEX_BYTES)
    if len(raw) > PREVIEW_HEX_BYTES:
        hex_part += "\n…（十六进制视图已截断）"
    text = "（二进制/非文本推测）\n\n" + hex_part + note_truncate
    return PreviewResult(kind="hex", text=text, truncated=truncated)
