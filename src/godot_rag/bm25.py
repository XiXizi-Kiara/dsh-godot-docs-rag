"""BM25 词法检索：基于 rank_bm25，保留 Godot 符号分词策略。

- 保留 snake_case 整体（add_child、queue_free）并拆分驼峰（CharacterBody2D）
- rank_bm25 的 BM25Okapi 用 numpy 向量化打分，26076 文档查询约 10-20 ms
"""
from __future__ import annotations

import pickle
import re

import numpy as np
from rank_bm25 import BM25Okapi

_TOKEN_RE = re.compile(r"[a-z0-9_]+")
_CAMEL_RE = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")
_TYPE_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


def tokenize(text: str) -> list[str]:
    """分词：小写整词 + 下划线子词 + 驼峰子词。"""
    low = text.lower()
    tokens: list[str] = []
    for tok in _TOKEN_RE.findall(low):
        tokens.append(tok)
        if "_" in tok:
            tokens.extend(p for p in tok.split("_") if p)
    for tok in _TYPE_TOKEN_RE.findall(text):
        parts = _CAMEL_RE.findall(tok)
        if len(parts) > 1:
            tokens.extend(p.lower() for p in parts if len(p) > 1)
    return tokens


class BM25Index:
    """BM25 索引包装（rank_bm25.BM25Okapi）。"""

    def __init__(self, bm25: BM25Okapi, n_docs: int, k1: float = 1.5, b: float = 0.75) -> None:
        self.bm25 = bm25
        self.n_docs = n_docs
        self.k1 = k1
        self.b = b

    @classmethod
    def build(cls, texts: list[str], k1: float = 1.5, b: float = 0.75) -> "BM25Index":
        corpus = [tokenize(t) or ["<empty>"] for t in texts]
        return cls(BM25Okapi(corpus, k1=k1, b=b), len(texts), k1=k1, b=b)

    def search(self, query: str, top_k: int = 50) -> list[tuple[int, float]]:
        """返回 [(doc_idx, score)]，按分数降序，只保留正分。"""
        q_tokens = tokenize(query)
        if not q_tokens:
            return []
        scores = self.bm25.get_scores(q_tokens)
        n = len(scores)
        if n == 0:
            return []
        top = min(top_k, n)
        idx = np.argpartition(-scores, top - 1)[:top]
        idx = idx[np.argsort(-scores[idx])]
        return [(int(i), float(scores[i])) for i in idx if scores[i] > 0]

    # -- 持久化 -----------------------------------------------------------
    def save(self, path: str) -> None:
        with open(path, "wb") as f:
            pickle.dump(
                {"bm25": self.bm25, "n_docs": self.n_docs, "k1": self.k1, "b": self.b},
                f,
                protocol=pickle.HIGHEST_PROTOCOL,
            )

    @classmethod
    def load(cls, path: str) -> "BM25Index":
        with open(path, "rb") as f:
            data = pickle.load(f)
        return cls(
            data["bm25"],
            data["n_docs"],
            data.get("k1", 1.5),
            data.get("b", 0.75),
        )
