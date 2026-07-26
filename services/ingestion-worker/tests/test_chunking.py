"""Coverage for issue #89: every Chunk inherits its content_type directly
from the ParsedSection it was cut from, and chunking never crosses a section
boundary (pre-existing invariant, FR-4) -- so this is really just checking
the field is threaded through, not re-testing the windowing logic itself."""

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


def test_a_section_split_into_multiple_chunks_keeps_its_content_type_on_each():
    long_table = " ".join(f"| row{i} | val{i} |" for i in range(50))
    sections = [ParsedSection(text=long_table, content_type="table")]

    chunks = chunk_sections(sections, target_words=10, overlap_ratio=0.1)

    assert len(chunks) > 1
    assert all(c.content_type == "table" for c in chunks)
