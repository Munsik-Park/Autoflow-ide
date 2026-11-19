import json
import sys
from pathlib import Path

import pytest

from autoflow_ide.runs import RunSourceError, coordinator_runs, parse_status

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return (FIXTURES / name).read_text()


def test_parse_recorded_run():
    [run] = parse_status(fixture("status-run.json"))
    assert (run.name, run.issue, run.branch, run.error) == ("autoflow-sandbox-3-sample", "3", "issue-3-sample", None)
    assert [(s.name, s.session, s.state, s.verdict) for s in run.steps] == [
        ("analysis-1", "S1", "pass", "pass"),
        ("review-1", "S1", "fail", "missed"),
        ("build-1 (rerun)", "S2", "noted", "noted"),
    ]
    assert [(s.name, s.state, s.surface) for s in run.sessions] == [("S1", "exited", "%1"), ("S2", "exited", "%2")]
    assert (run.reference, run.coordination) == ("analysis-review", None)   # run.env has no COORD_SURFACE
    assert (run.checkout, run.records) == (None, None)


def test_parse_recorded_reviews():
    # status.py of 0.8.0-rc2 on a run with three review module runs (tests/fixtures/README.md, #83)
    [run] = parse_status(fixture("status-reviews.json"))
    name = "autoflow-coordinator-119-2026-11-19-180359-3cbd8c18"
    records = f"/home/claude/fx/records/{name}/"
    assert (run.name, run.issue, run.branch, run.reference, run.coordination) == (
        name, "119", "issue-119-2026-11-19-180359-3cbd8c18", "review-only", "%1")
    assert [(s.shown, s.session, s.state, s.verdict, s.result, s.out) for s in run.steps] == [
        ("S1 (outside AutoFlow: implement and PR)", "S1", "pass", "met", None, None),
        ("review-1", "S2", "pass", "met", "ended", records + "review-1"),
        ("S1 fix 1 (review-1 F1)", "S1", "pass", "met", None, None),
        ("↺ review-2 ← review-1", "S2", "fail", "missed", "ended", records + "review-2"),
        ("S1 fix 2 (review-2 R2-1)", "S1", "pass", "met", None, None),
        ("↺ review-3 ← review-2", "S2", "pass", "met", "ended", records + "review-3"),
        ("S1 fix 3 (review-3 R3-1, comment-only)", "S1", "pass", "met", None, None),
        ("Merge review artifact (pr-postcard, one-off)", "X1", "pass", "met", None, None),
    ]
    review_2 = run.steps[3]
    assert review_2.verdict_text.startswith("missed: highest holding Medium (R2-1), head reviewed 9a46918")
    assert review_2.notes.startswith("previous_findings review-1/report.md; round 1 dispositions in the instructions")
    assert "https://example.com/artifact" in run.steps[-1].verdict_text      # the link replaced (README)
    assert [(s.name, s.state, s.surface) for s in run.sessions] == [
        ("S1", "exited", "%2"), ("S2", "exited", "%3"), ("X1", "exited", "%4")]
    assert (run.checkout, run.records) == (
        f"/home/claude/fx/base/autoflow-coordinator/autoflow-coordinator-wt-119-2026-11-19-180359-3cbd8c18",
        records.rstrip("/"))


def test_parse_recorded_coordination_surface():
    [run] = parse_status(fixture("status-coordination.json"))
    assert (run.reference, run.branch, run.coordination) == ("review-only", "issue-3-coord", "%34")


def test_parse_recorded_error_and_none():
    [run] = parse_status(fixture("status-error.json"))
    assert run.error.startswith("FileNotFoundError") and run.steps == [] and run.issue is None
    assert parse_status(fixture("status-none.json")) == []


@pytest.mark.parametrize("label, module, name", [
    ("s1", "review", "review s1"),
    ("build-2", "build", "build-2"),
    ("work and PR (outside AutoFlow)", None, "work and PR (outside AutoFlow)"),
])
def test_step_name_carries_module_and_label(label, module, name):
    step = json.dumps({"label": label, "module": module, "state": "running"})
    text = '{"runs": [{"name": "r", "steps": [%s]}]}' % step
    assert parse_status(text)[0].steps[0].name == name


@pytest.mark.parametrize("text", ["not json", "{}", '{"runs": [{"steps": []}]}'])
def test_parse_unexpected_output(text):
    with pytest.raises(RunSourceError, match="unexpected status.py output"):
        parse_status(text)


def fake_coordinator(tmp_path, body):
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "status.py").write_text(body)
    return str(tmp_path)


async def test_coordinator_runs_runs_status_py(tmp_path):
    recorded = fixture("status-run.json")
    coordinator = fake_coordinator(tmp_path, f"import sys\nsys.stdout.write({recorded!r})\n")
    [run] = await coordinator_runs(coordinator)
    assert run.name == "autoflow-sandbox-3-sample"


async def test_coordinator_runs_failure_carries_the_first_line(tmp_path):
    coordinator = fake_coordinator(tmp_path, "import sys\nsys.exit('runs root is not set')\n")
    with pytest.raises(RunSourceError, match="^runs root is not set$"):
        await coordinator_runs(coordinator)


async def test_no_status_py(tmp_path):
    with pytest.raises(RunSourceError, match="no .*status.py"):
        await coordinator_runs(str(tmp_path))


from autoflow_ide.runs import SUPPORTED_COORDINATOR, version_notice


def test_version_notice(tmp_path):
    assert version_notice(str(tmp_path)).startswith("coordinator: no ")
    # Any 0.8.0-rcN is the supported 0.8.0, and 0.7.0 is not (#83).
    for same in (SUPPORTED_COORDINATOR, "0.8.0", "0.8.0-rc1", "0.8.0-rc2", "0.8.0-rc12"):
        (tmp_path / "VERSION").write_text(same + "\n")
        assert version_notice(str(tmp_path)) == "", same
    for other in ("0.7.0", "0.7.0-rc9", "0.8.1", "0.8.0-beta1"):
        (tmp_path / "VERSION").write_text(other + "\n")
        assert version_notice(str(tmp_path)) == f"coordinator {other}, supported {SUPPORTED_COORDINATOR}"


def test_supported_version_is_the_recorded_one():
    assert "v" + SUPPORTED_COORDINATOR in (FIXTURES / "README.md").read_text()


def test_parse_a_return_and_a_rerun():
    run = parse_status((FIXTURES / "status-cycle.json").read_text())[0]
    by_label = {s.label: s for s in run.steps}
    assert [s.label for s in run.steps] == ["analysis-1", "build-1", "review-1", "build-2", "review-2"]
    assert (by_label["review-1"].state, by_label["review-1"].verdict) == ("fail", "missed")
    assert (by_label["build-2"].entry, by_label["build-2"].cause) == ("return", "review-1")
    assert (by_label["review-2"].entry, by_label["review-2"].cause) == ("rerun", "review-1")
    assert all(s.entry is None and s.cause is None for s in run.steps[:3])
    assert all(s.session_state is None and s.idle_sec is None for s in run.steps)
    # each module run's work folder, and its end record's status (#83)
    records = "/home/claude/fx/records/autoflow-sandbox-5-cycle/"
    assert [(s.out, s.result) for s in run.steps] == [(records + s.label, "ended") for s in run.steps]


def test_parse_a_session_waiting_for_input():
    # status-cycle.json with S2 in waiting-input, the state status.py gives a session that waits (#79).
    [run] = parse_status(fixture("status-waiting.json"))
    assert [(s.name, s.state) for s in run.sessions] == [("S1", "exited"), ("S2", "waiting-input"), ("S3", "exited")]
