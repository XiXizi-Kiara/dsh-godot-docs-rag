"""DSH 插件 ↔ GodotRAG 的 JSON Lines 桥。

协议：stdin/stdout 每行一个 JSON 对象。
请求  {"id": 1, "method": "search", "params": {...}}
响应  {"id": 1, "ok": true, "result": {...}, "meta": {...}}
错误  {"id": 1, "ok": false, "error": {"code","message","hint"}}

stdout 只写协议；日志一律写 stderr。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import __version__
from . import agent_api

PROTOCOL = 1
MAX_LINE_BYTES = 8 * 1024 * 1024


class BridgeState:
    def __init__(
        self,
        index_dir: str | Path | None = None,
        device: str | None = None,
        docs_base: str | None = None,
    ) -> None:
        self.index_dir = index_dir or os.environ.get("GODOT_RAG_INDEX_DIR")
        self.device = device
        self.docs_base = docs_base or os.environ.get("GODOT_RAG_DOCS_BASE_URL")
        self.rag = None


def _get_rag(state: BridgeState):
    if state.rag is None:
        from .retrieve import GodotRAG

        state.rag = GodotRAG(state.index_dir, device=state.device)
    return state.rag


def _index_dir(state: BridgeState) -> Path:
    from . import config

    return Path(state.index_dir or config.INDEX_DIR)


def _meta(state: BridgeState) -> dict:
    directory = _index_dir(state)
    meta_path = directory / "meta.json"
    n_chunks = None
    index_mtime = None
    if meta_path.is_file():
        try:
            n_chunks = json.loads(meta_path.read_text(encoding="utf-8")).get("n_chunks")
        except Exception:  # noqa: BLE001 - meta 只用于诊断
            n_chunks = None
        from datetime import datetime

        index_mtime = (
            datetime.fromtimestamp(meta_path.stat().st_mtime).astimezone().isoformat(timespec="seconds")
        )
    rag = state.rag
    return {
        "pid": os.getpid(),
        "version": __version__,
        "device": getattr(rag, "device", None) or (state.device or "auto"),
        "index_dir": str(directory),
        "index_mtime": index_mtime,
        "n_chunks": n_chunks,
    }


def _ok(rid, result, state: BridgeState) -> dict:
    return {"id": rid, "ok": True, "result": result, "meta": _meta(state)}


def _err(rid, error: dict) -> dict:
    return {"id": rid, "ok": False, "error": error}


def handle(request: dict, state: BridgeState) -> dict:
    rid = request.get("id")
    method = request.get("method")
    params = request.get("params") or {}
    if not isinstance(params, dict):
        return _err(rid, {"code": "BAD_PARAMS", "message": "params 必须是对象", "hint": ""})
    try:
        if method == "ping":
            return _ok(
                rid,
                {
                    "protocol": PROTOCOL,
                    "version": __version__,
                    "index_ready": (_index_dir(state) / "meta.json").is_file(),
                    "index_dir": str(_index_dir(state)),
                },
                state,
            )
        if method == "warmup":
            rag = _get_rag(state)
            started = time.perf_counter()
            rag.search("godot", k=1)
            return _ok(
                rid,
                {
                    "loaded": True,
                    "load_ms": round((time.perf_counter() - started) * 1000),
                    "device": getattr(rag, "device", "auto"),
                },
                state,
            )
        if method == "search":
            rag = _get_rag(state)
            payload = agent_api.search_payload(
                rag,
                query=params.get("query", ""),
                k=int(params.get("k", 5)),
                mode=str(params.get("mode", "hybrid")),
                base=state.docs_base,
            )
            return _ok(rid, payload, state)
        if method == "find_symbol":
            rag = _get_rag(state)
            payload = agent_api.find_symbol_payload(
                rag,
                name=params.get("name", ""),
                class_name=params.get("class_name"),
                kind=params.get("kind"),
                limit=int(params.get("limit", 5)),
                base=state.docs_base,
            )
            return _ok(rid, payload, state)
        if method == "get_doc":
            rag = _get_rag(state)
            payload = agent_api.get_doc_payload(
                rag,
                doc_id=params.get("doc_id", ""),
                offset=int(params.get("offset", 0)),
                limit=int(params.get("limit", 8000)),
                base=state.docs_base,
            )
            return _ok(rid, payload, state)
        if method == "stats":
            rag = _get_rag(state)
            return _ok(rid, agent_api.stats_payload(rag, _index_dir(state)), state)
        if method == "shutdown":
            return _ok(rid, {"stopping": True}, state)
        return _err(rid, {"code": "UNKNOWN_METHOD", "message": f"未知方法：{method}", "hint": ""})
    except Exception as exc:  # noqa: BLE001 - 单个请求失败不能终止进程
        return _err(rid, agent_api.error_payload(exc))


def serve(stdin, stdout, state: BridgeState) -> int:
    for raw in iter(stdin.readline, b""):
        request = None
        if not raw.strip():
            continue
        if len(raw) > MAX_LINE_BYTES:
            stdout.write(
                (json.dumps({"id": None, "ok": False, "error": {"code": "BAD_PARAMS",
                 "message": "请求行超过 8MB", "hint": ""}}, ensure_ascii=False) + "\n").encode("utf-8")
            )
            stdout.flush()
            continue
        try:
            request = json.loads(raw.decode("utf-8"))
            if not isinstance(request, dict):
                raise ValueError("请求必须是对象")
        except (UnicodeDecodeError, ValueError) as exc:
            response = _err(
                None,
                {"code": "BAD_PARAMS", "message": f"无法解析请求：{exc}", "hint": ""},
            )
        else:
            response = handle(request, state)
        stdout.write((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
        stdout.flush()
        if request is not None and request.get("method") == "shutdown":
            break
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="godot_rag.dsh_bridge")
    parser.add_argument("--index-dir", default=None, help="索引目录（默认 data/index）")
    parser.add_argument("--device", default=None, choices=("auto", "cpu", "cuda"))
    parser.add_argument("--docs-base", default=None, help="文档站 URL 前缀（默认官方 stable）")
    args = parser.parse_args(argv)
    state = BridgeState(index_dir=args.index_dir, device=args.device, docs_base=args.docs_base)
    print(f"[bridge] protocol={PROTOCOL} index_dir={_index_dir(state)}", file=sys.stderr, flush=True)
    return serve(sys.stdin.buffer, sys.stdout.buffer, state)


if __name__ == "__main__":
    raise SystemExit(main())
