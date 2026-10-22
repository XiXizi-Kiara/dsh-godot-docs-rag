"""analyze_usage 纯函数测试：fixture JSONL，不联网、不依赖索引。"""
from __future__ import annotations

import json
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import analyze_usage  # noqa: E402


def event(**overrides):
    base = {
        "v": 1, "kind": "tool_call", "ts": "2026-10-22T10:00:00+08:00",
        "session_id": "s1", "call_id": "c1", "tool": "godot_search_docs",
        "args": {"query": "add_child", "k": 5}, "status": "ok", "error_code": None,
        "latency_ms": 120, "result_count": 2,
        "results": [
            {"rank": 1, "doc_id": "classes/class_node", "chunk_id": "c1", "symbol": "Node.add_child", "score": 0.02},
            {"rank": 2, "doc_id": "classes/class_node", "chunk_id": "c2", "symbol": "", "score": 0.01},
        ],
        "backend": {"device": "cuda", "n_chunks": 26076},
    }
    base.update(overrides)
    return base


class TestAnalyze(unittest.TestCase):
    def test_summary_counts_and_zero_hits(self):
        events = [
            event(),
            event(call_id="c2", result_count=0, results=[], args={"query": "无命中查询"}),
            event(call_id="c3", kind="feedback", rating="bad", note="没命中", refs=[]),
        ]
        report = analyze_usage.summarize(events)
        self.assertEqual(report["overview"]["tool_calls"], 2)
        self.assertEqual(report["overview"]["errors"], 0)
        self.assertEqual(report["retrieval"]["zero_hit"], 1)
        self.assertEqual(report["feedback"]["bad"], 1)

    def test_low_score_and_chinese(self):
        events = [
            event(call_id="c1", results=[{"rank": 1, "doc_id": "d", "score": 0.001}]),
            event(call_id="c2", args={"query": "中文问题"}, results=[{"rank": 1, "doc_id": "d", "score": 0.02}]),
        ]
        report = analyze_usage.summarize(events)
        self.assertEqual(len(report["retrieval"]["low_score"]), 1)
        self.assertEqual(report["chinese"]["queries"], 1)
        self.assertEqual(report["chinese"]["zero_hit"], 0)

    def test_export_qa_rows(self):
        events = [
            event(),
            event(call_id="c9", kind="feedback", rating="good", note="", refs=[]),
        ]
        # 沙箱对 Python 新建子目录的 ACL 限制较严：写到已存在的 .tmp 下
        path = ROOT / ".tmp" / f"qa_candidates_{uuid.uuid4().hex}.jsonl"
        count = analyze_usage.export_qa(events, path)
        self.assertEqual(count, 1)
        row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(row["query"], "add_child")
        self.assertEqual(row["expected_doc_ids"], ["classes/class_node"])

    def test_markdown_contains_sections(self):
        md = analyze_usage.render_markdown(analyze_usage.summarize([event()]))
        for title in ("总览", "检索质量", "中文查询", "文档热度", "工具使用", "反馈"):
            self.assertIn(title, md)


if __name__ == "__main__":
    unittest.main()
