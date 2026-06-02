"""FastAPI app exposing the core memory API (DESIGN.md §19).

Step 0 scaffold: defines the request/response contract and wires the ArangoDB
client lifecycle. Endpoint bodies are stubs that return shape-correct responses
so the Vercel adapter can be developed against a stable contract. Pipeline
logic lands in later steps.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, Field

from ..client import ArangoMemoryClient
from ..config import settings

client = ArangoMemoryClient()


@asynccontextmanager
async def lifespan(app: FastAPI):
    client.connect()
    # TODO(step-0): ensure_schema() — collections, view, vector index (DESIGN.md §6)
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
async def store(req: StoreRequest) -> StoreResponse:
    # TODO(step-0): run minimal ingestion (episode + memory, idempotency key)
    return StoreResponse()


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
async def retrieve(req: RetrieveRequest) -> RetrieveResponse:
    # TODO(step-0): run minimal retrieval (BM25 + naive assembly)
    return RetrieveResponse()
