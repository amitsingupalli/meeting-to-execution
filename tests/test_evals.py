from __future__ import annotations

from meeting_to_execution.evals import load_golden, run_evals


def test_golden_set_is_well_formed() -> None:
    golden = load_golden()
    assert len(golden.meetings) == 4
    assert len(golden.conflicts) == 6


def test_deterministic_pipeline_matches_golden_set() -> None:
    report = run_evals()

    assert report.failures == []
    assert report.passed is True
    assert report.extraction_precision == 1.0
    assert report.extraction_recall == 1.0
    assert report.conflict_precision == 1.0
    assert report.conflict_recall == 1.0
    assert report.expected_items == report.matched_items == 22
    assert report.expected_conflicts == report.matched_conflicts == 6
    assert report.stats["meetings"] == 4
    assert report.stats["active_decisions"] == 6
    assert report.stats["superseded_decisions"] == 3
    assert report.stats["posted_tasks"] == 9
