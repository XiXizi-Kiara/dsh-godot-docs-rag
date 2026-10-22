"""parse_rst 单元测试：标题层级、代码块、列表、提示块、行内清洗。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from godot_rag.parse_rst import clean_inline, parse_documents, parse_rst  # noqa: E402

RST_SAMPLE = """\
:article_outdated: False

.. _doc_sample:

Sample page
===========

Intro paragraph with :ref:`Node<class_Node>` and ``queue_free()``.

.. note::

   This is a note.

Section Two
-----------

.. code-block:: gdscript

   func foo() -> void:
       pass

- item one
- item two

.. seealso:: See :doc:`Other <../other_page>`.
"""


class TestCleanInline(unittest.TestCase):
    def test_ref_label_kept_target_stripped(self):
        self.assertEqual(clean_inline(":ref:`Node<class_Node>`"), "Node")
        self.assertEqual(clean_inline(":ref:`add_child()<class_Node_method_add_child>`"), "add_child()")

    def test_code_literal(self):
        self.assertEqual(clean_inline("call ``queue_free()`` now"), "call queue_free() now")

    def test_bold_and_em(self):
        self.assertEqual(clean_inline("**add_child** and *scene*"), "add_child and scene")

    def test_hyperlink(self):
        self.assertEqual(clean_inline("`All Demos <https://example.com>`__"), "All Demos")

    def test_substitution(self):
        self.assertEqual(clean_inline(r"|void| **foo**\ (\ )"), "void foo ( )")

    def test_escape_removed(self):
        out = clean_inline(r"escaped \*star\* and \:colon")
        self.assertNotIn("\\", out)
        self.assertIn("star", out)
        self.assertIn(":colon", out)

    def test_width_zero_and_link_icon_removed(self):
        self.assertEqual(clean_inline("text \u200b :ref:`🔗<class_Node>`").strip(), "text")


class TestParseDocument(unittest.TestCase):
    def _parse_sample(self):
        # 沙箱下 tempfile 目录的 ACL 不可写，使用 workspace 内固定目录
        d = ROOT / ".tmp" / "tests"
        d.mkdir(parents=True, exist_ok=True)
        p = d / "sample.rst.txt"
        p.write_text(RST_SAMPLE, encoding="utf-8")
        return parse_rst(p, "tutorials/sample")

    def test_titles_and_sections(self):
        doc = self._parse_sample()
        self.assertEqual(doc.title, "Sample page")
        self.assertEqual(doc.doc_type, "tutorial")
        self.assertEqual([s.title for s in doc.sections], ["Sample page", "Section Two"])
        self.assertEqual(doc.sections[0].level, 1)
        self.assertEqual(doc.sections[1].level, 2)

    def test_blocks(self):
        doc = self._parse_sample()
        blocks = doc.sections[0].blocks
        kinds = [b.kind for b in blocks]
        self.assertIn("note", kinds)
        sec2 = doc.sections[1].blocks
        kinds2 = [b.kind for b in sec2]
        self.assertIn("code", kinds2)
        self.assertIn("list", kinds2)
        self.assertIn("seealso", kinds2)
        code = next(b for b in sec2 if b.kind == "code")
        self.assertEqual(code.lang, "gdscript")
        self.assertIn("func foo()", code.text)

    def test_field_list_stripped(self):
        doc = self._parse_sample()
        self.assertNotIn("article_outdated", " ".join(
            b.text for s in doc.sections for b in s.blocks
        ))


class TestParseRealDoc(unittest.TestCase):
    def test_class_node(self):
        path = ROOT / "godot-docs-html-stable/_sources/classes/class_node.rst.txt"
        if not path.exists():
            self.skipTest("文档快照不存在")
        doc = parse_rst(path, "classes/class_node")
        self.assertEqual(doc.doc_type, "class")
        self.assertEqual(doc.title, "Node")
        self.assertEqual(doc.class_name, "Node")
        self.assertEqual(doc.inherits, "Object")
        symbols = {b.symbol for s in doc.sections for b in s.blocks if b.kind == "symbol"}
        self.assertIn("add_child", symbols)
        self.assertIn("queue_free", symbols)
        self.assertIn("tree_entered", symbols)

    def test_all_documents_parse_without_error(self):
        docs = parse_documents(limit=20)
        self.assertEqual(len(docs), 20)
        for d in docs:
            self.assertTrue(d.doc_id)
            self.assertTrue(d.title)


if __name__ == "__main__":
    unittest.main()
