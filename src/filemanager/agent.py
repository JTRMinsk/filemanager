"""Agent 主循环（无 Qt 依赖）:LLM ↔ 工具调用 ↔ 会话状态，含上下文压缩与重开会话。

阶段 2 产物。可脱离 GUI 用脚本/命令行驱动（见 tools/cli_chat.py）。
GUI 在阶段 5 通过 ``agent_thread.py`` 在 QThread 里调用本类，避免阻塞界面。

设计要点:
- 完整扫描结果存 ``SessionState``（不进 LLM 上下文，方案 §4.2/§5.3）。
- 上下文过长 → ``maybe_compact`` 把早期对话压成摘要（方案 §5.4）。
- 重开会话 → ``new_session`` 丢弃短期状态；长期记忆（阶段 4 接入）不受影响。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from filemanager import tools
from filemanager.llm.base import LLMClient, Message, ToolCall
from filemanager.tools import ToolContext

# 防止模型在一轮里无限调工具
MAX_TOOL_ITERATIONS = 12

SYSTEM_PROMPT = """你是一个本地文件管理助手，通过调用工具帮助用户扫描、筛选、预览和了解文件。

工作方式:
- 用户描述目标，你决定调用哪些工具达成。
- 文件列表可能很大；工具只会返回摘要。要对具体文件做进一步操作时，用 filter_files 按条件缩小，而不是要求逐个列出。
- 路径要尽量用绝对路径。不确定用户指哪个目录时，先问清楚再扫描。
- 回答简洁，用中文。
"""


@dataclass
class SessionState:
    """单次会话的短期记忆（存内存，不落盘）。"""

    messages: list[Message] = field(default_factory=list)  # 对话历史（可能已压缩）
    last_scan: list | None = None          # 最近一次扫描的完整结果（供工具引用，不进 LLM）
    last_scan_root: Path | None = None
    last_filter: list | None = None        # 最近一次筛选的完整结果
    created_at: float = field(default_factory=time.time)


# emit 事件:供 GUI/CLI 展示中间过程。字段 type ∈ {assistant_text, tool_call, tool_result, final}
EmitCb = Callable[[dict], None]
# 破坏性操作确认:返回 True 才执行（阶段 2 只读工具不触发）
ConfirmCb = Callable[[dict], bool]


class Agent:
    def __init__(
        self,
        llm: LLMClient,
        *,
        allowed_roots: list[Path] | None = None,
        compact_threshold: int = 6000,   # 估算 token 超过此值触发压缩
        keep_recent_turns: int = 4,      # 压缩时保留最近多少条原始消息
        system_prompt: str = SYSTEM_PROMPT,
    ) -> None:
        self.llm = llm
        self.allowed_roots = allowed_roots or []
        self.compact_threshold = compact_threshold
        self.keep_recent_turns = keep_recent_turns
        self.system_prompt = system_prompt
        self.session = SessionState()

    # ---- 会话管理 ----
    def new_session(self) -> None:
        """重开会话:丢弃短期状态（含缓存的扫描结果）。长期记忆（阶段 4）不在此处理。"""
        self.session = SessionState()

    def _build_system(self) -> Message:
        """组装 system 消息。阶段 4 会在此追加 MD 长期记忆。"""
        return Message(role="system", content=self.system_prompt)

    # ---- 主循环 ----
    def run_turn(
        self,
        user_text: str,
        attached_paths: list[Path] | None = None,
        emit_cb: EmitCb | None = None,
        confirm_cb: ConfirmCb | None = None,
    ) -> str:
        """处理一轮用户输入，返回模型最终文本回复。

        attached_paths: 用户拖进来的文件;以结构化附件形式进入 user 消息（不自动读内容，
                        模型可按需调 preview_file）。
        emit_cb:        把中间步骤推给前端展示。
        confirm_cb:     破坏性工具的确认回调（阶段 3 用）。
        """
        emit = emit_cb or (lambda _e: None)

        # 1. 组装本轮 user 消息（含附件清单）
        content = user_text
        if attached_paths:
            lines = ["", "[用户附加的文件]"]
            for p in attached_paths:
                try:
                    sz = p.stat().st_size
                    lines.append(f"  {p}（{sz} 字节）")
                except OSError:
                    lines.append(f"  {p}（无法读取大小）")
            content += "\n".join(lines)
        self.session.messages.append(Message(role="user", content=content))

        # 2. 循环:chat → 执行工具 → 回填 → 直到模型不再调工具
        ctx = ToolContext(session=self.session, allowed_roots=self.allowed_roots)
        final_text = ""
        for _ in range(MAX_TOOL_ITERATIONS):
            convo = [self._build_system(), *self.session.messages]
            resp = self.llm.chat(convo, tools.TOOL_SPECS)

            # 记录 assistant 回合（含可能的文本与工具调用）
            self.session.messages.append(
                Message(role="assistant", content=resp.text, tool_calls=resp.tool_calls)
            )
            if resp.text:
                emit({"type": "assistant_text", "text": resp.text})

            if not resp.tool_calls:
                final_text = resp.text
                break

            # 逐个执行工具，结果作为 role=tool 消息回填
            for tc in resp.tool_calls:
                emit({"type": "tool_call", "name": tc.name, "args": tc.arguments})
                result = self._run_tool(tc, ctx, confirm_cb, emit)
                self.session.messages.append(
                    Message(
                        role="tool",
                        tool_call_id=tc.id,
                        tool_name=tc.name,
                        content=result.summary,
                    )
                )
                emit({"type": "tool_result", "name": tc.name, "summary": result.summary})
        else:
            # 达到迭代上限仍未收尾
            final_text = "（已达工具调用次数上限，本轮中止。）"

        emit({"type": "final", "text": final_text})

        # 3. 轮末检查上下文长度，必要时压缩
        self.maybe_compact()
        return final_text

    def _run_tool(self, tc: ToolCall, ctx: ToolContext, confirm_cb, emit) -> tools.ToolResult:
        """执行单个工具;破坏性工具先经确认（阶段 3 起生效）。"""
        result = tools.dispatch(tc.name, tc.arguments, ctx)
        if result.needs_confirmation:
            approved = bool(confirm_cb and confirm_cb({"name": tc.name, "args": tc.arguments, "preview": result.summary}))
            if not approved:
                return tools.ToolResult(summary="用户取消了该操作。")
            # 阶段 3:此处调用真正执行的二段函数。阶段 2 不会走到这里。
            return result
        return result

    # ---- 上下文压缩（方案 §5.4）----
    def maybe_compact(self) -> None:
        """估算 token 超阈值时:保留 system 之外的最近 N 条原文，更早的压成一段摘要。"""
        msgs = self.session.messages
        if self.llm.count_tokens([self._build_system(), *msgs]) < self.compact_threshold:
            return
        if len(msgs) <= self.keep_recent_turns:
            return  # 太短，压了也没意义

        old, recent = msgs[: -self.keep_recent_turns], msgs[-self.keep_recent_turns :]
        summary = self._summarize(old)
        # 用一条摘要消息替换早期历史；缓存的扫描结果（在 SessionState）本就不在 messages 里
        self.session.messages = [
            Message(role="user", content=f"[早期对话摘要]\n{summary}"),
            *recent,
        ]

    def _summarize(self, old_messages: list[Message]) -> str:
        """请模型把早期对话压成简短摘要。失败则退回机械截断。"""
        transcript_lines = []
        for m in old_messages:
            if m.role == "tool":
                transcript_lines.append(f"[工具结果:{m.tool_name}] {m.content[:300]}")
            elif m.content:
                transcript_lines.append(f"{m.role}: {m.content[:500]}")
        transcript = "\n".join(transcript_lines)
        prompt = (
            "把下面这段助手与用户的对话压缩成要点摘要，"
            "保留:用户目标、已确认的关键事实、涉及的目录/文件、尚未完成的任务。简洁，中文。\n\n"
            f"{transcript}"
        )
        try:
            resp = self.llm.chat(
                [
                    Message(role="system", content="你是对话摘要器，只输出摘要本身。"),
                    Message(role="user", content=prompt),
                ],
                tools=[],
            )
            return resp.text or transcript[:1000]
        except Exception:  # noqa: BLE001
            return transcript[:1000]
