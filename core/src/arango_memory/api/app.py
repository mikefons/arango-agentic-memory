"""FastAPI app exposing the core memory API (DESIGN.md §19).

Step 0 walking skeleton: minimal `/v1/store` (episode + memory) and
`/v1/retrieve` (BM25 + token-budgeted assembly), wired over the ArangoDB
client lifecycle. Enrichment, lifecycle, and security land in later steps.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from ..client import ArangoMemoryClient
from ..config import settings
from ..embedding import Embedder, get_embedder
from ..generation import Generator, get_generator
from ..ingest.extract import get_extractor
from ..ingest.procedural import get_steps
from ..ingest.queue import InProcessQueue, StepIntent, WriteIntent, WriteQueue
from ..ingest.worker import WriteWorker
from ..retrieve.enrich import QueryCache
from ..retrieve.search import retrieve
from ..schema.collections import ensure_schema
from ..security.forget import forget
from ..stats import stats


def get_client(request: Request) -> ArangoMemoryClient:
    """Resolve the request-scoped Arango client from app state."""
    return request.app.state.client  # type: ignore[no-any-return]


def get_embedder_dep(request: Request) -> Embedder:
    """Resolve the shared embedder from app state."""
    return request.app.state.embedder  # type: ignore[no-any-return]


def get_generator_dep(request: Request) -> Generator:
    """Resolve the shared generator (full-mode enrichment) from app state."""
    return request.app.state.generator  # type: ignore[no-any-return]


def get_cache_dep(request: Request) -> QueryCache:
    """Resolve the shared query cache from app state."""
    return request.app.state.cache  # type: ignore[no-any-return]


def get_queue_dep(request: Request) -> WriteQueue:
    """Resolve the shared write queue from app state."""
    return request.app.state.queue  # type: ignore[no-any-return]


# ── Shared models ─────────────────────────────────────────
class AccessContext(BaseModel):
    tenant_id: str
    agent_id: str
    session_id: str | None = None
    access_level: Literal["read", "write"] = "read"


def _require_write(ctx: AccessContext) -> None:
    """ABAC (§17): mutating endpoints require write access."""
    if ctx.access_level != "write":
        raise HTTPException(status_code=403, detail="write access required")


class RetrieveOptions(BaseModel):
    mode: Literal["lite", "full"] = settings.memory_mode
    max_memory_tokens: int = settings.max_memory_tokens
    n_probe: int = settings.n_probe
    k: int = settings.k


# ── /v1/store ─────────────────────────────────────────────
class StoreRequest(BaseModel):
    content: str
    ctx: AccessContext
    turn_index: int = 0


class StoreResponse(BaseModel):
    status: Literal["queued"] = "queued"
    episode_id: str | None = None
    memory_ids: list[str] = Field(default_factory=list)


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


# ── /v1/step (procedural memory) ──────────────────────────
class StepRequest(BaseModel):
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    outcome: Literal["success", "failure"]
    ctx: AccessContext
    pattern_summary: str = ""
    source_memory_key: str | None = None
    prev_step_key: str | None = None


class StepResponse(BaseModel):
    status: Literal["queued"] = "queued"
    step_id: str


class StepsResponse(BaseModel):
    steps: list[dict[str, Any]] = Field(default_factory=list)


# ── /v1/forget (right to be forgotten) ────────────────────
class ForgetRequest(BaseModel):
    tenant_id: str
    agent_id: str | None = None  # None → whole tenant
    access_level: Literal["read", "write"] = "read"


class ForgetResponse(BaseModel):
    status: Literal["forgotten"] = "forgotten"
    counts: dict[str, int] = Field(default_factory=dict)


# ── /v1/stats (graph health) ──────────────────────────────
class StatsResponse(BaseModel):
    counts: dict[str, int] = Field(default_factory=dict)


# ── Route handlers ────────────────────────────────────────
async def health(client: ArangoMemoryClient = Depends(get_client)) -> dict[str, object]:
    return {"status": "ok", "arango": client.ping(), "mode": settings.memory_mode}


async def store_endpoint(
    req: StoreRequest,
    queue: WriteQueue = Depends(get_queue_dep),
) -> StoreResponse:
    _require_write(req.ctx)
    # Durable write path (§15): enqueue and return immediately; the worker
    # commits asynchronously. Keys are deterministic from the idempotency key,
    # so they're known without committing; entity_ids are resolved async.
    intent = WriteIntent(
        content=req.content,
        tenant_id=req.ctx.tenant_id,
        agent_id=req.ctx.agent_id,
        session_id=req.ctx.session_id,
        turn_index=req.turn_index,
    )
    queue.enqueue(intent)
    return StoreResponse(episode_id=intent.key, memory_ids=[f"{intent.key}-mem"])


async def step_endpoint(
    req: StepRequest,
    queue: WriteQueue = Depends(get_queue_dep),
) -> StepResponse:
    _require_write(req.ctx)
    intent = StepIntent(
        tool_name=req.tool_name,
        arguments=req.arguments,
        outcome=req.outcome,
        tenant_id=req.ctx.tenant_id,
        agent_id=req.ctx.agent_id,
        pattern_summary=req.pattern_summary,
        source_memory_key=req.source_memory_key,
        prev_step_key=req.prev_step_key,
    )
    queue.enqueue(intent)
    return StepResponse(step_id=intent.key)


async def steps_endpoint(
    tenant_id: str,
    agent_id: str,
    tool_name: str | None = None,
    limit: int = 20,
    client: ArangoMemoryClient = Depends(get_client),
) -> StepsResponse:
    steps = get_steps(
        client.db, tenant_id=tenant_id, agent_id=agent_id, tool_name=tool_name, limit=limit
    )
    return StepsResponse(steps=steps)


async def forget_endpoint(
    req: ForgetRequest,
    client: ArangoMemoryClient = Depends(get_client),
) -> ForgetResponse:
    # Right to be forgotten (§17) — destructive, so requires write access.
    if req.access_level != "write":
        raise HTTPException(status_code=403, detail="write access required")
    counts = forget(client.db, tenant_id=req.tenant_id, agent_id=req.agent_id)
    return ForgetResponse(counts=counts)


async def stats_endpoint(
    tenant_id: str,
    client: ArangoMemoryClient = Depends(get_client),
) -> StatsResponse:
    return StatsResponse(counts=stats(client.db, tenant_id=tenant_id))


async def retrieve_endpoint(
    req: RetrieveRequest,
    client: ArangoMemoryClient = Depends(get_client),
    embedder: Embedder = Depends(get_embedder_dep),
    generator: Generator = Depends(get_generator_dep),
    cache: QueryCache = Depends(get_cache_dep),
) -> RetrieveResponse:
    result = retrieve(
        client.db,
        query=req.query,
        tenant_id=req.ctx.tenant_id,
        agent_id=req.ctx.agent_id,
        k=req.opts.k,
        max_memory_tokens=req.opts.max_memory_tokens,
        embedder=embedder,
        mode=req.opts.mode,
        generator=generator,
        cache=cache,
    )
    return RetrieveResponse(
        context=result.context,
        hits=[MemoryHit(text=h.text, score=h.score, source=h.source) for h in result.hits],
        tokens_injected=result.tokens_injected,
    )


# ── App factory ───────────────────────────────────────────
def create_app(client: ArangoMemoryClient | None = None) -> FastAPI:
    """Build the FastAPI app around a (possibly injected) Arango client.

    Tests pass a client configured for an ephemeral container; production and
    `make dev` call with no argument and get the env-driven default.
    """
    mem_client = client or ArangoMemoryClient()
    worker_client = ArangoMemoryClient(mem_client.config)  # own connection for the worker thread
    embedder = get_embedder()
    generator = get_generator()
    extractor = get_extractor()
    cache = QueryCache()
    queue = InProcessQueue()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        ensure_schema(mem_client.connect())
        worker = WriteWorker(
            queue, worker_client.connect(),
            embedder=embedder, extractor=extractor, generator=generator,
        )
        worker.start()
        app.state.worker = worker
        try:
            yield
        finally:
            worker.stop()

    app = FastAPI(title="arango-memory core", version="0.1.0", lifespan=lifespan)
    app.state.client = mem_client
    app.state.embedder = embedder
    app.state.generator = generator
    app.state.cache = cache
    app.state.queue = queue

    app.add_api_route("/health", health, methods=["GET"])
    app.add_api_route("/v1/store", store_endpoint, methods=["POST"], response_model=StoreResponse)
    app.add_api_route(
        "/v1/retrieve", retrieve_endpoint, methods=["POST"], response_model=RetrieveResponse
    )
    app.add_api_route("/v1/step", step_endpoint, methods=["POST"], response_model=StepResponse)
    app.add_api_route("/v1/steps", steps_endpoint, methods=["GET"], response_model=StepsResponse)
    app.add_api_route(
        "/v1/forget", forget_endpoint, methods=["POST"], response_model=ForgetResponse
    )
    app.add_api_route("/v1/stats", stats_endpoint, methods=["GET"], response_model=StatsResponse)
    return app


# Default app for uvicorn (`make dev`) and production.
app = create_app()
