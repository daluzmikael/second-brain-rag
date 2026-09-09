"""Local SQLite index: notes, chunks, embeddings, and a keyword index.

Replaces the old Postgres/pgvector setup. At this vault's scale (146 notes, ~230
chunks) a brute-force numpy dot product over the whole embedding matrix takes
well under a millisecond -- far less than a round trip to a database server --
and it means the bot boots with no daemon, no container, and no setup.

Keyword search uses SQLite's built-in FTS5 with porter stemming, so "seasonal"
matches "season" and proper nouns like "NAVC" match exactly.
"""

from __future__ import annotations

import functools
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.config import EMBEDDING_DIMENSIONS, INDEX_PATH

_FTS_TOKENIZER = "porter unicode61 remove_diacritics 2"

SCHEMA = f"""
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
    id           INTEGER PRIMARY KEY,
    rel_path     TEXT    NOT NULL UNIQUE,
    title        TEXT    NOT NULL,
    folder       TEXT    NOT NULL DEFAULT '',
    tags         TEXT    NOT NULL DEFAULT '[]',
    links        TEXT    NOT NULL DEFAULT '[]',
    frontmatter  TEXT    NOT NULL DEFAULT '{{}}',
    body         TEXT    NOT NULL,
    token_count  INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT    NOT NULL,
    mtime        REAL    NOT NULL DEFAULT 0,
    size         INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS chunks (
    id           INTEGER PRIMARY KEY,
    note_id      INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    chunk_index  INTEGER NOT NULL,
    heading_path TEXT    NOT NULL DEFAULT '[]',
    content      TEXT    NOT NULL,
    embed_text   TEXT    NOT NULL,
    token_count  INTEGER NOT NULL DEFAULT 0,
    embedding    BLOB
);

CREATE INDEX IF NOT EXISTS chunks_note_idx ON chunks(note_id, chunk_index);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
    USING fts5(text, tokenize='{_FTS_TOKENIZER}');

CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts
    USING fts5(title, folder, tags, links, tokenize='{_FTS_TOKENIZER}');
"""

_matrix_lock = threading.Lock()
_matrix_cache: tuple[str, np.ndarray, list[int]] | None = None


@dataclass
class ChunkRow:
    chunk_id: int
    note_id: int
    chunk_index: int
    heading_path: list[str]
    content: str
    token_count: int
    rel_path: str
    title: str
    folder: str


def retry_on_io_error(attempts: int = 4, delay: float = 0.15):
    """Retry a read that failed with a transient SQLite `disk I/O error`.

    These surface when the volume is under pressure -- a nearly-full disk, or a
    sleeping external one -- and are usually gone milliseconds later. A query
    that genuinely cannot be served still raises after the last attempt.
    """

    def decorate(function):
        @functools.wraps(function)
        def wrapper(*args, **kwargs):
            for attempt in range(attempts):
                try:
                    return function(*args, **kwargs)
                except sqlite3.OperationalError as error:
                    transient = "disk i/o" in str(error).lower() or "locked" in str(error).lower()
                    if not transient or attempt == attempts - 1:
                        raise
                    time.sleep(delay * (2**attempt))
            raise AssertionError("unreachable")

        return wrapper

    return decorate


@contextmanager
def connect(path: Path | None = None, readonly: bool = False):
    target = Path(path or INDEX_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        if not readonly:
            conn.commit()
    finally:
        conn.close()


def init_schema(path: Path | None = None) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)


@retry_on_io_error()
def index_exists(path: Path | None = None) -> bool:
    """Is there a usable index?

    Transient I/O errors are retried rather than reported as "no index" -- on a
    struggling disk, answering False here would tell the user their vault was
    never indexed when in fact the file is sitting right there, intact.
    """
    target = Path(path or INDEX_PATH)
    if not target.exists():
        return False
    with connect(target, readonly=True) as conn:
        row = conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='chunks'"
        ).fetchone()
        if not row or not row[0]:
            return False
        return bool(conn.execute("SELECT count(*) FROM chunks").fetchone()[0])


# --- meta ------------------------------------------------------------------
def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


def bump_index_version(conn: sqlite3.Connection) -> None:
    """Invalidate the in-process embedding matrix cache."""
    current = int(get_meta(conn, "index_version", "0") or 0)
    set_meta(conn, "index_version", str(current + 1))


# --- vectors ---------------------------------------------------------------
def pack_vector(vector) -> bytes:
    """Store L2-normalized float32 so cosine similarity is a plain dot product."""
    array = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(array))
    if norm > 0:
        array = array / norm
    return array.astype(np.float32).tobytes()


def vector_widths(conn: sqlite3.Connection) -> set[int]:
    """Distinct vector widths present. More than one means a broken migration."""
    return {
        int(row[0]) // 4
        for row in conn.execute(
            "SELECT DISTINCT length(embedding) FROM chunks WHERE embedding IS NOT NULL"
        )
        if row[0]
    }


def vector_width(conn: sqlite3.Connection) -> int:
    """Width of the stored vectors, read from the data rather than assumed."""
    row = conn.execute(
        "SELECT length(embedding) FROM chunks WHERE embedding IS NOT NULL LIMIT 1"
    ).fetchone()
    if row and row[0]:
        return int(row[0]) // 4  # float32
    return EMBEDDING_DIMENSIONS


def embedding_matrix(conn: sqlite3.Connection) -> tuple[np.ndarray, list[int]]:
    """All chunk embeddings as one (n, dim) matrix, cached per index version."""
    global _matrix_cache
    version = str(get_meta(conn, "index_version", "0"))
    with _matrix_lock:
        if _matrix_cache and _matrix_cache[0] == version:
            return _matrix_cache[1], _matrix_cache[2]

        rows = conn.execute(
            "SELECT id, embedding FROM chunks WHERE embedding IS NOT NULL ORDER BY id"
        ).fetchall()
        ids = [row[0] for row in rows]
        if not rows:
            matrix = np.zeros((0, vector_width(conn)), dtype=np.float32)
        else:
            vectors = [np.frombuffer(row[1], dtype=np.float32) for row in rows]
            widths = {v.shape[0] for v in vectors}
            if len(widths) > 1:
                raise RuntimeError(
                    f"Index contains vectors of different widths {sorted(widths)}, "
                    "so they cannot be compared.\n"
                    "  Rebuild it:  ./brain index --rebuild"
                )
            matrix = np.vstack(vectors)
        _matrix_cache = (version, matrix, ids)
        return matrix, ids


def clear_matrix_cache() -> None:
    global _matrix_cache
    with _matrix_lock:
        _matrix_cache = None


# --- writes ----------------------------------------------------------------
def stored_hashes(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        row["rel_path"]: row["content_hash"]
        for row in conn.execute("SELECT rel_path, content_hash FROM notes")
    }


def stored_fingerprints(conn: sqlite3.Connection) -> dict[str, tuple[str, float, int]]:
    """rel_path -> (content_hash, mtime, size), so unchanged files stay unopened."""
    return {
        row["rel_path"]: (row["content_hash"], float(row["mtime"]), int(row["size"]))
        for row in conn.execute("SELECT rel_path, content_hash, mtime, size FROM notes")
    }


def delete_note(conn: sqlite3.Connection, rel_path: str) -> None:
    row = conn.execute("SELECT id FROM notes WHERE rel_path=?", (rel_path,)).fetchone()
    if not row:
        return
    note_id = row[0]
    chunk_ids = [
        r[0] for r in conn.execute("SELECT id FROM chunks WHERE note_id=?", (note_id,))
    ]
    for chunk_id in chunk_ids:
        conn.execute("DELETE FROM chunks_fts WHERE rowid=?", (chunk_id,))
    conn.execute("DELETE FROM notes_fts WHERE rowid=?", (note_id,))
    conn.execute("DELETE FROM chunks WHERE note_id=?", (note_id,))
    conn.execute("DELETE FROM notes WHERE id=?", (note_id,))


def upsert_note(conn: sqlite3.Connection, note, chunks: list[dict]) -> int:
    """Replace a note and all of its chunks. `chunks` carry optional embeddings."""
    delete_note(conn, note.rel_path)

    cursor = conn.execute(
        """
        INSERT INTO notes (rel_path, title, folder, tags, links, frontmatter,
                           body, token_count, content_hash, mtime, size)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            note.rel_path,
            note.title,
            note.folder,
            json.dumps(note.tags),
            json.dumps(note.links),
            json.dumps(_jsonable(note.frontmatter)),
            note.body,
            note.token_count,
            note.content_hash,
            note.mtime,
            note.size,
        ),
    )
    note_id = int(cursor.lastrowid)

    conn.execute(
        "INSERT INTO notes_fts(rowid, title, folder, tags, links) VALUES (?, ?, ?, ?, ?)",
        (
            note_id,
            note.title,
            note.folder.replace("/", " "),
            " ".join(note.tags),
            " ".join(note.links),
        ),
    )

    for chunk in chunks:
        cursor = conn.execute(
            """
            INSERT INTO chunks (note_id, chunk_index, heading_path, content,
                                embed_text, token_count, embedding)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                note_id,
                chunk["chunk_index"],
                json.dumps(chunk["heading_path"]),
                chunk["content"],
                chunk["embed_text"],
                chunk["token_count"],
                chunk.get("embedding"),
            ),
        )
        conn.execute(
            "INSERT INTO chunks_fts(rowid, text) VALUES (?, ?)",
            (int(cursor.lastrowid), chunk["embed_text"]),
        )
    return note_id


def _jsonable(value):
    """Frontmatter can hold dates and other YAML types; make them storable."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


# --- reads -----------------------------------------------------------------
def _escape_fts(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


def fts_chunk_search(
    conn: sqlite3.Connection, match_expr: str, limit: int
) -> list[tuple[int, float]]:
    if not match_expr:
        return []
    try:
        rows = conn.execute(
            "SELECT rowid, bm25(chunks_fts) AS score FROM chunks_fts "
            "WHERE chunks_fts MATCH ? ORDER BY score LIMIT ?",
            (match_expr, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [(int(r[0]), float(r[1])) for r in rows]


def fts_note_search(
    conn: sqlite3.Connection, match_expr: str, limit: int
) -> list[tuple[int, float]]:
    if not match_expr:
        return []
    try:
        rows = conn.execute(
            "SELECT rowid, bm25(notes_fts) AS score FROM notes_fts "
            "WHERE notes_fts MATCH ? ORDER BY score LIMIT ?",
            (match_expr, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [(int(r[0]), float(r[1])) for r in rows]


def chunk_rows(conn: sqlite3.Connection, chunk_ids: list[int]) -> dict[int, ChunkRow]:
    if not chunk_ids:
        return {}
    placeholders = ",".join("?" * len(chunk_ids))
    rows = conn.execute(
        f"""
        SELECT c.id, c.note_id, c.chunk_index, c.heading_path, c.content,
               c.token_count, n.rel_path, n.title, n.folder
        FROM chunks c JOIN notes n ON n.id = c.note_id
        WHERE c.id IN ({placeholders})
        """,
        chunk_ids,
    ).fetchall()
    return {
        int(r["id"]): ChunkRow(
            chunk_id=int(r["id"]),
            note_id=int(r["note_id"]),
            chunk_index=int(r["chunk_index"]),
            heading_path=json.loads(r["heading_path"] or "[]"),
            content=r["content"],
            token_count=int(r["token_count"]),
            rel_path=r["rel_path"],
            title=r["title"],
            folder=r["folder"],
        )
        for r in rows
    }


def note_chunk_ids(conn: sqlite3.Connection, note_ids: list[int]) -> list[int]:
    if not note_ids:
        return []
    placeholders = ",".join("?" * len(note_ids))
    return [
        int(r[0])
        for r in conn.execute(
            f"SELECT id FROM chunks WHERE note_id IN ({placeholders})", note_ids
        )
    ]


def first_chunk_ids(conn: sqlite3.Connection, note_id: int, limit: int = 2) -> list[int]:
    """Leading chunks of a note, used when only its title matched the query."""
    return [
        int(r[0])
        for r in conn.execute(
            "SELECT id FROM chunks WHERE note_id=? ORDER BY chunk_index LIMIT ?",
            (note_id, limit),
        )
    ]


def note_record(conn: sqlite3.Connection, note_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()


@retry_on_io_error()
def stats(path: Path | None = None) -> dict:
    if not index_exists(path):
        return {"indexed": False, "notes": 0, "chunks": 0, "embedded_chunks": 0}
    with connect(path, readonly=True) as conn:
        notes = conn.execute("SELECT count(*) FROM notes").fetchone()[0]
        chunks = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
        embedded = conn.execute(
            "SELECT count(*) FROM chunks WHERE embedding IS NOT NULL"
        ).fetchone()[0]
        tokens = conn.execute(
            "SELECT coalesce(sum(token_count), 0) FROM notes"
        ).fetchone()[0]
        return {
            "indexed": True,
            "notes": int(notes),
            "chunks": int(chunks),
            "embedded_chunks": int(embedded),
            "note_tokens": int(tokens),
            "embedding_identity": get_meta(conn, "embedding_identity"),
            "embedding_dimensions": int(get_meta(conn, "embedding_dimensions", "0") or 0),
            "built_at": get_meta(conn, "built_at"),
            "vault_path": get_meta(conn, "vault_path"),
            "fake_embeddings": str(get_meta(conn, "embedding_identity", "")).startswith("fake:"),
        }
