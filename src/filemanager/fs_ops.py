from __future__ import annotations

import shutil
from pathlib import Path


def copy_paths(paths: list[Path], dest_dir: Path) -> tuple[list[str], list[str]]:
    """将文件复制到 dest_dir，保持文件名。返回 (成功相对描述, 错误信息)。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    ok: list[str] = []
    errors: list[str] = []
    for src in paths:
        try:
            if not src.is_file():
                errors.append(f"跳过非文件: {src}")
                continue
            target = dest_dir / src.name
            if target.resolve() == src.resolve():
                errors.append(f"源与目标相同: {src}")
                continue
            shutil.copy2(src, target)
            ok.append(str(target))
        except OSError as e:
            errors.append(f"{src}: {e}")
    return ok, errors


def trash_paths(paths: list[Path]) -> tuple[list[str], list[str]]:
    """移入回收站。依赖 send2trash。"""
    import send2trash  # 延迟导入

    ok: list[str] = []
    errors: list[str] = []
    for p in paths:
        try:
            send2trash.send2trash(str(p))
            ok.append(str(p))
        except Exception as e:  # noqa: BLE001
            errors.append(f"{p}: {e}")
    return ok, errors
