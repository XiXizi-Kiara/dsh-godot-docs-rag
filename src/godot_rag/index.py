"""索引持久化：chunks / faiss 向量索引 / BM25 / documents。

目录布局（data/index/ 或 data/index_smoke/）：

- meta.json          构建元信息
- documents.jsonl    全部 Document（调试/统计）
- chunks.pkl         list[dict] chunk，与 faiss 向量行对齐
- faiss.index        faiss.IndexFlatIP（内积 = 余弦，向量已归一化）
- bm25.pkl           BM25Index（rank_bm25）
- docs.pkl           dict[doc_id] -> Document
"""
from __future__ import annotations

import json
import pickle
import time
from pathlib import Path

import numpy as np

from . import config
from .bm25 import BM25Index
from .types import Document


def paths(index_dir: str | Path | None = None) -> dict[str, Path]:
    d = Path(index_dir or config.INDEX_DIR)
    return {
        "dir": d,
        "meta": d / "meta.json",
        "documents": d / "documents.jsonl",
        "chunks": d / "chunks.pkl",
        "faiss": d / "faiss.index",
        "bm25": d / "bm25.pkl",
        "docs": d / "docs.pkl",
    }


def save_index(
    chunks: list[dict],
    embeddings: np.ndarray,
    bm25: BM25Index,
    documents: list[Document],
    meta: dict,
    index_dir: str | Path | None = None,
) -> dict:
    import faiss

    p = paths(index_dir)
    p["dir"].mkdir(parents=True, exist_ok=True)

    mat = np.ascontiguousarray(embeddings.astype(np.float32))
    faiss_index = faiss.IndexFlatIP(mat.shape[1])
    faiss_index.add(mat)
    faiss.write_index(faiss_index, str(p["faiss"]))

    bm25.save(str(p["bm25"]))
    with open(p["chunks"], "wb") as f:
        pickle.dump(chunks, f, protocol=pickle.HIGHEST_PROTOCOL)
    docs = {d.doc_id: d for d in documents}
    with open(p["docs"], "wb") as f:
        pickle.dump(docs, f, protocol=pickle.HIGHEST_PROTOCOL)

    meta = dict(meta)
    meta.setdefault("created_at", time.strftime("%Y-%m-%d %H:%M:%S"))
    meta["n_chunks"] = len(chunks)
    meta["n_docs"] = len(documents)
    meta["index_backend"] = f"faiss.IndexFlatIP(dim={mat.shape[1]})"
    with open(p["meta"], "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return meta


def load_meta(index_dir: str | Path | None = None) -> dict:
    p = paths(index_dir)
    if not p["meta"].exists():
        raise FileNotFoundError(
            f"索引不存在：{p['dir']}\n请先运行：python scripts/run.py build"
        )
    with open(p["meta"], "r", encoding="utf-8") as f:
        return json.load(f)


def load_chunks(index_dir: str | Path | None = None) -> list[dict]:
    p = paths(index_dir)
    with open(p["chunks"], "rb") as f:
        return pickle.load(f)


def load_faiss(index_dir: str | Path | None = None):
    import faiss

    p = paths(index_dir)
    return faiss.read_index(str(p["faiss"]))


def load_bm25(index_dir: str | Path | None = None) -> BM25Index:
    p = paths(index_dir)
    return BM25Index.load(str(p["bm25"]))


def load_docs(index_dir: str | Path | None = None) -> dict[str, Document]:
    p = paths(index_dir)
    with open(p["docs"], "rb") as f:
        return pickle.load(f)
