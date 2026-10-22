"""检索层：稠密 + BM25 双路召回，RRF 融合，可选 cross-encoder 精排。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

from . import config
from . import index as index_mod
from .bm25 import BM25Index
from .embed import Encoder, Reranker
from .zh_dict import translate_query

VALID_MODES = {"hybrid", "dense", "bm25", "hybrid+rerank", "dense+rerank"}


class IndexNotReady(RuntimeError):
    """索引尚未构建。"""


def _normalize_mode(mode: str) -> str:
    m = (mode or "hybrid").strip().lower()
    if m not in VALID_MODES:
        raise ValueError(f"不支持的检索模式：{mode}（可选：{', '.join(sorted(VALID_MODES))}）")
    return m


class GodotRAG:
    """Godot 官方文档 RAG 引擎（懒加载，线程内使用）。"""

    def __init__(self, index_dir: str | Path | None = None, device: str | None = None) -> None:
        self.index_dir = Path(index_dir or config.INDEX_DIR)
        # auto / cpu / cuda：查询编码与重排都使用该设备（CUDA 可用时自动 GPU）
        self.device = (device or config.DEVICE or "auto").lower()
        self._meta: dict | None = None
        self._chunks: list[dict] | None = None
        self._faiss = None
        self._bm25: BM25Index | None = None
        self._docs: dict | None = None
        self._encoder: Encoder | None = None
        self._reranker: Reranker | None = None
        self._loaded = False

    # -- 状态 -------------------------------------------------------------
    @property
    def ready(self) -> bool:
        return (self.index_dir / "meta.json").exists()

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        if not self.ready:
            raise IndexNotReady(
                f"索引不存在：{self.index_dir}\n请先运行：python scripts/run.py build"
            )
        t0 = time.time()
        self._meta = index_mod.load_meta(self.index_dir)
        self._chunks = index_mod.load_chunks(self.index_dir)
        self._faiss = index_mod.load_faiss(self.index_dir)
        self._bm25 = index_mod.load_bm25(self.index_dir)
        self._docs = index_mod.load_docs(self.index_dir)
        self._loaded = True
        print(
            f"[rag] 索引加载完成 {len(self._chunks)} chunks / "
            f"{len(self._docs)} docs（{time.time() - t0:.1f}s）",
            file=sys.stderr,
        )

    def stats(self) -> dict:
        meta = None
        try:
            meta = index_mod.load_meta(self.index_dir)
        except FileNotFoundError:
            pass
        info = {
            "ready": self.ready,
            "index_dir": str(self.index_dir),
            "modes": sorted(VALID_MODES),
        }
        if meta:
            info.update(
                {
                    "n_chunks": meta.get("n_chunks"),
                    "n_docs": meta.get("n_docs"),
                    "dim": meta.get("dim"),
                    "embed_model": meta.get("embed_model"),
                    "rerank_model": meta.get("rerank_model"),
                    "rerank_available": (self.index_dir / "meta.json").exists()
                    and (config.RERANK_MODEL_DIR / "config.json").exists(),
                    "created_at": meta.get("created_at"),
                    "build_seconds": meta.get("build_seconds"),
                    "limit": meta.get("limit"),
                    "device": meta.get("device"),
                    "index_backend": meta.get("index_backend"),
                }
            )
        return info

    def chunks(self) -> list[dict]:
        """返回已加载的 chunk 列表（只读；供工具层做符号检索）。"""
        self._ensure_loaded()
        return self._chunks

    # -- 检索 -------------------------------------------------------------
    def search(self, query: str, k: int = config.DEFAULT_TOP_K, mode: str = "hybrid") -> list[dict]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("查询不能为空")
        query = query.strip()[:500]
        k = max(1, min(int(k), config.MAX_TOP_K))
        mode = _normalize_mode(mode)
        self._ensure_loaded()

        enhanced, hits = translate_query(query)
        # dense 用纯英文术语（若有中文映射），BM25 用"英文术语加权 + 原 query"
        dense_query = " ".join(en for _zh, en in hits) if hits else query
        bm25_query = enhanced
        if hits:
            en_terms = " ".join(en for _zh, en in hits)
            bm25_query = f"{en_terms} {en_terms} {query}"
        t_start = time.time()

        dense_ranked: list[tuple[int, float]] = []
        bm25_ranked: list[tuple[int, float]] = []
        if "dense" in mode or mode == "hybrid" or "rerank" in mode:
            if self._encoder is None:
                self._encoder = Encoder(device=self.device)
            qvec = self._encoder.encode_query(dense_query).reshape(1, -1).astype(np.float32)
            top = min(config.DENSE_TOP_K, self._faiss.ntotal)
            dist, ids = self._faiss.search(qvec, top)
            dense_ranked = [
                (int(i), float(d)) for i, d in zip(ids[0], dist[0]) if i >= 0
            ]
        if "bm25" in mode or mode == "hybrid" or "rerank" in mode:
            bm25_ranked = self._bm25.search(bm25_query, config.BM25_TOP_K)

        # 融合
        detail: dict[int, dict] = {}
        if mode == "dense":
            fused = [(i, s, {"dense": s, "bm25": None, "rerank": None}) for i, s in dense_ranked]
        elif mode == "bm25":
            fused = [(i, s, {"dense": None, "bm25": s, "rerank": None}) for i, s in bm25_ranked]
        else:
            rrf: dict[int, float] = {}
            rrf_k = config.RRF_K
            dw, bw = config.DENSE_WEIGHT, config.BM25_WEIGHT
            for rank, (i, s) in enumerate(dense_ranked):
                rrf[i] = rrf.get(i, 0.0) + dw / (rrf_k + rank + 1)
                detail.setdefault(i, {})["dense"] = s
            for rank, (i, s) in enumerate(bm25_ranked):
                rrf[i] = rrf.get(i, 0.0) + bw / (rrf_k + rank + 1)
                detail.setdefault(i, {})["bm25"] = s
            fused = [
                (i, sc, {"dense": detail.get(i, {}).get("dense"),
                         "bm25": detail.get(i, {}).get("bm25"),
                         "rerank": None})
                for i, sc in sorted(rrf.items(), key=lambda kv: kv[1], reverse=True)
            ]

        # 精排
        if "rerank" in mode:
            fused = self._apply_rerank(dense_query, fused, k)

        results: list[dict] = []
        for rank, (i, score, det) in enumerate(fused[:k], start=1):
            c = self._chunks[i]
            results.append(
                {
                    "rank": rank,
                    "score": float(score),
                    "chunk_id": c["chunk_id"],
                    "doc_id": c["doc_id"],
                    "doc_type": c["doc_type"],
                    "title": c["title"],
                    "breadcrumbs": c["breadcrumbs"],
                    "url": c["url"],
                    "anchor": c.get("anchor", ""),
                    "symbol": c.get("symbol", ""),
                    "text": c["text"],
                    "raw_text": c.get("raw_text", ""),
                    "score_detail": det,
                }
            )
        elapsed = (time.time() - t_start) * 1000
        print(f"[rag] query={query!r} mode={mode} k={k} {len(results)} results {elapsed:.0f}ms", file=sys.stderr)
        return results

    def _apply_rerank(self, query: str, fused: list, k: int) -> list:
        if self._reranker is None:
            self._reranker = Reranker(device=self.device)
        if not self._reranker.available():
            print("[rag] reranker 模型缺失，降级为 RRF 排序（运行 scripts/fetch_models.py --rerank 下载）", file=sys.stderr)
            return fused
        candidates = fused[: config.RERANK_TOP_K]
        passages = [self._chunks[i]["text"] for i, _s, _d in candidates]
        try:
            scores = self._reranker.score(query, passages)
        except Exception as e:  # 模型损坏/显存不足等，降级
            print(f"[rag] rerank 失败，降级为 RRF：{e}", file=sys.stderr)
            return fused
        reranked = []
        for (i, rrf_score, det), rs in zip(candidates, scores):
            nd = dict(det)
            nd["rerank"] = float(rs)
            reranked.append((i, float(rs), nd))
        reranked.sort(key=lambda x: x[1], reverse=True)
        # 未进入重排的尾部保持原顺序
        tail = [(i, s, d) for i, s, d in fused[config.RERANK_TOP_K :]]
        return reranked + tail

    # -- 问答 -------------------------------------------------------------
    def ask(
        self,
        query: str,
        k: int = config.DEFAULT_TOP_K,
        mode: str = "hybrid",
        lang: str | None = None,
    ) -> dict:
        passages = self.search(query, k=k, mode=mode)
        try:
            from .generate import answer as generate_answer
        except ImportError:  # 生成层尚未就绪时给出可用的降级
            generate_answer = None
        if generate_answer is not None:
            res = generate_answer(query, passages, lang=lang)
        else:
            first = passages[0]["text"].split("\n")
            summary = next((ln for ln in first if ln.strip()), "")
            res = {
                "answer": "（检索式回答：以下依据检索到的官方文档片段，未使用生成模型）\n" + summary,
                "llm_used": False,
            }
        citations = [
            {"n": i + 1, "title": p["title"], "url": p["url"], "symbol": p.get("symbol", "")}
            for i, p in enumerate(passages)
        ]
        return {
            "query": query,
            "mode": mode,
            "answer": res.get("answer", ""),
            "passages": passages,
            "citations": citations,
            "llm_used": bool(res.get("llm_used", False)),
        }

    def get_doc(self, doc_id: str) -> dict | None:
        self._ensure_loaded()
        doc = self._docs.get(doc_id)
        return doc.to_dict() if doc else None
