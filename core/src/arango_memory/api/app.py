"""FastAPI app exposing the core memory API (DESIGN.md §19).

Step 0 walking skeleton: minimal `/v1/store` (episode + memory) and
`/v1/retrieve` (BM25 + token-budgeted assembly), wired over the ArangoDB
client lifecycle. Enrichment, lifecycle, and security land in later steps.
"""

from __future__ import annotations

import asyncio
import time
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
from ..ingest.extract import Extractor, get_extractor
from ..ingest.procedural import get_steps
from ..ingest.queue import ArangoQueue, InProcessQueue, StepIntent, WriteIntent, WriteQueue
from ..ingest.worker import WriteWorker, commit_intent
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
from ..retrieve.prime import Include, prime
from ..retrieve.search import force_view_sync, retrieve
from ..schema.collections import ensure_schema
from ..security.auth import require_principal
from ..security.forget import forget
from ..stats import stats
from ..telemetry import latency
from ..telemetry.logging import RequestLogMiddleware, configure_logging, logger, tenant_var
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


def get_extractor_dep(request: Request) -> Extractor:
    """Resolve the shared extractor from app state (sync store commit path, MA-1)."""
    return request.app.state.extractor  # type: ignore[no-any-return]


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
    # Read across multiple agents in one fused pass (MA-2) — e.g. own + shared crew
    # tiers. None → just agent_id. Reads only; writes always use agent_id. Every id is
    # still tenant-scoped by the AQL (a cross-tenant id simply returns nothing).
    read_agent_ids: list[str] | None = None


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
    # Read-your-writes (MA-1): commit inline + force search-view visibility before
    # responding (status "committed"), instead of the default async queue ("queued").
    # For handoff boundaries — it forces a view commit, so don't set it every turn.
    sync: bool = False


class StoreResponse(BaseModel):
    status: Literal["queued", "committed"] = "queued"
    episode_id: str | None = None
    memory_ids: list[str] = Field(default_factory=list)


# ── /v1/flush (read-your-writes barrier, MA-1) ────────────
class FlushRequest(BaseModel):
    ctx: AccessContext
    timeout_ms: int = 5000


class FlushResponse(BaseModel):
    # "flushed": the queue drained for this tenant and the view is synced. "timeout":
    # `pending` intents remained at the deadline. Both are HTTP 200 — a timeout is a
    # caller-branchable state, not a server error. ("Drained" counts a dead-lettered
    # write as done; flush means the queue emptied, not that every write succeeded.)
    status: Literal["flushed", "timeout"]
    pending: int = 0


# ── /v1/retrieve ──────────────────────────────────────────
class RetrieveRequest(BaseModel):
    query: str
    ctx: AccessContext
    opts: RetrieveOptions = Field(default_factory=RetrieveOptions)


class MemoryHit(BaseModel):
    text: str
    score: float
    source: str
    agent_id: str = ""  # provenance: which agent wrote it (MA-2)


class RetrieveResponse(BaseModel):
    context: str = ""
    hits: list[MemoryHit] = Field(default_factory=list)
    tokens_injected: int = 0


# ── /v1/prime (task briefing, MA-3) ───────────────────────
class PrimeInclude(BaseModel):
    episodic: bool = True    # retrieved history section
    semantic: bool = True    # key-entities section
    procedural: bool = True  # prior-tool-runs section


class PrimeOptions(BaseModel):
    mode: Literal["lite", "full"] = "lite"
    k: int = settings.k
    max_memory_tokens: int = 1500
    include: PrimeInclude = Field(default_factory=PrimeInclude)


class PrimeRequest(BaseModel):
    task: str
    ctx: AccessContext
    opts: PrimeOptions = Field(default_factory=PrimeOptions)


class PrimeResponse(BaseModel):
    context: str = ""
    hits: list[MemoryHit] = Field(default_factory=list)
    entities: list[dict[str, Any]] = Field(default_factory=list)
    steps: list[dict[str, Any]] = Field(default_factory=list)
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
    sync: bool = False  # commit inline before responding (MA-1); see StoreRequest.sync


class StepResponse(BaseModel):
    status: Literal["queued", "committed"] = "queued"
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


def _sync_commit(request: Request, intent: Any, *, tenant_id: str, sync_view: bool) -> None:
    """Commit an intent inline on the request thread (MA-1 sync path), then force the
    search view to reflect it. Bypasses the queue — so no dead-letter; a commit failure
    surfaces to the caller as 503 (they asked to block on the result). Idempotency-keyed,
    so it can't duplicate a concurrent async commit of the same intent.
    """
    state = request.app.state
    db = state.client.db
    try:
        commit_intent(
            db, intent,
            embedder=state.embedder, extractor=state.extractor, generator=state.generator,
        )
    except Exception as exc:  # noqa: BLE001 — surface a blocked-write failure to the caller
        logger.error("sync commit failed", extra={"key": intent.key, "error": str(exc)})
        raise HTTPException(status_code=503, detail="sync write failed") from exc
    if sync_view:
        force_view_sync(db, tenant_id)


async def flush_endpoint(
    request: Request,
    req: FlushRequest,
    queue: WriteQueue = Depends(get_queue_dep),
) -> FlushResponse:
    """Block until this tenant's queued writes have committed and the search view
    reflects them (MA-1 handoff barrier). Returns "timeout" (still HTTP 200) if the
    queue hasn't drained by `timeout_ms`."""
    _authorize(request, tenant_id=req.ctx.tenant_id, access_level=req.ctx.access_level)
    deadline = time.monotonic() + req.timeout_ms / 1000.0
    pending = queue.pending_count(req.ctx.tenant_id)
    while pending > 0 and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
        pending = queue.pending_count(req.ctx.tenant_id)
    if pending > 0:
        return FlushResponse(status="timeout", pending=pending)
    force_view_sync(request.app.state.client.db, req.ctx.tenant_id)
    return FlushResponse(status="flushed")


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
    if req.sync:
        _sync_commit(request, intent, tenant_id=req.ctx.tenant_id, sync_view=True)
        return StoreResponse(status="committed", episode_id=intent.key,
                             memory_ids=[f"{intent.key}-mem"])
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
    if req.sync:
        # Steps land in the `steps` collection (immediately consistent), so no view sync.
        _sync_commit(request, intent, tenant_id=req.ctx.tenant_id, sync_view=False)
        return StepResponse(status="committed", step_id=intent.key)
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
        read_agent_ids=req.ctx.read_agent_ids,
        k=req.opts.k,
        max_memory_tokens=req.opts.max_memory_tokens,
        embedder=embedder,
        mode=req.opts.mode,
        generator=generator,
        cache=cache,
    )
    return RetrieveResponse(
        context=result.context,
        hits=[
            MemoryHit(text=h.text, score=h.score, source=h.source, agent_id=h.agent_id)
            for h in result.hits
        ],
        tokens_injected=result.tokens_injected,
    )


async def prime_endpoint(
    request: Request,
    req: PrimeRequest,
    client: ArangoMemoryClient = Depends(get_client),
    embedder: Embedder = Depends(get_embedder_dep),
    generator: Generator = Depends(get_generator_dep),
    cache: QueryCache = Depends(get_cache_dep),
) -> PrimeResponse:
    """Task briefing for a handoff (MA-3): history + key entities + prior tool runs,
    assembled under one token budget, spanning ctx.read_agent_ids."""
    _authorize(request, tenant_id=req.ctx.tenant_id, access_level=req.ctx.access_level)
    result = prime(
        client.db,
        task=req.task,
        tenant_id=req.ctx.tenant_id,
        agent_id=req.ctx.agent_id,
        read_agent_ids=req.ctx.read_agent_ids,
        mode=req.opts.mode,
        k=req.opts.k,
        max_memory_tokens=req.opts.max_memory_tokens,
        include=Include(
            episodic=req.opts.include.episodic,
            semantic=req.opts.include.semantic,
            procedural=req.opts.include.procedural,
        ),
        embedder=embedder,
        generator=generator,
        cache=cache,
    )
    return PrimeResponse(
        context=result.context,
        hits=[
            MemoryHit(text=h.text, score=h.score, source=h.source, agent_id=h.agent_id)
            for h in result.hits
        ],
        entities=result.entities,
        steps=result.steps,
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
def _warn_on_risky_config() -> None:
    """Log warnings for config that's valid but risky (caught at startup, §17)."""
    if settings.oidc_issuer and not settings.oidc_audience:
        logger.warning(
            "OIDC enabled without OIDC_AUDIENCE: the 'aud' claim is not verified, so any "
            "valid token from this issuer is accepted. Set OIDC_AUDIENCE to restrict."
        )


def create_app(client: ArangoMemoryClient | None = None) -> FastAPI:
    """Build the FastAPI app around a (possibly injected) Arango client.

    Tests pass a client configured for an ephemeral container; production and
    `make dev` call with no argument and get the env-driven default.
    """
    configure_logging()  # structured logs + correlation ids (§18)
    _warn_on_risky_config()
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
    app.state.extractor = extractor
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
        "/v1/flush", flush_endpoint, methods=["POST"], response_model=FlushResponse, tags=ingest_t
    )
    app.add_api_route(
        "/v1/retrieve", retrieve_endpoint, methods=["POST"], response_model=RetrieveResponse,
        tags=retr_t,
    )
    app.add_api_route(
        "/v1/prime", prime_endpoint, methods=["POST"], response_model=PrimeResponse, tags=retr_t,
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
