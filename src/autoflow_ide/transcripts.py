"""Claude Code's session records, read for the browser (#86): where a session's transcript is, and the end of each
of its files, cut to whole records.

Claude Code writes a session's records to ~/.claude/projects/<its working directory, each character other than a
letter or digit as ->/<session id>.jsonl, and a subagent's to <session id>/subagents/*.jsonl, one JSON record a line.
A work session's id is the one its run's sessions.tsv names (the coordinator's tools/launch.sh starts it with it);
a coordination session's is the one the IDE started it with and kept on its pane (start.py).
"""
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from autoflow_ide.model import Run
from autoflow_ide.results import OUTPUT_LIMIT
from autoflow_ide.start import clone_path

# At most this much of each file is sent, from its end (#86).
LIMIT = OUTPUT_LIMIT


class TranscriptUnknown(Exception):
    """Which transcript is the session's cannot be told; the message says why (#86)."""


@dataclass
class Tail:
    """The end of a transcript file as sent: whole records only, and what was not sent (#86)."""
    data: bytes
    notes: List[str] = field(default_factory=list)


def read_tail(path: Path, limit: int = LIMIT) -> Tail:
    """The whole records among the last `limit` bytes of `path`. A record cut by the start of that window, and one
    still being written at the end (no line feed yet), are not sent; the notes say how much was left out (#86)."""
    with open(path, "rb") as f:
        size = os.fstat(f.fileno()).st_size
        start = max(0, size - limit)
        f.seek(max(0, start - 1))
        before = f.read(1) if start else b""
        chunk = f.read(size - start)
    if start == 0 or before == b"\n":
        s = start
    else:
        first = chunk.find(b"\n")
        s = start + first + 1 if first >= 0 else size
    rest = chunk[s - start:]
    last = rest.rfind(b"\n")
    e = s + last + 1 if last >= 0 else s
    if s >= e and start > 0 and before != b"\n" and chunk.find(b"\n") in (-1, len(chunk) - 1):
        # One record spans the whole window. (#86)
        return Tail(b"", [f"the last record is over 512 KB: no record shown ({size} bytes in the file)"])
    notes = []
    if s > 0:
        notes.append(f"{s} bytes before this were left out")
    if e < size:
        notes.append(f"{size - e} bytes of a record being written not shown")
    return Tail(chunk[s - start:e - start], notes)


def project_folder(directory: str, home: Path) -> Path:
    """The folder Claude Code keeps the records of the sessions started in `directory` in, as the coordinator's
    tools/session.py finds it (#86)."""
    return Path(home) / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", os.path.expanduser(directory))


def coordination_folder(coordinator: str, home: Path) -> Path:
    """The folder of the coordination sessions: they start in the coordinator clone, as its physical path (#86)."""
    return project_folder(str(clone_path(coordinator)), home)


def checked_id(value: str) -> str:
    """`value` when it is a session id as uuid4() and `uuidgen | tr A-Z a-z` give it; no path is built from any
    other value (#86)."""
    try:
        canonical = str(uuid.UUID(value)) == value
    except ValueError:
        canonical = False
    if not canonical:
        raise TranscriptUnknown(f"transcript not known: the session id {value} is not a UUID")
    return value


def transcript_files(folder: Path, session_id: str) -> List[Path]:
    """The main file of session `session_id` in `folder`, whether it is written yet or not, then its subagent
    files in name order (#86)."""
    session_id = checked_id(session_id)
    subagents = folder / session_id / "subagents"
    return [folder / f"{session_id}.jsonl"] + (sorted(subagents.glob("*.jsonl")) if subagents.is_dir() else [])


def work_session_id(run_dir: Path, name: str) -> Optional[str]:
    """The session id of work session `name` in the run's sessions.tsv (name, surface, id), or None (#86)."""
    try:
        with open(Path(run_dir) / "sessions.tsv") as f:
            for line in f:
                row = line.rstrip("\n").split("\t")
                if row[0] == name and len(row) >= 3:
                    return row[2]
    except OSError:
        return None
    return None


def runs_folder(config_text: str) -> Optional[Path]:
    """The coordinator's runs folder, from the output of its tools/config.py run without a key (#86)."""
    for line in config_text.splitlines():
        fields = line.split("\t")
        if len(fields) == 2 and fields[0] == "runs":
            return Path(fields[1])
    return None


def work_transcripts(runs: Path, run: Run, name: str, home: Path) -> List[Path]:
    """The transcript files of work session `name` of `run`, whose folder is in `runs` (#86)."""
    session_id = work_session_id(Path(runs) / run.name, name)
    if session_id is None:
        raise TranscriptUnknown(f"transcript not known: {name} is not in the sessions.tsv of {run.name}")
    if not run.checkout:
        raise TranscriptUnknown(f"transcript not known: {run.name} names no worktree")
    return transcript_files(project_folder(run.checkout, home), session_id)
