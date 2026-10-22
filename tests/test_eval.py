"""eval.py 的单元测试：不依赖 GPU、不依赖真实索引。

用 fake RAG 验证 Recall / MRR / PageHit 的手算结果、按语言/类型分组、
失败案例、报告文件结构与错误码。运行（项目根目录）：

    set PYTHONPATH=src
    python -m unittest tests.test_eval -v
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path

# 直接按文件路径加载 eval.py，避免依赖 godot_rag 包的 __init__.py
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_EVAL_PATH = _PROJECT_ROOT / "src" / "godot_rag" / "eval.py"
# 临时文件统一放在工作区内的 .tmp/ 下（系统临时目录可能被沙箱限制为只读）
_TMP_ROOT = _PROJECT_ROOT / ".tmp"


class WorkspaceTempDir:
    """位于工作区 .tmp/ 下的临时目录（避免系统临时目录与 700 权限问题）。"""

    def __init__(self) -> None:
        _TMP_ROOT.mkdir(parents=True, exist_ok=True)
        self.name = str(_TMP_ROOT / f"eval_test_{uuid.uuid4().hex[:10]}")
        Path(self.name).mkdir(parents=True, exist_ok=True)

    def cleanup(self) -> None:
        shutil.rmtree(self.name, ignore_errors=True)

    def __enter__(self) -> str:
        return self.name

    def __exit__(self, *exc_info) -> None:
        self.cleanup()


def workspace_tempdir() -> WorkspaceTempDir:
    """创建位于工作区 .tmp/ 下的临时目录。"""
    return WorkspaceTempDir()


_spec = importlib.util.spec_from_file_location("godot_rag_eval_under_test", _EVAL_PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover - 环境异常
    raise RuntimeError(f"无法加载 {_EVAL_PATH}")
eval_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eval_mod)


def make_result(doc_id: str, text: str = "", score: float = 0.0) -> dict:
    """构造一条 SearchResult（只保留 eval 用到的字段）。"""
    return {"rank": 0, "score": score, "doc_id": doc_id, "text": text}


def make_qa(qid: str, query: str, gold: list[str], terms: list[str], lang: str = "en",
            doc_type: str = "class") -> dict:
    """构造一条 QA。"""
    return {
        "qid": qid,
        "query": query,
        "lang": lang,
        "doc_type": doc_type,
        "gold_doc_ids": gold,
        "gold_terms": terms,
        "note": "",
    }


class FakeRAG:
    """按 query 返回固定结果的假检索器，并记录调用参数。"""

    def __init__(self, script: dict[str, list[dict]]):
        self.script = script
        self.calls: list[tuple[str, int, str]] = []

    def search(self, query: str, k: int = 10, mode: str = "hybrid") -> list[dict]:
        self.calls.append((query, k, mode))
        return self.script.get(query, [])[:k]


# ---------------------------------------------------------------------------
# 手算用例（3 题）
# ---------------------------------------------------------------------------

class HandComputedMetricsTest(unittest.TestCase):
    """用 3 条 QA 手算 Recall/MRR/PageHit，逐项断言。"""

    def setUp(self):
        self.qa = [
            make_qa("q1", "query one", ["doc_a"], ["term_a"]),
            make_qa("q2", "query two", ["doc_d"], ["term_d"]),
            # q3：doc 完全不命中，但 rank2 的正文含 gold_term（大小写不同）
            make_qa("q3", "query three", ["doc_e"], ["needle"], lang="zh", doc_type="tutorial"),
        ]
        self.rag = FakeRAG(
            {
                "query one": [make_result("doc_a"), make_result("doc_b"), make_result("doc_c")],
                "query two": [make_result("doc_b"), make_result("doc_c"), make_result("doc_d")],
                "query three": [
                    make_result("doc_b"),
                    make_result("doc_x", text="This page mentions the NEEDLE."),
                ],
            }
        )
        self.report = eval_mod.evaluate(self.rag, self.qa, ["dense"])
        self.rows = self.report["results"]["dense"]["per_query"]

    def test_per_query_metrics(self):
        q1, q2, q3 = self.rows
        # q1：rank1 命中
        self.assertEqual((q1["recall@1"], q1["recall@5"], q1["recall@10"]), (1.0, 1.0, 1.0))
        self.assertAlmostEqual(q1["mrr@10"], 1.0)
        self.assertEqual(q1["pagehit@5"], 1.0)
        # q2：rank3 命中
        self.assertEqual((q2["recall@1"], q2["recall@5"], q2["recall@10"]), (0.0, 1.0, 1.0))
        self.assertAlmostEqual(q2["mrr@10"], 1.0 / 3.0)
        self.assertEqual(q2["first_relevant_rank"], 3)
        self.assertEqual(q2["pagehit@5"], 1.0)
        # q3：Recall 全 0，但 PageHit 由 gold_term 兜底命中
        self.assertEqual((q3["recall@1"], q3["recall@5"], q3["recall@10"]), (0.0, 0.0, 0.0))
        self.assertEqual(q3["mrr@10"], 0.0)
        self.assertEqual(q3["pagehit@5"], 1.0)

    def test_overall_aggregate(self):
        overall = self.report["results"]["dense"]["overall"]
        self.assertEqual(overall["n_queries"], 3)
        self.assertAlmostEqual(overall["recall@1"], 1.0 / 3.0, places=4)
        self.assertAlmostEqual(overall["recall@5"], 2.0 / 3.0, places=4)
        self.assertAlmostEqual(overall["recall@10"], 2.0 / 3.0, places=4)
        self.assertAlmostEqual(overall["mrr@10"], (1.0 + 1.0 / 3.0) / 3.0, places=4)
        self.assertAlmostEqual(overall["pagehit@5"], 1.0, places=4)

    def test_group_by_lang(self):
        by_lang = self.report["results"]["dense"]["by_lang"]
        self.assertEqual(set(by_lang), {"en", "zh"})
        self.assertEqual(by_lang["en"]["n_queries"], 2)
        self.assertAlmostEqual(by_lang["en"]["recall@1"], 0.5, places=4)
        self.assertAlmostEqual(by_lang["en"]["recall@5"], 1.0, places=4)
        self.assertAlmostEqual(by_lang["en"]["mrr@10"], (1.0 + 1.0 / 3.0) / 2.0, places=4)
        self.assertEqual(by_lang["zh"]["n_queries"], 1)
        self.assertAlmostEqual(by_lang["zh"]["recall@5"], 0.0, places=4)
        self.assertAlmostEqual(by_lang["zh"]["pagehit@5"], 1.0, places=4)

    def test_group_by_doc_type(self):
        by_type = self.report["results"]["dense"]["by_doc_type"]
        self.assertEqual(set(by_type), {"class", "tutorial"})
        self.assertEqual(by_type["class"]["n_queries"], 2)
        self.assertEqual(by_type["tutorial"]["n_queries"], 1)

    def test_search_called_with_top10(self):
        self.assertTrue(self.rag.calls)
        for _query, k, mode in self.rag.calls:
            self.assertEqual(k, 10)
            self.assertEqual(mode, "dense")


# ---------------------------------------------------------------------------
# 边界与失败案例
# ---------------------------------------------------------------------------

class EdgeCaseTest(unittest.TestCase):
    def test_mrr_only_counts_top10(self):
        """gold 文档排在第 11 位时 MRR@10、Recall@10、PageHit@5 都为 0。"""
        qa = [make_qa("q1", "deep", ["doc_gold"], ["term"])]
        results = [make_result(f"doc_{i}") for i in range(10)] + [make_result("doc_gold")]
        report = eval_mod.evaluate(FakeRAG({"deep": results}), qa, ["hybrid"])
        row = report["results"]["hybrid"]["per_query"][0]
        self.assertEqual(row["mrr@10"], 0.0)
        self.assertEqual(row["recall@10"], 0.0)
        self.assertEqual(row["pagehit@5"], 0.0)
        self.assertIsNone(row["first_relevant_rank"])

    def test_pagehit_only_checks_top5(self):
        """gold_term 出现在第 6 条结果时不算 PageHit@5。"""
        qa = [make_qa("q1", "term-late", ["gold"], ["hidden_word"])]
        results = [make_result(f"doc_{i}") for i in range(5)]
        results.append(make_result("doc_5", text="contains HIDDEN_WORD here"))
        report = eval_mod.evaluate(FakeRAG({"term-late": results}), qa, ["bm25"])
        row = report["results"]["bm25"]["per_query"][0]
        self.assertEqual(row["pagehit@5"], 0.0)

    def test_empty_results_and_search_error(self):
        """空结果与检索异常都按未命中处理，不抛异常。"""
        qa = [make_qa("q1", "empty", ["doc_a"], ["t"]), make_qa("q2", "boom", ["doc_b"], ["t"])]

        class BrokenRAG:
            def search(self, query, k=10, mode="hybrid"):
                if query == "boom":
                    raise RuntimeError("模拟检索失败")
                return []

        report = eval_mod.evaluate(BrokenRAG(), qa, ["hybrid"])
        data = report["results"]["hybrid"]
        self.assertEqual(data["overall"]["n_queries"], 2)
        self.assertEqual(data["overall"]["recall@5"], 0.0)
        self.assertEqual(data["n_errors"], 1)
        self.assertEqual(len(data["failures"]), 2)
        self.assertIn("模拟检索失败", data["failures"][1]["error"])

    def test_failure_includes_top3(self):
        """失败案例必须给出 query 与 top-3 返回的 doc_id。"""
        qa = [make_qa("q1", "miss", ["doc_gold"], ["no_such_term"])]
        results = [make_result("doc_x"), make_result("doc_y"), make_result("doc_z")]
        report = eval_mod.evaluate(FakeRAG({"miss": results}), qa, ["hybrid"])
        failure = report["results"]["hybrid"]["failures"][0]
        self.assertEqual(failure["qid"], "q1")
        self.assertEqual(failure["query"], "miss")
        self.assertEqual([item["doc_id"] for item in failure["top3"]], ["doc_x", "doc_y", "doc_z"])

    def test_multiple_configs_are_reported(self):
        qa = [make_qa("q1", "q", ["doc_a"], ["term"])]
        rag = FakeRAG({"q": [make_result("doc_a")]})
        report = eval_mod.evaluate(rag, qa, ["dense", "bm25"])
        self.assertEqual(set(report["results"]), {"dense", "bm25"})
        self.assertEqual({mode for _q, _k, mode in rag.calls}, {"dense", "bm25"})
        self.assertEqual(report["configs"], ["dense", "bm25"])

    def test_empty_configs_raise(self):
        with self.assertRaises(eval_mod.EvalError):
            eval_mod.evaluate(FakeRAG({}), [], [])


# ---------------------------------------------------------------------------
# load_qa
# ---------------------------------------------------------------------------

class LoadQaTest(unittest.TestCase):
    def test_loads_valid_jsonl(self):
        with workspace_tempdir() as tmp:
            path = Path(tmp) / "qa.jsonl"
            path.write_text(
                json.dumps(make_qa("q1", "hello", ["doc_a"], ["term"]), ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            qa = eval_mod.load_qa(path)
        self.assertEqual(len(qa), 1)
        self.assertEqual(qa[0]["qid"], "q1")

    def test_missing_file_raises(self):
        with self.assertRaises(eval_mod.EvalError):
            eval_mod.load_qa(_PROJECT_ROOT / ".tmp" / "definitely_missing_qa.jsonl")

    def test_bad_json_raises(self):
        with workspace_tempdir() as tmp:
            path = Path(tmp) / "qa.jsonl"
            path.write_text("{not json}\n", encoding="utf-8")
            with self.assertRaises(eval_mod.EvalError):
                eval_mod.load_qa(path)

    def test_missing_required_field_raises(self):
        with workspace_tempdir() as tmp:
            path = Path(tmp) / "qa.jsonl"
            item = make_qa("q1", "hello", ["doc_a"], ["term"])
            del item["gold_terms"]
            path.write_text(json.dumps(item) + "\n", encoding="utf-8")
            with self.assertRaises(eval_mod.EvalError):
                eval_mod.load_qa(path)

    def test_duplicate_qid_raises(self):
        with workspace_tempdir() as tmp:
            path = Path(tmp) / "qa.jsonl"
            item = make_qa("q1", "hello", ["doc_a"], ["term"])
            path.write_text(json.dumps(item) + "\n" + json.dumps(item) + "\n", encoding="utf-8")
            with self.assertRaises(eval_mod.EvalError):
                eval_mod.load_qa(path)


# ---------------------------------------------------------------------------
# 报告输出与 CLI 错误码
# ---------------------------------------------------------------------------

class ReportOutputTest(unittest.TestCase):
    def setUp(self):
        qa = [
            make_qa("q1", "hit", ["doc_a"], ["term_a"]),
            make_qa("q2", "miss", ["doc_b"], ["no_term"]),
        ]
        rag = FakeRAG(
            {
                "hit": [make_result("doc_a", text="term_a appears")],
                "miss": [make_result("doc_x"), make_result("doc_y")],
            }
        )
        self.report = eval_mod.evaluate(rag, qa, ["hybrid", "bm25"])
        self.tmp = workspace_tempdir()
        self.addCleanup(self.tmp.cleanup)

    def test_report_is_json_serializable_with_expected_keys(self):
        text = json.dumps(self.report, ensure_ascii=False)
        self.assertIn("recall@1", text)
        data = json.loads(text)
        for config in ("hybrid", "bm25"):
            block = data["results"][config]
            self.assertIn("overall", block)
            self.assertIn("by_lang", block)
            self.assertIn("by_doc_type", block)
            self.assertIn("failures", block)
            self.assertEqual(len(block["per_query"]), 2)
            for key in eval_mod.METRIC_KEYS:
                self.assertIn(key, block["overall"])
        self.assertAlmostEqual(data["results"]["hybrid"]["overall"]["recall@1"], 0.5, places=4)
        self.assertAlmostEqual(data["results"]["hybrid"]["overall"]["pagehit@5"], 0.5, places=4)

    def test_write_reports_creates_json_and_markdown(self):
        json_path, md_path = eval_mod.write_reports(self.report, self.tmp.name, stem="eval_test")
        self.assertTrue(json_path.is_file())
        self.assertTrue(md_path.is_file())
        self.assertEqual(json_path.name, "eval_test.json")
        markdown = md_path.read_text(encoding="utf-8")
        self.assertIn("# Godot RAG 检索评测报告", markdown)
        self.assertIn("Recall@5", markdown)
        self.assertIn("## 失败案例", markdown)
        self.assertIn("miss", markdown)  # 失败题的 query
        self.assertIn("doc_x", markdown)  # 失败题 top-3 的 doc_id
        saved = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["n_queries"], 2)


class MainErrorCodeTest(unittest.TestCase):
    """无 QA / 无索引时必须给出清晰错误并返回 2，且不抛裸异常。"""

    def _call_main(self, argv: list[str]) -> tuple[int, str]:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = eval_mod.main(argv)
        return code, stderr.getvalue()

    def test_missing_qa_returns_2(self):
        missing = _PROJECT_ROOT / ".tmp" / "no_such_qa_file.jsonl"
        code, err = self._call_main(["--qa", str(missing)])
        self.assertEqual(code, 2)
        self.assertIn("QA 文件不存在", err)

    def test_json_mode_error_stdout_is_valid_json(self):
        """--json 模式下即便失败，stdout 也必须是合法 JSON（错误对象）。"""
        missing = _PROJECT_ROOT / ".tmp" / "no_such_qa_file.jsonl"
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = eval_mod.main(["--qa", str(missing), "--json"])
        self.assertEqual(code, 2)
        payload = json.loads(stdout.getvalue())
        self.assertIn("error", payload)
        self.assertIn("QA 文件不存在", payload["error"])

    def test_missing_index_returns_2(self):
        with workspace_tempdir() as tmp:
            qa_path = Path(tmp) / "qa.jsonl"
            qa_path.write_text(
                json.dumps(make_qa("q1", "hello", ["doc_a"], ["term"])) + "\n",
                encoding="utf-8",
            )
            code, err = self._call_main(
                ["--qa", str(qa_path), "--index", str(Path(tmp) / "no_index")]
            )
        self.assertEqual(code, 2)
        self.assertIn("索引不存在", err)


class RealQaFileTest(unittest.TestCase):
    """若项目内已有正式评测集，验证 eval.load_qa 能直接读取。"""

    def test_project_qa_file(self):
        qa_path = _PROJECT_ROOT / "data" / "processed" / "qa.jsonl"
        if not qa_path.is_file():
            self.skipTest("尚未生成 data/processed/qa.jsonl")
        qa = eval_mod.load_qa(qa_path)
        self.assertEqual(len(qa), 40)
        self.assertTrue(all(item["gold_doc_ids"] for item in qa))


if __name__ == "__main__":
    sys.exit(unittest.main())
