"""RST -> 结构化文档解析器（仅标准库）。

Godot 官方文档由 Sphinx 从 RST 生成，_sources 下的 .rst.txt 保留了完整结构。
本模块实现一个"够用"的 RST 子集解析器，只保留对问答有价值的信息：

- 标题层级 -> Section 树
- 段落 / 列表 / 表格 / 代码块 / 提示块
- 内联角色（:ref:、:doc:、:class:、``code`` 等）清洗为纯文本
- class 参考页的符号结构（method / property / signal / constant / enum）

未知 directive 不会被丢弃，而是降级为普通文本。
"""
from __future__ import annotations

import re
from pathlib import Path

from . import config
from .types import Block, Document, Section

# 标题装饰字符（Sphinx 常见）
_ADORNMENT_RE = re.compile(r"^([=\-~^\"'`*#+:])\1{2,}\s*$")

# 形如 `:key: value` 的文件头字段
_FIELD_RE = re.compile(r"^:([a-zA-Z0-9_-]+):\s*")

# directive：`.. name:: args`
_DIRECTIVE_RE = re.compile(r"^(\s*)\.\.\s+([a-zA-Z0-9_+:-]+)::\s*(.*)$")

# 锚点：`.. _class_Node:`
_ANCHOR_RE = re.compile(r"^\s*\.\.\s+_([A-Za-z0-9_.:-]+):\s*$")

# 列表项：- * + 1. #.
_LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+\.|#\.)\s+")

# 表格行
_TABLE_JUNK_RE = re.compile(r"^[+|=\-:\s]{3,}$")

# 代码块 directive
_CODE_DIRECTIVES = {"code-block", "sourcecode", "code", "literalinclude"}
# 提示块 directive
_ADMONITION_DIRECTIVES = {
    "note", "warning", "tip", "important", "caution", "danger", "attention",
    "error", "hint", "seealso", "todo", "deprecated",
}
# 直接跳过 body 的 directive（表格类单独处理）
_SKIP_DIRECTIVES = {
    "image", "figure", "video", "math", "raw", "include", "toctree",
    "meta", "highlight", "default-role", "role",
    "rst-class", "class", "contents", "index", "only", "ifconfig",
    "centered", "line-block",
}
# class 参考页符号类型
_CLASSREF_KINDS = {
    "classref-method": "method",
    "classref-property": "property",
    "classref-signal": "signal",
    "classref-constant": "constant",
    "classref-enumeration": "enum",
    "classref-annotation": "annotation",
}
# 出现这些 rst-class 时视为"离开符号描述"
_CLASSREF_RESET = {
    "classref-introduction-group",
    "classref-reftable-group",
    "classref-descriptions-group",
    "classref-section-separator",
    "classref-inherited-group",
}

_SYMBOL_NAME_RE = re.compile(r"\*\*([A-Za-z_][A-Za-z0-9_]*)\*\*")

DOC_TYPE_BY_TOP = {
    "classes": "class",
    "tutorials": "tutorial",
    "getting_started": "getting_started",
    "engine_details": "engine_details",
    "about": "about",
    "community": "community",
}

# 行内角色：:ref:`text <target>`
_ROLE_RE = re.compile(r":([a-zA-Z0-9_+:-]+):`([^`]*)`")
# 匿名/命名链接：`text <target>`_
_LINK_RE = re.compile(r"`([^`<]*?)<([^`>]+)>`_+")
# 反引号 literal：``text`` 或 `text`
_DOUBLE_TICK_RE = re.compile(r"``([^`]*)``")
_SINGLE_TICK_RE = re.compile(r"`([^`]*)`")
# 强调
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_EM_RE = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
# 替换：|void|
_SUBST_RE = re.compile(r"\|([A-Za-z0-9_ +.-]+)\|")


def _strip_field_list(lines: list[str]) -> list[str]:
    """去掉文件开头的 :field: 元数据行。"""
    i = 0
    while i < len(lines) and (_FIELD_RE.match(lines[i]) or not lines[i].strip()):
        if not lines[i].strip():
            # 允许 field 列表中间的空行，但如果后面不是 field 就停止
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and _FIELD_RE.match(lines[j]):
                i = j
                continue
            break
        i += 1
    return lines[i:]


def clean_inline(text: str, links: list[str] | None = None) -> str:
    """把一行 RST 行内标记清洗为纯文本，并可选收集引用目标。"""
    if not text:
        return ""
    # RST 行尾反斜杠是续行/转义标记，去掉
    text = re.sub(r"\\\s*$", "", text)

    def role_repl(m: re.Match) -> str:
        role, content = m.group(1).lower(), m.group(2)
        # 抽取 <target>
        if "<" in content:
            label, target = content.split("<", 1)
            target = target.rstrip(">").strip()
            if links is not None and target:
                links.append(target)
            content = label.strip()
        if role in {"ref", "doc", "class", "method", "attr", "const", "signal",
                    "enum", "func", "kbd", "menu", "button", "abbr", "sup",
                    "sub", "math", "download", "term", "guilabel", "command",
                    "file", "envvar", "option", "program", "lua", "gdscript",
                    "csharp", "cpp", "js", "java", "sh"}:
            # abbr 的 "X (Y)" 形式只保留 X
            if role == "abbr" and "(" in content:
                content = content.split("(")[0]
            return content
        # :bdg-*: 徽章
        if role.startswith("bdg"):
            return content
        return content

    text = _ROLE_RE.sub(role_repl, text)
    text = _LINK_RE.sub(lambda m: m.group(1).strip() or m.group(2).strip(), text)
    text = _DOUBLE_TICK_RE.sub(r"\1", text)
    text = _SINGLE_TICK_RE.sub(r"\1", text)
    text = _BOLD_RE.sub(r"\1", text)
    text = _EM_RE.sub(r"\1", text)
    text = _SUBST_RE.sub(r"\1", text)
    # 转义：\: \- \ 等（RST 用反斜杠转义任意字符）
    text = re.sub(r"\\(.)", r"\1", text)
    # 移除不可见字符与 🔗
    text = text.replace("\u200b", "").replace("🔗", "")
    # 折叠空白
    text = re.sub(r"[ \t]+", " ", text).strip()
    return text


def _slug(title: str) -> str:
    """生成 Sphinx 风格锚点：小写、空格/非字母数字转连字符。"""
    s = title.strip().lower()
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", s).strip("-")
    return s


def _extract_links(line: str) -> list[str]:
    """从一行中抽取 :ref:/:doc: 的目标。"""
    links: list[str] = []
    clean_inline(line, links)
    return links


def _doc_id_from_path(rel: Path) -> str:
    name = rel.as_posix()
    if name.endswith(".rst.txt"):
        name = name[: -len(".rst.txt")]
    return name


def _doc_type_from_id(doc_id: str) -> str:
    top = doc_id.split("/", 1)[0]
    return DOC_TYPE_BY_TOP.get(top, "tutorial" if doc_id.startswith("tutorials") else "index")


def _breadcrumbs(doc_id: str, doc_type: str, title: str) -> list[str]:
    labels = {
        "class": "Class reference",
        "tutorial": "Tutorials",
        "getting_started": "Getting started",
        "engine_details": "Engine details",
        "about": "About",
        "community": "Community",
        "index": "Documentation",
    }
    crumbs = ["Godot Docs", labels.get(doc_type, "Documentation")]
    # 教程按子目录加一级，如 Tutorials > 2D
    parts = doc_id.split("/")
    if doc_type == "tutorial" and len(parts) > 2 and parts[1] not in {"index"}:
        crumbs.append(parts[1].replace("_", " ").title())
    crumbs.append(title)
    return crumbs


class _Parser:
    """单文件的解析状态机。"""

    def __init__(self, doc_id: str, doc_type: str) -> None:
        self.doc_id = doc_id
        self.doc_type = doc_type
        self.sections: list[Section] = []
        self.title = ""
        self.level_by_char: dict[str, int] = {}
        self.links: list[str] = []
        self.class_name: str | None = None
        self.inherits: str | None = None
        self.cur_ref_kind: str | None = None
        self.pending_ref_kind: str | None = None
        self.cur_anchor = ""

    # -- section 管理 -----------------------------------------------------
    def _ensure_section(self, title: str, level: int) -> Section:
        if not self.sections or self.sections[-1].title != title or self.sections[-1].level != level:
            self.sections.append(Section(title=title, level=level, anchor=_slug(title)))
        return self.sections[-1]

    def _add(self, block: Block) -> None:
        if not self.sections:
            self.sections.append(Section(title=self.title or "Introduction", level=1))
        self.sections[-1].blocks.append(block)

    # -- directive 解析 ---------------------------------------------------
    def _parse_directive(self, lines: list[str], i: int, indent: int) -> tuple[int, bool]:
        """解析 i 处的 directive，返回 (下一行索引, 是否处理了标题)。"""
        m = _DIRECTIVE_RE.match(lines[i])
        assert m is not None
        name = m.group(2).lower()
        arg = m.group(3).strip()
        i += 1
        # 收集 body：缩进大于 indent 的行（含空行）
        body: list[str] = []
        body_end = i
        while body_end < len(lines):
            line = lines[body_end]
            if not line.strip():
                body.append("")
                body_end += 1
                continue
            cur_indent = len(line) - len(line.lstrip())
            if cur_indent > indent:
                body.append(line)
                body_end += 1
            else:
                break
        # 去掉尾部空行
        while body and not body[-1].strip():
            body.pop()
        base_indent = min(
            (len(b) - len(b.lstrip()) for b in body if b.strip()), default=indent + 3
        )
        body = [b[base_indent:] if b.strip() else "" for b in body]

        # 指令选项（:linenos: 等）在 body 开头
        opt_end = 0
        while opt_end < len(body) and re.match(r"^\s*:[a-zA-Z-]+:", body[opt_end]):
            opt_end += 1

        if name in {"table", "csv-table", "list-table"}:
            # 表格：保留为可读文本（属性/方法签名清单对检索有价值）
            rows: list[str] = []
            for line in body[opt_end:]:
                s = line.strip()
                if not s or _TABLE_JUNK_RE.match(s):
                    continue
                s = clean_inline(s, self.links)
                if s:
                    rows.append(s)
            if rows:
                self._add(Block(kind="table", text="\n".join(rows), ref_kind=self.cur_ref_kind))
            return body_end, False

        if name == "code-tab":
            # 多语言代码页签：只保留 GDScript，避免同一段代码重复 2-4 遍
            lang = arg.split()[0].strip() if arg else None
            if lang and lang.lower() in {"csharp", "cpp", "c++", "javascript", "js", "java", "python"}:
                return body_end, False
            code = "\n".join(body[opt_end:]).strip("\n")
            if code.strip():
                self._add(Block(kind="code", text=code, lang=lang))
            return body_end, False

        if name in _CODE_DIRECTIVES:
            lang = arg.split()[0].strip() if arg else None
            code = "\n".join(body[opt_end:]).strip("\n")
            if code.strip():
                self._add(Block(kind="code", text=code, lang=lang))
            return body_end, False

        if name in _ADMONITION_DIRECTIVES:
            content = self._flatten_body(body[opt_end:])
            if arg and not content:
                content = clean_inline(arg, self.links)
            if content:
                kind = "seealso" if name == "seealso" else name
                self._add(Block(kind=kind, text=content, ref_kind=self.cur_ref_kind))
            return body_end, False

        if name == "rst-class":
            cls = arg.strip()
            if cls in _CLASSREF_KINDS:
                self.pending_ref_kind = _CLASSREF_KINDS[cls]
            elif cls in _CLASSREF_RESET:
                self.cur_ref_kind = None
            return body_end, False

        if name in {"tabs", "tab"}:
            if name == "tabs":
                content = self._flatten_tabs(body[opt_end:])
            else:
                content = self._flatten_body(body[opt_end:])
            if content:
                self._add(Block(kind="para", text=content, ref_kind=self.cur_ref_kind))
            return body_end, False

        if name in _SKIP_DIRECTIVES:
            return body_end, False

        # 未知 directive：正文降级为段落
        content = self._flatten_body(body[opt_end:])
        if arg and not content:
            content = clean_inline(arg, self.links)
        if content:
            self._add(Block(kind="para", text=content, ref_kind=self.cur_ref_kind))
        return body_end, False

    def _flatten_tabs(self, body: list[str]) -> str:
        """tabs 内的多语言示例只保留 GDScript 分支，避免同一内容重复两遍。"""
        sections: list[tuple[str, list[str]]] = []
        cur_label: str | None = None
        cur_lines: list[str] = []
        for line in body:
            m = re.match(r"^\s*\.\.\s+tab::\s*(.*)$", line)
            if m:
                if cur_label is not None or cur_lines:
                    sections.append((cur_label or "", cur_lines))
                cur_label = m.group(1).strip()
                cur_lines = []
            else:
                cur_lines.append(line)
        if cur_label is not None or cur_lines:
            sections.append((cur_label or "", cur_lines))
        if len(sections) <= 1:
            return self._flatten_body(body)
        chosen = None
        for label, lines in sections:
            if "gdscript" in label.lower():
                chosen = (label, lines)
                break
        if chosen is None:
            chosen = sections[0]
        return self._flatten_body(chosen[1])

    def _flatten_body(self, body: list[str]) -> str:
        """把 directive 的 block body 压成一段清洗文本（保留列表换行）。"""
        out: list[str] = []
        for line in body:
            s = line.rstrip()
            if not s:
                if out and out[-1] != "":
                    out.append("")
                continue
            out.append(clean_inline(s, self.links))
        text = "\n".join(out).strip()
        return re.sub(r"\n{3,}", "\n\n", text)

    # -- 主循环 -----------------------------------------------------------
    def parse(self, lines: list[str]) -> None:
        i = 0
        n = len(lines)
        para: list[str] = []
        list_buf: list[str] = []
        table_buf: list[str] = []
        in_code_literal = False

        def flush_para() -> None:
            nonlocal para
            if not para:
                return
            raw_text = " ".join(para).strip()
            para = []
            if not raw_text:
                return
            text = clean_inline(raw_text, self.links).strip()
            if not text:
                return
            # 去除纯装饰行
            if re.fullmatch(r"[=\-~^\"'`*#+:_]{3,}", text):
                return
            # class 符号签名行（原始文本含 **name**）
            if self.pending_ref_kind:
                m = _SYMBOL_NAME_RE.search(raw_text)
                if m:
                    self.cur_ref_kind = self.pending_ref_kind
                    self.pending_ref_kind = None
                    self._add(
                        Block(
                            kind="symbol",
                            text=text,
                            ref_kind=self.cur_ref_kind,
                            symbol=m.group(1),
                        )
                    )
                    return
            self._add(Block(kind="para", text=text, ref_kind=self.cur_ref_kind))

        def flush_list() -> None:
            nonlocal list_buf
            if not list_buf:
                return
            self._add(Block(kind="list", text="\n".join(list_buf), ref_kind=self.cur_ref_kind))
            list_buf = []

        def flush_table() -> None:
            nonlocal table_buf
            if not table_buf:
                return
            self._add(Block(kind="table", text="\n".join(table_buf), ref_kind=self.cur_ref_kind))
            table_buf = []

        while i < n:
            raw = lines[i]
            stripped = raw.strip()

            # 空行：结束当前块
            if not stripped:
                flush_para()
                flush_list()
                flush_table()
                in_code_literal = False
                i += 1
                continue

            # 锚点
            am = _ANCHOR_RE.match(raw)
            if am:
                flush_para()
                flush_list()
                flush_table()
                self.cur_anchor = am.group(1)
                i += 1
                continue

            # directive / 注释
            if raw.lstrip().startswith(".. ") or raw.strip() == "..":
                flush_para()
                flush_list()
                flush_table()
                if not _DIRECTIVE_RE.match(raw):
                    i += 1  # 普通注释行，跳过
                    continue
                indent = len(raw) - len(raw.lstrip())
                i, _ = self._parse_directive(lines, i, indent)
                continue

            # 标题检测：当前行文本 + 下一行装饰
            if i + 1 < n and _ADORNMENT_RE.match(lines[i + 1]) and not stripped.startswith(("+", "|")):
                if not _TABLE_JUNK_RE.match(stripped):
                    flush_para()
                    flush_list()
                    flush_table()
                    char = lines[i + 1].strip()[0]
                    if char not in self.level_by_char:
                        self.level_by_char[char] = len(self.level_by_char) + 1
                    level = self.level_by_char[char]
                    title = clean_inline(stripped, self.links)
                    if not self.title:
                        self.title = title
                    self.cur_ref_kind = None
                    self.pending_ref_kind = None
                    self._ensure_section(title, level)
                    i += 2
                    continue

            # 单独的分隔线
            if _ADORNMENT_RE.match(stripped) and not para:
                i += 1
                continue

            # 表格（class 符号签名行以 |void| 开头且含 **，不能当表格）
            if (
                stripped.startswith(("+", "|"))
                and "**" not in stripped
                and (_TABLE_JUNK_RE.match(stripped) or stripped.startswith("|"))
            ):
                flush_para()
                flush_list()
                cleaned = clean_inline(stripped, self.links)
                if cleaned and not _TABLE_JUNK_RE.match(stripped):
                    table_buf.append(cleaned)
                i += 1
                continue

            # 列表项
            if _LIST_RE.match(raw):
                flush_para()
                flush_table()
                item = _LIST_RE.sub("", raw).strip()
                list_buf.append("- " + clean_inline(item, self.links))
                i += 1
                continue

            # 列表续行（缩进且 list 非空）
            if list_buf and (raw.startswith(" ") or raw.startswith("\t")):
                item = clean_inline(stripped, self.links)
                if item:
                    list_buf.append("  " + item)
                i += 1
                continue

            # RST 字面代码块：段落以 :: 结尾 / 独立 ::
            if stripped == "::" or stripped.endswith("::"):
                if stripped != "::":
                    text = clean_inline(stripped[:-1], self.links)
                    if text:
                        para.append(text)
                flush_para()
                # 收集后续缩进行作为代码
                code: list[str] = []
                j = i + 1
                while j < n and (not lines[j].strip() or lines[j].startswith((" ", "\t"))):
                    code.append(lines[j])
                    j += 1
                while code and not code[-1].strip():
                    code.pop()
                if code:
                    base = min(len(c) - len(c.lstrip()) for c in code if c.strip())
                    self._add(Block(kind="code", text="\n".join(c[base:] for c in code).strip("\n")))
                i = j
                continue

            # 普通文本行（保留原始标记，flush 时统一清洗并识别 **符号**）
            if in_code_literal:
                i += 1
                continue
            para.append(stripped)
            i += 1

        flush_para()
        flush_list()
        flush_table()

    # -- 收尾 -------------------------------------------------------------
    def build(self) -> Document:
        meta = {"links": sorted(set(self.links))}
        # 从文件头字段推断 inherits
        for s in self.sections:
            for b in s.blocks:
                if b.text.startswith("Inherits:"):
                    target = b.text.split(":", 1)[1].strip()
                    self.inherits = target.split()[0] if target else None
                    break
            if self.inherits:
                break
        if self.doc_type == "class":
            self.class_name = self.title or None
        return Document(
            doc_id=self.doc_id,
            doc_type=self.doc_type,
            title=self.title or self.doc_id.rsplit("/", 1)[-1],
            breadcrumbs=_breadcrumbs(self.doc_id, self.doc_type, self.title or self.doc_id),
            url=self.doc_id + ".html",
            sections=self.sections,
            class_name=self.class_name,
            inherits=self.inherits,
            meta=meta,
        )


def parse_rst(path: str | Path, doc_id: str | None = None) -> Document:
    """解析单个 RST 文件为 Document。"""
    p = Path(path)
    if doc_id is None:
        try:
            doc_id = _doc_id_from_path(p.resolve().relative_to(config.RST_DIR.resolve()))
        except ValueError:
            doc_id = _doc_id_from_path(Path(p.name))
    doc_type = _doc_type_from_id(doc_id)
    text = p.read_text(encoding="utf-8", errors="replace")
    lines = _strip_field_list(text.splitlines())
    parser = _Parser(doc_id, doc_type)
    parser.parse(lines)
    return parser.build()


def iter_rst_files(rst_dir: Path | None = None) -> list[tuple[str, Path]]:
    """返回 (doc_id, 路径) 列表，按 doc_id 排序。"""
    rst_dir = rst_dir or config.RST_DIR
    items: list[tuple[str, Path]] = []
    for p in rst_dir.rglob("*.rst.txt"):
        rel = p.relative_to(rst_dir)
        items.append((_doc_id_from_path(rel), p))
    items.sort(key=lambda x: x[0])
    return items


def parse_documents(limit: int | None = None, rst_dir: Path | None = None) -> list[Document]:
    """解析全部文档（limit 用于试跑）。"""
    files = iter_rst_files(rst_dir)
    if limit is not None:
        files = files[:limit]
    return [parse_rst(p, doc_id) for doc_id, p in files]
