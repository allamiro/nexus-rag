"""Regression coverage for issue #88: PDF/DOCX table extraction used to fall
through to plain paragraph/page text extraction, which flattens a table's
rows and columns into an unstructured word sequence -- e.g. a table with
columns Name/Role/Clearance and a row Alice/Curator/Secret came out as
"Name Role Clearance Alice Curator Secret", with no way to tell which word
belonged to which cell or row. `app.parsing` now detects tables separately
and renders them as their own markdown block instead.

Fixtures (`tests/fixtures/*.pdf`, `*.docx`) are small synthetic documents
committed as binary files rather than generated at test time, so this suite
doesn't need reportlab as a test dependency just to build them.
"""

from pathlib import Path

import pytest
from app.parsing import ParsingError, parse_document

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_pdf_table_extracted_as_markdown_not_flattened_into_prose():
    sections = parse_document("table.pdf", _read("table.pdf"))

    assert len(sections) == 1
    text = sections[0].text

    # The table's own markdown block is present, with cells in their
    # original row/column positions...
    assert "| Name | Role | Clearance |" in text
    assert "| Alice | Curator | Secret |" in text
    assert "| Bob | Analyst | Top Secret |" in text

    # ...and the surrounding prose is intact and not interleaved with cell
    # text (the pre-fix behavior flattened the table into the middle of the
    # sentence stream with no separators).
    assert "Intro paragraph before the table describing context." in text
    assert "Closing paragraph after the table with more context." in text

    # The old bug's signature: table cells word-joined straight into prose
    # with no delimiter at all.
    assert "context.\nName\nRole" not in text
    assert "context. Name Role" not in text


def test_pdf_page_number_preserved_on_a_page_with_a_table():
    sections = parse_document("table.pdf", _read("table.pdf"))
    assert sections[0].page_or_slide == 1


def test_pdf_without_any_table_is_unaffected():
    sections = parse_document("prose_only.pdf", _read("prose_only.pdf"))
    assert len(sections) == 1
    assert "no tables at all" in sections[0].text
    assert "|" not in sections[0].text


def test_docx_table_extracted_as_markdown_within_its_section():
    sections = parse_document("table.docx", _read("table.docx"))

    # Fixture has two headed sections; the table lives in the first.
    assert [s.heading for s in sections] == ["Section One", "Section Two"]
    section = sections[0]

    assert "Intro paragraph before the table." in section.text
    assert "| Name | Role | Clearance |" in section.text
    assert "| Alice | Curator | Secret |" in section.text
    assert "Closing paragraph after the table." in section.text

    # Document order preserved: intro, then table, then closing paragraph.
    intro_pos = section.text.index("Intro paragraph")
    table_pos = section.text.index("| Name |")
    closing_pos = section.text.index("Closing paragraph")
    assert intro_pos < table_pos < closing_pos


@pytest.mark.parametrize(
    "filename,content,expected_message_fragment",
    [
        ("empty.txt", b"", "empty file"),
        ("corrupt.pdf", b"not a pdf", "corrupt PDF"),
        ("unsupported.exe", b"stuff", "unsupported file type"),
    ],
)
def test_existing_error_paths_unaffected(filename, content, expected_message_fragment):
    with pytest.raises(ParsingError, match=expected_message_fragment):
        parse_document(filename, content)


def test_table_to_markdown_drops_fully_empty_rows():
    from app.parsing import _table_to_markdown

    grid = [["a", "b"], [None, None], ["c", "d"]]
    markdown = _table_to_markdown(grid)
    assert markdown == "| a | b |\n| --- | --- |\n| c | d |"


def test_table_to_markdown_empty_grid_returns_empty_string():
    from app.parsing import _table_to_markdown

    assert _table_to_markdown([]) == ""
    assert _table_to_markdown([[None, None]]) == ""
