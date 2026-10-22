"""项目统一入口：用 conda godotrag 环境运行。

用法（在项目根目录）：
    C:\\Users\\Kiara\\.conda\\envs\\godotrag\\python.exe scripts\\run.py build
    C:\\Users\\Kiara\\.conda\\envs\\godotrag\\python.exe scripts\\run.py search "how to free a node" --json

等价于设置 PYTHONPATH=src 后执行 `python -m godot_rag`。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from godot_rag.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
