import json
import os
import stat
from pathlib import Path

import pytest

from autoflow_ide.issues import IssueSourceError, gh_issues, parse_issue_list, repository_from_workspace

FIXTURES = Path(__file__).parent / "fixtures"
RECORDED = (FIXTURES / "gh-issue-list.json").read_text()
NOT_SIGNED_IN = ("To get started with GitHub CLI, please run:  gh auth login\n"
                 "Alternatively, populate the GH_TOKEN environment variable with a GitHub API authentication token.\n")
NO_REPO = "GraphQL: Could not resolve to a Repository with the name 'Munsik-Park/no-such-repo-xyz'. (repository)\n"


def fake_gh(tmp_path, monkeypatch, stdout="", stderr="", status=0):
    """Put a gh on PATH that records its arguments and prints the given output."""
    (tmp_path / "out").write_text(stdout)
    (tmp_path / "err").write_text(stderr)
    gh = tmp_path / "gh"
    gh.write_text(f'#!/bin/sh\necho "$@" > "{tmp_path}/args"\ncat "{tmp_path}/out"\ncat "{tmp_path}/err" >&2\nexit {status}\n')
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    return tmp_path / "args"


def test_parse_recorded_output():
    issues = parse_issue_list(RECORDED)
    assert [(i.number, i.title) for i in issues][:2] == [
        (6, "Start a run from the IDE"), (5, "Sessions pane: move tmux focus to a session")]
    assert len(issues) == 4 and all(i.run is None for i in issues)


@pytest.mark.parametrize("text", ["not json", '{"number": 1}', '[{"title": "no number"}]'])
def test_parse_unexpected_output(text):
    with pytest.raises(IssueSourceError, match="unexpected gh output"):
        parse_issue_list(text)


async def test_gh_issues_runs_gh_for_the_repository(tmp_path, monkeypatch):
    args = fake_gh(tmp_path, monkeypatch, stdout=RECORDED)
    issues = await gh_issues("Munsik-Park/Autoflow-ide")
    assert len(issues) == 4
    assert args.read_text().split() == ["issue", "list", "--repo", "Munsik-Park/Autoflow-ide", "--state", "open",
                                        "--limit", "100", "--json", "number,title"]


@pytest.mark.parametrize("stderr, status, message", [
    (NOT_SIGNED_IN, 4, "To get started with GitHub CLI, please run:  gh auth login"),
    (NO_REPO, 1, "GraphQL: Could not resolve to a Repository"),
    ("", 2, "gh exited with status 2"),
])
async def test_gh_issues_failure_carries_the_first_line(tmp_path, monkeypatch, stderr, status, message):
    fake_gh(tmp_path, monkeypatch, stderr=stderr, status=status)
    with pytest.raises(IssueSourceError) as e:
        await gh_issues("Munsik-Park/no-such-repo-xyz")
    assert str(e.value).startswith(message)


async def test_gh_not_installed(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(IssueSourceError, match="gh is not installed"):
        await gh_issues("owner/name")


def test_repository_from_workspace(tmp_path):
    (tmp_path / "repo.json").write_text(json.dumps({"github": "owner/name", "clone": "~/x", "base_branch": "main"}))
    assert repository_from_workspace(str(tmp_path)) == "owner/name"


@pytest.mark.parametrize("content", [None, "{}", '{"github": "noslash"}', "not json"])
def test_repository_from_workspace_errors(tmp_path, content):
    if content is not None:
        (tmp_path / "repo.json").write_text(content)
    with pytest.raises(IssueSourceError):
        repository_from_workspace(str(tmp_path))


from autoflow_ide.issues import gh_issue, parse_issue

CLOSED = (FIXTURES / "gh-issue-closed.json").read_text()
OPEN = (FIXTURES / "gh-issue-open.json").read_text()


def test_parse_recorded_issue():
    closed, open_ = parse_issue(CLOSED), parse_issue(OPEN)
    assert (closed.number, closed.state, closed.closed) == (3, "closed", True)
    assert closed.title == "word_count miscounts empty text, repeated spaces and newlines"
    assert (open_.number, open_.state, open_.closed) == (4, "open", False)


@pytest.mark.parametrize("text", ["not json", "[]", '{"number": 3, "title": "no state"}'])
def test_parse_unexpected_issue(text):
    with pytest.raises(IssueSourceError, match="unexpected gh output"):
        parse_issue(text)


async def test_gh_issue_asks_the_rest_api(tmp_path, monkeypatch):
    args = fake_gh(tmp_path, monkeypatch, stdout=CLOSED)
    issue = await gh_issue("Munsik-Park/autoflow-sandbox", 3)
    assert issue.closed
    assert args.read_text().split()[:3] == ["api", "repos/Munsik-Park/autoflow-sandbox/issues/3", "--jq"]


async def test_gh_issue_not_found(tmp_path, monkeypatch):
    error_json = '{"message":"Not Found","status":"404"}'
    fake_gh(tmp_path, monkeypatch, stdout=error_json, stderr="gh: Not Found (HTTP 404)\n", status=1)
    with pytest.raises(IssueSourceError, match=r"gh: Not Found \(HTTP 404\)"):
        await gh_issue("Munsik-Park/autoflow-sandbox", 999)
