from __future__ import annotations

from collections import Counter
from pathlib import Path

from filemanager.models import FileEntry


# 根目录下若存在这些路径名，为强信号（小写匹配）
MARKERS: dict[str, list[str]] = {
    "Java / Maven": ["pom.xml"],
    "Java / Gradle": ["build.gradle", "build.gradle.kts"],
    "Node.js": ["package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock"],
    "Python": ["pyproject.toml", "setup.py", "requirements.txt", "Pipfile"],
    "Rust": ["Cargo.toml"],
    "Go": ["go.mod"],
    ".NET": ["*.sln", "*.csproj"],  # handled specially
    "Git 仓库": [".git"],
    "Docker": ["Dockerfile", "docker-compose.yml", "compose.yaml"],
}

TOPIC_BY_EXT: list[tuple[str, frozenset[str]]] = [
    ("以图片为主", frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".raw", ".cr2", ".nef"})),
    ("以视频为主", frozenset({".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".m4v"})),
    ("以音频为主", frozenset({".mp3", ".flac", ".wav", ".aac", ".ogg", ".m4a", ".wma"})),
    ("以文档为主", frozenset({".pdf", ".doc", ".docx", ".ppt", ".xls", ".xlsx", ".pptx", ".odt", ".rtf"})),
    ("以代码 / 文本为主", frozenset({".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".cpp", ".h", ".cs", ".go", ".rs", ".rb", ".php", ".swift", ".kt", ".sql", ".sh", ".md", ".json", ".yml", ".yaml", ".xml", ".html", ".css"})),
    ("以压缩包为主", frozenset({".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz"})),
]


def _root_marker_hits(root: Path) -> list[str]:
    hits: list[str] = []
    try:
        names = {p.name.lower() for p in root.iterdir() if p.exists()}
    except OSError:
        return hits

    for label, markers in MARKERS.items():
        for m in markers:
            if m.startswith("*."):
                # wildcard: any file ending with suffix in root
                suf = m[1:].lower()
                if any(n.endswith(suf) for n in names):
                    hits.append(label)
                    break
            else:
                if m.lower() in names:
                    hits.append(label)
                    break
    return list(dict.fromkeys(hits))


def summarize_directory(root: Path, entries: list[FileEntry]) -> str:
    """基于扩展名分布与根目录标记生成可读摘要。"""
    if not entries:
        return "未发现文件，或尚未扫描。"

    root = root.resolve()
    n = len(entries)
    total = sum(e.size for e in entries)
    exts = Counter((e.suffix or "(无扩展名)") for e in entries)
    top = exts.most_common(8)

    lines: list[str] = []
    lines.append(f"共 {n} 个文件，总大小约 {_format_size(total)}。")

    # 扩展名占比（按数量）
    lines.append("扩展名（按数量）：")
    for ext, c in top:
        pct = 100.0 * c / n
        lines.append(f"  • {ext}: {c} ({pct:.1f}%)")

    # 内容侧写
    ext_set = {e.suffix for e in entries}
    for label, group in TOPIC_BY_EXT:
        inter = ext_set & group
        if not inter:
            continue
        # 占文件数比例
        count = sum(1 for e in entries if e.suffix in group)
        if count / n >= 0.35:
            lines.append(f"推测：{label}（约 {100 * count / n:.0f}% 文件命中相关扩展名）。")

    markers = _root_marker_hits(root)
    if markers:
        lines.append("根目录特征：" + "、".join(markers) + "。")

    if len(lines) <= 3 and not markers:
        lines.append("未发现强烈类型特征，可能是通用资料或混合目录。")

    return "\n".join(lines)


def _format_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    for unit in ("KB", "MB", "GB", "TB"):
        n /= 1024.0
        if n < 1024:
            return f"{n:.1f} {unit}"
    return f"{n:.1f} PB"
