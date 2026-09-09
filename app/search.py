"""Hybrid retrieval over the indexed vault.

Two searchers run on every query and their rankings are fused:

  * vector  -- embedding cosine similarity, for "seasonal structure" matching a
               note that says "General Seasonal Model and Schedule"
  * keyword -- FTS5 BM25 with porter stemming, for exact terms a vector misses
               ("NAVC", "Ballon d'or", a note title typed verbatim)

A third, weaker signal boosts notes whose *title, folder, tags or wikilinks*
match, because questions here usually name the thing ("my volleyball league").
Rankings are combined with Reciprocal Rank Fusion, which needs no score
calibration between the two very different scales.

Context is then assembled per *note*, not per chunk. Most notes in this vault are
a few hundred tokens, so the bot can hand the model whole notes -- no half-tables,
no missing the one line that answered the question.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

from app import store
from app.config import (
    CONTEXT_TOKEN_BUDGET,
    EMPTY_NOTE_MARKER,
    MAX_CONTEXT_NOTES,
    MIN_SCORE_RATIO,
    RRF_K,
    TOP_K_CHUNKS,
    WEIGHT_FTS,
    WEIGHT_TITLE,
    WEIGHT_TITLE_EXACT,
    WEIGHT_TITLE_OVERLAP,
    WEIGHT_VECTOR,
)
from app.embeddings import embed_query
from app.tokens import count_tokens, truncate_to_tokens
from app.vault import has_written_content, obsidian_url

_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’/-]*")

# Question scaffolding that carries no retrieval signal. Kept deliberately short:
# over-filtering throws away real query terms.
_STOPWORDS = {
    "a", "about", "all", "am", "an", "and", "any", "anything", "are", "as", "at",
    "be", "been", "but", "by", "can", "did", "do", "does", "down", "for", "from", "get",
    "give", "had", "has", "have", "how", "i", "in", "into", "is", "it", "its",
    "know", "like", "me", "mine", "my", "note", "notes", "of", "on", "or", "our",
    "out", "put", "recall", "remember", "said", "say", "show", "so", "some", "something",
    "tell", "that", "the", "their", "them", "then", "there", "these", "they", "this",
    "those", "to", "up", "us", "was", "we", "were", "what", "whats", "when", "where",
    "which", "who", "why", "will", "with", "write", "wrote", "you", "your",
}


@dataclass
class ChunkHit:
    chunk_id: int
    note_id: int
    chunk_index: int
    rel_path: str
    title: str
    folder: str
    heading_path: list[str]
    content: str
    token_count: int
    score: float = 0.0
    vector_rank: int | None = None
    fts_rank: int | None = None
    title_rank: int | None = None
    similarity: float | None = None

    @property
    def heading_label(self) -> str:
        return " > ".join(self.heading_path)


@dataclass
class NoteHit:
    note_id: int
    rel_path: str
    title: str
    folder: str
    score: float
    chunk_hits: list[ChunkHit] = field(default_factory=list)
    title_rank: int | None = None


@dataclass
class NoteContext:
    rel_path: str
    title: str
    folder: str
    text: str
    score: float
    sections: list[str] = field(default_factory=list)
    token_count: int = 0
    truncated: bool = False

    @property
    def url(self) -> str:
        return obsidian_url(self.rel_path)

    @property
    def label(self) -> str:
        return self.title if not self.folder else f"{self.title} ({self.folder})"


def query_terms(query: str) -> list[str]:
    """Content words from a natural-language question."""
    words = _WORD_RE.findall(query.lower())
    kept = [w for w in words if w not in _STOPWORDS and len(w) > 1]
    return kept or [w for w in words if len(w) > 1]


def build_fts_query(query: str) -> str:
    """An FTS5 MATCH expression: any content word, BM25 ranks the overlap."""
    terms = query_terms(query)
    if not terms:
        return ""
    quoted = ['"' + term.replace('"', '""') + '"' for term in dict.fromkeys(terms)]
    return " OR ".join(quoted)


def _vector_ranking(conn, query: str, limit: int) -> list[tuple[int, float]]:
    matrix, ids = store.embedding_matrix(conn)
    if matrix.shape[0] == 0:
        return []
    vector = np.asarray(embed_query(query), dtype=np.float32)
    norm = float(np.linalg.norm(vector))
    if norm > 0:
        vector = vector / norm
    if vector.shape[0] != matrix.shape[1]:
        return []
    similarities = matrix @ vector
    count = min(limit, similarities.shape[0])
    top = np.argpartition(-similarities, count - 1)[:count]
    top = top[np.argsort(-similarities[top])]
    return [(ids[int(i)], float(similarities[int(i)])) for i in top]


def _title_affinity(query_words: set[str], title: str) -> float:
    """How much of the question is answered by the note's title alone.

    Notes here are often named for their subject and say it nowhere else --
    "Japan.md" contains only "Tokyo / Dotonbori" -- so a title that covers the
    question deserves to outrank a long note that merely mentions the word.
    """
    if not query_words:
        return 0.0
    title_words = set(query_terms(title))
    if not title_words:
        return 0.0
    overlap = len(query_words & title_words) / len(query_words)
    bonus = WEIGHT_TITLE_OVERLAP * overlap
    if overlap == 1.0 and title_words == query_words:
        bonus += WEIGHT_TITLE_EXACT
    return bonus


def _aggregate(scores: list[float]) -> float:
    """Combine a note's chunk scores without rewarding sheer note length.

    A 23-chunk note would otherwise out-score a precise 1-chunk match simply by
    accumulating weak hits, so only the best three chunks contribute and the
    2nd and 3rd are heavily discounted.
    """
    ranked = sorted(scores, reverse=True)[:3]
    weights = (1.0, 0.25, 0.15)
    return sum(score * weight for score, weight in zip(ranked, weights))


@store.retry_on_io_error()
def rank_notes(query: str, top_k: int = TOP_K_CHUNKS) -> list[NoteHit]:
    """Hybrid retrieval, aggregated to notes.

    Chunk-level fusion (vector + BM25) decides which passages matched; the title
    signal is added once per *note* rather than once per chunk, so a long note
    cannot win by having many chunks weakly boosted.
    """
    query = (query or "").strip()
    if not query:
        return []

    fused: dict[int, float] = {}
    vector_ranks: dict[int, int] = {}
    fts_ranks: dict[int, int] = {}
    similarities: dict[int, float] = {}

    with store.connect(readonly=True) as conn:
        for rank, (chunk_id, similarity) in enumerate(
            _vector_ranking(conn, query, top_k * 2), start=1
        ):
            fused[chunk_id] = fused.get(chunk_id, 0.0) + WEIGHT_VECTOR / (RRF_K + rank)
            vector_ranks[chunk_id] = rank
            similarities[chunk_id] = similarity

        match_expr = build_fts_query(query)
        for rank, (chunk_id, _score) in enumerate(
            store.fts_chunk_search(conn, match_expr, top_k * 2), start=1
        ):
            fused[chunk_id] = fused.get(chunk_id, 0.0) + WEIGHT_FTS / (RRF_K + rank)
            fts_ranks[chunk_id] = rank

        title_rank = {
            note_id: rank
            for rank, (note_id, _score) in enumerate(
                store.fts_note_search(conn, match_expr, 15), start=1
            )
        }

        # Pull in notes whose *title* matched but whose body never mentions the
        # subject -- "Japan.md" reads "Tokyo / Dotonbori" and nothing else.
        for note_id in title_rank:
            for chunk_id in store.first_chunk_ids(conn, note_id, 2):
                fused.setdefault(chunk_id, 0.0)

        if not fused:
            return []

        rows = store.chunk_rows(conn, list(fused))

    grouped: dict[int, list[ChunkHit]] = {}
    for chunk_id, score in fused.items():
        row = rows.get(chunk_id)
        if not row:
            continue
        grouped.setdefault(row.note_id, []).append(
            ChunkHit(
                chunk_id=row.chunk_id,
                note_id=row.note_id,
                chunk_index=row.chunk_index,
                rel_path=row.rel_path,
                title=row.title,
                folder=row.folder,
                heading_path=row.heading_path,
                content=row.content,
                token_count=row.token_count,
                score=score,
                vector_rank=vector_ranks.get(chunk_id),
                fts_rank=fts_ranks.get(chunk_id),
                title_rank=title_rank.get(row.note_id),
                similarity=similarities.get(chunk_id),
            )
        )

    query_words = set(query_terms(query))
    note_hits: list[NoteHit] = []
    for note_id, hits in grouped.items():
        score = _aggregate([h.score for h in hits])
        rank = title_rank.get(note_id)
        if rank is not None:
            score += WEIGHT_TITLE / (RRF_K + rank)
        first = hits[0]
        score += _title_affinity(query_words, first.title)
        note_hits.append(
            NoteHit(
                note_id=note_id,
                rel_path=first.rel_path,
                title=first.title,
                folder=first.folder,
                score=score,
                chunk_hits=sorted(hits, key=lambda h: (-h.score, h.chunk_index)),
                title_rank=rank,
            )
        )

    note_hits.sort(key=lambda n: n.score, reverse=True)

    # Trim the long tail of near-noise matches.
    if note_hits:
        floor = note_hits[0].score * MIN_SCORE_RATIO
        note_hits = [n for n in note_hits if n.score >= floor] or note_hits[:1]
    return note_hits


def search_chunks(query: str, top_k: int = TOP_K_CHUNKS) -> list[ChunkHit]:
    """Flat, chunk-level view of the same retrieval -- used by `search`/debugging."""
    hits: list[ChunkHit] = []
    for note in rank_notes(query, top_k=top_k):
        hits.extend(note.chunk_hits)
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:top_k]


def _excerpt_from_hits(body: str, hits: list[ChunkHit], budget: int) -> tuple[str, bool]:
    """For a note too large to include whole, stitch its matching sections."""
    parts, used = [], 0
    for hit in sorted(hits, key=lambda h: h.chunk_index):
        piece = hit.content
        if hit.heading_path:
            piece = f"#### {' > '.join(hit.heading_path)}\n{piece}"
        tokens = count_tokens(piece)
        if used + tokens > budget:
            remaining = budget - used
            if remaining > 80:
                parts.append(truncate_to_tokens(piece, remaining))
                used = budget
            break
        parts.append(piece)
        used += tokens
    if not parts:
        return truncate_to_tokens(body, budget), True
    return "\n\n[...]\n\n".join(parts), True


@store.retry_on_io_error()
def build_context(
    query: str,
    top_k: int = TOP_K_CHUNKS,
    max_notes: int = MAX_CONTEXT_NOTES,
    token_budget: int = CONTEXT_TOKEN_BUDGET,
) -> tuple[list[NoteContext], list[NoteHit]]:
    """Retrieve, then assemble whole notes into a token-bounded context.

    Notes here average ~200 tokens, so the model is handed entire notes wherever
    they fit -- no half-tables, no answer sitting just outside the chunk window.
    """
    note_hits = rank_notes(query, top_k=top_k)
    if not note_hits:
        return [], []

    contexts: list[NoteContext] = []
    remaining = token_budget

    with store.connect(readonly=True) as conn:
        for note in note_hits:
            if len(contexts) >= max_notes or remaining <= 120:
                break
            record = store.note_record(conn, note.note_id)
            if not record:
                continue

            body = record["body"] or ""

            if not has_written_content(body):
                # A note with a title and no content is still a real answer.
                text, truncated = EMPTY_NOTE_MARKER, False
                used = count_tokens(text)
            else:
                body_tokens = int(record["token_count"] or count_tokens(body))
                if body_tokens <= remaining:
                    text, truncated, used = body, False, body_tokens
                else:
                    text, truncated = _excerpt_from_hits(body, note.chunk_hits, remaining)
                    used = count_tokens(text)
                if not text.strip():
                    continue

            sections = [
                h.heading_label
                for h in sorted(note.chunk_hits, key=lambda h: h.chunk_index)
                if h.heading_label and h.score > 0
            ]
            contexts.append(
                NoteContext(
                    rel_path=record["rel_path"],
                    title=record["title"],
                    folder=record["folder"],
                    text=text,
                    score=note.score,
                    sections=list(dict.fromkeys(sections)),
                    token_count=used,
                    truncated=truncated,
                )
            )
            remaining -= used

    return contexts, note_hits
