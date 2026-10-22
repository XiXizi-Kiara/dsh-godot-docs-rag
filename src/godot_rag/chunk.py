"""把 Document 切分为可检索的 chunk。

两类策略：

- class 参考页：以符号（method / property / signal / constant / enum）为单位，
  每个符号一个 chunk，前缀标明 "Node.add_child() —— method"，保证精确 API 查询能命中。
- 教程/指南页：按 section 聚合段落，超过上限时按段落窗口切分并保留重叠。

chunk 的 text 都带面包屑前缀（Godot Docs > Class reference > Node > 方法），
这样即使脱离原页面上下文，语义仍然完整。
"""
from __future__ import annotations

import hashlib

from . import config
from .types import Block, Document, Section, estimate_tokens

_CLASS_KINDS_ZH = {
    "method": "method",
    "property": "property",
    "signal": "signal",
    "constant": "constant",
    "enum": "enum",
    "annotation": "annotation",
}
# class 页里这些 section 只有 reftable（信息与描述段重复），不需要单独建块
_SKIP_REFTABLE_SECTIONS = {"Properties", "Methods", "Signals", "Constants", "Enumerations"}


def _format_block(b: Block) -> str:
    if b.kind == "code":
        lang = b.lang or ""
        return f"```{lang}\n{b.text}\n```"
    if b.kind == "symbol":
        return b.text
    if b.kind in {"note", "warning", "tip", "important", "caution", "danger", "seealso"}:
        return f"[{b.kind}] {b.text}"
    return b.text


def _join_crumbs(doc: Document, section_title: str) -> str:
    parts = list(doc.breadcrumbs)
    if section_title and (not parts or parts[-1] != section_title):
        parts.append(section_title)
    return " > ".join(parts)


def _hard_split(text: str, max_tokens: int) -> list[str]:
    """超长单块按句子硬切，尽量在句号/换行处断开。"""
    if estimate_tokens(text) <= max_tokens:
        return [text]
    pieces: list[str] = []
    buf: list[str] = []
    buf_tok = 0
    # 按行/句切
    import re

    sentences = re.split(r"(?<=[.!?])\s+|\n", text)
    for s in sentences:
        if not s:
            continue
        st = estimate_tokens(s)
        if buf and buf_tok + st > max_tokens:
            pieces.append(" ".join(buf))
            buf, buf_tok = [], 0
        if st > max_tokens:
            # 极端长句：按字符切
            step = max_tokens * 4
            for k in range(0, len(s), step):
                pieces.append(s[k : k + step])
            continue
        buf.append(s)
        buf_tok += st
    if buf:
        pieces.append(" ".join(buf))
    return pieces or [text]


def _pack_units(
    units: list[str],
    target: int = config.CHUNK_TARGET_TOKENS,
    max_tokens: int = config.CHUNK_MAX_TOKENS,
    overlap_tokens: int = config.CHUNK_OVERLAP_TOKENS,
) -> list[str]:
    """把单元（段落/代码块）打包成目标大小的文本块，带段级重叠。"""
    chunks: list[str] = []
    cur: list[str] = []
    cur_tok = 0
    for u in units:
        if not u.strip():
            continue
        utok = estimate_tokens(u)
        if utok > max_tokens:
            if cur:
                chunks.append("\n\n".join(cur))
                cur, cur_tok = [], 0
            chunks.extend(_hard_split(u, max_tokens))
            continue
        if cur and cur_tok + utok > target:
            chunks.append("\n\n".join(cur))
            # 重叠：保留最后一段（若它不太长）
            last = cur[-1]
            if overlap_tokens > 0 and estimate_tokens(last) <= max(overlap_tokens * 2, 120):
                cur = [last]
                cur_tok = estimate_tokens(last)
            else:
                cur, cur_tok = [], 0
        cur.append(u)
        cur_tok += utok
    if cur:
        chunks.append("\n\n".join(cur))
    return chunks


def _make_chunk(
    doc: Document,
    section: Section,
    body: str,
    symbol: str = "",
    ref_kind: str = "",
    anchor: str = "",
    url: str = "",
) -> dict:
    if symbol:
        symbol_line = f"{doc.class_name}.{symbol} — {_CLASS_KINDS_ZH.get(ref_kind, ref_kind)}"
        text = f"{_join_crumbs(doc, section.title)}\n{symbol_line}\n{body}"
    else:
        text = f"{_join_crumbs(doc, section.title)}\n{body}"
    chunk_id = hashlib.sha1(
        f"{doc.doc_id}|{section.title}|{symbol}|{anchor}|{body[:200]}".encode("utf-8")
    ).hexdigest()[:16]
    return {
        "chunk_id": chunk_id,
        "doc_id": doc.doc_id,
        "doc_type": doc.doc_type,
        "title": doc.title,
        "breadcrumbs": list(doc.breadcrumbs),
        "url": url or doc.url,
        "anchor": anchor,
        "symbol": f"{doc.class_name}.{symbol}" if symbol else "",
        "text": text,
        "raw_text": body,
        "n_tokens": estimate_tokens(text),
    }


def _symbol_anchor(doc: Document, symbol: str, ref_kind: str) -> str:
    cls = (doc.class_name or "").lower().replace("_", "-")
    sym = symbol.lower().replace("_", "-")
    return f"class-{cls}-{ref_kind}-{sym}"


def _chunk_class(doc: Document) -> list[dict]:
    chunks: list[dict] = []
    for section in doc.sections:
        if not section.blocks:
            continue
        # 只有 reftable 的清单 section 跳过（描述在后续 section 中）
        if section.title in _SKIP_REFTABLE_SECTIONS and not any(
            b.kind == "symbol" for b in section.blocks
        ):
            continue

        # 按 symbol 分组：符号块开始新组，其余块归入当前组
        groups: list[dict] = []
        loose: list[Block] = []
        cur: dict | None = None
        for b in section.blocks:
            if b.kind == "symbol" and b.symbol:
                if cur:
                    groups.append(cur)
                cur = {"symbol": b.symbol, "kind": b.ref_kind or "", "blocks": [b]}
            elif cur is not None:
                cur["blocks"].append(b)
            else:
                loose.append(b)
        if cur:
            groups.append(cur)

        # 没有符号块的 section（Description / Tutorials 等）
        if not groups:
            units = [_format_block(b) for b in loose if b.text.strip()]
            for part in _pack_units(units):
                chunks.append(_make_chunk(doc, section, part))
            continue

        # 符号组
        for g in groups:
            blocks: list[Block] = g["blocks"]
            head = blocks[0]
            body_units = [_format_block(b) for b in blocks[1:] if b.text.strip()]
            prefix_signature = _format_block(head)
            body = "\n\n".join(body_units)
            anchor = _symbol_anchor(doc, g["symbol"], g["kind"])
            max_body = config.CHUNK_MAX_TOKENS
            if estimate_tokens(prefix_signature + "\n" + body) <= max_body:
                chunks.append(
                    _make_chunk(
                        doc, section, prefix_signature + ("\n\n" + body if body else ""),
                        symbol=g["symbol"], ref_kind=g["kind"], anchor=anchor,
                    )
                )
            else:
                for part in _pack_units(body_units, target=config.CHUNK_TARGET_TOKENS):
                    chunks.append(
                        _make_chunk(
                            doc, section, prefix_signature + "\n\n" + part,
                            symbol=g["symbol"], ref_kind=g["kind"], anchor=anchor,
                        )
                    )

        # section 开头没有归入符号的块
        if loose:
            units = [_format_block(b) for b in loose if b.text.strip()]
            for part in _pack_units(units):
                chunks.append(_make_chunk(doc, section, part))
    return chunks


def _chunk_sections(doc: Document) -> list[dict]:
    chunks: list[dict] = []
    for section in doc.sections:
        if not section.blocks:
            continue
        units = [_format_block(b) for b in section.blocks if b.text.strip()]
        if not units:
            continue
        for part in _pack_units(units):
            chunks.append(_make_chunk(doc, section, part))
    if not chunks and doc.sections:
        # 兜底：整篇合并
        units = [
            _format_block(b) for s in doc.sections for b in s.blocks if b.text.strip()
        ]
        for part in _pack_units(units):
            chunks.append(_make_chunk(doc, doc.sections[0], part))
    return chunks


def chunk_document(doc: Document) -> list[dict]:
    """单篇文档 -> chunk 列表。"""
    if doc.doc_type == "class":
        return _chunk_class(doc)
    return _chunk_sections(doc)


def chunk_documents(docs: list[Document]) -> list[dict]:
    """批量分块，返回全部 chunk。"""
    out: list[dict] = []
    for doc in docs:
        out.extend(chunk_document(doc))
    return out
