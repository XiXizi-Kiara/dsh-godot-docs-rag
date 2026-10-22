#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""QA 评测集校验脚本。

校验 data/processed/qa.jsonl 是否满足 docs/INTERFACES.md §8 的约定：

1. 每行是合法 JSON，字段齐全且类型正确；
2. qid 唯一、总数为 40；
3. gold_doc_ids 为 1~2 个，且都能在 godot-docs-html-stable/_sources/
   下找到对应的 .rst.txt 文件；
4. 语言分布为 en 28 / zh 12；
5. 类别分布为 class 20 / tutorial 12 / getting_started 4 /
   engine_details 2 / troubleshooting 2；
6. gold_terms 为 1~4 个，且每个词都在其任一 gold 文档原文中出现
   （大小写不敏感，对应 PageHit 的判定逻辑）。

用法（项目根目录）：
    python scripts/validate_qa.py            # 校验默认路径
    python scripts/validate_qa.py --qa 路径  # 指定其它 QA 文件

退出码：全部通过 0，否则 1。只依赖标准库。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 项目根目录 = scripts/ 的上一级
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QA = ROOT / "data" / "processed" / "qa.jsonl"
DEFAULT_SOURCES = ROOT / "godot-docs-html-stable" / "_sources"

# 预期分布（硬性要求）
EXPECTED_TOTAL = 40
EXPECTED_LANG = {"en": 28, "zh": 12}
EXPECTED_DOC_TYPE = {
    "class": 20,
    "tutorial": 12,
    "getting_started": 4,
    "engine_details": 2,
    "troubleshooting": 2,
}

REQUIRED_FIELDS = ("qid", "query", "lang", "doc_type", "gold_doc_ids", "gold_terms", "note")


def load_qa(path: Path) -> tuple[list[dict], list[str]]:
    """读取 JSONL；返回 (条目列表, 格式错误信息列表)。"""
    errors: list[str] = []
    items: list[dict] = []
    if not path.is_file():
        return [], [f"QA 文件不存在：{path}"]
    with path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"第 {lineno} 行不是合法 JSON：{exc}")
                continue
            if not isinstance(obj, dict):
                errors.append(f"第 {lineno} 行不是 JSON 对象")
                continue
            items.append(obj)
    return items, errors


def check_schema(item: dict, lineno: int) -> list[str]:
    """校验单条 QA 的字段与基本类型。"""
    errors: list[str] = []
    qid = item.get("qid", f"第{lineno}行")
    for field in REQUIRED_FIELDS:
        if field not in item:
            errors.append(f"{qid}: 缺少字段 {field}")
    if errors:
        return errors
    if not isinstance(item["query"], str) or not item["query"].strip():
        errors.append(f"{qid}: query 必须是非空字符串")
    if item["lang"] not in ("en", "zh"):
        errors.append(f"{qid}: lang 必须是 en/zh，实际 {item['lang']!r}")
    if item["doc_type"] not in EXPECTED_DOC_TYPE:
        errors.append(f"{qid}: doc_type 非法：{item['doc_type']!r}")
    golds = item["gold_doc_ids"]
    if not isinstance(golds, list) or not 1 <= len(golds) <= 2:
        errors.append(f"{qid}: gold_doc_ids 必须是 1~2 个元素的列表")
    elif not all(isinstance(g, str) and g.strip() for g in golds):
        errors.append(f"{qid}: gold_doc_ids 元素必须是非空字符串")
    terms = item["gold_terms"]
    if not isinstance(terms, list) or not 1 <= len(terms) <= 4:
        errors.append(f"{qid}: gold_terms 必须是 1~4 个元素的列表")
    elif not all(isinstance(t, str) and t.strip() for t in terms):
        errors.append(f"{qid}: gold_terms 元素必须是非空字符串")
    if not isinstance(item.get("note", ""), str):
        errors.append(f"{qid}: note 必须是字符串")
    return errors


def validate(qa_path: Path, sources: Path) -> tuple[list[str], dict]:
    """执行全部校验；返回 (错误列表, 统计信息)。"""
    errors: list[str] = []
    items, load_errors = load_qa(qa_path)
    errors.extend(load_errors)
    if load_errors:
        return errors, {}

    stats = {
        "total": len(items),
        "lang": {},
        "doc_type": {},
        "terms_checked": 0,
    }

    # 数量
    if len(items) != EXPECTED_TOTAL:
        errors.append(f"QA 总数应为 {EXPECTED_TOTAL}，实际 {len(items)}")

    # qid 唯一
    seen: dict[str, int] = {}
    for idx, item in enumerate(items):
        qid = str(item.get("qid", f"#{idx}"))
        if qid in seen:
            errors.append(f"qid 重复：{qid}（另一个在第 {seen[qid] + 1} 条）")
        else:
            seen[qid] = idx

    for idx, item in enumerate(items):
        errors.extend(check_schema(item, idx + 1))
        stats["lang"][item["lang"]] = stats["lang"].get(item["lang"], 0) + 1
        stats["doc_type"][item["doc_type"]] = stats["doc_type"].get(item["doc_type"], 0) + 1

        # gold_doc_ids 对应文件必须真实存在
        doc_texts: list[tuple[str, str]] = []
        for doc_id in item["gold_doc_ids"]:
            rst = sources / (doc_id + ".rst.txt")
            if not rst.is_file():
                errors.append(f"{item['qid']}: gold_doc_id 不存在：{doc_id}（{rst}）")
                continue
            doc_texts.append((doc_id, rst.read_text(encoding="utf-8").lower()))

        # gold_terms 必须在对应 RST 原文中出现（大小写不敏感）
        for term in item["gold_terms"]:
            stats["terms_checked"] += 1
            needle = term.lower()
            if not any(needle in text for _doc_id, text in doc_texts):
                errors.append(
                    f"{item['qid']}: gold_term {term!r} 未在 gold 文档 "
                    f"{item['gold_doc_ids']} 中找到"
                )

    # 分布校验
    for lang, want in EXPECTED_LANG.items():
        got = stats["lang"].get(lang, 0)
        if got != want:
            errors.append(f"lang={lang} 数量应为 {want}，实际 {got}")
    for dtype, want in EXPECTED_DOC_TYPE.items():
        got = stats["doc_type"].get(dtype, 0)
        if got != want:
            errors.append(f"doc_type={dtype} 数量应为 {want}，实际 {got}")
    extra_langs = set(stats["lang"]) - set(EXPECTED_LANG)
    extra_types = set(stats["doc_type"]) - set(EXPECTED_DOC_TYPE)
    if extra_langs:
        errors.append(f"出现未预期 lang：{sorted(extra_langs)}")
    if extra_types:
        errors.append(f"出现未预期 doc_type：{sorted(extra_types)}")

    return errors, stats


def main(argv: list[str] | None = None) -> int:
    """脚本入口；返回进程退出码。"""
    parser = argparse.ArgumentParser(description="校验 Godot RAG 评测集 qa.jsonl")
    parser.add_argument("--qa", type=Path, default=DEFAULT_QA, help="qa.jsonl 路径")
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES, help="_sources 目录")
    args = parser.parse_args(argv)

    qa_path: Path = args.qa
    sources: Path = args.sources
    print(f"[validate_qa] QA 文件：{qa_path}")
    print(f"[validate_qa] 文档源：{sources}")
    if not sources.is_dir():
        print(f"[validate_qa] 错误：文档源目录不存在：{sources}", file=sys.stderr)
        return 1

    errors, stats = validate(qa_path, sources)

    if stats:
        print(f"[validate_qa] 总数：{stats['total']}")
        print(
            "[validate_qa] lang 分布："
            + ", ".join(f"{k}={v}" for k, v in sorted(stats["lang"].items()))
        )
        print(
            "[validate_qa] doc_type 分布："
            + ", ".join(f"{k}={v}" for k, v in sorted(stats["doc_type"].items()))
        )
        print(f"[validate_qa] gold_terms 已校验：{stats['terms_checked']} 个")

    if errors:
        print(f"\n[validate_qa] 校验失败，共 {len(errors)} 个问题：", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("\n[validate_qa] ✅ 全部通过：40 条 QA 的字段、分布、doc_id 与 gold_terms 均有效。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
