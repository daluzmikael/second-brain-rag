"""Token counting, with a graceful fallback.

tiktoken needs its BPE file on disk (downloaded on first use). If it is not
available we fall back to a rough estimate rather than refusing to run -- token
counts here only drive chunk sizes and the context budget, so being a few
percent off is harmless.
"""

from functools import lru_cache

_encoder = None
_tried = False


def _get_encoder():
    global _encoder, _tried
    if _tried:
        return _encoder
    _tried = True
    try:
        import tiktoken

        for name in ("o200k_base", "cl100k_base"):
            try:
                _encoder = tiktoken.get_encoding(name)
                break
            except Exception:
                continue
    except Exception:
        _encoder = None
    return _encoder


def count_tokens(text: str) -> int:
    if not text:
        return 0
    encoder = _get_encoder()
    if encoder is None:
        # ~4 characters per token is the usual rule of thumb for English prose.
        return max(1, len(text) // 4)
    return len(encoder.encode(text, disallowed_special=()))


def truncate_to_tokens(text: str, limit: int) -> str:
    """Cut `text` down to at most `limit` tokens, on a line boundary if possible."""
    if limit <= 0 or not text:
        return ""
    if count_tokens(text) <= limit:
        return text

    encoder = _get_encoder()
    if encoder is None:
        return text[: limit * 4]

    tokens = encoder.encode(text, disallowed_special=())[:limit]
    cut = encoder.decode(tokens)
    # Prefer ending at the last complete line so tables/lists stay readable.
    newline = cut.rfind("\n")
    if newline > len(cut) * 0.6:
        cut = cut[:newline]
    return cut.rstrip()


@lru_cache(maxsize=4096)
def cached_count(text: str) -> int:
    return count_tokens(text)
