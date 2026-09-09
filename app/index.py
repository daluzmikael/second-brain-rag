"""Build and refresh the local index from the vault.

Refreshing is deliberately stingy about reading files. The vault sits in
~/Documents, which is iCloud-synced, and a full read of all 150 notes has been
measured at 16 seconds while the same read takes 0.4s warm. So a note whose
mtime and size are unchanged is never opened at all, and a note that is opened
but hashes the same is never re-embedded. Editing one note re-reads one note.

Changing the embedding model forces a full rebuild, since vectors from different
models are not comparable.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

from app import store
from app.chunking import Chunk, build_embed_text, chunk_markdown
from app.config import (
    EMBED_FLUSH_CHUNKS,
    EMPTY_NOTE_MARKER,
    INDEX_PATH,
    VAULT_PATH,
    require_embedding_key,
)
from app.embeddings import embed_texts, embedding_identity
from app.tokens import count_tokens
from app.vault import has_written_content, iter_note_paths, parse_note


def _unchanged_on_disk(fingerprint, mtime: float, size: int) -> bool:
    """Cheap staleness check that avoids opening the file."""
    if fingerprint is None:
        return False
    _stored_hash, stored_mtime, stored_size = fingerprint
    return stored_size == size and abs(stored_mtime - mtime) < 1e-6


def build_index(
    vault_path: Path | None = None,
    index_path: Path | None = None,
    rebuild: bool = False,
    log=print,
) -> dict:
    """Sync the index with the vault. Returns a summary dict."""
    vault = Path(vault_path or VAULT_PATH).expanduser()
    if not vault.is_dir():
        raise FileNotFoundError(
            f"Vault not found: {vault}\n"
            "  Set VAULT_PATH in .env to your Obsidian vault folder."
        )
    vault = vault.resolve()

    require_embedding_key()
    target = Path(index_path or INDEX_PATH)
    store.init_schema(target)

    started = time.time()
    log(f"Scanning vault: {vault}")
    paths = list(iter_note_paths(vault))
    log(f"Found {len(paths)} note files.")

    summary = {
        "files_seen": len(paths),
        "added": 0,
        "updated": 0,
        "unchanged": 0,
        "removed": 0,
        "skipped_unreadable": 0,
        "chunks_written": 0,
    }

    with store.connect(target) as conn:
        identity = embedding_identity()
        stored_identity = store.get_meta(conn, "embedding_identity")
        has_vectors = bool(
            conn.execute("SELECT 1 FROM chunks WHERE embedding IS NOT NULL LIMIT 1").fetchone()
        )
        # An index carrying vectors but no identity predates this field. Treat it
        # as a mismatch: leaving it alone would mix vector widths from two
        # different models in one table, which is unrecoverable at query time.
        identity_changed = has_vectors and stored_identity != identity
        widths = store.vector_widths(conn)
        mixed_widths = len(widths) > 1

        if rebuild or identity_changed or mixed_widths:
            if mixed_widths:
                log(f"Index holds vectors of differing widths {sorted(widths)}; rebuilding.")
            elif identity_changed:
                log(f"Embeddings changed ({stored_identity} -> {identity}); rebuilding.")
            else:
                log("Rebuilding index from scratch.")
            for table in ("chunks_fts", "notes_fts", "chunks", "notes"):
                conn.execute(f"DELETE FROM {table}")

        fingerprints = store.stored_fingerprints(conn)
        seen: set[str] = set()

        # Notes are embedded in batches spanning multiple notes, not one request
        # per note, so a full build is a few round trips instead of hundreds.
        pending: list[tuple] = []
        pending_chunks = 0

        def flush() -> None:
            nonlocal pending, pending_chunks
            if not pending:
                return

            inputs = [text for _note, _chunks, texts, _is_new in pending for text in texts]
            vectors = embed_texts(inputs)

            offset = 0
            for note, chunks, texts, is_new in pending:
                note_vectors = vectors[offset : offset + len(texts)]
                offset += len(texts)

                payload = [
                    {
                        "chunk_index": chunk.index,
                        "heading_path": chunk.heading_path,
                        "content": chunk.content,
                        "embed_text": embed_text,
                        "token_count": chunk.token_count,
                        "embedding": store.pack_vector(vector),
                    }
                    for chunk, embed_text, vector in zip(chunks, texts, note_vectors)
                ]
                store.upsert_note(conn, note, payload)
                summary["chunks_written"] += len(payload)

                if is_new:
                    summary["added"] += 1
                    log(f"  + {note.rel_path} ({len(payload)} chunks)")
                else:
                    summary["updated"] += 1
                    log(f"  ~ {note.rel_path} ({len(payload)} chunks)")

            pending = []
            pending_chunks = 0

        for path in paths:
            rel_path = path.relative_to(vault).as_posix()
            seen.add(rel_path)
            fingerprint = fingerprints.get(rel_path)

            try:
                stat = path.stat()
                mtime, size = stat.st_mtime, stat.st_size
            except OSError:
                mtime, size = 0.0, -1

            # Untouched since last index: never open the file.
            if _unchanged_on_disk(fingerprint, mtime, size):
                summary["unchanged"] += 1
                continue

            note = parse_note(path, vault)
            if note is None:
                summary["skipped_unreadable"] += 1
                log(f"  ! {rel_path} (could not be read)")
                continue

            # Touched but identical: refresh the fingerprint, skip re-embedding.
            if fingerprint and fingerprint[0] == note.content_hash:
                conn.execute(
                    "UPDATE notes SET mtime=?, size=? WHERE rel_path=?",
                    (note.mtime, note.size, rel_path),
                )
                summary["unchanged"] += 1
                continue

            chunks = chunk_markdown(note.body) if has_written_content(note.body) else []
            if not chunks:
                # Title-only note. Index the title anyway so the bot can answer
                # "you have that note, but you never wrote anything in it".
                chunks = [
                    Chunk(
                        index=0,
                        content=EMPTY_NOTE_MARKER,
                        heading_path=[],
                        token_count=count_tokens(EMPTY_NOTE_MARKER),
                    )
                ]

            embed_inputs = [
                build_embed_text(
                    note.title, note.folder, chunk.heading_path, note.tags, chunk.content
                )
                for chunk in chunks
            ]
            pending.append((note, chunks, embed_inputs, fingerprint is None))
            pending_chunks += len(chunks)
            if pending_chunks >= EMBED_FLUSH_CHUNKS:
                flush()

        flush()

        for rel_path in set(fingerprints) - seen:
            store.delete_note(conn, rel_path)
            summary["removed"] += 1
            log(f"  - {rel_path} (gone from vault)")

        store.set_meta(conn, "embedding_identity", identity)
        store.set_meta(conn, "embedding_dimensions", str(store.vector_width(conn)))
        store.set_meta(conn, "vault_path", str(vault))
        store.set_meta(conn, "built_at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        store.bump_index_version(conn)

    store.clear_matrix_cache()
    final = store.stats(target)
    summary.update(notes=final["notes"], chunks=final["chunks"])
    log(
        "Index ready in {elapsed:.1f}s: {notes} notes, {chunks} chunks "
        "(+{added} new, ~{updated} changed, ={unchanged} unchanged, -{removed} removed)".format(
            elapsed=time.time() - started,
            notes=final["notes"],
            chunks=final["chunks"],
            added=summary["added"],
            updated=summary["updated"],
            unchanged=summary["unchanged"],
            removed=summary["removed"],
        )
    )
    return summary
