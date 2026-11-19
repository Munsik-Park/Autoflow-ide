"""What a module run left in its work folder, read from the folder and from the run's end record.

autoflow-coordinator 0.8 (tools/invocation.py, tools/ended.py): a module run writes only inside its work folder
<records>/<module>-<n>/, and no file name in it is fixed. When its session ends the turn, the coordinator writes
the end record <records>/<module>-<n>.ended.json beside the folder, once and never again:
{"status": "ended", "outputs": {"files": [every file in the folder, as a path]}}, the files found by os.walk,
which does not go into a link to a folder. What the run left is what the folder holds now (#83); the end record
tells whether the run has ended and which files were written after it.
"""
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Set

ENDED = ".ended.json"


@dataclass
class Entry:
    """One entry of a folder as the detail lists it: `name` ends in / for a folder, and `after_end` says the end
    record does not list it, or for a folder that it holds a file the end record does not list (#83)."""
    name: str
    path: Path
    after_end: bool = False


@dataclass
class Work:
    """A module run's work folder and its end record. `ended` is whether the end record exists; `listed` the
    files it lists, relative to the folder, or None when there is none or it cannot be read, which is then named
    with the reason in `unreadable`."""
    path: Path
    folder: bool = False
    ended: bool = False
    listed: Optional[Set[str]] = None
    files: Set[str] = field(default_factory=set)
    unreadable: List[str] = field(default_factory=list)

    def entries(self, sub: str = "") -> List[Entry]:
        """The entries of the work folder, or of the folder `sub` inside it, sorted by name as read_output lists
        a folder, each marked when it was written after the end."""
        base = self.path / sub if sub else self.path
        try:
            found = [(e.name + ("/" if e.is_dir() else ""), e) for e in base.iterdir()]
        except OSError:
            return []
        prefix = f"{sub.strip('/')}/" if sub else ""
        return [Entry(name, path, self.after_end(prefix + name)) for name, path in sorted(found, key=lambda f: f[0])]

    def after_end(self, name: str) -> bool:
        if self.listed is None:
            return False
        if name.endswith("/"):
            return any(f.startswith(name) and f not in self.listed for f in self.files)
        return name not in self.listed


def files_in(folder: Path) -> Set[str]:
    """Every file in the folder, relative to it, found as the coordinator's ended.py finds them."""
    found = set()
    for d, _, names in os.walk(folder):
        found.update(str((Path(d) / n).relative_to(folder)) for n in names)
    return found


def read_work(out: str) -> Work:
    folder = Path(out).expanduser()
    work = Work(folder, folder.is_dir())
    record = folder.parent / (folder.name + ENDED)
    work.ended = record.exists()
    if work.folder:
        work.files = files_in(folder)
    if not work.ended:
        return work
    try:
        data = json.loads(record.read_text(errors="replace"))
    except OSError as e:
        work.unreadable.append(f"{record.name}: {e.strerror or e}")
        return work
    except ValueError as e:
        work.unreadable.append(f"{record.name}: not JSON ({e})")
        return work
    outputs = data.get("outputs") if isinstance(data, dict) else None
    listed = outputs.get("files") if isinstance(outputs, dict) else None
    if not isinstance(listed, list):
        work.unreadable.append(f"{record.name}: " + ("no outputs.files list" if isinstance(data, dict)
                                                     else "not a JSON object"))
        return work
    work.listed = {relative(path, folder) for path in listed if isinstance(path, str)}
    return work


def relative(path: str, folder: Path) -> str:
    """`path` inside `folder`, or after the folder's name when the record folder was copied or moved since
    the end record was written; as written otherwise."""
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
    """Where an output is: inside the step's work folder `out`, as `relative` gave it, or as written when it is
    an absolute path outside it."""
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
