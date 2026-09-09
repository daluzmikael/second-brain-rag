"""Configuration, loaded from .env with sensible defaults.

Everything the bot needs to boot lives here: where the vault is, where the
local index file goes, which models to call, and the retrieval tuning knobs.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


# --- Credentials -----------------------------------------------------------
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()

# Values that mean "not filled in yet" rather than a real key.
_PLACEHOLDERS = {
    "",
    "sk-...",
    "sk-proj-...",
    "sk-replace-me",
    "your-api-key-here",
    "your_api_key_here",
    "replace_me",
    "changeme",
    "todo",
}


def api_key_configured() -> bool:
    key = OPENAI_API_KEY
    return bool(key) and key.lower() not in _PLACEHOLDERS and not key.endswith("...")


class ConfigError(RuntimeError):
    """Raised when the bot cannot run because configuration is incomplete."""


def require_embedding_key() -> None:
    """Indexing needs a key only when embeddings come from OpenAI."""
    if FAKE_EMBEDDINGS or EMBEDDING_BACKEND == "local":
        return
    require_api_key()


def require_api_key() -> None:
    """Fail early, with instructions, instead of deep inside an API call."""
    if FAKE_EMBEDDINGS:
        return
    if not api_key_configured():
        raise ConfigError(
            "OPENAI_API_KEY is not set.\n"
            f"  Add your key to {PROJECT_ROOT / '.env'}:\n"
            "      OPENAI_API_KEY=sk-...\n"
            "  Then re-run. (To test the pipeline with no API calls at all, set\n"
            "  FAKE_EMBEDDINGS=1 -- retrieval works offline, answers do not.)"
        )


# --- Vault -----------------------------------------------------------------
VAULT_PATH = Path(
    os.environ.get(
        "VAULT_PATH",
        "~/Library/Mobile Documents/iCloud~md~obsidian/Documents/MiksVaultyBaulty",
    ).strip().strip("'\"")
).expanduser()

# Used to build obsidian:// deep links so citations are clickable.
VAULT_NAME = os.environ.get("VAULT_NAME", "").strip() or VAULT_PATH.name

# Directories never worth indexing.
IGNORED_DIRS = {
    ".obsidian",
    ".trash",
    ".git",
    ".github",
    "node_modules",
    "__pycache__",
    ".smart-env",
    ".makemd",
    ".space",
}
NOTE_SUFFIXES = {".md", ".markdown", ".txt"}


# --- Index -----------------------------------------------------------------
INDEX_PATH = Path(
    os.environ.get("INDEX_PATH", str(PROJECT_ROOT / "data" / "index.sqlite3")).strip()
).expanduser()


# --- Models ----------------------------------------------------------------
CHAT_MODEL = os.environ.get("CHAT_MODEL", "gpt-4o-mini").strip()

# Where embeddings come from: "local" runs a static model on this machine in
# about a tenth of a millisecond, against ~800ms for an OpenAI round trip, and
# needs no network or API key. "openai" keeps the hosted model.
EMBEDDING_BACKEND = os.environ.get("EMBEDDING_BACKEND", "local").strip().lower()

EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small").strip()
LOCAL_EMBEDDING_MODEL = os.environ.get(
    "LOCAL_EMBEDDING_MODEL", "minishlab/potion-retrieval-32M"
).strip()

# Fallback only. The real width is recorded in the index when it is built.
EMBEDDING_DIMENSIONS = _int("EMBEDDING_DIMENSIONS", 512 if EMBEDDING_BACKEND == "local" else 1536)

# Deterministic local embeddings, for testing the pipeline without an API key.
# Retrieval still works (keyword search is unaffected); semantic quality does not.
FAKE_EMBEDDINGS = _flag("FAKE_EMBEDDINGS")

EMBED_BATCH_SIZE = _int("EMBED_BATCH_SIZE", 96)

# Chunks buffered across notes before an embedding round trip. Embedding one
# note per request turned a full build into 150 sequential calls (~3.5 min);
# batching across notes makes it a handful.
EMBED_FLUSH_CHUNKS = _int("EMBED_FLUSH_CHUNKS", 256)


# --- Chunking --------------------------------------------------------------
# Notes here are short (a few hundred words), so chunks are generous: a whole
# "### Section" usually lands in one chunk, which keeps tables intact.
CHUNK_MAX_TOKENS = _int("CHUNK_MAX_TOKENS", 450)
CHUNK_MIN_TOKENS = _int("CHUNK_MIN_TOKENS", 48)
CHUNK_OVERLAP_TOKENS = _int("CHUNK_OVERLAP_TOKENS", 120)


# --- Retrieval -------------------------------------------------------------
TOP_K_CHUNKS = _int("TOP_K_CHUNKS", 24)       # candidates pulled from each searcher
MAX_CONTEXT_NOTES = _int("MAX_CONTEXT_NOTES", 8)
CONTEXT_TOKEN_BUDGET = _int("CONTEXT_TOKEN_BUDGET", 7000)

# Reciprocal-rank-fusion constant and per-signal weights.
RRF_K = _int("RRF_K", 60)
WEIGHT_VECTOR = float(os.environ.get("WEIGHT_VECTOR", "1.0"))
WEIGHT_FTS = float(os.environ.get("WEIGHT_FTS", "1.0"))
WEIGHT_TITLE = float(os.environ.get("WEIGHT_TITLE", "1.5"))
# Direct overlap between the question and a note's title. BM25 alone lets a
# note that merely mentions Brazil outrank the note actually titled "Brasil".
WEIGHT_TITLE_OVERLAP = float(os.environ.get("WEIGHT_TITLE_OVERLAP", "0.030"))
WEIGHT_TITLE_EXACT = float(os.environ.get("WEIGHT_TITLE_EXACT", "0.020"))

# Many notes here are a few words long and name their subject only in the
# filename ("Japan.md" contains "Tokyo / Dotonbori"), so a title match has to
# compete with body matches rather than merely nudge them.

# Drop trailing results far weaker than the best one; the top note always stays.
MIN_SCORE_RATIO = float(os.environ.get("MIN_SCORE_RATIO", "0.45"))


# Stand-in body for a note that exists but has nothing written in it.
EMPTY_NOTE_MARKER = "(This note exists in the vault but is empty - no content written yet.)"


# --- Server ----------------------------------------------------------------
# Seconds between background vault re-scans while the server runs. An
# unchanged vault costs ~0.02s to check, so this is effectively free.
# 0 disables it.
AUTO_REINDEX_SECONDS = _int("AUTO_REINDEX_SECONDS", 30)

HOST = os.environ.get("HOST", "127.0.0.1").strip()
PORT = _int("PORT", 8077)
