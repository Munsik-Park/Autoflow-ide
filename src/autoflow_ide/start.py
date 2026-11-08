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
import re
import shlex
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


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
class Setting:
    """A coordinator setting as its tools/config.py prints it: the value, and the settings file that set it, or
    None for the coordinator's built-in default."""
    value: str
    source: Optional[str]


def parse_config(text: str) -> Dict[str, Setting]:
    """The settings in the output of the coordinator's tools/config.py run without a key: a line per setting,
    `key<TAB>value<TAB>(source)`, the source a file path or `default`. The places under the root that follow
    (`key<TAB>path`, no source) are not settings and are left out."""
    found: Dict[str, Setting] = {}
    for line in text.splitlines():
        fields = line.split("\t")
        if len(fields) != 3 or not (fields[2].startswith("(") and fields[2].endswith(")")):
            continue
        source = fields[2][1:-1]
        found[fields[0]] = Setting(fields[1], None if source == "default" else source)
    return found


# The coordinator's settings files, by name (its SETTINGS.md): one at the root of the clone, one in ~/.autoflow.
SETTINGS_FILES = {"coordination.local.json": "local settings", "coordination.json": "global settings"}


def setting_source(source: Optional[str]) -> str:
    """Where a coordinator setting was set, as the operator reads it: which settings file, or the built-in default;
    the file's path when its name is not one of the coordinator's."""
    if source is None:
        return "built-in default"
    return SETTINGS_FILES.get(Path(source).name, source)


async def coordinator_base(coordinator: str) -> Setting:
    """The coordinator's base and where it was set, from its tools/config.py."""
    script = Path(coordinator).expanduser() / "tools" / "config.py"
    if not script.is_file():
        raise StartError(f"no {script}")
    settings = parse_config(await run(sys.executable, str(script)))
    if "base" not in settings:
        raise StartError(f"{script} printed no base")
    return settings["base"]


@dataclass
class Workspace:
    """A coordinator workspace, from its repo.json (the coordinator's WORKSPACE.md): `clone` is the repository's
    clone and `base_branch` the branch runs start from; either is None when repo.json does not give it."""
    name: str
    github: str
    clone: Optional[str] = None
    base_branch: Optional[str] = None
    # The workspace's folder in the base, where its gh-token is (#74).
    folder: Optional[Path] = field(default=None, compare=False)


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
                               branch if isinstance(branch, str) else None, folder=repo_json.parent))
    return found


# A workspace's GitHub token (the coordinator's WORKSPACE.md > gh-token, autoflow-coordinator#71): a file in the
# workspace's folder, the caller's own with no group or other permission, holding the token alone on one line. The
# coordinator starts a run only when the coordination session's GH_TOKEN is that token (#74).
TOKEN_FILE = "gh-token"


class TokenError(Exception):
    """A workspace's gh-token cannot be used; the message names the file, never the token."""


def read_token(folder: Path) -> Optional[str]:
    """The token of the workspace in `folder`, or None when it has no gh-token. A gh-token the coordinator would
    refuse (its tools/prepare.py read_token) raises TokenError."""
    path = folder / TOKEN_FILE
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as e:
        raise TokenError(f"{path}: {e.strerror}") from e
    if not stat.S_ISREG(st.st_mode):
        raise TokenError(f"{path} is not a regular file")
    if st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise TokenError(f"{path} must be yours and readable by you only: chmod 600 {path}")
    try:
        with open(path, newline="") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError) as e:
        raise TokenError(f"{path}: cannot be read") from e
    token = content[:-1] if content.endswith("\n") else content
    if not token:
        raise TokenError(f"{path} is empty")
    if re.search(r"\s", token):
        raise TokenError(f"{path}: not one token alone on one line")
    return token


def token_file(workspace: Workspace) -> Optional[Path]:
    """The workspace's gh-token when it has a usable one, None when it has none; TokenError when it has one that
    cannot be used."""
    if workspace.folder is None or read_token(workspace.folder) is None:
        return None
    return workspace.folder / TOKEN_FILE


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


async def open_coordination(coordinator: str, name: str, text: str, token: Optional[Path] = None) -> str:
    """Open a tmux window named `name` with a shell in the coordinator clone, start Claude Code there with
    `text` as its first prompt, and return the new pane's id. tmux moves to the new window.
    With `token` (a workspace's gh-token), Claude Code starts with that token as GH_TOKEN: the typed line reads
    the file, as the coordinator's tools/launch.sh does, so the token is neither typed nor in tmux's arguments."""
    if not os.environ.get("TMUX"):
        raise StartError("not inside tmux")
    clone = str(Path(coordinator).expanduser())
    pane = (await run("tmux", "new-window", "-c", clone, "-n", name, "-P", "-F", "#{pane_id}")).strip()
    if not pane.startswith("%"):
        raise StartError(f"tmux new-window: no pane id ({pane!r})")
    # As the coordinator's tmux terminal types a line: literal keys, then Enter.
    line = f"claude {shlex.quote(text)}"
    if token is not None:
        line = f"GH_TOKEN=$(cat {shlex.quote(str(token))}) {line}"
    await run("tmux", "send-keys", "-t", pane, "-l", "--", line)
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
    """Open the coordination session for `issue` of `repository`, with its workspace's token when it has one;
    returns its pane id. Nothing is opened when the workspace's gh-token cannot be used."""
    workspace = await coordinator_workspace_entry(coordinator, repository)
    try:
        token = token_file(workspace)
    except TokenError as e:
        raise StartError(str(e)) from e
    return await open_coordination(coordinator, window_name(workspace.name, issue),
                                   instruction(workspace.name, issue), token)


# Adding a workspace: the operator gives the repository; the coordination session asks for the rest (the clone, the
# base branch) and writes the workspace (the coordinator's WORKSPACE.md, "Creating").

# A GitHub repository, given as the operator has it at hand: owner/name, the web address
# (https://github.com/owner/name) or a clone address (git@github.com:owner/name.git, https://github.com/owner/name.git).
OWNER = r"(?P<owner>[A-Za-z0-9][A-Za-z0-9-]*)"
NAME = r"(?P<name>[A-Za-z0-9._-]+?)"
ADDRESSES = [re.compile(p) for p in (
    rf"{OWNER}/{NAME}(?:\.git)?",
    rf"(?:https?://)?(?:www\.)?github\.com/{OWNER}/{NAME}(?:\.git)?/?",
    rf"(?:ssh://)?git@github\.com[:/]{OWNER}/{NAME}(?:\.git)?",
)]


def repository_of(address: str) -> Optional[str]:
    """owner/name of the GitHub repository `address` names, or None when it names none."""
    for pattern in ADDRESSES:
        match = pattern.fullmatch(address.strip())
        if match and match["name"] not in (".", ".."):
            return f"{match['owner']}/{match['name']}"
    return None


def workspace_instruction(address: str) -> str:
    """The operator's first instruction to a coordination session that makes a workspace: the repository, and the
    address it was given as when that is more than owner/name, so the clone is made the way the operator gave it."""
    repository = repository_of(address) or address
    if address.strip() == repository:
        return f"Create a workspace for {repository}."
    return f"Create a workspace for {repository}, cloned from {address.strip()}."


def workspace_window_name(repository: str) -> str:
    """Not `<workspace>#<issue>`, so the IDE does not take it for an issue's window and close it."""
    return f"workspace:{repository}"


async def create_workspace(coordinator: str, address: str) -> str:
    """Open a coordination session that makes a workspace for the repository `address` names (see repository_of) in
    the coordinator's base; returns its pane id. Nothing is opened when a workspace of the base already names that
    repository."""
    repository = repository_of(address)
    if repository is None:
        raise StartError(f"{address} is not a GitHub repository")
    found = [w.name for w in await coordinator_workspaces(coordinator) if w.github == repository]
    if found:
        raise StartError(f"{repository} already has a workspace: {', '.join(found)}")
    return await open_coordination(coordinator, workspace_window_name(repository), workspace_instruction(address))
