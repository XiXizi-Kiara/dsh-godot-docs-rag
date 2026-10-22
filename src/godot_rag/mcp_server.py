"""MCP 服务：把 Godot 官方文档 RAG 暴露为 MCP tools。

支持两种传输：
- stdio（默认，推荐给 agent 本地集成）：`python -m godot_rag.mcp_server`
- streamable-http（常驻/远程）：`python -m godot_rag.mcp_server --transport streamable-http --port 8766`

工具（agent 侧名称形如 `mcp__<serverName>__<tool>`）：
- search_docs：检索官方文档，返回标题/面包屑/API 符号/URL/片段
- ask_docs：回答 + [n] 引用（无 LLM key 时为检索式回答）
- get_doc：按 doc_id 读取整页原文
- rag_stats：索引规模、模型与设备信息

索引在首次工具调用时懒加载，进程内复用。
"""
from __future__ import annotations

import json
import os
import re
from typing import Literal

from . import __version__

# 文档站前缀：结果里的 url 是相对路径（如 classes/class_node.html），拼成可点击链接
DOCS_BASE = (
    os.environ.get("GODOT_RAG_DOCS_BASE_URL")
    or "https://docs.godotengine.org/en/stable/"
).rstrip("/") + "/"

_RAG = None
_INDEX_DIR: str | None = None

INSTRUCTIONS = (
    "Godot 官方文档检索助手。可用工具："
    "search_docs（按关键词或自然语言检索官方文档，返回文档标题、面包屑、API 符号、"
    "URL 与片段）、ask_docs（给出回答并带 [n] 引用）、get_doc（按 doc_id 读取整页原文）、"
    "rag_stats（索引规模与模型信息）。"
    "回答 Godot 相关问题时先调用 search_docs 获取依据，只依据返回片段作答，不要编造 API、"
    "参数、默认值或行为；片段不足时明确说明缺少什么信息。"
)


def _get_rag():
    """懒加载 GodotRAG（首次工具调用时才加载模型/索引）。"""
    global _RAG
    if _RAG is None:
        from .retrieve import GodotRAG

        _RAG = GodotRAG(_INDEX_DIR)
    return _RAG


def _error_text(exc: Exception) -> str:
    from .retrieve import IndexNotReady

    if isinstance(exc, IndexNotReady):
        return (
            "索引尚未构建。请先在项目根目录运行：\n"
            r"C:\Users\Kiara\.conda\envs\godotrag\python.exe scripts\run.py build"
        )
    return f"Godot RAG 执行失败：{type(exc).__name__}: {exc}"


def _doc_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return DOCS_BASE
    if url.startswith(("http://", "https://")):
        return url
    return DOCS_BASE + url.lstrip("/")


def _trim(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _strip_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text or "")
    text = re.sub(r"</(p|h[1-6]|li|div)>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _search_impl(query: str, k: int = 5, mode: str = "hybrid") -> str:
    try:
        results = _get_rag().search(query, k=k, mode=mode)
    except Exception as exc:  # noqa: BLE001 - MCP 工具应返回可读错误而非抛异常
        return _error_text(exc)
    payload = {
        "query": query,
        "mode": mode,
        "k": k,
        "results": [
            {
                "rank": r["rank"],
                "score": round(float(r["score"]), 6),
                "title": r["title"],
                "symbol": r.get("symbol", ""),
                "breadcrumbs": r["breadcrumbs"],
                "url": _doc_url(r["url"]),
                "text": _trim(r.get("raw_text") or r["text"], 700),
            }
            for r in results
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _ask_impl(query: str, k: int = 6, mode: str = "hybrid") -> str:
    try:
        result = _get_rag().ask(query, k=k, mode=mode)
    except Exception as exc:  # noqa: BLE001
        return _error_text(exc)
    lines = [
        f"问题：{query}",
        "",
        str(result.get("answer") or "").strip(),
        "",
        "引用：",
    ]
    for cit in result.get("citations") or []:
        url = _doc_url(cit.get("url", ""))
        symbol = cit.get("symbol") or ""
        label = f"{cit.get('title', '')}" + (f" · {symbol}" if symbol else "")
        lines.append(f"- [{cit.get('n')}] {label} — {url}")
    if not result.get("citations"):
        lines.append("- （无引用）")
    return "\n".join(lines)


def _doc_impl(doc_id: str, max_chars: int = 6000) -> str:
    try:
        doc = _get_rag().get_doc(doc_id)
    except Exception as exc:  # noqa: BLE001
        return _error_text(exc)
    if not doc:
        return f"未找到文档：{doc_id}（可先用 search_docs 找到正确的 doc_id）"
    body = _strip_html(doc.get("html_text", ""))
    if len(body) > max_chars:
        body = body[:max_chars].rstrip() + "\n\n…（已截断，完整内容见 URL）"
    crumbs = " / ".join(doc.get("breadcrumbs") or [])
    return "\n".join(
        [
            f"# {doc.get('title', doc_id)}",
            f"doc_id: {doc.get('doc_id', doc_id)}",
            f"URL: {_doc_url(doc.get('url', ''))}",
            f"面包屑: {crumbs}",
            "",
            body,
        ]
    )


def _stats_impl() -> str:
    try:
        stats = _get_rag().stats()
    except Exception as exc:  # noqa: BLE001
        return _error_text(exc)
    return json.dumps(stats, ensure_ascii=False, indent=2)


def build_server():
    """构造 FastMCP server（工具在此注册）。"""
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("godot-rag", instructions=INSTRUCTIONS)

    @server.tool()
    def search_docs(query: str, k: int = 5, mode: str = "hybrid") -> str:
        """在 Godot 官方文档中检索。

        Args:
            query: 检索词或自然语言问题（英文命中率更高，中文可用常见术语）。
            k: 返回结果数，1-20，默认 5。
            mode: 检索模式：hybrid（默认）/ dense / bm25 / hybrid+rerank / dense+rerank。
        """
        return _search_impl(query, k=k, mode=mode)

    @server.tool()
    def ask_docs(query: str, k: int = 6, mode: str = "hybrid") -> str:
        """回答 Godot 问题，并给出 [n] 引用（无 LLM key 时为检索式回答）。

        Args:
            query: 用户问题。
            k: 用于回答的文档片段数，1-20，默认 6。
            mode: 检索模式，默认 hybrid。
        """
        return _ask_impl(query, k=k, mode=mode)

    @server.tool()
    def get_doc(doc_id: str, max_chars: int = 6000) -> str:
        """按 doc_id 读取整页官方文档原文。

        Args:
            doc_id: 文档 ID，如 classes/class_node、tutorials/2d/2d_movement。
            max_chars: 正文最大字符数，默认 6000。
        """
        return _doc_impl(doc_id, max_chars=max_chars)

    @server.tool()
    def rag_stats() -> str:
        """查看索引规模、模型与计算设备信息。"""
        return _stats_impl()

    return server


SERVER_NAME = "godot-rag"


def main(argv: list[str] | None = None) -> int:
    """MCP 服务入口。"""
    import argparse

    global _INDEX_DIR
    ap = argparse.ArgumentParser(prog="godot_rag.mcp_server", description="Godot RAG MCP 服务")
    ap.add_argument(
        "--transport",
        choices=("stdio", "streamable-http", "sse"),
        default="stdio",
        help="传输方式（默认 stdio）",
    )
    ap.add_argument("--host", default="127.0.0.1", help="HTTP 传输监听地址（默认 127.0.0.1）")
    ap.add_argument("--port", type=int, default=8766, help="HTTP 传输端口（默认 8766）")
    ap.add_argument("--index-dir", default=None, help="索引目录（默认 data/index）")
    args = ap.parse_args(argv)
    _INDEX_DIR = args.index_dir

    server = build_server()
    if args.transport != "stdio":
        server.settings.host = args.host
        server.settings.port = args.port
        print(
            f"Godot RAG MCP 服务（{args.transport}）已启动："
            f"http://{args.host}:{args.port}/mcp",
            flush=True,
        )
    server.run(transport=args.transport)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
