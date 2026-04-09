"""Tests for the Markdown parser."""

from rtfm.ingest.parser import parse_markdown


def test_parse_empty():
    assert parse_markdown("") == []


def test_parse_no_headings():
    sections = parse_markdown("Just some text.\n\nAnother paragraph.")
    assert len(sections) == 1
    assert sections[0].heading == ""
    assert "Just some text" in sections[0].content


def test_parse_single_heading():
    md = "## Hello\n\nSome content here."
    sections = parse_markdown(md)
    assert len(sections) == 1
    assert sections[0].heading == "Hello"
    assert sections[0].heading_hierarchy == ["Hello"]
    assert "Some content here" in sections[0].content


def test_parse_multiple_headings():
    md = """## First

Content of first.

## Second

Content of second.
"""
    sections = parse_markdown(md)
    assert len(sections) == 2
    assert sections[0].heading == "First"
    assert sections[1].heading == "Second"


def test_heading_hierarchy():
    md = """# Top

## Sub

Content under sub.

## Another Sub

More content.
"""
    sections = parse_markdown(md, split_level=2)
    # Should have: Top (h1), Sub (h2), Another Sub (h2)
    assert len(sections) == 3
    assert sections[1].heading_hierarchy == ["Top", "Sub"]
    assert sections[2].heading_hierarchy == ["Top", "Another Sub"]


def test_sub_headings_merged_into_parent():
    md = """## Parent

Some intro.

### Child

Child content.

### Another Child

More child content.
"""
    sections = parse_markdown(md, split_level=2)
    assert len(sections) == 1
    assert sections[0].heading == "Parent"
    assert "Child content" in sections[0].content
    assert "Another Child" in sections[0].content


def test_preamble_before_first_heading():
    md = """Some intro text.

## Heading

Content.
"""
    sections = parse_markdown(md)
    assert len(sections) == 2
    assert sections[0].heading == ""
    assert "intro text" in sections[0].content
    assert sections[1].heading == "Heading"


def test_heading_with_anchor_stripped():
    md = "## My Heading {#my-heading}\n\nContent."
    sections = parse_markdown(md)
    assert sections[0].heading == "My Heading"


def test_source_file_propagated():
    md = "## Test\n\nContent."
    sections = parse_markdown(md, source_file="docs/test.md")
    assert sections[0].source_file == "docs/test.md"


def test_frontmatter_stripped():
    md = """---
title: My Page
sidebar: true
---

## Heading

Content after frontmatter.
"""
    sections = parse_markdown(md)
    assert len(sections) == 1
    assert sections[0].heading == "Heading"
    assert "Content after frontmatter" in sections[0].content
    assert "sidebar" not in sections[0].content


def test_frontmatter_only_at_start():
    md = """## Heading

Some text with --- dashes --- in it.
"""
    sections = parse_markdown(md)
    assert "---" in sections[0].content


def test_rst_headings():
    rst = """ORM Quick Start
===============

Some intro text.

Declare Models
--------------

Model content here.
"""
    sections = parse_markdown(rst, source_file="orm/quickstart.rst")
    assert len(sections) == 2
    assert sections[0].heading == "ORM Quick Start"
    assert sections[0].level == 1
    assert sections[1].heading == "Declare Models"
    assert sections[1].level == 2
    assert sections[1].heading_hierarchy == ["ORM Quick Start", "Declare Models"]


def test_rst_three_levels():
    rst = """Top Title
=========

Intro.

Section
-------

Section content.

Subsection
~~~~~~~~~~

Sub content.
"""
    sections = parse_markdown(rst, source_file="doc.rst", split_level=2)
    assert len(sections) == 2
    assert sections[0].heading == "Top Title"
    assert sections[1].heading == "Section"
    assert "Sub content" in sections[1].content


def test_rst_autodetect_without_extension():
    """RST headings should be detected even without .rst extension."""
    rst = """Title
=====

Content here.
"""
    sections = parse_markdown(rst)
    assert len(sections) == 1
    assert sections[0].heading == "Title"
