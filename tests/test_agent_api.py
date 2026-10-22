"""agent_api 纯函数测试：不依赖网络 / GPU / 全量索引。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from godot_rag import agent_api  # noqa: E402


class FakeRAG:
    def search(self, query, k=5, mode="hybrid"):
        return [
            {
                "rank": 1, "score": 0.9, "chunk_id": "c1",
                "doc_id": "classes/class_node", "doc_type": "class",
                "title": "Node",
                "breadcrumbs": ["Godot Docs", "Class reference", "Node"],
                "url": "classes/class_node.html",
                "anchor": "class-node-method-add-child",
                "symbol": "Node.add_child",
                "text": "Godot Docs > Class reference > Node\nadd_child …",
                "raw_text": "Adds a child node. " * 100,
            }
        ][:k]

    def get_doc(self, doc_id):
        if doc_id != "classes/class_node":
            return None
        return {
            "doc_id": doc_id, "title": "Node",
            "url": "classes/class_node.html",
            "breadcrumbs": ["Godot Docs", "Class reference", "Node"],
            "html_text": "<article><h2>Node</h2><p>Base class.</p></article>",
        }

    def stats(self):
        return {"n_chunks": 26076, "n_docs": 1596, "device": "cuda", "ready": True}


class FakeChunkRAG:
    def __init__(self):
        self._chunks = [
            {"chunk_id": "c1", "doc_id": "classes/class_node", "doc_type": "class",
             "title": "Node", "breadcrumbs": ["Godot Docs", "Class reference", "Node"],
             "url": "classes/class_node.html", "anchor": "class-node-method-add-child",
             "symbol": "Node.add_child", "raw_text": "Adds a child node. " * 30},
            {"chunk_id": "c2", "doc_id": "classes/class_treeitem", "doc_type": "class",
             "title": "TreeItem", "breadcrumbs": ["Godot Docs", "Class reference", "TreeItem"],
             "url": "classes/class_treeitem.html", "anchor": "class-treeitem-method-add-child",
             "symbol": "TreeItem.add_child", "raw_text": "Adds a child TreeItem."},
            {"chunk_id": "c3", "doc_id": "classes/class_node", "doc_type": "class",
             "title": "Node", "breadcrumbs": ["Godot Docs", "Class reference", "Node"],
             "url": "classes/class_node.html", "anchor": "class-node-signal-child-entered-tree",
             "symbol": "Node.child_entered_tree", "raw_text": "Emitted when a child enters."},
        ]

    def chunks(self):
        return self._chunks


class TestAgentApi(unittest.TestCase):
    def test_search_payload_shape(self):
        payload = agent_api.search_payload(FakeRAG(), "add_child", k=3)
        self.assertEqual(payload["query"], "add_child")
        self.assertEqual(payload["mode"], "hybrid")
        r = payload["results"][0]
        self.assertEqual(r["symbol"], "Node.add_child")
        self.assertTrue(r["url"].startswith("https://docs.godotengine.org/en/stable/"))
        self.assertLessEqual(len(r["text"]), 701)

    def test_get_doc_payload_offset_and_truncation(self):
        payload = agent_api.get_doc_payload(FakeRAG(), "classes/class_node", offset=0, limit=12)
        self.assertEqual(payload["offset"], 0)
        self.assertEqual(len(payload["text"]), 12)
        self.assertTrue(payload["truncated"])
        self.assertGreater(payload["total_chars"], 12)

    def test_get_doc_missing_raises_bad_params(self):
        with self.assertRaises(agent_api.ApiError) as ctx:
            agent_api.get_doc_payload(FakeRAG(), "nope")
        self.assertEqual(ctx.exception.code, "BAD_PARAMS")

    def test_stats_payload_adds_index_mtime(self):
        payload = agent_api.stats_payload(FakeRAG(), ROOT / ".tmp" / "tests" / "no_such_index")
        self.assertEqual(payload["n_chunks"], 26076)
        self.assertIn("index_mtime", payload)
        self.assertIsNone(payload["index_mtime"])

    def test_error_payload_index_not_ready(self):
        from godot_rag.retrieve import IndexNotReady
        err = agent_api.error_payload(IndexNotReady("missing"))
        self.assertEqual(err["code"], "INDEX_NOT_READY")
        self.assertIn("索引尚未构建", err["message"])
        self.assertIn("scripts\\run.py build", err["hint"])

    def test_get_doc_from_sections(self):
        class SectionRAG:
            def get_doc(self, doc_id):
                return {
                    "doc_id": doc_id, "title": "Node",
                    "url": "classes/class_node.html", "breadcrumbs": [],
                    "sections": [{
                        "title": "Node", "level": 1,
                        "blocks": [
                            {"kind": "para", "text": "Base class for all scene objects."},
                            {"kind": "code", "text": "var n = Node.new()", "lang": "gdscript"},
                        ],
                    }],
                }

        payload = agent_api.get_doc_payload(SectionRAG(), "classes/class_node", limit=400)
        self.assertIn("Base class", payload["text"])
        self.assertIn("Node.new()", payload["text"])
        self.assertGreater(payload["total_chars"], 10)

    def test_error_payload_value_error_and_unknown(self):
        self.assertEqual(agent_api.error_payload(ValueError("bad")).get("code"), "BAD_PARAMS")
        self.assertEqual(agent_api.error_payload(RuntimeError("boom")).get("code"), "INTERNAL")

    def test_find_symbol_exact_and_class_first(self):
        rag = FakeChunkRAG()
        payload = agent_api.find_symbol_payload(rag, "Node.add_child")
        self.assertFalse(payload["approximate"])
        self.assertEqual(payload["matches"][0]["symbol"], "Node.add_child")
        self.assertEqual(payload["matches"][0]["kind"], "method")

    def test_find_symbol_bare_name_returns_classes_first(self):
        payload = agent_api.find_symbol_payload(FakeChunkRAG(), "add_child")
        symbols = [m["symbol"] for m in payload["matches"]]
        self.assertEqual(symbols[0], "Node.add_child")
        self.assertIn("TreeItem.add_child", symbols)

    def test_find_symbol_class_and_kind_filter(self):
        payload = agent_api.find_symbol_payload(FakeChunkRAG(), "add_child", class_name="TreeItem")
        self.assertEqual([m["symbol"] for m in payload["matches"]], ["TreeItem.add_child"])
        payload = agent_api.find_symbol_payload(FakeChunkRAG(), "add_child", kind="signal")
        self.assertEqual(payload["matches"], [])

    def test_find_symbol_empty_name_and_bad_kind(self):
        with self.assertRaises(agent_api.ApiError):
            agent_api.find_symbol_payload(FakeChunkRAG(), "   ")
        with self.assertRaises(agent_api.ApiError):
            agent_api.find_symbol_payload(FakeChunkRAG(), "add_child", kind="nope")

    def test_find_symbol_approximate(self):
        payload = agent_api.find_symbol_payload(FakeChunkRAG(), "add_chlid")
        self.assertTrue(payload["approximate"])
        self.assertTrue(payload["matches"])
        self.assertEqual(payload["matches"][0]["symbol"], "Node.add_child")


if __name__ == "__main__":
    unittest.main()
