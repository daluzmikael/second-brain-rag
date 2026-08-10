import hashlib
import sys
from pathlib import Path

from app.chunking import chunk_text
from app.db import get_connection
from app.embeddings import embed_texts

BATCH_SIZE = 64


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _iter_docs(docs_dir: Path):
    for path in sorted(docs_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in {".md", ".txt"}:
            yield path


def ingest_directory(docs_dir: Path) -> None:
    docs_dir = docs_dir.resolve()
    if not docs_dir.is_dir():
        raise FileNotFoundError(f"No such directory: {docs_dir}")

    paths = list(_iter_docs(docs_dir))
    if not paths:
        print(f"No .md/.txt files found under {docs_dir}")
        return

    with get_connection() as conn:
        for path in paths:
            content = path.read_text(encoding="utf-8", errors="ignore")
            content_hash = _hash(content)
            title = path.stem.replace("_", " ").replace("-", " ")
            source_path = str(path.relative_to(docs_dir.parent))

            existing = conn.execute(
                "SELECT id FROM documents WHERE content_hash = %s", (content_hash,)
            ).fetchone()
            if existing:
                print(f"skip (unchanged): {source_path}")
                continue

            conn.execute("DELETE FROM documents WHERE source_path = %s", (source_path,))

            doc_id = conn.execute(
                """
                INSERT INTO documents (source_path, title, content_hash)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (source_path, title, content_hash),
            ).fetchone()[0]

            chunks = chunk_text(content)
            if not chunks:
                print(f"skip (empty): {source_path}")
                continue

            for batch_start in range(0, len(chunks), BATCH_SIZE):
                batch = chunks[batch_start : batch_start + BATCH_SIZE]
                vectors = embed_texts(batch)
                for offset, (chunk, vector) in enumerate(zip(batch, vectors)):
                    conn.execute(
                        """
                        INSERT INTO chunks (document_id, chunk_index, content, embedding)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (doc_id, batch_start + offset, chunk, vector),
                    )

            print(f"ingested: {source_path} ({len(chunks)} chunks)")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/docs")
    ingest_directory(target)
