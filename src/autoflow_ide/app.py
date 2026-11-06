"""The IDE screen: issues, the selected issue's run, and its sessions."""
import argparse
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional, Set, Tuple, Union

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from rich.cells import cell_len
from rich.text import Text
from textual.widgets import DataTable, Footer, Input, Static

from autoflow_ide.detail import StepDetail, home_short
from autoflow_ide.focus import FocusError, focus_surface
from autoflow_ide.issues import IssueSourceError, gh_issue, gh_issues, repository_from_workspace, workspace_issues
from autoflow_ide.model import Issue, Run, placeholder_issues
from autoflow_ide.runs import RunSourceError, coordinator_runs, version_notice
from autoflow_ide.start import (StartError, Workspace, coordinator_workspace, coordinator_workspace_entry,
                               coordinator_workspaces, start_run)
from autoflow_ide.state import chosen, read_recent, state_path, write_recent
from autoflow_ide.tables import ColumnSpec, fill
from autoflow_ide.version import ide_version
from autoflow_ide.windows import TmuxWindows, Window, WindowError, pane_places

IssueLoader = Callable[[str], Awaitable[List[Issue]]]
WorkspaceFinder = Callable[[str, str], Awaitable[str]]
IssueLookup = Callable[[str, int], Awaitable[Issue]]
RunLoader = Callable[[str], Awaitable[List[Run]]]
Focuser = Callable[[str], Awaitable[None]]
Starter = Callable[[str, str, int], Awaitable[str]]
WorkspaceReader = Callable[[str, str], Awaitable[Workspace]]
Placer = Callable[[], Awaitable[Dict[str, str]]]
WorkspacesReader = Callable[[str], Awaitable[List[Workspace]]]
# An issue by its repository (owner/name) and number: numbers alone repeat across repositories.
IssueKey = Tuple[str, int]
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
# For every workspace, the number goes with the title, so a workspace's name has the room of both.
EVERY_ISSUE_COLUMNS: List[ColumnSpec] = [("", "run", 1), ("Issue", "title", None)]
UNFOLDED_MARK = "▾"
FOLDED_MARK = "▸"
RUN_COLUMNS: List[ColumnSpec] = [("Step", "step", 4), ("Module run", "name", None), ("Session", "session", 7),
                                 ("State", "state", 8), ("Verdict", "verdict", 16)]
SESSION_COLUMNS: List[ColumnSpec] = [("Session", "name", 7), ("State", "state", None)]


def issue_key(issue: Issue) -> IssueKey:
    return (issue.repository or "", issue.number)


@dataclass
class Group:
    """The issues of one repository, under its workspace."""
    repository: str
    # Its workspace (repo.json), when read; why it could not be, for the one repository.
    workspace: Optional[Workspace] = None
    workspace_error: Optional[str] = None
    # The open issues as listed, and those with the runs attached plus a row per run whose issue is not listed.
    open_issues: List[Issue] = field(default_factory=list)
    issues: List[Issue] = field(default_factory=list)
    # An issue found by its number (`#`), shown after its own until the next find or Esc.
    extra: List[Issue] = field(default_factory=list)
    # Whether its open issues have been read: until then, an issue with a run cannot be told to be missing.
    listed: bool = False
    # Why its open issues could not be read, the last time.
    error: Optional[str] = None

    @property
    def name(self) -> str:
        return self.workspace.name if self.workspace else self.repository


def group_title(group: Group, folded: bool) -> str:
    """The row of a workspace in the issues pane: folded, with the number of its rows."""
    if group.error:
        return f"{group.name}: {group.error}"
    return f"{group.name}  ({len(group.issues)})" if folded else group.name


class IdeApp(App):
    CSS_PATH = "app.tcss"
    TITLE = "autoflow-ide"
    BINDINGS = [
        ("1", "focus_pane('issues')", "Issues"),
        ("2", "focus_pane('run')", "Run"),
        ("3", "focus_pane('sessions')", "Sessions"),
        ("r", "refresh_issues", "Refresh"),
        ("s", "start_run", "Start run"),
        ("a", "toggle_running", "Running only"),
        ("number_sign", "find", "Issue #"),
        ("escape", "close_find", "Clear found"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, issues: Optional[List[Issue]] = None, repository: Optional[str] = None,
                 loader: Optional[IssueLoader] = None, coordinator: Optional[str] = None,
                 run_loader: Optional[RunLoader] = None, focuser: Optional[Focuser] = None,
                 run_refresh_sec: float = RUN_REFRESH_SEC, lookup: Optional[IssueLookup] = None,
                 starter: Optional[Starter] = None, issue_refresh_sec: float = ISSUE_REFRESH_SEC,
                 windows=None, workspace_finder: Optional[WorkspaceFinder] = None,
                 window_check_sec: float = WINDOW_CHECK_SEC, window_idle_sec: float = WINDOW_IDLE_SEC,
                 workspace_reader: Optional[WorkspaceReader] = None, placer: Optional[Placer] = None,
                 version: Optional[str] = None, workspaces_reader: Optional[WorkspacesReader] = None,
                 state_file: Optional[Path] = None):
        """With `issues`, show them as given. Otherwise load the open issues of `repository` with `loader`,
        again every `issue_refresh_sec` seconds; without `repository` but with `coordinator`, the most recent
        open issues of every workspace of the coordinator's base (`workspaces_reader`), under a row per workspace.
        With `coordinator` (a coordinator clone), read its active runs every `run_refresh_sec` seconds.
        An issue that has a run but is not in the open list is looked up with `lookup`.
        `starter` opens the coordination session for an issue (see start.py); `windows` finds and closes those
        sessions' windows (see windows.py) in the workspace `workspace_finder` names, or in every workspace.
        `workspace_reader` reads that workspace's repo.json and `placer` where each tmux pane is, both shown
        above the run pane.
        `version`, the IDE's own (see version.py), is shown in the bottom-right corner.
        `state_file` keeps the workspaces chosen recently, which open unfolded with those that have a run."""
        super().__init__()
        self.version = version
        self.static = issues is not None
        self.repository = repository
        self.coordinator = coordinator
        # Every workspace of the base, instead of one repository.
        self.every = not self.static and repository is None and coordinator is not None
        # Titles of the issues seen in the open list, shown for an issue that left it until it is looked up.
        self.titles: Dict[IssueKey, str] = {}
        self.groups: List[Group] = []
        if self.static:
            group = Group(repository or "", listed=True)
            self.groups.append(group)
            self.stamp(group, list(issues))
        elif repository:
            self.groups.append(Group(repository))
        self.workspaces_reader = workspaces_reader or coordinator_workspaces
        # The folded workspaces, by repository; the recently chosen ones, by workspace name, most recent first.
        self.folded: Set[str] = set()
        # Only the issues with a run, across workspaces and regardless of folding (`a`).
        self.running_only = False
        # The repository the find box is open for.
        self.finding: Optional[str] = None
        self.state_file = state_file
        self.recent: List[str] = read_recent(state_file) if state_file else []
        self.lookup = lookup or gh_issue
        # Issues missing from the open list, by (repository, number): the looked-up issue, or None while looking
        # or after a failure (looked up again on the next refresh of the issues).
        self.lookups: Dict[IssueKey, Optional[Issue]] = {}
        self.loader = loader or gh_issues
        self.run_loader = run_loader or coordinator_runs
        self.focuser = focuser or focus_surface
        self.starter = starter or start_run
        # Coordination sessions opened from this IDE, by issue: their pane, until the issue has a run.
        self.started: Dict[IssueKey, str] = {}
        self.run_refresh_sec = run_refresh_sec
        self.issue_refresh_sec = issue_refresh_sec
        self.runs: List[Run] = []
        # Whether the last read of the runs succeeded: without it, an issue cannot be told to have no run.
        self.runs_read = False
        self.windows = windows or TmuxWindows()
        self.workspace_finder = workspace_finder or coordinator_workspace
        self.window_check_sec = window_check_sec
        self.window_idle_sec = window_idle_sec
        # The workspace of the one repository, by name, as workspace_finder gave it.
        self.workspace: Optional[str] = None
        self.workspace_reader = workspace_reader or coordinator_workspace_entry
        self.placer = placer or pane_places
        # Where each tmux pane is ("2:1 autoflow-coordinator#59"), by pane id, as last read.
        self.places: Dict[str, str] = {}
        # The coordination session windows, by (workspace, issue): (window id, its screen, when first seen).
        self.screens: Dict[Tuple[str, int], Tuple[str, str, float]] = {}
        # When an issue was last looked up and found open, so it is not looked up on every check.
        self.found_open: Dict[IssueKey, float] = {}
        # The steps of each shown run, by run name, as last read: what a step change is compared against.
        self.step_marks: Dict[str, List[Tuple[str, str, Optional[str]]]] = {}
        self.current: Optional[Issue] = None
        # The repository of the workspace row the cursor is on, when it is on one.
        self.on_header: Optional[str] = None
        self.messages: Dict[str, str] = {}

    def compose(self) -> ComposeResult:
        with Horizontal(id="panes"):
            with Vertical(id="issues-pane", classes="pane") as pane:
                pane.border_title = "Issues"
                yield DataTable(id="issues", cursor_type="row")
                yield Input(id="find")
            with Vertical(id="run-pane", classes="pane") as pane:
                pane.border_title = "Run"
                yield Static(id="info")
                yield DataTable(id="run", cursor_type="row")
            with Vertical(id="sessions-pane", classes="pane") as pane:
                pane.border_title = "Sessions"
                if self.version:
                    pane.border_subtitle = f"ide {self.version}"
                yield DataTable(id="sessions", cursor_type="row")
        yield Static(id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#find", Input).display = False
        self.query_one("#issues", DataTable).focus()
        self.show_issues()
        if not self.static:
            self.action_refresh_issues()
            self.set_interval(self.issue_refresh_sec, self.action_refresh_issues)
        if self.coordinator:
            self.set_message("version", version_notice(self.coordinator))
            if self.repository and not self.static:
                self.run_worker(self.load_workspace(), group="workspace")
            self.refresh_runs()
            self.set_interval(self.run_refresh_sec, self.refresh_runs)
            if not self.static and (self.repository or self.every):
                self.set_interval(self.window_check_sec, self.check_windows)
        self.call_after_refresh(self.render_all)

    def on_resize(self) -> None:
        self.call_after_refresh(self.render_all)

    def render_all(self) -> None:
        self.render_issues()
        self.show_issue(self.current_in())

    # Issues, in groups by repository

    @property
    def issues(self) -> List[Issue]:
        return [i for g in self.groups for i in g.issues]

    @property
    def open_issues(self) -> List[Issue]:
        return [i for g in self.groups for i in g.open_issues]

    def group_of(self, repository: Optional[str]) -> Optional[Group]:
        return next((g for g in self.groups if g.repository == repository), None)

    def stamp(self, group: Group, issues: List[Issue]) -> None:
        """Make `issues` the open issues of `group`."""
        for issue in issues:
            issue.repository = group.repository
        group.open_issues = issues
        self.titles.update(((group.repository, i.number), i.title) for i in issues)

    def set_issues(self, issues: List[Issue]) -> None:
        """Show the open `issues` of the one repository, with the runs attached and the issues of runs that are
        not among them."""
        if self.groups:
            self.stamp(self.groups[0], issues)
        self.show_issues()

    def show_issues(self) -> None:
        self.attach_runs()
        self.render_issues()
        self.show_issue(self.current_in())

    def row_key(self, issue: Issue) -> str:
        return f"{issue.repository}#{issue.number}" if self.every else str(issue.number)

    def label(self, key: IssueKey) -> str:
        """How an issue is named in a message: its number, with its repository's name for every workspace."""
        return f"{key[0].split('/')[-1]}#{key[1]}" if self.every else f"#{key[1]}"

    def current_in(self) -> Optional[Issue]:
        """The highlighted issue if it is still listed, otherwise the first one in display order; none while the
        cursor is on a workspace row."""
        if self.on_header is not None and self.on_header in self.headers():
            return None
        key = issue_key(self.current) if self.current else None
        ordered = self.ordered()
        found = next((i for i in ordered if issue_key(i) == key), None)
        if (found is None and self.current is not None and not self.running_only
                and self.current.repository in self.folded):
            return None  # its workspace was folded: the cursor goes to the workspace's row
        return found or (ordered[0] if ordered else None)

    def shown(self, group: Group) -> List[Issue]:
        """The rows of `group` under its own: none when folded, those with a run first; only those with a run
        when only running issues are shown, folded or not."""
        if self.running_only:
            return [i for i in group.issues if i.run]
        return [] if group.repository in self.folded else sorted(group.issues, key=lambda i: i.run is None)

    def headers(self) -> List[str]:
        """The workspaces whose row is shown, by repository: all of them, or those with a run when only running
        issues are shown."""
        if not self.every:
            return []
        return [g.repository for g in self.groups if not self.running_only or self.shown(g)]

    def ordered(self) -> List[Issue]:
        """The issues shown, in their groups, those with a run first in each, each part in the order the source
        gave."""
        return [i for g in self.groups for i in self.shown(g)]

    def render_issues(self) -> None:
        table = self.query_one("#issues", DataTable)
        rows: List[Tuple[Optional[str], List[Union[str, Text]]]] = []
        headers = self.headers()
        for group in self.groups:
            issues = self.shown(group)
            if self.every:
                if group.repository not in headers:
                    continue
                folded = group.repository in self.folded and not self.running_only
                rows.append((group.repository, [FOLDED_MARK if folded else UNFOLDED_MARK,
                                                 Text(group_title(group, folded), style="bold")]))
                rows += [(self.row_key(i), [RUN_MARK if i.run else "", f"{i.number}  {issue_title(i)}"])
                         for i in issues]
            else:
                rows += [(self.row_key(i), [RUN_MARK if i.run else "", str(i.number), issue_title(i)]) for i in issues]
        fill(table, EVERY_ISSUE_COLUMNS if self.every else ISSUE_COLUMNS, rows)
        # With only running issues shown and none to show, the empty pane says why.
        none = self.running_only and not any(self.shown(g) for g in self.groups)
        if none or self.messages.get("running"):
            self.set_message("running", "no issue has a run" if none else "")
        current = self.current_in()
        if current:
            table.move_cursor(row=table.get_row_index(self.row_key(current)))
        elif headers:
            # On a workspace's row: the one the cursor was on, the folded one of the highlighted issue, or the first.
            for header in (self.on_header, self.current.repository if self.current else None, headers[0]):
                if header in headers:
                    self.on_header = header
                    table.move_cursor(row=table.get_row_index(header))
                    break

    def action_refresh_issues(self) -> None:
        if self.static:
            return
        if self.every:
            self.set_message("issues", "loading issues")
            self.lookups = {k: i for k, i in self.lookups.items() if i is not None}
            self.run_worker(self.load_every(), group="issues", exclusive=True)
            return
        if not self.repository:
            self.set_message("issues", "no repository: start with --repo owner/name or --workspace <dir>")
            return
        self.set_message("issues", "loading issues")
        self.lookups = {k: i for k, i in self.lookups.items() if i is not None}
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
                self.groups[0].listed = True
                self.set_issues(issues)
            self.set_message("issues", message)
        except NoMatches:
            pass  # the app is closing: its widgets are gone

    async def load_every(self) -> None:
        """Read the workspaces of the base again, then the most recent open issues of each."""
        try:
            workspaces = await self.workspaces_reader(self.coordinator)
        except StartError as e:
            self.set_message("issues", f"workspaces: {e}")
            return
        groups: List[Group] = []
        for workspace in workspaces:
            if any(g.repository == workspace.github for g in groups):
                continue  # a second workspace of the same repository: the coordinator cannot tell them apart
            group = self.group_of(workspace.github) or Group(workspace.github)
            group.workspace = workspace
            groups.append(group)
        found = await workspace_issues([g.repository for g in groups], loader=self.loader)
        failed = []
        for group in groups:
            result = found[group.repository]
            if isinstance(result, IssueSourceError):
                group.error = str(result)
                failed.append(f"{group.name}: {result}")
            else:
                group.error = None
                group.listed = True
                self.stamp(group, result)
        try:
            known = {g.repository for g in self.groups}
            self.groups = groups
            for group in groups:
                # A workspace seen for the first time opens unfolded when it has a run or was chosen recently.
                if group.repository not in known and group.name not in self.recent and not any(
                        self.run_repository(r) == group.repository for r in self.runs):
                    self.folded.add(group.repository)
            self.show_issues()
            if self.runs_read:
                self.count_runs()  # the runs read before the workspaces were known
            self.set_message("issues", "; ".join(failed) if failed
                             else f"{len(self.open_issues)} recent open issues in {len(groups)} workspaces")
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

    # The workspace of the one repository

    async def load_workspace(self) -> None:
        group = self.groups[0]
        try:
            group.workspace = await self.workspace_reader(self.coordinator, self.repository)
        except StartError as e:
            group.workspace_error = str(e)
        try:
            self.render_info()
        except NoMatches:
            pass  # the app is closing: its widgets are gone

    def show_runs(self, runs: List[Run]) -> None:
        before = {issue_key(i) for i in self.issues if i.run}
        self.runs = runs
        self.attach_runs()
        after = {issue_key(i) for i in self.issues if i.run}
        started = after - before
        if any(self.started.pop(k, None) for k in after):
            self.set_message("start", "")  # the coordination session prepared the run
        self.folded -= {repository for repository, _ in started}  # a run that starts shows in its workspace
        if started and (self.current is None or self.current.run is None):
            # The operator is not looking at a run: move to the issue whose run just started.
            self.on_header = None
            self.current = next(i for i in self.ordered() if issue_key(i) in started)
        self.render_issues()
        self.show_issue(self.current_in())
        self.count_runs()
        if before - after or self.steps_changed():
            # A run ended, or a step of a run changed: a merge may have closed an issue in the meantime.
            self.action_refresh_issues()

    def count_runs(self) -> None:
        own = len(self.own_runs())
        others = len(self.runs) - own
        self.set_message("runs", f"{own} active runs" + (f" ({others} in other repositories)" if others else ""))

    def steps_changed(self) -> bool:
        """Whether a step of a run already shown changed its state or verdict, or a step was added, since the
        last read; records the steps for the next one. Not the session state and idle time of a running step,
        which change on every read. A run seen for the first time is not a change."""
        marks = {r.name: [(s.label, s.state, s.verdict) for s in r.steps] for r in self.own_runs()}
        changed = any(name in self.step_marks and self.step_marks[name] != steps for name, steps in marks.items())
        self.step_marks = marks
        return changed

    def run_repository(self, run: Run) -> Optional[str]:
        """The shown repository `run` belongs to. status.py lists the active runs of every repository; the
        coordinator names a run <repository name>-<issue>-<key>."""
        if not run.issue:
            return None
        return next((g.repository for g in self.groups
                     if g.repository and run.name.startswith(f"{g.repository.split('/')[-1]}-{run.issue}-")), None)

    def own_runs(self) -> List[Run]:
        """The runs of the shown repositories."""
        return [r for r in self.runs if self.run_repository(r)]

    def attach_runs(self) -> None:
        """Each issue gets the first of its runs; status.py lists the most recently changed run first.
        A run stays active after its issue closes (a merge closes it) until the coordination session closes
        the run, so a run whose issue is not in the open list adds a row for that issue after the open ones."""
        if not self.coordinator:
            for group in self.groups:
                listed = {i.number for i in group.open_issues}
                group.issues = list(group.open_issues) + [i for i in group.extra if i.number not in listed]
            return
        for group in self.groups:
            listed = {i.number for i in group.open_issues}
            issues = list(group.open_issues) + [i for i in group.extra if i.number not in listed]
            runs = [r for r in self.runs if self.run_repository(r) == group.repository]
            for issue in issues:
                issue.run = next((r for r in runs if r.issue == str(issue.number)), None)
            if group.listed:
                shown = {i.number for i in issues}
                for run in runs:
                    if not run.issue.isdigit() or int(run.issue) in shown:
                        continue
                    number = int(run.issue)
                    shown.add(number)
                    issues.append(self.missing_issue((group.repository, number), run))
            group.issues = issues

    def missing_issue(self, key: IssueKey, run: Run) -> Issue:
        """The row for issue `key`, which has `run` but is not in the open list."""
        repository, number = key
        found = self.lookups.get(key)
        if found is not None:
            return Issue(number, found.title, run, found.state, repository)
        self.look_up(key)
        return Issue(number, self.titles.get(key, ""), run, None, repository)

    def look_up(self, key: IssueKey) -> None:
        if key in self.lookups or self.static or not key[0]:
            return
        self.lookups[key] = None
        self.run_worker(self.load_issue(key), group=f"issue-{key[0]}-{key[1]}")

    async def load_issue(self, key: IssueKey) -> None:
        try:
            issue = await self.lookup(*key)
        except IssueSourceError as e:
            issue, message = None, f"issue {self.label(key)}: {e}"
        else:
            message = ""
        try:
            if issue is not None:
                self.lookups[key] = issue
                self.show_issues()
            self.set_message("lookup", message)
        except NoMatches:
            pass  # the app is closing: its widgets are gone

    # Starting a run

    def action_start_run(self) -> None:
        issue = self.current
        if issue is None:
            return
        key = issue_key(issue)
        if self.static or not self.coordinator or not issue.repository:
            self.set_message("start", "start: needs --coordinator and --repo or --workspace")
        elif issue.closed:
            self.set_message("start", f"{self.label(key)} is closed")
        elif issue.run:
            self.set_message("start", f"{self.label(key)} already has a run: {issue.run.name}")
        elif key in self.started:
            # Opened from here, its run not prepared yet: go back to that coordination session.
            self.run_worker(self.focus_started(key), group="start", exclusive=True)
        else:
            self.run_worker(self.start(key), group="start", exclusive=True)

    async def start(self, key: IssueKey) -> None:
        self.set_message("start", f"{self.label(key)}: opening the coordination session")
        try:
            pane = await self.starter(self.coordinator, *key)
        except StartError as e:
            self.set_message("start", f"start {self.label(key)}: {e}")
            return
        self.started[key] = pane
        self.set_message("start", f"{self.label(key)}: coordination session in {pane}")
        group = self.group_of(key[0])
        if self.every and group:
            self.remember(group.name, True)

    async def focus_started(self, key: IssueKey) -> None:
        pane = self.started[key]
        try:
            await self.focuser(pane)
        except FocusError as e:
            del self.started[key]
            self.set_message("start", f"{self.label(key)}: {pane}: {e}; press s to open it again")
            return
        self.set_message("start", f"{self.label(key)}: coordination session in {pane}")

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
        found: Dict[Tuple[str, int], Tuple[str, Window]] = {}
        try:
            if self.every:
                places = [(g.workspace.name, g.repository) for g in self.groups if g.workspace]
            else:
                if self.workspace is None:
                    self.workspace = await self.workspace_finder(self.coordinator, self.repository)
                places = [(self.workspace, self.repository)]
            for workspace, repository in places:
                for number, window in (await self.windows.find(workspace)).items():
                    found[(workspace, number)] = (repository, window)
        except (StartError, WindowError) as e:
            self.set_message("windows", f"windows: {e}")
            return
        now = time.monotonic()
        self.screens = {k: s for k, s in self.screens.items() if k in found}
        for (workspace, number), (repository, window) in found.items():
            try:
                screen = await self.windows.screen(window)
            except WindowError as e:
                self.set_message("windows", f"{window.name}: {e}")
                continue
            seen = self.screens.get((workspace, number))
            if seen is None or seen[0] != window.id or seen[1] != screen:
                self.screens[(workspace, number)] = seen = (window.id, screen, now)
            if now - seen[2] < self.window_idle_sec or not await self.finished((repository, number), now):
                continue
            try:
                await self.windows.close(window)
            except WindowError as e:
                self.set_message("windows", f"{window.name}: {e}")
                continue
            del self.screens[(workspace, number)]
            self.started.pop((repository, number), None)
            self.set_message("windows", f"closed {window.name}: #{number} is closed and has no run")

    async def finished(self, key: IssueKey, now: float) -> bool:
        """Whether issue `key` is closed and has no active run. Closed is asked of GitHub, not taken from
        the issue missing from the open list; an issue found open is asked again after window_idle_sec."""
        repository, number = key
        group = self.group_of(repository)
        if group is None or not (group.listed and self.runs_read):
            return False
        if (any(r.issue == str(number) and self.run_repository(r) == repository for r in self.runs)
                or any(i.number == number for i in group.open_issues)):
            return False
        found_open = self.found_open.get(key)
        if found_open is not None and now - found_open < self.window_idle_sec:
            return False
        try:
            issue = await self.lookup(repository, number)
        except IssueSourceError as e:
            self.set_message("windows", f"issue {self.label(key)}: {e}")
            return False
        if not issue.closed:
            self.found_open[key] = now
        return issue.closed

    # Selection

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id != "issues" or event.row_key.value is None:
            return
        key = event.row_key.value
        if self.every and self.group_of(key):
            self.on_header = key
            self.show_issue(None)
            return
        self.show_issue(next((i for i in self.issues if self.row_key(i) == key), None))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "issues" and self.every and self.group_of(event.row_key.value):
            if not self.running_only:
                self.fold(event.row_key.value)
        elif event.data_table.id == "sessions" and event.row_key.value is not None:
            self.run_worker(self.focus_session(event.row_key.value), group="focus", exclusive=True)
        elif event.data_table.id == "run":
            steps = self.current.run.steps if self.current and self.current.run else []
            if 0 <= event.cursor_row < len(steps):
                self.push_screen(StepDetail(steps[event.cursor_row], event.cursor_row + 1), self.go_to_step)

    # Finding an issue by its number, in the highlighted workspace (searching by words is left for later, #52)

    def action_find(self) -> None:
        group = self.current_group()
        if self.static or group is None or not group.repository:
            self.set_message("find", "find: no repository to look in")
            return
        self.finding = group.repository
        box = self.query_one("#find", Input)
        box.placeholder = f"#number in {group.name}"
        box.restrict = r"[0-9]*"
        box.value = ""
        box.display = True
        box.focus()

    def action_close_find(self) -> None:
        """Esc: close the find box, or else clear what was found."""
        box = self.query_one("#find", Input)
        if box.display:
            self.end_find()
            return
        if any(g.extra for g in self.groups):
            for group in self.groups:
                group.extra = []
            self.show_issues()
            self.set_message("find", "")

    def end_find(self) -> None:
        self.finding = None
        self.query_one("#find", Input).display = False
        self.query_one("#issues", DataTable).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "find" or self.finding is None:
            return
        repository, text = self.finding, event.value.strip()
        self.end_find()
        if text:
            self.run_worker(self.find_number(repository, int(text)), group="find", exclusive=True)

    async def find_number(self, repository: str, number: int) -> None:
        """Issue `number` of `repository`: from its listed issues, or else looked up and shown with them."""
        group, key = self.group_of(repository), (repository, number)
        if group is None:
            return
        found = next((i for i in group.issues if i.number == number), None)
        if found is None:
            self.set_message("find", f"looking up {self.label(key)}")
            try:
                found = await self.lookup(repository, number)
            except IssueSourceError as e:
                self.set_message("find", f"{self.label(key)}: {e}")
                return
            found.repository = repository
            group.extra = [found]
        self.set_message("find", f"{self.label(key)} is closed" if found.closed else "")
        self.show_found(group, found)

    def show_found(self, group: Group, issue: Issue) -> None:
        """Show `group` unfolded, in every mode, with the cursor on `issue`."""
        if self.running_only:
            self.action_toggle_running()
        self.folded.discard(group.repository)
        self.on_header = None
        self.current = issue
        self.show_issues()

    def action_toggle_running(self) -> None:
        """Show only the issues with a run, or all again."""
        self.running_only = not self.running_only
        self.query_one("#issues-pane").border_title = "Issues · running" if self.running_only else "Issues"
        self.render_issues()
        self.show_issue(self.current_in())

    def fold(self, repository: str) -> None:
        """Fold the workspace of `repository`, or unfold it; an unfolded one is remembered as chosen recently."""
        group = self.group_of(repository)
        unfolding = repository in self.folded
        if unfolding:
            self.folded.discard(repository)
        else:
            self.folded.add(repository)
        self.on_header = repository
        self.render_issues()
        self.show_issue(self.current_in())
        self.remember(group.name, unfolding)

    def remember(self, name: str, chose: bool) -> None:
        """Keep workspace `name` as chosen recently, or drop it when folded, in the state file."""
        recent = chosen(self.recent, name) if chose else [n for n in self.recent if n != name]
        if recent == self.recent:
            return
        self.recent = recent
        if self.state_file:
            try:
                write_recent(self.state_file, recent)
            except OSError as e:
                self.set_message("state", f"state: {e}")

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
        if issue is not None:
            self.on_header = None
        run_table = self.query_one("#run", DataTable)
        sessions = self.query_one("#sessions", DataTable)
        run_row, session_row = run_table.cursor_row, sessions.cursor_row
        run = issue.run if issue else None
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
        group = self.current_group()
        info = self.query_one("#info", Static)
        lines = (info_lines(group.repository, group.workspace, group.workspace_error, run, self.places) if group
                 else info_lines(None, None, None, run, self.places))
        info.update(info_text(lines, info.content_size.width))

    def current_group(self) -> Optional[Group]:
        """The group of the highlighted issue or workspace row; the one repository's when there is only it."""
        if self.current is not None:
            return self.group_of(self.current.repository)
        if self.on_header is not None:
            return self.group_of(self.on_header)
        return self.groups[0] if not self.every and self.groups else None

    # Status line

    def set_message(self, slot: str, message: str) -> None:
        self.messages[slot] = message
        self.update_status()

    def update_status(self) -> None:
        issue, run = self.current, self.current.run if self.current else None
        group = self.current_group()
        parts = [(group.repository if group else self.repository) or ("every workspace" if self.every else "(no repository)"),
                 (f"#{issue.number}" + (" (closed)" if issue.closed else "")) if issue else "no issue",
                 run.name if run else "no run"]
        parts += [m for m in self.messages.values() if m]
        try:
            self.query_one("#status", Static).update("  |  ".join(parts))
        except NoMatches:
            pass  # the app is closing (a timer can fire meanwhile): its widgets are gone

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


# Lines that are a path or a run name: cut in the middle when too long, so their start (where it is) and their end
# (which run it is: the run's key, the run's folder name, the base branch) stay in view. The others are cut at
# their end.
CUT_MIDDLE = ("run", "clone", "worktree", "records")
# The share of the room kept from a value's start when it is cut in the middle; the rest is its end.
HEAD_SHARE = 0.4


def info_text(lines: List[Tuple[str, str]], width: int = 0) -> Text:
    """The info as text `width` cells wide (0: not known yet, nothing cut here)."""
    text = Text(no_wrap=True, overflow="ellipsis")
    room = width - INFO_LABEL_WIDTH
    for n, (label, value) in enumerate(lines):
        if n:
            text.append("\n")
        text.append(label.ljust(INFO_LABEL_WIDTH), style="dim")
        text.append(cut_middle(value, room) if label in CUT_MIDDLE and width > 0 else value)
    return text


def cut_middle(value: str, room: int) -> str:
    """`value` cut to `room` cells, an ellipsis in place of its middle, when it is longer."""
    if cell_len(value) <= room:
        return value
    if room < 2:
        return "…"[:max(room, 0)]
    head = take(value, int((room - 1) * HEAD_SHARE))
    tail = take(value[::-1], room - 1 - cell_len(head))[::-1]
    return head + "…" + tail


def take(value: str, cells: int) -> str:
    """The longest start of `value` that fits in `cells` cells."""
    used, end = 0, 0
    for char in value:
        used += cell_len(char)
        if used > cells:
            break
        end += 1
    return value[:end]


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
        IdeApp(issues=placeholder_issues(), repository="(demo)", version=ide_version()).run()
        return
    repository = args.repo
    if args.workspace:
        try:
            repository = repository_from_workspace(args.workspace)
        except IssueSourceError as e:
            parser.error(str(e))
    IdeApp(repository=repository, coordinator=args.coordinator, version=ide_version(), state_file=state_path()).run()


if __name__ == "__main__":
    main()
