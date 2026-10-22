"""MCP 工具层单元测试：不启动 server、不依赖网络/索引。"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from godot_rag import mcp_server  # noqa: E402


class FakeRAG:
    def search(self, query, k=5, mode="hybrid"):
        return [
            {
                "rank": 1,
                "score": 0.9,
                "chunk_id": "c1",
                "doc_id": "classes/class_node",
                "doc_type": "class",
                "title": "Node",
                "breadcrumbs": ["Godot Docs", "Class reference", "Node"],
                "url": "classes/class_node.html",
                "anchor": "class-node-method-add-child",
                "symbol": "Node.add_child",
                "text": "Godot Docs > Class reference > Node\nadd_child adds a child.",
                "raw_text": "add_child adds a child to the scene tree.",
            }
        ][:k]

    def ask(self, query, k=6, mode="hybrid"):
        return {
            "query": query,
            "mode": mode,
            "answer": "使用 add_child() 添加子节点。",
            "passages": [],
            "citations": [
                {"n": 1, "title": "Node", "url": "classes/class_node.html",
                 "symbol": "Node.add_child"},
            ],
            "llm_used": False,
        }

    def get_doc(self, doc_id):
        if doc_id == "classes/class_node":
            return {
                "doc_id": doc_id,
                "title": "Node",
                "url": "classes/class_node.html",
                "breadcrumbs": ["Godot Docs", "Class reference", "Node"],
                "html_text": "<article><h2>Node</h2><p>Base class for all scene objects.</p></article>",
            }
        return None

    def stats(self):
        return {"n_chunks": 26076, "n_docs": 1596, "device": "cuda"}


class TestMcpTools(unittest.TestCase):
    def setUp(self):
        self._saved_rag = mcp_server._RAG
        self._saved_index = mcp_server._INDEX_DIR
        mcp_server._RAG = FakeRAG()

    def tearDown(self):
        mcp_server._RAG = self._saved_rag
        mcp_server._INDEX_DIR = self._saved_index

    def test_search_returns_structured_json(self):
        payload = json.loads(mcp_server._search_impl("add_child", k=5))
        self.assertEqual(payload["query"], "add_child")
        self.assertEqual(len(payload["results"]), 1)
        r = payload["results"][0]
        self.assertEqual(r["title"], "Node")
        self.assertEqual(r["symbol"], "Node.add_child")
        self.assertTrue(r["url"].startswith("https://docs.godotengine.org/"))
        self.assertIn("add_child", r["text"])

    def test_search_without_index_returns_actionable_error(self):
        mcp_server._RAG = None
        mcp_server._INDEX_DIR = str(ROOT / ".tmp" / "tests" / "mcp_no_index")
        out = mcp_server._search_impl("add_child")
        self.assertIn("索引尚未构建", out)
        self.assertIn("scripts\\run.py build", out)

    def test_ask_returns_answer_with_citations(self):
        out = mcp_server._ask_impl("如何添加子节点？", k=3)
        self.assertIn("使用 add_child() 添加子节点。", out)
        self.assertIn("引用：", out)
        self.assertIn("[1]", out)
        self.assertIn("https://docs.godotengine.org/en/stable/classes/class_node.html", out)

    def test_get_doc_strips_html_and_truncates(self):
        out = mcp_server._doc_impl("classes/class_node", max_chars=200)
        self.assertIn("# Node", out)
        self.assertIn("doc_id: classes/class_node", out)
        self.assertIn("Base class for all scene objects.", out)
        self.assertNotIn("<p>", out)

        truncated = mcp_server._doc_impl("classes/class_node", max_chars=20)
        self.assertIn("已截断", truncated)

    def test_get_doc_missing_returns_hint(self):
        out = mcp_server._doc_impl("missing/doc")
        self.assertIn("未找到文档", out)
        self.assertIn("search_docs", out)

    def test_stats_json(self):
        payload = json.loads(mcp_server._stats_impl())
        self.assertEqual(payload["n_chunks"], 26076)


if __name__ == "__main__":
    unittest.main()
