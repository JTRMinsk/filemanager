"""写操作日志:把复制/删除等动作落 SQLite，便于回溯。

阶段 3 产物。仅标准库 ``sqlite3``，单文件存于用户数据目录（方案 §3.3 / §6.2）。
只读操作（扫描/筛选/预览/画像）不记录。
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from filemanager.config import MEMORY_DB

_SCHEMA = """
CREATE TABLE IF NOT EXISTS operations (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL    NOT NULL,
    kind    TEXT    NOT NULL,   -- copy / trash / delete_permanent
    src     TEXT,
    dest    TEXT,
    result  TEXT,               -- ok / error
    detail  TEXT
);
"""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(MEMORY_DB))
    conn.execute(_SCHEMA)
    return conn


def log_operation(kind: str, src: str = "", dest: str = "", result: str = "ok", detail: str = "") -> None:
    """记录一条操作。失败不抛（日志不该阻断主流程）。"""
    try:
        conn = _conn()
        with conn:
            conn.execute(
                "INSERT INTO operations (ts, kind, src, dest, result, detail) VALUES (?,?,?,?,?,?)",
                (time.time(), kind, src, dest, result, detail),
            )
        conn.close()
    except sqlite3.Error:
        pass


def recent_operations(limit: int = 50) -> list[dict]:
    """取最近若干条操作日志（供将来"撤销/查看历史"功能用）。"""
    try:
        conn = _conn()
        rows = conn.execute(
            "SELECT ts, kind, src, dest, result, detail FROM operations ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        return [
            {"ts": r[0], "kind": r[1], "src": r[2], "dest": r[3], "result": r[4], "detail": r[5]}
            for r in rows
        ]
    except sqlite3.Error:
        return []
