"""DSH 插件与 MCP 服务共用的工具层。

函数只负责把 GodotRAG 的结果转成稳定、可 JSON 序列化的 payload；
业务异常由调用方交给 error_payload() 映射成固定错误结构。
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

DOCS_BASE_DEFAULT = "https://docs.godotengine.org/en/stable/"


class ApiError(Exception):
    """工具层可预期错误（带稳定 code）。"""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


def docs_base(base: str | None = None) -> str:
    value = base or os.environ.get("GODOT_RAG_DOCS_BASE_URL") or DOCS_BASE_DEFAULT
    return value.rstrip("/") + "/"


def doc_url(url: str, base: str | None = None) -> str:
    url = (url or "").strip()
    root = docs_base(base)
    if not url:
        return root
    if url.startswith(("http://", "https://")):
        return url
    return root + url.lstrip("/")


def trim(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def strip_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text or "")
    text = re.sub(r"</(p|h[1-6]|li|div)>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def search_payload(rag, query: str, k: int = 5, mode: str = "hybrid", base: str | None = None) -> dict:
    results = rag.search(query, k=k, mode=mode)
    return {
        "query": query,
        "mode": mode,
        "k": k,
        "results": [
            {
                "rank": r["rank"],
                "score": round(float(r["score"]), 6),
                "title": r["title"],
                "symbol": r.get("symbol", ""),
                "breadcrumbs": list(r["breadcrumbs"]),
                "url": doc_url(r["url"], base),
                "text": trim(r.get("raw_text") or r["text"], 700),
            }
            for r in results
        ],
    }


def get_doc_payload(
    rag, doc_id: str, offset: int = 0, limit: int = 8000, base: str | None = None
) -> dict:
    doc_id = (doc_id or "").strip()
    if not doc_id:
        raise ApiError("BAD_PARAMS", "doc_id 不能为空")
    offset = max(0, int(offset))
    limit = max(1, min(int(limit), 20000))
    doc = rag.get_doc(doc_id)
    if not doc:
        raise ApiError(
            "BAD_PARAMS",
            f"未找到文档：{doc_id}",
            "可先用 godot_search_docs 找到正确的 doc_id",
        )
    body = strip_html(doc.get("html_text", ""))
    return {
        "doc_id": doc.get("doc_id", doc_id),
        "title": doc.get("title", doc_id),
        "url": doc_url(doc.get("url", ""), base),
        "breadcrumbs": list(doc.get("breadcrumbs") or []),
        "total_chars": len(body),
        "offset": offset,
        "text": body[offset : offset + limit],
        "truncated": offset + limit < len(body),
    }


def stats_payload(rag, index_dir: str | Path | None = None) -> dict:
    stats = dict(rag.stats())
    directory = Path(index_dir or stats.get("index_dir") or "")
    stats["index_dir"] = str(directory)
    meta = directory / "meta.json"
    stats["index_mtime"] = (
        datetime.fromtimestamp(meta.stat().st_mtime).astimezone().isoformat(timespec="seconds")
        if meta.is_file()
        else None
    )
    return stats


def error_payload(exc: Exception) -> dict:
    from .retrieve import IndexNotReady

    if isinstance(exc, IndexNotReady):
        return {
            "code": "INDEX_NOT_READY",
            "message": "索引尚未构建",
            "hint": "在项目根目录运行：python scripts\\run.py build（使用 conda godotrag 环境）",
        }
    if isinstance(exc, ApiError):
        return {"code": exc.code, "message": exc.message, "hint": exc.hint}
    if isinstance(exc, ValueError):
        return {"code": "BAD_PARAMS", "message": str(exc), "hint": ""}
    return {"code": "INTERNAL", "message": f"{type(exc).__name__}: {exc}", "hint": ""}
