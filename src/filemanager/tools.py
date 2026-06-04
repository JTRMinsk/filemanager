"""工具层:把 ``core`` / ``profile`` / ``fs_ops`` 的能力包成 LLM 可调用的工具。

阶段 3 起的结构:每个工具分两段，由 Agent 在中间插入用户确认——
- ``prepare(name, args, ctx) -> ToolPreview``:计算"将要做什么"（含写操作的受影响清单、护栏过滤），
  不产生副作用，供确认卡片展示。
- ``execute(name, args, ctx, preview) -> ToolResult``:真正执行（写操作在此调 fs_ops 并落日志）。

安全要点:
- **所有工具**默认都要确认（用户选择，最保守）；策略在 Agent 侧，可改。
- **绝不把完整文件列表塞回 LLM**（§4.2）:摘要进上下文，完整结果留 SessionState。
- **扫描封顶** ``SCAN_CAP``，超大目录不闷头扫。
- **写操作护栏**:目标路径过 ``guard.check_write_allowed``，系统目录硬禁。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from filemanager import core, guard, oplog
from filemanager.fs_ops import (
    copy_paths,
    delete_paths_permanent,
    path_expects_recycle_bin,
    trash_paths,
)
from filemanager.profile import _format_size, summarize_directory

if TYPE_CHECKING:
    from filemanager.agent import SessionState

SAMPLE_SIZE = 30          # 摘要/清单里最多列举多少个文件名
SCAN_CAP = 500            # Agent scan_directory 扫描数量软上限
WRITE_TOOLS = {"copy_files", "delete_files"}


@dataclass
class ToolResult:
    summary: str                     # 进 LLM 上下文的简短结果
    full_data: object = None         # 完整数据，留本地，不进上下文


@dataclass
class ToolPreview:
    """执行前的预览，供确认卡片展示。"""

    description: str                 # 人类可读"将要做什么"
    is_write: bool = False
    blocked: bool = False            # 护栏完全拦截（无可执行项）→ 不必确认，直接回错误
    blocked_reason: str = ""
    prepared: object = None          # execute 复用的预计算（如已过滤路径）


@dataclass
class ToolContext:
    session: "SessionState"
    allowed_roots: list[Path] = field(default_factory=list)
    scan_cap: int = SCAN_CAP


# ===========================================================================
# 摘要 / 清单
# ===========================================================================
def summarize_entries(entries: list, sample: int = SAMPLE_SIZE) -> str:
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
        lines.append(f"  …… 另有 {n - sample} 个未列出（如需精确操作，请用 filter_files 缩小）。")
    return "\n".join(lines)


def _path_list_preview(paths: list[Path], sample: int = SAMPLE_SIZE) -> str:
    lines = []
    for p in paths[:sample]:
        try:
            lines.append(f"  {p}  ({_format_size(p.stat().st_size)})")
        except OSError:
            lines.append(f"  {p}")
    if len(paths) > sample:
        lines.append(f"  …… 另有 {len(paths) - sample} 个未列出")
    return "\n".join(lines)


def _iso_to_ts(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def _working_set(ctx: ToolContext) -> list | None:
    """当前操作对象:优先上次筛选结果，否则上次扫描结果。"""
    if ctx.session.last_filter is not None:
        return ctx.session.last_filter
    return ctx.session.last_scan


def _resolve_target_paths(args: dict, ctx: ToolContext) -> list[Path]:
    """写操作的目标文件:显式 paths 优先，否则取当前工作集。"""
    if args.get("paths"):
        return [Path(p).expanduser() for p in args["paths"]]
    ws = _working_set(ctx)
    return [e.path for e in ws] if ws else []


# ===========================================================================
# prepare:计算"将要做什么"（无副作用）
# ===========================================================================
def _prepare_scan(args, ctx) -> ToolPreview:
    root = Path(args["root"]).expanduser()
    recursive = bool(args.get("recursive", True))
    return ToolPreview(description=f"扫描目录 {root}（递归={recursive}，最多 {ctx.scan_cap} 个文件）。")


def _prepare_filter(args, ctx) -> ToolPreview:
    base = _working_set(ctx)
    if base is None:
        return ToolPreview(description="（无可筛选的扫描结果，将提示先扫描）")
    conds = []
    if args.get("exts"):
        conds.append(f"扩展名∈{args['exts']}")
    if args.get("min_mb") is not None:
        conds.append(f"≥{args['min_mb']}MB")
    if args.get("max_mb") is not None:
        conds.append(f"≤{args['max_mb']}MB")
    if args.get("name_contains"):
        conds.append(f"名称含“{args['name_contains']}”")
    if args.get("modified_after"):
        conds.append(f"晚于{args['modified_after']}")
    if args.get("modified_before"):
        conds.append(f"早于{args['modified_before']}")
    cond_s = "、".join(conds) if conds else "无条件（全部）"
    return ToolPreview(description=f"在当前 {len(base)} 个文件上筛选:{cond_s}。")


def _prepare_preview(args, ctx) -> ToolPreview:
    return ToolPreview(description=f"预览文件 {Path(args['path']).expanduser()}。")


def _prepare_profile(args, ctx) -> ToolPreview:
    return ToolPreview(description="对最近一次扫描的目录生成启发式画像。")


def _prepare_copy(args, ctx) -> ToolPreview:
    dest = args.get("dest", "")
    if not dest:
        return ToolPreview(description="复制操作缺少目标目录 dest。", is_write=True,
                           blocked=True, blocked_reason="未提供目标目录 dest。")
    dest_p = Path(dest).expanduser()
    targets = _resolve_target_paths(args, ctx)
    if not targets:
        return ToolPreview(description="没有可复制的文件（请先扫描/筛选，或提供 paths）。",
                           is_write=True, blocked=True, blocked_reason="无目标文件。")
    # 护栏:目标目录必须允许写
    g = guard.check_write_allowed(dest_p, ctx.allowed_roots)
    if not g.allowed:
        return ToolPreview(description=f"目标目录被护栏拒绝:{dest_p}\n原因:{g.reason}",
                           is_write=True, blocked=True, blocked_reason=g.reason)
    desc = (
        f"复制 {len(targets)} 个文件到:{dest_p}\n"
        f"{_path_list_preview(targets)}"
    )
    return ToolPreview(description=desc, is_write=True, prepared={"paths": targets, "dest": dest_p})


def _prepare_delete(args, ctx) -> ToolPreview:
    targets = _resolve_target_paths(args, ctx)
    if not targets:
        return ToolPreview(description="没有可删除的文件（请先扫描/筛选，或提供 paths）。",
                           is_write=True, blocked=True, blocked_reason="无目标文件。")
    # 护栏过滤:拆出允许 / 拒绝
    allowed, denied = guard.filter_allowed(targets, ctx.allowed_roots)
    if not allowed:
        reasons = "；".join(f"{p}: {r}" for p, r in denied[:5])
        return ToolPreview(description=f"全部 {len(targets)} 个目标被护栏拒绝。\n{reasons}",
                           is_write=True, blocked=True, blocked_reason="全部目标越界。")
    # 区分回收站 / 永久删除
    recycle = [p for p in allowed if path_expects_recycle_bin(p)]
    perm = [p for p in allowed if not path_expects_recycle_bin(p)]
    lines = [f"⚠️ 删除 {len(allowed)} 个文件:"]
    if recycle:
        lines.append(f"• {len(recycle)} 个移入回收站（可恢复）")
    if perm:
        lines.append(f"• {len(perm)} 个永久删除（不可恢复！位于可移动/网络/光驱等卷）")
    if denied:
        lines.append(f"• {len(denied)} 个被护栏拒绝、将跳过（系统目录或越界）")
    lines.append(_path_list_preview(allowed))
    return ToolPreview(
        description="\n".join(lines),
        is_write=True,
        prepared={"recycle": recycle, "perm": perm, "denied": denied},
    )


def _prepare_remember(args, ctx) -> ToolPreview:
    text = (args.get("text") or "").strip()
    if not text:
        return ToolPreview(description="remember 缺少要记住的内容 text。", is_write=True,
                           blocked=True, blocked_reason="未提供 text。")
    section = (args.get("section") or "其它").strip()
    # is_write=True:写入持久记忆，需确认（即便将来放宽到 writes_only 也会确认）
    return ToolPreview(description=f"写入长期记忆 · 分类「{section}」:\n  {text}", is_write=True)


def _prepare_recall(args, ctx) -> ToolPreview:
    q = (args.get("query") or "").strip()
    return ToolPreview(description=f"检索长期记忆:{q or '（全部）'}")


_PREPARE = {
    "scan_directory": _prepare_scan,
    "filter_files": _prepare_filter,
    "preview_file": _prepare_preview,
    "profile_directory": _prepare_profile,
    "copy_files": _prepare_copy,
    "delete_files": _prepare_delete,
    "remember": _prepare_remember,
    "recall": _prepare_recall,
}


def prepare(name: str, args: dict, ctx: ToolContext) -> ToolPreview:
    fn = _PREPARE.get(name)
    if fn is None:
        return ToolPreview(description=f"未知工具:{name}", blocked=True, blocked_reason="未知工具")
    try:
        return fn(args, ctx)
    except Exception as e:  # noqa: BLE001
        return ToolPreview(description=f"准备 {name} 出错:{e}", blocked=True, blocked_reason=str(e))


# ===========================================================================
# execute:真正执行
# ===========================================================================
def _exec_scan(args, ctx, prep) -> ToolResult:
    root = Path(args["root"]).expanduser()
    recursive = bool(args.get("recursive", True))
    if not root.is_dir():
        return ToolResult(summary=f"错误:不是有效目录 — {root}")
    try:
        entries = core.scan_directory(root, recursive, max_files=ctx.scan_cap)
    except OSError as e:
        return ToolResult(summary=f"扫描失败:{e}")
    ctx.session.last_scan = entries
    ctx.session.last_scan_root = root.resolve()
    ctx.session.last_filter = None
    capped = ""
    if len(entries) >= ctx.scan_cap:
        capped = (f"\n⚠️ 已达扫描上限 {ctx.scan_cap} 个，可能还有更多文件未列入。"
                  f"建议选更具体的子目录，或关闭递归。")
    return ToolResult(
        summary=f"已扫描 {root}（递归={recursive}）。\n" + summarize_entries(entries) + capped,
        full_data=entries,
    )


def _exec_filter(args, ctx, prep) -> ToolResult:
    base = _working_set(ctx)
    if base is None:
        return ToolResult(summary="还没有扫描结果可筛选。请先调用 scan_directory。")
    exts = None
    if args.get("exts"):
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
        base, exts=exts, min_size=min_size, max_size=max_size,
        name_sub=args.get("name_contains", "") or "",
        min_mtime=_iso_to_ts(args.get("modified_after")),
        max_mtime=_iso_to_ts(args.get("modified_before")),
    )
    ctx.session.last_filter = result
    return ToolResult(summary=f"筛选后 {len(result)} / {len(base)} 个文件。\n" + summarize_entries(result),
                      full_data=result)


def _exec_preview(args, ctx, prep) -> ToolResult:
    path = Path(args["path"]).expanduser()
    pr = core.preview_file(path)
    if pr.kind == "image":
        return ToolResult(summary=f"图片文件:{path}（{path.suffix} 位图，可在界面查看缩略图）。")
    if pr.kind == "error":
        return ToolResult(summary=pr.text)
    head = pr.text[:2000]
    note = "" if len(pr.text) <= 2000 else "\n…（预览内容已截断）"
    label = "文本" if pr.kind == "text" else "二进制/十六进制"
    return ToolResult(summary=f"[{label}预览] {path}\n{head}{note}")


def _exec_profile(args, ctx, prep) -> ToolResult:
    if ctx.session.last_scan is None or ctx.session.last_scan_root is None:
        return ToolResult(summary="还没有扫描结果。请先调用 scan_directory，再生成画像。")
    text = summarize_directory(ctx.session.last_scan_root, ctx.session.last_scan)
    return ToolResult(summary="目录画像（启发式）:\n" + text)


def _exec_copy(args, ctx, prep) -> ToolResult:
    data = prep.prepared or {}
    paths, dest = data.get("paths", []), data.get("dest")
    if not paths or dest is None:
        return ToolResult(summary="复制未执行:缺少目标或文件。")
    ok, err = copy_paths(paths, dest)
    for s in ok:
        oplog.log_operation("copy", dest=s, result="ok")
    for e in err:
        oplog.log_operation("copy", dest=str(dest), result="error", detail=e)
    msg = f"已复制 {len(ok)} 个到 {dest}。"
    if err:
        msg += f"\n{len(err)} 个失败:\n" + "\n".join(err[:10])
    return ToolResult(summary=msg)


def _exec_delete(args, ctx, prep) -> ToolResult:
    data = prep.prepared or {}
    recycle, perm, denied = data.get("recycle", []), data.get("perm", []), data.get("denied", [])
    ok: list[str] = []
    err: list[str] = []
    if recycle:
        o, e = trash_paths(recycle)
        ok += o
        err += e
        for s in o:
            oplog.log_operation("trash", src=s, result="ok")
    if perm:
        o, e = delete_paths_permanent(perm)
        ok += o
        err += e
        for s in o:
            oplog.log_operation("delete_permanent", src=s, result="ok")
    # 删除后当前结果已失效，清掉筛选（下次需重新扫描/筛选）
    ctx.session.last_filter = None
    msg = f"已删除 {len(ok)} 个"
    if perm and recycle:
        msg += f"（回收站 {len(recycle)}，永久 {len(perm)}）"
    elif perm:
        msg += "（均为永久删除）"
    if denied:
        msg += f"；跳过 {len(denied)} 个越界目标"
    if err:
        msg += f"\n{len(err)} 个失败:\n" + "\n".join(err[:10])
    return ToolResult(summary=msg)


def _exec_remember(args, ctx, prep) -> ToolResult:
    from filemanager import memory

    text = (args.get("text") or "").strip()
    if not text:
        return ToolResult(summary="未记录:内容为空。")
    section = (args.get("section") or "其它").strip()
    memory.append(text, section)
    return ToolResult(summary=f"已记住（{section}）:{text}")


def _exec_recall(args, ctx, prep) -> ToolResult:
    from filemanager import memory

    items = memory.search(args.get("query", "") or "")
    if not items:
        return ToolResult(summary="长期记忆里没有相关条目。")
    body = "\n".join(f"- {it}" for it in items)
    return ToolResult(summary=f"相关记忆:\n{body}")


_EXECUTE = {
    "scan_directory": _exec_scan,
    "filter_files": _exec_filter,
    "preview_file": _exec_preview,
    "profile_directory": _exec_profile,
    "copy_files": _exec_copy,
    "delete_files": _exec_delete,
    "remember": _exec_remember,
    "recall": _exec_recall,
}


def execute(name: str, args: dict, ctx: ToolContext, preview: ToolPreview) -> ToolResult:
    fn = _EXECUTE.get(name)
    if fn is None:
        return ToolResult(summary=f"未知工具:{name}")
    try:
        return fn(args, ctx, preview)
    except Exception as e:  # noqa: BLE001
        return ToolResult(summary=f"工具 {name} 执行出错:{e}")


# ===========================================================================
# Schema（给模型看）
# ===========================================================================
def _build_specs() -> list:
    from filemanager.llm.base import ToolSpec

    return [
        ToolSpec(
            name="scan_directory",
            description=f"扫描一个目录列出文件（最多 {SCAN_CAP} 个）。结果缓存供 filter_files/profile_directory/写操作使用。",
            parameters={"type": "object", "properties": {
                "root": {"type": "string", "description": "目录绝对路径。"},
                "recursive": {"type": "boolean", "description": "是否递归子目录，默认 true。"},
            }, "required": ["root"]},
        ),
        ToolSpec(
            name="filter_files",
            description="在最近一次扫描/筛选结果上按条件过滤。",
            parameters={"type": "object", "properties": {
                "exts": {"type": "array", "items": {"type": "string"}, "description": "扩展名列表，如 ['pdf','.docx']。"},
                "min_mb": {"type": "number"}, "max_mb": {"type": "number"},
                "name_contains": {"type": "string"},
                "modified_after": {"type": "string", "description": "ISO 日期，如 2024-01-01。"},
                "modified_before": {"type": "string"},
            }},
        ),
        ToolSpec(
            name="preview_file",
            description="预览单个文件:图片提示，文本返回开头，其它返回十六进制摘录。",
            parameters={"type": "object", "properties": {
                "path": {"type": "string", "description": "文件绝对路径。"}}, "required": ["path"]},
        ),
        ToolSpec(
            name="profile_directory",
            description="对最近一次扫描的目录生成启发式画像。",
            parameters={"type": "object", "properties": {}},
        ),
        ToolSpec(
            name="copy_files",
            description="复制文件到目标目录。默认作用于当前筛选/扫描结果，也可用 paths 指定。需用户确认。",
            parameters={"type": "object", "properties": {
                "dest": {"type": "string", "description": "目标目录绝对路径。"},
                "paths": {"type": "array", "items": {"type": "string"}, "description": "可选:明确指定要复制的文件路径;不填则用当前工作集。"},
            }, "required": ["dest"]},
        ),
        ToolSpec(
            name="delete_files",
            description="删除文件（本地固定盘进回收站，可移动/网络/光驱卷永久删除）。默认作用于当前筛选/扫描结果，也可用 paths 指定。需用户确认。",
            parameters={"type": "object", "properties": {
                "paths": {"type": "array", "items": {"type": "string"}, "description": "可选:明确指定要删除的文件路径;不填则用当前工作集。"},
            }},
        ),
        ToolSpec(
            name="remember",
            description=(
                "把一条值得长期记住的信息写入长期记忆（跨会话保留），如用户偏好、目录用途、工作习惯。"
                "用户明确要求记住时，或你判断某信息长期有用时调用。会请用户确认后才写入。"
            ),
            parameters={"type": "object", "properties": {
                "text": {"type": "string", "description": "要记住的内容，一句话。"},
                "section": {"type": "string", "description": "分类，如 用户偏好/目录备注/工作习惯;可省略。"},
            }, "required": ["text"]},
        ),
        ToolSpec(
            name="recall",
            description="检索长期记忆。需要回忆用户偏好或之前记下的信息时调用。",
            parameters={"type": "object", "properties": {
                "query": {"type": "string", "description": "检索关键词;留空返回全部记忆。"},
            }},
        ),
    ]


TOOL_SPECS = _build_specs()
