"""嵌入与重排序：基于 sentence-transformers，CPU/GPU 自动选择。

- Embedding：BAAI/bge-small-en-v1.5（SentenceTransformer，CLS pooling + L2 归一化）
- Reranker：BAAI/bge-reranker-base（CrossEncoder）
- 有 CUDA 用 GPU（RTX 4060 实测 ~259 条/s，重排 50 条约 0.3s），否则 CPU
- device 参数 / 环境变量 GODOT_RAG_DEVICE：auto | cpu | cuda
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")

import numpy as np

from . import config


def _pick_device(device: str | None) -> str:
    if device and device != "auto":
        return device
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


class Encoder:
    """BGE embedding 编码器（SentenceTransformer）。"""

    def __init__(
        self,
        model_dir: str | Path | None = None,
        device: str | None = None,
        batch_size: int = 64,
        max_length: int = 512,
    ) -> None:
        self.model_dir = Path(model_dir or config.EMBED_MODEL_DIR)
        self.requested = (device or config.DEVICE or "auto").lower()
        self.device = _pick_device(self.requested)
        self.batch_size = batch_size
        self.max_length = max_length
        self._model = None
        print(f"[embed] device={self.device}（请求 {self.requested}）", file=sys.stderr)

    def _load(self) -> None:
        if self._model is not None:
            return
        if not (self.model_dir / "config.json").exists():
            raise RuntimeError(
                f"embedding 模型缺失：{self.model_dir}\n"
                "请先运行：python scripts/fetch_models.py（走 ModelScope/hf-mirror 下载）"
            )
        from sentence_transformers import SentenceTransformer

        t0 = time.time()
        self._model = SentenceTransformer(str(self.model_dir), device=self.device)
        self._model.max_seq_length = self.max_length
        print(
            f"[embed] 模型加载完成 device={self.device} ({time.time() - t0:.1f}s)",
            file=sys.stderr,
        )

    def encode(
        self,
        texts: list[str],
        is_query: bool = False,
        batch_size: int | None = None,
        show_progress: bool = True,
    ) -> np.ndarray:
        """编码文本，返回 (N, dim) float32 归一化向量。"""
        if not texts:
            return np.zeros((0, config.EMBED_DIM), dtype=np.float32)
        self._load()
        if is_query:
            texts = [config.QUERY_INSTRUCTION + t for t in texts]
        vecs = self._model.encode(
            texts,
            batch_size=batch_size or self.batch_size,
            normalize_embeddings=True,
            show_progress_bar=show_progress,
            convert_to_numpy=True,
        )
        return np.asarray(vecs, dtype=np.float32)

    def encode_query(self, query: str) -> np.ndarray:
        return self.encode([query], is_query=True, show_progress=False)[0]


class Reranker:
    """BGE cross-encoder 重排序器（CrossEncoder）；模型缺失时 available() 为 False。"""

    def __init__(
        self,
        model_dir: str | Path | None = None,
        device: str | None = None,
        batch_size: int = 32,
        max_length: int = 512,
    ) -> None:
        self.model_dir = Path(model_dir or config.RERANK_MODEL_DIR)
        self.requested = (device or config.DEVICE or "auto").lower()
        self.device = _pick_device(self.requested)
        self.batch_size = batch_size
        self.max_length = max_length
        self._model = None

    def available(self) -> bool:
        return (self.model_dir / "config.json").exists()

    def _load(self) -> None:
        if self._model is not None:
            return
        if not self.available():
            raise RuntimeError(f"reranker 模型缺失：{self.model_dir}")
        from sentence_transformers import CrossEncoder

        t0 = time.time()
        self._model = CrossEncoder(
            str(self.model_dir),
            device=self.device,
            max_length=self.max_length,
        )
        print(
            f"[rerank] 模型加载完成 device={self.device} ({time.time() - t0:.1f}s)",
            file=sys.stderr,
        )

    def score(self, query: str, passages: list[str]) -> np.ndarray:
        """返回每条 passage 与 query 的相关性分数（0-1）。"""
        if not passages:
            return np.zeros((0,), dtype=np.float32)
        self._load()
        pairs = [[query, p] for p in passages]
        scores = self._model.predict(
            pairs, batch_size=self.batch_size, show_progress_bar=False
        )
        return np.asarray(scores, dtype=np.float32).reshape(-1)
