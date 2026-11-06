"""Runs from the coordinator's tools/status.py."""
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import List

from autoflow_ide.model import Run, Session, Step


# The coordinator version whose status.py output the IDE reads, without -rcN. The coordinator's VERSION
# follows the AutoFlow version it supports, and improvements to the coordinator for that version go up as
# -rcN; the coordinator itself compares versions without -rcN (prepare.py).
SUPPORTED_COORDINATOR = "0.7.0"


class RunSourceError(Exception):
    """The runs could not be read; the message is shown to the operator."""


def parse_status(text: str) -> List[Run]:
    """Runs from the JSON of status.py: {"runs": [{"name", "issue", "branch", "reference", "steps", "coordination",
    "sessions", "error"?}]}, coordination being {"surface"} or null (from v0.7.0-rc3; absent before), and each
    step's out and result its output folder and result status (from v0.7.0-rc5; absent before)."""
    try:
        runs = []
        for r in json.loads(text)["runs"]:
            steps = [Step(s["label"], s["state"], s.get("verdict"), s.get("session"), s.get("module"),
                          s.get("entry"), s.get("cause"), s.get("session_state"), s.get("idle_sec"),
                          s.get("out"), s.get("result"))
                     for s in r.get("steps", [])]
            sessions = [Session(s["name"], s["state"], s.get("surface")) for s in r.get("sessions", [])]
            coordination = r.get("coordination") or {}
            runs.append(Run(r["name"], steps, sessions, r.get("issue"), r.get("branch"), r.get("error"),
                            r.get("reference"), coordination.get("surface")))
        return runs
    except (ValueError, TypeError, KeyError) as e:
        raise RunSourceError(f"unexpected status.py output: {e!r}") from e


def status_script(coordinator: str) -> Path:
    return Path(coordinator).expanduser() / "tools" / "status.py"


async def coordinator_runs(coordinator: str) -> List[Run]:
    """The active runs, from status.py of the coordinator clone at `coordinator`."""
    script = status_script(coordinator)
    if not script.is_file():
        raise RunSourceError(f"no {script}")
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(script), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()
    if proc.returncode != 0:
        lines = [line.strip() for line in err.decode(errors="replace").splitlines() if line.strip()]
        raise RunSourceError(lines[0] if lines else f"status.py exited with status {proc.returncode}")
    return parse_status(out.decode())


def version_notice(coordinator: str) -> str:
    """Empty when the coordinator clone is at the supported version, otherwise what differs."""
    path = Path(coordinator).expanduser() / "VERSION"
    try:
        version = path.read_text().strip()
    except OSError:
        return f"coordinator: no {path}"
    if without_rc(version) != SUPPORTED_COORDINATOR:
        return f"coordinator {version}, supported {SUPPORTED_COORDINATOR}"
    return ""


def without_rc(version: str) -> str:
    """The version without its -rcN, as the coordinator's prepare.py compares versions."""
    return re.sub(r"-rc[0-9]+$", "", version)
