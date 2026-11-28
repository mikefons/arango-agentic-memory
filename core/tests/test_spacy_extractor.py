"""SpacyExtractor's own logic, with a stand-in pipeline (spaCy isn't installed in CI)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from arango_memory.ingest.extract import ExtractedEntity, SpacyExtractor


@dataclass
class _Ent:
    text: str
    label_: str


@dataclass
class _Doc:
    ents: list[_Ent]


class _Pipeline:
    def __init__(self, *ents: tuple[str, str]) -> None:
        self.seen: list[str] = []
        self._ents = [_Ent(*e) for e in ents]

    def __call__(self, text: str) -> _Doc:
        self.seen.append(text)
        return _Doc(self._ents)


def test_values_are_not_entities() -> None:
    nlp = _Pipeline(
        ("Acme", "ORG"), ("Ada", "PERSON"), ("March 2026", "DATE"), ("41%", "PERCENT"),
        ("$5.2M", "MONEY"), ("12", "CARDINAL"), ("first", "ORDINAL"), ("3 km", "QUANTITY"),
        ("noon", "TIME"), ("French", "NORP"), ("Ada", "PERSON"),
    )
    assert SpacyExtractor(nlp=nlp).extract("whatever") == [
        ExtractedEntity("Acme", "Organization"),
        ExtractedEntity("Ada", "Person"),
        ExtractedEntity("French", "Concept"),  # still a thing, just not one of our labels
    ]


def test_a_spaced_dash_or_slash_is_read_as_a_sentence_break() -> None:
    nlp = _Pipeline()
    SpacyExtractor(nlp=nlp).extract("Acme — revenue: up – again, in a well-run 2019–2020 plan")
    SpacyExtractor(nlp=_Pipeline()).extract("x")
    SpacyExtractor(nlp=nlp).extract("Acme / Globex on AC/DC and and/or")
    assert nlp.seen == ["Acme. revenue: up. again, in a well-run 2019–2020 plan",
                        "Acme. Globex on AC/DC and and/or"]


def _names(*spans: str) -> list[str]:
    nlp = _Pipeline(*[(span, "ORG") for span in spans])
    return [e.name for e in SpacyExtractor(nlp=nlp).extract("x")]


def test_trailing_shorthand_is_trimmed_off_a_name() -> None:
    assert _names("Northwind CFO", "Acme Robotics ARR", "Globex CRM", "Initech Q4 2025",
                  "Umbrella FY2025 NRR", "Hooli's CFO") == [
        "Northwind", "Acme Robotics", "Globex", "Initech Q4 2025", "Umbrella", "Hooli"]


def test_a_comma_list_of_names_is_split() -> None:
    assert _names("Orion Retail, Vertex Foods") == ["Orion Retail", "Vertex Foods"]
    whole = ["Acme, Inc.", "Skadden, Arps, Slate", "Acme Foods, LLC"]
    assert _names(*whole) == whole
    assert _names("Acme Foods, the CFO") == ["Acme Foods"]


def test_document_words_are_dropped_only_alone() -> None:
    assert _names("Management", "Letter of Intent", "the Management", "Acme Asset Management",
                  "Acme Letter of Intent") == ["Acme Asset Management", "Acme Letter of Intent"]


def test_an_article_does_not_keep_a_document_word() -> None:
    assert _names("a Letter of Intent", "A Management", "an LOI", "a", "An Post") == ["An Post"]


def test_a_cut_off_bracket_is_not_part_of_a_name() -> None:
    assert _names("Northwind (Series", "Acme Robotics (Series B diligence", "(Series",
                  "Initech (UK) Labs") == ["Northwind", "Acme Robotics", "Initech (UK) Labs"]


@pytest.mark.parametrize(
    ("span", "found"),
    [
        ("CFO Marcus Cole", [("Marcus Cole", "Person")]),
        ("Northwind CFO Marcus Cole",
         [("Northwind", "Organization"), ("Marcus Cole", "Person")]),
        ("Northwind's CEO Dana Reyes",
         [("Northwind", "Organization"), ("Dana Reyes", "Person")]),
        ("the Interim CFO Marcus Cole", [("Marcus Cole", "Person")]),
        ("CEO & CFO Marcus Cole", [("Marcus Cole", "Person")]),
        # one word, or none, after the title: not read as a person
        ("CFO Partners", [("CFO Partners", "Organization")]),
        ("Northwind CFO", [("Northwind", "Organization")]),
        ("CTO of Northwind", [("CTO of Northwind", "Organization")]),
    ],
)
def test_a_name_is_split_at_a_job_title(span: str, found: list[tuple[str, str]]) -> None:
    entities = SpacyExtractor(nlp=_Pipeline((span, "ORG"))).extract("x")
    assert [(e.name, e.label) for e in entities] == found


def test_possessive_endings_are_not_part_of_a_name() -> None:
    nlp = _Pipeline(
        ("Vertex Foods'", "ORG"), ("Vertex Foods", "ORG"), ("Marcus Cole's", "PERSON"),
        ("Acme\u2019s", "ORG"), ("O'Brien", "PERSON"), ("'s", "ORG"),
    )
    assert [e.name for e in SpacyExtractor(nlp=nlp).extract("x")] == [
        "Vertex Foods", "Marcus Cole", "Acme", "O'Brien"]


def test_business_shorthand_is_not_an_entity() -> None:
    dropped = ["ARR", "NRR", "the LOI", "CFO", "CRM", "IP", "Q&A", "SaaS", "FY", "FY2025",
               "Q4", "Q4 2025", "NRR's"]
    kept = ["IBM", "SEC", "NASA", "CFO Partners", "Arr", "Q-Branch", "FYI Labs"]
    nlp = _Pipeline(*[(name, "ORG") for name in [*dropped, *kept]])
    assert [e.name for e in SpacyExtractor(nlp=nlp).extract("x")] == kept
