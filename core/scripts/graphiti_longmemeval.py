"""Graphiti on our LongMemEval harness — the head-to-head benchmark.

Same questions (`lme.json`, the stratified-90 slice), same answer prompt (`generate_answer`), same
judge (`judge_correct`), same answerer/judge model as `arango_memory.eval.longmemeval` — only the
memory system differs. Output rows match the harness's checkpoint shape, so the two runs pair
per question.

Graphiti is configured the way its own docs and the Zep paper describe, to give it its best case:
- one episode per LongMemEval session (`add_episode`, the path with fact invalidation), with the
  session date as `reference_time`;
- LLM: Claude Haiku 4.5 for extraction (Anthropic is a recommended structured-output provider);
  embeddings: OpenAI text-embedding-3-small (same as our core);
- retrieval: `COMBINED_HYBRID_SEARCH_CROSS_ENCODER` with Graphiti's local BGE reranker, top
  `--facts` edges + `--entities` nodes, rendered in the Zep paper's context template;
- backend: Neo4j 5.26 (Graphiti's primary backend, pinned in its own compose files); questions
  are isolated by `group_id`. FalkorDB is supported (`--backend falkordb`, one graph per
  question), but graphiti-core 0.30.2 fails to create its full-text indices on FalkorDB 6.x.

Needs an env with `graphiti-core[anthropic,falkordb]`, `sentence-transformers` and this package,
a running Neo4j (NEO4J_PASSWORD) or FalkorDB, and OPENAI_API_KEY + ANTHROPIC_API_KEY.

Usage:
  python scripts/graphiti_longmemeval.py lme.json --checkpoint out.jsonl [--resume] \\
    [--limit N] [--concurrency N] [--backend neo4j --port 7687]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from graphiti_core import Graphiti
from graphiti_core.cross_encoder.bge_reranker_client import BGERerankerClient
from graphiti_core.driver.driver import GraphDriver
from graphiti_core.driver.falkordb_driver import FalkorDriver
from graphiti_core.driver.neo4j_driver import Neo4jDriver
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.llm_client.anthropic_client import AnthropicClient
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.nodes import EpisodeType
from graphiti_core.search.search_config_recipes import COMBINED_HYBRID_SEARCH_CROSS_ENCODER

from arango_memory.eval.halu import generate_answer
from arango_memory.eval.locomo import load_dataset
from arango_memory.eval.longmemeval import judge_correct
from arango_memory.generation import get_generator
from arango_memory.retrieve.search import _event_sort_key

MODEL = "claude-haiku-4-5"
EMBEDDING_MODEL = "text-embedding-3-small"


def _context(edges: list[Any], nodes: list[Any]) -> str:
    """The Zep paper's context template: dated facts, then entity summaries."""

    def when(dt: datetime | None, default: str) -> str:
        return dt.strftime("%Y-%m-%d %H:%M") if dt else default

    facts = "\n".join(
        f"  - {e.fact} (Date range: {when(e.valid_at, 'unknown')}"
        f" - {when(e.invalid_at, 'present')})"
        for e in edges
    )
    entities = "\n".join(f"  - {n.name}: {n.summary}" for n in nodes)
    return (
        "FACTS and ENTITIES represent relevant context to the current conversation.\n"
        "# These are the most relevant facts and their valid date ranges. If the fact is about"
        " an event, the event takes place during this time.\n"
        "# format: FACT (Date range: from - to)\n"
        f"<FACTS>\n{facts}\n</FACTS>\n"
        "# These are the most relevant entities\n"
        "# ENTITY_NAME: entity summary\n"
        f"<ENTITIES>\n{entities}\n</ENTITIES>"
    )


class _SerialBGE(BGERerankerClient):
    """Graphiti's local BGE reranker with its `predict` calls serialized. Graphiti reranks edges
    and nodes concurrently on one model, and concurrent predict on Apple MPS kills the process
    (the same fault our core guards against, #253). Ranking is unchanged — only call order."""

    def __init__(self) -> None:
        super().__init__()
        self._lock = asyncio.Lock()

    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        async with self._lock:
            return await super().rank(query, passages)


_RERANKER: _SerialBGE | None = None  # one ~2 GB model, shared by every question


def _driver(gid: str, args: argparse.Namespace) -> GraphDriver:
    if args.backend == "falkordb":  # one graph per question
        return FalkorDriver(host="localhost", port=args.port, database=gid)
    # Neo4j: one database; group_id partitions it (clone() is a no-op for Neo4j)
    return Neo4jDriver(
        uri=f"bolt://localhost:{args.port}", user="neo4j", password=os.environ["NEO4J_PASSWORD"]
    )


def _graphiti(gid: str, args: argparse.Namespace) -> Graphiti:
    # A Graphiti per question (own LLM client → own token tracker), so each question's LLM
    # usage stays separately measurable under concurrency.
    llm = LLMConfig(api_key=os.environ["ANTHROPIC_API_KEY"], model=MODEL, small_model=MODEL)
    assert _RERANKER is not None
    return Graphiti(
        graph_driver=_driver(gid, args),
        llm_client=AnthropicClient(config=llm),
        embedder=OpenAIEmbedder(
            config=OpenAIEmbedderConfig(
                api_key=os.environ["OPENAI_API_KEY"], embedding_model=EMBEDDING_MODEL
            )
        ),
        cross_encoder=_RERANKER,
    )


async def run_sample(sample: Any, args: argparse.Namespace, gen: Any) -> list[dict[str, Any]]:
    gid = f"lme_{sample.sample_id}".replace("-", "_")
    graphiti = _graphiti(gid, args)
    try:
        if args.backend == "falkordb":
            await graphiti.build_indices_and_constraints()
        t0 = time.perf_counter()
        for i, session in enumerate(sample.sessions):
            if not session:
                continue
            when = _event_sort_key(session[0].event_time) or datetime(2023, 1, 1)
            await graphiti.add_episode(
                name=f"session-{i}",
                episode_body="\n".join(f"{t.speaker}: {t.text}" for t in session),
                source_description="chat session",
                reference_time=when.replace(tzinfo=UTC),
                source=EpisodeType.message,
                group_id=gid,
            )
        ingest_s = time.perf_counter() - t0
        usage = graphiti.llm_client.token_tracker.get_total_usage()

        config = COMBINED_HYBRID_SEARCH_CROSS_ENCODER.model_copy(deep=True)
        config.episode_config = None
        config.community_config = None
        rows: list[dict[str, Any]] = []
        for qa in sample.qa:
            config.limit = max(args.facts, args.entities)
            t1 = time.perf_counter()
            res = await graphiti.search_(qa.question, config=config, group_ids=[gid])
            context = _context(res.edges[: args.facts], res.nodes[: args.entities])
            search_s = time.perf_counter() - t1
            answer, correct, error = "", False, False
            try:
                answer = await asyncio.to_thread(
                    generate_answer, qa.question, context, generator=gen
                )
            except Exception:  # noqa: BLE001 — count it, like the harness does
                error = True
            else:
                correct = await asyncio.to_thread(
                    judge_correct,
                    qa.question,
                    qa.answer,
                    answer,
                    judge=gen,
                    abstention=qa.abstention,
                )
            rows.append(
                {
                    "question_id": sample.sample_id,
                    "question_type": qa.category or "unknown",
                    "correct": correct,
                    "abstention": qa.abstention,
                    "answer": answer,
                    "answer_error": error,
                    "system": "graphiti",
                    "ingest_seconds": round(ingest_s, 1),
                    "search_seconds": round(search_s, 2),
                    "context_chars": len(context),
                    "facts": len(res.edges[: args.facts]),
                    "entities": len(res.nodes[: args.entities]),
                    "llm_input_tokens": usage.input_tokens,
                    "llm_output_tokens": usage.output_tokens,
                    "episodes": sum(1 for s in sample.sessions if s),
                }
            )
        return rows
    finally:
        await graphiti.close()


async def main_async(args: argparse.Namespace) -> None:
    samples = load_dataset(args.dataset)
    if args.limit:
        samples = samples[: args.limit]
    out = Path(args.checkpoint)
    done = set()
    if args.resume and out.exists():
        done = {json.loads(line)["question_id"] for line in out.read_text().splitlines() if line}
    todo = [s for s in samples if s.sample_id not in done]
    print(f"{len(samples)} questions; {len(done)} done; {len(todo)} to run", flush=True)
    gen = get_generator()
    global _RERANKER
    _RERANKER = _SerialBGE()
    if args.backend == "neo4j":  # one database: build its indices once, up front
        setup = _graphiti("setup", args)
        await setup.build_indices_and_constraints()
        await setup.close()
    sem = asyncio.Semaphore(args.concurrency)
    lock = asyncio.Lock()

    async def one(idx: int, sample: Any) -> None:
        async with sem:
            t = time.perf_counter()
            try:
                rows = await run_sample(sample, args, gen)
            except Exception as exc:  # noqa: BLE001 — keep the run going; resume retries it
                print(f"[{idx}] {sample.sample_id}: FAILED {type(exc).__name__}: {exc}", flush=True)
                return
            async with lock:
                with out.open("a") as fh:
                    for row in rows:
                        fh.write(json.dumps(row) + "\n")
            r = rows[0]
            print(
                f"[{idx}] {sample.sample_id}: {'OK' if r['correct'] else 'x '} "
                f"{time.perf_counter() - t:.0f}s ingest={r['ingest_seconds']}s "
                f"tokens={r['llm_input_tokens']}/{r['llm_output_tokens']}",
                flush=True,
            )

    await asyncio.gather(*(one(i, s) for i, s in enumerate(todo, 1)))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("dataset")
    p.add_argument("--checkpoint", required=True)
    p.add_argument("--resume", action="store_true")
    p.add_argument("--limit", type=int)
    p.add_argument("--concurrency", type=int, default=1)
    p.add_argument("--facts", type=int, default=20)
    p.add_argument("--entities", type=int, default=20)
    p.add_argument("--backend", choices=("neo4j", "falkordb"), default="neo4j")
    p.add_argument("--port", type=int, default=7687, help="bolt (neo4j) or redis (falkordb)")
    asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    main()
