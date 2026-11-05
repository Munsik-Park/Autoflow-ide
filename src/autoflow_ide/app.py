"""The IDE screen: issues, the selected issue's run, and its sessions."""
import argparse
from typing import Awaitable, Callable, Dict, List, Optional

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.widgets import DataTable, Footer, Static

from autoflow_ide.focus import FocusError, focus_surface
from autoflow_ide.issues import IssueSourceError, gh_issues, repository_from_workspace
from autoflow_ide.model import Issue, Run, placeholder_issues
from autoflow_ide.runs import RunSourceError, coordinator_runs, version_notice
from autoflow_ide.tables import ColumnSpec, fill

IssueLoader = Callable[[str], Awaitable[List[Issue]]]
RunLoader = Callable[[str], Awaitable[List[Run]]]
Focuser = Callable[[str], Awaitable[None]]
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
        ("q", "quit", "Quit"),
    ]

    def __init__(self, issues: Optional[List[Issue]] = None, repository: Optional[str] = None,
                 loader: Optional[IssueLoader] = None, coordinator: Optional[str] = None,
                 run_loader: Optional[RunLoader] = None, focuser: Optional[Focuser] = None,
                 run_refresh_sec: float = RUN_REFRESH_SEC):
        """With `issues`, show them as given. Otherwise load the open issues of `repository` with `loader`.
        With `coordinator` (a coordinator clone), read its active runs every `run_refresh_sec` seconds."""
        super().__init__()
        self.issues: List[Issue] = list(issues) if issues is not None else []
        self.static = issues is not None
        self.repository = repository
        self.loader = loader or gh_issues
        self.coordinator = coordinator
        self.run_loader = run_loader or coordinator_runs
        self.focuser = focuser or focus_surface
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
        self.issues = issues
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
             ((str(i.number), [RUN_MARK if i.run else "", str(i.number), i.title]) for i in ordered))
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
        """Each issue gets the first of its runs; status.py lists the most recently changed run first."""
        if not self.coordinator:
            return
        runs = self.own_runs()
        for issue in self.issues:
            issue.run = next((r for r in runs if r.issue == str(issue.number)), None)

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
                 f"#{issue.number}" if issue else "no issue",
                 run.name if run else "no run"]
        parts += [m for m in self.messages.values() if m]
        self.query_one("#status", Static).update("  |  ".join(parts))

    def action_focus_pane(self, pane: str) -> None:
        self.query_one(f"#{pane}", DataTable).focus()


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
