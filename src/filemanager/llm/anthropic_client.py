"""Anthropic 适配器:统一格式 ↔ Anthropic Messages API（tool_use / tool_result）。

依赖（可选）: pip install anthropic
凭据: 环境变量 ANTHROPIC_API_KEY（不要在代码里硬编码 key）。

要点:Anthropic 的工具结果必须放在紧跟 assistant(tool_use) 之后的 user 消息里;
同一回合的多个工具结果合并进**一条** user 消息的多个 tool_result block。本适配器在
``_to_anthropic`` 中处理这种合并。
"""

from __future__ import annotations

import json
import os

from filemanager.llm.base import LLMClient, LLMResponse, Message, ToolCall, ToolSpec, approx_tokens

DEFAULT_MODEL = "claude-sonnet-4-20250514"


class AnthropicClient(LLMClient):
    def __init__(self, model: str = DEFAULT_MODEL, max_tokens: int = 2048, api_key: str = "") -> None:
        import anthropic  # 延迟导入,未选用此后端时不强制安装

        self._anthropic = anthropic
        # 显式传入的 key（GUI 选中的配置）优先;否则回退环境变量（命令行用）
        self._client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self.model = model
        self.max_tokens = max_tokens

    def _to_anthropic(self, messages: list[Message]):
        """统一消息 → (system_str, anthropic_messages)。合并连续 tool 结果到一条 user 消息。"""
        system = ""
        out: list[dict] = []
        for m in messages:
            if m.role == "system":
                system = (system + "\n" + m.content) if system else m.content
            elif m.role == "user":
                out.append({"role": "user", "content": m.content})
            elif m.role == "assistant":
                blocks = []
                if m.content:
                    blocks.append({"type": "text", "text": m.content})
                for tc in m.tool_calls:
                    blocks.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments})
                out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
            elif m.role == "tool":
                block = {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content}
                # 若上一条已是带 tool_result 的 user 消息，则并入它（同回合多工具）
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
        return system, out

    def chat(self, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        system, amsgs = self._to_anthropic(messages)
        kwargs = {"model": self.model, "max_tokens": self.max_tokens, "messages": amsgs}
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools
            ]
        resp = self._client.messages.create(**kwargs)

        text_parts, tool_calls = [], []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input)))
        usage = getattr(resp, "usage", None)
        tokens = (usage.input_tokens + usage.output_tokens) if usage else 0
        return LLMResponse(text="".join(text_parts), tool_calls=tool_calls, usage_tokens=tokens)

    def count_tokens(self, messages: list[Message]) -> int:
        # 近似即可（压缩判断用）。如需精确，可改调 self._client.messages.count_tokens(...)
        return approx_tokens(messages)
