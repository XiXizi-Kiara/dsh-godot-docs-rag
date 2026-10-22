"""Godot RAG 检索评测：指标计算与报告输出。

对应 docs/INTERFACES.md §8：

- ``load_qa(path)``：读取 ``data/processed/qa.jsonl``（一行一个 JSON 对象）；
- ``evaluate(rag, qa, configs)``：对每个检索配置计算 Recall@1/5/10、
  MRR@10、PageHit@5，并按 ``lang`` 与 ``doc_type`` 分组；
- ``write_reports(...)``：输出 ``data/reports/eval.json``（机读）与
  ``data/reports/eval.md``（人读，含失败案例）；
- ``main(argv)``：供 ``python -m godot_rag eval`` 调用的入口。

指标口径（固定）：

- Recall@k：top-k 中任一结果的 ``doc_id`` 属于 ``gold_doc_ids`` 即算命中
  （每题 gold 为 1~2 篇，采用“命中任一 gold 文档”的宽松口径）；
- MRR@10：top-10 中第一条 gold 文档排名的倒数，未命中为 0；
- PageHit@5：top-5 中任一结果 ``doc_id`` 命中 gold，或任一结果 ``text``
  小写后包含任一 ``gold_term`` 小写。

本模块只依赖标准库；对 ``GodotRAG`` 的导入是惰性的，便于无索引时单测。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

# 固定评测参数
TOP_K = 10
PAGEHIT_K = 5
# 默认只评测不依赖 reranker 模型的三种模式；若 models/ 下有重排模型，
# 可显式传 --configs hybrid,hybrid+rerank。
DEFAULT_CONFIGS = ("hybrid", "dense", "bm25")
SUPPORTED_MODES = ("hybrid", "dense", "bm25", "hybrid+rerank", "dense+rerank")
REQUIRED_QA_FIELDS = ("qid", "query", "gold_doc_ids", "gold_terms")
METRIC_KEYS = ("recall@1", "recall@5", "recall@10", "mrr@10", "pagehit@5")


class EvalError(Exception):
    """评测过程中可预期的错误（缺文件、缺索引、QA 格式非法等）。"""


# ---------------------------------------------------------------------------
# 路径与 QA 读取
# ---------------------------------------------------------------------------


def _project_root() -> Path:
    """定位项目根目录（含 docs/INTERFACES.md 的最近祖先）。"""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "docs" / "INTERFACES.md").is_file():
            return parent
    return Path.cwd()


ROOT = _project_root()
DEFAULT_QA_PATH = ROOT / "data" / "processed" / "qa.jsonl"
DEFAULT_INDEX_DIR = ROOT / "data" / "index"
DEFAULT_OUT_DIR = ROOT / "data" / "reports"


def load_qa(path: str | Path) -> list[dict]:
    """读取并校验 QA 集，返回 list[dict]。

    每条至少包含 qid、query、gold_doc_ids、gold_terms；lang 与 doc_type
    缺省时分别按 ``en`` 与 ``unknown`` 分组。文件不存在或格式非法时抛
    ``EvalError``（错误信息为中文，便于 CLI 直接展示）。
    """
    qa_path = Path(path)
    if not qa_path.is_file():
        raise EvalError(f"QA 文件不存在：{qa_path}")

    items: list[dict] = []
    seen: set[str] = set()
    try:
        with qa_path.open("r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise EvalError(f"QA 第 {lineno} 行不是合法 JSON：{exc}") from exc
                if not isinstance(item, dict):
                    raise EvalError(f"QA 第 {lineno} 行不是 JSON 对象")
                for field in REQUIRED_QA_FIELDS:
                    if field not in item:
                        raise EvalError(f"QA 第 {lineno} 行缺少字段 {field}")
                qid = str(item["qid"])
                if qid in seen:
                    raise EvalError(f"QA 第 {lineno} 行 qid 重复：{qid}")
                if not isinstance(item["query"], str) or not item["query"].strip():
                    raise EvalError(f"QA {qid} 的 query 不能为空")
                golds = item["gold_doc_ids"]
                if not isinstance(golds, list) or not golds:
                    raise EvalError(f"QA {qid} 的 gold_doc_ids 必须是非空列表")
                terms = item["gold_terms"]
                if not isinstance(terms, list):
                    raise EvalError(f"QA {qid} 的 gold_terms 必须是列表")
                seen.add(qid)
                items.append(item)
    except OSError as exc:
        raise EvalError(f"读取 QA 文件失败：{qa_path}（{exc}）") from exc

    if not items:
        raise EvalError(f"QA 文件为空：{qa_path}")
    return items


# ---------------------------------------------------------------------------
# 指标计算
# ---------------------------------------------------------------------------


def _as_result_list(raw: object) -> list[dict]:
    """把 rag.search 的返回值归一化为 list[dict]（容忍 None/异常结构）。"""
    if raw is None:
        return []
    if isinstance(raw, dict):
        raw = raw.get("results", [])
    if not isinstance(raw, (list, tuple)):
        return []
    return [r for r in raw if isinstance(r, dict)]


def _is_gold(result: dict, gold_doc_ids: list[str]) -> bool:
    """结果是否命中任一 gold 文档。"""
    return result.get("doc_id") in gold_doc_ids


def _first_relevant_rank(results: list[dict], gold_doc_ids: list[str], k: int = TOP_K) -> int | None:
    """返回 top-k 内第一条 gold 结果的 1-based 排名；未命中返回 None。"""
    for rank, result in enumerate(results[:k], 1):
        if _is_gold(result, gold_doc_ids):
            return rank
    return None


def _page_hit(results: list[dict], gold_doc_ids: list[str], gold_terms: list[str]) -> bool:
    """PageHit@5：doc_id 命中 gold，或结果正文包含任一 gold_term（大小写不敏感）。"""
    top = results[:PAGEHIT_K]
    if any(_is_gold(r, gold_doc_ids) for r in top):
        return True
    needles = [str(t).lower() for t in gold_terms if str(t).strip()]
    if not needles:
        return False
    for result in top:
        text = str(result.get("text") or "").lower()
        if any(needle in text for needle in needles):
            return True
    return False


def _mean(values: list[float]) -> float:
    """平均值；空列表返回 0.0。"""
    return sum(values) / len(values) if values else 0.0


def _aggregate(rows: list[dict]) -> dict:
    """把逐题结果聚合为指标字典（保留 4 位小数，便于对比）。"""
    n = len(rows)
    if n == 0:
        return {"n_queries": 0, **{key: 0.0 for key in METRIC_KEYS}, "latency_ms_mean": 0.0}
    return {
        "n_queries": n,
        "recall@1": round(_mean([row["recall@1"] for row in rows]), 4),
        "recall@5": round(_mean([row["recall@5"] for row in rows]), 4),
        "recall@10": round(_mean([row["recall@10"] for row in rows]), 4),
        "mrr@10": round(_mean([row["mrr@10"] for row in rows]), 4),
        "pagehit@5": round(_mean([row["pagehit@5"] for row in rows]), 4),
        "latency_ms_mean": round(_mean([row["latency_ms"] for row in rows]), 2),
    }


def _group(rows: list[dict], key: str) -> dict:
    """按某字段分组聚合并按组名排序。"""
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(str(row.get(key, "unknown")), []).append(row)
    return {name: _aggregate(groups[name]) for name in sorted(groups)}


def _make_row(item: dict, results: list[dict], latency_ms: float, error: str | None) -> dict:
    """计算单题指标，形成逐题结果行。"""
    gold = [str(g) for g in item.get("gold_doc_ids", [])]
    terms = [str(t) for t in item.get("gold_terms", [])]
    rank = _first_relevant_rank(results, gold, TOP_K) if not error else None
    hit_at = {k: (rank is not None and rank <= k) for k in (1, 5, 10)}
    return {
        "qid": str(item.get("qid", "")),
        "query": item.get("query", ""),
        "lang": str(item.get("lang", "en")),
        "doc_type": str(item.get("doc_type", "unknown")),
        "gold_doc_ids": gold,
        "gold_terms": terms,
        "recall@1": 1.0 if hit_at[1] else 0.0,
        "recall@5": 1.0 if hit_at[5] else 0.0,
        "recall@10": 1.0 if hit_at[10] else 0.0,
        "mrr@10": (1.0 / rank) if rank else 0.0,
        "pagehit@5": 1.0 if (not error and _page_hit(results, gold, terms)) else 0.0,
        "first_relevant_rank": rank,
        "latency_ms": round(latency_ms, 2),
        "top_doc_ids": [str(r.get("doc_id", "")) for r in results[:TOP_K]],
        "error": error,
    }


def _failures(rows: list[dict]) -> list[dict]:
    """收集失败题（PageHit@5 未命中），附 top-3 的 doc_id 供检索优化。"""
    failures = []
    for row in rows:
        if row["pagehit@5"] >= 1.0:
            continue
        failures.append(
            {
                "qid": row["qid"],
                "query": row["query"],
                "lang": row["lang"],
                "doc_type": row["doc_type"],
                "gold_doc_ids": row["gold_doc_ids"],
                "gold_terms": row["gold_terms"],
                "top3": [
                    {"rank": i, "doc_id": doc_id}
                    for i, doc_id in enumerate(row["top_doc_ids"][:3], 1)
                ],
                "error": row["error"],
            }
        )
    return failures


def evaluate(rag, qa: list[dict], configs: list[str] | tuple[str, ...]) -> dict:
    """对每个检索配置评测 QA 集，返回结构化结果（可 JSON 序列化）。

    ``rag`` 需提供 ``search(query, k=..., mode=...) -> list[dict]`` 接口；
    单题检索异常会记录到 ``error`` 字段并按未命中计，不中断整体评测。
    """
    configs = [str(c).strip() for c in configs if str(c).strip()]
    if not configs:
        raise EvalError("至少需要一个评测配置（--configs）")

    report: dict = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "n_queries": len(qa),
        "top_k": TOP_K,
        "pagehit_k": PAGEHIT_K,
        "configs": configs,
        "results": {},
    }

    for config in configs:
        rows: list[dict] = []
        for item in qa:
            query = str(item["query"])
            started = time.perf_counter()
            error: str | None = None
            results: list[dict] = []
            try:
                results = _as_result_list(rag.search(query, k=TOP_K, mode=config))
            except Exception as exc:  # noqa: BLE001 - 单题失败不应中断整体评测
                error = f"{type(exc).__name__}: {exc}"
            latency_ms = (time.perf_counter() - started) * 1000.0
            rows.append(_make_row(item, results, latency_ms, error))

        overall = _aggregate(rows)
        report["results"][config] = {
            "overall": overall,
            "by_lang": _group(rows, "lang"),
            "by_doc_type": _group(rows, "doc_type"),
            "n_errors": sum(1 for row in rows if row["error"]),
            "failures": _failures(rows),
            "per_query": rows,
        }
    return report


# ---------------------------------------------------------------------------
# 报告输出
# ---------------------------------------------------------------------------


def _fmt(value: float) -> str:
    """指标格式化为 4 位小数。"""
    return f"{value:.4f}"


def _metric_table(stats: dict, label: str) -> list[str]:
    """生成一张「分组 × 指标」的 markdown 表。"""
    lines = [
        f"| {label} | 题数 | Recall@1 | Recall@5 | Recall@10 | MRR@10 | PageHit@5 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, agg in stats.items():
        lines.append(
            f"| {name} | {agg['n_queries']} | {_fmt(agg['recall@1'])} | "
            f"{_fmt(agg['recall@5'])} | {_fmt(agg['recall@10'])} | "
            f"{_fmt(agg['mrr@10'])} | {_fmt(agg['pagehit@5'])} |"
        )
    return lines


def render_markdown(report: dict) -> str:
    """把评测结果渲染为中文 markdown 报告。"""
    lines: list[str] = [
        "# Godot RAG 检索评测报告",
        "",
        f"- 生成时间：{report.get('generated_at', '')}",
        f"- QA 数量：{report.get('n_queries', 0)}",
        f"- 检索深度：top-{report.get('top_k', TOP_K)}；PageHit 窗口：top-{report.get('pagehit_k', PAGEHIT_K)}",
        "- 指标口径：Recall@k = top-k 中命中任一 gold 文档；MRR@10 = 首条 gold 文档排名倒数；",
        "  PageHit@5 = top-5 命中 gold 文档，或结果正文包含任一 gold_term（大小写不敏感）。",
        "",
        "## 总览",
        "",
        "| 配置 | Recall@1 | Recall@5 | Recall@10 | MRR@10 | PageHit@5 | 平均延迟(ms) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for config, data in report.get("results", {}).items():
        agg = data["overall"]
        lines.append(
            f"| {config} | {_fmt(agg['recall@1'])} | {_fmt(agg['recall@5'])} | "
            f"{_fmt(agg['recall@10'])} | {_fmt(agg['mrr@10'])} | "
            f"{_fmt(agg['pagehit@5'])} | {agg['latency_ms_mean']:.2f} |"
        )

    for config, data in report.get("results", {}).items():
        lines.extend(["", f"## 按语言（{config}）", ""])
        lines.extend(_metric_table(data.get("by_lang", {}), "语言"))
        lines.extend(["", f"## 按文档类型（{config}）", ""])
        lines.extend(_metric_table(data.get("by_doc_type", {}), "类型"))

    lines.extend(["", "## 失败案例", ""])
    for config, data in report.get("results", {}).items():
        failures = data.get("failures", [])
        lines.append(f"### {config}（{len(failures)} 条）")
        lines.append("")
        if not failures:
            lines.append("无失败案例。")
            lines.append("")
            continue
        for failure in failures:
            lines.append(
                f"- **{failure['qid']}** [{failure['lang']}/{failure['doc_type']}] "
                f"{failure['query']}"
            )
            lines.append(f"  - gold：`{', '.join(failure['gold_doc_ids'])}`")
            if failure.get("error"):
                lines.append(f"  - 检索异常：{failure['error']}")
            top3 = failure.get("top3") or []
            if top3:
                rendered = "，".join(
                    f"#{item['rank']} `{item['doc_id']}`" for item in top3
                )
                lines.append(f"  - top-3：{rendered}")
            else:
                lines.append("  - top-3：（无检索结果）")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_reports(report: dict, out_dir: str | Path, stem: str = "eval") -> tuple[Path, Path]:
    """写出 ``<stem>.json`` 与 ``<stem>.md``，返回两个路径。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / f"{stem}.json"
    md_path = out / f"{stem}.md"
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write("\n")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


# ---------------------------------------------------------------------------
# 公共入口
# ---------------------------------------------------------------------------


def _portable_path(value: str | Path) -> str:
    """报告里的路径写成相对仓库根的 POSIX 形式，避免把本机绝对路径写进仓库。

    不 import config：本模块可能被测试以独立文件方式加载（无父包）。
    """
    path = Path(value)
    root = Path(__file__).resolve().parents[2]
    try:
        return path.resolve().relative_to(root).as_posix()
    except (ValueError, OSError):
        return path.name


def _make_rag(index_dir: str | Path):
    """惰性构造 GodotRAG；缺索引或包不可用时抛 EvalError。"""
    idx = Path(index_dir)
    if not (idx / "meta.json").is_file():
        raise EvalError(
            f"索引不存在：{idx}，请先运行 python -m godot_rag build"
        )
    try:
        from godot_rag import GodotRAG  # type: ignore[import-not-found]
    except ImportError:
        try:
            from . import GodotRAG  # type: ignore[import-not-found]
        except ImportError as exc:
            raise EvalError(
                "无法导入 GodotRAG，请确认已把 src 加入 PYTHONPATH（set PYTHONPATH=src）"
            ) from exc
    rag = GodotRAG(idx)
    if not getattr(rag, "ready", False):
        raise EvalError(
            f"索引未就绪：{idx}，请先运行 python -m godot_rag build"
        )
    return rag


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="godot_rag eval", description="Godot RAG 检索评测")
    parser.add_argument(
        "--configs",
        default=",".join(DEFAULT_CONFIGS),
        help=f"逗号分隔的检索配置，默认 {','.join(DEFAULT_CONFIGS)}；可用：{','.join(SUPPORTED_MODES)}",
    )
    parser.add_argument("--qa", default=str(DEFAULT_QA_PATH), help="QA 文件路径（默认 data/processed/qa.jsonl）")
    parser.add_argument("--index", default=str(DEFAULT_INDEX_DIR), help="索引目录（默认 data/index）")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="报告输出目录（默认 data/reports）")
    parser.add_argument("--json", action="store_true", help="stdout 只输出机读 JSON")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：``python -m godot_rag eval --configs ... --json``。

    返回进程退出码：0 成功；2 缺 QA 文件 / 缺索引 / 其它可预期错误。
    """
    args = _parse_args(argv)
    configs = [c.strip() for c in str(args.configs).split(",") if c.strip()]
    unknown = [c for c in configs if c not in SUPPORTED_MODES]
    for mode in unknown:
        print(f"[eval] 警告：未知检索配置 {mode!r}，将原样传给 rag.search", file=sys.stderr)

    def log(message: str) -> None:
        print(f"[eval] {message}", file=sys.stderr)

    try:
        qa = load_qa(args.qa)
        log(f"已加载 QA {len(qa)} 条：{args.qa}")
        rag = _make_rag(args.index)
        log(f"索引就绪：{args.index}；配置：{', '.join(configs)}")
        started = time.perf_counter()
        report = evaluate(rag, qa, configs)
        elapsed = time.perf_counter() - started
        report["qa_path"] = _portable_path(args.qa)
        report["index_dir"] = _portable_path(args.index)
        report["elapsed_seconds"] = round(elapsed, 2)
        json_path, md_path = write_reports(report, args.out_dir)
        log(f"报告已写入：{json_path} 与 {md_path}（耗时 {elapsed:.1f}s）")
    except EvalError as exc:
        print(f"[eval] 错误：{exc}", file=sys.stderr)
        if args.json:  # --json 模式下 stdout 也保持合法 JSON
            print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI 不抛裸异常
        message = f"评测失败：{type(exc).__name__}: {exc}"
        print(f"[eval] {message}", file=sys.stderr)
        if args.json:
            print(json.dumps({"error": message}, ensure_ascii=False))
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        print(f"评测完成：{report['n_queries']} 条 QA，配置 {', '.join(configs)}")
        for config, data in report["results"].items():
            agg = data["overall"]
            print(
                f"  {config}: Recall@5={_fmt(agg['recall@5'])} "
                f"MRR@10={_fmt(agg['mrr@10'])} PageHit@5={_fmt(agg['pagehit@5'])}"
            )
        print(f"报告：{json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
