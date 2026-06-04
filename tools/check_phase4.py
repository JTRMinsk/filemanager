"""阶段 4 验收:长期记忆（MD）。全程 MockLLMClient + 临时目录，无需 API key。

验证:
  1. remember 受确认约束:拒绝→不写;同意→写入。
  2. 记忆跨会话保留（new_session 后仍在）。
  3. 记忆被注入系统提示（_build_system 含「[长期记忆]」）。
  4. recall 能检索到。
  5. 记忆不绕过删除确认（核心安全约束）。
  6. clear 清空。

运行: python tools/check_phase4.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# 记忆/日志库指向临时目录
_tmp = Path(tempfile.mkdtemp())
import filemanager.config as cfg
cfg.MEMORY_MD = _tmp / "memory.md"
cfg.MEMORY_DB = _tmp / "memory.db"
import filemanager.memory as memory
memory.MEMORY_MD = cfg.MEMORY_MD
import filemanager.oplog as oplog
oplog.MEMORY_DB = cfg.MEMORY_DB

from filemanager.agent import Agent
from filemanager.llm.mock_client import MockLLMClient, call, say


def banner(t): print("\n" + "=" * 66 + f"\n{t}\n" + "=" * 66)
def ok(m): print(f"  OK  {m}")


# ---------------------------------------------------------------------------
banner("测试 1:remember 受确认约束（拒绝→不写）")
mock = MockLLMClient([call("remember", text="下载的PDF归到D:/Docs", section="用户偏好"), say("好的。")])
agent = Agent(mock)
agent.run_turn("记住:我的PDF放D:/Docs", confirm_cb=lambda info: False)  # 拒绝
assert memory.load_markdown() == "", "拒绝确认后不应写入记忆"
ok("remember 触发确认；拒绝→记忆为空")


# ---------------------------------------------------------------------------
banner("测试 2:同意→写入；跨会话保留")
mock2 = MockLLMClient([call("remember", text="下载的PDF归到D:/Docs", section="用户偏好"), say("已记住。")])
agent2 = Agent(mock2)
seen = []
agent2.run_turn("记住:我的PDF放D:/Docs", emit_cb=seen.append, confirm_cb=lambda info: True)  # 同意
# 确认卡片应标明是写入记忆
creq = [e for e in seen if e["type"] == "confirm_request" and e["name"] == "remember"]
assert creq and "长期记忆" in creq[0]["description"], "remember 应有写记忆的确认卡片"
md = memory.load_markdown()
assert "D:/Docs" in md and "用户偏好" in md, f"记忆未写入:{md!r}"
ok("同意→已写入；确认卡片标明写记忆")

# 重开会话:短期清空，长期仍在
agent2.new_session()
assert agent2.session.last_scan is None and agent2.session.messages == []
assert "D:/Docs" in memory.load_markdown(), "重开会话后长期记忆应仍在"
ok("new_session 后短期清空、长期记忆保留")


# ---------------------------------------------------------------------------
banner("测试 3:记忆注入系统提示")
sysmsg = agent2._build_system()
assert "[长期记忆]" in sysmsg.content and "D:/Docs" in sysmsg.content, "系统提示应含记忆"
ok("_build_system 注入了 [长期记忆]，含已记内容")


# ---------------------------------------------------------------------------
banner("测试 4:recall 检索")
mock4 = MockLLMClient([call("recall", query="PDF"), say("查到了。")])
agent4 = Agent(mock4)
seen4 = []
agent4.run_turn("我之前说过PDF放哪？", emit_cb=seen4.append, confirm_cb=lambda info: True)
rres = [e for e in seen4 if e["type"] == "tool_result" and e["name"] == "recall"]
assert rres and "D:/Docs" in rres[0]["summary"], "recall 应检索到 D:/Docs"
ok("recall 命中已记条目")


# ---------------------------------------------------------------------------
banner("测试 5:记忆不绕过删除确认（核心安全约束）")
# 先在记忆里写一条"可以删 X"，再让 Agent 删 —— 删除仍须确认；拒绝则不删
memory.append("E:/old 目录可以随便删", "用户偏好")
work = Path(tempfile.mkdtemp())
victim = work / "keep.txt"
victim.write_text("important", encoding="utf-8")
mock5 = MockLLMClient([
    call("delete_files", paths=[str(victim)]),
    say("好的。"),
])
agent5 = Agent(mock5, allowed_roots=[work])
seen5 = []
# 即便记忆里有"可以删"，删除依然弹确认；这里拒绝
agent5.run_turn("按你记得的，把该删的删了", emit_cb=seen5.append, confirm_cb=lambda info: False)
del_confirm = [e for e in seen5 if e["type"] == "confirm_request" and e["name"] == "delete_files"]
assert del_confirm, "删除必须弹确认（记忆不能绕过）"
assert victim.exists(), "拒绝确认后文件必须仍在"
ok("记忆中即使写了‘可删’，删除仍弹确认；拒绝→文件保留")


# ---------------------------------------------------------------------------
banner("测试 6:clear 清空记忆")
memory.clear()
assert memory.load_markdown() == "", "clear 后记忆应为空"
ok("clear 清空成功")


banner("阶段 4 全部通过 ✅")
print("MD 长期记忆:写入受确认 / 跨会话保留 / 注入系统提示 / recall检索 / 不绕过删除确认 / 清空 —— 均验证通过。")
