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

    @property
    def name(self) -> str:
        """The module and the run's label, as the run record names the step (review s1); the label alone
        when there is no module or the label already starts with it."""
        if self.module and not self.label.startswith(self.module):
            return f"{self.module} {self.label}"
        return self.label


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


@dataclass
class Issue:
    number: int
    title: str
    run: Optional[Run] = None


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
