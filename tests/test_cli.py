"""cli.py 单元测试：用 fake 索引目录 + 注入的假 RAG / build / eval，不加载模型。"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import time
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from godot_rag import cli  # noqa: E402

SEARCH_RESULTS = [
    {
        "rank": 1,
        "score": 0.9123,
        "chunk_id": "c1",
        "doc_id": "classes/class_node",
        "doc_type": "class",
        "title": "Node",
        "breadcrumbs": ["Godot Docs", "Class reference", "Node"],
        "url": "classes/class_node.html#class-node-method-add-child",
        "symbol": "Node.add_child()",
        "text": "Node.add_child() adds a child node to the scene tree.",
        "score_detail": {"dense": 0.8, "bm25": 3.2, "rerank": None},
    }
]

ASK_RESULT = {
    "query": "如何添加子节点",
    "mode": "hybrid",
    "answer": "根据 [1]，使用 add_child() 添加子节点。",
    "passages": SEARCH_RESULTS,
    "citations": [{"n": 1, "title": "Node", "url": "classes/class_node.html"}],
    "llm_used": False,
}

STATS = {
    "n_chunks": 2,
    "n_docs": 1,
    "modes": ["hybrid", "dense", "bm25"],
    "dim": 384,
}

# 沙箱内系统临时目录可能不可写；且 tempfile.mkdtemp 创建的目录在 Windows 沙箱里
# 可能无法再次进入，因此统一用 Path.mkdir 在项目 .tmp/ 下建目录。
_TMP_ROOT = Path(__file__).resolve().parents[1] / ".tmp"


def _make_temp_dir(prefix: str) -> Path:
    _TMP_ROOT.mkdir(parents=True, exist_ok=True)
    path = _TMP_ROOT / f"{prefix}{os.getpid()}-{time.time_ns()}"
    path.mkdir()
    return path


class _FakeRAG:
    """可配置的 GodotRAG 替身；调用记录挂在类属性上，方便断言。"""

    ready = True
    results = SEARCH_RESULTS
    ask_result = ASK_RESULT
    stats_data = STATS
    last_init = None
    last_search = None
    last_ask = None

    def __init__(self, index_dir="data/index"):
        type(self).last_init = index_dir
        self.index_dir = index_dir

    def search(self, query, k=8, mode="hybrid"):
        type(self).last_search = {"query": query, "k": k, "mode": mode}
        return list(type(self).results)

    def ask(self, query, k=8, mode="hybrid", lang=None):
        type(self).last_ask = {"query": query, "k": k, "mode": mode, "lang": lang}
        return dict(type(self).ask_result)

    def stats(self):
        return dict(type(self).stats_data)


class _FailingSearchRAG(_FakeRAG):
    def search(self, query, k=8, mode="hybrid"):
        raise RuntimeError("模拟检索崩溃")


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = _make_temp_dir("godot-rag-cli-")
        self.index_dir = self.tmpdir / "index"
        self.index_dir.mkdir()
        (self.index_dir / "meta.json").write_text(
            json.dumps({"n_chunks": 2, "n_docs": 1}), encoding="utf-8"
        )
        self.empty_dir = self.tmpdir / "empty"
        self.empty_dir.mkdir()

        self._saved = {
            "rag": cli._RAG_CLASS,
            "build": cli._BUILD_INDEX,
            "eval": cli._EVAL_MAIN,
        }
        cli._RAG_CLASS = _FakeRAG
        self.build_calls = []

        def fake_build(limit=None, force=False):
            self.build_calls.append({"limit": limit, "force": force})
            return {"n_chunks": 40, "n_docs": 3, "index_dir": "data/index_smoke"}

        cli._BUILD_INDEX = fake_build
        cli._EVAL_MAIN = None

        _FakeRAG.ready = True
        _FakeRAG.results = SEARCH_RESULTS
        _FakeRAG.ask_result = ASK_RESULT
        _FakeRAG.stats_data = STATS
        _FakeRAG.last_init = None
        _FakeRAG.last_search = None
        _FakeRAG.last_ask = None

    def tearDown(self):
        cli._RAG_CLASS = self._saved["rag"]
        cli._BUILD_INDEX = self._saved["build"]
        cli._EVAL_MAIN = self._saved["eval"]
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ---- 工具 ----
    def run_cli(self, argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    # ---- search ----
    def test_search_json_outputs_valid_json_only(self):
        code, stdout, _ = self.run_cli(
            ["search", "add_child", "--json", "--index-dir", str(self.index_dir)]
        )
        self.assertEqual(code, 0)
        payload = json.loads(stdout)  # stdout 必须只有 JSON
        self.assertEqual(payload["query"], "add_child")
        self.assertEqual(payload["mode"], "hybrid")
        self.assertEqual(payload["k"], 8)
        self.assertIsInstance(payload["elapsed_ms"], (int, float))
        self.assertEqual(payload["results"][0]["title"], "Node")
        self.assertEqual(_FakeRAG.last_search, {"query": "add_child", "k": 8, "mode": "hybrid"})
        self.assertEqual(_FakeRAG.last_init, str(self.index_dir))

    def test_search_k_and_mode_passthrough(self):
        code, stdout, _ = self.run_cli(
            ["search", "node", "--k", "3", "--mode", "bm25", "--json",
             "--index-dir", str(self.index_dir)]
        )
        self.assertEqual(code, 0)
        payload = json.loads(stdout)
        self.assertEqual(payload["k"], 3)
        self.assertEqual(payload["mode"], "bm25")
        self.assertEqual(_FakeRAG.last_search["k"], 3)
        self.assertEqual(_FakeRAG.last_search["mode"], "bm25")

    def test_search_human_output_is_readable(self):
        code, stdout, _ = self.run_cli(
            ["search", "add_child", "--index-dir", str(self.index_dir)]
        )
        self.assertEqual(code, 0)
        self.assertIn("查询：add_child", stdout)
        self.assertIn("[1]", stdout)
        self.assertIn("Node.add_child()", stdout)
        self.assertIn("Godot Docs / Class reference / Node", stdout)
        self.assertIn("链接：classes/class_node.html", stdout)

    def test_search_without_index_returns_exit_code_2(self):
        code, stdout, stderr = self.run_cli(
            ["search", "node", "--json", "--index-dir", str(self.empty_dir)]
        )
        self.assertEqual(code, 2)
        self.assertIn("索引不存在", stderr)
        self.assertIn("python -m godot_rag build", stderr)
        payload = json.loads(stdout)
        self.assertIn("索引不存在", payload["error"])
        self.assertIsNone(_FakeRAG.last_init)  # 不应构造 RAG

    def test_search_json_tolerates_numpy_scalars(self):
        class _Scalar:
            """模拟 numpy.float32：numpy 标量不能被 json 直接序列化。"""

            def item(self):
                return 0.5

        _FakeRAG.results = [dict(SEARCH_RESULTS[0], score=_Scalar())]
        code, stdout, _ = self.run_cli(
            ["search", "node", "--json", "--index-dir", str(self.index_dir)]
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["results"][0]["score"], 0.5)

    def test_search_runtime_error_no_traceback(self):
        cli._RAG_CLASS = _FailingSearchRAG
        code, stdout, stderr = self.run_cli(
            ["search", "node", "--json", "--index-dir", str(self.index_dir)]
        )
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", stderr)
        self.assertIn("检索失败", stderr)
        self.assertIn("检索失败", json.loads(stdout)["error"])

    def test_search_ready_false_returns_exit_code_2(self):
        _FakeRAG.ready = False
        code, _, stderr = self.run_cli(
            ["search", "node", "--index-dir", str(self.index_dir)]
        )
        self.assertEqual(code, 2)
        self.assertIn("索引尚未就绪", stderr)

    def test_bad_mode_is_rejected_by_argparse(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                cli.main(["search", "node", "--mode", "not-a-mode"])
        self.assertEqual(caught.exception.code, 2)

    # ---- ask ----
    def test_ask_json_matches_ask_result(self):
        code, stdout, _ = self.run_cli(
            ["ask", "如何添加子节点", "--json", "--index-dir", str(self.index_dir)]
        )
        self.assertEqual(code, 0)
        payload = json.loads(stdout)
        self.assertEqual(payload["answer"], ASK_RESULT["answer"])
        self.assertFalse(payload["llm_used"])
        self.assertEqual(payload["citations"][0]["n"], 1)
        self.assertEqual(_FakeRAG.last_ask["query"], "如何添加子节点")

    # ---- build ----
    def test_build_passes_limit_and_force(self):
        code, stdout, stderr = self.run_cli(["build", "--limit", "3", "--force"])
        self.assertEqual(code, 0)
        self.assertEqual(self.build_calls, [{"limit": 3, "force": True}])
        self.assertIn("构建完成", stdout)
        self.assertIn("片段数: 40", stdout)
        self.assertIn("开始构建索引", stderr)

    def test_build_json_outputs_stats(self):
        code, stdout, _ = self.run_cli(["build", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(stdout)
        self.assertEqual(payload["n_chunks"], 40)
        self.assertEqual(self.build_calls, [{"limit": None, "force": False}])

    # ---- stats ----
    def test_stats_json(self):
        code, stdout, _ = self.run_cli(
            ["stats", "--json", "--index-dir", str(self.index_dir)]
        )
        self.assertEqual(code, 0)
        payload = json.loads(stdout)
        self.assertEqual(payload["n_chunks"], 2)
        self.assertEqual(payload["dim"], 384)

    def test_stats_human_labels(self):
        code, stdout, _ = self.run_cli(["stats", "--index-dir", str(self.index_dir)])
        self.assertEqual(code, 0)
        self.assertIn("片段数: 2", stdout)
        self.assertIn("支持模式", stdout)

    # ---- eval ----
    def test_eval_forwards_arguments(self):
        recorded = []

        def fake_eval(argv):
            recorded.append(argv)
            return 0

        cli._EVAL_MAIN = fake_eval
        code, _, _ = self.run_cli(
            ["eval", "--configs", "hybrid,hybrid+rerank", "--json"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(recorded, [["--configs", "hybrid,hybrid+rerank", "--json"]])

    def test_eval_return_code_is_propagated(self):
        cli._EVAL_MAIN = lambda argv: 3
        code, _, _ = self.run_cli(["eval"])
        self.assertEqual(code, 3)


if __name__ == "__main__":
    unittest.main()
