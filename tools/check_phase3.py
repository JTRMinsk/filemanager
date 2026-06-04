"""阶段 3 验收:确认机制 + 扫描封顶 + 路径护栏 + 写操作两段式 + 操作日志。
全程 MockLLMClient + 临时目录，无需 API key、不碰真实文件系统之外的路径。

运行: python tools/check_phase3.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# 把记忆/日志库指向临时目录
_tmp = Path(tempfile.mkdtemp())
import filemanager.config as cfg
cfg.MEMORY_DB = _tmp / "memory.db"
import filemanager.oplog as oplog
oplog.MEMORY_DB = cfg.MEMORY_DB

from filemanager import guard
from filemanager.agent import Agent
from filemanager.llm.mock_client import MockLLMClient, call, say


def banner(t): print("\n" + "=" * 68 + f"\n{t}\n" + "=" * 68)
def ok(m): print(f"  OK  {m}")


# 准备一个可操作的工作目录（在用户主目录下，默认白名单允许）
work = Path(tempfile.mkdtemp(dir=Path.home())) if False else Path(tempfile.mkdtemp())
# 注意:tempfile 默认在 /tmp，不在用户主目录;为测试护栏放行，显式把 work 设为 allowed_roots
for i in range(5):
    (work / f"f{i}.txt").write_text(f"hello {i}\n" * 10, encoding="utf-8")
(work / "big.bin").write_bytes(b"\x00" * 2048)
dest = work / "backup"

ALLOWED = [work]


# ---------------------------------------------------------------------------
banner("测试 1:只读操作也要确认（confirm_mode=all），拒绝则不执行")
mock = MockLLMClient([call("scan_directory", root=str(work), recursive=False), say("好的。")])
agent = Agent(mock, allowed_roots=ALLOWED)
events = []
# confirm_cb 返回 False = 用户拒绝
agent.run_turn("扫描这个目录", emit_cb=events.append, confirm_cb=lambda info: False)
confirm_reqs = [e for e in events if e["type"] == "confirm_request"]
tool_results = [e for e in events if e["type"] == "tool_result"]
assert confirm_reqs, "只读 scan 也应触发确认请求"
assert "取消" in tool_results[0]["summary"], "拒绝后不应执行，应回‘取消’"
assert agent.session.last_scan is None, "拒绝后不应有扫描结果"
ok("只读操作触发确认；拒绝→不执行")


# ---------------------------------------------------------------------------
banner("测试 2:确认通过则执行（scan→delete 两段式）")
mock2 = MockLLMClient([
    call("scan_directory", root=str(work), recursive=False),
    call("filter_files", call_id="c2", name_contains="f1"),
    call("delete_files", call_id="c3"),       # 删除当前筛选结果（f1.txt）
    say("已删除。"),
])
agent2 = Agent(mock2, allowed_roots=ALLOWED)
seen = []
agent2.run_turn("删掉名字含 f1 的文件", emit_cb=seen.append, confirm_cb=lambda info: True)
# 删除确认卡片里应区分回收站/永久，并列出文件
del_confirm = [e for e in seen if e["type"] == "confirm_request" and e["name"] == "delete_files"]
assert del_confirm, "delete 应有确认请求"
assert "删除" in del_confirm[0]["description"]
assert not (work / "f1.txt").exists(), "确认后 f1.txt 应被删除"
assert (work / "f0.txt").exists(), "未被选中的 f0.txt 应保留"
ok("确认通过→删除执行；只删中选文件，其余保留")


# ---------------------------------------------------------------------------
banner("测试 3:路径护栏 —— 系统目录硬禁")
import sys as _sys
sysdir = Path(r"C:\Windows\System32") if _sys.platform == "win32" else Path("/usr/bin")
r = guard.check_write_allowed(sysdir / "x.dll", ALLOWED)
assert not r.allowed, "系统目录必须被拒绝"
ok(f"系统目录写操作被拒:{r.reason[:40]}")

# 主目录外、且不在白名单 → 拒绝
outside = Path(tempfile.mkdtemp()) / "z.txt"
r2 = guard.check_write_allowed(outside, ALLOWED)
assert not r2.allowed, "白名单外应被拒绝"
ok("白名单外路径被拒")

# 白名单内 → 允许
r3 = guard.check_write_allowed(work / "ok.txt", ALLOWED)
assert r3.allowed, "白名单内应允许"
ok("白名单内路径允许")


# ---------------------------------------------------------------------------
banner("测试 4:删除越界目标被护栏拦截（混合场景）")
# 让 delete 显式带一个越界路径 + 一个合法路径
mock4 = MockLLMClient([
    call("scan_directory", root=str(work), recursive=False),
    call("delete_files", call_id="c2", paths=[str(work / "f2.txt"), "/usr/bin/should_block"]),
    say("处理完毕。"),
])
agent4 = Agent(mock4, allowed_roots=ALLOWED)
seen4 = []
agent4.run_turn("删除这些", emit_cb=seen4.append, confirm_cb=lambda info: True)
dres = [e for e in seen4 if e["type"] == "tool_result" and e["name"] == "delete_files"][0]
assert "跳过" in dres["summary"], "越界目标应被跳过并说明"
assert not (work / "f2.txt").exists(), "合法目标 f2.txt 应被删"
ok("混合删除:合法的删、越界的跳过")


# ---------------------------------------------------------------------------
banner("测试 5:复制需目标目录 + 护栏 + 执行")
mock5 = MockLLMClient([
    call("scan_directory", root=str(work), recursive=False),
    call("filter_files", call_id="c2", name_contains="f3"),
    call("copy_files", call_id="c3", dest=str(dest)),
    say("已复制。"),
])
agent5 = Agent(mock5, allowed_roots=ALLOWED)
agent5.run_turn("把 f3 复制到 backup", confirm_cb=lambda info: True)
assert (dest / "f3.txt").exists(), "f3.txt 应被复制到 backup"
ok("复制执行成功，目标文件就位")


# ---------------------------------------------------------------------------
banner("测试 6:扫描封顶")
# 造一个超过上限的小目录，用很低的 scan_cap 触发
many = Path(tempfile.mkdtemp())
for i in range(20):
    (many / f"x{i}.txt").write_text("x", encoding="utf-8")
mock6 = MockLLMClient([call("scan_directory", root=str(many), recursive=False), say("ok")])
agent6 = Agent(mock6, allowed_roots=[many], scan_cap=10)  # 上限 10，目录有 20
seen6 = []
agent6.run_turn("扫描", emit_cb=seen6.append, confirm_cb=lambda info: True)
sres = [e for e in seen6 if e["type"] == "tool_result" and e["name"] == "scan_directory"][0]
assert "上限" in sres["summary"], "超限应给出提示"
assert len(agent6.session.last_scan) == 10, "应止于上限 10 个"
ok("扫描达上限即止并提示")


# ---------------------------------------------------------------------------
banner("测试 7:操作日志落库")
ops = oplog.recent_operations(50)
kinds = {o["kind"] for o in ops}
assert "trash" in kinds or "delete_permanent" in kinds, "删除应被记录"
assert "copy" in kinds, "复制应被记录"
ok(f"操作日志记录到 {len(ops)} 条（含 {kinds}）")


banner("阶段 3 全部通过 ✅")
print("确认机制 / 扫描封顶 / 路径护栏 / 写操作两段式 / 操作日志 —— 均在无 API key 下验证通过。")
