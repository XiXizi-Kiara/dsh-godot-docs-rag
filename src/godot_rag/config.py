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
# RRF 两路权重：dense 语义更强，BM25 作为精确符号召回的补充
DENSE_WEIGHT = 1.0
BM25_WEIGHT = 0.3
RERANK_TOP_K = 100
DEFAULT_TOP_K = 8
MAX_TOP_K = 20

# BGE 检索指令前缀（query 侧）
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "

# 计算设备：auto / cpu / cuda。
# auto：本进程 torch 有 CUDA 就用 GPU（godotrag 环境已装 torch cu128），否则 CPU。
DEVICE = os.environ.get("GODOT_RAG_DEVICE", "auto")


def ensure_dirs() -> None:
    """创建构建所需的输出目录。"""
    for d in (PROCESSED_DIR, INDEX_DIR, SMOKE_INDEX_DIR, REPORTS_DIR):
        d.mkdir(parents=True, exist_ok=True)

