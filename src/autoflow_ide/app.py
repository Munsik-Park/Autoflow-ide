"""The IDE screen: issues, the selected issue's run, and its sessions."""
import argparse
import asyncio
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional, Set, Tuple, Union

from textual.app import App, ComposeResult, SystemCommand
from textual.containers import Horizontal, Vertical
from textual.binding import Binding
from textual.css.query import NoMatches
from rich.cells import cell_len
from rich.style import Style
from rich.text import Text
from textual.timer import Timer
from textual.widgets import DataTable, Footer, Input, Static

from autoflow_ide.detail import StepDetail, home_short
from autoflow_ide.focus import FocusError, focus_surface
from autoflow_ide.issues import IssueSourceError, gh_issue, gh_issues, repository_from_workspace, workspace_issues
from autoflow_ide.model import Issue, Run, placeholder_issues
from autoflow_ide.runs import RunSourceError, coordinator_runs, version_notice
from autoflow_ide.screens import waiting
from autoflow_ide.sessions import CoordinationSession, sessions_of
from autoflow_ide.start import (Setting, StartError, TokenError, Workspace, coordinator_base, coordinator_workspace,
                               coordinator_workspace_entry, coordinator_workspaces, create_workspace, printable, read_token,
                               repository_of, setting_source, start_run)
from autoflow_ide.state import chosen, read_recent, state_path, write_recent
from autoflow_ide.tables import ColumnSpec, fill, flex_width
from autoflow_ide.version import ide_version
from autoflow_ide.windows import TmuxWindows, Window, WindowError, pane_places

IssueLoader = Callable[[str], Awaitable[List[Issue]]]
WorkspaceFinder = Callable[[str, str], Awaitable[str]]
IssueLookup = Callable[[str, int], Awaitable[Issue]]
RunLoader = Callable[[str], Awaitable[List[Run]]]
Focuser = Callable[[str], Awaitable[None]]
# Opens a coordination session: the coordinator clone, the repository, the issue and the operator's instructions for
# the run ("" for none); its pane id (#85).
Starter = Callable[[str, str, int, str], Awaitable[str]]
WorkspaceReader = Callable[[str, str], Awaitable[Workspace]]
Placer = Callable[[], Awaitable[Dict[str, str]]]
WorkspacesReader = Callable[[str], Awaitable[List[Workspace]]]
# Reads the coordinator's base and where it was set, given the coordinator clone.
BaseReader = Callable[[str], Awaitable[Setting]]
# Opens a coordination session that makes a workspace, given the coordinator clone and the repository's address as
# the operator typed it; its pane id.
WorkspaceCreator = Callable[[str, str], Awaitable[str]]
# An issue by its repository (owner/name) and number: numbers alone repeat across repositories.
IssueKey = Tuple[str, int]
# What read_windows_of returns when the coordinator names no workspace for the issue's repository (#85).
NO_WORKSPACE = "no workspace"
RUN_REFRESH_SEC = 5.0
# Browser control runs on this Python or later, the oldest aiohttp >= 3.14.5 supports (#86).
BROWSER_PYTHON = (3, 10)
# What a click on `open` runs to open the browser page in the system's default web browser: macOS's osascript, by
# its absolute path, reading `open location "<address>"` on its stdin, so the address is in no process's arguments.
# It runs in its own session with its output to /dev/null, so nothing it prints draws on the screen, and it is not
# waited on, so the IDE neither freezes nor waits for it at quit (#89).
OPEN_COMMAND = ["/usr/bin/osascript"]
# How long the IDE watches the opener's exit status: one still running then (osascript waiting for a browser that
# starts cold) has not failed (#89).
OPEN_WAIT_SEC = 3.0
OPEN_POLL_SEC = 0.1
OPEN_FAILED = "browser: could not open the page; its address is above"
# The open issues are also read again on this interval, so an issue a merge closed leaves the list without a
# key press (about 60 gh calls an hour), and when a step changes (see show_runs).
ISSUE_REFRESH_SEC = 60.0
# The coordination session window of an issue that is closed and has no run is closed WINDOW_IDLE_SEC after the
# later of its screen's last change and the IDE first finding the issue closed with no run, so a last report being
# written is not cut off and the operator can come back after a merge to go on with the session, whose screen
# stands still while it waits for input; checked every WINDOW_CHECK_SEC.
WINDOW_CHECK_SEC = 15.0
WINDOW_IDLE_SEC = 300.0
RUN_MARK = "●"
# An issue that waits for the operator's answer, in place of RUN_MARK or blank: a pane of its coordination session
# window shows a prompt at the last window check, or a work session of its run is in WAITING_INPUT, the state the
# coordinator's status.py gives such a session, at the last read of the runs (#79).
WAIT_MARK = "?"
WAITING_INPUT = "waiting-input"
# Notices of something that happened once (a coordination session opened, a window closed, a focus moved, an
# issue found, the state file not written) are shown first on the status line, so a narrow line does not cut
# them off, and go NOTICE_SEC after they are set (#26). The other messages say what is true now (the counts, the
# version, a read error until the next read succeeds) and stay while it is.
NOTICE_SLOTS = ("start", "workspace", "windows", "focus", "find", "state", "open")
NOTICE_SEC = 10.0
ISSUE_COLUMNS: List[ColumnSpec] = [("", "run", 1), ("#", "number", 4), ("Title", "title", None)]
# For every workspace, the number goes with the title, so a workspace's name has the room of both.
EVERY_ISSUE_COLUMNS: List[ColumnSpec] = [("", "run", 1), ("Workspace / Issue", "title", None)]
UNFOLDED_MARK = "▾"
FOLDED_MARK = "▸"
RUN_COLUMNS: List[ColumnSpec] = [("Step", "step", 4), ("Module run", "name", None), ("Session", "session", 7),
                                 ("State", "state", 8), ("Verdict", "verdict", 16)]
SESSION_COLUMNS: List[ColumnSpec] = [("Session", "name", 7), ("State", "state", None)]
# Before the label of a coordination session's row, under its issue's row (#85).
SESSION_INDENT = "└ "
# show_issue's default: keep the session shown when the issue stays the same (#85).
KEEP = object()


def page_opener_offered() -> bool:
    """Whether `open` is offered: on macOS with no BROWSER in the environment, where the opener takes the address on
    its stdin. Elsewhere the opener would take it in its arguments, which other OS users can read (#89)."""
    return sys.platform == "darwin" and "BROWSER" not in os.environ


def open_script(url: str) -> str:
    """The AppleScript that opens `url` in the default web browser, with the characters an AppleScript string
    escapes written as URL escapes (#89)."""
    return 'open location "%s"' % url.replace("\\", "%5C").replace('"', "%22")


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
    # An issue found by its number (`#`), shown after its own until the next find or Esc; read again at the end
    # of each read of the issues, and changed in place, so its row and the status line say what it is now (#80).
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
        # The left pane holds the issues of one repository, or workspaces and their issues (#71): one of these
        # two is shown, see check_action.
        ("1", "focus_pane('issues')", "Issues"),
        ("1", "focus_pane('issues', 'every')", "Workspaces"),
        ("2", "focus_pane('run')", "Run"),
        ("3", "focus_pane('sessions')", "Sessions"),
        ("r", "refresh_issues", "Refresh"),
        ("s", "start_run", "Start"),
        ("S", "new_session", "New session"),
        ("a", "toggle_running", "Running"),
        ("number_sign", "find", "Issue #"),
        ("w", "add_workspace", "Workspace"),
        ("escape", "close_find", "Clear"),
        ("q", "quit", "Quit"),
        # Not shown, so every key in the footer still fits at 124 columns; the command palette has it too (#86).
        Binding("b", "toggle_browser", "Browser", show=False),
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
                 state_file: Optional[Path] = None, notice_sec: float = NOTICE_SEC,
                 base_reader: Optional[BaseReader] = None, workspace_creator: Optional[WorkspaceCreator] = None,
                 browser_port: int = 0):
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
        `state_file` keeps the workspaces chosen recently, which open unfolded with those that have a run.
        `base_reader` reads the coordinator's base and where it was set, again whenever the issues are read.
        `workspace_creator` opens a coordination session that makes a workspace for a repository (`w`).
        `browser_port` is the localhost port browser control listens on (`b`), 0 for one the system picks (#86)."""
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
        self.base_reader = base_reader or coordinator_base
        # The coordinator's base as last read, or why it could not be.
        self.base: Optional[Setting] = None
        self.base_error: Optional[str] = None
        # The folded workspaces, by repository; the recently chosen ones, by workspace name, most recent first.
        self.folded: Set[str] = set()
        # Only the issues with a run, across workspaces and regardless of folding (`a`).
        self.running_only = False
        # The repository the find box is open for.
        self.finding: Optional[str] = None
        # Whether the box is open for the repository of a new workspace instead (`w`).
        self.adding = False
        self.workspace_creator = workspace_creator or create_workspace
        # Coordination sessions opened from here to make a workspace, by repository: their pane, until the
        # workspace shows in the base.
        self.creating: Dict[str, str] = {}
        self.state_file = state_file
        self.recent: List[str] = read_recent(state_file) if state_file else []
        # Read with each workspace's gh-token when it has one (#74); given ones are used as they are.
        self.lookup = lookup or self.gh_issue
        # Issues missing from the open list, by (repository, number): the looked-up issue, or None while looking
        # or after a failure (looked up again on the next refresh of the issues).
        self.lookups: Dict[IssueKey, Optional[Issue]] = {}
        # Why a found issue could not be read again, by (repository, number), until a later read of it succeeds,
        # Esc clears it or a new find replaces it: shown on the status line in a slot of its own, "found" (#80).
        self.found_failures: Dict[IssueKey, str] = {}
        self.loader = loader or self.gh_issues
        self.run_loader = run_loader or coordinator_runs
        self.focuser = focuser or focus_surface
        self.starter = starter or start_run
        # Coordination sessions opened from this IDE, by issue: their panes, until a run names the pane as its
        # coordination surface, or the pane's window was found and is gone (#85).
        self.started: Dict[IssueKey, List[str]] = {}
        # The opened panes a window check has found in a window.
        self.opened_seen: Set[str] = set()
        # Opens in progress, by issue: the instructions were given and the starter has not returned (#85).
        self.opening: Dict[IssueKey, int] = {}
        # The issue the box is open for, to take the instructions of a new coordination session (#85).
        self.instructing: Optional[IssueKey] = None
        self.instructed_by: Optional[str] = None  # the key, s or S, that opened the box for instructing (#85)
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
        # The workspaces read so far, by repository: where the issues of each are read with its gh-token.
        self.workspaces_by_repository: Dict[str, Workspace] = {}
        self.workspace_reader = workspace_reader or coordinator_workspace_entry
        self.placer = placer or pane_places
        # Where each tmux pane is ("2:1 autoflow-coordinator#59"), by pane id, as last read.
        self.places: Dict[str, str] = {}
        # The coordination session windows of each issue, as last found: an issue can have several (#85).
        self.windows_found: Dict[IssueKey, List[Window]] = {}
        # Each window's screen and when it was first seen, by window id.
        self.screens: Dict[str, Tuple[str, float]] = {}
        # The windows that showed a prompt at the last check that read their screen, by id (#79, #85).
        self.waiting_windows: Set[str] = set()
        # When an issue was last looked up and not found closed (found open, or the lookup failed), so it is not
        # looked up on every check.
        self.not_found_closed: Dict[IssueKey, float] = {}
        # When each window's issue was first found closed with no run, by window id, until a check finds it
        # otherwise: the wait to close the window counts from the later of this and its screen's last change.
        self.finished_since: Dict[str, float] = {}
        # The steps of each shown run, by run name, as last read: what a step change is compared against.
        self.step_marks: Dict[str, List[Tuple[str, str, Optional[str]]]] = {}
        self.current: Optional[Issue] = None
        # The coordination session whose row the cursor is on, under the current issue; None on the issue's row (#85).
        self.current_session: Optional[CoordinationSession] = None
        # (issue, pane, run name) of the one session the highlighted row shows, so a rebuild that changes the rows
        # finds it again; None on a workspace's row or the row of an issue with several sessions (#85).
        self.identity: Optional[Tuple[IssueKey, Optional[str], Optional[str]]] = None
        # What each row of the issues table is: its issue, and its session on a session's row.
        self.row_targets: Dict[str, Tuple[Issue, Optional[CoordinationSession]]] = {}
        # The name of the run the run pane shows: its cursor row is kept only while it is the same run (#85).
        self.shown_run_name: Optional[str] = None
        # The repository of the workspace row the cursor is on, when it is on one.
        self.on_header: Optional[str] = None
        # The row key of the issues table the cursor was last put on by render_issues, or last shown from a
        # RowHighlighted: another key under the cursor means a key moved it and its message is still queued (#16).
        self.placed: Optional[str] = None
        self.messages: Dict[str, str] = {}
        self.notice_sec = notice_sec
        # The timer that clears each notice slot.
        self.notice_timers: Dict[str, Timer] = {}
        # The issues table's rows as last built, by key (#86).
        self.issue_rows: List[Tuple[Optional[str], List[str]]] = []
        # Browser control (#86): the server while it runs, and whether it is being started or stopped.
        self.browser_port = browser_port
        self.browser = None
        self.browser_busy = False
        # Where the terminal's start and go-to messages go: its own status line (#86).
        self.terminal = TerminalReporter(self)

    def compose(self) -> ComposeResult:
        with Horizontal(id="panes"):
            with Vertical(id="issues-pane", classes="pane") as pane:
                pane.border_title = self.issues_title()
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
        yield Footer(compact=True)  # every key fits at 124 columns, the operator's terminal

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

    # Reading GitHub with each workspace's token (#74)

    async def token_of(self, repository: str) -> Optional[str]:
        """The gh-token of the workspace of `repository`, or None when there is no coordinator, no such workspace
        or no gh-token: then gh reads with the account of the shell the IDE was started from, as before."""
        if not self.coordinator:
            return None
        workspace = self.workspaces_by_repository.get(repository)
        if workspace is None:
            try:
                workspace = await self.workspace_reader(self.coordinator, repository)
            except StartError:
                return None
            self.workspaces_by_repository[repository] = workspace
        if workspace.folder is None:
            return None
        try:
            return read_token(workspace.folder)
        except TokenError as e:
            raise IssueSourceError(f"gh-token: {e}") from e

    async def gh_issues(self, repository: str, limit: int = 100) -> List[Issue]:
        return await gh_issues(repository, limit, token=await self.token_of(repository))

    async def gh_issue(self, repository: str, number: int) -> Issue:
        return await gh_issue(repository, number, token=await self.token_of(repository))

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
        self.show_found_failures()

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

    def cursor_key(self, table: DataTable) -> Optional[str]:
        return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value if table.row_count else None

    def follow_cursor(self) -> None:
        """Take the row a key moved the issues table's cursor to before its RowHighlighted is handled, so that a
        rebuild of the table does not move the cursor back (#16). A caller with an intent of its own (a run that
        starts, an issue found, a workspace folded) calls this first and then sets its intent, which wins."""
        key = self.cursor_key(self.query_one("#issues", DataTable))
        if self.placed is None or key is None or key == self.placed:
            return
        self.placed = key
        if self.every and self.group_of(key):
            self.on_header, self.current, self.current_session, self.identity = key, None, None, None
        elif key in self.row_targets:
            self.on_header = None
            self.current, self.current_session = self.row_targets[key]
            self.remember_shown(self.current, self.current_session)

    def waits(self, issue: Issue) -> bool:
        """Whether `issue` waits for the operator: one of its windows showed a prompt, or a work session of any of
        its runs, matched as finished() matches them, is in WAITING_INPUT (#79, #85)."""
        if any(w.id in self.waiting_windows for w in self.windows_found.get(issue_key(issue), [])):
            return True
        return any(s.state == WAITING_INPUT for r in self.issue_runs(issue) for s in r.sessions)

    def session_mark(self, session: CoordinationSession) -> Union[str, Text]:
        """The first cell of a coordination session's row: its window's prompt or its run's, then its run (#85)."""
        if ((session.window is not None and session.window.id in self.waiting_windows)
                or (session.run is not None and any(s.state == WAITING_INPUT for s in session.run.sessions))):
            return Text(WAIT_MARK, style="bold")
        return RUN_MARK if session.run else ""

    # The coordination sessions of an issue (#85)

    def issue_runs(self, issue: Issue, runs: Optional[List[Run]] = None) -> List[Run]:
        """The active runs of `issue` among `runs`, the last read by default; the given run of a given issue."""
        if not self.coordinator:
            return [issue.run] if issue.run else []
        return [r for r in (self.runs if runs is None else runs)
                if r.issue == str(issue.number) and self.run_repository(r) == issue.repository]

    def sessions(self, issue: Issue, windows: Optional[List[Window]] = None,
                 runs: Optional[List[Run]] = None) -> List[CoordinationSession]:
        """The sessions of `issue` from `windows` and `runs` when given, else from the last ones read (#85)."""
        key = issue_key(issue)
        return sessions_of(self.windows_found.get(key, []) if windows is None else windows,
                           self.issue_runs(issue, runs), self.started.get(key, []))

    def session_keys(self, issue: Issue, sessions: List[CoordinationSession]) -> List[str]:
        """The row key of each session: its issue's row key and its pane, with its run's name when the pane holds
        several runs; a run with no pane by its name."""
        panes = [s.pane for s in sessions]
        keys = []
        for session in sessions:
            part = session.pane if session.pane else session.run.name
            if session.pane and session.run and panes.count(session.pane) > 1:
                part = f"{session.pane}/{session.run.name}"
            keys.append(f"{self.row_key(issue)}/{part}")
        return keys

    def session_label(self, session: CoordinationSession) -> str:
        """The run's name once prepared; before, where its pane is in tmux (session:index), or the pane's id."""
        if session.run:
            return session.run.name
        place = self.places.get(session.pane or "")
        return place.split(" ")[0] if place else (session.pane or "")

    def remember_shown(self, issue: Optional[Issue], session: Optional[CoordinationSession]) -> None:
        """Keep which one session the highlighted row shows: the session of its row, or the one session of an
        issue's row."""
        if issue is not None and session is None:
            sessions = self.sessions(issue)
            session = sessions[0] if len(sessions) == 1 else None
        self.identity = ((issue_key(issue), session.pane, session.run.name if session.run else None)
                         if issue is not None and session is not None else None)

    def resolve_session(self, issue: Issue, sessions: List[CoordinationSession]) -> Optional[CoordinationSession]:
        """The session whose row the cursor goes on after the rows are made again: the one shown before, found by
        its pane and run, then by its run, then by its pane (the earliest of its runs); none when the issue has one
        session or none, when nothing matches, or when the issue's own row was shown with several sessions."""
        if len(sessions) < 2 or self.identity is None or self.identity[0] != issue_key(issue):
            return None
        _, pane, run = self.identity

        def name(session: CoordinationSession) -> Optional[str]:
            return session.run.name if session.run else None

        for matches in (lambda s: s.pane == pane and name(s) == run,
                        lambda s: run is not None and name(s) == run,
                        lambda s: pane is not None and s.pane == pane):
            found = next((s for s in sessions if matches(s)), None)
            if found is not None:
                return found
        return None

    def current_run(self) -> Optional[Run]:
        """The run the highlighted row shows: its session's, or an issue's with one session; none on the row of an
        issue with several sessions."""
        return self.run_of(self.current, self.current_session)

    def run_of(self, issue: Optional[Issue], session: Optional[CoordinationSession]) -> Optional[Run]:
        """The run the row of `issue` (and `session`, on a session's row) shows, for the terminal or a page (#86)."""
        if issue is None:
            return None
        if session is not None:
            return session.run
        sessions = self.sessions(issue)
        if len(sessions) > 1:
            return None
        return sessions[0].run if sessions else issue.run

    def parent_sessions(self) -> int:
        """The number of sessions of the highlighted issue when its own row, with several, is highlighted."""
        return self.several_of(self.current, self.current_session)

    def several_of(self, issue: Optional[Issue], session: Optional[CoordinationSession]) -> int:
        """The number of sessions of `issue` when its own row, with several, is the row shown (#86)."""
        if issue is None or session is not None:
            return 0
        count = len(self.sessions(issue))
        return count if count > 1 else 0

    def find_issue(self, key: IssueKey) -> Optional[Issue]:
        return next((i for i in self.issues if issue_key(i) == key), None)

    def sessions_shape(self) -> Tuple:
        """What the session rows are, to tell whether a read changed them."""
        return tuple((issue_key(i), tuple((s.pane, s.run.name if s.run else None, s.window.id if s.window else None)
                                          for s in self.sessions(i))) for i in self.issues)

    def mark(self, issue: Issue) -> Union[str, Text]:
        """The first cell of an issue's row (#79)."""
        if self.waits(issue):
            return Text(WAIT_MARK, style="bold")
        return RUN_MARK if issue.run else ""

    def render_issues(self) -> None:
        table = self.query_one("#issues", DataTable)
        self.follow_cursor()
        columns = EVERY_ISSUE_COLUMNS if self.every else ISSUE_COLUMNS
        # A label too long for the pane is cut in the middle, so runs whose names start alike stay apart (#85).
        room = flex_width(table.size.width, columns, table.cell_padding) - cell_len(SESSION_INDENT)
        rows: List[Tuple[Optional[str], List[Union[str, Text]]]] = []
        targets: Dict[str, Tuple[Issue, Optional[CoordinationSession]]] = {}
        headers = self.headers()
        for group in self.groups:
            issues = self.shown(group)
            if self.every:
                if group.repository not in headers:
                    continue
                folded = group.repository in self.folded and not self.running_only
                rows.append((group.repository, [FOLDED_MARK if folded else UNFOLDED_MARK,
                                                 Text(group_title(group, folded), style="bold")]))
            for issue in issues:
                key = self.row_key(issue)
                targets[key] = (issue, None)
                rows.append((key, [self.mark(issue), f"{issue.number}  {issue_title(issue)}"] if self.every
                             else [self.mark(issue), str(issue.number), issue_title(issue)]))
                sessions = self.sessions(issue)
                if len(sessions) < 2:
                    continue  # one session or none: the issue's row alone, as before (#85)
                for session, session_key in zip(sessions, self.session_keys(issue, sessions)):
                    targets[session_key] = (issue, session)
                    label = SESSION_INDENT + cut_middle(self.session_label(session), room)
                    rows.append((session_key, [self.session_mark(session), label] if self.every
                                 else [self.session_mark(session), "", label]))
        fill(table, columns, rows)
        self.row_targets = targets
        # As built, before the table cuts them to its width: a page has its own width (#86).
        self.issue_rows = [(key, [str(c) for c in cells]) for key, cells in rows]
        # With only running issues shown and none to show, the empty pane says why.
        none = self.running_only and not any(self.shown(g) for g in self.groups)
        if none or self.messages.get("running"):
            self.set_message("running", "no issue has a run" if none else "")
        current = self.current_in()
        if current:
            sessions = self.sessions(current)
            self.current_session = self.resolve_session(current, sessions)
            row = self.row_key(current)
            if self.current_session is not None:
                row = self.session_keys(current, sessions)[sessions.index(self.current_session)]
            table.move_cursor(row=table.get_row_index(row))
        elif headers:
            # On a workspace's row: the one the cursor was on, the folded one of the highlighted issue, or the first.
            for header in (self.on_header, self.current.repository if self.current else None, headers[0]):
                if header in headers:
                    self.on_header = header
                    table.move_cursor(row=table.get_row_index(header))
                    break
        self.placed = self.cursor_key(table)
        self.browser_changed()

    def action_refresh_issues(self) -> None:
        if self.static:
            return
        if self.coordinator:
            self.run_worker(self.load_base(), group="base", exclusive=True)
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
            return  # the app is closing: its widgets are gone
        await self.read_found()  # also when the list could not be read (#80)

    async def load_every(self) -> None:
        """Read the workspaces of the base again, then the most recent open issues of each."""
        try:
            workspaces = await self.workspaces_reader(self.coordinator)
        except StartError as e:
            self.set_message("issues", f"workspaces: {e}")
            await self.read_found()  # the workspaces as last read (#80)
            return
        groups: List[Group] = []
        for workspace in workspaces:
            if any(g.repository == workspace.github for g in groups):
                continue  # a second workspace of the same repository: the coordinator cannot tell them apart
            group = self.group_of(workspace.github) or Group(workspace.github)
            group.workspace = workspace
            groups.append(group)
            self.workspaces_by_repository[workspace.github] = workspace
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
                if self.creating.pop(group.repository, None) is not None:
                    self.remember(group.name, True)  # made from here: it opens unfolded
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
            return  # the app is closing: its widgets are gone
        await self.read_found()  # also for a workspace whose list could not be read (#80)

    # Issues found by their number, read again at the end of each read of the issues (#80)

    def held_found(self) -> List[Tuple[Group, Issue]]:
        """The found issues shown from the copy held when found: those whose number the open list of their
        workspace does not show. One the list shows is drawn from the list (#80)."""
        return [(g, i) for g in self.groups for i in g.extra
                if i.number not in {o.number for o in g.open_issues}]

    async def read_found(self) -> None:
        """Read the found issues the list just applied does not show again, one after another, in the worker
        that read the list: that work runs one at a time (group "issues", exclusive), so each issue has one read
        out and the results apply in the order they come back (#80)."""
        changed = False
        for group, held in self.held_found():
            key = issue_key(held)
            try:
                result: Union[Issue, IssueSourceError] = await self.lookup(group.repository, held.number)
            except IssueSourceError as e:
                result = e
            if not any(i is held for i in group.extra) or not any(g is group for g in self.groups):
                continue  # cleared by Esc or replaced by a new find while it was read: not brought back (#80)
            if isinstance(result, IssueSourceError):
                self.found_failures[key] = f"issue {self.label(key)}: {result}"  # it keeps what it had (#80)
                continue
            self.found_failures.pop(key, None)
            if (held.state, held.title, held.pull) != (result.state, result.title, result.pull):
                # In place, so the highlighted issue stays the same one and keeps its repository and run (#80).
                held.state, held.title, held.pull = result.state, result.title, result.pull
                changed = True
        try:
            if changed:
                self.show_issues()
            else:
                self.show_found_failures()
        except NoMatches:
            pass  # the app is closing: its widgets are gone

    def show_found_failures(self) -> None:
        """Put on the status line why found issues could not be read again, for those still shown from their
        held copy: the failure of one cleared by Esc, replaced by a new find, or now shown from the open list
        goes (#80)."""
        held = {issue_key(i) for _, i in self.held_found()}
        self.found_failures = {k: m for k, m in self.found_failures.items() if k in held}
        message = "; ".join(self.found_failures.values())
        if message != self.messages.get("found", ""):
            self.set_message("found", message)

    # Runs

    def refresh_runs(self) -> None:
        self.run_worker(self.load_runs(), group="runs", exclusive=True)

    async def load_runs(self) -> Optional[List[Run]]:
        """Read the active runs and show them; the runs read, or None when they could not be read (#85)."""
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
        if runs is not None and (any(r.coordination for r in self.own_runs()) or self.started or self.windows_found):
            await self.load_places()
        return runs

    async def load_places(self) -> None:
        """Where the panes are, for the coordination session of the shown run and the labels of sessions with no
        run yet; kept as last read on a failure."""
        try:
            places = await self.placer()
        except WindowError:
            return
        changed, self.places = places != self.places, places
        try:
            if changed and (self.started or self.windows_found):
                self.render_issues()
            self.render_info()
        except NoMatches:
            pass  # the app is closing: its widgets are gone

    async def load_base(self) -> None:
        try:
            self.base, self.base_error = await self.base_reader(self.coordinator), None
        except StartError as e:
            self.base, self.base_error = None, str(e)
        try:
            self.render_info()
        except NoMatches:
            pass  # the app is closing: its widgets are gone

    # The workspace of the one repository

    async def load_workspace(self) -> None:
        group = self.groups[0]
        try:
            group.workspace = await self.workspace_reader(self.coordinator, self.repository)
            self.workspaces_by_repository[self.repository] = group.workspace
        except StartError as e:
            group.workspace_error = str(e)
        try:
            self.render_info()
        except NoMatches:
            pass  # the app is closing: its widgets are gone

    def show_runs(self, runs: List[Run]) -> None:
        # Runs are told apart by name: an issue can have several, so one starting or ending is not seen from the
        # issues that have a run (#85).
        before = {r.name for r in self.own_runs()}
        self.follow_cursor()
        # Whether the operator looks at a run, as the rows were before this read.
        on_run = self.current is not None and self.current_run() is not None
        self.runs = runs
        self.attach_runs()
        own = self.own_runs()
        after = {r.name for r in own}
        started = [r for r in own if r.name not in before]
        if self.drop_prepared_opens():
            self.set_message("start", "")  # the coordination session prepared the run
        self.folded -= {self.run_repository(r) for r in started}  # a run that starts shows in its workspace
        if started and not on_run:
            # The operator is not looking at a run: move to the session whose run just started.
            target = next(((i, r) for i in self.ordered() for r in started
                           if r.issue == str(i.number) and self.run_repository(r) == i.repository), None)
            if target is not None:
                issue, run = target
                self.on_header, self.current, self.current_session = None, issue, None
                self.identity = (issue_key(issue), run.coordination, run.name)
        self.render_issues()
        self.show_issue(self.current_in())
        self.count_runs()
        if before - after or self.steps_changed():
            # A run ended, or a step of a run changed: a merge may have closed an issue in the meantime.
            self.action_refresh_issues()

    def drop_prepared_opens(self) -> bool:
        """Drop the opened panes a run of their issue names as its coordination surface: the run is that session
        now. Whether any was dropped."""
        dropped = False
        for key in list(self.started):
            named = {r.coordination for r in self.runs
                     if r.issue == str(key[1]) and self.run_repository(r) == key[0]}
            kept = [p for p in self.started[key] if p not in named]
            dropped = dropped or len(kept) < len(self.started[key])
            self.set_opened(key, kept)
        return dropped

    def set_opened(self, key: IssueKey, panes: List[str]) -> None:
        if panes:
            self.started[key] = panes
        else:
            self.started.pop(key, None)

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

    # Starting a run. Each check says why it refuses through the reporter of the side that asked: the terminal's
    # status line, or the asking browser page (#86).

    def can_start(self, issue: Issue, reporter: Optional["Reporter"] = None) -> bool:
        """Whether a coordination session can be opened for `issue`; says why not."""
        reporter = reporter or self.terminal
        key = issue_key(issue)
        if self.static or not self.coordinator or not issue.repository:
            reporter.say("start", "start: needs --coordinator and --repo or --workspace")
        elif issue.pull:
            reporter.say("start", f"{self.label(key)} is a pull request")
        elif issue.closed:
            reporter.say("start", f"{self.label(key)} is closed")
        else:
            return True
        return False

    def action_start_run(self) -> None:
        """`s`: open the issue's coordination session, or go back to the one it has (#85)."""
        issue = self.current
        if issue is None:
            return
        key = issue_key(issue)
        if self.current_session is not None:
            # A session's row: go to it, as Enter does, whatever the issue now is; the refusals are for opening (#85).
            self.run_worker(self.go_back(key, self.current_session), group="start-focus", exclusive=True)
        elif not self.can_start(issue):
            return
        elif self.opening.get(key):
            self.set_message("start", f"{self.label(key)}: opening the coordination session")
        else:
            self.run_worker(self.start_or_go_back(key), group="start-focus", exclusive=True)

    async def browser_start(self, row: str, reporter: "Reporter") -> None:
        """`s` from a browser page, on the row `row` it chose: the steps of action_start_run, in its order, told to
        that page; run in the worker group `browser`, beside the terminal's (#86)."""
        target = self.row_targets.get(row)
        if target is None:
            reporter.say("start", f"{row}: not a row of the issues")
            return
        issue, session = target
        key = issue_key(issue)
        if session is not None:
            await self.go_back(key, session, reporter)
        elif self.can_start(issue, reporter) and not self.open_in_progress(key, reporter):
            await self.start_or_go_back(key, reporter)

    def action_new_session(self) -> None:
        """`S`: open another coordination session for the issue, whatever sessions or runs it has (#85)."""
        issue = self.current
        if issue is not None and not self.box_taken("start", "S") and self.can_start(issue):
            self.ask_instructions(issue_key(issue), "S")

    def browser_new_session(self, row: str, reporter: "Reporter") -> None:
        """`S` from a browser page: its own instructions box opens, after action_new_session's checks (#86)."""
        target = self.row_targets.get(row)
        if target is None:
            reporter.say("start", f"{row}: not a row of the issues")
        elif self.can_start(target[0], reporter):
            reporter.ask(issue_key(target[0]), "S")

    async def start_or_go_back(self, key: IssueKey, reporter: Optional["Reporter"] = None) -> None:
        """Read the issue's windows again, so one the periodic check has not found yet is gone back to; open the
        instructions box when the issue still has no session; otherwise go back to its one session with no run, or
        say why not (#85)."""
        reporter = reporter or self.terminal
        why, windows = await self.read_windows_of(key)
        if self.windows_unread(key, why, reporter):
            return
        # What `s` checked before the read is checked again, as the issue now is: it may have closed, or an S
        # submitted during the read may be opening a session whose pane is not known yet (#85).
        issue = self.find_issue(key)
        if not self.still_startable(key, issue, reporter) or self.open_in_progress(key, reporter):
            return
        sessions = self.sessions(issue, windows)
        if not sessions:
            reporter.ask(key, "s")
        elif len(sessions) == 1 and not sessions[0].run:
            await self.go_back(key, sessions[0], reporter)
        else:
            self.say_sessions(key, sessions, reporter)

    def still_startable(self, key: IssueKey, issue: Optional[Issue], reporter: Optional["Reporter"] = None) -> bool:
        """Whether issue `key`, as it is listed now, can still have a coordination session opened; says why not
        (#85)."""
        reporter = reporter or self.terminal
        if issue is None:
            reporter.say("start", f"{self.label(key)} is no longer listed")
            return False
        return self.can_start(issue, reporter)

    def open_in_progress(self, key: IssueKey, reporter: Optional["Reporter"] = None) -> bool:
        """Whether a coordination session of issue `key` is being opened; says so (#85)."""
        if self.opening.get(key):
            (reporter or self.terminal).say("start", f"{self.label(key)}: opening the coordination session")
            return True
        return False

    def say_sessions(self, key: IssueKey, sessions: List[CoordinationSession],
                     reporter: Optional["Reporter"] = None) -> None:
        """Say why `s` opens no session for issue `key`, which has `sessions` (#85)."""
        reporter = reporter or self.terminal
        if len(sessions) > 1:
            reporter.say("start", f"{self.label(key)} has {len(sessions)} sessions: highlight one, or S for another")
        elif sessions[0].run:
            reporter.say("start", f"{self.label(key)} already has a run: {sessions[0].run.name}")
        else:
            reporter.say("start", f"{self.label(key)} has a session: s goes to it, or S for another")

    def decide_open(self, key: IssueKey, opener: Optional[str], text: str,
                    reporter: Optional["Reporter"] = None) -> None:
        """Enter in the instructions box of issue `key`, opened by `opener` (s or S): open its coordination session
        only when the state read now permits it. What can be told at once is checked here; the rest is read again
        in a worker, with the open marked as in progress from now on, so no other start comes between those reads
        and the open (#85). The box is the terminal's or a browser page's, and `reporter` the side's (#86)."""
        reporter = reporter or self.terminal
        issue = self.find_issue(key)
        if not self.still_startable(key, issue, reporter) or (opener == "s" and self.open_in_progress(key, reporter)):
            return
        self.opening[key] = self.opening.get(key, 0) + 1
        reporter.say("start", f"{self.label(key)}: checking before opening the coordination session")
        self.run_worker(self.open_if_still_startable(key, opener, text, reporter), group=f"start-{key[0]}-{key[1]}")

    async def open_if_still_startable(self, key: IssueKey, opener: Optional[str], text: str,
                                      reporter: Optional["Reporter"] = None) -> None:
        """Open the session decide_open marked as in progress, unless the state read now refuses it; the mark is
        dropped when nothing opens, and by start once the starter returns (#85)."""
        reporter = reporter or self.terminal
        opens = False
        try:
            opens = not await self.refuses_at_enter(key, opener, reporter)
        finally:
            if not opens:
                self.end_open(key)
        if opens:
            await self.start(key, text, reporter)

    async def refuses_at_enter(self, key: IssueKey, opener: Optional[str],
                               reporter: Optional["Reporter"] = None) -> bool:
        """Whether a session of issue `key` is refused, from its state read now; says why. It opens only when each
        input is known to permit it: the issue listed, and open and not a pull request as GitHub gives it now; for
        `s`, also its windows and the runs read again, no other open in progress and no session. One that cannot be
        read refuses (#85)."""
        reporter = reporter or self.terminal
        label = self.label(key)
        issue = self.find_issue(key)
        if not self.still_startable(key, issue, reporter):
            return True
        # Read every time: a row shows "open" until the next read of the list, which can be after it closed (#85).
        try:
            found = await self.lookup(*key)
        except IssueSourceError as e:
            reporter.say("start", f"{label}: nothing opened: its state could not be read: {e}")
            return True
        if key in self.lookups:
            self.lookups[key] = found
            self.show_issues()
        if found.pull:
            reporter.say("start", f"{label} is a pull request")
            return True
        if found.state != "open":
            reporter.say("start", f"{label} is {found.state or 'of unknown state'}")
            return True
        windows: Optional[List[Window]] = None
        runs: Optional[List[Run]] = None
        if opener == "s":
            why, windows = await self.read_windows_of(key)
            if self.windows_unread(key, why, reporter):
                return True
            runs = await self.load_runs()
            if runs is None:
                reporter.say("start", f"{label}: nothing opened: the runs could not be read")
                return True
        # Looked up again after the reads above, which a refresh of the list may have changed meanwhile (#85).
        issue = self.find_issue(key)
        if not self.still_startable(key, issue, reporter):
            return True
        if opener != "s":
            return False
        if self.opening.get(key, 0) > 1:  # an open started during the reads, besides this one (#85)
            reporter.say("start", f"{label}: opening the coordination session")
            return True
        # From the windows and runs read above, not the last ones shown: a periodic read that began before them
        # can end during the awaits above and show what was there before (#85).
        sessions = self.sessions(issue, windows, runs)
        if sessions:
            self.say_sessions(key, sessions, reporter)
            return True
        return False

    def windows_unread(self, key: IssueKey, why: Optional[str], reporter: Optional["Reporter"] = None) -> bool:
        """Whether the read of issue `key`'s windows failed, with `why`; says so (#85)."""
        if why is None or why == NO_WORKSPACE:
            return False
        (reporter or self.terminal).say("start", f"{self.label(key)}: nothing opened: its windows could not be read: {why}")
        return True

    async def read_windows_of(self, key: IssueKey) -> Tuple[Optional[str], List[Window]]:
        """The windows of issue `key`, read now and shown; kept as last found when they cannot be read. Returns
        what find_windows_of returns (#85)."""
        why, found = await self.find_windows_of(key)
        if why is not None:
            return why, found
        before = self.sessions_shape()
        self.set_windows(key, found)
        if self.sessions_shape() != before:
            self.render_issues()
            self.show_issue(self.current_in())
        return None, found

    async def find_windows_of(self, key: IssueKey) -> Tuple[Optional[str], List[Window]]:
        """The windows of issue `key`, read now; the windows are neither kept nor shown, though the repository's
        workspace is looked up and kept when it is not known yet. Returns why they were not read and the
        windows: None and the windows read; NO_WORKSPACE when the coordinator names no workspace for the
        repository (no window can be opened for it either: the starter, which looks for the workspace again, says
        why), with the windows last found; or else why tmux could not list them (#85, #86)."""
        last = list(self.windows_found.get(key, []))
        try:
            group = self.group_of(key[0])
            if self.every:
                if group is None or group.workspace is None:
                    return NO_WORKSPACE, last
                workspace = group.workspace.name
            else:
                if self.workspace is None:
                    self.workspace = await self.workspace_finder(self.coordinator, self.repository)
                workspace = self.workspace
        except StartError:
            return NO_WORKSPACE, last
        try:
            found = (await self.windows.find(workspace)).get(key[1], [])
        except WindowError as e:
            return str(e) or "tmux failed", last
        return None, found

    def set_windows(self, key: IssueKey, windows: List[Window]) -> None:
        """Make `windows` the found windows of issue `key`. An opened pane found in a window is kept while its
        window is; one whose window was found and is gone is dropped."""
        if windows:
            self.windows_found[key] = windows
        else:
            self.windows_found.pop(key, None)
        panes = {p for w in windows for p in w.panes}
        kept = []
        for pane in self.started.get(key, []):
            if pane in panes:
                self.opened_seen.add(pane)
            elif pane in self.opened_seen:
                self.opened_seen.discard(pane)
                continue
            kept.append(pane)
        self.set_opened(key, kept)

    def ask_instructions(self, key: IssueKey, opener: str) -> None:
        """Open the box for the instructions of a new coordination session of issue `key`, asked by `opener`, the
        key s or S; the caller has checked that the box is not open for another action (#85)."""
        self.box_for(instructing=key)
        self.instructed_by = opener
        box = self.query_one("#find", Input)
        box.border_title = f"Instructions for {self.label(key)}"
        box.placeholder = "focus or approach; Enter to start, Esc to cancel"
        box.restrict = None
        box.value = ""
        box.display = True
        box.focus()

    def end_open(self, key: IssueKey) -> None:
        """One open of issue `key` is no longer in progress."""
        self.opening[key] = self.opening.get(key, 1) - 1
        if not self.opening[key]:
            del self.opening[key]

    async def start(self, key: IssueKey, text: str = "", reporter: Optional["Reporter"] = None) -> None:
        """Open a coordination session with the instructions given; decide_open marked it as in progress, so `s`
        meanwhile opens no second one, and an open is not cancelled by a later one. From a browser page, its window
        opens detached and the terminal's cursor stays where it is (#86)."""
        reporter = reporter or self.terminal
        reporter.say("start", f"{self.label(key)}: opening the coordination session")
        try:
            if reporter.detached:
                pane = await self.starter(self.coordinator, *key, text, detached=True)
            else:
                pane = await self.starter(self.coordinator, *key, text)
        except StartError as e:
            reporter.say("start", f"start {self.label(key)}: {e}")
            return
        finally:
            self.end_open(key)
        self.started[key] = self.started.get(key, []) + [pane]
        reporter.say("start", f"{self.label(key)}: coordination session in {pane}")
        group = self.group_of(key[0])
        if self.every and group:
            self.remember(group.name, True)
        issue = self.find_issue(key)
        if issue is None:
            return
        try:
            if reporter.detached:
                # Its row is added; the terminal's cursor stays where it is (#86).
                self.render_issues()
                self.show_issue(self.current_in())
                return
            # Its row is added at once, with the cursor on it.
            self.follow_cursor()
            self.on_header, self.current, self.current_session = None, issue, None
            self.identity = (key, pane, None)
            self.render_issues()
            self.show_issue(self.current_in())
        except NoMatches:
            pass  # the app is closing: its widgets are gone

    def session_pane(self, session: CoordinationSession) -> Optional[str]:
        """The coordination pane of `session`: its run's, or the window's pane it was found in (#86)."""
        return session.run.coordination if session.run and session.run.coordination else session.pane

    async def go_back(self, key: IssueKey, session: CoordinationSession,
                      reporter: Optional["Reporter"] = None) -> None:
        """Go to the coordination pane of `session`: tmux focus moves to it for the terminal; a browser page opens
        its view (#86)."""
        reporter = reporter or self.terminal
        pane = self.session_pane(session)
        if not pane:
            reporter.say("start", f"{self.label(key)}: {session.run.name if session.run else 'session'}: no pane")
            return
        await reporter.go(key, pane)

    def pane_gone(self, key: IssueKey, pane: str, error: str, reporter: Optional["Reporter"] = None) -> None:
        """The coordination pane `pane` of issue `key` could not be reached, with `error`: an opened pane is no
        longer the issue's session, on both sides; say so to the side that went to it (#86)."""
        reporter = reporter or self.terminal
        if pane in self.started.get(key, []):
            self.set_opened(key, [p for p in self.started[key] if p != pane])
            reporter.say("start", f"{self.label(key)}: {pane}: {error}; press s to open it again")
            try:
                self.render_issues()
                self.show_issue(self.current_in())
            except NoMatches:
                pass  # the app is closing: its widgets are gone (#86)
        else:
            reporter.say("start", f"{self.label(key)}: {pane}: {error}")

    # Adding a workspace: a coordination session makes it, as one starts a run (#60)

    def action_add_workspace(self) -> None:
        if self.static or not self.coordinator:
            self.set_message("workspace", "add workspace: needs --coordinator")
            return
        if self.box_taken("workspace", "add workspace"):
            return
        self.box_for(adding=True)
        box = self.query_one("#find", Input)
        box.border_title = "New workspace"
        box.placeholder = "GitHub address"
        box.restrict = r"[A-Za-z0-9._/:@-]*"  # owner/name, the web address or a clone address (start.py)
        box.value = ""
        box.display = True
        box.focus()

    def add_workspace(self, address: str) -> None:
        repository = repository_of(address)
        if repository is None:
            self.set_message("workspace", f"add workspace: {address} is not a GitHub repository")
        elif repository in self.creating:
            self.run_worker(self.focus_creating(repository), group="workspace", exclusive=True)
        else:
            self.run_worker(self.create(repository, address), group="workspace", exclusive=True)

    async def create(self, repository: str, address: str) -> None:
        self.set_message("workspace", f"workspace for {repository}: opening the coordination session")
        try:
            pane = await self.workspace_creator(self.coordinator, address)
        except StartError as e:
            self.set_message("workspace", f"add workspace: {e}")
            return
        self.creating[repository] = pane
        self.set_message("workspace", f"workspace for {repository}: coordination session in {pane}")

    async def focus_creating(self, repository: str) -> None:
        pane = self.creating[repository]
        try:
            await self.focuser(pane)
        except FocusError as e:
            del self.creating[repository]
            self.set_message("workspace", f"workspace for {repository}: {pane}: {e}; press w to open it again")
            return
        self.set_message("workspace", f"workspace for {repository}: coordination session in {pane}")

    # Closing the coordination session of a finished issue

    def check_windows(self) -> None:
        self.run_worker(self.check_windows_once(), group="windows", exclusive=True)

    async def check_windows_once(self) -> None:
        try:
            await self.close_finished_windows()
        except NoMatches:
            pass  # the app is closing: its widgets are gone

    async def close_finished_windows(self) -> None:
        """Close each window of every issue that is closed and has no run, each on its own: window_idle_sec after
        the later of that window's last screen change and the first check that found the issue closed with no run,
        so a session waiting when its issue closed keeps its window for the whole wait. GitHub is asked
        again just before the window is closed, so an issue reopened during the wait keeps it. One issue can take
        several runs, so an open issue keeps its window after a run ends; a merge closes the issue before the run
        ends, so a run keeps it too."""
        # Every window of each issue, by window id: each is watched and closed on its own (#85).
        found: Dict[str, Tuple[IssueKey, Window]] = {}
        try:
            if self.every:
                places = [(g.workspace.name, g.repository) for g in self.groups if g.workspace]
            else:
                if self.workspace is None:
                    self.workspace = await self.workspace_finder(self.coordinator, self.repository)
                places = [(self.workspace, self.repository)]
            for workspace, repository in places:
                for number, windows in (await self.windows.find(workspace)).items():
                    for window in windows:
                        found[window.id] = ((repository, number), window)
        except (StartError, WindowError) as e:
            self.set_message("windows", f"windows: {e}")
            return
        before = self.sessions_shape()
        by_issue: Dict[IssueKey, List[Window]] = {}
        for key, window in found.values():
            by_issue.setdefault(key, []).append(window)
        for key in set(self.windows_found) | set(by_issue) | set(self.started):
            self.set_windows(key, by_issue.get(key, []))
        now = time.monotonic()
        self.screens = {k: s for k, s in self.screens.items() if k in found}
        self.finished_since = {k: t for k, t in self.finished_since.items() if k in found}
        # Every screen first, each pane captured once, so the marks do not wait for the lookups below (#79).
        panes: Dict[str, List[str]] = {}
        for window_id, (key, window) in found.items():
            try:
                panes[window_id] = await self.windows.screens(window)
            except WindowError as e:
                self.set_message("windows", f"{window.name}: {e}")
        self.mark_waiting(found, panes, self.sessions_shape() != before)
        for window_id, (key, window) in found.items():
            if window_id not in panes:
                continue
            screen = "\n".join(panes[window_id])
            seen = self.screens.get(window_id)
            if seen is None or seen[0] != screen:
                self.screens[window_id] = seen = (screen, now)
            since = self.finished_since.get(window_id)
            if not await self.finished(key, now, ask=since is None):
                self.finished_since.pop(window_id, None)
                continue
            if since is None:
                # When the lookup returned, not when the check began: a slow lookup does not use up the wait.
                self.finished_since[window_id] = since = time.monotonic()
            if now - max(seen[1], since) < self.window_idle_sec:
                continue
            if not await self.finished(key, now, ask=True):
                # Reopened during the wait, or not known: the wait starts again once it is found closed.
                self.finished_since.pop(window_id, None)
                continue
            try:
                await self.windows.close(window)
            except WindowError as e:
                self.set_message("windows", f"{window.name}: {e}")
                continue
            del self.screens[window_id]
            del self.finished_since[window_id]
            self.waiting_windows.discard(window_id)
            self.set_windows(key, [w for w in self.windows_found.get(key, []) if w.id != window_id])
            self.set_opened(key, [p for p in self.started.get(key, []) if p not in window.panes])
            self.set_message("windows", f"closed {window.name}: #{key[1]} is closed and has no run")
            self.render_issues()
            self.show_issue(self.current_in())

    def mark_waiting(self, found: Dict[str, Tuple[IssueKey, Window]], panes: Dict[str, List[str]],
                     changed: bool = False) -> None:
        """Keep the windows that show a prompt in any of their panes, and redraw the issues when they changed, or
        when `changed` (the sessions did). A window whose screen could not be read keeps its last judgment until a
        check reads it; one no longer found is dropped (#79)."""
        shown = set()
        for window_id in found:
            if window_id in panes:
                if any(waiting(screen) for screen in panes[window_id]):
                    shown.add(window_id)
            elif window_id in self.waiting_windows:
                shown.add(window_id)
        if shown != self.waiting_windows or changed:
            self.waiting_windows = shown
            self.render_issues()
            if changed:
                self.show_issue(self.current_in())

    async def finished(self, key: IssueKey, now: float, ask: bool = True) -> bool:
        """Whether issue `key` is closed and has no active run. Closed is asked of GitHub, not taken from
        the issue missing from the open list; without `ask`, an earlier closed answer stands and GitHub is not
        asked. An issue found open, or whose lookup failed, is asked again after window_idle_sec, so a window
        is looked up at most about once per window_idle_sec while GitHub says open or cannot be reached."""
        repository, number = key
        group = self.group_of(repository)
        if group is None or not (group.listed and self.runs_read):
            return False
        if (any(r.issue == str(number) and self.run_repository(r) == repository for r in self.runs)
                or any(i.number == number for i in group.open_issues)):
            return False
        if not ask:
            return True
        asked = self.not_found_closed.get(key)
        if asked is not None and now - asked < self.window_idle_sec:
            return False
        try:
            issue = await self.lookup(repository, number)
        except IssueSourceError as e:
            self.set_message("windows", f"issue {self.label(key)}: {e}")
            self.not_found_closed[key] = now
            return False
        if not issue.closed:
            self.not_found_closed[key] = now
        return issue.closed

    # Selection

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id != "issues" or event.row_key.value is None:
            return
        if event.cursor_row != event.data_table.cursor_row:
            # From before the table was rebuilt (render_issues clears it, which puts the cursor on row 0, then
            # moves it back): showing that row would empty the run pane and lose its highlighted step (#65).
            return
        key = event.row_key.value
        self.placed = key
        try:
            if self.every and self.group_of(key):
                self.on_header = key
                self.show_issue(None)
                return
            issue, session = self.row_targets.get(key, (None, None))
            self.show_issue(issue, session)
        except NoMatches:
            pass  # the app is closing: its widgets are gone (#16)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "issues" and self.every and self.group_of(event.row_key.value):
            if not self.running_only:
                self.fold(event.row_key.value)
        elif event.data_table.id == "issues":
            issue, session = self.row_targets.get(event.row_key.value, (None, None))
            if issue is not None and session is not None:
                # A coordination session's row: tmux focus goes to its coordination pane (#85).
                self.run_worker(self.go_back(issue_key(issue), session), group="focus", exclusive=True)
        elif event.data_table.id == "sessions" and event.row_key.value is not None:
            self.run_worker(self.focus_session(event.row_key.value), group="focus", exclusive=True)
        elif event.data_table.id == "run":
            run = self.current_run()
            steps = run.steps if run else []
            if 0 <= event.cursor_row < len(steps):
                self.push_screen(StepDetail(steps[event.cursor_row], event.cursor_row + 1), self.go_to_step)

    # Finding an issue by its number, in the highlighted workspace (searching by words is left for later, #52)

    def action_find(self) -> None:
        group = self.current_group()
        if self.static or group is None or not group.repository:
            self.set_message("find", "find: no repository to look in")
            return
        if self.box_taken("find", "find"):
            return
        self.box_for(finding=group.repository)
        box = self.query_one("#find", Input)
        box.border_title = ""
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

    def box_for(self, finding: Optional[str] = None, adding: bool = False,
                instructing: Optional[IssueKey] = None) -> None:
        """Set what the shared box is open for, one purpose, so an Enter in it does what its opener asked (#85)."""
        self.finding, self.adding, self.instructing = finding, adding, instructing
        self.instructed_by = None

    def box_taken(self, slot: str, name: str) -> bool:
        """Whether the shared box is open, for the one purpose and issue its opener set; then the action `name`
        does not open it, so as not to retitle, clear or take over that box, and the status line says why (#85)."""
        if not self.query_one("#find", Input).display:
            return False
        if self.instructing is not None:
            purpose = f"the instructions of {self.label(self.instructing)}"
        elif self.adding:
            purpose = "a new workspace"
        else:
            purpose = "a find"
        self.set_message(slot, f"{name}: the box is open for {purpose}; Enter or Esc closes it first")
        return True

    def end_find(self) -> None:
        self.box_for()
        self.query_one("#find", Input).display = False
        self.query_one("#issues", DataTable).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "find" and self.instructing is not None:
            # Whitespace as a plain space, other control and non-printable characters dropped, outer blanks removed,
            # the rest as typed; none, or only blanks, starts it as before. The issue may have closed, or gained a
            # session, while the box was open: the open is decided from its state read now (#85).
            key, opener, text = self.instructing, self.instructed_by, printable(event.value).strip()
            self.end_find()
            self.decide_open(key, opener, text)
            return
        if event.input.id == "find" and self.adding:
            text = event.value.strip()
            self.end_find()
            if text:
                self.add_workspace(text)
            return
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
            self.found_failures.pop(key, None)  # a new find replaces it, and what its read said (#80)
        self.set_message("find", "")
        self.show_found(group, found)

    def show_found(self, group: Group, issue: Issue) -> None:
        """Show `group` unfolded, in every mode, with the cursor on `issue`."""
        if self.running_only:
            self.action_toggle_running()
        self.folded.discard(group.repository)
        self.follow_cursor()
        self.on_header = None
        self.current, self.current_session, self.identity = issue, None, None
        self.show_issues()

    def action_toggle_running(self) -> None:
        """Show only the issues with a run, or all again."""
        self.running_only = not self.running_only
        self.query_one("#issues-pane").border_title = self.issues_title()
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
        self.follow_cursor()
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
        run = self.current_run()
        steps = run.steps if run else []
        index = next((n for n, s in enumerate(steps) if s.label == label), None)
        if index is not None:
            table = self.query_one("#run", DataTable)
            table.focus()
            table.move_cursor(row=index)

    async def focus_session(self, key: str) -> None:
        run = self.current_run()
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

    def show_issue(self, issue: Optional[Issue], session=KEEP) -> None:
        """Show `issue`, and `session` of it on a session's row; by default the session shown stays while the issue
        does."""
        if session is KEEP:
            same = issue is not None and self.current is not None and issue_key(issue) == issue_key(self.current)
            session = self.current_session if same else None
        self.current, self.current_session = issue, session
        if issue is not None:
            self.on_header = None
        self.remember_shown(issue, session)
        run_table = self.query_one("#run", DataTable)
        sessions = self.query_one("#sessions", DataTable)
        run = self.current_run()
        # The cursor rows are kept while the same run is shown; another run starts at the top (#85).
        same_run = run is not None and run.name == self.shown_run_name
        self.shown_run_name = run.name if run else None
        run_row, session_row = (run_table.cursor_row, sessions.cursor_row) if same_run else (0, 0)
        fill(run_table, RUN_COLUMNS, ((None, cells) for cells in step_cells(run)))
        fill(sessions, SESSION_COLUMNS,
             ((s.name, cells) for s, cells in zip(run.sessions if run else [], session_cells(run))))
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
        info = self.query_one("#info", Static)
        lines = self.info_for(self.current, self.current_session, self.current_group())
        info.update(info_text(lines, info.content_size.width))
        self.browser_changed()

    def info_for(self, issue: Optional[Issue], session: Optional[CoordinationSession],
                 group: Optional[Group]) -> List[Tuple[str, str]]:
        """The info lines of the row of `issue` and `session` in `group`, for the terminal or a page (#86)."""
        run = self.run_of(issue, session)
        base = self.base or self.base_error
        several = self.several_of(issue, session)
        pane = session.pane if session is not None else None
        if pane is None and issue is not None and not several:
            sessions = self.sessions(issue)
            pane = sessions[0].pane if sessions else None
        extra = dict(sessions=several, pane=pane)
        return (info_lines(group.repository, group.workspace, group.workspace_error, run, self.places, base, **extra)
                if group else info_lines(None, None, None, run, self.places, base, **extra))

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
        if slot in NOTICE_SLOTS:
            timer = self.notice_timers.pop(slot, None)
            if timer is not None:
                timer.stop()
            if message:
                self.notice_timers[slot] = self.set_timer(self.notice_sec, lambda: self.clear_notice(slot, message))
        self.update_status()

    def clear_notice(self, slot: str, message: str) -> None:
        """Clear notice `slot` if it still holds `message`."""
        if self.messages.get(slot) == message:
            self.notice_timers.pop(slot, None)
            self.set_message(slot, "")

    def update_status(self) -> None:
        issue, run = self.current, self.current_run()
        group = self.current_group()
        kind = issue_kind(issue) if issue else ""
        several = self.parent_sessions()
        parts = [self.messages[s] for s in NOTICE_SLOTS if self.messages.get(s)]
        parts += [(group.repository if group else self.repository) or ("every workspace" if self.every else "(no repository)"),
                  (f"#{issue.number}" + (f" ({kind})" if kind else "")) if issue else "no issue",
                  f"{several} sessions" if several else run.name if run else "no run"]
        parts += [m for s, m in self.messages.items() if m and s not in NOTICE_SLOTS and s != "browser"]
        browser = self.messages.get("browser")
        # Built as Text, never as markup, so no message text (from gh, tmux, a window name) is styled or becomes a
        # click action; only the IDE's own `open` carries one (#89).
        line = Text(no_wrap=True, overflow="ellipsis")
        try:
            status = self.query_one("#status", Static)
            if browser:
                # Browser control's address, or why it could not start, on a row of its own above the rest, so a
                # notice or the rest of the line never pushes it out of a 124-column screen (#86). While it serves,
                # where `open` is offered, the row starts with it, so a narrow screen cuts the address before it (#89).
                status.add_class("with-browser")
                if (self.browser is not None and browser == f"browser {self.browser.url}"
                        and page_opener_offered()):
                    line.append("open", Style(bold=True, underline=True) + Style.from_meta({"@click": "app.open_page"}))
                    line.append("  ")
                line.append(browser)
                line.append("\n")
            else:
                status.remove_class("with-browser")
            line.append("  |  ".join(parts))
            status.update(line)
        except NoMatches:
            pass  # the app is closing (a timer can fire meanwhile): its widgets are gone

    def issues_title(self) -> str:
        """The left pane's title: it lists workspaces when it shows every workspace (#71)."""
        title = "Workspaces" if self.every else "Issues"
        return f"{title} · running" if self.running_only else title

    def check_action(self, action: str, parameters: Tuple[object, ...]) -> Optional[bool]:
        """Of the two bindings of `1`, the one that names what the left pane holds is shown and runs."""
        if action == "focus_pane" and parameters and parameters[0] == "issues":
            return ("every" in parameters[1:]) == self.every
        return True

    def action_focus_pane(self, pane: str, mode: str = "") -> None:
        self.query_one(f"#{pane}", DataTable).focus()

    # Browser control (#86): a server on localhost, in this process, that shows the screen to a browser page and
    # acts through the same code as the keys. Its status line slot keeps the address while it runs. (#86)

    def action_toggle_browser(self) -> None:
        """`b`: start browser control, or stop it."""
        if not self.browser_busy:
            self.browser_busy = True
            self.run_worker(self.toggle_browser(), group="server-control")

    async def toggle_browser(self) -> None:
        try:
            if self.browser is not None:
                await self.stop_browser()
            else:
                await self.start_browser()
        finally:
            self.browser_busy = False

    async def start_browser(self) -> None:
        if sys.version_info < BROWSER_PYTHON:
            # The aiohttp releases for older Pythons have known flaws; the extra installs none there (#86).
            self.set_message("browser", "browser control needs Python 3.10 or later")
            return
        try:
            from autoflow_ide.browser import BrowserServer  # only browser control needs aiohttp (#86)
        except ImportError:
            self.set_message("browser", "browser control needs aiohttp: pip install -e '.[browser]'")
            return
        server = BrowserServer(self, self.browser_port)
        try:
            await server.start()
        except OSError as e:
            self.set_message("browser", f"browser control: {e}")
            return
        self.browser = server
        self.set_message("browser", f"browser {server.url}")

    async def stop_browser(self) -> None:
        # The address and `open` go as the stop begins, which can wait for an input under way (#89).
        server, self.browser = self.browser, None
        self.set_message("browser", "")
        if server is not None:
            await server.stop()

    def action_open_page(self) -> None:
        """A click on `open`: the browser page's address, as it is now, to the system's opener (#89)."""
        if self.browser is None or not page_opener_offered():
            return
        script = open_script(self.browser.url)
        try:
            child = subprocess.Popen(list(OPEN_COMMAND), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            self.set_message("open", OPEN_FAILED)
            return
        try:
            child.stdin.write(script.encode())
            child.stdin.close()
        except OSError:
            pass  # the child ended already: its exit status says how (#89)
        self.run_worker(self.watch_opener(child), group="open")

    async def watch_opener(self, child: subprocess.Popen) -> None:
        """Say so when the opener fails within OPEN_WAIT_SEC; neither wait for it longer nor stop it (#89)."""
        end = time.monotonic() + OPEN_WAIT_SEC
        while child.poll() is None and time.monotonic() < end:
            await asyncio.sleep(OPEN_POLL_SEC)
        if child.returncode not in (None, 0):
            self.set_message("open", OPEN_FAILED)

    async def action_quit(self) -> None:
        await self.stop_browser()
        self.exit()

    async def on_unmount(self) -> None:
        await self.stop_browser()  # also when the IDE ends some other way (#86)

    def get_system_commands(self, screen):
        yield from super().get_system_commands(screen)
        if self.browser is not None:
            yield SystemCommand("Browser control: stop", "Stop the browser page's server (b)", self.action_toggle_browser)
        else:
            yield SystemCommand("Browser control: start", "Serve this screen to a browser page on localhost (b)",
                                self.action_toggle_browser)

    def browser_changed(self) -> None:
        """The screen changed: the pages get it at the same read (#86)."""
        if self.browser is not None:
            self.browser.changed()

    def snapshot(self, chosen: Optional[str] = None) -> dict:
        """What a page shows: the issues table's rows as the terminal builds them, and for the row the page chose,
        what the info, run and sessions panes show for it (#86)."""
        rows = []
        for key, cells in self.issue_rows:
            issue = self.row_targets.get(key, (None, None))[0]
            rows.append({"key": key, "cells": cells, "repository": issue.repository if issue else None,
                         "number": issue.number if issue else None})
        return {"issues": rows, "chosen": self.chosen_view(chosen)}

    def chosen_view(self, row: Optional[str]) -> dict:
        issue, session, group = None, None, None
        if row is not None and self.every and self.group_of(row):
            group = self.group_of(row)
        elif row is not None:
            issue, session = self.row_targets.get(row, (None, None))
            group = self.group_of(issue.repository) if issue else None
        if row is None or (issue is None and group is None):
            return {"key": row, "info": [], "steps": [], "sessions": [], "run": None}
        run = self.run_of(issue, session)
        return {"key": row, "info": [list(line) for line in self.info_for(issue, session, group)],
                "steps": step_cells(run), "sessions": session_cells(run), "run": run.name if run else None}

    def coordination_target(self, row: str, windows: Optional[List[Window]] = None
                            ) -> Tuple[Optional[IssueKey], Optional[str], Optional[str]]:
        """The issue and coordination pane the row `row` names, or why it names none: a session's row, or the row
        of an issue with one session, its sessions from `windows` when given, else from the last ones found (#86)."""
        target = self.row_targets.get(row)
        if target is None:
            return None, None, f"{row}: not a row of the issues"
        issue, session = target
        key = issue_key(issue)
        if session is None:
            sessions = self.sessions(issue, windows)
            if not sessions:
                return key, None, f"{self.label(key)} has no session"
            if len(sessions) > 1:
                return key, None, f"{self.label(key)} has {len(sessions)} sessions: choose one"
            session = sessions[0]
        pane = self.session_pane(session)
        if not pane:
            return key, None, f"{self.label(key)}: {session.run.name if session.run else 'session'}: no pane"
        return key, pane, None


INFO_LABEL_WIDTH = 10


def info_lines(repository: Optional[str], workspace: Optional[Workspace], workspace_error: Optional[str],
               run: Optional[Run], places: Dict[str, str],
               base: Union[Setting, str, None] = None, sessions: int = 0,
               pane: Optional[str] = None) -> List[Tuple[str, str]]:
    """(label, value) lines for the info above the run pane. The base comes from the coordinator's config.py (a
    Setting, or why it could not be read); the workspace lines from repo.json; the run lines from status.py (its
    worktree and record folder), the coordination session's pane placed in tmux when
    it is found there. With `sessions` (an issue's row with several), their number in place of the run lines; with
    no run, the session's `pane` (#85)."""
    lines = [("repo", repository or "(no repository)")]
    if isinstance(base, Setting):
        lines.append(("base", f"{home_short(base.value)} ({setting_source(base.source)})"))
    elif base:
        lines.append(("base", base))
    if workspace:
        lines.append(("workspace", workspace.name))
        if workspace.clone:
            lines.append(("clone", home_short(workspace.clone)
                          + (f" ({workspace.base_branch})" if workspace.base_branch else "")))
    elif workspace_error:
        lines.append(("workspace", workspace_error))
    if sessions:
        lines.append(("sessions", str(sessions)))
        return lines
    if not run:
        lines.append(("run", "none"))
        if pane:
            place = places.get(pane)
            lines.append(("session", f"tmux {place}" if place else pane))
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
CUT_MIDDLE = ("run", "base", "clone", "worktree", "records")
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


def issue_kind(issue: Issue) -> str:
    """What the number is when it is not an open issue: a number found by `#` can be a closed issue or a pull
    request."""
    return ("closed PR" if issue.closed else "PR") if issue.pull else ("closed" if issue.closed else "")


def step_cells(run: Optional[Run]) -> List[List[str]]:
    """The run pane's cells of each step of `run` (#86)."""
    return [[str(n), s.shown, s.session or "", s.state, s.verdict or ""] for n, s in enumerate(run.steps if run else [], 1)]


def session_cells(run: Optional[Run]) -> List[List[str]]:
    """The sessions pane's cells of each work session of `run` (#86)."""
    return [[s.name, s.state] for s in (run.sessions if run else [])]


class Reporter:
    """Where the start and go-to checks say what they found: the terminal's status line, or one browser page
    (#86). `detached`: a session opened for it opens without moving tmux or the terminal's cursor."""
    detached = False

    def say(self, slot: str, text: str) -> None:
        raise NotImplementedError

    def ask(self, key: IssueKey, opener: str) -> None:
        """Open this side's instructions box for a new session of issue `key`, asked by `opener` (s or S)."""
        raise NotImplementedError

    async def go(self, key: IssueKey, pane: str) -> None:
        """Go to the coordination pane `pane` of issue `key`."""
        raise NotImplementedError


class TerminalReporter(Reporter):
    """The terminal's side: its status line slots, its box, and tmux focus (#86)."""

    def __init__(self, app: "IdeApp"):
        self.app = app

    def say(self, slot: str, text: str) -> None:
        self.app.set_message(slot, text)

    def ask(self, key: IssueKey, opener: str) -> None:
        if not self.app.box_taken("start", opener):  # a box opened during the read is left as it is (#85)
            self.app.ask_instructions(key, opener)

    async def go(self, key: IssueKey, pane: str) -> None:
        try:
            await self.app.focuser(pane)
        except FocusError as e:
            self.app.pane_gone(key, pane, str(e), self)
            return
        self.app.set_message("start", f"{self.app.label(key)}: coordination session in {pane}")


def issue_title(issue: Issue) -> str:
    kind = issue_kind(issue)
    return f"({kind}) {issue.title}".rstrip() if kind else issue.title


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(prog="autoflow-ide", description="An issue-based terminal IDE for AutoFlow")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--repo", metavar="OWNER/NAME", help="the GitHub repository whose issues to list")
    source.add_argument("--workspace", metavar="DIR", help="a coordinator workspace; its repo.json names the repository")
    source.add_argument("--demo", action="store_true", help="show placeholder data")
    parser.add_argument("--coordinator", metavar="DIR", help="the autoflow-coordinator clone whose runs to show")
    parser.add_argument("--browser-port", metavar="N", type=int, default=0,
                        help="the localhost port browser control (b) listens on; 0, the default, lets the system pick")
    args = parser.parse_args(argv)
    if args.demo:
        IdeApp(issues=placeholder_issues(), repository="(demo)", version=ide_version(),
               browser_port=args.browser_port).run()
        return
    repository = args.repo
    if args.workspace:
        try:
            repository = repository_from_workspace(args.workspace)
        except IssueSourceError as e:
            parser.error(str(e))
    IdeApp(repository=repository, coordinator=args.coordinator, version=ide_version(), state_file=state_path(),
           browser_port=args.browser_port).run()


if __name__ == "__main__":
    main()
