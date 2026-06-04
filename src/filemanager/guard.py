"""写操作安全护栏:路径白名单 + 系统目录硬禁。

阶段 3 产物。所有**写操作**（复制目标、删除对象）在执行前必须经 ``check_write_allowed``。
设计:
- **硬黑名单**:系统关键目录永远禁止写操作，即使用户把它加进白名单也拦（防误删系统盘）。
- **白名单**:用户可配置允许操作的目录树;为空时默认允许"用户主目录及其子目录"（开箱即用）。
- 判定基于 ``resolve()`` 后的真实路径，并用 ``is_relative_to`` 做子树包含判断，防 ``..`` 越权。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path


def _system_blocklist() -> list[Path]:
    """各平台禁止写操作的系统关键目录（**子树匹配**:目录本身及其下任意路径都禁止）。

    注意:**不包含裸根**（``/`` 或 ``C:\\``）——根的子树是整个磁盘，放进来会拦下一切。
    "不许在根/系统盘乱操作"由白名单负责（默认白名单仅含用户主目录，自然不含这些）。
    """
    blocks: list[Path] = []
    if sys.platform == "win32":
        import os

        sysroot = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        blocks += [
            sysroot,                                   # C:\Windows（及其下全部）
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
            Path(os.environ.get("ProgramData", r"C:\ProgramData")),
        ]
    elif sys.platform == "darwin":
        blocks += [Path("/System"), Path("/Library"), Path("/usr"), Path("/bin"),
                   Path("/sbin"), Path("/private")]
    else:
        blocks += [Path("/usr"), Path("/bin"), Path("/sbin"), Path("/etc"),
                   Path("/boot"), Path("/lib"), Path("/sys"), Path("/proc")]
    # 规范化
    out: list[Path] = []
    for b in blocks:
        try:
            out.append(b.resolve())
        except OSError:
            out.append(b)
    return out


def _default_allowed() -> list[Path]:
    """白名单为空时的默认:允许用户主目录及其子目录。"""
    try:
        return [Path.home().resolve()]
    except OSError:
        return [Path.home()]


@dataclass
class GuardResult:
    allowed: bool
    reason: str = ""


def _is_within(path: Path, root: Path) -> bool:
    """path 是否在 root 子树内（含 root 自身）。基于已 resolve 的真实路径。"""
    try:
        return path == root or path.is_relative_to(root)
    except ValueError:
        return False


def check_write_allowed(path: Path, allowed_roots: list[Path] | None = None) -> GuardResult:
    """判断对 ``path`` 的写操作是否被允许。

    规则（按优先级）:
    1. 命中系统黑名单（或其子目录）→ 拒绝（最高优先级，白名单也不能覆盖）。
    2. 在白名单某个根的子树内 → 允许。
    3. 白名单为空时，回退到默认（用户主目录子树）。
    4. 其余 → 拒绝。
    """
    try:
        p = path.resolve()
    except OSError as e:
        return GuardResult(False, f"无法解析路径:{e}")

    # 1. 系统目录硬禁（含位于系统目录内的任何子路径）
    for blocked in _system_blocklist():
        if _is_within(p, blocked):
            return GuardResult(False, f"位于受保护的系统目录内，禁止操作:{blocked}")

    # 2/3. 白名单（空则用默认）
    roots = [r.resolve() for r in allowed_roots] if allowed_roots else _default_allowed()
    for root in roots:
        if _is_within(p, root):
            return GuardResult(True)

    # 4. 不在任何允许范围
    pretty = "、".join(str(r) for r in roots)
    return GuardResult(False, f"不在允许操作的目录范围内（当前允许:{pretty}）")


def filter_allowed(paths: list[Path], allowed_roots: list[Path] | None = None) -> tuple[list[Path], list[tuple[Path, str]]]:
    """把一批路径按护栏拆成 (允许的, [(被拒的, 原因)])。"""
    ok: list[Path] = []
    denied: list[tuple[Path, str]] = []
    for p in paths:
        r = check_write_allowed(p, allowed_roots)
        (ok if r.allowed else denied).append(p if r.allowed else (p, r.reason))
    return ok, denied
