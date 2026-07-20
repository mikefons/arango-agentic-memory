"""MuSiQue release → runner-schema converter (BX-1). Pure, no DB."""

from __future__ import annotations

import json
from typing import Any

from arango_memory.eval.locomo import load_dataset
from arango_memory.eval.musique_convert import _load_items, convert

# Two minimal MuSiQue-Ans items: one 2-hop question with 2 supporting + 1 distractor
# paragraph, and one item with no supporting paragraph (must be excluded).
_ITEMS: list[dict[str, Any]] = [
    {
        "id": "2hop__1",
        "question": "Where does the person Alice met at the reunion work?",
        "answer": "Acme",
        "paragraphs": [
            {"idx": 0, "title": "Alice", "paragraph_text": "Alice met Bob at the reunion.",
             "is_supporting": True},
            {"idx": 1, "title": "Bob", "paragraph_text": "Bob works at Acme.",
             "is_supporting": True},
            {"idx": 2, "title": "Weather", "paragraph_text": "It rained all week.",
             "is_supporting": False},
        ],
    },
    {
        "id": "no_support",
        "question": "Unanswerable?",
        "answer": "x",
        "paragraphs": [
            {"idx": 0, "title": "T", "paragraph_text": "Irrelevant text.", "is_supporting": False},
        ],
    },
]


def _two_answerable() -> list[dict[str, Any]]:
    """Two answerable questions that share one paragraph (Bob) — for the pooled/dedup test."""
    return [
        _ITEMS[0],  # Alice + Bob + Weather
        {
            "id": "2hop__2",
            "question": "Who did Bob meet?",
            "answer": "Alice",
            "paragraphs": [
                {"title": "Bob", "paragraph_text": "Bob works at Acme.", "is_supporting": True},
                {"title": "Carol", "paragraph_text": "Carol sails boats.", "is_supporting": True},
            ],
        },
    ]


def test_gold_facts_are_the_supporting_paragraphs_only() -> None:
    dataset, stats = convert(_ITEMS)
    assert stats == {"samples": 1, "questions": 1, "excluded_no_support": 1}
    qa = dataset["samples"][0]["qa"][0]
    assert qa["gold_facts"] == ["Alice met Bob at the reunion.", "Bob works at Acme."]
    assert qa["category"] == "multi-hop"


def test_distractor_is_ingested_but_not_gold() -> None:
    dataset, _ = convert(_ITEMS)
    turns = dataset["samples"][0]["sessions"][0]
    texts = [t["text"] for t in turns]
    assert "It rained all week." in texts  # distractor is part of the searchable corpus
    assert "It rained all week." not in dataset["samples"][0]["qa"][0]["gold_facts"]


def test_round_trips_through_load_dataset_as_multi_evidence_support() -> None:
    dataset, _ = convert(_ITEMS)
    samples = load_dataset_from_dict(dataset)
    qa = samples[0].qa[0]
    assert qa.support() == ["Alice met Bob at the reunion.", "Bob works at Acme."]


def test_limit_caps_the_sample_count() -> None:
    doubled = _ITEMS + [dict(_ITEMS[0], id="2hop__2")]
    dataset, stats = convert(doubled, limit=1)
    assert stats["samples"] == 1


def test_load_items_reads_jsonl_and_array() -> None:
    jsonl = "\n".join(json.dumps(i) for i in _ITEMS)
    assert _load_items(jsonl) == _ITEMS
    assert _load_items(json.dumps(_ITEMS)) == _ITEMS


def test_pooled_merges_into_one_deduped_corpus_keeping_all_questions() -> None:
    dataset, stats = convert(_two_answerable(), pooled=True)
    assert stats["samples"] == 1 and stats["questions"] == 2  # one tenant, both questions
    sample = dataset["samples"][0]
    assert sample["sample_id"] == "musique-pooled"
    assert len(sample["qa"]) == 2  # every question retained, scored against the shared corpus
    corpus = [t["text"] for t in sample["sessions"][0]]
    # 5 raw paragraphs across the two questions, but "Bob works at Acme." appears in both →
    # deduped to 4, and reported in stats.
    assert corpus.count("Bob works at Acme.") == 1
    assert stats["corpus_paragraphs"] == 4
    assert set(corpus) == {
        "Alice met Bob at the reunion.", "Bob works at Acme.",
        "It rained all week.", "Carol sails boats.",
    }


def test_pooled_gold_facts_survive_and_round_trip() -> None:
    dataset, _ = convert(_two_answerable(), pooled=True)
    samples = load_dataset_from_dict(dataset)
    supports = {tuple(qa.support()) for qa in samples[0].qa}
    assert ("Alice met Bob at the reunion.", "Bob works at Acme.") in supports
    assert ("Bob works at Acme.", "Carol sails boats.") in supports


def load_dataset_from_dict(dataset: dict[str, Any]) -> Any:
    """Round-trip the converted dict through JSON so load_dataset parses it (as in a real run)."""
    import tempfile
    from pathlib import Path

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        fh.write(json.dumps(dataset))
        path = fh.name
    return load_dataset(Path(path))
