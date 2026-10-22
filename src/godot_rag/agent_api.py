"""DSH 插件与 MCP 服务共用的工具层。

函数只负责把 GodotRAG 的结果转成稳定、可 JSON 序列化的 payload；
业务异常由调用方交给 error_payload() 映射成固定错误结构。
"""
from __future__ import annotations

import difflib
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


def document_text(doc: dict) -> str:
    """从 Document.to_dict() 的 sections 提取纯文本。

    真实索引里的文档没有 html_text（那是 service 层临时拼的）；
    这里直接消费 sections，保证 MCP 与 DSH 两端 get_doc 都有正文。
    与文档标题同名的 section 标题不再重复输出。
    """
    doc_title = (doc.get("title") or "").strip()
    parts: list[str] = []
    for section in doc.get("sections") or []:
        title = (section.get("title") or "").strip()
        if title and title != doc_title:
            parts.append(title)
        for block in section.get("blocks") or []:
            text = (block.get("text") or "").strip()
            if not text:
                continue
            if block.get("kind") == "code":
                lang = block.get("lang") or ""
                parts.append(f"```{lang}\n{text}\n```")
            else:
                parts.append(text)
    return "\n\n".join(parts)


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
                "doc_id": r.get("doc_id", ""),
                "chunk_id": r.get("chunk_id", ""),
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
    raw_html = doc.get("html_text")
    body = strip_html(raw_html) if raw_html else document_text(doc)
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


_SYMBOL_KINDS = (
    "method", "property", "signal", "constant", "enum",
    "constructor", "operator", "annotation", "theme_item",
)
_KIND_RE = re.compile(r"-(" + "|".join(_SYMBOL_KINDS) + r")-")


def _chunk_kind(chunk: dict) -> str:
    match = _KIND_RE.search(chunk.get("anchor") or "")
    return match.group(1) if match else ""


def _symbol_full(chunk: dict) -> str:
    return (chunk.get("symbol") or "").lower()


def _symbol_bare(chunk: dict) -> str:
    symbol = _symbol_full(chunk)
    return symbol.split(".", 1)[1] if "." in symbol else symbol


def _match_payload(chunk: dict, base: str | None) -> dict:
    url = doc_url(chunk.get("url", ""), base)
    anchor = chunk.get("anchor") or ""
    if anchor and "#" not in url:
        url = f"{url}#{anchor}"
    return {
        "symbol": chunk.get("symbol", ""),
        "kind": _chunk_kind(chunk),
        "title": chunk.get("title", ""),
        "breadcrumbs": list(chunk.get("breadcrumbs") or []),
        "url": url,
        "text": trim(chunk.get("raw_text", ""), 600),
    }


def find_symbol_payload(
    rag,
    name: str,
    class_name: str | None = None,
    kind: str | None = None,
    limit: int = 5,
    base: str | None = None,
) -> dict:
    query = (name or "").strip().lower()
    if not query:
        raise ApiError("BAD_PARAMS", "name 不能为空")
    if kind and kind not in _SYMBOL_KINDS:
        raise ApiError("BAD_PARAMS", f"kind 必须是 {', '.join(_SYMBOL_KINDS)} 之一")
    limit = max(1, min(int(limit), 20))
    class_filter = (class_name or "").strip().lower()

    chunks = list(rag.chunks())
    if class_filter:
        chunks = [c for c in chunks if _symbol_full(c).split(".", 1)[0] == class_filter]
    if kind:
        chunks = [c for c in chunks if _chunk_kind(c) == kind]

    tiers = [
        [c for c in chunks if _symbol_full(c) == query],
        [c for c in chunks if _symbol_bare(c) == query],
        [c for c in chunks if query in _symbol_full(c)],
    ]
    matches: list[dict] = []
    seen: set[str] = set()
    for tier in tiers:
        tier.sort(key=lambda c: (c.get("doc_type") != "class", _symbol_full(c)))
        for chunk in tier:
            chunk_id = chunk.get("chunk_id", "")
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            matches.append(_match_payload(chunk, base))
            if len(matches) >= limit:
                break
        if len(matches) >= limit:
            break

    approximate = False
    if not matches:
        fulls = sorted({_symbol_full(c) for c in chunks if _symbol_full(c)})
        bares = sorted({_symbol_bare(c) for c in chunks if _symbol_bare(c)})
        close = set(difflib.get_close_matches(query, fulls + bares, n=limit, cutoff=0.6))
        for chunk in chunks:
            if (_symbol_full(chunk) in close or _symbol_bare(chunk) in close) and chunk.get("chunk_id") not in seen:
                seen.add(chunk.get("chunk_id", ""))
                matches.append(_match_payload(chunk, base))
                if len(matches) >= limit:
                    break
        approximate = bool(matches)

    for index, match in enumerate(matches, start=1):
        match["rank"] = index
    return {
        "name": name,
        "class_name": class_name or "",
        "kind": kind or "",
        "approximate": approximate,
        "matches": matches,
    }
