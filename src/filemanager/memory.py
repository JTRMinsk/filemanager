"""长期记忆:Markdown 文件（``CLAUDE.md`` 风格），存于用户数据目录。

阶段 4 产物。设计（按用户选择）:
- **MD 为主**:纯文本、人可读可手改、零依赖。存 ``config.MEMORY_MD``（%APPDATA% 等）。
- **记什么由用户把关**:Agent 通过 ``remember`` 工具写入，而该工具受确认机制约束
  （明说"记住X"或 Agent 主动想记，都会弹确认，用户点头才落盘）。
- **只帮理解、不驱动破坏**:记忆注入系统提示供模型理解偏好;删除/复制等仍逐次确认，
  绝不因记忆内容跳过确认（该约束在系统提示中明确，并由 Agent 的确认流程兜底）。
- 原型阶段不做敏感信息过滤（用户选择）。

文件结构示例:
    # 用户偏好
    - 下载的 PDF 习惯归到 D:\\Docs\\PDF

    # 目录备注
    - E:\\Projects\\old :已废弃，node_modules 可清理
"""

from __future__ import annotations

from filemanager.config import MEMORY_MD

DEFAULT_SECTION = "其它"


def load_markdown() -> str:
    """读取全部记忆原文;文件不存在返回空串。供 ``_build_system`` 注入系统提示。"""
    try:
        if MEMORY_MD.exists():
            return MEMORY_MD.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return ""


def read_all() -> str:
    """同 ``load_markdown``，语义化别名（供 GUI"查看记忆"用）。"""
    return load_markdown()


def append(text: str, section: str = DEFAULT_SECTION) -> None:
    """把一条记忆追加到指定小节（小节不存在则创建）。

    实现简单稳健:读出全文→定位/新建 ``# section`` 标题→在该小节末尾加 ``- text``→写回。
    """
    text = text.strip()
    if not text:
        return
    section = (section or DEFAULT_SECTION).strip()
    line = f"- {text}"

    content = load_markdown()
    if not content:
        new = f"# {section}\n{line}\n"
        _write(new)
        return

    lines = content.split("\n")
    header = f"# {section}"
    # 找到该小节标题
    idx = None
    for i, ln in enumerate(lines):
        if ln.strip() == header:
            idx = i
            break

    if idx is None:
        # 没有这个小节:追加到文件末尾
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(header)
        lines.append(line)
    else:
        # 有这个小节:插到它的下一个标题之前（即小节末尾）
        insert_at = len(lines)
        for j in range(idx + 1, len(lines)):
            if lines[j].lstrip().startswith("# "):
                insert_at = j
                break
        # 跳过小节末尾的空行，紧贴内容插入
        k = insert_at
        while k - 1 > idx and not lines[k - 1].strip():
            k -= 1
        lines.insert(k, line)

    _write("\n".join(lines).strip() + "\n")


def search(query: str, limit: int = 20) -> list[str]:
    """关键词检索:返回包含 query（大小写不敏感）的记忆条目行。

    阶段 4 用最简单的子串匹配;记忆量大时可在阶段 6 换向量检索（接口不变）。
    query 为空则返回全部条目（去掉标题行）。
    """
    content = load_markdown()
    if not content:
        return []
    q = query.strip().lower()
    items = []
    for ln in content.split("\n"):
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        bullet = s[2:].strip() if s.startswith("- ") else s
        if not q or q in bullet.lower():
            items.append(bullet)
        if len(items) >= limit:
            break
    return items


def clear() -> None:
    """清空全部记忆（删除文件）。供 GUI"清空记忆"用。"""
    try:
        if MEMORY_MD.exists():
            MEMORY_MD.unlink()
    except OSError:
        pass


def overwrite(content: str) -> None:
    """整体覆盖记忆内容（供 GUI 直接编辑后保存）。"""
    _write(content if content.endswith("\n") else content + "\n")


def _write(content: str) -> None:
    try:
        MEMORY_MD.parent.mkdir(parents=True, exist_ok=True)
        MEMORY_MD.write_text(content, encoding="utf-8")
    except OSError:
        pass
