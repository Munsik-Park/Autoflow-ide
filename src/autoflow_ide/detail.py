"""What happened in a step: the run pane's Enter opens it over the screen. Its result comes from the files the
module run left in its output folder (results.py); the run pane already shows a return or a rerun and its cause."""
from pathlib import Path
from typing import List, Optional, Tuple

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Markdown, Static

from autoflow_ide.model import Step
from autoflow_ide.results import (FAILURE, FINDINGS, OUTPUT_LIMIT, Evaluation, Outcome, OutputFile, output_path,
                                  read_outcome, read_output)

LABEL_WIDTH = 8


def describe(step: Step) -> List[Tuple[str, str]]:
    """The step's own fields, from status.py, as (label, value) rows."""
    session = step.session or "-"
    if step.session_state:
        session += f" ({step.session_state}" + (f", idle {step.idle_sec:.0f}s" if step.idle_sec is not None else "") + ")"
    rows = [
        ("Module", step.module or "- (no invocation)"),
        ("State", step.state),
        ("Verdict", step.verdict or "-"),
        ("Result", step.result or "-"),
        ("Session", session),
    ]
    if step.out:
        rows.append(("Folder", home_short(step.out)))
    return rows


def row_cells(step: Step) -> List[Tuple[str, str]]:
    """(title, text) of the run record's cells for the step's row: its Verdict cell when it says more than the
    verdict, and its Notes cell when not empty."""
    cells = []
    verdict_text = (step.verdict_text or "").strip()
    if verdict_text and verdict_text.lower() != (step.verdict or "").strip().lower():
        cells.append(("Verdict, as the run record has it", verdict_text))
    notes = (step.notes or "").strip()
    if notes:
        cells.append(("Notes", notes))
    return cells


def note(step: Step, outcome: Optional[Outcome]) -> Optional[str]:
    """Why there is nothing to show from the folder, when there is not."""
    if not step.out:
        return "No module run: this step has no output folder."
    if not outcome.folder:
        return "The output folder is not there yet."
    if not outcome.has_result:
        return "No result yet: the module run has not checked out."
    return None


def evaluation_text(evaluation: Evaluation) -> Text:
    text = Text()
    width = max((len(item) for item, _ in evaluation.scores), default=0)
    for item, score in evaluation.scores:
        text.append(f"  {item.ljust(width)}  ")
        text.append(score, style="bold")
        text.append("\n")
    if evaluation.summary:
        text.append("\nSummary\n", style="bold")
        text.append(evaluation.summary.strip() + "\n")
    if evaluation.recommendations:
        text.append(f"\nRecommendations ({len(evaluation.recommendations)})\n", style="bold")
        for r in evaluation.recommendations:
            head = " · ".join(p for p in (r.severity, r.item, r.subject) if p)
            text.append(f"• {head}\n", style="bold" if r.severity and r.severity.lower() != "low" else "")
            if r.finding:
                text.append(f"  {r.finding.strip()}\n")
    return text


def listed(lines: List[str]) -> Text:
    return Text("\n".join(f"• {line}" for line in lines) + "\n")


def numbered(lines: List[str]) -> Text:
    text = Text()
    for n, line in enumerate(lines, 1):
        text.append(f"{n}  ", style="bold")
        text.append(f"{line}\n")
    return text


# Outputs past the ninth are listed but have no key to open them.
OPENABLE = 9


def shown_outputs(outcome: Outcome) -> List[str]:
    """The outputs the detail lists, which a number key opens: when the folder holds nothing more telling (the
    reason the run fell short, an evaluation, the review's findings), the outputs are the result."""
    if outcome.failure is None and not outcome.evaluation and outcome.findings is None:
        return outcome.outputs
    return []


def lacked_text(lacked: List[Tuple[str, str]]) -> Text:
    text = Text()
    for missing, instead in lacked:
        text.append(f"• {missing}\n")
        if instead:
            text.append("  instead: ", style="dim")
            text.append(f"{instead}\n")
    return text


def sections(outcome: Outcome) -> List[Tuple[str, Widget]]:
    """(title, body) of each part of the result the folder holds, the reason the run fell short first."""
    parts: List[Tuple[str, Widget]] = []
    if outcome.unreadable:
        parts.append(("Could not read", Static(listed(outcome.unreadable))))
    if outcome.failure is not None:
        parts.append((f"Why it did not reach its goal ({FAILURE})", Markdown(outcome.failure)))
    if outcome.defects:
        parts.append(("Defects", Static(listed(outcome.defects))))
    if outcome.evaluation:
        parts.append(("Scores", Static(evaluation_text(outcome.evaluation))))
    if outcome.findings is not None:
        parts.append((f"Review findings ({FINDINGS})", Markdown(outcome.findings)))
    if shown_outputs(outcome):
        parts.append(("Outputs", Static(numbered(shown_outputs(outcome)))))
    if outcome.lacked:
        parts.append(("Lacked", Static(lacked_text(outcome.lacked))))
    return parts


class StepDetail(ModalScreen[Optional[str]]):
    """Dismissed with the cause's label when the operator goes to it, None otherwise."""
    BINDINGS = [("escape", "dismiss", "Close"), ("enter", "dismiss", "Close"), ("c", "go_to_cause", "Go to cause")] + [
        (str(n), f"open_output({n})", "Open output") for n in range(1, OPENABLE + 1)]

    def __init__(self, step: Step, number: int):
        super().__init__()
        self.step = step
        self.number = number
        self.outcome = read_outcome(step.out) if step.out else None
        self.outputs = shown_outputs(self.outcome) if self.outcome and not note(step, self.outcome) else []

    def compose(self) -> ComposeResult:
        with Vertical(id="detail") as box:
            box.border_title = f"Step {self.number}  {self.step.shown}"
            with VerticalScroll(id="detail-body"):
                yield Static("\n".join(f"{label:<{LABEL_WIDTH}} {value}" for label, value in describe(self.step)),
                             id="detail-fields")
                for title, text in row_cells(self.step):
                    yield Static(title, classes="detail-section")
                    yield Static(Text(text), classes="detail-cell")
                message = note(self.step, self.outcome)
                if message:
                    yield Static(message, classes="detail-note")
                for title, body in (sections(self.outcome) if self.outcome and not message else []):
                    yield Static(title, classes="detail-section")
                    yield body
            count = min(len(self.outputs), OPENABLE)
            opens = ("1: open the output  ·  " if count == 1 else f"1–{count}: open an output  ·  ") if count else ""
            hint = (f"c: go to {self.step.cause}  ·  " if self.step.cause else "") + opens \
                + "↑↓ PgUp PgDn: scroll  ·  Esc: close"
            yield Static(hint, id="detail-hint")

    def on_mount(self) -> None:
        self.query_one("#detail-body").focus()

    def action_dismiss(self, result: Optional[str] = None) -> None:
        self.dismiss(result)

    def action_go_to_cause(self) -> None:
        if self.step.cause:
            self.dismiss(self.step.cause)

    def action_open_output(self, n: int) -> None:
        if 1 <= n <= len(self.outputs):
            path = output_path(self.step.out, self.outputs[n - 1])
            self.app.push_screen(OutputView(self.outputs[n - 1], read_output(path)))


class OutputView(ModalScreen[None]):
    """One output of a step, over its detail (#67): Markdown rendered, JSON indented, other text as it is."""
    BINDINGS = [("escape", "dismiss", "Close"), ("enter", "dismiss", "Close")]

    def __init__(self, name: str, output: OutputFile):
        super().__init__()
        self.name_shown = name
        self.output = output

    def compose(self) -> ComposeResult:
        output = self.output
        with Vertical(id="detail") as box:
            box.border_title = self.name_shown
            with VerticalScroll(id="detail-body"):
                yield Static(home_short(str(output.path)), id="detail-fields")
                if output.kind in ("missing", "unreadable"):
                    yield Static(output.text, classes="detail-note")
                elif output.kind == "markdown":
                    yield Markdown(output.text)
                else:
                    yield Static(Text(output.text), classes="detail-cell")
                if output.cut:
                    yield Static(f"Only the first {OUTPUT_LIMIT // 1024} KB is shown.", classes="detail-note")
            yield Static("↑↓ PgUp PgDn: scroll  ·  Esc: back to the step", id="detail-hint")

    def on_mount(self) -> None:
        self.query_one("#detail-body").focus()

    def action_dismiss(self, result: None = None) -> None:
        self.dismiss(None)


def home_short(path: str) -> str:
    """`path` with the home folder written as ~, as the operator types it."""
    full = Path(path).expanduser()
    try:
        return "~/" + str(full.relative_to(Path.home()))
    except ValueError:
        return str(full)
