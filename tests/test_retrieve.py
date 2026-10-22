"""检索层单元测试：用假索引验证 RRF/模式/错误处理，不加载真实模型。"""
from __future__ import annotations

import json
import pickle
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from godot_rag import config  # noqa: E402
from godot_rag.bm25 import BM25Index  # noqa: E402
from godot_rag.index import save_index  # noqa: E402
from godot_rag.retrieve import GodotRAG, IndexNotReady, _normalize_mode  # noqa: E402
from godot_rag.types import Document, Section  # noqa: E402


def _chunk(i: int, text: str) -> dict:
    return {
        "chunk_id": f"c{i:016d}",
        "doc_id": f"tutorials/doc{i}",
        "doc_type": "tutorial",
        "title": f"Doc {i}",
        "breadcrumbs": ["Godot Docs", "Tutorials", f"Doc {i}"],
        "url": f"tutorials/doc{i}.html",
        "anchor": "",
        "symbol": "",
        "text": text,
        "raw_text": text,
        "n_tokens": max(1, len(text) // 4),
    }


class FakeEncoder:
    """固定向量：query 与第 0 条最相似。"""

    device = "cpu"
    backend = "local_cpu"

    def __init__(self, *a, **kw):
        pass

    def encode_query(self, q):
        return self.vec

    def encode(self, texts, is_query=False, **kw):
        return np.tile(self.vec, (len(texts), 1)).astype(np.float32)


class TestRetrieve(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 沙箱下 tempfile 目录不可写，使用 workspace 内固定目录
        cls.index_dir = ROOT / ".tmp" / "tests" / "retrieve_index"
        cls.index_dir.mkdir(parents=True, exist_ok=True)
        texts = [
            "add_child adds a child node to the scene tree",
            "queue_free frees the node at the end of the frame",
            "signals connect callbacks",
        ]
        chunks = [_chunk(i, t) for i, t in enumerate(texts)]
        rng = np.random.default_rng(0)
        emb = rng.normal(size=(3, config.EMBED_DIM)).astype(np.float32)
        emb /= np.linalg.norm(emb, axis=1, keepdims=True)
        bm25 = BM25Index.build(texts)
        docs = [
            Document(
                doc_id=f"tutorials/doc{i}",
                doc_type="tutorial",
                title=f"Doc {i}",
                breadcrumbs=["Godot Docs", "Tutorials", f"Doc {i}"],
                url=f"tutorials/doc{i}.html",
                sections=[Section(title="s", level=1)],
            )
            for i in range(3)
        ]
        save_index(chunks, emb, bm25, docs, {"n_docs": 3, "dim": config.EMBED_DIM}, cls.index_dir)
        cls.emb = emb

    def _fake_encoder(self):
        enc = FakeEncoder()
        enc.vec = self.emb[0]  # 查询向量 = 第 0 条
        return enc

    def test_ready_stats(self):
        rag = GodotRAG(self.index_dir)
        self.assertTrue(rag.ready)
        st = rag.stats()
        self.assertEqual(st["n_chunks"], 3)
        self.assertEqual(st["n_docs"], 3)

    def test_hybrid_order_and_fields(self):
        rag = GodotRAG(self.index_dir)
        with patch("godot_rag.retrieve.Encoder", side_effect=lambda *a, **kw: self._fake_encoder()):
            res = rag.search("add_child", k=2, mode="hybrid")
        self.assertEqual(len(res), 2)
        self.assertEqual(res[0]["doc_id"], "tutorials/doc0")  # dense + bm25 双路命中
        for r in res:
            for key in ("rank", "score", "chunk_id", "doc_id", "title", "breadcrumbs", "url", "text"):
                self.assertIn(key, r)
            self.assertIn("score_detail", r)
        self.assertIsNotNone(res[0]["score_detail"]["dense"])
        self.assertIsNotNone(res[0]["score_detail"]["bm25"])

    def test_bm25_only(self):
        rag = GodotRAG(self.index_dir)
        with patch("godot_rag.retrieve.Encoder"):
            res = rag.search("queue_free", k=1, mode="bm25")
        self.assertEqual(res[0]["doc_id"], "tutorials/doc1")
        self.assertIsNone(res[0]["score_detail"]["dense"])

    def test_invalid_mode(self):
        rag = GodotRAG(self.index_dir)
        with self.assertRaises(ValueError):
            rag.search("x", mode="nope")
        self.assertEqual(_normalize_mode("Hybrid"), "hybrid")

    def test_empty_query(self):
        rag = GodotRAG(self.index_dir)
        with self.assertRaises(ValueError):
            rag.search("   ")

    def test_k_clamped(self):
        rag = GodotRAG(self.index_dir)
        with patch("godot_rag.retrieve.Encoder", side_effect=lambda *a, **kw: self._fake_encoder()):
            res = rag.search("add_child", k=99, mode="hybrid")
        self.assertLessEqual(len(res), config.MAX_TOP_K)

    def test_missing_index(self):
        rag = GodotRAG(index_dir=ROOT / ".tmp" / "tests" / "nope_index")
        self.assertFalse(rag.ready)
        with self.assertRaises(IndexNotReady):
            rag.search("x")

    def test_get_doc(self):
        rag = GodotRAG(self.index_dir)
        doc = rag.get_doc("tutorials/doc1")
        self.assertIsNotNone(doc)
        self.assertEqual(doc["title"], "Doc 1")
        self.assertIsNone(rag.get_doc("missing/doc"))


if __name__ == "__main__":
    unittest.main()
