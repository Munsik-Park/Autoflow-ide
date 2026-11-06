"""Reading what a module run left in its output folder, on output recorded from AutoFlow v0.7.0
(tests/fixtures/README.md)."""
from pathlib import Path

from autoflow_ide.results import read_outcome

RECORDS = Path(__file__).parent / "fixtures" / "records" / "autoflow-sandbox-6-results"


def test_an_evaluation():
    outcome = read_outcome(str(RECORDS / "plan-1"))
    assert (outcome.folder, outcome.has_result, outcome.status) == (True, True, "accepted")
    assert outcome.outputs == ["evaluation.json"]          # result.json names it where the run wrote it
    evaluation = outcome.evaluation
    assert evaluation.scores == [("Decision grounds", "9"), ("Verification fit", "9"), ("Scope", "9"), ("Security", "9")]
    assert evaluation.summary.startswith("GATE:PLAN, issue #4, first evaluation")
    assert len(evaluation.recommendations) == 7
    first = evaluation.recommendations[0]
    assert (first.severity, first.item, first.subject) == (
        "Low", "Decision grounds", "feature-design.md > Decisions > D4 (Holds under)")
    assert first.finding.startswith("D4 does not mention the stdout encoding")
    assert (outcome.findings, outcome.failure, outcome.defects, outcome.lacked, outcome.unreadable) == (None, None, [], [], [])


def test_review_findings():
    outcome = read_outcome(str(RECORDS / "review-1"))
    assert outcome.status == "accepted" and outcome.evaluation is None
    assert outcome.findings.startswith("# Review findings — Munsik-Park/autoflow-sandbox#8 (issue #4)")
    assert outcome.outputs == ["reviews/claude.md", "reviews/codex.md", "review-findings.md"]


def test_what_a_run_lacked():
    outcome = read_outcome(str(RECORDS / "build-2"))
    assert outcome.status == "accepted"
    assert outcome.outputs == ["commits.json", "build-report.md"]
    [(missing, instead)] = outcome.lacked
    assert missing.startswith("A Windows environment") and instead.startswith("The F1 test's acceptance")


def test_refused_at_check_in():
    outcome = read_outcome(str(RECORDS / "review-2"))
    assert (outcome.status, outcome.outputs) == ("refused", [])
    assert outcome.defects == ["param 'pr' does not match ^[^/]+/[^#]+#[0-9]+$: 8"]


def test_failed():
    outcome = read_outcome(str(RECORDS / "review-3"))
    assert outcome.status == "failed"
    assert outcome.failure.startswith("# Review — failure (issue #6)")
    assert (outcome.outputs, outcome.findings) == (["failure.md"], None)


def test_no_result_yet_and_no_folder(tmp_path):
    running = read_outcome(str(RECORDS / "review-4"))
    assert (running.folder, running.has_result, running.status) == (True, False, None)
    gone = read_outcome(str(tmp_path / "nothing"))
    assert (gone.folder, gone.has_result) == (False, False)


def test_unreadable_files_are_named(tmp_path):
    (tmp_path / "result.json").write_text("{not json")
    (tmp_path / "evaluation.json").write_text("[]")
    outcome = read_outcome(str(tmp_path))
    assert outcome.has_result and outcome.status is None
    assert [u.split(":")[0] for u in outcome.unreadable] == ["result.json", "evaluation.json"]
