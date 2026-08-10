import tiktoken

_ENCODING = tiktoken.get_encoding("cl100k_base")


def chunk_text(text: str, chunk_size: int = 300, overlap: int = 50) -> list[str]:
    """Split text into overlapping chunks measured in tokens.

    A sliding window keeps related sentences together across chunk
    boundaries better than a hard character split, at the cost of a
    little duplicated context (bounded by `overlap`).
    """
    tokens = _ENCODING.encode(text)
    if not tokens:
        return []

    chunks = []
    start = 0
    step = chunk_size - overlap
    while start < len(tokens):
        window = tokens[start : start + chunk_size]
        chunk = _ENCODING.decode(window).strip()
        if chunk:
            chunks.append(chunk)
        if start + chunk_size >= len(tokens):
            break
        start += step
    return chunks
