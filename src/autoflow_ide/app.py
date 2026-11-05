"""The IDE screen: issues, the selected issue's run, and its sessions."""
from typing import List, Optional

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Static

from autoflow_ide.model import Issue, placeholder_issues


class IdeApp(App):
    CSS_PATH = "app.tcss"
    TITLE = "autoflow-ide"
    BINDINGS = [
        ("1", "focus_pane('issues')", "Issues"),
        ("2", "focus_pane('run')", "Run"),
        ("3", "focus_pane('sessions')", "Sessions"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, issues: Optional[List[Issue]] = None, repository: str = "(no repository)"):
        super().__init__()
        self.issues = placeholder_issues() if issues is None else issues
        self.repository = repository

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
        issues = self.query_one("#issues", DataTable)
        issues.add_columns("#", "Title")
        for issue in self.issues:
            title = issue.title + ("  (run)" if issue.run else "")
            issues.add_row(str(issue.number), title, key=str(issue.number))
        self.query_one("#run", DataTable).add_columns("Step", "Module", "State", "Verdict")
        self.query_one("#sessions", DataTable).add_columns("Session", "State")
        issues.focus()
        self.show_issue(self.issues[0] if self.issues else None)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id != "issues" or event.row_key.value is None:
            return
        number = int(event.row_key.value)
        self.show_issue(next(i for i in self.issues if i.number == number))

    def show_issue(self, issue: Optional[Issue]) -> None:
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
        where = f"#{issue.number}" if issue else "no issue"
        self.query_one("#status", Static).update(
            f"{self.repository}  |  {where}  |  {run.name if run else 'no run'}")

    def action_focus_pane(self, pane: str) -> None:
        self.query_one(f"#{pane}", DataTable).focus()


def main() -> None:
    IdeApp().run()


if __name__ == "__main__":
    main()
