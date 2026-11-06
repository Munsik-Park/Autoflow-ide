"""What the screen shows: issues, the run of an issue, and its sessions."""
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Step:
    label: str
    state: str
    verdict: Optional[str] = None
    session: Optional[str] = None
    module: Optional[str] = None
    # null on a module's first run; "return" when a later run's finding sent the flow back to it, "rerun" otherwise
    entry: Optional[str] = None
    # the run that sent it back or whose output it runs again on, when the invocation names one
    cause: Optional[str] = None
    # the session's state and seconds since its last activity, on a running step only
    session_state: Optional[str] = None
    idle_sec: Optional[float] = None
    # the module run's output folder, as its invocation names it; null for a step with no invocation
    out: Optional[str] = None
    # the status in that folder's result.json (accepted, refused or failed); null until it is written or
    # when it cannot be read
    result: Optional[str] = None

    @property
    def name(self) -> str:
        """The module and the run's label, as the run record names the step (review s1); the label alone
        when there is no module or the label already starts with it."""
        if self.module and not self.label.startswith(self.module):
            return f"{self.module} {self.label}"
        return self.label

    @property
    def shown(self) -> str:
        """The name as the run pane shows it: marked ↩ for a return and ↺ for a rerun, with the run that
        caused it (↩ design-2 ← review-1), as the coordinator's run-status pane shows a step."""
        mark = ENTRY_MARK.get(self.entry or "", "")
        return f"{mark}{self.name}" + (f" ← {self.cause}" if self.cause else "")


ENTRY_MARK = {"return": "↩ ", "rerun": "↺ "}


@dataclass
class Session:
    name: str
    state: str
    surface: Optional[str] = None


@dataclass
class Run:
    name: str
    steps: List[Step] = field(default_factory=list)
    sessions: List[Session] = field(default_factory=list)
    issue: Optional[str] = None
    branch: Optional[str] = None
    error: Optional[str] = None
    # the workflow the run follows, as its run record names it (review-only)
    reference: Optional[str] = None
    # the coordination session's surface (a tmux pane id), when run.env records it
    coordination: Optional[str] = None
    # the run's worktree and its record folder, as run.env writes them (from coordinator v0.7.0-rc6)
    checkout: Optional[str] = None
    records: Optional[str] = None


@dataclass
class Issue:
    number: int
    title: str
    run: Optional[Run] = None
    # "open" or "closed" as GitHub gives it; None while an issue missing from the open list is being looked up.
    state: Optional[str] = "open"

    @property
    def closed(self) -> bool:
        return self.state == "closed"


def placeholder_issues() -> List[Issue]:
    """Sample data for --demo."""
    return [
        Issue(12, "Add the issues pane", Run(
            "issue-12",
            steps=[Step("analysis", "pass", "pass", "S1"), Step("build", "running", None, "S2")],
            sessions=[Session("S1", "turn-end"), Session("S2", "working")],
        )),
        Issue(15, "Read runs from status.py"),
        Issue(18, "Move tmux focus to a session"),
    ]
