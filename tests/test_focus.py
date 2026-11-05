import os
import stat

import pytest

from autoflow_ide.focus import FocusError, focus_surface


def fake_tmux(tmp_path, monkeypatch, fail_with=None):
    log = tmp_path / "calls"
    script = f'#!/bin/sh\necho "$@" >> "{log}"\n'
    if fail_with:
        script += f'echo "{fail_with}" >&2\nexit 1\n'
    tmux = tmp_path / "tmux"
    tmux.write_text(script)
    tmux.chmod(tmux.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("TMUX", "/tmp/tmux-test,1,0")
    return log


async def test_selects_the_window_then_the_pane(tmp_path, monkeypatch):
    log = fake_tmux(tmp_path, monkeypatch)
    await focus_surface("%2")
    assert log.read_text().splitlines() == ["select-window -t %2", "select-pane -t %2"]


async def test_tmux_error_carries_its_message(tmp_path, monkeypatch):
    fake_tmux(tmp_path, monkeypatch, fail_with="can't find pane: %999")
    with pytest.raises(FocusError, match="can't find pane: %999"):
        await focus_surface("%999")


async def test_outside_tmux(monkeypatch):
    monkeypatch.delenv("TMUX", raising=False)
    with pytest.raises(FocusError, match="not inside tmux"):
        await focus_surface("%1")


async def test_not_a_pane_id(monkeypatch):
    monkeypatch.setenv("TMUX", "x")
    with pytest.raises(FocusError, match="not a tmux pane"):
        await focus_surface("surface:3")
