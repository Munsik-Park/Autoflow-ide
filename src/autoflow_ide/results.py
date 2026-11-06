"""What a module run left in its output folder, read from the files AutoFlow writes there.

AutoFlow v0.7.0 (docs/autoflow-guide.md > Running a module, Connecting modules): check-out writes
`result.json` with `status` (accepted, refused, or failed when the run wrote its contract's failure output),
`outputs` (each output's path, or an array of paths), `defects` and `tool_requests` ({missing, instead}). A run
that does not reach its goal writes `failure.md`, an evaluation module writes `evaluation.json` (its scores,
their grounds and its recommendations), and the review writes `review-findings.md`.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Tuple

RESULT = "result.json"
FAILURE = "failure.md"
EVALUATION = "evaluation.json"
FINDINGS = "review-findings.md"


@dataclass
class Recommendation:
    severity: Optional[str]
    item: str
    subject: str
    finding: str


@dataclass
class Evaluation:
    # (item, score) in the order the evaluation gives them; a score as written when it is not a number
    scores: List[Tuple[str, str]] = field(default_factory=list)
    summary: str = ""
    recommendations: List[Recommendation] = field(default_factory=list)


@dataclass
class Outcome:
    """What the folder holds. A file that is absent is None or empty; one that cannot be read is named with
    the reason in `unreadable`."""
    folder: bool = False
    has_result: bool = False
    status: Optional[str] = None
    # output names, relative to the folder when they are inside it
    outputs: List[str] = field(default_factory=list)
    defects: List[str] = field(default_factory=list)
    # (what the run lacked, what it did instead)
    lacked: List[Tuple[str, str]] = field(default_factory=list)
    failure: Optional[str] = None
    evaluation: Optional[Evaluation] = None
    findings: Optional[str] = None
    unreadable: List[str] = field(default_factory=list)


def read_outcome(out: str) -> Outcome:
    folder = Path(out).expanduser()
    outcome = Outcome(folder=folder.is_dir())
    if not outcome.folder:
        return outcome
    outcome.has_result = (folder / RESULT).exists()
    result = read_json(folder / RESULT, outcome)
    if result is not None:
        if isinstance(result, dict):
            read_result(result, folder, outcome)
        else:
            outcome.unreadable.append(f"{RESULT}: not a JSON object")
    outcome.failure = read_text(folder / FAILURE, outcome)
    evaluation = read_json(folder / EVALUATION, outcome)
    if evaluation is not None:
        if isinstance(evaluation, dict):
            outcome.evaluation = read_evaluation(evaluation)
        else:
            outcome.unreadable.append(f"{EVALUATION}: not a JSON object")
    outcome.findings = read_text(folder / FINDINGS, outcome)
    return outcome


def read_result(result: dict, folder: Path, outcome: Outcome) -> None:
    status = result.get("status")
    outcome.status = status if isinstance(status, str) else None
    outputs = result.get("outputs")
    for value in (outputs.values() if isinstance(outputs, dict) else []):
        for path in (value if isinstance(value, list) else [value]):
            if isinstance(path, str):
                outcome.outputs.append(relative(path, folder))
    outcome.defects = [str(d) for d in as_list(result.get("defects"))]
    for request in as_list(result.get("tool_requests")):
        if isinstance(request, dict):
            outcome.lacked.append((text_of(request.get("missing")), text_of(request.get("instead"))))


def read_evaluation(d: dict) -> Evaluation:
    evaluation = Evaluation(summary=text_of(d.get("summary")))
    scores = d.get("scores")
    for item, value in (scores.items() if isinstance(scores, dict) else []):
        score = value.get("score") if isinstance(value, dict) else value
        evaluation.scores.append((str(item), score_text(score)))
    for r in as_list(d.get("recommendations")):
        if isinstance(r, dict):
            severity = r.get("severity")
            evaluation.recommendations.append(Recommendation(
                severity if isinstance(severity, str) else None,
                text_of(r.get("item")), text_of(r.get("subject")), text_of(r.get("finding"))))
    return evaluation


def read_json(path: Path, outcome: Outcome) -> Any:
    text = read_text(path, outcome)
    if text is None:
        return None
    try:
        return json.loads(text)
    except ValueError as e:
        outcome.unreadable.append(f"{path.name}: not JSON ({e})")
        return None


def read_text(path: Path, outcome: Outcome) -> Optional[str]:
    if not path.exists():
        return None
    try:
        return path.read_text(errors="replace")
    except OSError as e:
        outcome.unreadable.append(f"{path.name}: {e.strerror or e}")
        return None


def relative(path: str, folder: Path) -> str:
    """`path` inside `folder`, or after the folder's name when the record folder was copied or moved since
    result.json was written; as written otherwise."""
    try:
        return str(Path(path).relative_to(folder))
    except ValueError:
        parts = Path(path).parts
        at = [n for n, part in enumerate(parts[:-1]) if part == folder.name]
        return str(Path(*parts[at[-1] + 1:])) if at else path


# The most of an output file read to show it; the rest is left out, and the window says so.
OUTPUT_LIMIT = 512 * 1024


@dataclass
class OutputFile:
    """An output of a module run as read to show it. `kind` is markdown, json or text (`text` is the content, `cut`
    when it was longer than OUTPUT_LIMIT), folder (`text` lists its entries), or missing or unreadable (`text` says
    why)."""
    path: Path
    kind: str
    text: str
    cut: bool = False


def output_path(out: str, output: str) -> Path:
    """Where an output listed in result.json is: inside the step's output folder `out`, as `relative` gave it, or
    as written when it is an absolute path outside it."""
    path = Path(output).expanduser()
    return path if path.is_absolute() else Path(out).expanduser() / path


def read_output(path: Path) -> OutputFile:
    if path.is_dir():
        try:
            entries = sorted(e.name + ("/" if e.is_dir() else "") for e in path.iterdir())
        except OSError as e:
            return OutputFile(path, "unreadable", str(e.strerror or e))
        return OutputFile(path, "folder", "\n".join(entries) if entries else "(empty folder)")
    if not path.exists():
        return OutputFile(path, "missing", "This output is not there (moved, or not written yet).")
    try:
        with path.open("rb") as f:
            data = f.read(OUTPUT_LIMIT + 1)
    except OSError as e:
        return OutputFile(path, "unreadable", str(e.strerror or e))
    cut = len(data) > OUTPUT_LIMIT
    text = data[:OUTPUT_LIMIT].decode(errors="replace")
    suffix = path.suffix.lower()
    if suffix == ".json" and not cut:
        try:
            return OutputFile(path, "json", json.dumps(json.loads(text), indent=2, ensure_ascii=False))
        except ValueError:
            pass
    return OutputFile(path, "markdown" if suffix == ".md" else "text", text, cut)


def as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def text_of(value: Any) -> str:
    return value if isinstance(value, str) else ("" if value is None else str(value))


def score_text(score: Any) -> str:
    if isinstance(score, bool):
        return str(score)
    if isinstance(score, (int, float)):
        return f"{score:g}"
    return text_of(score)
