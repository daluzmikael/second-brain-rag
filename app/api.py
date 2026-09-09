"""FastAPI server: the chat UI plus a small JSON/SSE API.

Chat history is never stored server-side. The browser holds the current
conversation and sends it with each request, so reloading the page starts a
genuinely fresh chat -- which is the behaviour asked for.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from app import store
from app.bot import retrieve
from app.config import (
    AUTO_REINDEX_SECONDS,
    CHAT_MODEL,
    MAX_CONTEXT_NOTES,
    TOP_K_CHUNKS,
    VAULT_PATH,
    ConfigError,
    api_key_configured,
)
from app.embeddings import active_backend, embedding_identity, warm_up
from app.search import search_chunks
from app.synthesize import answer_stream

WEB_DIR = Path(__file__).resolve().parent / "web"

logger = logging.getLogger("secondbrain")

_stop_watching = threading.Event()
_last_reindex: dict = {"at": None, "changed": 0}


def _watch_vault() -> None:
    """Keep the index in step with the vault while the server runs.

    Checking an unchanged vault costs about 0.02s -- mtime and size only, no
    file is opened -- so polling is cheaper here than a filesystem-event
    dependency, and it survives Obsidian rewriting files in ways FSEvents
    reports oddly.
    """
    from app.index import build_index

    while not _stop_watching.wait(AUTO_REINDEX_SECONDS):
        try:
            summary = build_index(log=lambda _message: None)
        except Exception as error:  # a vault on a sleeping disk, a bad edit, etc.
            logger.warning("auto-reindex failed: %s", error)
            continue
        changed = summary["added"] + summary["updated"] + summary["removed"]
        _last_reindex.update(at=time.time(), changed=changed)
        if changed:
            logger.info(
                "vault changed: +%d ~%d -%d",
                summary["added"], summary["updated"], summary["removed"],
            )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Load the local embedding model now so the first question is not the one
    # that pays for it.
    try:
        warm_up()
    except Exception as error:
        logger.warning("embedding warm-up failed: %s", error)

    watcher = None
    if AUTO_REINDEX_SECONDS > 0:
        watcher = threading.Thread(target=_watch_vault, name="vault-watch", daemon=True)
        watcher.start()
    try:
        yield
    finally:
        _stop_watching.set()
        if watcher:
            watcher.join(timeout=2)


app = FastAPI(
    title="Second Brain",
    description="Ask questions about your own Obsidian vault",
    docs_url="/api/docs",
    lifespan=lifespan,
)


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    question: str = Field(min_length=1)
    history: list[Message] = Field(default_factory=list)
    top_k: int = TOP_K_CHUNKS
    max_notes: int = MAX_CONTEXT_NOTES


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = 8


@app.get("/")
def index():
    page = WEB_DIR / "index.html"
    if not page.is_file():
        raise HTTPException(status_code=500, detail="UI file missing: app/web/index.html")
    return FileResponse(page)


@app.get("/api/status")
def status():
    info = store.stats()
    info.update(
        api_key_configured=api_key_configured(),
        chat_model=CHAT_MODEL,
        embedding_backend=active_backend(),
        embedding_identity_config=embedding_identity(),
        vault_path_config=str(VAULT_PATH),
        vault_exists=VAULT_PATH.is_dir(),
        auto_reindex_seconds=AUTO_REINDEX_SECONDS,
    )
    return info


@app.post("/api/search")
def search_endpoint(request: SearchRequest):
    """Retrieval only -- no model call. Useful for checking what the bot sees."""
    try:
        hits = search_chunks(request.query, top_k=request.top_k)
    except ConfigError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return [
        {
            "rel_path": hit.rel_path,
            "title": hit.title,
            "folder": hit.folder,
            "section": hit.heading_label,
            "score": round(hit.score, 6),
            "vector_rank": hit.vector_rank,
            "fts_rank": hit.fts_rank,
            "title_rank": hit.title_rank,
            "content": hit.content,
        }
        for hit in hits
    ]


@app.post("/api/reindex")
def reindex_endpoint():
    from app.index import build_index

    try:
        summary = build_index(log=lambda _message: None)
    except (ConfigError, FileNotFoundError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    summary.update(store.stats())
    return summary


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.post("/api/chat")
def chat_endpoint(request: ChatRequest):
    """Stream an answer: `sources` first, then `delta` events, then `done`."""
    history = [m.model_dump() for m in request.history]

    def generate():
        try:
            found = retrieve(
                request.question,
                history,
                top_k=request.top_k,
                max_notes=request.max_notes,
            )
        except ConfigError as error:
            yield _sse("error", {"message": str(error)})
            return
        except Exception as error:  # index missing, vault moved, etc.
            yield _sse("error", {"message": f"Retrieval failed: {error}"})
            return

        yield _sse(
            "sources",
            {
                "search_query": found.search_query,
                "rewritten": found.rewritten,
                "sources": [
                    {
                        "n": i,
                        "title": note.title,
                        "rel_path": note.rel_path,
                        "folder": note.folder,
                        "url": note.url,
                        "sections": note.sections[:4],
                        "tokens": note.token_count,
                        "truncated": note.truncated,
                    }
                    for i, note in enumerate(found.contexts, start=1)
                ],
            },
        )

        try:
            for piece in answer_stream(request.question, found.contexts, history):
                yield _sse("delta", {"text": piece})
        except Exception as error:
            yield _sse("error", {"message": f"{type(error).__name__}: {error}"})
            return

        yield _sse("done", {})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
