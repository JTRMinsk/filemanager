"""find_files 回归:scan 被 cap 截断时仍能通过 find 命中深层目标文件。

运行:
  python tools/check_find_files.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from filemanager import core
from filemanager.agent import SessionState
from filemanager.tools import ToolContext, execute, prepare


def _fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def _ok(msg: str) -> None:
    print(f"  OK  {msg}")


def test_find_after_scan_cap_misses() -> None:
    """junk 文件超过 scan_max，scan+filter 找不到 ppt，find_files 仍能命中。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        deep = root / "nested" / "wechat" / "FileStorage" / "2026-05"
        deep.mkdir(parents=True)
        target = deep / "big-report.pptx"
        target.write_bytes(b"x" * (500 * 1024))  # 500 KB，用 min_mb 测大小过滤

        for i in range(12_000):
            (root / f"junk_{i:05d}.ts").write_text("x")

        scan_cap = 100
        scanned = core.scan_directory(root, True, max_files=scan_cap)
        filtered = core.filter_entries(scanned, exts={".pptx"})
        if filtered:
            _fail("scan+filter 不应在 cap 截断场景下找到 ppt")

        matches, meta = core.find_files(
            root,
            True,
            exts={".pptx"},
            max_results=10,
            max_visited=500_000,
        )
        if len(matches) != 1:
            _fail(f"find_files 应找到 1 个 ppt，实际 {len(matches)}")
        if matches[0].path.resolve() != target.resolve():
            _fail(f"find 路径不对: {matches[0].path} != {target}")
        if meta.truncated and meta.stopped_reason == "max_visited":
            _fail("不应因 max_visited 截断")
        _ok("scan cap 截断后 find_files 仍命中深层 ppt")


def test_find_tool_execute() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        f = root / "note.pptx"
        f.write_bytes(b"ppt")
        ctx = ToolContext(session=SessionState(), find_max_results=10, find_max_visited=1000)
        prep = prepare("find_files", {"root": str(root), "exts": ["pptx"]}, ctx)
        result = execute("find_files", {"root": str(root), "exts": ["pptx"]}, ctx, prep)
        if str(f.resolve()) not in result.summary:
            _fail(f"工具摘要应含路径: {result.summary!r}")
        if ctx.session.last_filter is None or len(ctx.session.last_filter) != 1:
            _fail("find 应写入 last_filter")
        _ok("find_files 工具 execute")


def test_list_user_folders_tool() -> None:
    ctx = ToolContext(session=SessionState())
    prep = prepare("list_user_folders", {}, ctx)
    result = execute("list_user_folders", {}, ctx, prep)
    if "Documents" not in result.summary and "文档" not in result.summary:
        _fail(f"应列出 Documents: {result.summary!r}")
    _ok("list_user_folders 工具")


def main() -> None:
    print("find_files check\n")
    test_find_after_scan_cap_misses()
    test_find_tool_execute()
    test_list_user_folders_tool()
    print("\nAll find_files checks passed.")


if __name__ == "__main__":
    main()
