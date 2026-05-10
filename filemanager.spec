# -*- mode: python ; coding: utf-8 -*-
"""
打包：在项目根目录执行
  pip install -e ".[pack]"
  pyinstaller filemanager.spec
生成目录: dist/FileManager/ （内含 FileManager.exe）
"""

from PyInstaller.utils.hooks import collect_all

_pyside_datas, _pyside_binaries, _pyside_hidden = collect_all("PySide6")

a = Analysis(
    ["src/filemanager/main.py"],
    pathex=["src"],
    binaries=_pyside_binaries,
    datas=_pyside_datas,
    hiddenimports=list(_pyside_hidden) + ["send2trash"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FileManager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="FileManager",
)
