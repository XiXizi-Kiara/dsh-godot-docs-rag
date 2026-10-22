"""全局配置：路径、模型与构建参数。

所有路径均可通过环境变量覆盖，便于测试与迁移。
"""
from __future__ import annotations

import os
from pathlib import Path

# 项目根目录（src/godot_rag/ 上三级）
ROOT: Path = Path(os.environ.get("GODOT_RAG_ROOT", Path(__file__).resolve().parents[2]))

# 数据源
DOCS_ROOT: Path = ROOT / "godot-docs-html-stable"
RST_DIR: Path = DOCS_ROOT / "_sources"
HTML_DIR: Path = DOCS_ROOT

# 中间产物与索引
DATA_DIR: Path = ROOT / "data"
PROCESSED_DIR: Path = DATA_DIR / "processed"
INDEX_DIR: Path = Path(os.environ.get("GODOT_RAG_INDEX_DIR", DATA_DIR / "index"))
SMOKE_INDEX_DIR: Path = DATA_DIR / "index_smoke"
REPORTS_DIR: Path = DATA_DIR / "reports"

# 模型
MODELS_DIR: Path = ROOT / "models"
EMBED_MODEL_DIR: Path = Path(os.environ.get("GODOT_RAG_EMBED_MODEL", MODELS_DIR / "bge-small-en-v1.5"))
RERANK_MODEL_DIR: Path = Path(os.environ.get("GODOT_RAG_RERANK_MODEL", MODELS_DIR / "bge-reranker-base"))

# 模型标识（写入 meta.json，供排查）
EMBED_MODEL_NAME = "BAAI/bge-small-en-v1.5"
RERANK_MODEL_NAME = "BAAI/bge-reranker-base"
EMBED_DIM = 384

# 分块参数（token 为估算值，按 4 字符 ≈ 1 token）
CHUNK_TARGET_TOKENS = 320
CHUNK_MAX_TOKENS = 600
CHUNK_MIN_TOKENS = 80
CHUNK_OVERLAP_TOKENS = 40

# 检索参数
DENSE_TOP_K = 100
BM25_TOP_K = 100
RRF_K = 60
RERANK_TOP_K = 50
DEFAULT_TOP_K = 8
MAX_TOP_K = 20

# BGE 检索指令前缀（query 侧）
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "

# 计算设备：auto / cpu / cuda。
# auto：本进程有 CUDA 就用 GPU；否则若找到可用的 GPU Python 解释器，则把批量嵌入
#       委托给该解释器（外部 GPU 后端）；再否则用 CPU。
DEVICE = os.environ.get("GODOT_RAG_DEVICE", "auto")

# 外部 GPU 后端解释器（需装有 CUDA 版 torch）。可用环境变量显式指定，
# 否则自动探测常见的 conda 环境。
GPU_PYTHON = os.environ.get("GODOT_RAG_GPU_PYTHON") or None


def _detect_gpu_python() -> Path | None:
    if GPU_PYTHON:
        p = Path(GPU_PYTHON)
        return p if p.exists() else None
    candidates = [
        Path.home() / ".conda" / "envs" / "pytorch" / "python.exe",
        Path("D:/Anaconda3/envs/pytorch/python.exe"),
        Path.home() / "miniconda3" / "envs" / "pytorch" / "python.exe",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


GPU_PYTHON_PATH = _detect_gpu_python()


def ensure_dirs() -> None:
    """创建构建所需的输出目录。"""
    for d in (PROCESSED_DIR, INDEX_DIR, SMOKE_INDEX_DIR, REPORTS_DIR):
        d.mkdir(parents=True, exist_ok=True)

