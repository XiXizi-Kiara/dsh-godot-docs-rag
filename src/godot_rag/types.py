"""文档与分块的数据结构。

Document / Section / Block 是 parse_rst 的输出，也是 chunks.jsonl 的输入。
Chunk 直接使用 dict（便于 jsonl / pickle 序列化），字段见 docs/INTERFACES.md。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict


@dataclass
class Block:
    """文档中的最小内容块。"""

    kind: str  # para | code | note | warning | tip | seealso | list | table | image | symbol
    text: str
    lang: str | None = None
    # class 参考页中该块所属的符号类别：method/property/signal/constant/enum
    ref_kind: str | None = None
    # class 参考页中符号名（如 add_child），普通块为 None
    symbol: str | None = None


@dataclass
class Section:
    """文档小节（对应 RST 的一个标题层级）。"""

    title: str
    level: int
    blocks: list[Block] = field(default_factory=list)
    anchor: str = ""


@dataclass
class Document:
    """一篇 RST 文档。"""

    doc_id: str
    doc_type: str
    title: str
    breadcrumbs: list[str]
    url: str
    sections: list[Section] = field(default_factory=list)
    class_name: str | None = None
    inherits: str | None = None
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Document":
        sections = [
            Section(
                title=s["title"],
                level=s["level"],
                anchor=s.get("anchor", ""),
                blocks=[Block(**b) for b in s.get("blocks", [])],
            )
            for s in data.get("sections", [])
        ]
        return cls(
            doc_id=data["doc_id"],
            doc_type=data["doc_type"],
            title=data["title"],
            breadcrumbs=data.get("breadcrumbs", []),
            url=data.get("url", ""),
            sections=sections,
            class_name=data.get("class_name"),
            inherits=data.get("inherits"),
            meta=data.get("meta", {}),
        )


# 文档类型 -> 面包屑第二级名称
DOC_TYPE_LABELS = {
    "class": "Class reference",
    "tutorial": "Tutorials",
    "getting_started": "Getting started",
    "engine_details": "Engine details",
    "about": "About",
    "community": "Community",
    "index": "Documentation",
}


def estimate_tokens(text: str) -> int:
    """粗略 token 估算：英文约 4 字符/token。"""
    return max(1, len(text) // 4)
