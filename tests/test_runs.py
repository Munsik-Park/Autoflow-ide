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
    assert (run.reference, run.coordination) == ("analysis-review", None)   # rc2 has no coordination


def test_parse_recorded_results():
    [run] = parse_status(fixture("status-results.json"))
    records = "/home/claude/fx/records/autoflow-sandbox-6-results/"
    assert [(s.shown, s.state, s.result, s.out) for s in run.steps] == [
        ("plan-1", "pass", "accepted", records + "plan-1"),
        ("build-1", "pass", "accepted", records + "build-1"),
        ("review-1", "fail", "accepted", records + "review-1"),
        ("↩ build-2 ← review-1", "pass", "accepted", records + "build-2"),
        ("CI check (gh)", "pass", None, None),
        ("↺ review-2 ← review-1", "awaiting", "refused", records + "review-2"),
        ("↺ review-3 ← review-1", "fail", "failed", records + "review-3"),
        ("↺ review-4 ← review-1", "running", None, records + "review-4"),
    ]


def test_before_rc5_a_step_has_no_out_or_result():
    [run] = parse_status(fixture("status-cycle.json"))
    assert {(s.out, s.result) for s in run.steps} == {(None, None)}


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
    for same in (SUPPORTED_COORDINATOR, "0.7.0-rc2", "0.7.0-rc3", "0.7.0-rc12"):
        (tmp_path / "VERSION").write_text(same + "\n")
        assert version_notice(str(tmp_path)) == "", same
    for other in ("0.8.0-rc1", "0.7.1", "0.7.0-beta1"):
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
