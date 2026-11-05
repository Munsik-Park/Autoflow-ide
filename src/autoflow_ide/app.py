"""The IDE screen: issues, the selected issue's run, and its sessions."""
import argparse
from typing import Awaitable, Callable, Dict, List, Optional

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Static

from autoflow_ide.focus import FocusError, focus_surface
from autoflow_ide.issues import IssueSourceError, gh_issues, repository_from_workspace
from autoflow_ide.model import Issue, Run, placeholder_issues
from autoflow_ide.runs import RunSourceError, coordinator_runs, version_notice

IssueLoader = Callable[[str], Awaitable[List[Issue]]]
RunLoader = Callable[[str], Awaitable[List[Run]]]
Focuser = Callable[[str], Awaitable[None]]
RUN_REFRESH_SEC = 5.0


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
        self.query_one("#issues", DataTable).add_columns(("#", "number"), ("Title", "title"))
        self.query_one("#run", DataTable).add_columns("Step", "Module run", "Session", "State", "Verdict")
        self.query_one("#sessions", DataTable).add_columns("Session", "State")
        self.query_one("#issues", DataTable).focus()
        self.set_issues(self.issues)
        if not self.static:
            self.action_refresh_issues()
        if self.coordinator:
            self.set_message("version", version_notice(self.coordinator))
            self.refresh_runs()
            self.set_interval(self.run_refresh_sec, self.refresh_runs)

    # Issues

    def set_issues(self, issues: List[Issue]) -> None:
        self.issues = issues
        self.attach_runs()
        table = self.query_one("#issues", DataTable)
        table.clear()
        for issue in issues:
            table.add_row(str(issue.number), self.issue_title(issue), key=str(issue.number))
        self.show_issue(issues[0] if issues else None)

    @staticmethod
    def issue_title(issue: Issue) -> str:
        return issue.title + ("  (run)" if issue.run else "")

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
            self.set_message("issues", f"issues: {e}")
            return
        self.set_issues(issues)
        self.set_message("issues", f"{len(issues)} open issues")

    # Runs

    def refresh_runs(self) -> None:
        self.run_worker(self.load_runs(), group="runs", exclusive=True)

    async def load_runs(self) -> None:
        try:
            runs = await self.run_loader(self.coordinator)
        except RunSourceError as e:
            self.set_message("runs", f"runs: {e}")
            return
        self.runs = runs
        self.attach_runs()
        table = self.query_one("#issues", DataTable)
        for issue in self.issues:
            table.update_cell(str(issue.number), "title", self.issue_title(issue))
        self.show_issue(self.current)
        self.set_message("runs", f"{len(runs)} active runs")

    def attach_runs(self) -> None:
        """Each issue gets the first run that names it; status.py lists the most recently changed run first."""
        if not self.coordinator:
            return
        for issue in self.issues:
            issue.run = next((r for r in self.runs if r.issue == str(issue.number)), None)

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
        run_table.clear()
        sessions.clear()
        run = issue.run if issue else None
        self.query_one("#run-pane").border_title = f"Run: {run.name}" if run else "Run"
        if run:
            for n, step in enumerate(run.steps, 1):
                run_table.add_row(str(n), step.name, step.session or "", step.state, step.verdict or "")
            for session in run.sessions:
                sessions.add_row(session.name, session.state, key=session.name)
            run_table.move_cursor(row=min(run_row, max(run_table.row_count - 1, 0)))
            sessions.move_cursor(row=min(session_row, max(sessions.row_count - 1, 0)))
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
