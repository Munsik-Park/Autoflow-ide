# autoflow-ide

An issue-based terminal IDE for [AutoFlow](https://github.com/Munsik-Park/autoflow).

You pick an issue, and the IDE runs it through the coordination session of [autoflow-coordinator](https://github.com/Munsik-Park/autoflow-coordinator): the coordination session proposes a workflow, runs AutoFlow modules in work sessions and judges each result. The IDE puts the issues, the run's progress and the sessions on one screen.

Status: early development. The design is in [docs/design.md](docs/design.md).

## The repositories

| Repository | Place |
|---|---|
| [autoflow](https://github.com/Munsik-Park/autoflow) | The back end: the contract modules. |
| [autoflow-coordinator](https://github.com/Munsik-Park/autoflow-coordinator) | The coordination session and its tools, which open work sessions in tmux and judge their results. |
| autoflow-ide (this repository) | The screen over the coordinator: issues, runs and sessions in one terminal UI. |

The IDE uses the coordinator through its command-line tools and their output. It does not carry the coordinator's code.

## Requirements

- Python 3.9 or later
- tmux, with the IDE started inside it
- [autoflow-coordinator](https://github.com/Munsik-Park/autoflow-coordinator) v0.7.0 (any 0.7.0-rcN) and its requirements, including Claude Code and the AutoFlow plugin v0.7.0
- `gh`, signed in, for the issue list

## Run

```
pip install -e .
autoflow-ide --repo owner/name          # open issues of a repository
autoflow-ide --workspace <workspace>    # the repository named in a coordinator workspace's repo.json
autoflow-ide --demo                     # placeholder data
autoflow-ide --coordinator <clone>      # every workspace of the coordinator's base
```

With `--coordinator` and neither `--repo` nor `--workspace`, the left pane (titled `Workspaces`, its column `Workspace / Issue` and its key `1 Workspaces`; with one repository it is `Issues`) lists every workspace of the coordinator's base (`tools/config.py base`, each folder with a `repo.json`), in name order, each under a bold row with its name, and under it the 5 most recent open issues of its repository, newest first, an issue with a run first and marked `●`. The pane is where an issue is started and watched, not where the work is chosen (#44). A workspace whose issues cannot be read says why on its row, and the others still show. The issues are read with the GitHub account of the shell the IDE is started from (`gh`, with its `GH_TOKEN` when set): start it where that account is the one you work with. A workspace whose repository that account cannot read shows by its name only, with GitHub's error on its row and no issues, so no run can be started there; that is intended, as work on a repository needs access to it anyway. A token narrowed to a few repositories (such as one kept for working on this clone) hides the others. On a workspace's row the run pane is empty and the info shows that workspace; `s` starts nothing there, and Enter folds it (`▸`, with the number of its rows) or unfolds it (`▾`). At start the workspaces with a run and the 3 chosen most recently are unfolded, the others folded; a workspace is chosen by unfolding it or starting a run in it, and dropped by folding it. The choices are kept in `.ide-state.json` at the top of the clone the IDE runs from (ignored by git), or in `~/.config/autoflow-ide/state.json` when it runs from elsewhere. A run that starts unfolds its workspace. `a` shows only the issues with a run, under their workspaces whatever is folded (the pane's title says `Workspaces · running`, or `Issues · running` with one repository, and the status line `no issue has a run` when there is none), and `a` again everything; with one repository it shows its issues with a run.

An issue that is not among the recent ones is reached by its number, in the workspace the cursor is in (on an issue or on a workspace's row): `#` and the number, then Enter, moves to exactly that issue when it is listed and otherwise looks it up on GitHub and shows it with that workspace's issues (its row says what the number is when it is not an open issue: `(closed)`, `(PR)` or `(closed PR)`, as issues and pull requests share the numbers, and `s` starts only an open issue). Esc closes the box, and with the box closed clears what was found. Searching by words is left for later (#52).

Add `--coordinator <autoflow-coordinator clone>` to show the active runs: the IDE reads the coordinator's `tools/status.py` every 5 seconds and shows each run under its issue. Above the run pane, the coordinator's base (where the workspaces are) and where it was set: `global settings` (`~/.autoflow/coordination.json`), `local settings` (`coordination.local.json` in the coordinator clone) or the `built-in default`, read from `tools/config.py` whenever the issues are read, as the coordinator's settings decide it and the IDE does not change it (#58); then the highlighted issue's repository and its workspace (name, clone and base branch, from the workspace's `repo.json`) and, when it has a run, the run's name, workflow and branch, its worktree and record folder, and where its coordination session is in tmux (session:window and the window's name). A run name, base, clone, worktree or record folder too long for the pane is cut in the middle, so its start (where it is) and its end (which run it is) stay in view. The run pane marks a return `↩` and a rerun `↺`, with the step that caused it (`↩ build-2 ← review-1`), as the coordinator's run-status pane does. In the run pane, Enter opens what happened in a step: its state, verdict, result status (accepted, refused or failed), session and output folder; the Verdict and Notes cells of its row in the run record, which say what work outside AutoFlow did and how the coordinator disposed of a run's findings; and what the module run left in its folder, read from the files AutoFlow writes: why it did not reach its goal (`failure.md`), the defects of a refusal, an evaluation's scores, summary and recommendations (`evaluation.json`), the review's findings (`review-findings.md`), or else the outputs `result.json` lists, numbered: a number key opens that output in its own window over the detail (Markdown rendered, JSON indented, other text as it is, a folder's entries; the first 512 KB of a long file), and Esc goes back to the step (#67); and what the run lacked (its tool requests). `c` goes to the step that caused it. In the sessions pane, Enter moves tmux focus to that session's pane.

The bottom-right corner shows the commit the IDE was started from (`ide 68afb41`), read once when it starts, so an IDE left running on older code after a pull can be told apart; when the IDE does not run from its own clone, it shows the package version.

The open issues are read again every minute, when a step of a run changes its state or verdict or a step is added, and when a run ends, so an issue closed by a merge leaves the list without a key press (an issue that still has a run stays as a "(closed)" row). `r` reloads them at once.

`s` starts a run for the highlighted issue, as an operator does by hand: it opens a tmux window named `<workspace>#<issue>` with a shell in the coordinator clone and starts Claude Code there with `Workspace <workspace>, issue #<issue>.` as its first prompt. The workspace is the one in the coordinator's base (`tools/config.py base`) whose `repo.json` names the repository. The coordination session then presents the workflow for approval in that window. `s` again, before the run is prepared, goes back to that window.

`w` adds a workspace for a repository, as `s` starts a run: give the repository's GitHub address — the web address (`https://github.com/owner/name`), a clone address (`git@github.com:owner/name.git`) or `owner/name` — and Enter (Esc closes the box), and the IDE opens a tmux window named `workspace:<owner/name>` with Claude Code in the coordinator clone and `Create a workspace for <owner/name>.` as its first prompt, with `, cloned from <address>` when the address was more than `owner/name`, so the clone is made the way it was given. The coordination session asks for the rest (the clone, the base branch) and writes the workspace in the base (the coordinator's `WORKSPACE.md`, "Creating"); the IDE does not write it. When a workspace of the base already names the repository, nothing is opened. `w` with the same repository again, before the workspace shows, goes back to that window. The workspace shows on the next read of the issues (`r`, or within a minute), unfolded as one chosen recently; with `--repo` or `--workspace` the IDE shows only its one repository. The IDE does not close that window (#60).

The IDE closes such a window when its issue is done: the issue is closed (asked of GitHub) and has no active run, and the window's screen has not changed for 5 minutes, so a last report is not cut off and the operator can come back to the session after a merge (while it waits for input, its screen does not change). An issue can take several runs, so a run ending while its issue is open keeps the window. The windows are found by their `<workspace>#<issue>` name, so those opened before the IDE restarted are closed too, and so is one named that way by hand. The status line says when a window is closed; the session can be resumed with `claude --resume` in the coordinator clone.

The status line starts with a notice of something that just happened (a coordination session opened, a window closed, a focus moved, an issue found by `#`), so a narrow terminal does not cut it off, and the notice goes 10 seconds later. Then come the repository, the issue (with what it is when not an open issue: `#2 (closed PR)`) and the run, and what is true now: the issue and run counts, a read error until the next read succeeds.

## Tests

`pip install -e '.[test]'`, then `pytest` with the test files to run.

- **CI** (`.github/workflows/test.yml`) runs on every pull request and on main, and a pull request is merged when it passes. It runs only the tests of what the IDE reads from other tools: `tests/test_runs.py` (the coordinator's `status.py` output and `VERSION`), `tests/test_results.py` (the files AutoFlow writes in a module run's output folder), `tests/test_issues.py` (gh output), `tests/test_focus.py`, `tests/test_start.py` and `tests/test_windows.py` (the coordinator's `config.py` and the tmux commands). They run on output recorded from those tools (`tests/fixtures/README.md` says from which versions), so they are what breaks when a tool changes.
- **Screen tests** (`tests/test_app.py`) are not in CI. A pull request runs the ones of what it changes.
- **One-off checks**, such as replaying a recorded run against a real `status.py`, are not kept as tests. The pull request states what was checked and the result.
- A new test goes into CI only when it checks what the IDE reads from another tool, on recorded output.

## License

[Apache License 2.0](LICENSE)
