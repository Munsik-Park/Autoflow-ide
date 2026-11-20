"""The coordination sessions of one issue, made on each read from what is found: the panes this IDE opened for it,
its tmux windows and its active runs (#85).

A session is one coordination pane. The pane is the one id known at every stage: tmux gives it when the window is
opened, Claude Code runs in it, and the coordinator records it as the run's COORD_SURFACE, which status.py reports
as coordination.surface. A pane holding several active runs of the issue is one session per run.
"""
from dataclasses import dataclass
from typing import List, Optional

from autoflow_ide.model import Run
from autoflow_ide.windows import Window


@dataclass
class CoordinationSession:
    # The coordination pane; None for a run whose run.env records no surface.
    pane: Optional[str]
    run: Optional[Run] = None
    # The found window holding the pane, when one does.
    window: Optional[Window] = None


def window_order(window: Window) -> int:
    """tmux numbers window ids in creation order."""
    number = window.id.lstrip("@")
    return int(number) if number.isdigit() else -1


def sessions_of(windows: List[Window], runs: List[Run], opened: List[str]) -> List[CoordinationSession]:
    """The sessions of an issue from its windows, its active runs and the panes opened for it here, in order: by
    window, within a window by pane, the runs of one pane by name; then the runs and opened panes in no window.
    A window's coordination panes are those a run or an open names; a window that none names has its first pane,
    the one new-window made, as the coordinator splits work-session panes after it. A run with no surface goes to
    a window no run or open names."""
    runs = sorted(runs, key=lambda r: r.name)
    # A run whose run.env records no surface cannot be told apart from the session of a window no run names: it is
    # taken as that window's, one run to a window in order, and as a session of its own when no such window is left.
    unplaced = [r for r in runs if not r.coordination]
    sessions: List[CoordinationSession] = []
    placed = set()
    for window in sorted(windows, key=window_order):
        coordination = [p for p in window.panes if p in opened or any(r.coordination == p for r in runs)]
        if not coordination and unplaced:
            run = unplaced.pop(0)
            sessions.append(CoordinationSession(window.panes[0], run, window))
            placed.add(run.name)
            continue
        for pane in coordination or window.panes[:1]:
            mine = [r for r in runs if r.coordination == pane]
            sessions += [CoordinationSession(pane, r, window) for r in mine] or [CoordinationSession(pane, None, window)]
            placed.update(r.name for r in mine)
    sessions += [CoordinationSession(r.coordination, r) for r in runs if r.name not in placed]
    in_windows = {p for w in windows for p in w.panes}
    named = {r.coordination for r in runs}
    sessions += [CoordinationSession(p) for p in opened if p not in in_windows and p not in named]
    return sessions
