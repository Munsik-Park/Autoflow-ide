"""Open issues of a repository, from GitHub through gh."""
import asyncio
import json
from pathlib import Path
from typing import List

from autoflow_ide.model import Issue

FIELDS = "number,title"
# One issue through the REST API (gh issue view needs GraphQL); state is "open" or "closed".
ISSUE_JQ = "{number: .number, title: .title, state: .state}"


class IssueSourceError(Exception):
    """The issues could not be read; the message is shown to the operator."""


def parse_issue_list(text: str) -> List[Issue]:
    """Issues from the JSON of `gh issue list --json number,title`."""
    try:
        rows = json.loads(text)
        return [Issue(int(row["number"]), str(row["title"])) for row in rows]
    except (ValueError, TypeError, KeyError) as e:
        raise IssueSourceError(f"unexpected gh output: {e}") from e


def parse_issue(text: str) -> Issue:
    """One issue from the output of `gh api repos/<owner>/<name>/issues/<n> --jq ISSUE_JQ`."""
    try:
        row = json.loads(text)
        return Issue(int(row["number"]), str(row["title"]), state=str(row["state"]))
    except (ValueError, TypeError, KeyError) as e:
        raise IssueSourceError(f"unexpected gh output: {e}") from e


async def run_gh(*args: str) -> str:
    """The stdout of gh with `args`; on failure, the first line of its stderr as an IssueSourceError."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "gh", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except FileNotFoundError as e:
        raise IssueSourceError("gh is not installed") from e
    out, err = await proc.communicate()
    if proc.returncode != 0:
        lines = [line.strip() for line in err.decode(errors="replace").splitlines() if line.strip()]
        raise IssueSourceError(lines[0] if lines else f"gh exited with status {proc.returncode}")
    return out.decode()


async def gh_issues(repository: str, limit: int = 100) -> List[Issue]:
    """The open issues of `repository` (owner/name)."""
    return parse_issue_list(await run_gh(
        "issue", "list", "--repo", repository, "--state", "open", "--limit", str(limit), "--json", FIELDS))


async def gh_issue(repository: str, number: int) -> Issue:
    """Issue `number` of `repository`, open or closed."""
    return parse_issue(await run_gh("api", f"repos/{repository}/issues/{number}", "--jq", ISSUE_JQ))


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
