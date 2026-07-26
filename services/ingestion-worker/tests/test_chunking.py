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


def test_a_table_section_past_table_max_words_is_split_by_row_preserving_header():
    header = "| id | value |"
    separator = "| --- | --- |"
    rows = [f"| row{i} | val{i} |" for i in range(400)]
    long_table = "\n".join([header, separator, *rows])
    sections = [ParsedSection(text=long_table, content_type="table")]

    chunks = chunk_sections(sections, table_max_words=50)

    assert len(chunks) > 1
    assert all(c.content_type == "table" for c in chunks)

    seen_rows: list[str] = []
    for chunk in chunks:
        lines = chunk.text.split("\n")
        assert lines[0] == header
        assert lines[1] == separator
        assert len(chunk.text.split()) <= 50
        seen_rows.extend(lines[2:])
    # No row dropped, duplicated, or split -- exactly the original rows, in order.
    assert seen_rows == rows


def test_a_table_section_under_table_max_words_is_not_split():
    rows = [f"| row{i} | val{i} |" for i in range(5)]
    small_table = "\n".join(["| id | value |", "| --- | --- |", *rows])
    sections = [ParsedSection(text=small_table, content_type="table")]

    chunks = chunk_sections(sections, table_max_words=1000)

    assert len(chunks) == 1
    assert chunks[0].text == small_table


def test_a_single_row_wider_than_table_max_words_becomes_its_own_oversized_chunk():
    huge_row = "| " + " ".join(f"cell{i}" for i in range(200)) + " |"
    table = "\n".join(["| id | value |", "| --- | --- |", huge_row])
    sections = [ParsedSection(text=table, content_type="table")]

    chunks = chunk_sections(sections, table_max_words=10)

    assert len(chunks) == 1
    assert chunks[0].text == table


def test_a_pdf_dot_leader_table_of_contents_is_collapsed_before_sizing():
    # A real CIS benchmark PDF's ToC: dozens of lines like this, each dot run
    # a single whitespace-split "word" that nonetheless tokenizes to
    # thousands of embedding-model tokens -- see module docstring.
    toc_line = "Overview " + ("." * 150) + " 7"
    sections = [ParsedSection(text=toc_line, content_type="text")]

    chunks = chunk_sections(sections)

    assert len(chunks) == 1
    assert chunks[0].text == "Overview ... 7"


def test_repeated_char_collapsing_leaves_normal_prose_untouched():
    prose = "This is completely ordinary prose, nothing repeated here."
    sections = [ParsedSection(text=prose, content_type="text")]

    chunks = chunk_sections(sections)

    assert chunks[0].text == prose
