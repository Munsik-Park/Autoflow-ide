"""The IDE screen: issues, the selected issue's run, and its sessions."""
import argparse
from typing import Awaitable, Callable, Dict, List, Optional

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.widgets import DataTable, Footer, Static

from autoflow_ide.focus import FocusError, focus_surface
from autoflow_ide.issues import IssueSourceError, gh_issue, gh_issues, repository_from_workspace
from autoflow_ide.model import Issue, Run, placeholder_issues
from autoflow_ide.runs import RunSourceError, coordinator_runs, version_notice
from autoflow_ide.start import StartError, start_run
from autoflow_ide.tables import ColumnSpec, fill

IssueLoader = Callable[[str], Awaitable[List[Issue]]]
IssueLookup = Callable[[str, int], Awaitable[Issue]]
RunLoader = Callable[[str], Awaitable[List[Run]]]
Focuser = Callable[[str], Awaitable[None]]
Starter = Callable[[str, str, int], Awaitable[str]]
RUN_REFRESH_SEC = 5.0
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
                 starter: Optional[Starter] = None):
        """With `issues`, show them as given. Otherwise load the open issues of `repository` with `loader`.
        With `coordinator` (a coordinator clone), read its active runs every `run_refresh_sec` seconds.
        An issue that has a run but is not in the open list is looked up with `lookup`.
        `starter` opens the coordination session for an issue (see start.py)."""
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
        self.runs: List[Run] = []
        self.current: Optional[Issue] = None
        self.messages: Dict[str, str] = {}

    def compose(self) -> ComposeResult:
        with Horizontal(id="panes"):
            with Vertical(id="issues-pane", classes="pane") as pane:
                pane.border_title = "Issues"
                yield DataTable(id="issues", cursor_type="row")
            with Vertical(id="run-pane", classes="pane") as pane:
                pane.border_title = "Run"
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
        if self.coordinator:
            self.set_message("version", version_notice(self.coordinator))
            self.refresh_runs()
            self.set_interval(self.run_refresh_sec, self.refresh_runs)
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
        try:
            if runs is None:
                self.set_message("runs", error)
            else:
                self.show_runs(runs)
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
        if before - after:
            # A run ended: its issue may have been closed by the merge that ended it.
            self.action_refresh_issues()

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

    # Selection

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id != "issues" or event.row_key.value is None:
            return
        number = int(event.row_key.value)
        self.show_issue(next((i for i in self.issues if i.number == number), None))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "sessions" and event.row_key.value is not None:
            self.run_worker(self.focus_session(event.row_key.value), group="focus", exclusive=True)

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
             ((None, [str(n), s.name, s.session or "", s.state, s.verdict or ""]) for n, s in enumerate(steps, 1)))
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
