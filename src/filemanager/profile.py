"""目录「画像」：根据扫描得到的文件列表与根目录下文件名做启发式统计，生成说明文字。

注意：这是经验规则，不是分类器；仅供用户快速建立对文件夹用途的印象。
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from filemanager.models import FileEntry


# 根目录下若存在这些路径名，为强信号（小写匹配）。
# 值中的 "*.sln" 等在 _root_marker_hits 里按后缀通配处理。
MARKERS: dict[str, list[str]] = {
    "Java / Maven": ["pom.xml"],
    "Java / Gradle": ["build.gradle", "build.gradle.kts"],
    "Node.js": ["package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock"],
    "Python": ["pyproject.toml", "setup.py", "requirements.txt", "Pipfile"],
    "Rust": ["Cargo.toml"],
    "Go": ["go.mod"],
    ".NET": ["*.sln", "*.csproj"],  # 通配：根下任意文件名以该后缀结尾即命中
    "Git 仓库": [".git"],
    "Docker": ["Dockerfile", "docker-compose.yml", "compose.yaml"],
}

# 按扩展名集合推断「目录更可能以哪类内容为主」；仅当命中文件占比 ≥ 阈值时才输出一句推测。
TOPIC_BY_EXT: list[tuple[str, frozenset[str]]] = [
    ("以图片为主", frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".raw", ".cr2", ".nef"})),
    ("以视频为主", frozenset({".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".m4v"})),
    ("以音频为主", frozenset({".mp3", ".flac", ".wav", ".aac", ".ogg", ".m4a", ".wma"})),
    ("以文档为主", frozenset({".pdf", ".doc", ".docx", ".ppt", ".xls", ".xlsx", ".pptx", ".odt", ".rtf"})),
    ("以代码 / 文本为主", frozenset({".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".cpp", ".h", ".cs", ".go", ".rs", ".rb", ".php", ".swift", ".kt", ".sql", ".sh", ".md", ".json", ".yml", ".yaml", ".xml", ".html", ".css"})),
    ("以压缩包为主", frozenset({".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz"})),
]


def _root_marker_hits(root: Path) -> list[str]:
    """扫描 root 下直接子项的文件名（小写集合），与 MARKERS 规则匹配，返回去重后的标签列表。"""
    hits: list[str] = []
    try:
        names = {p.name.lower() for p in root.iterdir() if p.exists()}
    except OSError:
        return hits

    for label, markers in MARKERS.items():
        for m in markers:
            if m.startswith("*."):
                # 通配：根目录任意文件名以该后缀结尾即视为该项目类型
                suf = m[1:].lower()
                if any(n.endswith(suf) for n in names):
                    hits.append(label)
                    break
            else:
                if m.lower() in names:
                    hits.append(label)
                    break
    # dict.fromkeys 保持顺序去重
    return list(dict.fromkeys(hits))


def summarize_directory(root: Path, entries: list[FileEntry]) -> str:
    """基于扩展名分布与根目录标记生成可读摘要（多行字符串，供 QPlainTextEdit 显示）。"""
    if not entries:
        return "未发现文件，或尚未扫描。"

    root = root.resolve()
    n = len(entries)
    total = sum(e.size for e in entries)
    exts = Counter((e.suffix or "(无扩展名)") for e in entries)
    top = exts.most_common(8)

    lines: list[str] = []
    lines.append(f"共 {n} 个文件，总大小约 {_format_size(total)}。")

    # 扩展名占比（按数量，非按总字节）
    lines.append("扩展名（按数量）：")
    for ext, c in top:
        pct = 100.0 * c / n
        lines.append(f"  • {ext}: {c} ({pct:.1f}%)")

    # 「内容侧写」：某类扩展名在全部文件中占比 ≥ 35% 则输出一条推测句
    ext_set = {e.suffix for e in entries}
    for label, group in TOPIC_BY_EXT:
        inter = ext_set & group
        if not inter:
            continue
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
    """人类可读字节数（与 table_model 中展示逻辑同源，避免重复实现大数算法）。"""
    if n < 1024:
        return f"{n} B"
    for unit in ("KB", "MB", "GB", "TB"):
        n /= 1024.0
        if n < 1024:
            return f"{n:.1f} {unit}"
    return f"{n:.1f} PB"
