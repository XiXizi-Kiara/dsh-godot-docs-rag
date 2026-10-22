"""FastAPI + uvicorn 服务（默认后端）。

复用 service.py 的 App（索引懒加载、参数校验、单页 UI、整页原文），只把 HTTP 层
换成 FastAPI：端点、错误码与标准库实现完全一致。标准库实现保留在 service.py，
可用 `serve --backend stdlib` 启动（无第三方依赖场景）。
"""
from __future__ import annotations

import json

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from . import __version__
from . import service as base
from .service import (
    ApiError,
    App,
    NotReadyError,
    _validate_k,
    _validate_lang,
    _validate_mode,
    _validate_query,
    render_ui,
)

DEFAULT_HOST = base.DEFAULT_HOST
DEFAULT_PORT = base.DEFAULT_PORT
DEFAULT_INDEX_DIR = base.DEFAULT_INDEX_DIR


async def _read_json(request: Request) -> dict:
    body = await request.body()
    if not body:
        raise ApiError(400, "请求体不能为空")
    if len(body) > base.MAX_BODY_BYTES:
        raise ApiError(400, "请求体过大")
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ApiError(400, "请求体不是合法的 JSON")
    if not isinstance(data, dict):
        raise ApiError(400, "请求体必须是 JSON 对象")
    return data


def create_app(index_dir=DEFAULT_INDEX_DIR, rag_factory=None, docs_provider=None) -> FastAPI:
    """创建 FastAPI 应用（便于测试注入 fake RAG / 文档源）。"""
    state = App(index_dir=index_dir, rag_factory=rag_factory, docs_provider=docs_provider)
    api = FastAPI(title="Godot 官方文档 RAG", version=__version__)

    @api.exception_handler(ApiError)
    async def _handle_api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content={"error": exc.message})

    @api.exception_handler(NotReadyError)
    async def _handle_not_ready(_request: Request, exc: NotReadyError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"error": str(exc)})

    @api.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return render_ui()

    @api.get("/api/health")
    async def health() -> dict:
        ready, n_chunks = state.ready_info()
        return {"ok": True, "ready": ready, "n_chunks": n_chunks}

    @api.get("/api/stats")
    async def stats() -> dict:
        return state.get_rag().stats()

    @api.post("/api/search")
    async def search(request: Request) -> dict:
        data = await _read_json(request)
        results = state.get_rag().search(
            _validate_query(data), k=_validate_k(data), mode=_validate_mode(data)
        )
        return {"results": list(results or [])}

    @api.post("/api/ask")
    async def ask(request: Request) -> dict:
        data = await _read_json(request)
        return state.get_rag().ask(
            _validate_query(data),
            k=_validate_k(data),
            mode=_validate_mode(data),
            lang=_validate_lang(data),
        )

    @api.get("/api/doc")
    async def doc(doc_id: str = "") -> dict:
        doc_id = (doc_id or "").strip()
        if not doc_id:
            raise ApiError(400, "缺少 doc_id 参数")
        if len(doc_id) > 300:
            raise ApiError(400, "doc_id 过长")
        payload = state.get_doc(doc_id)
        if payload is None:
            raise ApiError(404, "未找到文档：" + doc_id)
        return payload

    return api


def serve(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    index_dir=DEFAULT_INDEX_DIR,
) -> None:
    """启动 FastAPI 服务（uvicorn）。"""
    import uvicorn

    app = create_app(index_dir=index_dir)
    display_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    print(f"Godot RAG（FastAPI）已启动：http://{display_host}:{port}/", flush=True)
    uvicorn.run(app, host=host, port=port, log_level="warning")
