"""分析 logs/rag_usage.jsonl：检索质量 / 工具使用 / 反馈，并可导出 QA 候选。

纯标准库；不联网、不读写索引。用法见 docs/USAGE_LOG.md。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG = ROOT / "logs" / "rag_usage.jsonl"
DEFAULT_QA_OUT = ROOT / "data" / "processed" / "qa_candidates.jsonl"
CJK = re.compile(r"[\u4e00-\u9fff]")


def load_events(paths: list[Path]) -> list[dict]:
    events: list[dict] = []
    for path in paths:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                events.append(item)
    events.sort(key=lambda item: item.get("ts") or "")
    return events


def _percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(int(round((len(ordered) - 1) * ratio)), len(ordered) - 1)
    return float(ordered[index])


def _top_score(item):
    results = item.get("results") or []
    return max((float(r.get("score") or 0) for r in results), default=None)


def summarize(events: list[dict], low_score: float = 0.02) -> dict:
    calls = [item for item in events if item.get("kind") == "tool_call"]
    feedback = [item for item in events if item.get("kind") == "feedback"]
    latencies = [float(item.get("latency_ms") or 0) for item in calls]
    tools = Counter(item.get("tool") for item in calls)
    errors = [item for item in calls if item.get("status") == "error"]
    zero_hit = [item for item in calls if int(item.get("result_count") or 0) == 0]
    low = [
        item for item in calls
        if item.get("results") and (_top_score(item) is not None and _top_score(item) < low_score)
    ]
    chinese = [item for item in calls if CJK.search(str(item.get("args", {}).get("query", "")))]
    chinese_zero = [item for item in chinese if int(item.get("result_count") or 0) == 0]

    doc_counts: Counter[str] = Counter()
    for item in calls:
        for result in item.get("results") or []:
            if result.get("doc_id"):
                doc_counts[result["doc_id"]] += 1

    reads = [item for item in calls if item.get("tool") == "godot_get_doc"]
    searches = [item for item in calls if item.get("tool") == "godot_search_docs"]
    symbols = [item for item in calls if item.get("tool") == "godot_find_symbol"]

    return {
        "overview": {
            "tool_calls": len(calls),
            "errors": len(errors),
            "error_rate": round(len(errors) / len(calls), 4) if calls else 0.0,
            "tools": dict(tools),
            "latency_p50_ms": round(_percentile(latencies, 0.5)),
            "latency_p95_ms": round(_percentile(latencies, 0.95)),
            "latency_max_ms": round(max(latencies)) if latencies else 0,
            "slow_first_loads": sum(1 for item in calls if float(item.get("latency_ms") or 0) > 5000),
            "devices": dict(Counter((item.get("backend") or {}).get("device") for item in calls)),
        },
        "retrieval": {
            "zero_hit": len(zero_hit),
            "zero_hit_rate": round(len(zero_hit) / len(calls), 4) if calls else 0.0,
            "low_score": low,
            "modes": dict(Counter(str(item.get("args", {}).get("mode") or "unknown") for item in searches)),
        },
        "chinese": {
            "queries": len(chinese),
            "zero_hit": len(chinese_zero),
            "share": round(len(chinese) / len(calls), 4) if calls else 0.0,
        },
        "docs": {
            "top_docs": doc_counts.most_common(20),
            "read_but_never_retrieved": sorted(
                {item.get("args", {}).get("doc_id") for item in reads}
                - {doc for doc, _count in doc_counts.items()}
            ),
        },
        "tools": {
            "find_symbol_calls": len(symbols),
            "find_symbol_share": round(len(symbols) / len(calls), 4) if calls else 0.0,
            "get_doc_calls": len(reads),
            "search_to_doc_ratio": round(len(reads) / len(searches), 4) if searches else 0.0,
        },
        "feedback": {
            "good": sum(1 for item in feedback if item.get("rating") == "good"),
            "bad": sum(1 for item in feedback if item.get("rating") == "bad"),
            "bad_queries": [
                {"note": item.get("note", ""),
                 "refs": [ref.get("query", "") for ref in item.get("refs") or []]}
                for item in feedback if item.get("rating") == "bad"
            ],
        },
        "cleans": {"events": len(events), "low_score_threshold": low_score},
    }


def render_markdown(report: dict) -> str:
    overview = report["overview"]
    retrieval = report["retrieval"]
    chinese = report["chinese"]
    docs = report["docs"]
    tools = report["tools"]
    feedback = report["feedback"]
    lines = [
        "# Godot RAG 使用日志报告",
        "",
        "## 总览",
        f"- 工具调用：{overview['tool_calls']}（错误 {overview['errors']}，错误率 {overview['error_rate']:.2%}）",
        f"- 延迟：P50 {overview['latency_p50_ms']} ms / P95 {overview['latency_p95_ms']} ms / max {overview['latency_max_ms']} ms",
        f"- 首次加载（>5s）：{overview['slow_first_loads']} 次",
        f"- 工具分布：{overview['tools']}",
        f"- 设备：{overview['devices']}",
        "",
        "## 检索质量",
        f"- 零命中：{retrieval['zero_hit']}（{retrieval['zero_hit_rate']:.2%}）",
        f"- 低分查询（top1 < {report['cleans']['low_score_threshold']}）：{len(retrieval['low_score'])} 条",
        f"- 检索模式：{retrieval['modes']}",
        "",
        "## 中文查询",
        f"- 中文查询 {chinese['queries']} 条（占比 {chinese['share']:.2%}），零命中 {chinese['zero_hit']} 条",
        "",
        "## 文档热度",
        f"- Top 文档：{docs['top_docs'][:10]}",
        f"- 被 get_doc 读取但从未命中：{docs['read_but_never_retrieved'][:10]}",
        "",
        "## 工具使用",
        f"- find_symbol：{tools['find_symbol_calls']} 次（占比 {tools['find_symbol_share']:.2%}）",
        f"- get_doc：{tools['get_doc_calls']} 次，search→get_doc 比率 {tools['search_to_doc_ratio']}",
        "",
        "## 反馈",
        f"- good：{feedback['good']} / bad：{feedback['bad']}",
    ]
    if feedback["bad_queries"]:
        lines.append("- bad 关联查询：")
        for item in feedback["bad_queries"][:10]:
            lines.append(f"  - {item['note'] or '(无备注)'} — {item['refs']}")
    return "\n".join(lines) + "\n"


def export_qa(events: list[dict], path: Path) -> int:
    candidates: list[dict] = []
    good_sessions = {
        item.get("session_id") for item in events
        if item.get("kind") == "feedback" and item.get("rating") == "good"
    }
    for item in events:
        if item.get("kind") != "tool_call" or item.get("tool") != "godot_search_docs":
            continue
        if item.get("status") != "ok" or not item.get("results"):
            continue
        doc_ids: list[str] = []
        titles: list[str] = []
        for result in item["results"]:
            doc_id = result.get("doc_id")
            if doc_id and doc_id not in doc_ids:
                doc_ids.append(doc_id)
                titles.append(result.get("title", ""))
        if not doc_ids:
            continue
        candidates.append({
            "query": item.get("args", {}).get("query", ""),
            "expected_doc_ids": doc_ids[:3],
            "expected_titles": titles[:3],
            "source": "good" if item.get("session_id") in good_sessions else "unrated",
            "ts": item.get("ts"),
        })
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in candidates) + ("\n" if candidates else ""),
        encoding="utf-8",
    )
    return len(candidates)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="analyze_usage.py", description="分析 Godot RAG 使用日志")
    parser.add_argument("--file", action="append", default=None, help="日志文件（可多次；默认 logs/rag_usage.jsonl）")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--low-score", type=float, default=0.02)
    parser.add_argument("--json", action="store_true", help="输出 JSON 而不是 Markdown")
    parser.add_argument("--export-qa", nargs="?", const=str(DEFAULT_QA_OUT), default=None)
    args = parser.parse_args(argv)
    paths = [Path(item) for item in args.file] if args.file else [DEFAULT_LOG]
    events = load_events(paths)
    if not events:
        print(
            f"没有可用日志（检查 {', '.join(str(p) for p in paths)}）。\n"
            "使用日志默认关闭：请在插件管理列表启用『Godot 文档 RAG 使用日志』。",
            file=sys.stderr,
        )
    report = summarize(events, low_score=args.low_score)
    report["overview"]["top"] = args.top
    if args.export_qa:
        count = export_qa(events, Path(args.export_qa))
        report["exported_qa"] = {"path": args.export_qa, "rows": count}
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
