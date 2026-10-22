"""BM25 单元测试：分词、排序、持久化。"""
from __future__ import annotations

import sys
import tempfile
import unittest

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from godot_rag.bm25 import BM25Index, tokenize  # noqa: E402


class TestTokenize(unittest.TestCase):
    def test_keeps_snake_case_symbol(self):
        self.assertIn("add_child", tokenize("Call add_child() on the node"))

    def test_splits_camel_case(self):
        toks = tokenize("CharacterBody2D and AnimationPlayer")
        self.assertIn("characterbody2d", toks)
        self.assertIn("character", toks)
        self.assertIn("body", toks)
        self.assertIn("animationplayer", toks)

    def test_lowercase(self):
        self.assertIn("queue_free", tokenize("QUEUE_FREE"))

    def test_empty(self):
        self.assertEqual(tokenize(""), [])


class TestBM25(unittest.TestCase):
    def setUp(self):
        self.texts = [
            "add_child adds a child node to the scene tree",
            "queue_free destroys the node at the end of the frame",
            "signals connect a callback to an object",
            "CharacterBody2D move_and_slide physics movement",
        ]
        self.index = BM25Index.build(self.texts)

    def test_search_symbol(self):
        ranked = self.index.search("add_child", top_k=3)
        self.assertTrue(ranked)
        self.assertEqual(ranked[0][0], 0)

    def test_search_natural_language(self):
        ranked = self.index.search("delete node free", top_k=3)
        self.assertEqual(ranked[0][0], 1)

    def test_search_without_hits(self):
        self.assertEqual(self.index.search("zzzz_unlikely_token", top_k=3), [])

    def test_empty_query(self):
        self.assertEqual(self.index.search("", top_k=3), [])

    def test_save_load_roundtrip(self):
        d = ROOT / ".tmp" / "tests"
        d.mkdir(parents=True, exist_ok=True)
        path = str(d / "bm25_test.pkl")
        self.index.save(path)
        loaded = BM25Index.load(path)
        self.assertEqual(loaded.n_docs, self.index.n_docs)
        a = self.index.search("queue_free", top_k=2)
        b = loaded.search("queue_free", top_k=2)
        self.assertEqual([i for i, _ in a], [i for i, _ in b])


if __name__ == "__main__":
    unittest.main()
