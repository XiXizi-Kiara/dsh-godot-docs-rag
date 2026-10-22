"""Godot RAG 命令行入口（`python -m godot_rag <子命令>`）。

子命令：build / search / ask / serve / stats / eval。
所有子命令的 `--json` 模式只往 stdout 写 JSON（日志、进度走 stderr），
方便 agent 解析；普通模式输出中文可读文本。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

DEFAULT_INDEX_DIR = "data/index"
MISSING_INDEX_MSG = "索引不存在，请先运行 python -m godot_rag build"
NOT_READY_MSG = "索引尚未就绪，请先运行 python -m godot_rag build"
MODES = ("hybrid", "dense", "bm25", "hybrid+rerank", "dense+rerank")

# 延迟导入并缓存，测试可直接替换这三个全局变量注入假实现。
_RAG_CLASS = None
_BUILD_INDEX = None
_EVAL_MAIN = None


class _IndexUnavailable(RuntimeError):
    """索引不可用（exit code 2）。"""


def _json_default(value):
    """兼容 numpy 标量等非常规 JSON 类型，保证 --json 输出稳定。"""
    item = getattr(value, "item", None)
    if callable(item):  # numpy.float32 / numpy.int64 / torch 标量
        try:
            return item()
        except Exception:
            pass
    if isinstance(value, (set, tuple)):
        return list(value)
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _dumps(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, default=_json_default)


# ---------------------------------------------------------------------------
# 依赖延迟加载（测试可注入）
# ---------------------------------------------------------------------------
def _load_rag_class():
    global _RAG_CLASS
    if _RAG_CLASS is None:
        from godot_rag import GodotRAG  # 避免无索引时也加载模型

        _RAG_CLASS = GodotRAG
    return _RAG_CLASS


def _load_build_index():
    global _BUILD_INDEX
    if _BUILD_INDEX is None:
        from godot_rag.build import build_index

        _BUILD_INDEX = build_index
    return _BUILD_INDEX


def _load_eval_main():
    global _EVAL_MAIN
    if _EVAL_MAIN is None:
        from godot_rag.eval import main as eval_main

        _EVAL_MAIN = eval_main
    return _EVAL_MAIN


def _index_ready(index_dir) -> bool:
    """快速检查索引是否可用：只看 meta.json，避免加载模型。"""
    meta_path = Path(index_dir) / "meta.json"
    if not meta_path.is_file():
        return False
    try:
        with meta_path.open("r", encoding="utf-8") as handle:
            json.load(handle)
    except (OSError, ValueError):
        return False
    return True


def _open_rag(index_dir):
    if not _index_ready(index_dir):
        raise _IndexUnavailable(MISSING_INDEX_MSG)
    try:
        rag_class = _load_rag_class()
    except ImportError as exc:
        raise _IndexUnavailable("RAG 模块尚未就绪（godot_rag.GodotRAG 不可用）") from exc
    try:
        rag = rag_class(index_dir=str(index_dir))
    except Exception as exc:
        raise RuntimeError(f"加载索引失败：{exc.__class__.__name__}: {exc}") from exc
    if not getattr(rag, "ready", True):
        raise _IndexUnavailable(NOT_READY_MSG)
    return rag


# ---------------------------------------------------------------------------
# 输出辅助
# ---------------------------------------------------------------------------
def _ensure_utf8_stdio() -> None:
    """Windows 控制台默认可能是 GBK，显式切到 UTF-8，避免中文输出报错。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


def _fail(message: str, code: int = 1, json_mode: bool = False) -> int:
    """统一错误输出：stderr 给人看；--json 时 stdout 再给一份 JSON（仍是合法 JSON）。"""
    if json_mode:
        print(_dumps({"error": message}))
    print(message, file=sys.stderr)
    return code


def _collapse(text) -> str:
    return " ".join(str(text or "").split())


def _truncate(text, limit: int = 320) -> str:
    text = _collapse(text)
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _format_value(value) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (dict, list, tuple)):
        return _dumps(value)
    return str(value)


_STATS_LABELS = {
    "n_chunks": "片段数",
    "n_docs": "文档数",
    "n_vectors": "向量数",
    "dim": "向量维度",
    "modes": "支持模式",
    "models": "模型",
    "embed_model": "嵌入模型",
    "rerank_model": "重排模型",
    "index_dir": "索引目录",
    "created_at": "构建时间",
    "build_time_s": "构建耗时（秒）",
    "ready": "索引就绪",
}


def _print_stats(stats: dict) -> None:
    print("索引统计：")
    for key, value in stats.items():
        print(f"  {_STATS_LABELS.get(key, key)}: {_format_value(value)}")


# ---------------------------------------------------------------------------
# 子命令实现
# ---------------------------------------------------------------------------
def _cmd_build(args) -> int:
    print("开始构建索引…", file=sys.stderr, flush=True)
    if args.limit:
        print(f"试跑模式：只处理前 {args.limit} 篇文档（写入 data/index_smoke/）", file=sys.stderr)
    try:
        build_index = _load_build_index()
    except ImportError as exc:
        return _fail(f"构建模块尚未就绪：{exc}", 2, args.json)
    try:
        stats = build_index(limit=args.limit, force=args.force)
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        if args.debug:
            traceback.print_exc()
        return _fail(f"构建索引失败：{exc}", 1, args.json)

    if args.json:
        print(_dumps(stats))
    else:
        print("构建完成。")
        if isinstance(stats, dict):
            _print_stats(stats)
        else:
            print(f"  {_format_value(stats)}")
    return 0


def _cmd_search(args) -> int:
    try:
        rag = _open_rag(args.index_dir)
    except _IndexUnavailable as exc:
        return _fail(str(exc), 2, args.json)

    started = time.perf_counter()
    try:
        results = rag.search(args.query, k=args.k, mode=args.mode)
    except Exception as exc:
        if args.debug:
            traceback.print_exc()
        return _fail(f"检索失败：{exc}", 1, args.json)
    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 1)
    results = list(results or [])

    if args.json:
        payload = {
            "query": args.query,
            "mode": args.mode,
            "k": args.k,
            "elapsed_ms": elapsed_ms,
            "results": results,
        }
        print(_dumps(payload))
        return 0

    print(f"查询：{args.query}")
    print(f"模式：{args.mode}    Top-K：{args.k}    耗时：{elapsed_ms:.1f} ms    结果：{len(results)} 条")
    if not results:
        print("\n没有找到相关结果。试试更具体的英文 API 名称或更短的查询。")
        return 0
    print()
    for index, item in enumerate(results, start=1):
        rank = item.get("rank", index)
        title = item.get("title") or item.get("doc_id") or "未命名文档"
        symbol = item.get("symbol") or ""
        score = item.get("score")
        score_text = f"{float(score):.4f}" if isinstance(score, (int, float)) else "-"
        breadcrumbs = " / ".join(item.get("breadcrumbs") or [])
        print(f"[{rank}] 得分 {score_text}  {title}" + (f"  ·  {symbol}" if symbol else ""))
        if breadcrumbs:
            print(f"    路径：{breadcrumbs}")
        print(f"    片段：{_truncate(item.get('text'))}")
        if item.get("url"):
            print(f"    链接：{item['url']}")
        print()
    return 0


def _cmd_ask(args) -> int:
    try:
        rag = _open_rag(args.index_dir)
    except _IndexUnavailable as exc:
        return _fail(str(exc), 2, args.json)

    started = time.perf_counter()
    try:
        result = rag.ask(args.query, k=args.k, mode=args.mode)
    except Exception as exc:
        if args.debug:
            traceback.print_exc()
        return _fail(f"回答失败：{exc}", 1, args.json)
    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 1)

    if args.json:
        print(_dumps(result))
        return 0

    print(f"问题：{args.query}")
    print(f"模式：{args.mode}    耗时：{elapsed_ms:.1f} ms    "
          f"{'使用生成模型' if result.get('llm_used') else '检索式回答'}")
    print("\n回答：")
    print(result.get("answer", ""))
    citations = result.get("citations") or []
    if citations:
        print("\n引用：")
        for citation in citations:
            print(f"  [{citation.get('n')}] {citation.get('title', '')} — {citation.get('url', '')}")
    passages = result.get("passages") or []
    if passages:
        print("\n依据片段：")
        for index, passage in enumerate(passages, start=1):
            title = passage.get("title") or passage.get("doc_id") or ""
            print(f"  [{index}] {title}  {passage.get('url', '')}")
    return 0


def _cmd_stats(args) -> int:
    try:
        rag = _open_rag(args.index_dir)
    except _IndexUnavailable as exc:
        return _fail(str(exc), 2, args.json)
    try:
        stats = rag.stats()
    except Exception as exc:
        if args.debug:
            traceback.print_exc()
        return _fail(f"读取统计失败：{exc}", 1, args.json)
    if args.json:
        print(_dumps(stats))
    else:
        if isinstance(stats, dict):
            _print_stats(stats)
        else:
            print(_format_value(stats))
    return 0


def _cmd_serve(args) -> int:
    backend = getattr(args, "backend", "fastapi")
    try:
        if backend == "fastapi":
            from godot_rag import service_fastapi as service
        else:
            from godot_rag import service
    except ImportError as exc:
        return _fail(f"服务模块尚未就绪：{exc}", 2, False)
    try:
        service.serve(host=args.host, port=args.port, index_dir=args.index_dir)
    except OSError as exc:
        return _fail(f"启动服务失败（端口 {args.port} 可能被占用）：{exc}", 1, False)
    return 0


def _cmd_eval(args) -> int:
    try:
        eval_main = _load_eval_main()
    except ImportError as exc:
        return _fail(f"评测模块尚未就绪：{exc}", 2, args.json)
    argv: list[str] = []
    if args.configs:
        argv += ["--configs", args.configs]
    if args.json:
        argv += ["--json"]
    try:
        result = eval_main(argv)
    except SystemExit as exc:  # eval 自己解析参数时可能退出
        return int(exc.code or 0)
    except Exception as exc:
        if args.debug:
            traceback.print_exc()
        return _fail(f"评测执行失败：{exc}", 1, args.json)
    return int(result or 0)


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m godot_rag",
        description="Godot 官方文档 RAG：构建索引、检索、问答、Web 服务与评测。",
    )
    parser.add_argument("--debug", action="store_true", help="出错时打印完整 traceback")
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="<子命令>")

    build = subparsers.add_parser("build", help="构建检索索引")
    build.add_argument("--limit", type=int, default=None, metavar="N",
                       help="只处理前 N 篇文档（试跑，写入 data/index_smoke/）")
    build.add_argument("--force", action="store_true", help="强制重建已有索引")
    build.add_argument("--json", action="store_true", help="以 JSON 输出统计")
    build.set_defaults(handler=_cmd_build)

    search = subparsers.add_parser("search", help="检索文档片段")
    search.add_argument("query", help="查询词或问题")
    search.add_argument("--k", type=int, default=8, metavar="K", help="返回条数（默认 8）")
    search.add_argument("--mode", default="hybrid", choices=MODES, help="检索模式（默认 hybrid）")
    search.add_argument("--index-dir", default=DEFAULT_INDEX_DIR, help="索引目录（默认 data/index）")
    search.add_argument("--json", action="store_true", help="以 JSON 输出，便于程序解析")
    search.set_defaults(handler=_cmd_search)

    ask = subparsers.add_parser("ask", help="基于检索片段回答问题")
    ask.add_argument("query", help="问题")
    ask.add_argument("--k", type=int, default=8, metavar="K", help="参考片段数（默认 8）")
    ask.add_argument("--mode", default="hybrid", choices=MODES, help="检索模式（默认 hybrid）")
    ask.add_argument("--index-dir", default=DEFAULT_INDEX_DIR, help="索引目录（默认 data/index）")
    ask.add_argument("--json", action="store_true", help="以 JSON 输出 AskResult")
    ask.set_defaults(handler=_cmd_ask)

    serve = subparsers.add_parser("serve", help="启动本地 Web UI 与 HTTP API")
    serve.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    serve.add_argument("--port", type=int, default=8765, help="监听端口（默认 8765）")
    serve.add_argument("--index-dir", default=DEFAULT_INDEX_DIR, help="索引目录（默认 data/index）")
    serve.add_argument(
        "--backend",
        choices=("fastapi", "stdlib"),
        default="fastapi",
        help="HTTP 后端：fastapi（默认）或 stdlib（零依赖标准库实现）",
    )
    serve.set_defaults(handler=_cmd_serve)

    stats = subparsers.add_parser("stats", help="查看索引统计")
    stats.add_argument("--index-dir", default=DEFAULT_INDEX_DIR, help="索引目录（默认 data/index）")
    stats.add_argument("--json", action="store_true", help="以 JSON 输出")
    stats.set_defaults(handler=_cmd_stats)

    eval_parser = subparsers.add_parser("eval", help="运行离线评测")
    eval_parser.add_argument("--configs", default=None,
                             help="逗号分隔的评测配置，如 hybrid,hybrid+rerank")
    eval_parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    eval_parser.set_defaults(handler=_cmd_eval)

    return parser


def main(argv=None) -> int:
    """CLI 入口；返回进程退出码。由 __main__.py 调用。"""
    _ensure_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args) or 0)
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        return 130
    except _IndexUnavailable as exc:
        return _fail(str(exc), 2, getattr(args, "json", False))
    except SystemExit:
        raise
    except Exception as exc:
        if getattr(args, "debug", False):
            traceback.print_exc()
        return _fail(f"命令执行失败：{exc}", 1, getattr(args, "json", False))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
