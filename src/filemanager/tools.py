"""工具层:把 ``core`` / ``profile`` 的能力包成 LLM 可调用的工具。

阶段 2 只做**只读**工具:scan / filter / preview / profile。
（写操作 copy/delete 在阶段 3 加;记忆 remember/recall 在阶段 4 加。）

关键约束（方案 §4.2）:**绝不把完整文件列表塞回 LLM**。
- 工具结果 ``ToolResult.summary`` 是给模型读的**简短摘要**（总数/大小/扩展名分布/前 N 条样本）。
- 完整结果存进 ``SessionState``（``last_scan`` / ``last_filter``），供后续工具按条件再引用。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from filemanager import core
from filemanager.profile import _format_size, summarize_directory

if TYPE_CHECKING:
    from filemanager.agent import SessionState

# 摘要里最多列举多少个文件名（其余只给计数）
SAMPLE_SIZE = 30


@dataclass
class ToolResult:
    """工具执行结果。``summary`` 进 LLM 上下文;``full_data`` 仅供本地/调试，不进上下文。"""

    summary: str
    full_data: object = None
    needs_confirmation: bool = False  # 破坏性操作用;阶段 2 只读工具恒为 False


@dataclass
class ToolContext:
    """工具执行环境。让工具能读写当前会话状态（如 filter 引用上一次 scan 的结果）。"""

    session: "SessionState"
    allowed_roots: list[Path] = field(default_factory=list)  # 阶段 3 写操作护栏用


# ===========================================================================
# 摘要构造（供 scan / filter 复用）
# ===========================================================================
def summarize_entries(entries: list, sample: int = SAMPLE_SIZE) -> str:
    """把 FileEntry 列表压成给模型读的简短摘要。"""
    n = len(entries)
    if n == 0:
        return "0 个文件。"
    total = sum(e.size for e in entries)
    exts = Counter((e.suffix or "(无扩展名)") for e in entries).most_common(8)
    lines = [f"共 {n} 个文件，总大小约 {_format_size(total)}。"]
    lines.append("扩展名分布:" + "，".join(f"{ext} {c}" for ext, c in exts))
    lines.append(f"前 {min(sample, n)} 个文件:")
    for e in entries[:sample]:
        lines.append(f"  {e.name}  {_format_size(e.size)}  {e.modified_dt():%Y-%m-%d %H:%M}")
    if n > sample:
        lines.append(f"  …… 另有 {n - sample} 个未列出（如需精确操作，请用 filter_files 进一步缩小）。")
    return "\n".join(lines)


def _iso_to_ts(s: str | None) -> float | None:
    """ISO 日期串 → Unix 秒。无效或空返回 None。"""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


# ===========================================================================
# 工具 Schema（给模型看）
# ===========================================================================
TOOL_SPECS: list = []  # 在文件末尾用 base.ToolSpec 填充（避免顶部循环导入）


# ===========================================================================
# 工具 Handler（实际执行）
# ===========================================================================
def _scan_directory(args: dict, ctx: ToolContext) -> ToolResult:
    root = Path(args["root"]).expanduser()
    recursive = bool(args.get("recursive", True))
    if not root.is_dir():
        return ToolResult(summary=f"错误:不是有效目录 — {root}")
    try:
        entries = core.scan_directory(root, recursive)
    except OSError as e:
        return ToolResult(summary=f"扫描失败:{e}")
    # 完整结果存会话，仅摘要进上下文
    ctx.session.last_scan = entries
    ctx.session.last_scan_root = root.resolve()
    ctx.session.last_filter = None
    return ToolResult(
        summary=f"已扫描 {root}（递归={recursive}）。\n" + summarize_entries(entries),
        full_data=entries,
    )


def _filter_files(args: dict, ctx: ToolContext) -> ToolResult:
    base = ctx.session.last_filter if ctx.session.last_filter is not None else ctx.session.last_scan
    if base is None:
        return ToolResult(summary="还没有扫描结果可筛选。请先调用 scan_directory。")
    exts = None
    if args.get("exts"):
        # 接受 ["pdf", ".txt"] 等，统一成小写带点
        exts = set()
        for x in args["exts"]:
            x = str(x).strip().lower()
            if x and not x.startswith("."):
                x = "." + x
            if x:
                exts.add(x)
    min_size = int(args["min_mb"] * 1024 * 1024) if args.get("min_mb") is not None else None
    max_size = int(args["max_mb"] * 1024 * 1024) if args.get("max_mb") is not None else None
    result = core.filter_entries(
        base,
        exts=exts,
        min_size=min_size,
        max_size=max_size,
        name_sub=args.get("name_contains", "") or "",
        min_mtime=_iso_to_ts(args.get("modified_after")),
        max_mtime=_iso_to_ts(args.get("modified_before")),
    )
    ctx.session.last_filter = result
    return ToolResult(
        summary=f"筛选后 {len(result)} / {len(base)} 个文件。\n" + summarize_entries(result),
        full_data=result,
    )


def _preview_file(args: dict, ctx: ToolContext) -> ToolResult:
    path = Path(args["path"]).expanduser()
    pr = core.preview_file(path)
    if pr.kind == "image":
        return ToolResult(summary=f"图片文件:{path}（{path.suffix} 位图，可在界面查看缩略图）。")
    if pr.kind == "error":
        return ToolResult(summary=pr.text)
    # text / hex:截断后给模型（避免大文件灌满上下文）
    head = pr.text[:2000]
    note = "" if len(pr.text) <= 2000 else "\n…（预览内容已截断）"
    label = "文本" if pr.kind == "text" else "二进制/十六进制"
    return ToolResult(summary=f"[{label}预览] {path}\n{head}{note}")


def _profile_directory(args: dict, ctx: ToolContext) -> ToolResult:
    if ctx.session.last_scan is None or ctx.session.last_scan_root is None:
        return ToolResult(summary="还没有扫描结果。请先调用 scan_directory，再生成画像。")
    text = summarize_directory(ctx.session.last_scan_root, ctx.session.last_scan)
    return ToolResult(summary="目录画像（启发式）:\n" + text)


_HANDLERS = {
    "scan_directory": _scan_directory,
    "filter_files": _filter_files,
    "preview_file": _preview_file,
    "profile_directory": _profile_directory,
}


def dispatch(name: str, arguments: dict, ctx: ToolContext) -> ToolResult:
    """按名分发到 handler。未知工具返回错误摘要（不抛异常，交回模型自处理）。"""
    handler = _HANDLERS.get(name)
    if handler is None:
        return ToolResult(summary=f"未知工具:{name}")
    try:
        return handler(arguments, ctx)
    except Exception as e:  # noqa: BLE001 —— 工具内部异常不应崩溃整个循环
        return ToolResult(summary=f"工具 {name} 执行出错:{e}")


# ===========================================================================
# 填充 Schema（放末尾,导入 base 不会与本模块循环）
# ===========================================================================
def _build_specs() -> list:
    from filemanager.llm.base import ToolSpec

    return [
        ToolSpec(
            name="scan_directory",
            description="扫描一个目录，列出其中的文件（不含子目录本身作为条目）。结果会缓存供后续 filter_files / profile_directory 使用。",
            parameters={
                "type": "object",
                "properties": {
                    "root": {"type": "string", "description": "要扫描的目录绝对路径。"},
                    "recursive": {"type": "boolean", "description": "是否递归子目录，默认 true。"},
                },
                "required": ["root"],
            },
        ),
        ToolSpec(
            name="filter_files",
            description="在最近一次扫描（或上一次筛选）的结果上按条件过滤。不传任何条件则返回全部。",
            parameters={
                "type": "object",
                "properties": {
                    "exts": {"type": "array", "items": {"type": "string"}, "description": "扩展名列表，如 ['pdf','.docx']。"},
                    "min_mb": {"type": "number", "description": "最小大小（MB）。"},
                    "max_mb": {"type": "number", "description": "最大大小（MB）。"},
                    "name_contains": {"type": "string", "description": "文件名包含的子串（大小写不敏感）。"},
                    "modified_after": {"type": "string", "description": "修改时间下界，ISO 日期如 2024-01-01。"},
                    "modified_before": {"type": "string", "description": "修改时间上界，ISO 日期。"},
                },
            },
        ),
        ToolSpec(
            name="preview_file",
            description="预览单个文件:图片返回提示，文本返回开头内容，其它返回十六进制摘录。",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string", "description": "文件绝对路径。"}},
                "required": ["path"],
            },
        ),
        ToolSpec(
            name="profile_directory",
            description="对最近一次扫描的目录生成启发式画像（扩展名占比、内容倾向、工程标记）。",
            parameters={"type": "object", "properties": {}},
        ),
    ]


TOOL_SPECS = _build_specs()
