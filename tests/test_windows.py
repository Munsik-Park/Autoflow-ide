import os
import stat
from pathlib import Path

import pytest

from autoflow_ide.windows import PANES_FORMAT, TmuxWindows, Window, WindowError, parse_panes

FIXTURES = Path(__file__).parent / "fixtures"


def test_coordination_windows_of_the_workspace_by_issue():
    windows = parse_panes((FIXTURES / "tmux-list-panes.txt").read_text(), "autoflow-sandbox")
    assert windows == {
        4: [Window("@2", "autoflow-sandbox#4", ["%2", "%3"])],     # split by hand: both panes
        12: [Window("@3", "autoflow-sandbox#12", ["%4"])],
        7: [Window("@7", "autoflow-sandbox#7", ["%8"])],           # in another tmux session
    }
    # Not: the IDE's window, an auto-named one, another workspace's, a name with more after the number or
    # more before the workspace.


def fake_tmux(tmp_path, monkeypatch, stdout="", fail_with=None):
    """A tmux on PATH that records its arguments and prints `stdout`."""
    log = tmp_path / "calls"
    out = tmp_path / "out"
    out.write_text(stdout)
    script = f'#!/bin/sh\necho "$@" >> "{log}"\n'
    if fail_with:
        script += f'echo "{fail_with}" >&2\nexit 1\n'
    script += f'cat "{out}"\n'
    tmux = tmp_path / "tmux"
    tmux.write_text(script)
    tmux.chmod(tmux.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("TMUX", "/tmp/tmux-test,1,0")
    return log


async def test_find_lists_every_pane_with_the_format(tmp_path, monkeypatch):
    log = fake_tmux(tmp_path, monkeypatch, (FIXTURES / "tmux-list-panes.txt").read_text())
    windows = await TmuxWindows().find("autoflow-sandbox")
    assert sorted(windows) == [4, 7, 12]
    assert log.read_text().splitlines() == [f"list-panes -a -F {PANES_FORMAT}"]


async def test_no_windows_outside_tmux(monkeypatch):
    monkeypatch.delenv("TMUX", raising=False)
    assert await TmuxWindows().find("autoflow-sandbox") == {}


async def test_screen_captures_each_pane_and_close_kills_the_window(tmp_path, monkeypatch):
    log = fake_tmux(tmp_path, monkeypatch, "> \n")
    window = Window("@2", "autoflow-sandbox#4", ["%2", "%3"])
    assert await TmuxWindows().screen(window) == "> \n\n> \n"
    await TmuxWindows().close(window)
    assert log.read_text().splitlines() == ["capture-pane -p -t %2", "capture-pane -p -t %3", "kill-window -t @2"]


async def test_screens_capture_each_pane_once_in_order(tmp_path, monkeypatch):
    # A prompt is judged per pane (#79); the joined screen, for the idle wait, is made from the same captures.
    log = fake_tmux(tmp_path, monkeypatch, "> \n")
    window = Window("@2", "autoflow-sandbox#4", ["%2", "%3"])
    assert await TmuxWindows().screens(window) == ["> \n", "> \n"]
    assert log.read_text().splitlines() == ["capture-pane -p -t %2", "capture-pane -p -t %3"]


async def test_tmux_error_carries_its_message(tmp_path, monkeypatch):
    # As tmux 3.4 reports a window that is gone (exit status 1).
    fake_tmux(tmp_path, monkeypatch, fail_with="can't find window: @99")
    with pytest.raises(WindowError, match="can't find window: @99"):
        await TmuxWindows().close(Window("@99", "autoflow-sandbox#4", ["%99"]))


def test_places_of_the_panes():
    from autoflow_ide.windows import parse_places
    assert parse_places((FIXTURES / "tmux-places.txt").read_text()) == {
        "%0": "1:0 ide", "%5": "1:1 tmux", "%1": "2:0 bash",
        "%2": "2:1 autoflow-coordinator#59", "%3": "2:1 autoflow-coordinator#59",   # one window, two panes
        "%4": "2:2 my window",                                                      # a name with a space
    }


async def test_no_places_outside_tmux(monkeypatch):
    from autoflow_ide.windows import pane_places
    monkeypatch.delenv("TMUX", raising=False)
    assert await pane_places() == {}


# Several coordination sessions of one issue (#85)

from autoflow_ide.runs import parse_status

def test_every_window_of_an_issue_with_its_own_panes_in_listed_order():
    # Two windows named autoflow-sandbox#6, the second split by split-window without -b as the coordinator's
    # tools/terminals/tmux.py splits (tests/fixtures/README.md): both are found, not only the first (#85).
    windows = parse_panes((FIXTURES / "tmux-list-panes-sessions.txt").read_text(), "autoflow-sandbox")
    assert windows == {
        6: [Window("@1", "autoflow-sandbox#6", ["%1"]), Window("@2", "autoflow-sandbox#6", ["%2", "%3"])],
        8: [Window("@3", "autoflow-sandbox#8", ["%4"])],
    }


A = "autoflow-sandbox-6-2026-11-20-220000-aaaaaaaa"
B = "autoflow-sandbox-6-2026-11-20-221000-bbbbbbbb"
C = "autoflow-sandbox-6-2026-11-20-222000-cccccccc"


@pytest.mark.parametrize("status, order", [
    ("status-sessions.json", [C, B, A]),
    ("status-sessions-reordered.json", [B, C, A]),     # a file added to B's run directory moves it first
])
def test_each_run_is_the_session_of_the_pane_its_coordination_surface_names(status, order):
    # Matched by coordination.surface, not by status.py's order: the two runs of the first window's pane are
    # two sessions of that pane, in run-name order, then the second window's (#85).
    from autoflow_ide.sessions import sessions_of      # imported here: the rest of the file runs without it
    windows = parse_panes((FIXTURES / "tmux-list-panes-sessions.txt").read_text(), "autoflow-sandbox")
    runs = parse_status((FIXTURES / status).read_text())
    assert [r.name for r in runs] == order
    sessions = sessions_of(windows[6], [r for r in runs if r.issue == "6"], [])
    assert [(s.pane, s.run.name) for s in sessions] == [("%1", A), ("%1", B), ("%2", C)]
    # A window holding no run's pane is one session, by its first pane.
    assert [(s.pane, s.run) for s in sessions_of(windows[8], [], [])] == [("%4", None)]


def test_a_window_left_with_only_the_work_session_pane_of_a_run_is_no_session_of_its_own():
    # A run's coordination pane %1 was killed while the run stays active; its window keeps only the work-session
    # pane %2 that status.py gives as the run's session S1. That pane is the run's, not a second session: the run is
    # the issue's one session, by the pane its run.env names (#85).
    from autoflow_ide.sessions import sessions_of
    windows = parse_panes((FIXTURES / "tmux-list-panes-lost-coordination.txt").read_text(), "autoflow-sandbox")
    runs = parse_status((FIXTURES / "status-lost-coordination.json").read_text())
    assert windows == {6: [Window("@1", "autoflow-sandbox#6", ["%2"])]}
    assert [s.surface for s in runs[0].sessions] == ["%2"]
    sessions = sessions_of(windows[6], runs, [])
    assert [(s.pane, s.run.name, s.window) for s in sessions] == [
        ("%1", "autoflow-sandbox-6-2026-11-21-090000-dddddddd", None)]
    # A pane that is no run's work session, as a split by hand, is still the window's session.
    hand = [Window("@1", "autoflow-sandbox#6", ["%2", "%7"])]
    assert [(s.pane, s.run and s.run.name) for s in sessions_of(hand, runs, [])] == [
        ("%7", None), ("%1", "autoflow-sandbox-6-2026-11-21-090000-dddddddd")]
