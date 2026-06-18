"""Convert the official LoCoMo release into the runner's dataset schema (§23).

The public dataset (`locomo10.json` from snap-research/locomo) is **bring-your-own**
— externally licensed and large, so it's never committed; CI runs on the smoke
slice. This converter maps that JSON into the `{"samples": [...]}` shape
`locomo.load_dataset` consumes, so `eval.benchmark` can run the real benchmark:

    python -m arango_memory.eval.locomo_convert locomo10.json converted.json
    python -m arango_memory.eval.benchmark converted.json --mode lite

Mapping:
  - `conversation.session_N` (ordered numerically) → `sessions`, each turn → {speaker, text}.
  - each QA's first resolvable `evidence` dia-id → `gold_fact` (the turn text retrieval
    must surface for Recall@k); `answer` → `answer`; integer `category` → a name.
  - **Adversarial (category 5) / evidence-less questions are excluded** from the
    converted QA (no supporting fact to retrieve) and counted in the returned stats.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

#: LoCoMo integer categories → names (snap-research/locomo).
CATEGORY_NAMES = {
    1: "multi-hop",
    2: "temporal",
    3: "open-domain",
    4: "single-hop",
    5: "adversarial",
}
_SESSION_KEY = re.compile(r"^session_(\d+)$")  # excludes session_N_date_time


def _ordered_sessions(conversation: dict[str, Any]) -> list[tuple[str, list[dict[str, Any]]]]:
    """The `session_N` lists, in numeric order, paired with their key."""
    keyed = []
    for key, value in conversation.items():
        m = _SESSION_KEY.match(key)
        if m and isinstance(value, list):
            keyed.append((int(m.group(1)), key, value))
    keyed.sort(key=lambda t: t[0])
    return [(key, turns) for _, key, turns in keyed]


def _convert_sample(raw: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """One LoCoMo conversation → one Sample dict. Returns (sample, n_excluded_qa)."""
    conversation = raw["conversation"]
    sessions_out: list[list[dict[str, str]]] = []
    dia_text: dict[str, str] = {}  # dia_id → turn text, for evidence resolution

    for _key, turns in _ordered_sessions(conversation):
        session_out: list[dict[str, str]] = []
        for turn in turns:
            text = str(turn.get("text", "")).strip()
            if not text:
                continue  # skip media-only/blank turns
            session_out.append({"speaker": str(turn.get("speaker", "")), "text": text})
            if dia_id := turn.get("dia_id"):
                dia_text[str(dia_id)] = text
        if session_out:
            sessions_out.append(session_out)

    qa_out: list[dict[str, Any]] = []
    excluded = 0
    for item in raw.get("qa", []):
        category = item.get("category")
        evidence = item.get("evidence") or []
        gold_fact = next((dia_text[e] for e in map(str, evidence) if e in dia_text), None)
        # Adversarial (cat 5) and any QA whose evidence we can't resolve have no fact
        # to surface → out of the retrieval-recall metric (counted, not scored).
        if category == 5 or gold_fact is None:
            excluded += 1
            continue
        qa_out.append(
            {
                "question": str(item["question"]),
                "answer": str(item.get("answer", "")),
                "gold_fact": gold_fact,
                "category": CATEGORY_NAMES.get(category, str(category) if category else None),
            }
        )

    sample = {"sample_id": str(raw["sample_id"]), "sessions": sessions_out, "qa": qa_out}
    return sample, excluded


def convert(raw: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, int]]:
    """Convert the LoCoMo release list → (dataset dict, conversion stats)."""
    samples = []
    excluded = 0
    for raw_sample in raw:
        sample, n_excluded = _convert_sample(raw_sample)
        samples.append(sample)
        excluded += n_excluded
    stats = {
        "samples": len(samples),
        "questions": sum(len(s["qa"]) for s in samples),
        "excluded_adversarial_or_unresolved": excluded,
    }
    return {"samples": samples}, stats


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="arango_memory.eval.locomo_convert")
    parser.add_argument("input", help="path to the official locomo10.json")
    parser.add_argument("output", help="path to write the converted dataset")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    raw = json.loads(Path(args.input).read_text())
    dataset, stats = convert(raw)
    Path(args.output).write_text(json.dumps(dataset, indent=2))
    print(
        f"converted {stats['samples']} samples, {stats['questions']} scorable questions "
        f"({stats['excluded_adversarial_or_unresolved']} adversarial/unresolved excluded) "
        f"→ {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
