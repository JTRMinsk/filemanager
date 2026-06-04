"""命令行聊天入口:用大白话驱动文件操作。阶段 2 的验收出口。

前置:
  pip install anthropic
  export ANTHROPIC_API_KEY=sk-...      # 不要写进代码

运行:
  python tools/cli_chat.py
  python tools/cli_chat.py --backend anthropic --model claude-sonnet-4-20250514

会话内命令:
  /new    重开会话（清空短期记忆）
  /quit   退出

说明:本入口真实调用所选模型，会产生 API 费用。若只想验证循环逻辑而不联网，
请改跑 tools/check_phase2.py（用 MockLLMClient，无需 key）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from filemanager.agent import Agent
from filemanager.config import AgentConfig, make_llm_client


def on_event(e: dict) -> None:
    """把 Agent 的中间过程打印到终端（GUI 阶段会换成消息流渲染）。"""
    t = e["type"]
    if t == "tool_call":
        print(f"  \033[36m→ 调用工具 {e['name']}({_fmt_args(e['args'])})\033[0m")
    elif t == "tool_result":
        first = e["summary"].splitlines()[0] if e["summary"] else ""
        print(f"  \033[90m  ↳ {first}\033[0m")
    elif t == "assistant_text" and e["text"]:
        pass  # 最终文本统一在 run_turn 返回后打印，避免重复
    # final 不单独打印


def _fmt_args(args: dict) -> str:
    return ", ".join(f"{k}={v!r}" for k, v in args.items())


def main() -> None:
    ap = argparse.ArgumentParser(description="FileManager Agent 命令行")
    ap.add_argument("--backend", default=None, help="anthropic / openai / ollama")
    ap.add_argument("--model", default=None, help="具体模型名，留空用后端默认")
    args = ap.parse_args()

    cfg = AgentConfig.from_env()
    if args.backend:
        cfg.llm_backend = args.backend
    if args.model:
        cfg.llm_model = args.model

    try:
        llm = make_llm_client(cfg)
    except Exception as e:  # noqa: BLE001
        print(f"\033[31m无法初始化模型后端:{e}\033[0m")
        print("提示:确认已 pip install 对应 SDK，并设置了 API key 环境变量。")
        sys.exit(1)

    agent = Agent(
        llm,
        allowed_roots=cfg.allowed_roots,
        compact_threshold=cfg.compact_threshold,
        keep_recent_turns=cfg.keep_recent_turns,
    )

    print(f"\033[1mFileManager Agent\033[0m（后端={cfg.llm_backend}）  输入 /new 重开、/quit 退出")
    print("试试:扫描 ~/Downloads，挑出大于 10MB 的 PDF\n")

    while True:
        try:
            user = input("\033[1m你 ›\033[0m ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            break
        if not user:
            continue
        if user == "/quit":
            print("再见。")
            break
        if user == "/new":
            agent.new_session()
            print("\033[33m（已重开会话）\033[0m\n")
            continue

        try:
            reply = agent.run_turn(user, emit_cb=on_event)
        except Exception as e:  # noqa: BLE001
            print(f"\033[31m出错:{e}\033[0m\n")
            continue
        print(f"\033[1m助手 ›\033[0m {reply}\n")


if __name__ == "__main__":
    main()
