"""可切换 LLM 抽象层 —— 统一的消息/工具/响应格式 + 客户端基类。

阶段 2 产物。Agent 只依赖本模块的 ``LLMClient`` 接口与统一数据格式,**不依赖任何具体厂商**。
每个适配器（``anthropic_client`` / ``openai_client`` / ``ollama_client`` / 测试用 ``mock_client``）
负责在"统一格式"与"厂商格式"之间双向转换。

切换后端:见 ``filemanager.config.make_llm_client``。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    """模型发起的一次工具调用。"""

    id: str
    name: str
    arguments: dict


@dataclass
class Message:
    """统一内部消息格式。各适配器负责与厂商格式互转。

    - role="system":   系统提示（含人格、工具说明、长期记忆注入）。
    - role="user":     用户输入。
    - role="assistant":模型回复;若 ``tool_calls`` 非空表示模型要调工具。
    - role="tool":     工具执行结果;``tool_call_id`` 指明对应哪次调用。
    """

    role: str
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str = ""
    tool_name: str = ""


@dataclass
class ToolSpec:
    """给模型看的工具说明（名称 + 描述 + JSON Schema 参数）。"""

    name: str
    description: str
    parameters: dict  # JSON Schema


@dataclass
class LLMResponse:
    """一次 ``chat`` 调用的归一化结果。"""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)  # 空 = 模型不想调工具，对话可结束
    usage_tokens: int = 0


class LLMClient(ABC):
    """所有后端适配器的统一接口。Agent 仅依赖此接口。"""

    @abstractmethod
    def chat(self, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        """单轮调用:统一格式 → 厂商格式 → 请求 → 厂商响应 → 统一格式。"""
        raise NotImplementedError

    @abstractmethod
    def count_tokens(self, messages: list[Message]) -> int:
        """估算消息列表的 token 数，供上下文压缩判断。

        可调用厂商精确接口，或用近似（字符数/4）。压缩只需量级正确，近似即可。
        """
        raise NotImplementedError


def approx_tokens(messages: list[Message]) -> int:
    """通用近似:按 字符数/4 估算（含工具调用参数）。适配器可直接复用。"""
    chars = 0
    for m in messages:
        chars += len(m.content or "")
        for tc in m.tool_calls:
            chars += len(tc.name) + len(str(tc.arguments))
    return chars // 4
