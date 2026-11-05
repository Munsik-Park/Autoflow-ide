"""Open issues of a repository, from GitHub through gh."""
import asyncio
import json
from pathlib import Path
from typing import List

from autoflow_ide.model import Issue

FIELDS = "number,title"


class IssueSourceError(Exception):
    """The issues could not be read; the message is shown to the operator."""


def parse_issue_list(text: str) -> List[Issue]:
    """Issues from the JSON of `gh issue list --json number,title`."""
    try:
        rows = json.loads(text)
        return [Issue(int(row["number"]), str(row["title"])) for row in rows]
    except (ValueError, TypeError, KeyError) as e:
        raise IssueSourceError(f"unexpected gh output: {e}") from e


async def gh_issues(repository: str, limit: int = 100) -> List[Issue]:
    """The open issues of `repository` (owner/name)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "gh", "issue", "list", "--repo", repository, "--state", "open",
            "--limit", str(limit), "--json", FIELDS,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except FileNotFoundError as e:
        raise IssueSourceError("gh is not installed") from e
    out, err = await proc.communicate()
    if proc.returncode != 0:
        lines = [line.strip() for line in err.decode(errors="replace").splitlines() if line.strip()]
        raise IssueSourceError(lines[0] if lines else f"gh exited with status {proc.returncode}")
    return parse_issue_list(out.decode())


def repository_from_workspace(workspace: str) -> str:
    """The `github` value of a coordinator workspace's repo.json."""
    path = Path(workspace).expanduser() / "repo.json"
    try:
        repository = json.loads(path.read_text())["github"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise IssueSourceError(f"no github value in {path}: {e}") from e
    if not isinstance(repository, str) or "/" not in repository:
        raise IssueSourceError(f"github in {path} is not owner/name: {repository!r}")
    return repository
