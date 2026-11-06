"""The IDE screen: issues, the selected issue's run, and its sessions."""
import argparse
import time
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from rich.cells import cell_len
from rich.text import Text
from textual.widgets import DataTable, Footer, Static

from autoflow_ide.detail import StepDetail, home_short
from autoflow_ide.focus import FocusError, focus_surface
from autoflow_ide.issues import IssueSourceError, gh_issue, gh_issues, repository_from_workspace
from autoflow_ide.model import Issue, Run, placeholder_issues
from autoflow_ide.runs import RunSourceError, coordinator_runs, version_notice
from autoflow_ide.start import StartError, Workspace, coordinator_workspace, coordinator_workspace_entry, start_run
from autoflow_ide.tables import ColumnSpec, fill
from autoflow_ide.windows import TmuxWindows, WindowError, pane_places

IssueLoader = Callable[[str], Awaitable[List[Issue]]]
WorkspaceFinder = Callable[[str, str], Awaitable[str]]
IssueLookup = Callable[[str, int], Awaitable[Issue]]
RunLoader = Callable[[str], Awaitable[List[Run]]]
Focuser = Callable[[str], Awaitable[None]]
Starter = Callable[[str, str, int], Awaitable[str]]
WorkspaceReader = Callable[[str, str], Awaitable[Workspace]]
Placer = Callable[[], Awaitable[Dict[str, str]]]
RUN_REFRESH_SEC = 5.0
# The open issues are also read again on this interval, so an issue a merge closed leaves the list without a
# key press (about 60 gh calls an hour), and when a step changes (see show_runs).
ISSUE_REFRESH_SEC = 60.0
# The coordination session window of an issue that is closed and has no run is closed once its screen has not
# changed for WINDOW_IDLE_SEC, so a last report being written is not cut off and the operator can come back after
# a merge to go on with the session, whose screen stands still while it waits for input; checked every
# WINDOW_CHECK_SEC.
WINDOW_CHECK_SEC = 15.0
WINDOW_IDLE_SEC = 300.0
RUN_MARK = "●"
ISSUE_COLUMNS: List[ColumnSpec] = [("", "run", 1), ("#", "number", 4), ("Title", "title", None)]
RUN_COLUMNS: List[ColumnSpec] = [("Step", "step", 4), ("Module run", "name", None), ("Session", "session", 7),
                                 ("State", "state", 8), ("Verdict", "verdict", 16)]
SESSION_COLUMNS: List[ColumnSpec] = [("Session", "name", 7), ("State", "state", None)]


class IdeApp(App):
    CSS_PATH = "app.tcss"
    TITLE = "autoflow-ide"
    BINDINGS = [
        ("1", "focus_pane('issues')", "Issues"),
        ("2", "focus_pane('run')", "Run"),
        ("3", "focus_pane('sessions')", "Sessions"),
        ("r", "refresh_issues", "Refresh"),
        ("s", "start_run", "Start run"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, issues: Optional[List[Issue]] = None, repository: Optional[str] = None,
                 loader: Optional[IssueLoader] = None, coordinator: Optional[str] = None,
                 run_loader: Optional[RunLoader] = None, focuser: Optional[Focuser] = None,
                 run_refresh_sec: float = RUN_REFRESH_SEC, lookup: Optional[IssueLookup] = None,
                 starter: Optional[Starter] = None, issue_refresh_sec: float = ISSUE_REFRESH_SEC,
                 windows=None, workspace_finder: Optional[WorkspaceFinder] = None,
                 window_check_sec: float = WINDOW_CHECK_SEC, window_idle_sec: float = WINDOW_IDLE_SEC,
                 workspace_reader: Optional[WorkspaceReader] = None, placer: Optional[Placer] = None):
        """With `issues`, show them as given. Otherwise load the open issues of `repository` with `loader`,
        again every `issue_refresh_sec` seconds.
        With `coordinator` (a coordinator clone), read its active runs every `run_refresh_sec` seconds.
        An issue that has a run but is not in the open list is looked up with `lookup`.
        `starter` opens the coordination session for an issue (see start.py); `windows` finds and closes those
        sessions' windows (see windows.py) in the workspace `workspace_finder` names.
        `workspace_reader` reads that workspace's repo.json and `placer` where each tmux pane is, both shown
        above the run pane."""
        super().__init__()
        self.open_issues: List[Issue] = list(issues) if issues is not None else []
        self.issues: List[Issue] = list(self.open_issues)
        self.static = issues is not None
        # Until the open issues have been read, an issue with a run cannot be told to be missing from them.
        self.listed = self.static
        self.lookup = lookup or gh_issue
        # Issues missing from the open list, by number: the looked-up issue, or None while looking or after a
        # failure (looked up again on the next refresh of the issues).
        self.lookups: Dict[int, Optional[Issue]] = {}
        # Titles of the issues seen in the open list, shown for an issue that left it until it is looked up.
        self.titles: Dict[int, str] = {}
        self.repository = repository
        self.loader = loader or gh_issues
        self.coordinator = coordinator
        self.run_loader = run_loader or coordinator_runs
        self.focuser = focuser or focus_surface
        self.starter = starter or start_run
        # Coordination sessions opened from this IDE, by issue: their pane, until the issue has a run.
        self.started: Dict[int, str] = {}
        self.run_refresh_sec = run_refresh_sec
        self.issue_refresh_sec = issue_refresh_sec
        self.runs: List[Run] = []
        # Whether the last read of the runs succeeded: without it, an issue cannot be told to have no run.
        self.runs_read = False
        self.windows = windows or TmuxWindows()
        self.workspace_finder = workspace_finder or coordinator_workspace
        self.window_check_sec = window_check_sec
        self.window_idle_sec = window_idle_sec
        self.workspace: Optional[str] = None
        self.workspace_reader = workspace_reader or coordinator_workspace_entry
        # The workspace of the repository, or why it could not be read; None until read.
        self.workspace_entry: Optional[Workspace] = None
        self.workspace_error: Optional[str] = None
        self.placer = placer or pane_places
        # Where each tmux pane is ("2:1 autoflow-coordinator#59"), by pane id, as last read.
        self.places: Dict[str, str] = {}
        # The coordination session windows, by issue: (window id, its screen, when that screen was first seen).
        self.screens: Dict[int, Tuple[str, str, float]] = {}
        # When an issue was last looked up and found open, so it is not looked up on every check.
        self.found_open: Dict[int, float] = {}
        # The steps of each shown run, by run name, as last read: what a step change is compared against.
        self.step_marks: Dict[str, List[Tuple[str, str, Optional[str]]]] = {}
        self.current: Optional[Issue] = None
        self.messages: Dict[str, str] = {}

    def compose(self) -> ComposeResult:
        with Horizontal(id="panes"):
            with Vertical(id="issues-pane", classes="pane") as pane:
                pane.border_title = "Issues"
                yield DataTable(id="issues", cursor_type="row")
            with Vertical(id="run-pane", classes="pane") as pane:
                pane.border_title = "Run"
                yield Static(id="info")
                yield DataTable(id="run", cursor_type="row")
            with Vertical(id="sessions-pane", classes="pane") as pane:
                pane.border_title = "Sessions"
                yield DataTable(id="sessions", cursor_type="row")
        yield Static(id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#issues", DataTable).focus()
        self.set_issues(self.issues)
        if not self.static:
            self.action_refresh_issues()
            self.set_interval(self.issue_refresh_sec, self.action_refresh_issues)
        if self.coordinator:
            self.set_message("version", version_notice(self.coordinator))
            if self.repository and not self.static:
                self.run_worker(self.load_workspace(), group="workspace")
            self.refresh_runs()
            self.set_interval(self.run_refresh_sec, self.refresh_runs)
            if not self.static and self.repository:
                self.set_interval(self.window_check_sec, self.check_windows)
        self.call_after_refresh(self.render_all)

    def on_resize(self) -> None:
        self.call_after_refresh(self.render_all)

    def render_all(self) -> None:
        self.render_issues()
        self.show_issue(self.current)

    # Issues

    def set_issues(self, issues: List[Issue]) -> None:
        """Show the open `issues`, with the runs attached and the issues of runs that are not among them."""
        self.open_issues = issues
        self.titles.update((i.number, i.title) for i in issues)
        self.attach_runs()
        self.render_issues()
        self.show_issue(self.current_in(issues))

    def current_in(self, issues: List[Issue]) -> Optional[Issue]:
        """The highlighted issue if it is still listed, otherwise the first one in display order."""
        number = self.current.number if self.current else None
        ordered = self.ordered()
        return next((i for i in ordered if i.number == number), ordered[0] if ordered else None)

    def ordered(self) -> List[Issue]:
        """Issues with a run first, each group in the order the source gave."""
        return sorted(self.issues, key=lambda i: i.run is None)

    def render_issues(self) -> None:
        table = self.query_one("#issues", DataTable)
        ordered = self.ordered()
        fill(table, ISSUE_COLUMNS,
             ((str(i.number), [RUN_MARK if i.run else "", str(i.number), issue_title(i)]) for i in ordered))
        current = self.current_in(ordered)
        if current:
            table.move_cursor(row=table.get_row_index(str(current.number)))

    def action_refresh_issues(self) -> None:
        if self.static:
            return
        if not self.repository:
            self.set_message("issues", "no repository: start with --repo owner/name or --workspace <dir>")
            return
        self.set_message("issues", "loading issues")
        self.lookups = {n: i for n, i in self.lookups.items() if i is not None}
        self.run_worker(self.load_issues(), group="issues", exclusive=True)

    async def load_issues(self) -> None:
        try:
            issues = await self.loader(self.repository)
        except IssueSourceError as e:
            issues, message = None, f"issues: {e}"
        else:
            message = f"{len(issues)} open issues"
        try:
            if issues is not None:
                self.listed = True
                self.set_issues(issues)
            self.set_message("issues", message)
        except NoMatches:
            pass  # the app is closing: its widgets are gone

    # Runs

    def refresh_runs(self) -> None:
        self.run_worker(self.load_runs(), group="runs", exclusive=True)

    async def load_runs(self) -> None:
        try:
            runs = await self.run_loader(self.coordinator)
        except RunSourceError as e:
            runs, error = None, f"runs: {e}"
        self.runs_read = runs is not None
        try:
            if runs is None:
                self.set_message("runs", error)
            else:
                self.show_runs(runs)
        except NoMatches:
            pass  # the app is closing: its widgets are gone
        if runs is not None and any(r.coordination for r in self.own_runs()):
            await self.load_places()

    async def load_places(self) -> None:
        """Where the panes are, for the coordination session of the shown run; kept as last read on a failure."""
        try:
            self.places = await self.placer()
        except WindowError:
            return
        try:
            self.render_info()
        except NoMatches:
            pass  # the app is closing: its widgets are gone

    # The workspace

    async def load_workspace(self) -> None:
        try:
            self.workspace_entry = await self.workspace_reader(self.coordinator, self.repository)
        except StartError as e:
            self.workspace_error = str(e)
        try:
            self.render_info()
        except NoMatches:
            pass  # the app is closing: its widgets are gone

    def show_runs(self, runs: List[Run]) -> None:
        before = {i.number for i in self.issues if i.run}
        self.runs = runs
        self.attach_runs()
        after = {i.number for i in self.issues if i.run}
        started = after - before
        if any(self.started.pop(n, None) for n in after):
            self.set_message("start", "")  # the coordination session prepared the run
        if started and (self.current is None or self.current.run is None):
            # The operator is not looking at a run: move to the issue whose run just started.
            self.current = next(i for i in self.ordered() if i.number in started)
        self.render_issues()
        self.show_issue(self.current_in(self.issues))
        own = len(self.own_runs())
        others = len(runs) - own
        self.set_message("runs", f"{own} active runs" + (f" ({others} in other repositories)" if others else ""))
        if before - after or self.steps_changed():
            # A run ended, or a step of a run changed: a merge may have closed an issue in the meantime.
            self.action_refresh_issues()

    def steps_changed(self) -> bool:
        """Whether a step of a run already shown changed its state or verdict, or a step was added, since the
        last read; records the steps for the next one. Not the session state and idle time of a running step,
        which change on every read. A run seen for the first time is not a change."""
        marks = {r.name: [(s.label, s.state, s.verdict) for s in r.steps] for r in self.own_runs()}
        changed = any(name in self.step_marks and self.step_marks[name] != steps for name, steps in marks.items())
        self.step_marks = marks
        return changed

    def own_runs(self) -> List[Run]:
        """The runs of this repository. status.py lists the active runs of every repository; the coordinator
        names a run <repository name>-<issue>-<key>."""
        if not self.repository:
            return []
        name = self.repository.split("/")[-1]
        return [r for r in self.runs if r.issue and r.name.startswith(f"{name}-{r.issue}-")]

    def attach_runs(self) -> None:
        """Each issue gets the first of its runs; status.py lists the most recently changed run first.
        A run stays active after its issue closes (a merge closes it) until the coordination session closes
        the run, so a run whose issue is not in the open list adds a row for that issue after the open ones."""
        issues = list(self.open_issues)
        if self.coordinator:
            runs = self.own_runs()
            for issue in issues:
                issue.run = next((r for r in runs if r.issue == str(issue.number)), None)
            if self.listed:
                shown = {i.number for i in issues}
                for run in runs:
                    if not run.issue.isdigit() or int(run.issue) in shown:
                        continue
                    number = int(run.issue)
                    shown.add(number)
                    issues.append(self.missing_issue(number, run))
        self.issues = issues

    def missing_issue(self, number: int, run: Run) -> Issue:
        """The row for issue `number`, which has `run` but is not in the open list."""
        found = self.lookups.get(number)
        if found is not None:
            return Issue(number, found.title, run, found.state)
        self.look_up(number)
        return Issue(number, self.titles.get(number, ""), run, None)

    def look_up(self, number: int) -> None:
        if number in self.lookups or self.static or not self.repository:
            return
        self.lookups[number] = None
        self.run_worker(self.load_issue(number), group=f"issue-{number}")

    async def load_issue(self, number: int) -> None:
        try:
            issue = await self.lookup(self.repository, number)
        except IssueSourceError as e:
            issue, message = None, f"issue #{number}: {e}"
        else:
            message = ""
        try:
            if issue is not None:
                self.lookups[number] = issue
                self.set_issues(self.open_issues)
            self.set_message("lookup", message)
        except NoMatches:
            pass  # the app is closing: its widgets are gone

    # Starting a run

    def action_start_run(self) -> None:
        issue = self.current
        if issue is None:
            return
        if self.static or not self.coordinator or not self.repository:
            self.set_message("start", "start: needs --coordinator and --repo or --workspace")
        elif issue.closed:
            self.set_message("start", f"#{issue.number} is closed")
        elif issue.run:
            self.set_message("start", f"#{issue.number} already has a run: {issue.run.name}")
        elif issue.number in self.started:
            # Opened from here, its run not prepared yet: go back to that coordination session.
            self.run_worker(self.focus_started(issue.number), group="start", exclusive=True)
        else:
            self.run_worker(self.start(issue.number), group="start", exclusive=True)

    async def start(self, number: int) -> None:
        self.set_message("start", f"#{number}: opening the coordination session")
        try:
            pane = await self.starter(self.coordinator, self.repository, number)
        except StartError as e:
            self.set_message("start", f"start #{number}: {e}")
            return
        self.started[number] = pane
        self.set_message("start", f"#{number}: coordination session in {pane}")

    async def focus_started(self, number: int) -> None:
        pane = self.started[number]
        try:
            await self.focuser(pane)
        except FocusError as e:
            del self.started[number]
            self.set_message("start", f"#{number}: {pane}: {e}; press s to open it again")
            return
        self.set_message("start", f"#{number}: coordination session in {pane}")

    # Closing the coordination session of a finished issue

    def check_windows(self) -> None:
        self.run_worker(self.check_windows_once(), group="windows", exclusive=True)

    async def check_windows_once(self) -> None:
        try:
            await self.close_finished_windows()
        except NoMatches:
            pass  # the app is closing: its widgets are gone

    async def close_finished_windows(self) -> None:
        """Close the coordination session window of each issue that is closed and has no run, once its screen
        has not changed for window_idle_sec. One issue can take several runs, so an open issue keeps its
        window after a run ends; a merge closes the issue before the run ends, so a run keeps it too."""
        try:
            if self.workspace is None:
                self.workspace = await self.workspace_finder(self.coordinator, self.repository)
            windows = await self.windows.find(self.workspace)
        except (StartError, WindowError) as e:
            self.set_message("windows", f"windows: {e}")
            return
        now = time.monotonic()
        self.screens = {n: s for n, s in self.screens.items() if n in windows}
        for number, window in windows.items():
            try:
                screen = await self.windows.screen(window)
            except WindowError as e:
                self.set_message("windows", f"{window.name}: {e}")
                continue
            seen = self.screens.get(number)
            if seen is None or seen[0] != window.id or seen[1] != screen:
                self.screens[number] = seen = (window.id, screen, now)
            if now - seen[2] < self.window_idle_sec or not await self.finished(number, now):
                continue
            try:
                await self.windows.close(window)
            except WindowError as e:
                self.set_message("windows", f"{window.name}: {e}")
                continue
            del self.screens[number]
            self.started.pop(number, None)
            self.set_message("windows", f"closed {window.name}: #{number} is closed and has no run")

    async def finished(self, number: int, now: float) -> bool:
        """Whether issue `number` is closed and has no active run. Closed is asked of GitHub, not taken from
        the issue missing from the open list; an issue found open is asked again after window_idle_sec."""
        if not (self.listed and self.runs_read):
            return False
        if any(r.issue == str(number) for r in self.own_runs()) or any(i.number == number for i in self.open_issues):
            return False
        found_open = self.found_open.get(number)
        if found_open is not None and now - found_open < self.window_idle_sec:
            return False
        try:
            issue = await self.lookup(self.repository, number)
        except IssueSourceError as e:
            self.set_message("windows", f"issue #{number}: {e}")
            return False
        if not issue.closed:
            self.found_open[number] = now
        return issue.closed

    # Selection

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id != "issues" or event.row_key.value is None:
            return
        number = int(event.row_key.value)
        self.show_issue(next((i for i in self.issues if i.number == number), None))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "sessions" and event.row_key.value is not None:
            self.run_worker(self.focus_session(event.row_key.value), group="focus", exclusive=True)
        elif event.data_table.id == "run":
            steps = self.current.run.steps if self.current and self.current.run else []
            if 0 <= event.cursor_row < len(steps):
                self.push_screen(StepDetail(steps[event.cursor_row], event.cursor_row + 1), self.go_to_step)

    def go_to_step(self, label: Optional[str]) -> None:
        """Highlight the first step named `label` in the run pane (the detail's cause)."""
        steps = self.current.run.steps if self.current and self.current.run else []
        index = next((n for n, s in enumerate(steps) if s.label == label), None)
        if index is not None:
            table = self.query_one("#run", DataTable)
            table.focus()
            table.move_cursor(row=index)

    async def focus_session(self, key: str) -> None:
        run = self.current.run if self.current else None
        session = next((s for s in run.sessions if s.name == key), None) if run else None
        if session is None or not session.surface:
            self.set_message("focus", f"{key}: no pane")
            return
        try:
            await self.focuser(session.surface)
        except FocusError as e:
            self.set_message("focus", f"{key}: {e}")
            return
        self.set_message("focus", "")

    def show_issue(self, issue: Optional[Issue]) -> None:
        self.current = issue
        run_table = self.query_one("#run", DataTable)
        sessions = self.query_one("#sessions", DataTable)
        run_row, session_row = run_table.cursor_row, sessions.cursor_row
        run = issue.run if issue else None
        self.query_one("#run-pane").border_title = f"Run: {run.name}" if run else "Run"
        steps = run.steps if run else []
        fill(run_table, RUN_COLUMNS,
             ((None, [str(n), s.shown, s.session or "", s.state, s.verdict or ""]) for n, s in enumerate(steps, 1)))
        fill(sessions, SESSION_COLUMNS,
             ((s.name, [s.name, s.state]) for s in (run.sessions if run else [])))
        if run_table.row_count:
            run_table.move_cursor(row=min(run_row, run_table.row_count - 1))
        if sessions.row_count:
            sessions.move_cursor(row=min(session_row, sessions.row_count - 1))
        if run and run.error:
            self.set_message("run", f"{run.name}: {run.error}")
        else:
            self.set_message("run", "")
        self.render_info()

    def render_info(self) -> None:
        """Whose work the selected issue is and where it happens, above its run."""
        run = self.current.run if self.current else None
        info = self.query_one("#info", Static)
        info.update(info_text(info_lines(self.repository, self.workspace_entry, self.workspace_error, run, self.places),
                              info.content_size.width))

    # Status line

    def set_message(self, slot: str, message: str) -> None:
        self.messages[slot] = message
        self.update_status()

    def update_status(self) -> None:
        issue, run = self.current, self.current.run if self.current else None
        parts = [self.repository or "(no repository)",
                 (f"#{issue.number}" + (" (closed)" if issue.closed else "")) if issue else "no issue",
                 run.name if run else "no run"]
        parts += [m for m in self.messages.values() if m]
        self.query_one("#status", Static).update("  |  ".join(parts))

    def action_focus_pane(self, pane: str) -> None:
        self.query_one(f"#{pane}", DataTable).focus()


INFO_LABEL_WIDTH = 10


def info_lines(repository: Optional[str], workspace: Optional[Workspace], workspace_error: Optional[str],
               run: Optional[Run], places: Dict[str, str]) -> List[Tuple[str, str]]:
    """(label, value) lines for the info above the run pane. The workspace lines come from repo.json; the run
    lines from status.py (its worktree and record folder from coordinator v0.7.0-rc6), the coordination session's
    pane placed in tmux when it is found there."""
    lines = [("repo", repository or "(no repository)")]
    if workspace:
        lines.append(("workspace", workspace.name))
        if workspace.clone:
            lines.append(("clone", home_short(workspace.clone)
                          + (f" ({workspace.base_branch})" if workspace.base_branch else "")))
    elif workspace_error:
        lines.append(("workspace", workspace_error))
    if not run:
        lines.append(("run", "none"))
        return lines
    lines.append(("run", run.name))
    if run.reference:
        lines.append(("workflow", run.reference))
    if run.branch:
        lines.append(("branch", run.branch))
    if run.checkout:
        lines.append(("worktree", home_short(run.checkout)))
    if run.records:
        lines.append(("records", home_short(run.records)))
    if run.coordination:
        place = places.get(run.coordination)
        lines.append(("session", f"tmux {place}" if place else run.coordination))
    return lines


# Lines whose end tells one run or place from another (a run's key, the run's folder name, the base branch): cut
# at their start when too long. The others are cut at their end.
KEEP_END = ("run", "clone", "worktree", "records")


def info_text(lines: List[Tuple[str, str]], width: int = 0) -> Text:
    """The info as text `width` cells wide (0: not known yet, nothing cut here)."""
    text = Text(no_wrap=True, overflow="ellipsis")
    room = width - INFO_LABEL_WIDTH
    for n, (label, value) in enumerate(lines):
        if n:
            text.append("\n")
        text.append(label.ljust(INFO_LABEL_WIDTH), style="dim")
        text.append(keep_end(value, room) if label in KEEP_END and width > 0 else value)
    return text


def keep_end(value: str, room: int) -> str:
    """`value` cut at its start to `room` cells, an ellipsis in front, when it is longer."""
    if cell_len(value) <= room:
        return value
    if room < 2:
        return "…"[:max(room, 0)]
    kept, used = [], 1
    for char in reversed(value):
        used += cell_len(char)
        if used > room:
            break
        kept.append(char)
    return "…" + "".join(reversed(kept))


def issue_title(issue: Issue) -> str:
    return f"(closed) {issue.title}".rstrip() if issue.closed else issue.title


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(prog="autoflow-ide", description="An issue-based terminal IDE for AutoFlow")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--repo", metavar="OWNER/NAME", help="the GitHub repository whose issues to list")
    source.add_argument("--workspace", metavar="DIR", help="a coordinator workspace; its repo.json names the repository")
    source.add_argument("--demo", action="store_true", help="show placeholder data")
    parser.add_argument("--coordinator", metavar="DIR", help="the autoflow-coordinator clone whose runs to show")
    args = parser.parse_args(argv)
    if args.demo:
        IdeApp(issues=placeholder_issues(), repository="(demo)").run()
        return
    repository = args.repo
    if args.workspace:
        try:
            repository = repository_from_workspace(args.workspace)
        except IssueSourceError as e:
            parser.error(str(e))
    IdeApp(repository=repository, coordinator=args.coordinator).run()


if __name__ == "__main__":
    main()
