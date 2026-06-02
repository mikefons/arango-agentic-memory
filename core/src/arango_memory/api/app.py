"""FastAPI app exposing the core memory API (DESIGN.md §19).

Step 0 walking skeleton: minimal `/v1/store` (episode + memory) and
`/v1/retrieve` (BM25 + token-budgeted assembly), wired over the ArangoDB
client lifecycle. Enrichment, lifecycle, and security land in later steps.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, Field

from ..client import ArangoMemoryClient
from ..config import settings
from ..ingest.store import store
from ..retrieve.search import retrieve
from ..schema.collections import ensure_schema

client = ArangoMemoryClient()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    db = client.connect()
    ensure_schema(db)
    yield


app = FastAPI(title="arango-memory core", version="0.1.0", lifespan=lifespan)


# ── Shared models ─────────────────────────────────────────
class AccessContext(BaseModel):
    tenant_id: str
    agent_id: str
    session_id: str | None = None
    access_level: Literal["read", "write"] = "read"


class RetrieveOptions(BaseModel):
    mode: Literal["lite", "full"] = settings.memory_mode
    max_memory_tokens: int = settings.max_memory_tokens
    n_probe: int = settings.n_probe
    k: int = settings.k


# ── /health ───────────────────────────────────────────────
@app.get("/health")
async def health() -> dict[str, object]:
    return {"status": "ok", "arango": client.ping(), "mode": settings.memory_mode}


# ── /v1/store ─────────────────────────────────────────────
class StoreRequest(BaseModel):
    content: str
    ctx: AccessContext
    turn_index: int = 0


class StoreResponse(BaseModel):
    episode_id: str | None = None
    memory_ids: list[str] = Field(default_factory=list)
    entity_ids: list[str] = Field(default_factory=list)


@app.post("/v1/store", response_model=StoreResponse)
async def store_endpoint(req: StoreRequest) -> StoreResponse:
    result = store(
        client.db,
        content=req.content,
        tenant_id=req.ctx.tenant_id,
        agent_id=req.ctx.agent_id,
        session_id=req.ctx.session_id,
        turn_index=req.turn_index,
    )
    return StoreResponse(
        episode_id=result.episode_id,
        memory_ids=result.memory_ids,
        entity_ids=result.entity_ids,
    )


# ── /v1/retrieve ──────────────────────────────────────────
class RetrieveRequest(BaseModel):
    query: str
    ctx: AccessContext
    opts: RetrieveOptions = Field(default_factory=RetrieveOptions)


class MemoryHit(BaseModel):
    text: str
    score: float
    source: str


class RetrieveResponse(BaseModel):
    context: str = ""
    hits: list[MemoryHit] = Field(default_factory=list)
    tokens_injected: int = 0


@app.post("/v1/retrieve", response_model=RetrieveResponse)
async def retrieve_endpoint(req: RetrieveRequest) -> RetrieveResponse:
    result = retrieve(
        client.db,
        query=req.query,
        tenant_id=req.ctx.tenant_id,
        agent_id=req.ctx.agent_id,
        k=req.opts.k,
        max_memory_tokens=req.opts.max_memory_tokens,
    )
    return RetrieveResponse(
        context=result.context,
        hits=[MemoryHit(text=h.text, score=h.score, source=h.source) for h in result.hits],
        tokens_injected=result.tokens_injected,
    )
