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


# Browser control (#86): the view's reads of a pane, the session id kept on a pane, and the input sent to one.
# Imported in each test: the rest of the file runs without them. (#86)

SHAPE = "#{cursor_x} #{cursor_y} #{cursor_flag} #{pane_width} #{pane_height} #{alternate_on} #{pane_in_mode}"


def dispatching_tmux(tmp_path, monkeypatch, display="", captures=(), fail_with=None):
    """A tmux on PATH that records its arguments; display-message prints `display`, each capture-pane prints the
    next of `captures` (the last one again after them), load-buffer keeps its stdin in `loaded` (#86)."""
    log = tmp_path / "calls"
    (tmp_path / "display").write_text(display)
    for n, text in enumerate(captures, 1):
        (tmp_path / f"capture-{n}").write_text(text)
    last = len(captures)
    script = f'#!/bin/sh\necho "$@" >> "{log}"\n'
    if fail_with:
        script += f'echo "{fail_with}" >&2\nexit 1\n'
    script += (f'cd "{tmp_path}"\n'
               'case "$1" in\n'
               '  display-message) cat display;;\n'
               '  capture-pane) n=$(cat count 2>/dev/null || echo 0); n=$((n+1)); echo $n > count;\n'
               f'    if [ $n -gt {last} ]; then n={last}; fi; cat capture-$n;;\n'
               '  load-buffer) cat > loaded;;\n'
               'esac\n')
    tmux = tmp_path / "tmux"
    tmux.write_text(script)
    tmux.chmod(tmux.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("TMUX", "/tmp/tmux-test,1,0")
    return log


def test_the_frame_of_a_recorded_pane_has_its_rows_cursor_and_size():
    # tmux-pane-shape.txt and tmux-capture-e.txt: one pane, in the alternate screen, with colours (#86).
    from autoflow_ide.windows import SHAPE_FORMAT, parse_frame
    assert SHAPE_FORMAT == SHAPE
    frame = parse_frame((FIXTURES / "tmux-pane-shape.txt").read_text(), (FIXTURES / "tmux-capture-e.txt").read_text())
    assert frame.rows == ["\x1b[32mGREEN\x1b[39m plain", "\x1b[1m\x1b[31mBOLD RED\x1b[0m", ">", "", "", "", "", ""]
    assert (frame.width, frame.height) == (40, 8)
    assert frame.cursor == (2, 2) and frame.cursor_visible
    assert frame.alternate and not frame.in_mode


async def test_the_views_read_runs_only_display_message_and_capture_pane(tmp_path, monkeypatch):
    # No attach, -C, refresh-client, resize-window or pipe-pane: nothing tmux counts as a client or a pane's pipe, (#86)
    # so no window's size changes (#86).
    from autoflow_ide.windows import SHAPE_FORMAT
    log = dispatching_tmux(tmp_path, monkeypatch, (FIXTURES / "tmux-pane-shape.txt").read_text(),
                           [(FIXTURES / "tmux-capture-e.txt").read_text()])
    frame = await TmuxWindows().frame("%7")
    calls = log.read_text().splitlines()
    assert sorted(calls) == sorted([f"display-message -p -t %7 {SHAPE_FORMAT}", "capture-pane -p -e -t %7"])
    assert (frame.width, frame.height, frame.cursor) == (40, 8, (2, 2))


async def test_the_frame_of_a_gone_pane_fails_with_tmuxs_message(tmp_path, monkeypatch):
    dispatching_tmux(tmp_path, monkeypatch, fail_with="can't find pane: %99")
    with pytest.raises(WindowError, match="can't find pane: %99"):
        await TmuxWindows().frame("%99")


def test_a_frame_keeps_only_text_and_colours():
    # tmux-capture-osc8.txt: capture-pane -e passes an OSC 8 link through; a frame gives xterm.js no link to open, (#86)
    # and no other escape than SGR (#86).
    import re
    from autoflow_ide.windows import parse_frame
    frame = parse_frame((FIXTURES / "tmux-pane-shape.txt").read_text(),
                        (FIXTURES / "tmux-capture-osc8.txt").read_text())
    assert frame.rows[0] == "LINK \x1b[31mRED\x1b[39m"
    text = "\n".join(frame.rows)
    assert "\x1b]" not in text and "\x1bP" not in text and "https://example.com/" not in text
    plain = re.sub(r"\x1b\[[0-9;:]*m", "", text)
    assert "\x1b" not in plain and all(c.isprintable() or c == "\n" for c in plain)


@pytest.mark.parametrize("row, kept", [
    ("a\x1b[2Jb", "ab"),                          # another CSI (#86)
    ("a\x1b[?25lb", "ab"),                        # a private CSI (#86)
    ("a\x1b]2;TITLE\x07b", "ab"),                 # OSC ended by BEL (#86)
    ("a\x1bPq#0;1;1\x1b\\b", "ab"),               # DCS (#86)
    ("a\x07b\x08c\x0bd", "abcd"),                 # C0 controls (#86)
    ("\x1b[38;5;196mX\x1b[38:2::1:2:3mY\x1b[0m", "\x1b[38;5;196mX\x1b[38:2::1:2:3mY\x1b[0m"),   # SGR kept (#86)
])
def test_a_frame_drops_every_escape_but_sgr(row, kept):
    from autoflow_ide.windows import parse_frame
    frame = parse_frame("0 0 1 10 1 0 0\n", row + "\n")
    assert frame.rows == [kept]


async def test_the_session_id_is_read_from_the_panes_option(tmp_path, monkeypatch):
    log = dispatching_tmux(tmp_path, monkeypatch, "00000000-0000-4000-8000-0000000000aa\n")
    assert await TmuxWindows().session_id("%7") == "00000000-0000-4000-8000-0000000000aa"
    assert log.read_text().splitlines() == ["display-message -p -t %7 #{@autoflow-session-id}"]


NBSP = "\xa0"


def with_draft(screen, text):
    return screen.replace(f"\n❯{NBSP}\n", f"\n❯{NBSP}{text}\n")


async def test_a_message_is_pasted_checked_and_then_submitted(tmp_path, monkeypatch):
    # As the coordinator's submit enters it: load-buffer and paste-buffer -r, not send-keys -l (each line would be (#86)
    # submitted) nor -p (pasted material); Enter only once the input area shows the text (#86).
    import re
    from autoflow_ide.inputs import enter_message
    empty = (FIXTURES / "screens" / "turn-end.txt").read_text()
    log = dispatching_tmux(tmp_path, monkeypatch, "0\n", [empty, with_draft(empty, "look at the second step")])
    await enter_message(TmuxWindows(), "%7", "look at the second step")
    calls = log.read_text().splitlines()
    assert calls[:2] == ["display-message -p -t %7 #{pane_in_mode}", "capture-pane -p -t %7"]
    load = re.fullmatch(r"load-buffer -b (\S+) -", calls[2])
    assert load, calls
    assert calls[3:] == [f"paste-buffer -r -d -b {load[1]} -t %7", "capture-pane -p -t %7", "send-keys -t %7 Enter"]
    assert (tmp_path / "loaded").read_text() == "look at the second step"


async def test_each_paste_has_a_buffer_of_its_own(tmp_path, monkeypatch):
    # Two pastes to one pane at once must not load into the same buffer (#86).
    import re
    log = dispatching_tmux(tmp_path, monkeypatch)
    await TmuxWindows().paste("%7", "alpha")
    await TmuxWindows().paste("%7", "beta")
    loads = [re.fullmatch(r"load-buffer -b (\S+) -", c) for c in log.read_text().splitlines() if c.startswith("load")]
    assert len(loads) == 2 and all(loads) and loads[0][1] != loads[1][1]


async def test_no_enter_when_the_input_area_does_not_show_the_text(tmp_path, monkeypatch):
    from autoflow_ide.inputs import InputRefused, enter_message
    empty = (FIXTURES / "screens" / "turn-end.txt").read_text()
    log = dispatching_tmux(tmp_path, monkeypatch, "0\n", [empty, empty])
    with pytest.raises(InputRefused, match="the input area does not show the text; Enter not sent"):
        await enter_message(TmuxWindows(), "%7", "look at the second step")
    assert "send-keys -t %7 Enter" not in log.read_text().splitlines()


@pytest.mark.parametrize("display, screen, text, reason", [
    ("1\n", "turn-end.txt", "hi", "%7 is in copy mode: leave it (q) and retry"),
    ("0\n", "turn-end.txt", "x" * 701, "701 bytes is over 700: put it in a file"),
    ("0\n", "permission-prompt.txt", "hi", "no input area on the screen"),
    ("0\n", "choice-prompt.txt", "hi", "the input area is not empty"),       # a dialog holds it (#86)
    ("0\n", "draft", "hi", "the input area is not empty"),
])
async def test_a_message_the_pane_would_not_take_enters_nothing(tmp_path, monkeypatch, display, screen, text, reason):
    from autoflow_ide.inputs import InputRefused, enter_message
    turn_end = (FIXTURES / "screens" / "turn-end.txt").read_text()
    shown = with_draft(turn_end, "my draft") if screen == "draft" else (FIXTURES / "screens" / screen).read_text()
    log = dispatching_tmux(tmp_path, monkeypatch, display, [shown])
    with pytest.raises(InputRefused) as e:
        await enter_message(TmuxWindows(), "%7", text)
    assert reason in str(e.value)
    assert not any(c.split()[0] in ("load-buffer", "paste-buffer", "send-keys") for c in log.read_text().splitlines())


PROMPT_KEYS = [str(n) for n in range(10)] + ["Up", "Down", "Left", "Right", "Enter", "Escape"]


@pytest.mark.parametrize("key", PROMPT_KEYS)
async def test_a_prompt_key_is_sent_by_its_tmux_name(tmp_path, monkeypatch, key):
    from autoflow_ide.inputs import answer_prompt
    log = dispatching_tmux(tmp_path, monkeypatch, "0\n", [(FIXTURES / "screens" / "choice-prompt.txt").read_text()])
    await answer_prompt(TmuxWindows(), "%7", key)
    assert log.read_text().splitlines() == [
        "display-message -p -t %7 #{pane_in_mode}", "capture-pane -p -t %7", f"send-keys -t %7 {key}"]


@pytest.mark.parametrize("key", ["a", "q", "C-c", "Tab", "BSpace", "10", "1 2", "Enter;", "-l", ""])
async def test_no_other_key_is_sent(tmp_path, monkeypatch, key):
    from autoflow_ide.inputs import InputRefused, answer_prompt
    log = dispatching_tmux(tmp_path, monkeypatch, "0\n", [(FIXTURES / "screens" / "choice-prompt.txt").read_text()])
    with pytest.raises(InputRefused):
        await answer_prompt(TmuxWindows(), "%7", key)
    assert not log.exists() or not any(c.startswith("send-keys") for c in log.read_text().splitlines())


@pytest.mark.parametrize("display, screen", [("1\n", "choice-prompt.txt"), ("0\n", "turn-end.txt")])
async def test_no_prompt_key_in_copy_mode_or_without_a_prompt(tmp_path, monkeypatch, display, screen):
    from autoflow_ide.inputs import InputRefused, answer_prompt
    log = dispatching_tmux(tmp_path, monkeypatch, display, [(FIXTURES / "screens" / screen).read_text()])
    with pytest.raises(InputRefused):
        await answer_prompt(TmuxWindows(), "%7", "1")
    assert not any(c.startswith("send-keys") for c in log.read_text().splitlines())
