# Fixtures

Output recorded from gh 2.103.3.

- `gh-issue-list.json`: stdout of `gh issue list --repo Munsik-Park/Autoflow-ide --state open --limit 5 --json number,title`, exit status 0.

Output recorded from gh 2.89.0 through the REST API:

- `gh-issue-closed.json`: stdout of `gh api repos/Munsik-Park/autoflow-sandbox/issues/3 --jq '{number: .number, title: .title, state: .state}'`, exit status 0 (closed by the merge of PR #6).
- `gh-issue-open.json`: the same for issue #4, open.
- issue not found (issue #999, exit status 1): stderr `gh: Not Found (HTTP 404)`; stdout carries the API's error JSON, not the `--jq` output.

Messages recorded from gh 2.103.3, used in `tests/test_issues.py`:

- not signed in (exit status 4): `To get started with GitHub CLI, please run:  gh auth login`
- unknown repository (exit status 1): `GraphQL: Could not resolve to a Repository with the name 'Munsik-Park/no-such-repo-xyz'. (repository)`

Output recorded from autoflow-coordinator `tools/status.py` at v0.7.0-rc2 (commit 988011d), the supported version:

- `status-run.json`: a run directory named `autoflow-sandbox-3-sample` (the coordinator's `<repo>-<issue>-<key>` form) with run.env (`ISSUE=3`, `TERMINAL=tmux`), two sessions in sessions.tsv whose claude processes are gone, and three rows in runbook.md's progress table (a pass, a missed verdict and a noted one).
- `status-error.json`: a run directory without run.env.
- `status-none.json`: no active runs.
