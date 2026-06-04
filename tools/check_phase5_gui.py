"""阶段 5 GUI 组件离屏验证（无需真实显示/网络/API key）。

用 Qt offscreen 平台构造 ChatPanel / SettingsDialog，验证:
  - 导入与构造无误、信号槽接线正常
  - api_store 多配置增删改 + 选中 往返正确
  - ChatPanel 的事件渲染逻辑（直接喂假事件，不起线程不联网）

运行: QT_QPA_PLATFORM=offscreen python tools/check_phase5_gui.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# 把配置文件指向临时目录，避免污染真实 %APPDATA%
_tmp = tempfile.mkdtemp()
import filemanager.config as cfg
cfg.CONFIG_FILE = Path(_tmp) / "config.json"
import filemanager.api_store as api_store
api_store.CONFIG_FILE = cfg.CONFIG_FILE  # api_store 从 config 导入了引用，这里同步

from PySide6.QtWidgets import QApplication
from filemanager.api_store import ApiProfile


def ok(msg):
    print(f"  OK  {msg}")


app = QApplication.instance() or QApplication(sys.argv)

print("== api_store 多配置往返 ==")
api_store.upsert_profile(ApiProfile(name="我的Claude", backend="anthropic", model="", key="sk-test-1"))
api_store.upsert_profile(ApiProfile(name="公司OpenAI", backend="openai", model="gpt-4o", key="sk-test-2", base_url=""))
api_store.upsert_profile(ApiProfile(
    name="DeepSeek测试",
    backend="deepseek",
    model="deepseek-v4-flash",
    key="sk-ds",
    base_url="https://api.deepseek.com",
))
profs = api_store.list_profiles()
assert len(profs) == 3, profs
assert api_store.get_active().name == "我的Claude", "第一条应自动成为 active"
api_store.set_active("公司OpenAI")
assert api_store.get_active().name == "公司OpenAI"
api_store.upsert_profile(ApiProfile(name="我的Claude", backend="anthropic", model="claude-x", key="sk-test-1b"))
assert any(p.model == "claude-x" for p in api_store.list_profiles()), "更新未生效"
api_store.delete_profile("公司OpenAI")
assert len(api_store.list_profiles()) == 2
assert api_store.get_active().name == "我的Claude", "删除 active 后应回退"
ds = next(p for p in api_store.list_profiles() if p.name == "DeepSeek测试")
assert ds.base_url == "https://api.deepseek.com" and ds.backend == "deepseek"
ok("增 / 改 / 选 / 删 + active 回退 + DeepSeek base_url 全部正确")

print("== make_llm_client_from_profile (DeepSeek) ==")
from filemanager.config import make_llm_client_from_profile
from filemanager.llm.openai_client import OpenAIClient

try:
    client = make_llm_client_from_profile(ds)
    assert isinstance(client, OpenAIClient)
    assert client.model == "deepseek-v4-flash"
    ok("DeepSeek 工厂返回 OpenAIClient")
except ModuleNotFoundError:
    ok("openai 未安装，跳过 client 实例化（pip install openai 可测）")

print("== SettingsDialog 构造 ==")
from filemanager.settings_dialog import SettingsDialog
dlg = SettingsDialog()
assert dlg._list.count() == 2, "列表应显示 2 条配置（Claude + DeepSeek）"
dlg._on_new()
ok("设置窗口构造、列表加载、新增表单清空 正常")

print("== ChatPanel 构造与状态 ==")
from filemanager.chat_panel import ChatPanel
panel = ChatPanel()
# 当前有一个带 key 的 anthropic 配置，状态栏应显示其名
assert "我的Claude" in panel._status.text(), panel._status.text()
ok(f"状态栏显示当前配置:{panel._status.text()!r}")

print("== ChatPanel 事件渲染（喂假事件，不联网）==")
panel._append_user("扫描我的下载目录")
panel._on_step({"type": "tool_call", "name": "scan_directory", "args": {"root": "/x", "recursive": True}})
panel._on_step({"type": "tool_result", "name": "scan_directory", "summary": "共 42 个文件，总大小约 1.2 GB。\n更多…"})
panel._on_finished("已扫描，共 42 个文件。")
doc = panel._stream.toPlainText()
assert "扫描我的下载目录" in doc
assert "scan_directory" in doc
assert "42 个文件" in doc
ok("用户气泡 / 工具调用 / 工具结果 / 助手回复 均渲染入消息流")

print("== 无配置时的提示 ==")
api_store.delete_profile("我的Claude")
api_store.delete_profile("DeepSeek测试")
panel._reset_agent()
panel._refresh_status()
assert "未配置" in panel._status.text()
assert panel._ensure_agent() is None, "无配置应返回 None，不应崩"
ok("无配置时状态提示正确、_ensure_agent 安全返回 None")

print("\nAll phase-5 GUI checks passed (offscreen).")
print("注:真实对话需在有显示环境的机器上、配好 key 后运行 GUI 验证。")
