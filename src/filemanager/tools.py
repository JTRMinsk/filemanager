"""工具层:把 ``core`` / ``profile`` / ``fs_ops`` 的能力包成 LLM 可调用的工具。

阶段 3 起的结构:每个工具分两段，由 Agent 在中间插入用户确认——
- ``prepare(name, args, ctx) -> ToolPreview``:计算"将要做什么"（含写操作的受影响清单、护栏过滤），
  不产生副作用，供确认卡片展示。
- ``execute(name, args, ctx, preview) -> ToolResult``:真正执行（写操作在此调 fs_ops 并落日志）。

安全要点:
- **所有工具**默认都要确认（用户选择，最保守）；策略在 Agent 侧，可改。
- **绝不把完整文件列表塞回 LLM**（§4.2）:摘要进上下文，完整结果留 SessionState。
- **扫描封顶** 由设置中的 ``scan_max`` 控制（默认 10000），每次扫描前读取。
- **写操作护栏**:目标路径过 ``guard.check_write_allowed``，系统目录硬禁。
"""

from __future__ import annotations

import string
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from filemanager import api_store, core, guard, oplog
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
    scan_cap: int = field(default_factory=api_store.get_scan_max)
    find_max_results: int = field(default_factory=api_store.get_find_max_results)
    find_max_visited: int = field(default_factory=api_store.get_find_max_visited)


# ===========================================================================
# 摘要 / 清单
# ===========================================================================
def summarize_entries(
    entries: list,
    sample: int = SAMPLE_SIZE,
    scan_root: Path | None = None,
) -> str:
    n = len(entries)
    if n == 0:
        return "0 个文件。"
    total = sum(e.size for e in entries)
    exts = Counter((e.suffix or "(无扩展名)") for e in entries).most_common(8)
    lines = [f"共 {n} 个文件，总大小约 {_format_size(total)}。"]
    lines.append("扩展名分布:" + "，".join(f"{ext} {c}" for ext, c in exts))
    lines.append(f"前 {min(sample, n)} 个文件:")
    for e in entries[:sample]:
        path_str = str(e.path.resolve())
        if scan_root is not None:
            path_str = e.relative_display(scan_root)
        lines.append(f"  {path_str}  {_format_size(e.size)}  {e.modified_dt():%Y-%m-%d %H:%M}")
    if n > sample:
        lines.append(f"  …… 另有 {n - sample} 个未列出（如需精确操作，请用 filter_files 缩小）。")
    if n <= sample:
        lines.append("完整路径:")
        for e in entries:
            lines.append(f"  {e.path.resolve()}")
    return "\n".join(lines)


def _list_volumes() -> list[str]:
    """列出本机可用卷/挂载点。"""
    if sys.platform == "win32":
        return [f"{d}:\\" for d in string.ascii_uppercase if Path(f"{d}:\\").exists()]
    volumes: list[str] = ["/"]
    for candidate in (Path("/Volumes"), Path("/media"), Path("/mnt")):
        if not candidate.is_dir():
            continue
        try:
            for p in candidate.iterdir():
                if p.is_dir():
                    volumes.append(str(p))
        except OSError:
            continue
    return volumes


def _list_user_folders() -> list[tuple[str, Path]]:
    """当前用户常用目录（存在才返回）。"""
    home = Path.home()
    candidates = [
        ("Desktop/桌面", home / "Desktop"),
        ("Documents/文档", home / "Documents"),
        ("Downloads/下载", home / "Downloads"),
        ("Pictures/图片", home / "Pictures"),
        ("WeChat Files/微信文件", home / "Documents" / "WeChat Files"),
    ]
    out: list[tuple[str, Path]] = []
    for label, p in candidates:
        try:
            if p.is_dir():
                out.append((label, p.resolve()))
        except OSError:
            continue
    return out


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


def _parse_exts_from_args(args: dict) -> set[str] | None:
    if not args.get("exts"):
        return None
    exts: set[str] = set()
    for x in args["exts"]:
        x = str(x).strip().lower()
        if x and not x.startswith("."):
            x = "." + x
        if x:
            exts.add(x)
    return exts or None


def _parse_filter_kwargs(args: dict) -> dict:
    return {
        "exts": _parse_exts_from_args(args),
        "min_size": int(args["min_mb"] * 1024 * 1024) if args.get("min_mb") is not None else None,
        "max_size": int(args["max_mb"] * 1024 * 1024) if args.get("max_mb") is not None else None,
        "name_sub": args.get("name_contains", "") or "",
        "min_mtime": _iso_to_ts(args.get("modified_after")),
        "max_mtime": _iso_to_ts(args.get("modified_before")),
    }


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


def _prepare_resolve_path(args, ctx) -> ToolPreview:
    return ToolPreview(description=f"验证路径是否存在:{Path(args['path']).expanduser()}。")


def _prepare_list_volumes(args, ctx) -> ToolPreview:
    return ToolPreview(description="列出本机可用磁盘卷/挂载点。")


def _prepare_find(args, ctx) -> ToolPreview:
    root = Path(args["root"]).expanduser()
    recursive = bool(args.get("recursive", True))
    fk = _parse_filter_kwargs(args)
    conds = []
    if fk["exts"]:
        conds.append(f"扩展名∈{sorted(fk['exts'])}")
    if args.get("min_mb") is not None:
        conds.append(f"≥{args['min_mb']}MB")
    if args.get("max_mb") is not None:
        conds.append(f"≤{args['max_mb']}MB")
    if fk["name_sub"]:
        conds.append(f"名称含「{fk['name_sub']}」")
    cond_s = "、".join(conds) if conds else "（至少建议指定 exts 或大小）"
    return ToolPreview(
        description=(
            f"在 {root} 按条件搜索（递归={recursive}，最多 {ctx.find_max_results} 条匹配）:{cond_s}。"
        )
    )


def _prepare_list_user_folders(args, ctx) -> ToolPreview:
    return ToolPreview(description="列出当前用户常用目录（Desktop/Documents/WeChat Files 等）。")


_PREPARE = {
    "scan_directory": _prepare_scan,
    "filter_files": _prepare_filter,
    "find_files": _prepare_find,
    "preview_file": _prepare_preview,
    "profile_directory": _prepare_profile,
    "copy_files": _prepare_copy,
    "delete_files": _prepare_delete,
    "remember": _prepare_remember,
    "recall": _prepare_recall,
    "resolve_path": _prepare_resolve_path,
    "list_volumes": _prepare_list_volumes,
    "list_user_folders": _prepare_list_user_folders,
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
        capped = (
            f"\n⚠️ 已达扫描上限 {ctx.scan_cap} 个，本次仅为部分采样，"
            f"**不能**据此判断某类型文件是否存在。"
            f"要找特定类型/大小的文件请用 find_files；或选更具体的子目录。"
        )
    return ToolResult(
        summary=(
            f"已扫描 {root}（递归={recursive}）。\n"
            + summarize_entries(entries, scan_root=root.resolve())
            + capped
        ),
        full_data=entries,
    )


def _exec_filter(args, ctx, prep) -> ToolResult:
    base = _working_set(ctx)
    if base is None:
        return ToolResult(summary="还没有扫描结果可筛选。请先调用 scan_directory。")
    fk = _parse_filter_kwargs(args)
    result = core.filter_entries(base, **fk)
    ctx.session.last_filter = result
    scan_root = ctx.session.last_scan_root
    return ToolResult(
        summary=(
            f"筛选后 {len(result)} / {len(base)} 个文件。\n"
            + summarize_entries(result, scan_root=scan_root)
        ),
        full_data=result,
    )


def _exec_find(args, ctx, prep) -> ToolResult:
    root = Path(args["root"]).expanduser()
    recursive = bool(args.get("recursive", True))
    if not root.is_dir():
        return ToolResult(summary=f"错误:不是有效目录 — {root}")
    fk = _parse_filter_kwargs(args)
    try:
        matches, meta = core.find_files(
            root,
            recursive,
            max_results=ctx.find_max_results,
            max_visited=ctx.find_max_visited,
            **fk,
        )
    except OSError as e:
        return ToolResult(summary=f"搜索失败:{e}")
    ctx.session.last_scan = None
    ctx.session.last_scan_root = root.resolve()
    ctx.session.last_filter = matches
    body = summarize_entries(matches, scan_root=root.resolve())
    extra = f"\n（已检查 {meta.visited_count} 个候选文件）"
    if meta.truncated:
        if meta.stopped_reason == "max_results":
            extra += (
                f"\n⚠️ 已达匹配上限 {ctx.find_max_results} 条，可能还有更多符合项；"
                f"请缩小 root 或加条件。"
            )
        elif meta.stopped_reason == "max_visited":
            extra += (
                f"\n⚠️ 已达遍历上限 {ctx.find_max_visited} 个 stat，搜索未穷尽；"
                f"请缩小 root。"
            )
    return ToolResult(
        summary=(
            f"在 {root} 找到 {len(matches)} 个匹配（递归={recursive}）。\n"
            + body
            + extra
        ),
        full_data=matches,
    )


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


def _exec_resolve_path(args, ctx, prep) -> ToolResult:
    raw = Path(args["path"]).expanduser()
    try:
        path = raw.resolve()
    except OSError:
        path = raw
    if not path.is_file():
        return ToolResult(summary=f"不存在: {raw}")
    try:
        size = path.stat().st_size
    except OSError as e:
        return ToolResult(summary=f"存在但无法读取大小: {path} ({e})")
    return ToolResult(summary=f"存在: {path} ({_format_size(size)})")


def _exec_list_volumes(args, ctx, prep) -> ToolResult:
    volumes = _list_volumes()
    if not volumes:
        return ToolResult(summary="未检测到可用卷。")
    return ToolResult(summary="可用卷: " + "，".join(volumes))


def _exec_list_user_folders(args, ctx, prep) -> ToolResult:
    folders = _list_user_folders()
    if not folders:
        return ToolResult(summary="未找到常用用户目录。")
    lines = ["常用目录（微信文件常在 Documents\\WeChat Files 下）:"]
    for label, p in folders:
        lines.append(f"  {label}: {p}")
    return ToolResult(summary="\n".join(lines))


_EXECUTE = {
    "scan_directory": _exec_scan,
    "filter_files": _exec_filter,
    "find_files": _exec_find,
    "preview_file": _exec_preview,
    "profile_directory": _exec_profile,
    "copy_files": _exec_copy,
    "delete_files": _exec_delete,
    "remember": _exec_remember,
    "recall": _exec_recall,
    "resolve_path": _exec_resolve_path,
    "list_volumes": _exec_list_volumes,
    "list_user_folders": _exec_list_user_folders,
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
            description=(
                f"扫描一个目录列出文件（最多 {api_store.DEFAULT_SCAN_MAX} 个，可在设置中调整）。"
                "结果缓存供 filter_files/profile_directory/写操作使用。"
            ),
            parameters={"type": "object", "properties": {
                "root": {"type": "string", "description": "目录绝对路径。"},
                "recursive": {"type": "boolean", "description": "是否递归子目录，默认 true。"},
            }, "required": ["root"]},
        ),
        ToolSpec(
            name="filter_files",
            description="在最近一次 scan_directory 结果上按条件过滤（不能替代 find_files 做全盘搜索）。",
            parameters={"type": "object", "properties": {
                "exts": {"type": "array", "items": {"type": "string"}, "description": "扩展名列表，如 ['pdf','.docx']。"},
                "min_mb": {"type": "number"}, "max_mb": {"type": "number"},
                "name_contains": {"type": "string"},
                "modified_after": {"type": "string", "description": "ISO 日期，如 2024-01-01。"},
                "modified_before": {"type": "string"},
            }},
        ),
        ToolSpec(
            name="find_files",
            description=(
                "按扩展名/大小/名称在目录树中搜索文件（遍历时匹配，不受 scan 上限截断影响）。"
                "用户要找某类文件（如 500MB 的 ppt）时优先用此工具，不要用 scan_directory+filter_files。"
            ),
            parameters={"type": "object", "properties": {
                "root": {"type": "string", "description": "搜索根目录绝对路径。"},
                "recursive": {"type": "boolean", "description": "是否递归子目录，默认 true。"},
                "exts": {"type": "array", "items": {"type": "string"}, "description": "扩展名，如 ['ppt','pptx']。"},
                "min_mb": {"type": "number"},
                "max_mb": {"type": "number"},
                "name_contains": {"type": "string"},
                "modified_after": {"type": "string"},
                "modified_before": {"type": "string"},
            }, "required": ["root"]},
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
        ToolSpec(
            name="resolve_path",
            description="验证单个路径是否存在且为文件，返回绝对路径与大小。用于确认路径，不要为验证路径而 preview_file。",
            parameters={"type": "object", "properties": {
                "path": {"type": "string", "description": "待验证的文件绝对或相对路径。"},
            }, "required": ["path"]},
        ),
        ToolSpec(
            name="list_volumes",
            description="列出本机可用磁盘卷（如 Windows 盘符 C:\\、D:\\）。全盘搜索前先调用，避免扫描不存在的盘。",
            parameters={"type": "object", "properties": {}},
        ),
        ToolSpec(
            name="list_user_folders",
            description=(
                "列出当前用户常用目录绝对路径（Desktop/Documents/Downloads/WeChat Files 等）。"
                "大范围找文件前可先调用以确定 find_files 的 root。"
            ),
            parameters={"type": "object", "properties": {}},
        ),
    ]


TOOL_SPECS = _build_specs()
