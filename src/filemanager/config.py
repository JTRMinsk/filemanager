"""全局配置:可切换 LLM 后端 + 用户数据目录解析。

阶段 2 范围:
- ``make_llm_client``:按配置选择并实例化 LLM 适配器（"可切换后端"的唯一入口）。
- 存储路径:记忆/配置一律落用户数据目录，与 .exe 位置无关（方案 §3.3）。

约束:
- 不在代码里硬编码 API key;凭据从环境变量或 GUI profile 读取。
- Ollama 适配器尚未实现，选用时给出清晰报错，不静默失败。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from filemanager.llm.base import LLMClient
from filemanager.llm.openai_client import (
    DEEPSEEK_BASE_URL,
    DEEPSEEK_DEFAULT_MODEL,
    OPENAI_DEFAULT_MODEL,
    OpenAIClient,
)


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

    llm_backend: str = "anthropic"          # "anthropic" | "openai" | "deepseek" | "ollama"
    llm_model: str = ""                      # 空 = 用该后端的默认模型
    llm_base_url: str = ""                   # CLI 可选；空 = 用后端默认
    allowed_roots: list[Path] = field(default_factory=list)  # 阶段 3 写操作护栏用
    compact_threshold: int = 6000
    keep_recent_turns: int = 4

    @classmethod
    def from_env(cls) -> "AgentConfig":
        """从环境变量读取（便于命令行/CI）。GUI 后续可改为读 CONFIG_FILE。"""
        return cls(
            llm_backend=os.environ.get("FM_LLM_BACKEND", "anthropic"),
            llm_model=os.environ.get("FM_LLM_MODEL", ""),
            llm_base_url=os.environ.get("FM_LLM_BASE_URL", ""),
        )


# ===========================================================================
# OpenAI 兼容后端（OpenAI / DeepSeek 共用适配器）
# ===========================================================================
def _make_openai_compatible_client(
    *,
    model: str,
    api_key: str,
    base_url: str,
    backend: str,
) -> OpenAIClient:
    """按 backend 解析默认 base_url / model，构造 OpenAI 兼容 client。"""
    b = backend.lower()
    if b == "deepseek":
        resolved_base = base_url or DEEPSEEK_BASE_URL
        resolved_model = model or DEEPSEEK_DEFAULT_MODEL
    else:
        resolved_base = base_url or ""
        resolved_model = model or OPENAI_DEFAULT_MODEL
    return OpenAIClient(model=resolved_model, api_key=api_key, base_url=resolved_base)


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

    if backend in ("openai", "deepseek"):
        key = os.environ.get("OPENAI_API_KEY", "") if backend == "openai" else os.environ.get("DEEPSEEK_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
        return _make_openai_compatible_client(
            model=cfg.llm_model,
            api_key=key,
            base_url=cfg.llm_base_url,
            backend=backend,
        )

    if backend == "ollama":
        raise NotImplementedError(
            "Ollama 后端尚未实现。请参照 anthropic_client.py 新增 ollama_client.py（HTTP 调用）。"
        )

    raise ValueError(f"未知的 LLM 后端:{cfg.llm_backend}（支持:anthropic / openai / deepseek / ollama）")


def make_llm_client_from_profile(profile) -> LLMClient:
    """从一条 ApiProfile（GUI 选中的配置）构造 client，显式传入 key。

    与 ``make_llm_client`` 的区别:后者从环境变量读 key（命令行用），本函数从配置对象
    拿 key（GUI 用）。``profile`` 类型为 ``api_store.ApiProfile``（此处不 import 避免循环）。
    """
    backend = profile.backend.lower()
    if backend == "anthropic":
        from filemanager.llm.anthropic_client import AnthropicClient, DEFAULT_MODEL

        return AnthropicClient(model=profile.model or DEFAULT_MODEL, api_key=profile.key)
    if backend in ("openai", "deepseek"):
        return _make_openai_compatible_client(
            model=profile.model,
            api_key=profile.key,
            base_url=getattr(profile, "base_url", "") or "",
            backend=backend,
        )
    if backend == "ollama":
        raise NotImplementedError("Ollama 后端尚未实现（参照 anthropic_client.py）。")
    raise ValueError(f"未知的 LLM 后端:{profile.backend}")
