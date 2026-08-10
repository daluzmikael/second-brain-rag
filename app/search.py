from dataclasses import dataclass

from app.db import get_connection
from app.embeddings import embed_query


@dataclass
class SearchResult:
    content: str
    source_path: str
    title: str
    chunk_index: int
    similarity: float


def search(query: str, top_k: int = 5) -> list[SearchResult]:
    query_vector = embed_query(query)

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                c.content,
                d.source_path,
                d.title,
                c.chunk_index,
                1 - (c.embedding <=> %s::vector) AS similarity
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            ORDER BY c.embedding <=> %s::vector
            LIMIT %s
            """,
            (query_vector, query_vector, top_k),
        ).fetchall()

    return [
        SearchResult(
            content=row[0],
            source_path=row[1],
            title=row[2],
            chunk_index=row[3],
            similarity=row[4],
        )
        for row in rows
    ]
