"""SC-1a scaling profiler: percentile helper (no DB) + a tiny profile smoke (DB)."""

from __future__ import annotations

from arango.database import StandardDatabase

from arango_memory.eval.scaling_profile import ProfileRow, _content, _format, _pct, profile


def test_pct_nearest_rank() -> None:
    vals = [10.0, 20.0, 30.0, 40.0]
    assert _pct(vals, 0.5) == 30.0
    assert _pct(vals, 0.99) == 40.0
    assert _pct([], 0.5) == 0.0


def test_content_is_entity_rich() -> None:
    # capitalized spans → entities; the fake extractor accumulates them across stores.
    c = _content(7)
    assert "Ent7" in c and "Ent8" in c and "Hub" in c and "Topic" in c


def test_format_reports_growth() -> None:
    rows = [ProfileRow(10, 5.0, 6.0, 1.0, 2.0), ProfileRow(20, 15.0, 20.0, 2.0, 3.0)]
    out = _format(rows)
    assert "3.0×" in out  # 15/5
    assert "O(N²)" in out


def test_profile_runs_and_samples(db: StandardDatabase) -> None:
    rows = profile(db, max_n=20, step=10, probes=3, tenant_id="t_scale")
    assert len(rows) == 2  # checkpoints at 10 and 20
    assert [r.size for r in rows] == [10, 20]
    assert all(r.store_p50 >= 0.0 and r.retrieve_p50 >= 0.0 for r in rows)
