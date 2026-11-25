"""Latency check for a running core (ROADMAP PF-1).

`proxy` puts a fixed delay in front of ArangoDB, to stand in for a remote database:

    python scripts/latency_bench.py proxy --listen 8530 --target 8529 --rtt-ms 90

Point a core at the proxy (ARANGO_URL=http://127.0.0.1:8530), then measure it with a
tenant that already holds memories:

    python scripts/latency_bench.py load --url http://127.0.0.1:8080 \
        --tenant <tenant> --agent <agent>

`load` reports sequential retrieve latency, the same retrieves sent at once, and how
long `/health` takes meanwhile, plus the core's own stage percentiles.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import threading
import time

import httpx

_QUERIES = (
    "What did I say about my car's maintenance schedule?",
    "Which restaurant did I recommend for a birthday dinner?",
    "How many books did I read last month?",
    "What is my sister's new job?",
    "Where did I go on vacation in the summer?",
    "What kind of dog do I have?",
    "What did I buy for my home office?",
    "When is my next dentist appointment?",
)


# ── delay proxy ───────────────────────────────────────────
async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, delay: float) -> None:
    """Forward bytes `delay` seconds after they arrive, keeping their order."""
    queue: asyncio.Queue[tuple[float, bytes]] = asyncio.Queue()

    async def send() -> None:
        while True:
            due, data = await queue.get()
            if not data:
                break
            await asyncio.sleep(max(0.0, due - time.monotonic()))
            writer.write(data)
            await writer.drain()
        writer.close()

    sender = asyncio.create_task(send())
    try:
        while data := await reader.read(65536):
            queue.put_nowait((time.monotonic() + delay, data))
    finally:
        queue.put_nowait((0.0, b""))
        await sender


async def _proxy(listen: int, target_host: str, target: int, rtt_ms: float) -> None:
    one_way = rtt_ms / 2000.0

    async def handle(creader: asyncio.StreamReader, cwriter: asyncio.StreamWriter) -> None:
        sreader, swriter = await asyncio.open_connection(target_host, target)
        await asyncio.gather(
            _pump(creader, swriter, one_way), _pump(sreader, cwriter, one_way),
            return_exceptions=True,
        )

    server = await asyncio.start_server(handle, "127.0.0.1", listen)
    async with server:
        await server.serve_forever()


# ── load ──────────────────────────────────────────────────
def _load(url: str, tenant: str, agent: str) -> dict[str, object]:
    key = os.environ.get("CORE_API_KEY")
    headers = {"Authorization": f"Bearer {key}"} if key else {}

    def client() -> httpx.Client:
        return httpx.Client(base_url=url, headers=headers, timeout=120)

    def retrieve(http: httpx.Client, query: str) -> float:
        started = time.perf_counter()
        http.post("/v1/retrieve", json={
            "query": query, "ctx": {"tenant_id": tenant, "agent_id": agent},
        }).raise_for_status()
        return (time.perf_counter() - started) * 1000

    def health(http: httpx.Client) -> float:
        started = time.perf_counter()
        http.get("/health").raise_for_status()
        return (time.perf_counter() - started) * 1000

    with client() as http:
        for query in _QUERIES:
            retrieve(http, query)  # warm-up: connections and the embedding cache
        sequential = [retrieve(http, query) for query in _QUERIES]
        idle = [health(http) for _ in range(5)]

    loaded: list[float] = []
    probes: list[float] = []
    done = threading.Event()

    def one(query: str) -> None:
        with client() as http:
            loaded.append(retrieve(http, query))

    def probe() -> None:
        with client() as http:
            while not done.is_set():
                probes.append(health(http))
                done.wait(0.1)

    rounds = 5
    walls: list[float] = []
    prober = threading.Thread(target=probe)
    prober.start()
    try:
        for _ in range(rounds):
            threads = [threading.Thread(target=one, args=(q,)) for q in _QUERIES]
            started = time.perf_counter()
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            walls.append((time.perf_counter() - started) * 1000)
    finally:
        done.set()
        prober.join()

    if len(loaded) != rounds * len(_QUERIES):
        raise SystemExit("some concurrent retrieves failed; see the errors above")
    with client() as http:
        stages = http.get("/health").json()["latency_ms"]
    ordered = sorted(probes)
    return {
        "sequential_p50_ms": round(statistics.median(sequential)),
        "concurrent_requests": len(_QUERIES),
        "concurrent_wall_p50_ms": round(statistics.median(walls)),
        "concurrent_wall_max_ms": round(max(walls)),
        "concurrent_p50_ms": round(statistics.median(loaded)),
        "health_idle_p50_ms": round(statistics.median(idle)),
        "health_under_load_p50_ms": round(statistics.median(ordered)),
        "health_under_load_p95_ms": round(ordered[max(0, int(0.95 * len(ordered) + 0.5) - 1)]),
        "stages_p50_ms": {k: v["p50"] for k, v in stages.items() if k.startswith("retriev")},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    proxy = commands.add_parser("proxy", help="add latency in front of ArangoDB")
    proxy.add_argument("--listen", type=int, default=8530)
    proxy.add_argument("--target-host", default="127.0.0.1")
    proxy.add_argument("--target", type=int, default=8529)
    proxy.add_argument("--rtt-ms", type=float, default=90.0)
    load = commands.add_parser("load", help="measure a running core (CORE_API_KEY if locked)")
    load.add_argument("--url", default="http://127.0.0.1:8080")
    load.add_argument("--tenant", required=True)
    load.add_argument("--agent", required=True)
    args = parser.parse_args()
    if args.command == "proxy":
        asyncio.run(_proxy(args.listen, args.target_host, args.target, args.rtt_ms))
    else:
        print(json.dumps(_load(args.url, args.tenant, args.agent), indent=1))


if __name__ == "__main__":
    main()
