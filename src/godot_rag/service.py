"""HTTP 服务层 + 内嵌单页 Web UI（标准库 ThreadingHTTPServer，零依赖）。

端点见 docs/INTERFACES.md §6：

    GET  /                单页 UI
    GET  /api/health      {"ok", "ready", "n_chunks"}
    GET  /api/stats       rag.stats()
    POST /api/search      {"query", "k", "mode"} -> {"results": [...]}
    POST /api/ask         {"query", "k", "mode", "lang"} -> AskResult
    GET  /api/doc?doc_id= 整页原文（Document.sections 拼简单 HTML）

`GodotRAG` 在第一次真正需要检索时才加载；索引目录里没有 meta.json 时
`/api/health` 返回 ready=false，UI 会提示先运行 build。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import traceback
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_INDEX_DIR = "data/index"
VALID_MODES = ("hybrid", "dense", "bm25", "hybrid+rerank", "dense+rerank")
DEFAULT_MODE = "hybrid"
DEFAULT_K = 8
MIN_K, MAX_K = 1, 20
MAX_QUERY_LEN = 500
MAX_BODY_BYTES = 1 << 20
DOCS_BASE_ENV = "GODOT_RAG_DOCS_BASE_URL"
DEFAULT_DOCS_BASE = "https://docs.godotengine.org/en/stable/"
NOT_READY_MSG = "索引尚未构建，请先运行 python -m godot_rag build"
RAG_MISSING_MSG = "检索模块尚未就绪（godot_rag.GodotRAG 不可用）"

# 允许测试注入 GodotRAG 替身；真实运行时为 None，按需 import。
_RAG_CLASS = None


class NotReadyError(RuntimeError):
    """索引/文档索引尚未就绪（HTTP 503）。"""


class ApiError(RuntimeError):
    """请求参数错误（HTTP 400）。"""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _attr(obj, name: str, default=None):
    """兼容 dataclass 与 dict 两种文档表示。"""
    if isinstance(obj, dict):
        value = obj.get(name, default)
    else:
        value = getattr(obj, name, default)
    return default if value is None else value


def _json_default(value):
    """兼容 numpy 标量等非常规 JSON 类型。"""
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except Exception:
            pass
    if isinstance(value, (set, tuple)):
        return list(value)
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _import_rag_class():
    global _RAG_CLASS
    if _RAG_CLASS is None:
        from godot_rag import GodotRAG  # 延迟导入，避免启动时就加载 torch
        _RAG_CLASS = GodotRAG
    return _RAG_CLASS


class App:
    """服务端共享状态：懒加载 GodotRAG、文档原文缓存、索引就绪探测。"""

    def __init__(self, index_dir=DEFAULT_INDEX_DIR, rag_factory=None, docs_provider=None):
        self.index_dir = Path(index_dir)
        self.meta_path = self.index_dir / "meta.json"
        self._rag = None
        self._rag_factory = rag_factory
        self._rag_lock = threading.Lock()
        self._docs_provider = docs_provider
        self._docs_store = _DocsStore(self.index_dir)

    def read_meta(self) -> dict | None:
        """读取 meta.json；缺失或损坏时返回 None（不触发模型加载）。"""
        try:
            with self.meta_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def ready_info(self) -> tuple[bool, int]:
        meta = self.read_meta()
        if meta is None:
            return False, 0
        try:
            return True, int(meta.get("n_chunks") or meta.get("num_chunks") or 0)
        except (TypeError, ValueError):
            return True, 0

    def get_rag(self):
        if self._rag is not None:
            return self._rag
        with self._rag_lock:
            if self._rag is not None:
                return self._rag
            if self.read_meta() is None:
                raise NotReadyError(NOT_READY_MSG)
            try:
                rag = (self._rag_factory or _import_rag_class())(self.index_dir)
            except ImportError as exc:  # pragma: no cover - 依赖缺失时的兜底
                raise RuntimeError(RAG_MISSING_MSG) from exc
            except Exception as exc:
                raise RuntimeError(f"加载索引失败：{exc.__class__.__name__}") from exc
            if not getattr(rag, "ready", True):
                raise NotReadyError(NOT_READY_MSG)
            self._rag = rag
            return rag

    def get_doc(self, doc_id: str) -> dict | None:
        doc = None
        if self._docs_provider is not None:
            doc = self._docs_provider(doc_id)
        elif self._rag is not None:
            getter = getattr(self._rag, "get_doc", None)
            docs = getattr(self._rag, "docs", None)
            if callable(getter):
                doc = getter(doc_id)
            elif isinstance(docs, dict):
                doc = docs.get(doc_id)
        if doc is None and self._docs_provider is None:
            doc = self._docs_store.get(doc_id)
        return None if doc is None else _doc_payload(doc)


class _DocsStore:
    """懒加载 data/index/docs.pkl，供 /api/doc 使用。"""

    def __init__(self, index_dir: Path):
        self.path = Path(index_dir) / "docs.pkl"
        self._docs = None
        self._lock = threading.Lock()

    def load(self) -> dict:
        with self._lock:
            if self._docs is not None:
                return self._docs
            if not self.path.is_file():
                raise NotReadyError(NOT_READY_MSG)
            try:
                import pickle
                with self.path.open("rb") as handle:
                    docs = pickle.load(handle)
            except Exception as exc:
                raise RuntimeError("读取文档索引失败") from exc
            if not isinstance(docs, dict):
                raise RuntimeError("文档索引格式不正确")
            self._docs = docs
            return docs

    def get(self, doc_id: str):
        return self.load().get(doc_id)


# ---------------------------------------------------------------------------
# 文档 -> 简单 HTML
# ---------------------------------------------------------------------------
def _doc_payload(doc) -> dict:
    doc_id = str(_attr(doc, "doc_id", ""))
    title = str(_attr(doc, "title", "") or doc_id)
    breadcrumbs = [str(item) for item in (_attr(doc, "breadcrumbs", []) or [])]
    sections = _attr(doc, "sections", []) or []
    return {
        "doc_id": doc_id,
        "title": title,
        "url": str(_attr(doc, "url", "") or ""),
        "breadcrumbs": breadcrumbs,
        "html_text": _render_document(title, breadcrumbs, sections),
    }


def _render_document(title: str, breadcrumbs: list[str], sections: list) -> str:
    """把 Document.sections 拼成转义后的简单 HTML（不执行任何脚本）。"""
    parts = ['<article class="doc-page">']
    if breadcrumbs:
        parts.append(f'<p class="doc-crumbs">{escape(" / ".join(breadcrumbs))}</p>')
    parts.append(f"<h2>{escape(title)}</h2>")
    for section in sections:
        heading = str(_attr(section, "title", "") or "").strip()
        if heading:
            try:
                level = int(_attr(section, "level", 3) or 3)
            except (TypeError, ValueError):
                level = 3
            tag = f"h{min(max(level + 1, 3), 6)}"
            anchor = str(_attr(section, "anchor", "") or "")
            anchor_attr = f' id="{escape(anchor)}"' if anchor else ""
            parts.append(f"<{tag}{anchor_attr}>{escape(heading)}</{tag}>")
        for block in _attr(section, "blocks", []) or []:
            kind = str(_attr(block, "kind", "para") or "para")
            text = str(_attr(block, "text", "") or "")
            if not text.strip():
                continue
            if kind in ("code", "sourcecode"):
                lang = str(_attr(block, "lang", "") or "")
                cls = f' class="language-{escape(lang)}"' if lang else ""
                parts.append(f"<pre><code{cls}>{escape(text)}</code></pre>")
            elif kind in ("note", "warning", "tip", "seealso", "admonition"):
                parts.append(f'<blockquote class="doc-note doc-note-{escape(kind)}">'
                             f"{escape(text)}</blockquote>")
            elif kind == "image":
                parts.append(f'<p class="doc-image">[图片] {escape(text)}</p>')
            else:
                parts.append(f"<p>{escape(text)}</p>")
    parts.append("</article>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 请求参数校验
# ---------------------------------------------------------------------------
def _validate_query(data: dict) -> str:
    query = data.get("query")
    if not isinstance(query, str):
        raise ApiError(400, "query 必须是字符串")
    query = query.strip()
    if not query:
        raise ApiError(400, "query 不能为空")
    if len(query) > MAX_QUERY_LEN:
        raise ApiError(400, f"query 过长（最多 {MAX_QUERY_LEN} 字符）")
    return query


def _validate_k(data: dict) -> int:
    k = data.get("k", DEFAULT_K)
    if isinstance(k, bool) or not isinstance(k, int):
        raise ApiError(400, "k 必须是整数")
    if not MIN_K <= k <= MAX_K:
        raise ApiError(400, f"k 必须在 {MIN_K} 到 {MAX_K} 之间")
    return k


def _validate_mode(data: dict) -> str:
    mode = data.get("mode", DEFAULT_MODE)
    if not isinstance(mode, str) or mode not in VALID_MODES:
        raise ApiError(400, "mode 必须是以下之一：" + " / ".join(VALID_MODES))
    return mode


def _validate_lang(data: dict) -> str | None:
    lang = data.get("lang")
    if lang is not None and not isinstance(lang, str):
        raise ApiError(400, "lang 必须是字符串或 null")
    return lang or None


# ---------------------------------------------------------------------------
# HTTP 请求处理
# ---------------------------------------------------------------------------
class _Handler(BaseHTTPRequestHandler):
    server_version = "GodotRAG/1.0"
    sys_version = ""

    @property
    def app(self) -> App:
        return self.server.app  # type: ignore[attr-defined]

    def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=_json_default).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _send_html(self, status: int, html: str) -> None:
        self._send_bytes(status, html.encode("utf-8"), "text/html; charset=utf-8")

    def log_message(self, fmt: str, *args) -> None:
        if not getattr(self.server, "quiet", False):
            sys.stderr.write("[godot-rag] %s\n" % (fmt % args))

    def do_GET(self) -> None:  # noqa: N802
        try:
            parsed = urlsplit(self.path)
            path = parsed.path.rstrip("/") or "/"
            if path in ("/", "/index.html"):
                self._send_html(200, render_ui())
            elif path == "/api/health":
                ready, n_chunks = self.app.ready_info()
                self._send_json(200, {"ok": True, "ready": ready, "n_chunks": n_chunks})
            elif path == "/api/stats":
                self._send_json(200, self.app.get_rag().stats())
            elif path == "/api/doc":
                self._handle_doc(parsed.query)
            else:
                self._send_json(404, {"error": "未找到该接口：" + path})
        except ApiError as exc:
            self._send_json(exc.status, {"error": exc.message})
        except NotReadyError as exc:
            self._send_json(503, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            self._log_exception()
            self._send_json(500, {"error": "服务内部错误，请查看服务端日志"})

    def do_POST(self) -> None:  # noqa: N802
        try:
            path = urlsplit(self.path).path.rstrip("/") or "/"
            if path == "/api/search":
                self._handle_search()
            elif path == "/api/ask":
                self._handle_ask()
            else:
                self._send_json(404, {"error": "未找到该接口：" + path})
        except ApiError as exc:
            self._send_json(exc.status, {"error": exc.message})
        except NotReadyError as exc:
            self._send_json(503, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            self._log_exception()
            self._send_json(500, {"error": "服务内部错误，请查看服务端日志"})

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError(400, "Content-Length 不合法")
        if length <= 0:
            raise ApiError(400, "请求体不能为空")
        if length > MAX_BODY_BYTES:
            raise ApiError(400, "请求体过大")
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ApiError(400, "请求体不是合法的 JSON")
        if not isinstance(data, dict):
            raise ApiError(400, "请求体必须是 JSON 对象")
        return data

    def _handle_search(self) -> None:
        data = self._read_json()
        results = self.app.get_rag().search(
            _validate_query(data), k=_validate_k(data), mode=_validate_mode(data)
        )
        self._send_json(200, {"results": list(results or [])})

    def _handle_ask(self) -> None:
        data = self._read_json()
        result = self.app.get_rag().ask(
            _validate_query(data),
            k=_validate_k(data),
            mode=_validate_mode(data),
            lang=_validate_lang(data),
        )
        self._send_json(200, result)

    def _handle_doc(self, raw_query: str) -> None:
        values = parse_qs(raw_query, keep_blank_values=True).get("doc_id") or []
        doc_id = values[0].strip() if values else ""
        if not doc_id:
            raise ApiError(400, "缺少 doc_id 参数")
        if len(doc_id) > 300:
            raise ApiError(400, "doc_id 过长")
        payload = self.app.get_doc(doc_id)
        if payload is None:
            self._send_json(404, {"error": "未找到文档：" + doc_id})
        else:
            self._send_json(200, payload)

    def _log_exception(self) -> None:
        sys.stderr.write("[godot-rag] 请求处理异常：\n")
        traceback.print_exc()


# ---------------------------------------------------------------------------
# 单页 UI（内联 CSS/JS，无外链依赖；`__DOCS_BASE__` 为文档站前缀）
# ---------------------------------------------------------------------------
_UI_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Godot 官方文档检索</title>
<style>
:root{--bg:#0e1219;--bg-soft:#151b25;--card:#1a2231;--border:#2a3547;--text:#e9eef7;--muted:#93a2ba;--accent:#5aa9ff;--accent2:#7ee0b8;--mark:#ffd76a;--danger:#ff9d9d}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;color:var(--text);line-height:1.65;font-family:"Segoe UI","Microsoft YaHei",system-ui,-apple-system,sans-serif;background:radial-gradient(1100px 520px at 15% -12%,#1c2e49 0%,var(--bg) 58%) no-repeat}
.page{max-width:1040px;margin:0 auto;padding:34px 20px 64px}
.hero{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:22px}
.brand{display:flex;align-items:center;gap:14px;min-width:0}
.logo{width:46px;height:46px;border-radius:13px;display:grid;place-items:center;font-size:22px;background:linear-gradient(140deg,#2f6fd0,#1d3f6e);box-shadow:0 6px 22px #0a1220}
h1{margin:0;font-size:22px;letter-spacing:.3px}
.sub{margin:2px 0 0;color:var(--muted);font-size:13px}
.badge{font-size:12px;padding:5px 11px;border-radius:999px;border:1px solid var(--border);color:var(--muted);white-space:nowrap}
.badge-ok{color:var(--accent2);border-color:#2f5f52;background:#12241f}
.badge-bad{color:var(--danger);border-color:#5f2f2f;background:#241414}
.banner{border:1px solid #6b4a1f;background:#2a2012;color:#ffd9a0;border-radius:12px;padding:12px 15px;margin-bottom:16px;font-size:14px}
.banner code{background:#3a2c16;padding:1px 6px;border-radius:5px}
.hidden{display:none}
.searchbar{display:flex;gap:10px;flex-wrap:nowrap;align-items:stretch}
.searchbar input{flex:1 1 340px;min-width:0;font-size:15px;padding:13px 15px;border-radius:12px;border:1px solid var(--border);background:var(--bg-soft);color:var(--text);outline:none}
.searchbar input:focus{border-color:var(--accent);box-shadow:0 0 0 3px #1b3a5e}
.searchbar select{font-size:14px;padding:0 12px;border-radius:12px;border:1px solid var(--border);background:var(--bg-soft);color:var(--text);outline:none;max-width:190px}
.searchbar button{font-size:15px;font-weight:600;padding:0 26px;border:0;border-radius:12px;cursor:pointer;color:#06121f;background:linear-gradient(135deg,#7fc0ff,#5aa9ff);transition:filter .15s,opacity .15s}
.searchbar button:hover{filter:brightness(1.08)}
.searchbar button:disabled{opacity:.55;cursor:progress}
.meta{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;margin:12px 2px 18px;color:var(--muted);font-size:13px}
#elapsed{color:var(--accent2)}
.card{background:linear-gradient(180deg,var(--card),var(--bg-soft));border:1px solid var(--border);border-radius:14px;padding:16px 18px;margin-bottom:14px;box-shadow:0 4px 18px #060a11a0}
.card-head{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.card h2{margin:0;font-size:17px;line-height:1.35}
.rank{flex:0 0 auto;min-width:26px;height:26px;padding:0 6px;border-radius:8px;display:inline-grid;place-items:center;font-size:12px;color:#0b1726;background:var(--accent2);font-weight:700}
.crumbs{color:var(--muted);font-size:12px;margin:5px 0 8px;word-break:break-word}
.snippet{margin:0;font-size:14px;color:#cfdaea;white-space:pre-wrap;word-break:break-word}
mark{background:var(--mark);color:#211a05;border-radius:3px;padding:0 2px}
.card-foot{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-top:11px;font-size:12px;color:var(--muted)}
.card-foot a{color:var(--accent);text-decoration:none}
.card-foot a:hover{text-decoration:underline}
.ghost{font-size:12px;padding:5px 12px;border-radius:8px;border:1px solid var(--border);background:#131a25;color:#b9c6da;cursor:pointer}
.ghost:hover{border-color:var(--accent);color:var(--accent)}
.ghost:disabled{opacity:.5;cursor:progress}
.doc-panel{margin-top:14px;border-top:1px dashed var(--border);padding-top:14px;font-size:14px;color:#cbd6e6}
.doc-page p,.doc-page blockquote{white-space:pre-wrap;margin:8px 0}
.doc-page h2{font-size:19px;margin:4px 0 10px}
.doc-page h3,.doc-page h4,.doc-page h5{font-size:15px;color:var(--accent);margin:14px 0 6px}
.doc-page .doc-crumbs{color:var(--muted);font-size:12px;margin:0 0 6px}
.doc-page pre{background:#0b1018;border:1px solid var(--border);border-radius:10px;padding:12px;overflow:auto;font-size:13px;line-height:1.5}
.doc-page code{font-family:Consolas,"Cascadia Mono",monospace}
.doc-note{border-left:3px solid var(--accent);background:#131c29;padding:8px 12px;border-radius:0 8px 8px 0;color:#b9c9de}
.doc-image{color:var(--muted)}
.empty{padding:38px 20px;text-align:center;color:var(--muted);border:1px dashed var(--border);border-radius:14px}
.foot{margin-top:26px;color:#6d7c93;font-size:12px;text-align:center}
.loading .card,.loading .empty{opacity:.55}
@media (max-width:900px){.page{padding:22px 14px 48px}.hero{align-items:flex-start}h1{font-size:19px}.searchbar{flex-wrap:wrap}.searchbar input{flex:1 1 100%;order:-1}.searchbar select{flex:1 1 140px;padding:11px 12px;max-width:none}.searchbar button{flex:1 1 120px;padding:12px}.card{padding:14px}}
@media (max-width:520px){.logo{width:38px;height:38px;font-size:18px}.sub{font-size:12px}.card h2{font-size:16px}.card-foot{gap:10px}}
</style>
</head>
<body>
<div class="page">
  <header class="hero">
    <div class="brand"><span class="logo" aria-hidden="true">◆</span>
      <div><h1>Godot 官方文档检索</h1><p class="sub">Godot 4.x stable · 本地 RAG 索引 · 结果可溯源</p></div>
    </div>
    <span id="health" class="badge">连接中…</span>
  </header>
  <div id="banner" class="banner hidden" role="alert"></div>
  <form id="search-form" class="searchbar" autocomplete="off">
    <input id="query" type="search" maxlength="500" required placeholder="输入问题或 API 名称，例如：add_child、如何释放节点、信号怎么连接">
    <select id="mode" title="检索模式">
      <option value="hybrid" selected>hybrid（混合）</option><option value="dense">dense（向量）</option>
      <option value="bm25">bm25（关键词）</option><option value="hybrid+rerank">hybrid+rerank（重排）</option>
    </select>
    <select id="k" title="返回条数">
      <option value="3">3 条</option><option value="5">5 条</option><option value="8" selected>8 条</option>
      <option value="10">10 条</option><option value="15">15 条</option><option value="20">20 条</option>
    </select>
    <button id="submit" type="submit">搜索</button>
  </form>
  <div class="meta"><span id="status">输入关键词后按回车或点击“搜索”</span><span id="elapsed"></span></div>
  <main id="results" aria-live="polite"></main>
  <footer class="foot">结果链接指向 Godot 官方文档 · 索引规模见 <code>/api/stats</code></footer>
</div>
<noscript><p style="text-align:center;color:#ff9d9d">本页面需要启用 JavaScript。</p></noscript>
<script>
(function () {
  "use strict";
  var DOCS_BASE = __DOCS_BASE__;
  var form = document.getElementById("search-form"), input = document.getElementById("query");
  var modeSel = document.getElementById("mode"), kSel = document.getElementById("k");
  var submitBtn = document.getElementById("submit"), resultsEl = document.getElementById("results");
  var statusEl = document.getElementById("status"), elapsedEl = document.getElementById("elapsed");
  var healthEl = document.getElementById("health"), bannerEl = document.getElementById("banner");

  function showBanner(text) { bannerEl.textContent = text; bannerEl.classList.remove("hidden"); }
  function hideBanner() { bannerEl.classList.add("hidden"); }
  function setHealth(ready, text) {
    healthEl.className = "badge " + (ready ? "badge-ok" : "badge-bad");
    healthEl.textContent = text;
  }
  function setLoading(on) {
    submitBtn.disabled = on;
    submitBtn.textContent = on ? "检索中…" : "搜索";
    document.body.classList.toggle("loading", on);
  }
  function escapeRegExp(text) { return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }
  function highlightInto(el, text, query) {
    var terms = [];
    query.toLowerCase().split(/\s+/).forEach(function (t) {
      if (t.length >= 2 && terms.indexOf(t) === -1) terms.push(t);
    });
    terms = terms.slice(0, 12);
    if (!terms.length) { el.textContent = text; return; }
    var re = new RegExp("(" + terms.map(escapeRegExp).join("|") + ")", "gi");
    var last = 0, match;
    while ((match = re.exec(text)) !== null) {
      if (match.index > last) el.appendChild(document.createTextNode(text.slice(last, match.index)));
      var mark = document.createElement("mark");
      mark.textContent = match[0];
      el.appendChild(mark);
      last = match.index + match[0].length;
      if (match[0].length === 0) re.lastIndex += 1;
    }
    if (last < text.length) el.appendChild(document.createTextNode(text.slice(last)));
  }
  function docUrl(url) {
    if (!url) return DOCS_BASE;
    if (/^https?:/i.test(url)) return url;
    var parts = String(url).split("#");
    var path = parts[0].split("/").map(encodeURIComponent).join("/");
    var anchor = parts.length > 1 ? "#" + encodeURIComponent(parts.slice(1).join("#")) : "";
    return DOCS_BASE + path + anchor;
  }
  function toggleDoc(button, panel, docId) {
    if (panel.dataset.loaded === "1") {
      var visible = !panel.classList.contains("hidden");
      panel.classList.toggle("hidden", visible);
      button.textContent = visible ? "查看整页原文" : "收起原文";
      return;
    }
    button.disabled = true;
    button.textContent = "加载中…";
    fetch("/api/doc?doc_id=" + encodeURIComponent(docId))
      .then(function (resp) {
        return resp.json().catch(function () { return {}; })
          .then(function (data) {
            if (!resp.ok) throw new Error(data.error || "加载原文失败");
            return data;
          });
      })
      .then(function (data) {
        panel.innerHTML = data.html_text || "<p>该文档没有可显示的正文。</p>";
        var link = document.createElement("p");
        link.innerHTML = '<a href="' + docUrl(data.url) + '" target="_blank" rel="noopener">在官方文档中打开 ↗</a>';
        panel.appendChild(link);
        panel.dataset.loaded = "1";
        panel.classList.remove("hidden");
        button.textContent = "收起原文";
      })
      .catch(function (err) { showBanner(err.message || "加载原文失败，请稍后重试"); button.textContent = "查看整页原文"; })
      .then(function () { button.disabled = false; });
  }
  function renderResults(results, query) {
    resultsEl.innerHTML = "";
    if (!results.length) {
      var empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "没有找到结果。试试更具体的英文 API 名称，或更短的查询。";
      resultsEl.appendChild(empty);
      return;
    }
    results.forEach(function (item) {
      var card = document.createElement("article");
      card.className = "card";
      var head = document.createElement("div");
      head.className = "card-head";
      var rank = document.createElement("span");
      rank.className = "rank";
      rank.textContent = "#" + (item.rank != null ? item.rank : "");
      var title = document.createElement("h2");
      title.textContent = item.title || item.doc_id || "未命名文档";
      head.appendChild(rank);
      head.appendChild(title);
      card.appendChild(head);
      var crumbs = document.createElement("p");
      crumbs.className = "crumbs";
      crumbs.textContent = (item.breadcrumbs || []).join(" / ") || (item.doc_type || "");
      card.appendChild(crumbs);
      var snippet = document.createElement("p");
      snippet.className = "snippet";
      var text = String(item.text || "");
      highlightInto(snippet, text.length > 420 ? text.slice(0, 420) + "…" : text, query);
      card.appendChild(snippet);
      var foot = document.createElement("div");
      foot.className = "card-foot";
      var score = document.createElement("span");
      score.textContent = "相关度 " + (typeof item.score === "number" ? item.score.toFixed(3) : "-");
      var source = document.createElement("a");
      source.href = docUrl(item.url);
      source.target = "_blank";
      source.rel = "noopener";
      source.textContent = "来源链接 ↗";
      var docBtn = document.createElement("button");
      docBtn.type = "button";
      docBtn.className = "ghost";
      docBtn.textContent = "查看整页原文";
      var panel = document.createElement("div");
      panel.className = "doc-panel hidden";
      docBtn.addEventListener("click", function () { toggleDoc(docBtn, panel, item.doc_id); });
      foot.appendChild(score);
      foot.appendChild(source);
      foot.appendChild(docBtn);
      card.appendChild(foot);
      card.appendChild(panel);
      resultsEl.appendChild(card);
    });
  }
  function runSearch() {
    var query = input.value.trim();
    if (!query) { showBanner("请输入查询内容"); input.focus(); return; }
    if (query.length > 500) { showBanner("查询内容过长（最多 500 字符）"); return; }
    hideBanner();
    setLoading(true);
    statusEl.textContent = "正在检索…";
    elapsedEl.textContent = "";
    resultsEl.innerHTML = "";
    var started = performance.now();
    fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: query, k: Number(kSel.value), mode: modeSel.value })
    }).then(function (resp) {
      return resp.json().catch(function () { return {}; }).then(function (data) {
        if (!resp.ok) throw new Error(data.error || ("请求失败（HTTP " + resp.status + "）"));
        return data;
      });
    }).then(function (data) {
      var elapsed = performance.now() - started;
      var items = data.results || [];
      renderResults(items, query);
      statusEl.textContent = "“" + query + "” · " + modeSel.value + " · 共 " + items.length + " 条结果";
      elapsedEl.textContent = "检索耗时 " + elapsed.toFixed(0) + " ms";
    }).catch(function (err) {
      showBanner(err.message || "检索失败，请稍后重试");
      statusEl.textContent = "检索失败";
    }).then(function () { setLoading(false); });
  }
  form.addEventListener("submit", function (event) { event.preventDefault(); runSearch(); });
  fetch("/api/health").then(function (resp) { return resp.json(); }).then(function (data) {
    if (data.ready) {
      setHealth(true, "索引就绪 · " + (data.n_chunks || 0) + " 个片段");
    } else {
      setHealth(false, "索引未就绪");
      showBanner("索引尚未构建，请先在项目根目录运行 python -m godot_rag build");
    }
  }).catch(function () {
    setHealth(false, "服务不可用");
    showBanner("无法连接检索服务，请确认服务已启动。");
  });
})();
</script>
</body>
</html>
"""


def render_ui() -> str:
    """渲染单页 UI，文档站前缀可在运行时通过环境变量覆盖。"""
    base = (os.environ.get(DOCS_BASE_ENV, "").strip() or DEFAULT_DOCS_BASE).rstrip("/") + "/"
    return _UI_TEMPLATE.replace("__DOCS_BASE__", json.dumps(base))


# ---------------------------------------------------------------------------
# 启动入口
# ---------------------------------------------------------------------------
def create_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    index_dir=DEFAULT_INDEX_DIR,
    rag_factory=None,
    docs_provider=None,
    quiet: bool = False,
) -> ThreadingHTTPServer:
    """创建（但不启动）HTTP 服务，测试可直接用 port=0 取随机端口。"""
    app = App(index_dir=index_dir, rag_factory=rag_factory, docs_provider=docs_provider)
    httpd = ThreadingHTTPServer((host, port), _Handler)
    httpd.daemon_threads = True
    httpd.block_on_close = False  # 测试里 shutdown 后不必等待残留线程
    httpd.app = app  # type: ignore[attr-defined]
    httpd.quiet = quiet  # type: ignore[attr-defined]
    return httpd


def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, index_dir=DEFAULT_INDEX_DIR) -> None:
    """启动服务并阻塞运行，Ctrl+C 优雅退出。"""
    httpd = create_server(host=host, port=port, index_dir=index_dir)
    bound_host, bound_port = httpd.server_address[:2]
    display_host = "127.0.0.1" if bound_host in ("0.0.0.0", "::") else bound_host
    print(f"Godot RAG 文档检索服务已启动：http://{display_host}:{bound_port}/", flush=True)
    print("索引目录：%s（按 Ctrl+C 停止服务）" % Path(index_dir).resolve(), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止服务…", flush=True)
    finally:
        httpd.server_close()
