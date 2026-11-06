"""Start a run: open the coordination session for an issue, as an operator does by hand.

The operator opens Claude Code in the coordinator clone and gives it the workspace and the issue
("Workspace autoflow-sandbox, issue #2."). The IDE does the same in a new tmux window: a shell in the
clone, then `claude "<instruction>"`, which starts an interactive session with that first prompt. The
coordinator starts its work sessions the same way (tools/launch.sh types `claude ... "$(cat prompt)"` into
a shell), and the coordination session presents the workflow for approval before it prepares the run.
"""
import asyncio
import json
import os
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


class StartError(Exception):
    """The run could not be started; the message is shown to the operator."""


async def run(*args: str) -> str:
    """The stdout of `args`; on failure, the first line of stderr as a StartError."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except FileNotFoundError as e:
        raise StartError(f"{args[0]} is not installed") from e
    out, err = await proc.communicate()
    if proc.returncode != 0:
        lines = [line.strip() for line in err.decode(errors="replace").splitlines() if line.strip()]
        name = Path(args[1]).name if args[0] == sys.executable else args[0]
        raise StartError(lines[0] if lines else f"{name} exited with status {proc.returncode}")
    return out.decode()


async def workspace_base(coordinator: str) -> Path:
    """The coordinator's base, which holds a workspace per repository, from its tools/config.py."""
    script = Path(coordinator).expanduser() / "tools" / "config.py"
    if not script.is_file():
        raise StartError(f"no {script}")
    return Path((await run(sys.executable, str(script), "base")).strip())


@dataclass
class Workspace:
    """A coordinator workspace, from its repo.json (the coordinator's WORKSPACE.md): `clone` is the repository's
    clone and `base_branch` the branch runs start from; either is None when repo.json does not give it."""
    name: str
    github: str
    clone: Optional[str] = None
    base_branch: Optional[str] = None


def list_workspaces(base: Path) -> List[Workspace]:
    """Every workspace in `base`, in name order: a folder whose repo.json has a `github` value. A repo.json that
    cannot be read, or has no `github` string, is skipped."""
    found: List[Workspace] = []
    for repo_json in sorted(base.glob("*/repo.json")):
        try:
            values = json.loads(repo_json.read_text())
            github = values.get("github")
        except (OSError, ValueError, AttributeError):
            continue
        if not isinstance(github, str):
            continue
        clone, branch = values.get("clone"), values.get("base_branch")
        found.append(Workspace(repo_json.parent.name, github, clone if isinstance(clone, str) else None,
                               branch if isinstance(branch, str) else None))
    return found


def find_workspace_entry(base: Path, repository: str) -> Workspace:
    """The workspace in `base` whose repo.json has `repository` as its github value."""
    found = [w for w in list_workspaces(base) if w.github == repository]
    if not found:
        raise StartError(f"no workspace for {repository} in {base}")
    if len(found) > 1:
        raise StartError(f"several workspaces for {repository} in {base}: {', '.join(w.name for w in found)}")
    return found[0]


def find_workspace(base: Path, repository: str) -> str:
    """The name of the workspace in `base` whose repo.json has `repository` as its github value."""
    return find_workspace_entry(base, repository).name


def instruction(workspace: str, issue: int) -> str:
    """The operator's first instruction to the coordination session."""
    return f"Workspace {workspace}, issue #{issue}."


def window_name(workspace: str, issue: int) -> str:
    return f"{workspace}#{issue}"


async def open_coordination(coordinator: str, name: str, text: str) -> str:
    """Open a tmux window named `name` with a shell in the coordinator clone, start Claude Code there with
    `text` as its first prompt, and return the new pane's id. tmux moves to the new window."""
    if not os.environ.get("TMUX"):
        raise StartError("not inside tmux")
    clone = str(Path(coordinator).expanduser())
    pane = (await run("tmux", "new-window", "-c", clone, "-n", name, "-P", "-F", "#{pane_id}")).strip()
    if not pane.startswith("%"):
        raise StartError(f"tmux new-window: no pane id ({pane!r})")
    # As the coordinator's tmux terminal types a line: literal keys, then Enter.
    await run("tmux", "send-keys", "-t", pane, "-l", "--", f"claude {shlex.quote(text)}")
    await run("tmux", "send-keys", "-t", pane, "Enter")
    return pane


async def coordinator_workspace(coordinator: str, repository: str) -> str:
    """The name of the coordinator's workspace for `repository`."""
    return find_workspace(await workspace_base(coordinator), repository)


async def coordinator_workspaces(coordinator: str) -> List[Workspace]:
    """Every workspace in the coordinator's base."""
    return list_workspaces(await workspace_base(coordinator))


async def coordinator_workspace_entry(coordinator: str, repository: str) -> Workspace:
    """The coordinator's workspace for `repository`."""
    return find_workspace_entry(await workspace_base(coordinator), repository)


async def start_run(coordinator: str, repository: str, issue: int) -> str:
    """Open the coordination session for `issue` of `repository`; returns its pane id."""
    workspace = await coordinator_workspace(coordinator, repository)
    return await open_coordination(coordinator, window_name(workspace, issue), instruction(workspace, issue))
