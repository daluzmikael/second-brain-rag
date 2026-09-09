"""Terminal chat loop.

Keeps only the current conversation, in memory. Quitting forgets everything --
there is no transcript on disk.
"""

from __future__ import annotations

import sys

from app import store
from app.bot import retrieve
from app.config import CHAT_MODEL, ConfigError, require_api_key
from app.synthesize import answer_stream

BANNER = """Second Brain -- ask your vault.
Commands: /new  /sources  /status  /reindex  /help  /exit"""

HELP = """/new      forget this conversation and start over
/sources  show the notes used for the last answer
/status   index and model info
/reindex  re-scan the vault for new or edited notes
/exit     quit (nothing is saved)"""


def _setup_readline() -> None:
    try:
        import readline  # noqa: F401  (enables arrow keys and line editing)
    except Exception:
        pass


def _print_sources(contexts) -> None:
    if not contexts:
        print("  (no notes were used)")
        return
    for i, note in enumerate(contexts, start=1):
        flag = " [excerpt]" if note.truncated else ""
        print(f"  [{i}] {note.rel_path}{flag}")
        if note.sections:
            print(f"      sections: {' | '.join(note.sections[:3])}")


def run() -> int:
    _setup_readline()

    info = store.stats()
    if not info.get("indexed"):
        print("No index yet. Build one first:\n    ./brain index", file=sys.stderr)
        return 1

    try:
        require_api_key()
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 1

    print(BANNER)
    print(f"{info['notes']} notes, {info['chunks']} chunks, model {CHAT_MODEL}")
    if info.get("fake_embeddings"):
        print("NOTE: index was built in offline test mode; run ./brain index --rebuild")
    print()

    history: list[dict] = []
    last_contexts = []

    while True:
        try:
            question = input("you › ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not question:
            continue

        lowered = question.lower()
        if lowered in {"/exit", "/quit", "exit", "quit"}:
            return 0
        if lowered == "/help":
            print(HELP, "\n")
            continue
        if lowered == "/new":
            history = []
            last_contexts = []
            print("(conversation cleared)\n")
            continue
        if lowered == "/sources":
            _print_sources(last_contexts)
            print()
            continue
        if lowered == "/status":
            for key, value in store.stats().items():
                print(f"  {key}: {value}")
            print()
            continue
        if lowered == "/reindex":
            from app.index import build_index

            try:
                build_index(log=lambda message: print(f"  {message}"))
            except (ConfigError, FileNotFoundError) as error:
                print(f"  {error}")
            print()
            continue

        try:
            found = retrieve(question, history)
        except Exception as error:
            print(f"  retrieval failed: {error}\n", file=sys.stderr)
            continue

        if found.rewritten:
            print(f"      (searched: {found.search_query})")

        print("\nbrain › ", end="", flush=True)
        collected = []
        try:
            for piece in answer_stream(question, found.contexts, history):
                collected.append(piece)
                print(piece, end="", flush=True)
        except KeyboardInterrupt:
            print("\n  (interrupted)\n")
            continue
        except Exception as error:
            print(f"\n  answer failed: {type(error).__name__}: {error}\n", file=sys.stderr)
            continue

        print("\n")
        _print_sources(found.contexts)
        print()

        reply = "".join(collected).strip()
        if reply:
            history.append({"role": "user", "content": question})
            history.append({"role": "assistant", "content": reply})
            last_contexts = found.contexts
