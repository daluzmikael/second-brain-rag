"""The ask pipeline: rewrite -> retrieve -> answer.

Shared by the terminal chat and the web UI so both behave identically.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import CONTEXT_TOKEN_BUDGET, MAX_CONTEXT_NOTES, TOP_K_CHUNKS
from app.search import NoteContext, build_context
from app.synthesize import answer, answer_stream, rewrite_query


@dataclass
class Retrieval:
    question: str
    search_query: str
    contexts: list[NoteContext]

    @property
    def rewritten(self) -> bool:
        return self.search_query.strip().lower() != self.question.strip().lower()


def retrieve(
    question: str,
    history: list[dict] | None = None,
    top_k: int = TOP_K_CHUNKS,
    max_notes: int = MAX_CONTEXT_NOTES,
    token_budget: int = CONTEXT_TOKEN_BUDGET,
) -> Retrieval:
    """Resolve a follow-up into a standalone query, then search the vault."""
    search_query = rewrite_query(question, history)
    contexts, _ = build_context(
        search_query,
        top_k=top_k,
        max_notes=max_notes,
        token_budget=token_budget,
    )
    return Retrieval(question=question, search_query=search_query, contexts=contexts)


def ask(question: str, history: list[dict] | None = None, **kwargs) -> tuple[str, Retrieval]:
    found = retrieve(question, history, **kwargs)
    return answer(question, found.contexts, history), found


def ask_stream(question: str, history: list[dict] | None = None, **kwargs):
    """Yield the retrieval first, then answer text chunks as they arrive."""
    found = retrieve(question, history, **kwargs)
    yield found
    yield from answer_stream(question, found.contexts, history)
