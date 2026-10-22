"""用 conda godotrag 环境运行全部单元测试。

    python scripts/run_tests.py [-v]

`python` 指 conda 环境 godotrag 的解释器（可先 activate，或用其绝对路径）。
不依赖 GPU、不依赖全量索引、不访问网络。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    verbosity = 2 if "-v" in sys.argv else 1
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT))
    result = unittest.TextTestRunner(verbosity=verbosity).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
