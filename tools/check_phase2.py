"""阶段 2 离线验收:MockLLMClient 驱动 Agent，无需 API key / 真实模型。

运行:
  python tools/check_phase2.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from filemanager.agent import Agent
from filemanager.llm.mock_client import MockLLMClient, call, say


def _fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def _ok(msg: str) -> None:
    print(f"  OK  {msg}")


def test_scan_filter_profile() -> None:
    """scan → filter → profile → 收尾回复。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "a.txt").write_text("hello")
        (root / "b.pdf").write_bytes(b"%PDF-1.4")
        (root / "big.bin").write_bytes(b"\x00" * 2000)

        llm = MockLLMClient(
            [
                call("scan_directory", root=str(root), recursive=False),
                call("filter_files", call_id="c2", exts=[".txt"]),
                call("profile_directory", call_id="c3"),
                say("完成：已扫描并筛选出 txt 文件，画像已生成。"),
            ]
        )
        agent = Agent(llm)
        reply = agent.run_turn("帮我看看这个目录里的 txt")

        if agent.session.last_scan is None or len(agent.session.last_scan) != 3:
            _fail(f"last_scan 应为 3 个文件，实际 {len(agent.session.last_scan or [])}")
        if agent.session.last_filter is None or len(agent.session.last_filter) != 1:
            _fail(f"last_filter 应为 1 个文件，实际 {len(agent.session.last_filter or [])}")
        if "完成" not in reply:
            _fail(f"最终回复异常: {reply!r}")
        if len(llm.calls) != 4:
            _fail(f"期望 4 次 chat 调用，实际 {len(llm.calls)}")
        _ok("scan → filter → profile 链路")


def test_preview_and_new_session() -> None:
    """preview_file + new_session 清空状态。"""
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "note.txt"
        f.write_text("preview me")

        llm = MockLLMClient([call("preview_file", path=str(f)), say("已预览。")])
        agent = Agent(llm)
        agent.run_turn("预览这个文件")

        if agent.session.last_scan is not None:
            _fail("preview 不应写入 last_scan")

        agent.new_session()
        if agent.session.messages or agent.session.last_scan or agent.session.last_filter:
            _fail("new_session 未清空短期状态")
        _ok("preview + new_session")


def test_context_compact() -> None:
    """token 超阈值触发压缩（摘要调用消耗 mock 脚本最后一项）。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "x.txt").write_text("x")

        llm = MockLLMClient(
            [
                call("scan_directory", root=str(root)),
                say("第一轮完成。"),
                say("这是压缩后的摘要。"),  # maybe_compact → _summarize 消耗
                call("scan_directory", call_id="c2", root=str(root)),
                say("第二轮完成。"),
            ]
        )
        llm.token_override = 99999  # 强制触发压缩
        agent = Agent(llm, compact_threshold=100, keep_recent_turns=2)

        agent.run_turn("第一轮")
        n_after_first = len(agent.session.messages)
        agent.run_turn("第二轮")
        n_after_second = len(agent.session.messages)

        if n_after_first < 3:
            _fail("第一轮消息后数量过少")
        if n_after_second >= n_after_first + 4:
            _fail("压缩后消息应比无压缩时更短")
        if not any("[早期对话摘要]" in (m.content or "") for m in agent.session.messages):
            _fail("未找到压缩摘要消息")
        _ok("上下文压缩")


def main() -> None:
    print("Phase 2 check (MockLLMClient, no API key)\n")
    test_scan_filter_profile()
    test_preview_and_new_session()
    test_context_compact()
    print("\nAll checks passed.")


if __name__ == "__main__":
    main()
