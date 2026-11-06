import os
import stat
from pathlib import Path

import pytest

from autoflow_ide.windows import PANES_FORMAT, TmuxWindows, Window, WindowError, parse_panes

FIXTURES = Path(__file__).parent / "fixtures"


def test_coordination_windows_of_the_workspace_by_issue():
    windows = parse_panes((FIXTURES / "tmux-list-panes.txt").read_text(), "autoflow-sandbox")
    assert windows == {
        4: Window("@2", "autoflow-sandbox#4", ["%2", "%3"]),     # split by hand: both panes
        12: Window("@3", "autoflow-sandbox#12", ["%4"]),
        7: Window("@7", "autoflow-sandbox#7", ["%8"]),           # in another tmux session
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
