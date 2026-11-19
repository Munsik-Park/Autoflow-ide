"""What happened in a step: the run pane's Enter opens it over the screen. Beside the step's fields and its row's
cells from status.py, it lists what the module run left in its work folder (results.py, #83): a number key opens
an entry, a file as an output is read and a folder into its own entries; the run pane already shows a return or
a rerun and its cause."""
from pathlib import Path
from typing import List, Optional, Tuple

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Markdown, Static

from autoflow_ide.model import Step
from autoflow_ide.results import OUTPUT_LIMIT, Entry, OutputFile, Work, read_output, read_work

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


def note(step: Step, work: Optional[Work]) -> Optional[str]:
    """Why there is nothing to show from the work folder, when there is not."""
    if not step.out:
        return "No module run: this step has no work folder."
    if not work.folder:
        return "The work folder is not there yet."
    return None


def listed(lines: List[str]) -> Text:
    return Text("\n".join(f"• {line}" for line in lines) + "\n")


# The entries past the ninth are listed but have no key to open them (#83).
OPENABLE = 9
AFTER_END = "written after the end"


def numbered(entries: List[Entry]) -> Text:
    """The entries, numbered as their keys open them, each written after the end marked (#83)."""
    text = Text()
    for n, entry in enumerate(entries, 1):
        text.append(f"{n}  ", style="bold")
        text.append(entry.name)
        if entry.after_end:
            text.append(f"  · {AFTER_END}", style="bold")
        text.append("\n")
    return text if entries else Text("(empty folder)\n")


def opens(count: int) -> str:
    """The hint's part for the number keys, for `count` entries."""
    count = min(count, OPENABLE)
    if not count:
        return ""
    return "1: open the entry  ·  " if count == 1 else f"1–{count}: open an entry  ·  "


class Listing(ModalScreen):
    """A screen that lists entries of a work folder whose number keys open them: a file in OutputView, a folder
    in a FolderView over this one (#83)."""

    def __init__(self, work: Optional[Work], sub: str = ""):
        super().__init__()
        self.work = work
        self.sub = sub
        self.entries: List[Entry] = []
        # why the folder could not be listed, shown under "Could not read" (#83)
        self.error: Optional[str] = None
        if work and work.folder:
            self.entries, self.error = work.listing(sub)

    def action_open_entry(self, n: int) -> None:
        if not 1 <= n <= min(len(self.entries), OPENABLE):
            return
        entry = self.entries[n - 1]
        name = f"{self.sub}/{entry.name}" if self.sub else entry.name
        if entry.name.endswith("/"):
            self.app.push_screen(FolderView(self.work, name.rstrip("/")))
        else:
            self.app.push_screen(OutputView(name, read_output(entry.path)))


ENTRY_KEYS = [(str(n), f"open_entry({n})", "Open") for n in range(1, OPENABLE + 1)]


class StepDetail(Listing):
    """Dismissed with the cause's label when the operator goes to it, None otherwise."""
    BINDINGS = [("escape", "dismiss", "Close"), ("enter", "dismiss", "Close"), ("c", "go_to_cause", "Go to cause")] \
        + ENTRY_KEYS

    def __init__(self, step: Step, number: int):
        work = read_work(step.out) if step.out else None
        super().__init__(work if work and not note(step, work) else None)
        self.step = step
        self.number = number
        self.message = note(step, work)

    def compose(self) -> ComposeResult:
        with Vertical(id="detail") as box:
            box.border_title = f"Step {self.number}  {self.step.shown}"
            with VerticalScroll(id="detail-body"):
                yield Static("\n".join(f"{label:<{LABEL_WIDTH}} {value}" for label, value in describe(self.step)),
                             id="detail-fields")
                for title, text in row_cells(self.step):
                    yield Static(title, classes="detail-section")
                    yield Static(Text(text), classes="detail-cell")
                if self.message:
                    yield Static(self.message, classes="detail-note")
                else:
                    unreadable = self.work.unreadable + ([self.error] if self.error else [])
                    if unreadable:
                        yield Static("Could not read", classes="detail-section")
                        yield Static(listed(unreadable))
                    yield Static("Work folder", classes="detail-section")
                    if not self.work.ended:
                        yield Static("The module run has not ended yet: it has no end record.", classes="detail-note")
                    if not self.error:
                        yield Static(numbered(self.entries))
            hint = (f"c: go to {self.step.cause}  ·  " if self.step.cause else "") + opens(len(self.entries)) \
                + "↑↓ PgUp PgDn: scroll  ·  Esc: close"
            yield Static(hint, id="detail-hint")

    def on_mount(self) -> None:
        self.query_one("#detail-body").focus()

    def action_dismiss(self, result: Optional[str] = None) -> None:
        self.dismiss(result)

    def action_go_to_cause(self) -> None:
        if self.step.cause:
            self.dismiss(self.step.cause)


class FolderView(Listing):
    """A folder inside a work folder, over the screen it was opened from: its entries, opened in the same way
    (#83)."""
    BINDINGS = [("escape", "dismiss", "Back"), ("enter", "dismiss", "Back")] + ENTRY_KEYS

    def compose(self) -> ComposeResult:
        with Vertical(id="detail") as box:
            # Names a module run wrote, shown as written, never read as markup (#83).
            box.border_title = Text(f"{self.sub}/")
            with VerticalScroll(id="detail-body"):
                yield Static(Text(home_short(str(self.work.path / self.sub))), id="detail-fields")
                if self.error:
                    yield Static("Could not read", classes="detail-section")
                    yield Static(listed([self.error]))
                else:
                    yield Static(numbered(self.entries))
            yield Static(opens(len(self.entries)) + "↑↓ PgUp PgDn: scroll  ·  Esc: back", id="detail-hint")

    def on_mount(self) -> None:
        self.query_one("#detail-body").focus()

    def action_dismiss(self, result: None = None) -> None:
        self.dismiss(None)


class OutputView(ModalScreen[None]):
    """One file of a step's work folder, over the screen it was opened from (#67): Markdown rendered, JSON
    indented, other text as it is."""
    BINDINGS = [("escape", "dismiss", "Close"), ("enter", "dismiss", "Close")]

    def __init__(self, name: str, output: OutputFile):
        super().__init__()
        self.name_shown = name
        self.output = output

    def compose(self) -> ComposeResult:
        output = self.output
        with Vertical(id="detail") as box:
            # A path from a work folder, shown as written, never read as markup (#83).
            box.border_title = Text(self.name_shown)
            with VerticalScroll(id="detail-body"):
                yield Static(Text(home_short(str(output.path))), id="detail-fields")
                if output.kind in ("missing", "unreadable"):
                    yield Static(output.text, classes="detail-note")
                elif output.kind == "markdown":
                    yield Markdown(output.text)
                else:
                    yield Static(Text(output.text), classes="detail-cell")
                if output.cut:
                    yield Static(f"Only the first {OUTPUT_LIMIT // 1024} KB is shown.", classes="detail-note")
            yield Static("↑↓ PgUp PgDn: scroll  ·  Esc: back", id="detail-hint")

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
