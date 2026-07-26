"""FR-4: chunk parsed text respecting document structure rather than pure
fixed-token splitting -- chunking never crosses a ParsedSection boundary
(heading/page/slide), and within a section applies a sliding window with the
Section 2 starting point of ~512 tokens, ~15% overlap.

Simplification: "tokens" here are approximated by whitespace-split words, not
a model-specific tokenizer -- close enough for a target chunk size and cheap
to compute without pulling in a tokenizer dependency. Revisit if chunk sizes
need to track the embedding model's actual token count precisely.

issue #90: a section's content_type marks whether its text is an atomic
block that must never be cut by the sliding window -- currently just
"table" (a markdown table from `_table_to_markdown`, or a spreadsheet sheet;
see parsing.py), since those are the only non-prose content_type today. A
table section is emitted as a single chunk even when it's longer than
target_words -- splitting it mid-row would scatter a row's fields across two
separate embedded chunks, which is worse than one oversized chunk.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from app.parsing import ParsedSection

# FR-4: "configurable target chunk size and overlap" -- these were hardcoded
# constants with no way to change them short of editing code; now read from
# the environment, with the same Section 2 starting-point values as defaults.
DEFAULT_TARGET_WORDS = int(os.environ.get("CHUNK_TARGET_WORDS", 512))
DEFAULT_OVERLAP_RATIO = float(os.environ.get("CHUNK_OVERLAP_RATIO", 0.15))


@dataclass
class Chunk:
    text: str
    chunk_index: int
    heading: str | None = None
    page_or_slide: int | None = None
    # issue #89: inherited straight from the ParsedSection this chunk was cut
    # from ("text" or "table") -- chunking never crosses a section boundary,
    # so a chunk's content_type is always exactly its source section's, no
    # per-chunk detection needed.
    content_type: str = "text"


def chunk_sections(
    sections: list[ParsedSection],
    *,
    target_words: int = DEFAULT_TARGET_WORDS,
    overlap_ratio: float = DEFAULT_OVERLAP_RATIO,
) -> list[Chunk]:
    overlap = int(target_words * overlap_ratio)
    chunks: list[Chunk] = []

    for section in sections:
        text = section.text.strip()
        if not text:
            continue

        # issue #90: atomic sections (currently just tables) are kept whole,
        # regardless of target_words -- the sliding window below only ever
        # runs on prose, so it can no longer land a cut inside a table row.
        if section.content_type != "text":
            chunks.append(
                Chunk(
                    text=text,
                    chunk_index=len(chunks),
                    heading=section.heading,
                    page_or_slide=section.page_or_slide,
                    content_type=section.content_type,
                )
            )
            continue

        words = text.split()
        start = 0
        while True:
            end = min(start + target_words, len(words))
            chunks.append(
                Chunk(
                    text=" ".join(words[start:end]),
                    chunk_index=len(chunks),
                    heading=section.heading,
                    page_or_slide=section.page_or_slide,
                    content_type=section.content_type,
                )
            )
            if end == len(words):
                break
            start = end - overlap

    return chunks
