"""Convert the official LongMemEval release into the runner's dataset schema (HX-1).

[LongMemEval](https://github.com/xiaowu0162/LongMemEval) is the standard long-term-memory
QA benchmark: each question ships a long, multi-session chat history in which a few
*evidence* sessions are buried among many distractor sessions, and the system is scored on
**answer correctness** (not retrieval recall). LongMemEval-**S** is the ~115k-token-per-
question variant (~500 questions). Its question types line up with capabilities this core
claims — `knowledge-update` (bi-temporal supersession), `temporal-reasoning` (`valid_time`),
`single-session-preference`, `multi-session` — which is exactly why it's worth running here.

The public dataset (`longmemeval_s.json`) is **bring-your-own** — externally licensed and
large, so it's never committed; CI runs on the smoke slice. This converter maps that JSON
into the `{"samples": [...]}` shape `locomo.load_dataset` consumes, so `eval.longmemeval`
can run the benchmark:

    python -m arango_memory.eval.longmemeval_convert longmemeval_s.json lme.json
    python -m arango_memory.eval.longmemeval lme.json --mode lite --rerank

Mapping (one question → one Sample = one tenant, so distractors are realistic but bounded,
mirroring the MuSiQue converter):
  - `haystack_sessions` (a list of sessions, each a list of `{role, content}` turns) →
    `sessions`, each turn → `{speaker: role, text: content}` (blank turns dropped).
  - `question`/`answer` → the single `qa`; `question_type` → `category`.
  - abstention questions (official convention: `question_id` ends with `_abs`) are flagged
    `abstention=true` — the correct behavior is to decline, and the runner judges them so.

Retrieval-recall (`gold_fact`) is intentionally not populated: LongMemEval scores the
*answer*, not the evidence, so this harness reports QA accuracy only.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _convert_item(item: dict[str, Any]) -> dict[str, Any]:
    """One LongMemEval question → one Sample dict.

    The session timestamp (`haystack_dates[i]`) is prefixed onto each turn's text, and the
    `question_date` onto the question — without them `temporal-reasoning` questions are
    unanswerable (no time signal) and cross-session recency is ambiguous. The dates thus live
    in the retrievable/injected text, which is what the answerer reasons over."""
    dates = item.get("haystack_dates") or []
    sessions_out: list[list[dict[str, str]]] = []
    for i, session in enumerate(item.get("haystack_sessions", [])):
        date = str(dates[i]).strip() if i < len(dates) else ""
        session_out: list[dict[str, str]] = []
        for turn in session:
            text = str(turn.get("content", "")).strip()
            if not text:
                continue  # drop blank/system-only turns
            if date:
                text = f"[{date}] {text}"
            session_out.append({"speaker": str(turn.get("role", "")), "text": text})
        if session_out:
            sessions_out.append(session_out)

    question_id = str(item["question_id"])
    question = str(item["question"])
    if question_date := item.get("question_date"):
        question = f"[Today's date is {question_date}.] {question}"
    qa = {
        "question": question,
        "answer": str(item.get("answer", "")),
        "category": str(item.get("question_type") or "") or None,
        # Official convention: abstention questions carry an `_abs` id suffix.
        "abstention": question_id.endswith("_abs"),
    }
    return {"sample_id": question_id, "sessions": sessions_out, "qa": [qa]}


def _stratified_sample(raw: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Pick up to `limit` questions spread evenly across `question_type` (round-robin over
    per-type buckets). LongMemEval-S is grouped by type, so a plain first-N slice returns a
    single category — this gives a type-representative subset instead. Within-type order kept."""
    from collections import deque

    buckets: dict[str, deque[dict[str, Any]]] = {}
    for item in raw:
        buckets.setdefault(str(item.get("question_type") or "unknown"), deque()).append(item)
    queues = list(buckets.values())
    out: list[dict[str, Any]] = []
    while len(out) < limit and any(queues):
        for q in queues:
            if q:
                out.append(q.popleft())
                if len(out) >= limit:
                    break
    return out


def convert(raw: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, int]]:
    """Convert the LongMemEval release list → (dataset dict, conversion stats)."""
    samples = [_convert_item(item) for item in raw]
    stats = {
        "questions": len(samples),
        "abstention": sum(1 for s in samples if s["qa"][0]["abstention"]),
    }
    return {"samples": samples}, stats


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="arango_memory.eval.longmemeval_convert")
    parser.add_argument("input", help="path to the official longmemeval_s.json (or _m/_oracle)")
    parser.add_argument("output", help="path to write the converted dataset")
    parser.add_argument("--limit", type=int, default=None,
                        help="convert only N questions (first N, or evenly across types with "
                             "--stratified)")
    parser.add_argument("--stratified", action="store_true",
                        help="sample --limit questions evenly across question_type (LongMemEval-S "
                             "is grouped by type, so a plain --limit returns one category)")
    return parser


def main(argv: list[str] | None = None) -> int:
    from collections import Counter

    args = _build_parser().parse_args(argv)
    raw = json.loads(Path(args.input).read_text())
    if args.stratified:
        raw = _stratified_sample(raw, args.limit if args.limit is not None else len(raw))
    elif args.limit is not None:
        raw = raw[: args.limit]
    dataset, stats = convert(raw)
    Path(args.output).write_text(json.dumps(dataset, indent=2))
    by_type = Counter(str(item.get("question_type") or "unknown") for item in raw)
    dist = ", ".join(f"{t}={n}" for t, n in sorted(by_type.items()))
    print(
        f"converted {stats['questions']} questions "
        f"({stats['abstention']} abstention) → {args.output}\n  types: {dist}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
