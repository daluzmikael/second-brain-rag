"""Structure-aware markdown chunking.

A plain sliding window over tokens cuts markdown tables in half and strips a
passage of the heading that gives it meaning. These notes are heavy on both
("### General Seasonal Model and Schedule" followed by a month-by-month table),
so chunking follows the document's structure instead:

  1. split on ATX headings, tracking the full heading path
  2. split each section into blocks (paragraph, table, list, code fence)
  3. pack whole blocks into chunks, never splitting a table row-wise unless the
     table alone exceeds the budget -- and then repeat its header row

Each chunk is embedded with a context header (note title, folder, heading path)
so a bare table still retrieves for "volleyball season".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.config import CHUNK_MAX_TOKENS, CHUNK_MIN_TOKENS, CHUNK_OVERLAP_TOKENS
from app.tokens import count_tokens, truncate_to_tokens

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_TABLE_RE = re.compile(r"^\s*\|")
_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")
_LIST_RE = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s)")


@dataclass
class Chunk:
    index: int
    content: str
    heading_path: list[str] = field(default_factory=list)
    token_count: int = 0

    @property
    def heading_label(self) -> str:
        return " > ".join(self.heading_path)


@dataclass
class _Block:
    lines: list[str]
    kind: str  # paragraph | table | list | code
    _tokens: int | None = None

    @property
    def text(self) -> str:
        return "\n".join(self.lines).strip("\n")

    @property
    def tokens(self) -> int:
        if self._tokens is None:
            self._tokens = count_tokens(self.text)
        return self._tokens


def _split_sections(text: str) -> list[tuple[list[str], list[str]]]:
    """Return (heading_path, body_lines) per section, in document order."""
    sections: list[tuple[list[str], list[str]]] = []
    # (level, title) so same-level headings replace each other instead of nesting.
    stack: list[tuple[int, str]] = []
    current: list[str] = []
    in_fence = False

    def flush() -> None:
        if any(line.strip() for line in current):
            sections.append(([title for _, title in stack], list(current)))
        current.clear()

    for line in text.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            current.append(line)
            continue

        heading = None if in_fence else _HEADING_RE.match(line)
        if heading:
            flush()
            level = len(heading.group(1))
            title = heading.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            if title:
                stack.append((level, title))
        else:
            current.append(line)

    flush()
    return sections


def _split_blocks(lines: list[str]) -> list[_Block]:
    blocks: list[_Block] = []
    buffer: list[str] = []
    kind = "paragraph"
    in_fence = False

    def flush() -> None:
        nonlocal buffer, kind
        if any(line.strip() for line in buffer):
            blocks.append(_Block(lines=[l for l in buffer], kind=kind))
        buffer = []
        kind = "paragraph"

    for line in lines:
        if _FENCE_RE.match(line):
            if in_fence:
                buffer.append(line)
                in_fence = False
                flush()
            else:
                flush()
                in_fence = True
                kind = "code"
                buffer.append(line)
            continue

        if in_fence:
            buffer.append(line)
            continue

        if not line.strip():
            flush()
            continue

        if _TABLE_RE.match(line):
            if kind != "table":
                flush()
                kind = "table"
            buffer.append(line)
            continue

        if _LIST_RE.match(line) or (kind == "list" and line.startswith((" ", "\t"))):
            if kind != "list":
                flush()
                kind = "list"
            buffer.append(line)
            continue

        if kind in {"table", "list"}:
            flush()
        kind = "paragraph"
        buffer.append(line)

    flush()
    return blocks


def _split_oversized(block: _Block, limit: int) -> list[str]:
    """Break a single block that is too big for one chunk."""
    if block.kind == "table":
        lines = block.lines
        header: list[str] = []
        body = lines
        if len(lines) >= 2 and _TABLE_SEP_RE.match(lines[1]):
            header, body = lines[:2], lines[2:]
        header_tokens = count_tokens("\n".join(header))

        pieces: list[str] = []
        current, current_tokens = [], header_tokens
        for row in body:
            row_tokens = count_tokens(row)
            if current and current_tokens + row_tokens > limit - header_tokens:
                pieces.append("\n".join(header + current))
                current, current_tokens = [row], header_tokens + row_tokens
            else:
                current.append(row)
                current_tokens += row_tokens
        if current:
            pieces.append("\n".join(header + current))
        return pieces

    # Paragraph / list / code: walk lines, then fall back to a token window.
    pieces, current, current_tokens = [], [], 0
    for line in block.lines:
        line_tokens = count_tokens(line)
        if line_tokens > limit:
            if current:
                pieces.append("\n".join(current))
                current, current_tokens = [], 0
            remaining = line
            while remaining:
                head = truncate_to_tokens(remaining, limit)
                if not head:
                    break
                pieces.append(head)
                remaining = remaining[len(head) :].lstrip()
            continue
        if current and current_tokens + line_tokens > limit:
            pieces.append("\n".join(current))
            current, current_tokens = [line], line_tokens
        else:
            current.append(line)
            current_tokens += line_tokens
    if current:
        pieces.append("\n".join(current))
    return pieces


def chunk_markdown(
    text: str,
    max_tokens: int = CHUNK_MAX_TOKENS,
    min_tokens: int = CHUNK_MIN_TOKENS,
    overlap_tokens: int = CHUNK_OVERLAP_TOKENS,
) -> list[Chunk]:
    """Split a note body into structure-aware chunks."""
    chunks: list[Chunk] = []

    for heading_path, lines in _split_sections(text):
        blocks = _split_blocks(lines)
        if not blocks:
            continue

        packed: list[list[_Block]] = []
        current: list[_Block] = []
        current_tokens = 0

        for block in blocks:
            block_tokens = block.tokens
            if block_tokens > max_tokens:
                if current:
                    packed.append(current)
                    current, current_tokens = [], 0
                for piece in _split_oversized(block, max_tokens):
                    packed.append([_Block(lines=piece.splitlines(), kind=block.kind)])
                continue

            if current and current_tokens + block_tokens > max_tokens:
                packed.append(current)
                # Carry the previous tail block forward so a chunk boundary does
                # not orphan the sentence that introduced it.
                tail = current[-1]
                if tail.tokens <= overlap_tokens and len(current) > 1:
                    current, current_tokens = [tail], tail.tokens
                else:
                    current, current_tokens = [], 0

            current.append(block)
            current_tokens += block_tokens

        if current:
            packed.append(current)

        # Fold a runt trailing chunk back into its predecessor.
        texts = ["\n\n".join(b.text for b in group) for group in packed]
        if len(texts) > 1 and count_tokens(texts[-1]) < min_tokens:
            merged = texts[-2] + "\n\n" + texts[-1]
            if count_tokens(merged) <= max_tokens * 1.4:
                texts = texts[:-2] + [merged]

        for body in texts:
            body = body.strip()
            if not body:
                continue
            chunks.append(
                Chunk(
                    index=len(chunks),
                    content=body,
                    heading_path=list(heading_path),
                    token_count=count_tokens(body),
                )
            )

    if not chunks:
        body = text.strip()
        if body:
            chunks.append(Chunk(index=0, content=body, token_count=count_tokens(body)))
    return chunks


def build_embed_text(
    title: str,
    folder: str,
    heading_path: list[str],
    tags: list[str],
    content: str,
) -> str:
    """Contextualize a chunk before embedding it.

    Without this, a chunk that is only a table of months retrieves for nothing
    useful -- the words "volleyball" and "league" live in the title and folder,
    not in the table.
    """
    lines = [f"Note: {title}"]
    if folder:
        lines.append(f"Folder: {folder.replace('/', ' / ')}")
    if heading_path:
        lines.append(f"Section: {' > '.join(heading_path)}")
    if tags:
        lines.append(f"Tags: {', '.join(tags[:12])}")
    lines.append("")
    lines.append(content)
    return "\n".join(lines)
