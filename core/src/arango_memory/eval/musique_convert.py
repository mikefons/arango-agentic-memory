"""Convert the MuSiQue-Ans release into the runner's dataset schema (BX-1, DESIGN §23).

MuSiQue (Trivedi et al., TACL 2022) is **bring-your-own** — externally licensed and
large, so it is never committed. Each JSONL line is one 2–4-hop question with ~20
candidate paragraphs (2–4 supporting + distractors) and `is_supporting` annotations.
Unlike LoCoMo, its evidence is genuinely multi-turn, so it exercises the multi-evidence
recall metric (BX-1a) that LoCoMo's single-turn `gold_fact` cannot.

    python -m arango_memory.eval.musique_convert musique_ans_v1.0_dev.jsonl musique.json [--limit N]
    python -m arango_memory.eval.benchmark musique.json --mode lite

Mapping (one question → one Sample, faithful to MuSiQue's per-question context):
  - each candidate `paragraph` → a turn `{speaker: title, text: paragraph_text}`; distractors
    are ingested too, as retrieval difficulty (retrieval is tenant-scoped per question).
  - the `is_supporting` paragraphs' text → `gold_facts` (the multi-evidence support set
    Recall@k / recall-frac score against); `question`/`answer` pass through; `category`
    = "multi-hop".
  - questions with no supporting paragraph are skipped (nothing to retrieve) and counted.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, cast


def _load_items(text: str) -> list[dict[str, Any]]:
    """Read MuSiQue as JSONL (one object per line) or a JSON array, whichever it is."""
    stripped = text.lstrip()
    if stripped.startswith("["):
        return cast(list[dict[str, Any]], json.loads(text))
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _convert_item(raw: dict[str, Any]) -> tuple[dict[str, Any] | None, bool]:
    """One MuSiQue question → one Sample dict. Returns (sample or None, excluded)."""
    turns: list[dict[str, str]] = []
    gold_facts: list[str] = []
    for para in raw.get("paragraphs", []):
        text = str(para.get("paragraph_text", "")).strip()
        if not text:
            continue
        title = str(para.get("title", "")).strip()
        turns.append({"speaker": title or "context", "text": text})
        if para.get("is_supporting"):
            gold_facts.append(text)
    # No supporting paragraph → nothing to retrieve → out of the recall metric (counted).
    if not gold_facts or not turns:
        return None, True
    sample = {
        "sample_id": str(raw["id"]),
        "sessions": [turns],
        "qa": [
            {
                "question": str(raw["question"]),
                "answer": str(raw.get("answer", "")),
                "gold_facts": gold_facts,
                "category": "multi-hop",
            }
        ],
    }
    return sample, False


def _pool(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Collapse per-question samples into one shared-corpus Sample (BX-2, open retrieval):
    dedup every paragraph by (title, text) into a single tenant and keep every question, so
    each query retrieves against the *whole* corpus, not just its own ~20 paragraphs."""
    seen: set[tuple[str, str]] = set()
    turns: list[dict[str, str]] = []
    qa: list[dict[str, Any]] = []
    for sample in samples:
        for session in sample["sessions"]:
            for turn in session:
                key = (turn["speaker"], turn["text"])
                if key in seen:
                    continue
                seen.add(key)
                turns.append(turn)
        qa.extend(sample["qa"])
    return {"sample_id": "musique-pooled", "sessions": [turns], "qa": qa}


def convert(
    items: list[dict[str, Any]], *, limit: int | None = None, pooled: bool = False
) -> tuple[dict[str, Any], dict[str, int]]:
    """Convert MuSiQue items → (dataset dict, conversion stats). `limit` caps the questions;
    `pooled` merges them into one open-retrieval corpus (BX-2) instead of per-question tenants.
    """
    samples: list[dict[str, Any]] = []
    excluded = 0
    for raw in items:
        sample, was_excluded = _convert_item(raw)
        if was_excluded:
            excluded += 1
            continue
        assert sample is not None
        samples.append(sample)
        if limit is not None and len(samples) >= limit:
            break
    n_questions = len(samples)  # one question per MuSiQue item
    if pooled and samples:
        pooled_sample = _pool(samples)
        stats = {
            "samples": 1,
            "questions": n_questions,
            "corpus_paragraphs": len(pooled_sample["sessions"][0]),
            "excluded_no_support": excluded,
        }
        return {"samples": [pooled_sample]}, stats
    stats = {
        "samples": n_questions,
        "questions": n_questions,
        "excluded_no_support": excluded,
    }
    return {"samples": samples}, stats


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="arango_memory.eval.musique_convert")
    parser.add_argument("input", help="path to a MuSiQue-Ans .jsonl (or .json array)")
    parser.add_argument("output", help="path to write the converted dataset")
    parser.add_argument(
        "--limit", type=int, default=None, help="keep only the first N questions (smoke run)"
    )
    parser.add_argument(
        "--pooled", action="store_true",
        help="pool all selected questions' deduped paragraphs into one open-retrieval corpus "
             "(BX-2); default is one ~20-paragraph tenant per question",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    items = _load_items(Path(args.input).read_text())
    dataset, stats = convert(items, limit=args.limit, pooled=args.pooled)
    Path(args.output).write_text(json.dumps(dataset, indent=2))
    corpus = f", {stats['corpus_paragraphs']} pooled paragraphs" if args.pooled else ""
    print(
        f"converted {stats['questions']} multi-hop questions"
        f"{corpus} ({stats['excluded_no_support']} without support excluded) → {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
