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
*answer*, not the evidence, so by default this harness reports QA accuracy only. `--evidence`
(RQ-3) additionally carries every `has_answer` turn — its text, session date and session id —
into `qa.evidence`, so a harness can score *which* evidence retrieval surfaced (e.g. for
knowledge-update: is the newest statement of the fact ranked above the stale one?).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _evidence(item: dict[str, Any]) -> list[dict[str, str]]:
    """The `has_answer` turns (RQ-3), each with its session's date + id. Texts are stripped the
    same way `_convert_item` strips turn text, so they match the ingested memory content."""
    dates = item.get("haystack_dates") or []
    ids = item.get("haystack_session_ids") or []
    out: list[dict[str, str]] = []
    for i, session in enumerate(item.get("haystack_sessions", [])):
        for turn in session:
            text = str(turn.get("content", "")).strip()
            if turn.get("has_answer") and text:
                out.append({
                    "text": text,
                    "event_time": str(dates[i]).strip() if i < len(dates) else "",
                    "session_id": str(ids[i]) if i < len(ids) else str(i),
                })
    return out


def _convert_item(item: dict[str, Any], *, evidence: bool = False) -> dict[str, Any]:
    """One LongMemEval question → one Sample dict.

    IN-5 / IN-4b: the session timestamp (`haystack_dates[i]`) rides on each turn as an
    **`event_time` field**, not folded into the turn text — the retrieval assembly surfaces it
    into the injected context (`[event_time] …`) so `temporal-reasoning` gets its time signal,
    but the date never dilutes BM25/vector matching (an earlier text-prefix version lifted
    temporal but cost single-session-user recall). The `question_date` still prefixes the
    question (that's read by the answerer, not a corpus memory, so it can't dilute retrieval)."""
    dates = item.get("haystack_dates") or []
    sessions_out: list[list[dict[str, str]]] = []
    for i, session in enumerate(item.get("haystack_sessions", [])):
        date = str(dates[i]).strip() if i < len(dates) else ""
        session_out: list[dict[str, str]] = []
        for turn in session:
            text = str(turn.get("content", "")).strip()
            if not text:
                continue  # drop blank/system-only turns
            turn_out: dict[str, str] = {"speaker": str(turn.get("role", "")), "text": text}
            if date:
                turn_out["event_time"] = date  # a field, not a text prefix (IN-4b)
            session_out.append(turn_out)
        if session_out:
            sessions_out.append(session_out)

    question_id = str(item["question_id"])
    question = str(item["question"])
    if question_date := item.get("question_date"):
        question = f"[Today's date is {question_date}.] {question}"
    qa: dict[str, Any] = {
        "question": question,
        "answer": str(item.get("answer", "")),
        "category": str(item.get("question_type") or "") or None,
        # Official convention: abstention questions carry an `_abs` id suffix.
        "abstention": question_id.endswith("_abs"),
    }
    if evidence:
        qa["evidence"] = _evidence(item)
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


def convert(
    raw: list[dict[str, Any]], *, evidence: bool = False
) -> tuple[dict[str, Any], dict[str, int]]:
    """Convert the LongMemEval release list → (dataset dict, conversion stats)."""
    samples = [_convert_item(item, evidence=evidence) for item in raw]
    stats = {
        "questions": len(samples),
        "abstention": sum(1 for s in samples if s["qa"][0]["abstention"]),
    }
    if evidence:
        stats["evidence_turns"] = sum(len(s["qa"][0]["evidence"]) for s in samples)
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
    parser.add_argument("--types", default=None,
                        help="comma-separated question_types to keep (e.g. "
                             "knowledge-update,temporal-reasoning), applied before --limit")
    parser.add_argument("--offset", type=int, default=0,
                        help="skip the first N questions (after --types, before --limit) — e.g. "
                             "a dev split is `--limit 26`, its held-out remainder `--offset 26`")
    parser.add_argument("--evidence", action="store_true",
                        help="carry has_answer turns (+ session date/id) into qa.evidence for "
                             "evidence-level scoring (RQ-3)")
    return parser


def main(argv: list[str] | None = None) -> int:
    from collections import Counter

    args = _build_parser().parse_args(argv)
    raw = json.loads(Path(args.input).read_text())
    if args.types:
        keep = {t.strip() for t in args.types.split(",") if t.strip()}
        raw = [item for item in raw if item.get("question_type") in keep]
    raw = raw[args.offset:]
    if args.stratified:
        raw = _stratified_sample(raw, args.limit if args.limit is not None else len(raw))
    elif args.limit is not None:
        raw = raw[: args.limit]
    dataset, stats = convert(raw, evidence=args.evidence)
    Path(args.output).write_text(json.dumps(dataset, indent=2))
    by_type = Counter(str(item.get("question_type") or "unknown") for item in raw)
    dist = ", ".join(f"{t}={n}" for t, n in sorted(by_type.items()))
    evidence_note = f", {stats['evidence_turns']} evidence turns" if args.evidence else ""
    print(
        f"converted {stats['questions']} questions "
        f"({stats['abstention']} abstention{evidence_note}) → {args.output}\n  types: {dist}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
