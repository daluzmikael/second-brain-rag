"""Command line entry point: index, ask, search, chat, serve, status."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.config import (
    HOST,
    MAX_CONTEXT_NOTES,
    PORT,
    TOP_K_CHUNKS,
    VAULT_PATH,
    ConfigError,
)


def _cmd_index(args) -> int:
    from app.index import build_index

    build_index(vault_path=args.vault, rebuild=args.rebuild)
    return 0


def _cmd_status(_args) -> int:
    from app import store
    from app.config import CHAT_MODEL, api_key_configured
    from app.embeddings import embedding_identity

    info = store.stats()
    print(f"vault         {VAULT_PATH}  ({'found' if VAULT_PATH.is_dir() else 'MISSING'})")
    print(f"api key       {'configured' if api_key_configured() else 'NOT SET (see .env)'}")
    print(f"chat model    {CHAT_MODEL}")
    print(f"embeddings    {embedding_identity()}")
    if not info.get("indexed"):
        print("index         not built yet -- run: ./brain index")
        return 0
    print(f"index         {info['notes']} notes, {info['chunks']} chunks "
          f"({info.get('embedding_dimensions')} dims)")
    print(f"built at      {info.get('built_at')}")
    if info.get("fake_embeddings"):
        print("WARNING       built in offline test mode; rerun: ./brain index --rebuild")
    return 0


def _cmd_search(args) -> int:
    from app.search import search_chunks

    hits = search_chunks(args.query, top_k=args.top_k)
    if not hits:
        print("No matches.")
        return 0
    for i, hit in enumerate(hits, start=1):
        signals = []
        if hit.vector_rank:
            signals.append(f"vec#{hit.vector_rank}")
        if hit.fts_rank:
            signals.append(f"kw#{hit.fts_rank}")
        if hit.title_rank:
            signals.append(f"title#{hit.title_rank}")
        print(f"[{i}] {hit.rel_path}   score={hit.score:.4f}  {' '.join(signals)}")
        if hit.heading_label:
            print(f"    section: {hit.heading_label}")
        snippet = " ".join(hit.content.split())[:180]
        print(f"    {snippet}...\n")
    return 0


def _cmd_ask(args) -> int:
    from app.bot import ask

    reply, found = ask(args.question, top_k=args.top_k, max_notes=args.max_notes)
    print(reply)
    print("\nSources:")
    for i, note in enumerate(found.contexts, start=1):
        print(f"  [{i}] {note.rel_path}{' (excerpt)' if note.truncated else ''}")
    return 0


def _cmd_chat(_args) -> int:
    from app.chat import run

    return run()


def _cmd_serve(args) -> int:
    url = f"http://{args.host}:{args.port}"
    print(f"Second Brain UI -> {url}", flush=True)
    print("Starting server...", flush=True)

    import uvicorn

    # Import the app object rather than handing uvicorn the "app.api:app" import
    # string: the string form makes uvicorn resolve the import itself, which can
    # stall before the socket is ever bound.
    from app.api import app as fastapi_app

    if not args.no_browser:
        import threading
        import webbrowser

        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    uvicorn.run(fastapi_app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


def _cmd_service(args) -> int:
    from app import service

    return {
        "install": service.install,
        "uninstall": service.uninstall,
        "restart": service.restart,
        "status": service.status,
        "logs": lambda: service.logs(args.lines),
    }[args.action]()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="brain",
        description="Ask questions about your own Obsidian vault.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("index", help="scan the vault and build/refresh the index")
    p.add_argument("vault", nargs="?", type=Path, default=None, help="vault path (defaults to VAULT_PATH)")
    p.add_argument("--rebuild", action="store_true", help="discard the existing index first")
    p.set_defaults(func=_cmd_index)

    p = sub.add_parser("status", help="show vault, index and model status")
    p.set_defaults(func=_cmd_status)

    p = sub.add_parser("search", help="retrieval only, no model call")
    p.add_argument("query")
    p.add_argument("--top-k", type=int, default=8)
    p.set_defaults(func=_cmd_search)

    p = sub.add_parser("ask", help="one question, one answer")
    p.add_argument("question")
    p.add_argument("--top-k", type=int, default=TOP_K_CHUNKS)
    p.add_argument("--max-notes", type=int, default=MAX_CONTEXT_NOTES)
    p.set_defaults(func=_cmd_ask)

    p = sub.add_parser("chat", help="interactive chat in the terminal")
    p.set_defaults(func=_cmd_chat)

    p = sub.add_parser("serve", help="start the web chat UI")
    p.add_argument("--host", default=HOST)
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--log-level", default="warning")
    p.set_defaults(func=_cmd_serve)

    p = sub.add_parser("service", help="run the server in the background, always on")
    p.add_argument(
        "action",
        choices=["install", "uninstall", "restart", "status", "logs"],
        help="install registers a launchd agent that starts at login",
    )
    p.add_argument("--lines", type=int, default=40, help="for `logs`")
    p.set_defaults(func=_cmd_service)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as error:
        print(f"\n{error}\n", file=sys.stderr)
        return 1
    except FileNotFoundError as error:
        print(f"\n{error}\n", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print()
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
