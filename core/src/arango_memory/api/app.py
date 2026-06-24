"""FastAPI app exposing the core memory API (DESIGN.md §19).

Step 0 walking skeleton: minimal `/v1/store` (episode + memory) and
`/v1/retrieve` (BM25 + token-budgeted assembly), wired over the ArangoDB
client lifecycle. Enrichment, lifecycle, and security land in later steps.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from enum import Enum
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import __version__
from ..client import ArangoMemoryClient
from ..config import settings
from ..embedding import Embedder, get_embedder
from ..entity_api import get_entity, list_entities, seed
from ..generation import Generator, get_generator
from ..graph_api import tenant_graph
from ..ingest.extract import get_extractor
from ..ingest.procedural import get_steps
from ..ingest.queue import ArangoQueue, InProcessQueue, StepIntent, WriteIntent, WriteQueue
from ..ingest.worker import WriteWorker
from ..lifecycle.community import compute_communities
from ..lifecycle.conflict import supersede
from ..lifecycle.dream import run_dream_state
from ..lifecycle.ontology import (
    approve_proposal,
    list_proposals,
    propose_relationship_types,
    reject_proposal,
)
from ..lifecycle.salience import compute_centrality
from ..retrieve.enrich import QueryCache
from ..retrieve.search import retrieve
from ..schema.collections import ensure_schema
from ..security.auth import require_principal
from ..security.forget import forget
from ..stats import stats
from ..telemetry import latency
from ..telemetry.logging import RequestLogMiddleware, configure_logging, tenant_var
from .limits import RequestSizeLimitMiddleware, rate_limit


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


def _authorize(
    request: Request, *, tenant_id: str, access_level: str = "read", write: bool = False
) -> None:
    """ABAC + authn (§17).

    Enforced mode (an API key authenticated the caller): identity comes from the
    **key** — the body's `tenant_id` must match it, and a write needs a write-scoped
    key. Open mode (no keys configured): the body-asserted `access_level` governs
    writes, exactly as before.
    """
    tenant_var.set(tenant_id)  # correlation: tag this request's logs with the tenant
    principal = getattr(request.state, "principal", None)
    if principal is None:  # open mode — body-asserted ABAC (unchanged)
        if write and access_level != "write":
            raise HTTPException(status_code=403, detail="write access required")
        return
    if tenant_id != principal.tenant_id:
        raise HTTPException(status_code=403, detail="tenant mismatch")
    if write and principal.scope != "write":
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
    source_reliability: float = 1.0
    memory_type: Literal["episodic", "working"] = "episodic"


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


# ── /v1/entity, /v1/entities, /v1/seed (semantic memory) ──
class EntityResponse(BaseModel):
    entity: dict[str, Any]
    related: list[dict[str, Any]] = Field(default_factory=list)


class EntitiesResponse(BaseModel):
    entities: list[dict[str, Any]] = Field(default_factory=list)


class SeedRequest(BaseModel):
    profile: dict[str, Any]
    ctx: AccessContext


class SeedResponse(BaseModel):
    status: Literal["seeded"] = "seeded"
    entity_ids: list[str] = Field(default_factory=list)


# ── /v1/supersede (bi-temporal conflict resolution, §12) ──
class SupersedeRequest(BaseModel):
    new_key: str
    old_key: str
    ctx: AccessContext


class SupersedeResponse(BaseModel):
    status: Literal["superseded"] = "superseded"


# ── /v1/graph (full semantic graph for visualization) ─────
class GraphResponse(BaseModel):
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)


# ── /v1/dream (Dream State consolidation, §13) ────────────
class DreamRequest(BaseModel):
    ctx: AccessContext


class DreamResponse(BaseModel):
    reviewed: int = 0
    superseded: int = 0
    consolidated: int = 0
    cleared: int = 0
    breaker_tripped: bool = False


# ── /v1/salience (graph centrality, §9/§13) ───────────────
class SalienceRequest(BaseModel):
    ctx: AccessContext


class SalienceResponse(BaseModel):
    entities: int = 0


# ── /v1/community (graph community detection, §9/§13) ──────
class CommunityRequest(BaseModel):
    ctx: AccessContext


class CommunityResponse(BaseModel):
    entities: int = 0
    communities: int = 0


# ── /v1/ontology (ontology evolution, §13 — flag-gated v2) ─
class OntologyScanRequest(BaseModel):
    ctx: AccessContext


class OntologyScanResponse(BaseModel):
    clusters: int = 0
    proposed: int = 0


class OntologyDecisionRequest(BaseModel):
    ctx: AccessContext
    key: str


def _require_ontology() -> None:
    if not settings.ontology_evolution:
        raise HTTPException(status_code=404, detail="ontology evolution is disabled")


# ── Route handlers ────────────────────────────────────────
async def health(client: ArangoMemoryClient = Depends(get_client)) -> dict[str, object]:
    # **Liveness** — is the process up? Always 200 when serving; *not* gated on the DB
    # (so a DB blip can't trigger a liveness-probe restart loop — that's /ready's job).
    # `arango` is informational; `latency` is process-global p50/p95/p99 (§18/§23),
    # surfaced here (not /v1/stats, which is per-tenant). Wire k8s livenessProbe here.
    return {
        "status": "ok",
        "arango": client.ping(),
        "mode": settings.memory_mode,
        "latency_ms": latency.snapshot(),
    }


async def ready(client: ArangoMemoryClient = Depends(get_client)) -> JSONResponse:
    # **Readiness** — can the service actually serve (DB reachable)? `503` when not,
    # so an orchestrator stops routing traffic without restarting the pod. Wire k8s
    # readinessProbe here.
    ok = client.ping()
    return JSONResponse(
        {"status": "ready" if ok else "unavailable", "arango": ok},
        status_code=200 if ok else 503,
    )


async def store_endpoint(
    request: Request,
    req: StoreRequest,
    queue: WriteQueue = Depends(get_queue_dep),
) -> StoreResponse:
    _authorize(request, tenant_id=req.ctx.tenant_id, access_level=req.ctx.access_level, write=True)
    # Durable write path (§15): enqueue and return immediately; the worker
    # commits asynchronously. Keys are deterministic from the idempotency key,
    # so they're known without committing; entity_ids are resolved async.
    intent = WriteIntent(
        content=req.content,
        tenant_id=req.ctx.tenant_id,
        agent_id=req.ctx.agent_id,
        session_id=req.ctx.session_id,
        turn_index=req.turn_index,
        source_reliability=req.source_reliability,
        memory_type=req.memory_type,
    )
    queue.enqueue(intent)
    return StoreResponse(episode_id=intent.key, memory_ids=[f"{intent.key}-mem"])


async def step_endpoint(
    request: Request,
    req: StepRequest,
    queue: WriteQueue = Depends(get_queue_dep),
) -> StepResponse:
    _authorize(request, tenant_id=req.ctx.tenant_id, access_level=req.ctx.access_level, write=True)
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
    request: Request,
    tenant_id: str,
    agent_id: str,
    tool_name: str | None = None,
    limit: int = 20,
    client: ArangoMemoryClient = Depends(get_client),
) -> StepsResponse:
    _authorize(request, tenant_id=tenant_id)
    steps = get_steps(
        client.db, tenant_id=tenant_id, agent_id=agent_id, tool_name=tool_name, limit=limit
    )
    return StepsResponse(steps=steps)


async def forget_endpoint(
    request: Request,
    req: ForgetRequest,
    client: ArangoMemoryClient = Depends(get_client),
) -> ForgetResponse:
    # Right to be forgotten (§17) — destructive, so requires write access.
    _authorize(request, tenant_id=req.tenant_id, access_level=req.access_level, write=True)
    counts = forget(client.db, tenant_id=req.tenant_id, agent_id=req.agent_id)
    return ForgetResponse(counts=counts)


async def stats_endpoint(
    request: Request,
    tenant_id: str,
    client: ArangoMemoryClient = Depends(get_client),
) -> StatsResponse:
    _authorize(request, tenant_id=tenant_id)
    return StatsResponse(counts=stats(client.db, tenant_id=tenant_id))


async def entity_endpoint(
    request: Request,
    entity_id: str,
    tenant_id: str,
    client: ArangoMemoryClient = Depends(get_client),
) -> EntityResponse:
    _authorize(request, tenant_id=tenant_id)
    found = get_entity(client.db, entity_id=entity_id, tenant_id=tenant_id)
    if found is None:
        raise HTTPException(status_code=404, detail="entity not found")
    return EntityResponse(entity=found["entity"], related=found["related"])


async def entities_endpoint(
    request: Request,
    tenant_id: str,
    agent_id: str | None = None,
    label: str | None = None,
    limit: int = 50,
    client: ArangoMemoryClient = Depends(get_client),
) -> EntitiesResponse:
    _authorize(request, tenant_id=tenant_id)
    rows = list_entities(
        client.db, tenant_id=tenant_id, agent_id=agent_id, label=label, limit=limit
    )
    return EntitiesResponse(entities=rows)


async def seed_endpoint(
    request: Request,
    req: SeedRequest,
    client: ArangoMemoryClient = Depends(get_client),
    embedder: Embedder = Depends(get_embedder_dep),
) -> SeedResponse:
    _authorize(request, tenant_id=req.ctx.tenant_id, access_level=req.ctx.access_level, write=True)
    ids = seed(
        client.db,
        profile=req.profile,
        tenant_id=req.ctx.tenant_id,
        agent_id=req.ctx.agent_id,
        embedder=embedder,
    )
    return SeedResponse(entity_ids=ids)


async def supersede_endpoint(
    request: Request,
    req: SupersedeRequest,
    client: ArangoMemoryClient = Depends(get_client),
) -> SupersedeResponse:
    """Record `new` superseding `old` (Supersedes edge + soft-deprecate old, §12)."""
    _authorize(request, tenant_id=req.ctx.tenant_id, access_level=req.ctx.access_level, write=True)
    supersede(client.db, new_key=req.new_key, old_key=req.old_key)
    return SupersedeResponse()


async def graph_endpoint(
    request: Request,
    tenant_id: str,
    client: ArangoMemoryClient = Depends(get_client),
) -> GraphResponse:
    """The tenant's full semantic graph (entities + relates_to/Supersedes), §11."""
    _authorize(request, tenant_id=tenant_id)
    g = tenant_graph(client.db, tenant_id=tenant_id)
    return GraphResponse(nodes=g["nodes"], edges=g["edges"])


async def dream_endpoint(
    request: Request,
    req: DreamRequest,
    client: ArangoMemoryClient = Depends(get_client),
    generator: Generator = Depends(get_generator_dep),
) -> DreamResponse:
    """Run Dream State consolidation for the tenant (§13) — mutating, write-only."""
    _authorize(request, tenant_id=req.ctx.tenant_id, access_level=req.ctx.access_level, write=True)
    r = run_dream_state(client.db, tenant_id=req.ctx.tenant_id, generator=generator)
    return DreamResponse(
        reviewed=r.reviewed,
        superseded=r.superseded,
        consolidated=r.consolidated,
        cleared=r.cleared,
        breaker_tripped=r.breaker_tripped,
    )


async def salience_endpoint(
    request: Request,
    req: SalienceRequest,
    client: ArangoMemoryClient = Depends(get_client),
) -> SalienceResponse:
    """Recompute PageRank centrality for the tenant's entities (§9/§13) — write-only."""
    _authorize(request, tenant_id=req.ctx.tenant_id, access_level=req.ctx.access_level, write=True)
    result = compute_centrality(client.db, tenant_id=req.ctx.tenant_id)
    return SalienceResponse(entities=result.get("entities", 0))


async def community_endpoint(
    request: Request,
    req: CommunityRequest,
    client: ArangoMemoryClient = Depends(get_client),
) -> CommunityResponse:
    """Recompute LPA community labels for the tenant's entities (§9/§13) — write-only."""
    _authorize(request, tenant_id=req.ctx.tenant_id, access_level=req.ctx.access_level, write=True)
    result = compute_communities(client.db, tenant_id=req.ctx.tenant_id)
    return CommunityResponse(
        entities=result.get("entities", 0), communities=result.get("communities", 0)
    )


async def ontology_scan_endpoint(
    request: Request,
    req: OntologyScanRequest,
    client: ArangoMemoryClient = Depends(get_client),
    generator: Generator = Depends(get_generator_dep),
) -> OntologyScanResponse:
    """Propose typed relationships from co-occurrence clusters (§13) — write-only, flag-gated."""
    _require_ontology()
    _authorize(request, tenant_id=req.ctx.tenant_id, access_level=req.ctx.access_level, write=True)
    result = propose_relationship_types(
        client.db, tenant_id=req.ctx.tenant_id, generator=generator
    )
    return OntologyScanResponse(
        clusters=result.get("clusters", 0), proposed=result.get("proposed", 0)
    )


async def ontology_proposals_endpoint(
    request: Request,
    tenant_id: str,
    status: str | None = None,
    access_level: Literal["read", "write"] = "read",
    client: ArangoMemoryClient = Depends(get_client),
) -> list[dict[str, Any]]:
    """List relationship proposals for human review (§13) — flag-gated."""
    _require_ontology()
    _authorize(request, tenant_id=tenant_id)
    return list_proposals(client.db, tenant_id=tenant_id, status=status)


async def ontology_approve_endpoint(
    request: Request,
    req: OntologyDecisionRequest,
    client: ArangoMemoryClient = Depends(get_client),
) -> dict[str, Any]:
    """Approve a proposal → relabel the tenant's matching co-occurrence edges — write-only."""
    _require_ontology()
    _authorize(request, tenant_id=req.ctx.tenant_id, access_level=req.ctx.access_level, write=True)
    return approve_proposal(client.db, tenant_id=req.ctx.tenant_id, key=req.key)


async def ontology_reject_endpoint(
    request: Request,
    req: OntologyDecisionRequest,
    client: ArangoMemoryClient = Depends(get_client),
) -> dict[str, Any]:
    """Reject a proposal (no graph change) — write-only."""
    _require_ontology()
    _authorize(request, tenant_id=req.ctx.tenant_id, access_level=req.ctx.access_level, write=True)
    return reject_proposal(client.db, tenant_id=req.ctx.tenant_id, key=req.key)


async def retrieve_endpoint(
    request: Request,
    req: RetrieveRequest,
    client: ArangoMemoryClient = Depends(get_client),
    embedder: Embedder = Depends(get_embedder_dep),
    generator: Generator = Depends(get_generator_dep),
    cache: QueryCache = Depends(get_cache_dep),
) -> RetrieveResponse:
    _authorize(request, tenant_id=req.ctx.tenant_id, access_level=req.ctx.access_level)
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


# ── OpenAPI metadata (served at /docs, /redoc, /openapi.json) ──
_API_DESCRIPTION = (
    "Agentic memory core for ArangoDB — durable ingestion, hybrid retrieval, "
    "lifecycle consolidation, and decay over the `/v1` boundary.\n\n"
    "**Auth:** open by default; set `API_KEYS` to require `Authorization: Bearer "
    "<key>` (tenant + scope derive from the key). `/health` and these docs are always "
    "public. **Embeddings are never returned over the API** (inversion defense, §17)."
)
_OPENAPI_TAGS = [
    {"name": "system", "description": "Liveness, readiness, and process-global latency."},
    {"name": "ingestion", "description": "Write conversation turns and tool/action traces."},
    {"name": "retrieval", "description": "Hybrid BM25 + vector + graph retrieval."},
    {"name": "entities & graph", "description": "Read/seed entities and the memory graph."},
    {"name": "lifecycle", "description": "Consolidation, salience, communities, ontology."},
    {"name": "memory ops", "description": "Per-tenant stats and right-to-be-forgotten."},
]


# ── App factory ───────────────────────────────────────────
def create_app(client: ArangoMemoryClient | None = None) -> FastAPI:
    """Build the FastAPI app around a (possibly injected) Arango client.

    Tests pass a client configured for an ephemeral container; production and
    `make dev` call with no argument and get the env-driven default.
    """
    configure_logging()  # structured logs + correlation ids (§18)
    mem_client = client or ArangoMemoryClient()
    worker_client = ArangoMemoryClient(mem_client.config)  # own connection for the worker thread
    embedder = get_embedder()
    generator = get_generator()
    extractor = get_extractor()
    cache = QueryCache()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        ensure_schema(mem_client.connect())
        # Build the queue after the schema exists (the durable backend needs its
        # collection). "arango" survives restarts; "memory" is the dev/CI default.
        queue: WriteQueue
        if settings.write_queue_backend == "arango":
            queue = ArangoQueue(
                ArangoMemoryClient(mem_client.config).connect(),
                lease_seconds=settings.write_lease_seconds,
            )
        else:
            queue = InProcessQueue()
        app.state.queue = queue
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

    # Authn then rate limit (§17): require_principal runs first so rate_limit can key
    # off the authenticated tenant. Both no-op unless configured; /health stays open.
    app = FastAPI(
        title="arango-memory core",
        version=__version__,
        description=_API_DESCRIPTION,
        openapi_tags=_OPENAPI_TAGS,
        lifespan=lifespan,
        dependencies=[Depends(require_principal), Depends(rate_limit)],
    )
    # Request-size cap (§17): reject oversized bodies before they're buffered.
    app.add_middleware(RequestSizeLimitMiddleware)
    # Correlation id + access log, added last so it's the OUTERMOST layer (even a 413
    # gets a request id + access line). §18.
    app.add_middleware(RequestLogMiddleware)
    app.state.client = mem_client
    app.state.embedder = embedder
    app.state.generator = generator
    app.state.cache = cache
    # app.state.queue is set in the lifespan (after the schema exists).

    # Typed as the route-decorator's `tags` param expects (list invariance).
    sys_t: list[str | Enum] = ["system"]
    ingest_t: list[str | Enum] = ["ingestion"]
    retr_t: list[str | Enum] = ["retrieval"]
    eg_t: list[str | Enum] = ["entities & graph"]
    life_t: list[str | Enum] = ["lifecycle"]
    ops_t: list[str | Enum] = ["memory ops"]

    app.add_api_route("/health", health, methods=["GET"], tags=sys_t)
    app.add_api_route("/ready", ready, methods=["GET"], tags=sys_t)
    app.add_api_route(
        "/v1/store", store_endpoint, methods=["POST"], response_model=StoreResponse, tags=ingest_t
    )
    app.add_api_route(
        "/v1/retrieve", retrieve_endpoint, methods=["POST"], response_model=RetrieveResponse,
        tags=retr_t,
    )
    app.add_api_route(
        "/v1/step", step_endpoint, methods=["POST"], response_model=StepResponse, tags=ingest_t
    )
    app.add_api_route(
        "/v1/steps", steps_endpoint, methods=["GET"], response_model=StepsResponse, tags=ingest_t
    )
    app.add_api_route(
        "/v1/forget", forget_endpoint, methods=["POST"], response_model=ForgetResponse, tags=ops_t
    )
    app.add_api_route(
        "/v1/stats", stats_endpoint, methods=["GET"], response_model=StatsResponse, tags=ops_t
    )
    app.add_api_route(
        "/v1/entity", entity_endpoint, methods=["GET"], response_model=EntityResponse, tags=eg_t
    )
    app.add_api_route(
        "/v1/entities", entities_endpoint, methods=["GET"], response_model=EntitiesResponse,
        tags=eg_t,
    )
    app.add_api_route(
        "/v1/seed", seed_endpoint, methods=["POST"], response_model=SeedResponse, tags=eg_t
    )
    app.add_api_route(
        "/v1/supersede", supersede_endpoint, methods=["POST"], response_model=SupersedeResponse,
        tags=eg_t,
    )
    app.add_api_route(
        "/v1/graph", graph_endpoint, methods=["GET"], response_model=GraphResponse, tags=eg_t
    )
    app.add_api_route(
        "/v1/dream", dream_endpoint, methods=["POST"], response_model=DreamResponse, tags=life_t
    )
    app.add_api_route(
        "/v1/salience", salience_endpoint, methods=["POST"], response_model=SalienceResponse,
        tags=life_t,
    )
    app.add_api_route(
        "/v1/community", community_endpoint, methods=["POST"], response_model=CommunityResponse,
        tags=life_t,
    )
    app.add_api_route(
        "/v1/ontology/scan", ontology_scan_endpoint, methods=["POST"],
        response_model=OntologyScanResponse, tags=life_t,
    )
    app.add_api_route(
        "/v1/ontology/proposals", ontology_proposals_endpoint, methods=["GET"], tags=life_t
    )
    app.add_api_route(
        "/v1/ontology/approve", ontology_approve_endpoint, methods=["POST"], tags=life_t
    )
    app.add_api_route(
        "/v1/ontology/reject", ontology_reject_endpoint, methods=["POST"], tags=life_t
    )
    return app


# Default app for uvicorn (`make dev`) and production.
app = create_app()
