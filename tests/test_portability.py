"""仓库可移植性守卫：被扫描的文本文件不得出现机器相关的绝对路径。

背景：早期设计稿没有约束路径写法，导致错误提示、脚本注释与文档示例里出现了
`C:\\Users\\<name>\\...` 这类本机路径（已修复代码侧）。规则写回
`docs/superpowers/specs/2026-10-22-godot-rag-design.md` §15。

规则：
- 代码、脚本、配置、测试：禁止出现机器路径；
- 历史文档里的示例暂列 allowlist（M4 文档清理后必须移除），
  allowlist 之外的新增违规立即失败。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TEXT_SUFFIXES = {
    ".py", ".ps1", ".sh", ".bat", ".cmd", ".js", ".mjs", ".cjs", ".ts",
    ".json", ".yml", ".yaml", ".toml", ".cfg", ".ini", ".md", ".txt",
    ".html", ".css",
}

SKIP_DIRS = {
    ".git", ".tmp", ".venv", "venv", "node_modules", "__pycache__",
    ".pytest_cache", "models", "logs", "dist", "build",
    "godot-docs-html-stable",
}

MAX_BYTES = 2_000_000

PATTERNS = (
    re.compile(r"[A-Za-z]:\\Users\\[A-Za-z0-9_.-]+"),   # C:\Users\<name>
    re.compile(r"/Users/[A-Za-z0-9_.-]+/"),             # /Users/<name>/
    re.compile(r"/home/[A-Za-z0-9_.-]+/"),              # /home/<name>/
)

# 历史文档示例：仅这些文件允许暂时存在机器路径。
# M4 清理 docs 后必须清空；清空前新增的其它违规仍会失败。
LEGACY_ALLOWLIST = {
    "README.md",
    "docs/USAGE.md",
    "docs/INTERFACES.md",
    "docs/ENV.md",
    "docs/DSH_MCP_SETUP.md",
}

# 守卫自身的检测样例包含机器路径字面量，不能扫描自己。
SELF_EXEMPT = {"tests/test_portability.py"}


def iter_text_files(root: Path = ROOT):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            if path.stat().st_size > MAX_BYTES:
                continue
        except OSError:
            continue
        yield path, rel.as_posix()


def find_violations(root: Path = ROOT, allowlist=frozenset(LEGACY_ALLOWLIST)):
    hits: list[str] = []
    for path, rel in iter_text_files(root):
        if rel in allowlist or rel in SELF_EXEMPT:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            for pattern in PATTERNS:
                match = pattern.search(line)
                if match:
                    hits.append(f"{rel}:{lineno}: {match.group(0)}")
                    break
    return hits


class TestPortability(unittest.TestCase):
    def test_detector_matches_machine_paths(self):
        sample = (
            r"run C:\Users\alice\.conda\envs\godotrag\python.exe scripts\run.py"
            " and /home/bob/project and /Users/carol/work"
        )
        matched = [p.pattern for p in PATTERNS if p.search(sample)]
        self.assertEqual(len(matched), 3, f"守卫未识别全部机器路径：{matched}")

    def test_detector_accepts_placeholders(self):
        sample = (
            "python <PYTHON_EXE> <PROJECT_DIR> "
            "$HOME/.conda/envs/godotrag/python.exe "
            "/Users/<name>/project <homedir>/.conda/envs/godotrag"
        )
        hits = [p.pattern for p in PATTERNS if p.search(sample)]
        self.assertEqual(hits, [], f"占位符被误判为机器路径：{hits}")

    def test_repository_has_no_machine_paths(self):
        hits = find_violations()
        self.assertEqual(
            hits,
            [],
            "发现机器相关绝对路径（请改为环境变量/占位符，或由 config 推导）：\n"
            + "\n".join(hits[:20]),
        )


if __name__ == "__main__":
    unittest.main()
