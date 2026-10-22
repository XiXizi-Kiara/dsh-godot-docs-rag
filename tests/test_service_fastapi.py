"""FastAPI 服务层单元测试（TestClient，不加载真实模型、不联网）。"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from godot_rag.service_fastapi import create_app  # noqa: E402


class FakeRAG:
    ready = True

    def search(self, query, k=8, mode="hybrid"):
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
                "symbol": "Node.add_child",
                "text": "Godot Docs > Class reference > Node\nNode.add_child adds a child.",
            }
        ]

    def ask(self, query, k=8, mode="hybrid", lang=None):
        return {
            "query": query,
            "mode": mode,
            "answer": "使用 add_child()。",
            "passages": [],
            "citations": [],
            "llm_used": False,
        }

    def stats(self):
        return {"n_chunks": 3, "n_docs": 2, "dim": 384}

    def get_doc(self, doc_id):
        if doc_id == "classes/class_node":
            return {
                "doc_id": doc_id,
                "title": "Node",
                "url": "classes/class_node.html",
                "breadcrumbs": ["Godot Docs", "Class reference", "Node"],
                "html_text": "<article><h2>Node</h2></article>",
            }
        return None


def _make_index(has_meta: bool = True) -> Path:
    d = ROOT / ".tmp" / "tests" / ("fastapi_index" if has_meta else "fastapi_index_empty")
    d.mkdir(parents=True, exist_ok=True)
    meta = d / "meta.json"
    if has_meta:
        meta.write_text(json.dumps({"n_chunks": 3, "n_docs": 2}), encoding="utf-8")
    elif meta.exists():
        meta.unlink()
    return d


class TestFastAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index_dir = _make_index(True)
        fake = FakeRAG()
        cls.app = create_app(
            str(cls.index_dir),
            rag_factory=lambda d: fake,
            docs_provider=fake.get_doc,
        )
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    def test_health(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["ok"], True)
        self.assertEqual(body["ready"], True)
        self.assertEqual(body["n_chunks"], 3)

    def test_ui(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Godot", r.text)
        self.assertIn("text/html", r.headers["content-type"])

    def test_search_ok(self):
        r = self.client.post("/api/search", json={"query": "add_child", "k": 3})
        self.assertEqual(r.status_code, 200)
        results = r.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["doc_id"], "classes/class_node")

    def test_search_invalid(self):
        self.assertEqual(self.client.post("/api/search", json={}).status_code, 400)
        self.assertEqual(
            self.client.post("/api/search", json={"query": ""}).status_code, 400
        )
        self.assertEqual(
            self.client.post("/api/search", json={"query": "x", "k": 0}).status_code, 400
        )
        self.assertEqual(
            self.client.post("/api/search", json={"query": "x", "mode": "nope"}).status_code,
            400,
        )
        self.assertEqual(
            self.client.post("/api/search", content=b"not-json").status_code, 400
        )

    def test_ask_ok(self):
        r = self.client.post("/api/ask", json={"query": "add_child", "k": 3})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("answer", body)
        self.assertFalse(body["llm_used"])

    def test_doc_ok_and_404(self):
        r = self.client.get("/api/doc", params={"doc_id": "classes/class_node"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["title"], "Node")
        r = self.client.get("/api/doc", params={"doc_id": "missing/doc"})
        self.assertEqual(r.status_code, 404)
        r = self.client.get("/api/doc")
        self.assertEqual(r.status_code, 400)

    def test_not_ready_returns_503(self):
        empty_dir = _make_index(False)
        app = create_app(str(empty_dir), rag_factory=lambda d: FakeRAG())
        client = TestClient(app)
        try:
            r = client.post("/api/search", json={"query": "add_child"})
            self.assertEqual(r.status_code, 503)
            r = client.get("/api/stats")
            self.assertEqual(r.status_code, 503)
            r = client.get("/api/health")
            self.assertEqual(r.status_code, 200)
            self.assertFalse(r.json()["ready"])
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
