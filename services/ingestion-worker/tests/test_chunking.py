"""Coverage for issue #89: every Chunk inherits its content_type directly
from the ParsedSection it was cut from, and chunking never crosses a section
boundary (pre-existing invariant, FR-4) -- so this is really just checking
the field is threaded through, not re-testing the windowing logic itself.

Also covers issue #90: a section whose content_type isn't "text" (currently
just "table") is an atomic block -- the sliding window must never cut it,
even when it's longer than target_words, since a cut could land inside a
table row and split a label from its value across two chunks."""

from __future__ import annotations

from app.chunking import chunk_sections
from app.parsing import ParsedSection


def test_chunk_inherits_content_type_from_its_section():
    sections = [
        ParsedSection(text="prose " * 5, heading="H1", content_type="text"),
        ParsedSection(text="| a | b |\n| --- | --- |\n| 1 | 2 |", content_type="table"),
    ]
    chunks = chunk_sections(sections, target_words=512, overlap_ratio=0.15)

    assert len(chunks) == 2
    assert chunks[0].content_type == "text"
    assert chunks[1].content_type == "table"


def test_chunk_defaults_to_text_content_type():
    sections = [ParsedSection(text="some prose without an explicit content_type")]
    chunks = chunk_sections(sections)
    assert chunks[0].content_type == "text"


def test_a_long_text_section_is_split_into_multiple_chunks():
    long_prose = " ".join(f"word{i}" for i in range(50))
    sections = [ParsedSection(text=long_prose, content_type="text")]

    chunks = chunk_sections(sections, target_words=10, overlap_ratio=0.1)

    assert len(chunks) > 1
    assert all(c.content_type == "text" for c in chunks)


def test_a_table_section_is_never_split_even_when_longer_than_target_words():
    long_table = "\n".join(f"| row{i} | val{i} |" for i in range(50))
    sections = [ParsedSection(text=long_table, content_type="table")]

    chunks = chunk_sections(sections, target_words=10, overlap_ratio=0.1)

    assert len(chunks) == 1
    assert chunks[0].text == long_table
    assert chunks[0].content_type == "table"


def test_a_table_section_stays_atomic_alongside_a_split_text_section():
    long_prose = " ".join(f"word{i}" for i in range(30))
    long_table = "\n".join(f"| row{i} | val{i} |" for i in range(50))
    sections = [
        ParsedSection(text=long_prose, content_type="text"),
        ParsedSection(text=long_table, content_type="table"),
    ]

    chunks = chunk_sections(sections, target_words=10, overlap_ratio=0.1)

    table_chunks = [c for c in chunks if c.content_type == "table"]
    text_chunks = [c for c in chunks if c.content_type == "text"]
    assert len(table_chunks) == 1
    assert table_chunks[0].text == long_table
    assert len(text_chunks) > 1
