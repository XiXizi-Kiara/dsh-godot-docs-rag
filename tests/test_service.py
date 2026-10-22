"""service.py 集成测试：在随机端口启动真实 HTTP 服务，注入 fake RAG / 文档。"""
from __future__ import annotations

import json
import os
import pickle
import shutil
import sys
import time
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from godot_rag import service  # noqa: E402

SEARCH_RESULT = {
    "rank": 1,
    "score": 0.87,
    "chunk_id": "c1",
    "doc_id": "classes/class_node",
    "doc_type": "class",
    "title": "Node",
    "breadcrumbs": ["Godot Docs", "Class reference", "Node"],
    "url": "classes/class_node.html#class-node-method-add-child",
    "symbol": "Node.add_child()",
    "text": "Node.add_child() adds a child node to the scene tree.",
    "score_detail": {"dense": 0.7, "bm25": 2.1, "rerank": None},
}

ASK_RESULT = {
    "query": "如何添加子节点",
    "mode": "hybrid",
    "answer": "根据 [1]，使用 add_child() 添加子节点。",
    "passages": [SEARCH_RESULT],
    "citations": [{"n": 1, "title": "Node", "url": "classes/class_node.html"}],
    "llm_used": False,
}

DOC_DICT = {
    "doc_id": "classes/class_node",
    "title": "Node",
    "url": "classes/class_node.html",
    "breadcrumbs": ["Godot Docs", "Class reference", "Node"],
    "sections": [
        {
            "title": "add_child()",
            "level": 3,
            "anchor": "class-node-method-add-child",
            "blocks": [
                {
                    "kind": "para",
                    "text": "Adds a child <node> & keeps it. <script>alert('x')</script>",
                },
                {"kind": "code", "text": "add_child(child)", "lang": "gdscript"},
            ],
        }
    ],
}


class _FakeRAG:
    def __init__(self, index_dir="data/index"):
        self.index_dir = str(index_dir)
        self.ready = True
        self.search_calls = []
        self.ask_calls = []

    def search(self, query, k=8, mode="hybrid"):
        self.search_calls.append({"query": query, "k": k, "mode": mode})
        return [dict(SEARCH_RESULT)]

    def ask(self, query, k=8, mode="hybrid", lang=None):
        self.ask_calls.append({"query": query, "k": k, "mode": mode, "lang": lang})
        result = dict(ASK_RESULT)
        result["query"] = query
        result["mode"] = mode
        return result

    def stats(self):
        return {"n_chunks": 3, "n_docs": 1, "dim": 384, "modes": list(service.VALID_MODES)}


def _docs_provider(doc_id):
    return DOC_DICT if doc_id == "classes/class_node" else None


# 沙箱内系统临时目录可能不可写；且 tempfile.mkdtemp 创建的目录在 Windows 沙箱里
# 可能无法再次进入，因此统一用 Path.mkdir 在项目 .tmp/ 下建目录。
_TMP_ROOT = Path(__file__).resolve().parents[1] / ".tmp"


def _make_temp_dir(prefix: str) -> Path:
    _TMP_ROOT.mkdir(parents=True, exist_ok=True)
    path = _TMP_ROOT / f"{prefix}{os.getpid()}-{time.time_ns()}"
    path.mkdir()
    return path


def _start_server(index_dir, rag_factory=None, docs_provider=None):
    httpd = service.create_server(
        host="127.0.0.1",
        port=0,
        index_dir=index_dir,
        rag_factory=rag_factory,
        docs_provider=docs_provider,
        quiet=True,
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def _stop_server(httpd, thread):
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def _request(base, path, method="GET", payload=None, raw=None):
    data = raw
    if data is None and payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(base + path, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read().decode("utf-8"), dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8"), dict(exc.headers)


class _ServerTestCase(unittest.TestCase):
    """带一个就绪索引（meta.json + fake RAG + fake 文档）的测试基类。"""

    def setUp(self):
        self.tmpdir = _make_temp_dir("godot-rag-service-")
        self.index_dir = self.tmpdir / "index"
        self.index_dir.mkdir()
        (self.index_dir / "meta.json").write_text(
            json.dumps({"n_chunks": 3, "n_docs": 1}), encoding="utf-8"
        )
        self.fake_rag = _FakeRAG(self.index_dir)
        self.httpd, self.thread = _start_server(
            self.index_dir,
            rag_factory=lambda index_dir: self.fake_rag,
            docs_provider=_docs_provider,
        )
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]

    def tearDown(self):
        _stop_server(self.httpd, self.thread)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def get(self, path):
        return _request(self.base, path)

    def post(self, path, payload=None, raw=None):
        return _request(self.base, path, method="POST", payload=payload, raw=raw)


class HealthAndStatsTest(_ServerTestCase):
    def test_health_ready(self):
        status, body, headers = self.get("/api/health")
        self.assertEqual(status, 200)
        self.assertIn("application/json", headers["Content-Type"])
        data = json.loads(body)
        self.assertTrue(data["ok"])
        self.assertTrue(data["ready"])
        self.assertEqual(data["n_chunks"], 3)

    def test_stats(self):
        status, body, _ = self.get("/api/stats")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["n_chunks"], 3)
        self.assertIn("hybrid", data["modes"])


class SearchApiTest(_ServerTestCase):
    def test_search_ok(self):
        status, body, _ = self.post(
            "/api/search", {"query": "add_child", "k": 5, "mode": "bm25"}
        )
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(len(data["results"]), 1)
        self.assertEqual(data["results"][0]["title"], "Node")
        self.assertEqual(self.fake_rag.search_calls[-1],
                         {"query": "add_child", "k": 5, "mode": "bm25"})

    def test_search_defaults(self):
        status, body, _ = self.post("/api/search", {"query": "add_child"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["results"][0]["rank"], 1)
        self.assertEqual(self.fake_rag.search_calls[-1],
                         {"query": "add_child", "k": 8, "mode": "hybrid"})

    def test_search_query_length_boundary(self):
        status, _, _ = self.post("/api/search", {"query": "x" * 500})
        self.assertEqual(status, 200)
        status, body, _ = self.post("/api/search", {"query": "x" * 501})
        self.assertEqual(status, 400)
        self.assertIn("query", json.loads(body)["error"])

    def test_search_k_boundaries(self):
        for k in (1, 20):
            with self.subTest(k=k):
                status, _, _ = self.post("/api/search", {"query": "node", "k": k})
                self.assertEqual(status, 200)
        for k in (0, 21):
            with self.subTest(k=k):
                status, _, _ = self.post("/api/search", {"query": "node", "k": k})
                self.assertEqual(status, 400)

    def test_search_validation_errors(self):
        cases = [
            ("非法 JSON", dict(raw=b"{not json")),
            ("空 body", dict(raw=b"")),
            ("非对象 body", dict(payload=[1, 2, 3])),
            ("缺少 query", dict(payload={"k": 8})),
            ("空 query", dict(payload={"query": ""})),
            ("空白 query", dict(payload={"query": "   "})),
            ("query 非字符串", dict(payload={"query": 123})),
            ("k 非整数", dict(payload={"query": "node", "k": "8"})),
            ("k 为布尔", dict(payload={"query": "node", "k": True})),
            ("非法 mode", dict(payload={"query": "node", "mode": "magic"})),
        ]
        for name, kwargs in cases:
            with self.subTest(case=name):
                status, body, _ = self.post("/api/search", **kwargs)
                self.assertEqual(status, 400, body)
                self.assertIn("error", json.loads(body))


class AskApiTest(_ServerTestCase):
    def test_ask_ok(self):
        status, body, _ = self.post(
            "/api/ask", {"query": "如何添加子节点", "k": 4, "mode": "dense", "lang": "zh"}
        )
        self.assertEqual(status, 200)
        data = json.loads(body)
        for key in ("query", "mode", "answer", "passages", "citations", "llm_used"):
            self.assertIn(key, data)
        self.assertFalse(data["llm_used"])
        self.assertEqual(self.fake_rag.ask_calls[-1],
                         {"query": "如何添加子节点", "k": 4, "mode": "dense", "lang": "zh"})

    def test_ask_invalid_fields(self):
        status, _, _ = self.post("/api/ask", {"query": "node", "lang": 3})
        self.assertEqual(status, 400)
        status, _, _ = self.post("/api/ask", {"query": "node", "mode": "nope"})
        self.assertEqual(status, 400)


class DocApiTest(_ServerTestCase):
    def test_doc_ok_and_escaped(self):
        status, body, _ = self.get("/api/doc?doc_id=classes/class_node")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["doc_id"], "classes/class_node")
        self.assertEqual(data["title"], "Node")
        self.assertEqual(data["breadcrumbs"], ["Godot Docs", "Class reference", "Node"])
        html = data["html_text"]
        self.assertIn("add_child", html)
        self.assertIn("&lt;node&gt;", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>", html)
        self.assertIn("<pre><code", html)

    def test_doc_missing_parameter(self):
        status, body, _ = self.get("/api/doc")
        self.assertEqual(status, 400)
        self.assertIn("doc_id", json.loads(body)["error"])

    def test_doc_not_found(self):
        status, body, _ = self.get("/api/doc?doc_id=classes/class_unknown")
        self.assertEqual(status, 404)
        self.assertIn("未找到文档", json.loads(body)["error"])


class UiTest(_ServerTestCase):
    def test_index_page_has_ui_elements(self):
        status, body, headers = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn("charset=utf-8", headers["Content-Type"])
        for marker in (
            'id="query"', 'id="mode"', 'id="k"', 'id="results"', 'id="health"',
            'id="banner"', "查看整页原文", "来源链接", "hybrid+rerank",
            "/api/search", "/api/doc", "/api/health", "maxlength=\"500\"",
        ):
            self.assertIn(marker, body)
        # 无外链依赖：不能引用外部 JS/CSS
        self.assertNotIn("<script src", body)
        self.assertNotIn("<link ", body)
        self.assertIn("@media", body)  # 响应式

    def test_unknown_routes(self):
        status, body, _ = self.get("/api/nope")
        self.assertEqual(status, 404)
        self.assertIn("error", json.loads(body))
        status, _, _ = self.get("/nope")
        self.assertEqual(status, 404)
        status, _, _ = self.post("/api/nope", {})
        self.assertEqual(status, 404)
        status, _, _ = self.get("/api/search")
        self.assertEqual(status, 404)


class NoIndexTest(unittest.TestCase):
    """索引目录为空：health ready=false，检索返回 503。"""

    def setUp(self):
        self.tmpdir = _make_temp_dir("godot-rag-noidx-")
        self.httpd, self.thread = _start_server(self.tmpdir)
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]

    def tearDown(self):
        _stop_server(self.httpd, self.thread)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_health_not_ready(self):
        status, body, _ = _request(self.base, "/api/health")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])
        self.assertFalse(data["ready"])
        self.assertEqual(data["n_chunks"], 0)

    def test_search_returns_503(self):
        status, body, _ = _request(
            self.base, "/api/search", method="POST", payload={"query": "node"}
        )
        self.assertEqual(status, 503)
        self.assertIn("索引尚未构建", json.loads(body)["error"])

    def test_stats_returns_503(self):
        status, _, _ = _request(self.base, "/api/stats")
        self.assertEqual(status, 503)

    def test_doc_returns_503(self):
        status, _, _ = _request(self.base, "/api/doc?doc_id=classes/class_node")
        self.assertEqual(status, 503)

    def test_ui_still_served(self):
        status, body, _ = _request(self.base, "/")
        self.assertEqual(status, 200)
        self.assertIn("索引尚未构建", body)


class DocsPickleTest(unittest.TestCase):
    """不加 docs_provider 时，/api/doc 直接读 data/index/docs.pkl。"""

    def setUp(self):
        self.tmpdir = _make_temp_dir("godot-rag-docpickle-")
        (self.tmpdir / "meta.json").write_text(json.dumps({"n_chunks": 1}), encoding="utf-8")
        with (self.tmpdir / "docs.pkl").open("wb") as handle:
            pickle.dump({"classes/class_node": DOC_DICT}, handle)
        self.fake_rag = _FakeRAG(self.tmpdir)
        self.httpd, self.thread = _start_server(
            self.tmpdir, rag_factory=lambda index_dir: self.fake_rag
        )
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]

    def tearDown(self):
        _stop_server(self.httpd, self.thread)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_doc_from_pickle(self):
        status, body, _ = _request(self.base, "/api/doc?doc_id=classes/class_node")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["title"], "Node")
        self.assertIn("add_child", data["html_text"])


class UiRenderTest(unittest.TestCase):
    def test_docs_base_env_override(self):
        import os

        old = os.environ.get(service.DOCS_BASE_ENV)
        os.environ[service.DOCS_BASE_ENV] = "https://example.test/docs/"
        try:
            html = service.render_ui()
        finally:
            if old is None:
                os.environ.pop(service.DOCS_BASE_ENV, None)
            else:
                os.environ[service.DOCS_BASE_ENV] = old
        self.assertIn('"https://example.test/docs/"', html)
        self.assertNotIn("__DOCS_BASE__", html)


if __name__ == "__main__":
    unittest.main()
