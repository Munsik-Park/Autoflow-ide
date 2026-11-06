# Fixtures

Output recorded from gh 2.103.3.

- `gh-issue-list.json`: stdout of `gh issue list --repo Munsik-Park/Autoflow-ide --state open --limit 5 --json number,title`, exit status 0.

Output recorded from gh 2.89.0 through the REST API:

- `gh-issue-closed.json`: stdout of `gh api repos/Munsik-Park/autoflow-sandbox/issues/3 --jq '{number: .number, title: .title, state: .state}'`, exit status 0 (closed by the merge of PR #6).
- `gh-issue-open.json`: the same for issue #4, open.
- `gh-issue-pull.json`: recorded with gh 2.89.0, `gh api repos/Munsik-Park/opensre/issues/29 --jq '{number: .number, title: .title, state: .state, pull: (.pull_request != null)}'`, exit status 0: an open pull request, which this endpoint gives for its number like an issue. `gh-issue-not-pull.json`: the same for issue #3 of that repository, an open issue. The two files above were recorded before `pull` was asked for and have no such key.
- issue not found (issue #999, exit status 1): stderr `gh: Not Found (HTTP 404)`; stdout carries the API's error JSON, not the `--jq` output.

Messages recorded from gh 2.103.3, used in `tests/test_issues.py`:

- not signed in (exit status 4): `To get started with GitHub CLI, please run:  gh auth login`
- unknown repository (exit status 1): `GraphQL: Could not resolve to a Repository with the name 'Munsik-Park/no-such-repo-xyz'. (repository)`

Output recorded from autoflow-coordinator `tools/status.py` at v0.7.0-rc2 (commit 988011d), of the supported version v0.7.0. At v0.7.0-rc3 (commit 4b08d51) the same run directories give the same output with one more key per run, `coordination` (null here: run.env has no COORD_SURFACE); the IDE does not read it.

- `status-run.json`: a run directory named `autoflow-sandbox-3-sample` (the coordinator's `<repo>-<issue>-<key>` form) with run.env (`ISSUE=3`, `TERMINAL=tmux`), two sessions in sessions.tsv whose claude processes are gone, and three rows in runbook.md's progress table (a pass, a missed verdict and a noted one).
- `status-cycle.json`: a run directory named `autoflow-sandbox-5-cycle` (`ISSUE=5`) with five invocations in its records folder, written in this order: `analysis-1`, `build-1`, `review-1`, `build-2` (input `findings` from `review-1`) and `review-2` (input `previous_findings` from `review-1`), each with a `result.json`; a progress row for each, `review-1` missed and the others pass; three sessions whose processes are gone. `build-2` is a return caused by `review-1` and `review-2` a rerun of it. Recorded with `tools/status.py autoflow-sandbox-5-cycle`.
- `status-error.json`: a run directory without run.env.
- `status-coordination.json`: recorded at v0.7.0-rc4 (commit 9b2415d): a run directory named `autoflow-sandbox-3-coord` with run.env (`TERMINAL=tmux`, `ISSUE=3`, `BRANCH=issue-3-coord`, `COORD_SURFACE=%34`, `STATE=active`), no sessions.tsv, and a runbook.md with `- Reference: review-only: sample` and an empty progress table. Recorded with `tools/status.py <that directory>`.
- `status-none.json`: no active runs.
- `status-results.json`: recorded at v0.7.0-rc8 (commit 3cc4678), which gives each step from a row its `verdict_text` and `notes` (from v0.7.0-rc5, commit 8f44586, each step has its `out` and `result`; from v0.7.0-rc6, commit cd1dd77, each run its `checkout` and `records`; rc6 gave the same output without the two step keys, with the runbook before its Notes column was added), with `tools/status.py /home/claude/fx/runs/autoflow-sandbox-6-results` (tmux 3.4). The run directory is `runs/autoflow-sandbox-6-results` here (run.env `TERMINAL=tmux`, `CHECKOUT=/home/claude/fx/base/autoflow-sandbox/autoflow-sandbox-wt-6-results`, a folder that does not exist, `ISSUE=6`, `BRANCH=issue-6-results`, `STATE=active`, `RECORDS` the records folder below as it was at `/home/claude/fx/records/…`; three sessions whose processes are gone; a runbook.md with `- Reference: full` and seven rows under a header with a Notes column, as `tools/prepare.py` writes it; the `CI check (gh)` row's Verdict cell holds an escaped pipe `\|`). Its records folder is `records/autoflow-sandbox-6-results`, with seven invocations written in this order: `plan-1`, `build-1`, `review-1`, `build-2` (input `findings` from `review-1`), `review-2`, `review-3` and `review-4` (each `previous_findings` from `review-1`). The steps: `review-1` missed, `build-2` a return caused by it, a row outside AutoFlow (`CI check (gh)`), `review-2` with an empty verdict and a refused result, `review-3` failed, and `review-4` with no row and no result.json (running).

Module run output recorded from AutoFlow v0.7.0 (commit cc5f047), in `records/autoflow-sandbox-6-results`:

- `plan-1/{evaluation.json,result.json}`, `review-1/{review-findings.md,result.json}`, `build-1/result.json` and `build-2/result.json` (outputs and a tool request): copied unchanged from the operator's run of autoflow-sandbox #4 (`autoflow-sandbox-4-2026-11-05-220513-295dbab3`), so the paths in each result.json are where that run wrote them.
- `review-2/result.json`: written by `scripts/contracts/contract.sh check-in` on an invocation whose `pr` is `8`, exit status 1 (refused at check-in).
- `review-3/result.json`: written by `contract.sh check-out` after the run's `failure.md` and `tool-requests.json` (`[]`), exit status 3 (failed). No run in the operator's records had a `failure.md` (all 45 record folders listed on 2026-11-06), so this `failure.md` was written for the fixture, as the review contract's failure output describes it: what the run did and why it did not reach its goal.
- `review-4/.check-in.json`: written by `contract.sh check-in`, exit status 0; the run has not checked out.

Output recorded from tmux 3.4 (the operator's Mac has 3.2a):

- `tmux-list-panes.txt`: stdout of `tmux list-panes -a -F '#{window_id} #{pane_id} #{window_name}'`, exit status 0, on a server with a window `ide`, an auto-named one, `autoflow-sandbox#4` split in two panes, `autoflow-sandbox#12`, `opensre#5`, `autoflow-sandbox#4b`, `my autoflow-sandbox#9`, and `autoflow-sandbox#7` in a second session. Windows were created with `new-window -n <name>`, which turns automatic-rename off for them (`show-options -w -v automatic-rename` gives `off`). In a format, tmux 3.4 prints a tab as `_`, so the fields are separated by spaces.
- `tmux-places.txt`: stdout of `tmux list-panes -a -F '#{pane_id} #{session_name}:#{window_index} #{window_name}'`, exit status 0, on a server with a session `1` (windows `ide` and an auto-named one) and a session `2` (an auto-named window, `autoflow-coordinator#59` split in two panes, and `my window`).
- a window that is gone (exit status 1): stderr `can't find window: @99`; a pane that is gone: `can't find pane: %99`.
