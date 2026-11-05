import sys
from pathlib import Path

import pytest

from autoflow_ide.runs import RunSourceError, coordinator_runs, parse_status

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return (FIXTURES / name).read_text()


def test_parse_recorded_run():
    [run] = parse_status(fixture("status-run.json"))
    assert (run.name, run.issue, run.branch, run.error) == ("issue-3-sample", "3", "issue-3-sample", None)
    assert [(s.name, s.session, s.state, s.verdict) for s in run.steps] == [
        ("analysis-1", "S1", "pass", "pass"),
        ("review-1", "S1", "fail", "missed"),
        ("build-1 (rerun)", "S2", "noted", "noted"),
    ]
    assert [(s.name, s.state, s.surface) for s in run.sessions] == [("S1", "exited", "%1"), ("S2", "exited", "%2")]


def test_parse_recorded_error_and_none():
    [run] = parse_status(fixture("status-error.json"))
    assert run.error.startswith("FileNotFoundError") and run.steps == [] and run.issue is None
    assert parse_status(fixture("status-none.json")) == []


def test_module_name_wins_over_label():
    text = '{"runs": [{"name": "r", "steps": [{"label": "build-2", "module": "build", "state": "running"}]}]}'
    assert parse_status(text)[0].steps[0].name == "build"


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
    assert run.name == "issue-3-sample"


async def test_coordinator_runs_failure_carries_the_first_line(tmp_path):
    coordinator = fake_coordinator(tmp_path, "import sys\nsys.exit('runs root is not set')\n")
    with pytest.raises(RunSourceError, match="^runs root is not set$"):
        await coordinator_runs(coordinator)


async def test_no_status_py(tmp_path):
    with pytest.raises(RunSourceError, match="no .*status.py"):
        await coordinator_runs(str(tmp_path))
