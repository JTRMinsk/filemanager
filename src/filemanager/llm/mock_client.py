"""测试用假模型:脚本化地按预设顺序返回响应,实现与真适配器相同的 ``LLMClient`` 接口。

用途:
- 在**没有 API key** 的情况下,完整测试 Agent 循环、工具分发、会话状态、上下文压缩。
- 同时证明"可切换":Mock 与真适配器满足同一接口,Agent 对二者无差别。

用法:把要让"模型"依次返回的 ``LLMResponse`` 放进 ``script`` 队列;每次 ``chat`` 弹出一个。
``count_tokens`` 用通用近似,可被 ``token_override`` 强制返回固定值（测试压缩用）。
"""

from __future__ import annotations

from collections import deque

from filemanager.llm.base import LLMClient, LLMResponse, Message, ToolCall, ToolSpec, approx_tokens


class MockLLMClient(LLMClient):
    def __init__(self, script: list[LLMResponse] | None = None) -> None:
        self._script: deque[LLMResponse] = deque(script or [])
        self.calls: list[tuple[list[Message], list[ToolSpec]]] = []  # 记录每次调用,供断言
        self.token_override: int | None = None

    def push(self, resp: LLMResponse) -> None:
        """追加一个脚本响应。"""
        self._script.append(resp)

    def chat(self, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        # 深记录调用时的消息快照（浅记录引用即可，测试只读）
        self.calls.append((list(messages), tools))
        if not self._script:
            # 脚本耗尽:默认返回一句收尾文本，避免测试死循环
            return LLMResponse(text="(mock: 脚本已耗尽，结束本轮)")
        return self._script.popleft()

    def count_tokens(self, messages: list[Message]) -> int:
        if self.token_override is not None:
            return self.token_override
        return approx_tokens(messages)


# ---- 便捷构造器:让测试脚本更易读 ----
def say(text: str) -> LLMResponse:
    """模型只说话、不调工具（对话结束）。"""
    return LLMResponse(text=text)


def call(tool_name: str, call_id: str = "c1", **arguments) -> LLMResponse:
    """模型发起一次工具调用。"""
    return LLMResponse(tool_calls=[ToolCall(id=call_id, name=tool_name, arguments=dict(arguments))])
