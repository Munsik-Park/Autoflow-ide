"""The IDE screen: issues, the selected issue's run, and its sessions."""
import argparse
from typing import Awaitable, Callable, List, Optional

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Static

from autoflow_ide.issues import IssueSourceError, gh_issues, repository_from_workspace
from autoflow_ide.model import Issue, placeholder_issues

IssueLoader = Callable[[str], Awaitable[List[Issue]]]


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
                 loader: Optional[IssueLoader] = None):
        """With `issues`, show them as given. Otherwise load the open issues of `repository` with `loader`."""
        super().__init__()
        self.issues: List[Issue] = list(issues) if issues is not None else []
        self.static = issues is not None
        self.repository = repository
        self.loader = loader or gh_issues
        self.current: Optional[Issue] = None
        self.message = ""

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
        self.query_one("#issues", DataTable).add_columns("#", "Title")
        self.query_one("#run", DataTable).add_columns("Step", "Module", "State", "Verdict")
        self.query_one("#sessions", DataTable).add_columns("Session", "State")
        self.query_one("#issues", DataTable).focus()
        self.set_issues(self.issues)
        if not self.static:
            self.action_refresh_issues()

    def set_issues(self, issues: List[Issue]) -> None:
        self.issues = issues
        table = self.query_one("#issues", DataTable)
        table.clear()
        for issue in issues:
            title = issue.title + ("  (run)" if issue.run else "")
            table.add_row(str(issue.number), title, key=str(issue.number))
        self.show_issue(issues[0] if issues else None)

    def action_refresh_issues(self) -> None:
        if self.static:
            return
        if not self.repository:
            self.set_message("no repository: start with --repo owner/name or --workspace <dir>")
            return
        self.set_message("loading issues")
        self.run_worker(self.load_issues(), group="issues", exclusive=True)

    async def load_issues(self) -> None:
        try:
            issues = await self.loader(self.repository)
        except IssueSourceError as e:
            self.set_message(f"issues: {e}")
            return
        self.set_issues(issues)
        self.set_message(f"{len(issues)} open issues")

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id != "issues" or event.row_key.value is None:
            return
        number = int(event.row_key.value)
        self.show_issue(next((i for i in self.issues if i.number == number), None))

    def show_issue(self, issue: Optional[Issue]) -> None:
        self.current = issue
        run_table = self.query_one("#run", DataTable)
        sessions = self.query_one("#sessions", DataTable)
        run_table.clear()
        sessions.clear()
        run = issue.run if issue else None
        self.query_one("#run-pane").border_title = f"Run: {run.name}" if run else "Run"
        if run:
            for n, step in enumerate(run.steps, 1):
                run_table.add_row(str(n), step.module, step.state, step.verdict or "")
            for session in run.sessions:
                sessions.add_row(session.name, session.state)
        self.update_status()

    def set_message(self, message: str) -> None:
        self.message = message
        self.update_status()

    def update_status(self) -> None:
        issue, run = self.current, self.current.run if self.current else None
        parts = [self.repository or "(no repository)",
                 f"#{issue.number}" if issue else "no issue",
                 run.name if run else "no run"]
        if self.message:
            parts.append(self.message)
        self.query_one("#status", Static).update("  |  ".join(parts))

    def action_focus_pane(self, pane: str) -> None:
        self.query_one(f"#{pane}", DataTable).focus()


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(prog="autoflow-ide", description="An issue-based terminal IDE for AutoFlow")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--repo", metavar="OWNER/NAME", help="the GitHub repository whose issues to list")
    source.add_argument("--workspace", metavar="DIR", help="a coordinator workspace; its repo.json names the repository")
    source.add_argument("--demo", action="store_true", help="show placeholder data")
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
    IdeApp(repository=repository).run()


if __name__ == "__main__":
    main()
