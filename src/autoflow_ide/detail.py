"""What happened in a step: the run pane's Enter opens it over the screen."""
from typing import List, Optional, Tuple

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from autoflow_ide.model import Step


def entry_text(step: Step) -> str:
    """Why this run of the module happened, from status.py's entry and cause."""
    if step.entry == "return":
        return f"return: sent back by {step.cause}" if step.cause else "return: sent back by an input the coordinator made"
    if step.entry == "rerun":
        return f"rerun of {step.cause}" if step.cause else "rerun"
    return "first run" if step.module else "-"


def describe(step: Step, number: int) -> List[Tuple[str, str]]:
    """The step's fields as (label, value) rows."""
    session = step.session or "-"
    if step.session_state:
        session += f" ({step.session_state}" + (f", idle {step.idle_sec:.0f}s" if step.idle_sec is not None else "") + ")"
    return [
        ("Step", f"{number}  {step.name}"),
        ("Module", step.module or "- (no invocation)"),
        ("State", step.state),
        ("Verdict", step.verdict or "-"),
        ("Session", session),
        ("Entry", entry_text(step)),
    ]


class StepDetail(ModalScreen[Optional[str]]):
    """Dismissed with the cause's label when the operator goes to it, None otherwise."""
    BINDINGS = [("escape", "dismiss", "Close"), ("enter", "dismiss", "Close"), ("c", "go_to_cause", "Go to cause")]

    def __init__(self, step: Step, number: int):
        super().__init__()
        self.step = step
        self.number = number

    def compose(self) -> ComposeResult:
        with Vertical(id="detail") as box:
            box.border_title = f"Step {self.number}"
            yield Static("\n".join(f"{label:<8} {value}" for label, value in describe(self.step, self.number)),
                         id="detail-body")
            hint = (f"c: go to {self.step.cause}  ·  " if self.step.cause else "") + "Esc: close"
            yield Static(hint, id="detail-hint")

    def action_dismiss(self, result: Optional[str] = None) -> None:
        self.dismiss(result)

    def action_go_to_cause(self) -> None:
        if self.step.cause:
            self.dismiss(self.step.cause)
