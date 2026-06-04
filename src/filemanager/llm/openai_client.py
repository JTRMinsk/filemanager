"""OpenAI 兼容适配器:统一格式 ↔ OpenAI Chat Completions API（tools / tool_calls）。

依赖（可选）: pip install openai
用于 OpenAI 官方 API 及 DeepSeek 等 OpenAI 兼容端点（通过 ``base_url`` 区分）。

DeepSeek 默认参数由 ``config`` 工厂层传入，本模块不硬编码后端选择逻辑。
"""

from __future__ import annotations

import json
import os

from filemanager.llm.base import LLMClient, LLMResponse, Message, ToolCall, ToolSpec, approx_tokens

OPENAI_DEFAULT_MODEL = "gpt-4o"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_DEFAULT_MODEL = "deepseek-v4-flash"


class OpenAIClient(LLMClient):
    def __init__(
        self,
        model: str = OPENAI_DEFAULT_MODEL,
        api_key: str = "",
        base_url: str = "",
        max_tokens: int = 2048,
    ) -> None:
        from openai import OpenAI  # 延迟导入,未选用此后端时不强制安装

        kwargs: dict = {"api_key": api_key or os.environ.get("OPENAI_API_KEY") or ""}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = OpenAI(**kwargs)
        self.model = model
        self.max_tokens = max_tokens

    def _to_openai(self, messages: list[Message]) -> list[dict]:
        """统一消息 → OpenAI messages 列表。"""
        out: list[dict] = []
        for m in messages:
            if m.role == "system":
                out.append({"role": "system", "content": m.content})
            elif m.role == "user":
                out.append({"role": "user", "content": m.content})
            elif m.role == "assistant":
                msg: dict = {"role": "assistant"}
                if m.content:
                    msg["content"] = m.content
                if m.tool_calls:
                    msg["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                            },
                        }
                        for tc in m.tool_calls
                    ]
                if "content" not in msg and "tool_calls" not in msg:
                    msg["content"] = ""
                out.append(msg)
            elif m.role == "tool":
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": m.tool_call_id,
                        "content": m.content,
                    }
                )
        return out

    def _tools_to_openai(self, tools: list[ToolSpec]) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ]

    def chat(self, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        kwargs: dict = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": self._to_openai(messages),
        }
        if tools:
            kwargs["tools"] = self._tools_to_openai(tools)

        resp = self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0].message
        text = choice.content or ""

        tool_calls: list[ToolCall] = []
        if choice.tool_calls:
            for tc in choice.tool_calls:
                raw_args = tc.function.arguments
                if isinstance(raw_args, str):
                    try:
                        args = json.loads(raw_args) if raw_args else {}
                    except json.JSONDecodeError:
                        args = {}
                else:
                    args = dict(raw_args) if raw_args else {}
                tool_calls.append(
                    ToolCall(id=tc.id, name=tc.function.name, arguments=args)
                )

        usage = getattr(resp, "usage", None)
        tokens = 0
        if usage:
            tokens = (usage.prompt_tokens or 0) + (usage.completion_tokens or 0)

        return LLMResponse(text=text, tool_calls=tool_calls, usage_tokens=tokens)

    def count_tokens(self, messages: list[Message]) -> int:
        return approx_tokens(messages)
