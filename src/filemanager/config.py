"""全局配置:可切换 LLM 后端 + 用户数据目录解析。

阶段 2 范围:
- ``make_llm_client``:按配置选择并实例化 LLM 适配器（"可切换后端"的唯一入口）。
- 存储路径:记忆/配置一律落用户数据目录，与 .exe 位置无关（方案 §3.3）。

约束:
- 不在代码里硬编码 API key;凭据从环境变量读取（见各适配器）。
- OpenAI / Ollama 适配器尚未实现，选用时给出清晰报错，不静默失败。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from filemanager.llm.base import LLMClient


# ===========================================================================
# 用户数据目录（方案 §3.3）—— 所有记忆/配置/日志的根，绝不解析到 .exe 所在目录
# ===========================================================================
def user_data_dir() -> Path:
    """跨平台用户数据目录。Windows→%APPDATA%，macOS→Application Support，其它→XDG_CONFIG_HOME。"""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    d = base / "filemanager"
    d.mkdir(parents=True, exist_ok=True)
    return d


# 全部派生自 user_data_dir()，不接受覆盖到 .exe 旁边
MEMORY_MD = user_data_dir() / "memory.md"
MEMORY_DB = user_data_dir() / "memory.db"
CONFIG_FILE = user_data_dir() / "config.json"


# ===========================================================================
# 模型配置
# ===========================================================================
@dataclass
class AgentConfig:
    """运行配置。阶段 2 只关注 LLM 后端;路径/阈值留默认即可。"""

    llm_backend: str = "anthropic"          # "anthropic" | "openai" | "ollama"
    llm_model: str = ""                      # 空 = 用该后端的默认模型
    allowed_roots: list[Path] = field(default_factory=list)  # 阶段 3 写操作护栏用
    compact_threshold: int = 6000
    keep_recent_turns: int = 4

    @classmethod
    def from_env(cls) -> "AgentConfig":
        """从环境变量读取（便于命令行/CI）。GUI 后续可改为读 CONFIG_FILE。"""
        return cls(
            llm_backend=os.environ.get("FM_LLM_BACKEND", "anthropic"),
            llm_model=os.environ.get("FM_LLM_MODEL", ""),
        )


# ===========================================================================
# 可切换后端工厂（唯一入口）
# ===========================================================================
def make_llm_client(config: AgentConfig | None = None) -> LLMClient:
    """按配置实例化 LLM 适配器。Agent 只通过本函数拿 client，不直接 import 具体适配器。"""
    cfg = config or AgentConfig.from_env()
    backend = cfg.llm_backend.lower()

    if backend == "anthropic":
        from filemanager.llm.anthropic_client import AnthropicClient, DEFAULT_MODEL

        return AnthropicClient(model=cfg.llm_model or DEFAULT_MODEL)

    if backend == "openai":
        # 尚未实现:参照 anthropic_client.py 实现 openai_client.AOpenAIClient（tools/tool_calls 格式）
        raise NotImplementedError(
            "OpenAI 后端尚未实现。请参照 src/filemanager/llm/anthropic_client.py 新增 "
            "openai_client.py，并在此处接入。"
        )

    if backend == "ollama":
        # 尚未实现:本地模型走 HTTP（/api/chat）；工具调用能力依模型而定，需在 prompt 兜底
        raise NotImplementedError(
            "Ollama 后端尚未实现。请参照 anthropic_client.py 新增 ollama_client.py（HTTP 调用）。"
        )

    raise ValueError(f"未知的 LLM 后端:{cfg.llm_backend}（支持:anthropic / openai / ollama）")
