"""多 API 配置的持久化存储（隔离模块）。

阶段 5 范围:管理多个 LLM 配置（别名 + 后端 + 模型 + key），存到用户数据目录的 JSON。
当前为**明文存储**（原型阶段，简单优先）。

⚠️ 安全隔离设计:全部读写集中在本模块。将来要换成系统密钥库（keyring），
只需改 ``_read_keys`` / ``_write_key`` / ``_delete_key`` 三处，其余代码与界面不用动。

数据布局（config.json）:
{
  "active": "我的Claude",                      # 当前选中的配置别名
  "profiles": [
     {"name": "我的Claude", "backend": "anthropic", "model": "", "key": "sk-...", "base_url": ""},
     {"name": "DeepSeek",     "backend": "deepseek", "model": "deepseek-v4-flash", "key": "sk-...", "base_url": "https://api.deepseek.com"}
  ]
}
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from filemanager.config import CONFIG_FILE


@dataclass
class ApiProfile:
    """单条 API 配置。``key`` 当前明文保存;换 keyring 后此字段在文件里留空、key 进密钥库。"""

    name: str           # 用户起的别名，列表里展示与选择用
    backend: str        # "anthropic" | "openai" | "deepseek" | "ollama"
    model: str = ""     # 空 = 用后端默认模型
    key: str = ""       # API key（明文，原型阶段）
    base_url: str = ""  # 空 = 用后端默认（DeepSeek 默认 https://api.deepseek.com）


# ===========================================================================
# 安全隔离层:key 的读写集中在这三个函数。换 keyring 只改这里。
# ===========================================================================
def _read_keys(raw: dict) -> dict[str, str]:
    """从已加载的配置数据里取出 {别名: key}。明文版直接读 profile.key。"""
    return {p.get("name", ""): p.get("key", "") for p in raw.get("profiles", [])}


def _strip_keys_for_disk(data: dict) -> dict:
    """写盘前对 key 的处理。明文版原样保留;keyring 版应在此清空 key 字段。"""
    return data  # 明文:不剥离


# ===========================================================================
# 加载 / 保存
# ===========================================================================
def _load_raw() -> dict:
    if not CONFIG_FILE.exists():
        return {"active": "", "profiles": []}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"active": "", "profiles": []}


def _save_raw(data: dict) -> None:
    CONFIG_FILE.write_text(
        json.dumps(_strip_keys_for_disk(data), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ===========================================================================
# 公开 API（供设置界面调用）
# ===========================================================================
def list_profiles() -> list[ApiProfile]:
    """返回所有已配置的 API（含 key）。"""
    raw = _load_raw()
    keys = _read_keys(raw)
    out: list[ApiProfile] = []
    for p in raw.get("profiles", []):
        name = p.get("name", "")
        out.append(
            ApiProfile(
                name=name,
                backend=p.get("backend", "anthropic"),
                model=p.get("model", ""),
                key=keys.get(name, ""),
                base_url=p.get("base_url", ""),
            )
        )
    return out


def get_active() -> ApiProfile | None:
    """返回当前选中的配置;无则 None。"""
    raw = _load_raw()
    active = raw.get("active", "")
    for prof in list_profiles():
        if prof.name == active:
            return prof
    return None


def set_active(name: str) -> None:
    """切换当前使用的配置（按别名）。"""
    raw = _load_raw()
    raw["active"] = name
    _save_raw(raw)


def upsert_profile(profile: ApiProfile) -> None:
    """新增或按别名更新一条配置。若是第一条，自动设为 active。"""
    raw = _load_raw()
    profiles = raw.get("profiles", [])
    for i, p in enumerate(profiles):
        if p.get("name") == profile.name:
            profiles[i] = asdict(profile)
            break
    else:
        profiles.append(asdict(profile))
        if not raw.get("active"):
            raw["active"] = profile.name
    raw["profiles"] = profiles
    _save_raw(raw)


def delete_profile(name: str) -> None:
    """删除一条配置;若删的是 active，active 落到剩余第一条（或空）。"""
    raw = _load_raw()
    profiles = [p for p in raw.get("profiles", []) if p.get("name") != name]
    raw["profiles"] = profiles
    if raw.get("active") == name:
        raw["active"] = profiles[0]["name"] if profiles else ""
    _save_raw(raw)
