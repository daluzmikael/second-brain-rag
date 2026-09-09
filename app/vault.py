"""Reading an Obsidian vault into plain note records.

Handles the Obsidian-specific bits that a naive file walk gets wrong: YAML
frontmatter, `[[wikilinks]]`, `#tags`, inline HTML, and the folders Obsidian
keeps for itself.
"""

from __future__ import annotations

import hashlib
import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from app.config import IGNORED_DIRS, NOTE_SUFFIXES, VAULT_NAME
from app.tokens import count_tokens

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)
_WIKILINK_RE = re.compile(r"\[\[([^\[\]|#]+)(?:#[^\[\]|]*)?(?:\|([^\[\]]*))?\]\]")
_TAG_RE = re.compile(r"(?:(?<=\s)|\A)#([A-Za-z][\w/-]*)")
_INLINE_HTML_RE = re.compile(
    r"</?(?:u|b|i|em|strong|mark|small|sub|sup|span|div|center|br|hr)\s*/?>",
    re.IGNORECASE,
)
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_TABLE_ROW_RE = re.compile(r"^\s*\|")


def has_written_content(body: str) -> bool:
    """Is there anything here beyond headings?

    This vault is full of placeholders -- `Iceland.md` is the single line
    "# Iceland". Indexed as-is, the bot answers "what did I write about
    Iceland?" by reciting the heading back. Treating these as empty lets it say
    the honest thing instead.
    """
    for line in body.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return True
    return False


def obsidian_url(rel_path: str) -> str:
    """Deep link that opens `rel_path` in Obsidian, for clickable citations."""
    file_ref = rel_path
    for suffix in NOTE_SUFFIXES:
        if file_ref.lower().endswith(suffix):
            file_ref = file_ref[: -len(suffix)]
            break
    return (
        "obsidian://open?vault="
        + urllib.parse.quote(VAULT_NAME)
        + "&file="
        + urllib.parse.quote(file_ref)
    )


@dataclass
class Note:
    """One markdown file, parsed."""

    rel_path: str
    abs_path: Path
    title: str
    folder: str
    body: str
    frontmatter: dict = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    content_hash: str = ""
    mtime: float = 0.0
    size: int = 0
    token_count: int = 0

    @property
    def obsidian_url(self) -> str:
        return obsidian_url(self.rel_path)

    @property
    def display_label(self) -> str:
        return f"{self.title} — {self.rel_path}" if self.folder else self.title


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    raw, body = match.group(1), text[match.end() :]
    try:
        import yaml

        data = yaml.safe_load(raw)
    except Exception:
        data = None
    if not isinstance(data, dict):
        data = {}
    return data, body


def _strip_code_fences(text: str) -> str:
    """Blank out fenced code so tag/link scanning ignores code samples."""
    out, in_fence = [], False
    for line in text.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        out.append("" if in_fence else line)
    return "\n".join(out)


def _collect_tags(frontmatter: dict, body: str) -> list[str]:
    tags: list[str] = []
    raw = frontmatter.get("tags") or frontmatter.get("tag")
    if isinstance(raw, str):
        tags.extend(part.strip() for part in re.split(r"[,\s]+", raw) if part.strip())
    elif isinstance(raw, (list, tuple)):
        tags.extend(str(part).strip() for part in raw if str(part).strip())

    tags.extend(_TAG_RE.findall(_strip_code_fences(body)))

    seen, unique = set(), []
    for tag in tags:
        key = tag.lstrip("#").lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(tag.lstrip("#"))
    return unique


def _collect_links(body: str) -> list[str]:
    seen, links = set(), []
    for target, _alias in _WIKILINK_RE.findall(_strip_code_fences(body)):
        name = target.strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            links.append(name)
    return links


def _normalize_tables(text: str) -> str:
    """Squeeze the alignment padding out of markdown tables.

    Obsidian pads every cell so the pipes line up in the editor, which in this
    vault produces 3000-character table rows that are mostly spaces. Left alone,
    a single row can eat 250 tokens of pure whitespace -- crowding out real
    content in both the chunk budget and the embedding.
    """
    out = []
    in_fence = False
    for line in text.split("\n"):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            out.append(line)
            continue
        if not in_fence and _TABLE_ROW_RE.match(line):
            line = line.strip()
            line = re.sub(r"-{3,}", "---", line)   # `| ------------ |` -> `| --- |`
            line = re.sub(r"[ \t]{2,}", " ", line)
        out.append(line)
    return "\n".join(out)


def _clean_body(text: str) -> str:
    text = _INLINE_HTML_RE.sub("", text)
    text = text.replace(" ", " ").replace("\r\n", "\n").replace("\r", "\n")
    text = _normalize_tables(text)
    # Collapse runs of blank lines; they only waste tokens.
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Trailing spaces survive table normalization on non-table lines.
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return text.strip()


def _title_from(path: Path, frontmatter: dict, body: str) -> str:
    for key in ("title", "Title"):
        value = frontmatter.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    stem = path.stem.strip()
    if stem:
        return stem
    # Untitled file: fall back to its first heading.
    for line in body.splitlines():
        heading = re.match(r"^#{1,6}\s+(.+)$", line.strip())
        if heading:
            return heading.group(1).strip()
    return path.name


def parse_note(abs_path: Path, vault_root: Path) -> Note | None:
    try:
        raw = abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    frontmatter, body = _parse_frontmatter(raw)
    body = _clean_body(body)
    rel_path = abs_path.relative_to(vault_root).as_posix()
    folder = str(Path(rel_path).parent) if Path(rel_path).parent != Path(".") else ""

    try:
        stat = abs_path.stat()
        mtime, size = stat.st_mtime, stat.st_size
    except OSError:
        mtime, size = 0.0, len(raw)

    return Note(
        rel_path=rel_path,
        abs_path=abs_path,
        title=_title_from(abs_path, frontmatter, body),
        folder=folder,
        body=body,
        frontmatter=frontmatter if isinstance(frontmatter, dict) else {},
        tags=_collect_tags(frontmatter, body),
        links=_collect_links(body),
        content_hash=hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest(),
        mtime=mtime,
        size=size,
        token_count=count_tokens(body),
    )


def iter_note_paths(vault_root: Path):
    """Every indexable file in the vault, skipping Obsidian's own folders."""
    vault_root = vault_root.resolve()
    stack = [vault_root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            if name.startswith("."):
                continue
            if entry.is_dir():
                if name in IGNORED_DIRS:
                    continue
                stack.append(entry)
            elif entry.suffix.lower() in NOTE_SUFFIXES:
                yield entry


def load_notes(vault_root: Path) -> list[Note]:
    notes = []
    for path in iter_note_paths(vault_root):
        note = parse_note(path, vault_root.resolve())
        # Empty notes are kept: a note titled "Brasil" with nothing in it is still
        # a real answer to "what do I have for Brasil?" -- just an honest one.
        if note is not None:
            notes.append(note)
    return sorted(notes, key=lambda n: n.rel_path)
