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
    """One LongMemEval question → one Sample dict."""
    sessions_out: list[list[dict[str, str]]] = []
    for session in item.get("haystack_sessions", []):
        session_out: list[dict[str, str]] = []
        for turn in session:
            text = str(turn.get("content", "")).strip()
            if not text:
                continue  # drop blank/system-only turns
            session_out.append({"speaker": str(turn.get("role", "")), "text": text})
        if session_out:
            sessions_out.append(session_out)

    question_id = str(item["question_id"])
    qa = {
        "question": str(item["question"]),
        "answer": str(item.get("answer", "")),
        "category": str(item.get("question_type") or "") or None,
        # Official convention: abstention questions carry an `_abs` id suffix.
        "abstention": question_id.endswith("_abs"),
    }
    return {"sample_id": question_id, "sessions": sessions_out, "qa": [qa]}


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
                        help="convert only the first N questions")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    raw = json.loads(Path(args.input).read_text())
    if args.limit is not None:
        raw = raw[: args.limit]
    dataset, stats = convert(raw)
    Path(args.output).write_text(json.dumps(dataset, indent=2))
    print(
        f"converted {stats['questions']} questions "
        f"({stats['abstention']} abstention) → {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
