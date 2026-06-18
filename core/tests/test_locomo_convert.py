"""LoCoMo release → runner-schema converter (DESIGN.md §23). Pure, no DB."""

from __future__ import annotations

from typing import Any

from arango_memory.eval.locomo import Sample, load_dataset
from arango_memory.eval.locomo_convert import convert

# A minimal stand-in for one official locomo10.json conversation: two ordered
# sessions with dia-ids, plus QA referencing evidence and an adversarial item.
_RAW: list[dict[str, Any]] = [
    {
        "sample_id": "conv-1",
        "conversation": {
            "speaker_a": "Caroline",
            "speaker_b": "Melanie",
            "session_1_date_time": "1:00 pm on 8 May, 2023",
            "session_1": [
                {"speaker": "Caroline", "text": "I adopted a dog named Biscuit.", "dia_id": "D1:1"},
                {"speaker": "Melanie", "text": "How old is he?", "dia_id": "D1:2"},
                {"speaker": "Caroline", "text": "", "dia_id": "D1:3", "img_url": "x"},
            ],
            "session_2_date_time": "9:00 am on 20 May, 2023",
            "session_2": [
                {"speaker": "Caroline", "text": "I started at Vertex Logistics.", "dia_id": "D2:1"},
            ],
        },
        "qa": [
            {"question": "What pet does Caroline have?", "answer": "a dog named Biscuit",
             "evidence": ["D1:1"], "category": 4},
            {"question": "Where does Caroline work?", "answer": "Vertex Logistics",
             "evidence": ["D2:1"], "category": 1},
            {"question": "What car does Caroline drive?", "answer": "Not mentioned",
             "evidence": [], "category": 5},  # adversarial → excluded
        ],
    }
]


def test_convert_maps_sessions_and_evidence() -> None:
    dataset, stats = convert(_RAW)
    sample = dataset["samples"][0]

    assert [t["text"] for t in sample["sessions"][0]] == [
        "I adopted a dog named Biscuit.", "How old is he?",  # blank/media turn dropped
    ]
    assert sample["sessions"][1][0]["speaker"] == "Caroline"  # session_2 ordered after _1

    # gold_fact = the evidence turn's text; category int → name.
    pet_qa = next(q for q in sample["qa"] if "pet" in q["question"])
    assert pet_qa["gold_fact"] == "I adopted a dog named Biscuit."
    assert pet_qa["category"] == "single-hop"


def test_convert_excludes_adversarial_and_counts_it() -> None:
    dataset, stats = convert(_RAW)
    questions = dataset["samples"][0]["qa"]
    assert len(questions) == 2  # the category-5 question is dropped
    assert all("car" not in q["question"] for q in questions)
    assert stats == {"samples": 1, "questions": 2, "excluded_adversarial_or_unresolved": 1}


def test_converted_output_loads_as_runner_dataset(tmp_path: Any) -> None:
    import json

    dataset, _ = convert(_RAW)
    path = tmp_path / "converted.json"
    path.write_text(json.dumps(dataset))
    samples = load_dataset(path)  # must parse cleanly into Sample/Turn/QA
    assert isinstance(samples[0], Sample)
    assert samples[0].qa[0].gold_fact
