"""索引构建流程：解析 -> 分块 -> 嵌入 -> BM25 -> 落盘。"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import config
from . import index as index_mod
from .bm25 import BM25Index
from .chunk import chunk_documents
from .embed import Encoder
from .parse_rst import parse_documents


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def build_index(
    limit: int | None = None,
    force: bool = False,
    index_dir: str | Path | None = None,
    device: str | None = None,
) -> dict:
    """构建索引并返回统计信息。

    limit 不为 None 时视为试跑：默认写入 data/index_smoke/，不覆盖正式索引。
    device：auto / cpu / cuda（见 embed.Encoder 的后端选择逻辑）。
    """
    t0 = time.time()
    if limit is not None and index_dir is None:
        index_dir = config.SMOKE_INDEX_DIR
    index_dir = Path(index_dir or config.INDEX_DIR)
    if (index_dir / "meta.json").exists() and not force:
        meta = index_mod.load_meta(index_dir)
        print(f"[build] 索引已存在（{index_dir}），如需重建请加 --force", flush=True)
        return {"skipped": True, **meta}

    config.ensure_dirs()

    # 1. 解析
    print(f"[build] 解析 RST（limit={limit}）...", flush=True)
    docs = parse_documents(limit=limit)
    print(f"[build] 文档 {len(docs)} 篇（{time.time() - t0:.1f}s）", flush=True)

    # 2. 分块
    chunks = chunk_documents(docs)
    chunks = [c for c in chunks if c["text"].strip()]
    print(
        f"[build] chunk {len(chunks)} 个，平均 {sum(c['n_tokens'] for c in chunks) // max(len(chunks), 1)} tok",
        flush=True,
    )

    # 3. 嵌入
    texts = [c["text"] for c in chunks]
    encoder = Encoder(device=device)
    embeddings = encoder.encode(texts, show_progress=True)

    # 4. BM25
    print("[build] 构建 BM25 ...", flush=True)
    bm25 = BM25Index.build(texts)

    # 5. 元信息与落盘
    import faiss
    import sentence_transformers

    meta = {
        "embed_model": config.EMBED_MODEL_NAME,
        "dim": int(embeddings.shape[1]) if len(embeddings) else config.EMBED_DIM,
        "rerank_model": config.RERANK_MODEL_NAME
        if (config.RERANK_MODEL_DIR / "config.json").exists()
        else None,
        "device": encoder.device,
        "embed_backend": "sentence-transformers",
        "bm25_backend": "rank_bm25.BM25Okapi",
        "libs": {
            "sentence_transformers": sentence_transformers.__version__,
            "faiss": faiss.__version__,
            "torch": __import__("torch").__version__,
            "transformers": __import__("transformers").__version__,
        },
        "limit": limit,
        "build_seconds": round(time.time() - t0, 1),
    }
    saved = index_mod.save_index(chunks, embeddings, bm25, docs, meta, index_dir)

    # 6. 调试产物（全量构建才写 processed，试跑写 smoke 前缀）
    if limit is None:
        _write_jsonl(config.PROCESSED_DIR / "documents.jsonl", [d.to_dict() for d in docs])
        _write_jsonl(config.PROCESSED_DIR / "chunks.jsonl", chunks)
    else:
        _write_jsonl(config.PROCESSED_DIR / "smoke_documents.jsonl", [d.to_dict() for d in docs])
        _write_jsonl(config.PROCESSED_DIR / "smoke_chunks.jsonl", chunks)

    stats = {
        "skipped": False,
        "index_dir": str(index_dir),
        "n_docs": len(docs),
        "n_chunks": len(chunks),
        "dim": saved["dim"],
        "device": encoder.device,
        "build_seconds": saved["build_seconds"],
        "avg_tokens": round(sum(c["n_tokens"] for c in chunks) / max(len(chunks), 1), 1),
    }
    print(
        f"[build] 完成：{stats['n_docs']} 篇 / {stats['n_chunks']} chunks / "
        f"{stats['dim']} 维 / {stats['build_seconds']}s -> {index_dir}",
        flush=True,
    )
    return stats
