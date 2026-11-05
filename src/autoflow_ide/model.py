"""What the screen shows: issues, the run of an issue, and its sessions."""
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Step:
    module: str
    state: str
    verdict: Optional[str] = None


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


@dataclass
class Issue:
    number: int
    title: str
    run: Optional[Run] = None


def placeholder_issues() -> List[Issue]:
    """Sample data for the screen until the issue and run sources are connected."""
    return [
        Issue(12, "Add the issues pane", Run(
            "issue-12",
            steps=[Step("analysis", "pass", "pass"), Step("build", "running")],
            sessions=[Session("coordination", "working"), Session("S1", "turn-end"), Session("S2", "working")],
        )),
        Issue(15, "Read runs from status.py"),
        Issue(18, "Move tmux focus to a session"),
    ]
