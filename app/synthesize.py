from openai import OpenAI

from app.config import CHAT_MODEL, OPENAI_API_KEY
from app.search import SearchResult

_client = OpenAI(api_key=OPENAI_API_KEY)

SYSTEM_PROMPT = (
    "You are a research assistant answering questions using only the provided "
    "excerpts from the user's own notes. Cite sources inline as [n], matching "
    "the excerpt numbers given. If the excerpts don't contain the answer, say so "
    "plainly instead of guessing."
)


def _format_context(results: list[SearchResult]) -> str:
    parts = []
    for i, r in enumerate(results, start=1):
        parts.append(f"[{i}] Source: {r.source_path}\n{r.content}")
    return "\n\n".join(parts)


def answer(query: str, results: list[SearchResult]) -> str:
    if not results:
        return "No relevant notes found to answer this question."

    context = _format_context(results)
    response = _client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Excerpts:\n\n{context}\n\nQuestion: {query}",
            },
        ],
        temperature=0.2,
    )
    return response.choices[0].message.content
