"""文件操作封装：复制、回收站删除、按卷区分的永久删除。

与 ``window._trash_selected`` 的配合方式：
- ``path_expects_recycle_bin`` 为 True 的路径走 ``send2trash``（用户侧文案为回收站）；
- 为 False 的路径走 ``unlink``，避免在 U 盘/网络盘上误导「可恢复」。

非 Windows 平台未做卷类型细分，一律视为可走 ``send2trash``（由操作系统与库决定实际行为）。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

# Windows API：GetDriveTypeW 用于区分固定盘 / 可移动 / 网络 / 光驱等。
# 文档：https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getdrivetypew
if sys.platform == "win32":
    import ctypes

    _DRIVE_REMOVABLE = 2  # 通常为 U 盘、部分读卡器
    _DRIVE_REMOTE = 4  # 映射的网络驱动器
    _DRIVE_CDROM = 5  # 光驱


def path_expects_recycle_bin(path: Path) -> bool:
    """是否适合向用户承诺「移入回收站后可恢复」。

    Windows 上可移动盘、网络驱动器、光驱等卷通常不会把删除放进本机回收站语义；
    其它平台由 send2trash 处理，此处返回 True。

    无盘符路径（如部分 UNC）保守返回 False，由界面按「永久删除」提示。
    标注为「固定盘」的外接硬盘（部分硬盘盒）仍可能返回 True，与资源管理器常见行为一致。
    """
    if sys.platform != "win32":
        return True
    try:
        resolved = path.resolve()
    except OSError:
        return True
    drive = resolved.drive
    if not drive:
        return False
    root = f"{drive}\\"
    try:
        dt = int(ctypes.windll.kernel32.GetDriveTypeW(root))
    except (AttributeError, OSError, ValueError):
        return True
    if dt in (_DRIVE_REMOVABLE, _DRIVE_REMOTE, _DRIVE_CDROM):
        return False
    return True


def delete_paths_permanent(paths: list[Path]) -> tuple[list[str], list[str]]:
    """永久删除文件（不经过回收站）。返回 (成功路径字符串列表, 错误信息列表)。"""
    ok: list[str] = []
    errors: list[str] = []
    for p in paths:
        try:
            if not p.is_file():
                errors.append(f"跳过非文件: {p}")
                continue
            p.unlink()
            ok.append(str(p))
        except OSError as e:
            errors.append(f"{p}: {e}")
    return ok, errors


def copy_paths(paths: list[Path], dest_dir: Path) -> tuple[list[str], list[str]]:
    """将文件复制到 dest_dir，保持文件名。返回 (成功列表, 错误列表)。

    ``shutil.copy2`` 会尽量保留元数据；源与目标为同一文件时跳过，避免无意义或自复制。"""
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
    """移入回收站（依赖第三方库 send2trash，调用 Shell/桌面环境提供的「回收」语义）。

    延迟导入：避免未使用删除功能时仍加载该依赖。"""
    import send2trash  # 延迟导入

    ok: list[str] = []
    errors: list[str] = []
    for p in paths:
        try:
            send2trash.send2trash(str(p))
            ok.append(str(p))
        except Exception as e:  # noqa: BLE001
            # send2trash 可能抛出多种异常；统一记入 errors 供界面展示
            errors.append(f"{p}: {e}")
    return ok, errors
