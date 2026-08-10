from fastapi import FastAPI
from pydantic import BaseModel

from app.search import search
from app.synthesize import answer

app = FastAPI(title="Second Brain RAG", description="Semantic search over your own notes")


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5


class SearchHit(BaseModel):
    content: str
    source_path: str
    title: str
    chunk_index: int
    similarity: float


class AskResponse(BaseModel):
    answer: str
    sources: list[SearchHit]


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/search", response_model=list[SearchHit])
def search_endpoint(req: SearchRequest):
    results = search(req.query, top_k=req.top_k)
    return [SearchHit(**r.__dict__) for r in results]


@app.post("/ask", response_model=AskResponse)
def ask_endpoint(req: SearchRequest):
    results = search(req.query, top_k=req.top_k)
    generated = answer(req.query, results)
    return AskResponse(
        answer=generated,
        sources=[SearchHit(**r.__dict__) for r in results],
    )
