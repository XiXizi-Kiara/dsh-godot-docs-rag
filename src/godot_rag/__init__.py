"""Godot 官方文档 RAG 引擎。

用法：
    from godot_rag import GodotRAG
    rag = GodotRAG("data/index")
    results = rag.search("how to free a node", k=8)
    answer = rag.ask("queue_free 和 free 有什么区别")
"""
from __future__ import annotations

from .build import build_index
from .retrieve import GodotRAG, IndexNotReady

__version__ = "0.1.0"
__all__ = ["GodotRAG", "IndexNotReady", "build_index", "__version__"]
