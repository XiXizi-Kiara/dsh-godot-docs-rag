"""纯 Python BM25 倒排索引（Okapi BM25）。

- 分词保留 snake_case / 数字：`add_child`、`queue_free`、`characterbody2d`。
- 额外拆分驼峰与下划线子词，兼顾 "character body" 这类自然语言查询。
- 倒排用 array 压缩，40k chunk 规模下内存约几十 MB。
"""
from __future__ import annotations

import pickle
import re
from array import array
from collections import Counter

_TOKEN_RE = re.compile(r"[a-z0-9_]+")
_CAMEL_RE = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")
_TYPE_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


def tokenize(text: str) -> list[str]:
    """分词：小写整词 + 驼峰/下划线子词。"""
    low = text.lower()
    tokens: list[str] = []
    for tok in _TOKEN_RE.findall(low):
        tokens.append(tok)
        if "_" in tok:
            tokens.extend(p for p in tok.split("_") if p)
    # 驼峰子词（从原文取，避免全小写丢失边界）
    for tok in _TYPE_TOKEN_RE.findall(text):
        parts = _CAMEL_RE.findall(tok)
        if len(parts) > 1:
            tokens.extend(p.lower() for p in parts if len(p) > 1)
    return tokens


class BM25Index:
    """倒排 BM25 索引。"""

    def __init__(
        self,
        postings: dict[str, tuple[array, array]],
        doc_len: array,
        avgdl: float,
        n_docs: int,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        self.postings = postings
        self.doc_len = doc_len
        self.avgdl = avgdl or 1.0
        self.n_docs = n_docs
        self.k1 = k1
        self.b = b
        # 预计算 idf
        self.idf: dict[str, float] = {}
        for term, (ids, _tfs) in postings.items():
            df = len(ids)
            self.idf[term] = float(
                __import__("math").log(1.0 + (n_docs - df + 0.5) / (df + 0.5))
            )

    # -- 构建 -------------------------------------------------------------
    @classmethod
    def build(cls, texts: list[str], k1: float = 1.5, b: float = 0.75) -> "BM25Index":
        n = len(texts)
        doc_len = array("I", [0]) * n
        raw: dict[str, list[int]] = {}
        for i, text in enumerate(texts):
            toks = tokenize(text)
            doc_len[i] = len(toks)
            for term, tf in Counter(toks).items():
                bucket = raw.get(term)
                if bucket is None:
                    raw[term] = bucket = []
                # 每个 (term, doc) 存一次：用扩展序列记录 (doc, tf) 对
                bucket.append(i)
                bucket.append(tf)
        postings: dict[str, tuple[array, array]] = {}
        for term, flat in raw.items():
            ids = array("I", flat[0::2])
            tfs = array("H", flat[1::2])
            postings[term] = (ids, tfs)
        total = sum(doc_len)
        avgdl = total / n if n else 0.0
        return cls(postings, doc_len, avgdl, n, k1=k1, b=b)

    # -- 检索 -------------------------------------------------------------
    def search(self, query: str, top_k: int = 50) -> list[tuple[int, float]]:
        """返回 [(doc_idx, score)]，按分数降序。"""
        q_tokens = tokenize(query)
        if not q_tokens:
            return []
        scores: dict[int, float] = {}
        k1, b, avgdl = self.k1, self.b, self.avgdl
        for term in set(q_tokens):
            post = self.postings.get(term)
            if not post:
                continue
            ids, tfs = post
            idf = self.idf[term]
            for di, tf in zip(ids, tfs):
                dl = self.doc_len[di]
                denom = tf + k1 * (1.0 - b + b * dl / avgdl)
                scores[di] = scores.get(di, 0.0) + idf * tf * (k1 + 1.0) / denom
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
        return ranked

    # -- 持久化 -----------------------------------------------------------
    def save(self, path: str) -> None:
        with open(path, "wb") as f:
            pickle.dump(
                {
                    "postings": self.postings,
                    "doc_len": self.doc_len,
                    "avgdl": self.avgdl,
                    "n_docs": self.n_docs,
                    "k1": self.k1,
                    "b": self.b,
                },
                f,
                protocol=pickle.HIGHEST_PROTOCOL,
            )

    @classmethod
    def load(cls, path: str) -> "BM25Index":
        with open(path, "rb") as f:
            data = pickle.load(f)
        return cls(
            data["postings"],
            data["doc_len"],
            data["avgdl"],
            data["n_docs"],
            k1=data.get("k1", 1.5),
            b=data.get("b", 0.75),
        )
