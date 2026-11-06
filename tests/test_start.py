import json
import os
import stat

import pytest

from autoflow_ide.start import (StartError, Workspace, coordinator_workspaces, find_workspace, find_workspace_entry,
                                instruction, list_workspaces, open_coordination, start_run, workspace_base)


def fake_tmux(tmp_path, monkeypatch, fail_with=None):
    """A tmux on PATH that records its arguments; new-window prints a pane id as -P -F '#{pane_id}' does."""
    log = tmp_path / "calls"
    script = f'#!/bin/sh\necho "$@" >> "{log}"\n'
    if fail_with:
        script += f'echo "{fail_with}" >&2\nexit 1\n'
    script += 'if [ "$1" = new-window ]; then echo %7; fi\n'
    tmux = tmp_path / "tmux"
    tmux.write_text(script)
    tmux.chmod(tmux.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("TMUX", "/tmp/tmux-test,1,0")
    return log


def coordinator_clone(tmp_path, base):
    """A coordinator clone whose tools/config.py prints `base` for the key base, as the coordinator's
    config.py prints a setting's value alone (expanded) when given its key."""
    tools = tmp_path / "coordination" / "tools"
    tools.mkdir(parents=True)
    (tools / "config.py").write_text(
        "import sys\n"
        f"print({str(base)!r}) if sys.argv[1:] == ['base'] else sys.exit('unknown key ' + ' '.join(sys.argv[1:]))\n")
    return str(tmp_path / "coordination")


def workspace(base, name, github):
    (base / name).mkdir(parents=True)
    (base / name / "repo.json").write_text(json.dumps({"github": github, "clone": "~/x", "base_branch": "main"}))


def test_instruction_is_the_operators():
    # As the operator gave it to the coordination session in the sandbox runs (run record, verbatim).
    assert instruction("autoflow-sandbox", 2) == "Workspace autoflow-sandbox, issue #2."


def test_find_workspace(tmp_path):
    workspace(tmp_path, "autoflow-sandbox", "Munsik-Park/autoflow-sandbox")
    workspace(tmp_path, "opensre", "Munsik-Park/opensre")
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "repo.json").write_text("not json")
    assert find_workspace(tmp_path, "Munsik-Park/autoflow-sandbox") == "autoflow-sandbox"
    with pytest.raises(StartError, match="no workspace for owner/none"):
        find_workspace(tmp_path, "owner/none")
    workspace(tmp_path, "sandbox-2", "Munsik-Park/autoflow-sandbox")
    with pytest.raises(StartError, match="several workspaces .*: autoflow-sandbox, sandbox-2"):
        find_workspace(tmp_path, "Munsik-Park/autoflow-sandbox")


def test_find_workspace_entry_reads_repo_json(tmp_path):
    workspace(tmp_path, "autoflow-sandbox", "Munsik-Park/autoflow-sandbox")
    (tmp_path / "bare").mkdir()
    (tmp_path / "bare" / "repo.json").write_text(json.dumps({"github": "owner/bare"}))
    assert find_workspace_entry(tmp_path, "Munsik-Park/autoflow-sandbox") == Workspace(
        "autoflow-sandbox", "Munsik-Park/autoflow-sandbox", "~/x", "main")
    assert find_workspace_entry(tmp_path, "owner/bare") == Workspace("bare", "owner/bare", None, None)


def test_list_workspaces_in_name_order_skipping_what_is_not_one(tmp_path):
    workspace(tmp_path, "opensre", "Munsik-Park/opensre")
    workspace(tmp_path, "autoflow-sandbox", "Munsik-Park/autoflow-sandbox")
    (tmp_path / "bare").mkdir()
    (tmp_path / "bare" / "repo.json").write_text(json.dumps({"github": "owner/bare"}))
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "repo.json").write_text("not json")
    (tmp_path / "list").mkdir()
    (tmp_path / "list" / "repo.json").write_text("[]")
    (tmp_path / "nogithub").mkdir()
    (tmp_path / "nogithub" / "repo.json").write_text(json.dumps({"clone": "~/x"}))
    (tmp_path / "records").mkdir()
    assert list_workspaces(tmp_path) == [
        Workspace("autoflow-sandbox", "Munsik-Park/autoflow-sandbox", "~/x", "main"),
        Workspace("bare", "owner/bare", None, None),
        Workspace("opensre", "Munsik-Park/opensre", "~/x", "main"),
    ]
    assert list_workspaces(tmp_path / "nowhere") == []


async def test_coordinator_workspaces_are_those_of_its_base(tmp_path):
    base = tmp_path / "base"
    workspace(base, "autoflow-sandbox", "Munsik-Park/autoflow-sandbox")
    workspace(base, "opensre", "Munsik-Park/opensre")
    clone = coordinator_clone(tmp_path, base)
    assert [w.name for w in await coordinator_workspaces(clone)] == ["autoflow-sandbox", "opensre"]


async def test_workspace_base_from_config_py(tmp_path):
    clone = coordinator_clone(tmp_path, tmp_path / "base")
    assert await workspace_base(clone) == tmp_path / "base"
    with pytest.raises(StartError, match="no .*config.py"):
        await workspace_base(str(tmp_path / "nowhere"))


async def test_opens_a_window_in_the_clone_and_starts_claude_with_the_instruction(tmp_path, monkeypatch):
    log = fake_tmux(tmp_path, monkeypatch)
    pane = await open_coordination("/coord", "autoflow-sandbox#4", "Workspace autoflow-sandbox, issue #4.")
    assert pane == "%7"
    assert log.read_text().splitlines() == [
        "new-window -c /coord -n autoflow-sandbox#4 -P -F #{pane_id}",
        "send-keys -t %7 -l -- claude 'Workspace autoflow-sandbox, issue #4.'",
        "send-keys -t %7 Enter",
    ]


async def test_not_inside_tmux(monkeypatch):
    monkeypatch.delenv("TMUX", raising=False)
    with pytest.raises(StartError, match="not inside tmux"):
        await open_coordination("/coord", "w", "text")


async def test_tmux_failure_carries_its_message(tmp_path, monkeypatch):
    fake_tmux(tmp_path, monkeypatch, fail_with="no server running on /tmp/tmux-501/default")
    with pytest.raises(StartError, match="no server running"):
        await open_coordination("/coord", "w", "text")


async def test_start_run(tmp_path, monkeypatch):
    base = tmp_path / "base"
    workspace(base, "autoflow-sandbox", "Munsik-Park/autoflow-sandbox")
    clone = coordinator_clone(tmp_path, base)
    log = fake_tmux(tmp_path, monkeypatch)
    assert await start_run(clone, "Munsik-Park/autoflow-sandbox", 4) == "%7"
    calls = log.read_text().splitlines()
    assert calls[0] == f"new-window -c {clone} -n autoflow-sandbox#4 -P -F #{{pane_id}}"
    assert calls[1].endswith("claude 'Workspace autoflow-sandbox, issue #4.'")
