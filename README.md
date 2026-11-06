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
```

Add `--coordinator <autoflow-coordinator clone>` to show the active runs: the IDE reads the coordinator's `tools/status.py` every 5 seconds and shows each run under its issue. Above the run pane, the highlighted issue's repository and its workspace (name, clone and base branch, from the workspace's `repo.json`) and, when it has a run, the run's name, workflow and branch and where its coordination session is in tmux (session:window and the window's name). In the run pane, Enter opens what happened in a step: its state, verdict, session, and whether it is the module's first run, a rerun or a return and which step sent it back (`c` goes to that step). In the sessions pane, Enter moves tmux focus to that session's pane.

The open issues are read again every minute, when a step of a run changes its state or verdict or a step is added, and when a run ends, so an issue closed by a merge leaves the list without a key press (an issue that still has a run stays as a "(closed)" row). `r` reloads them at once.

`s` starts a run for the highlighted issue, as an operator does by hand: it opens a tmux window named `<workspace>#<issue>` with a shell in the coordinator clone and starts Claude Code there with `Workspace <workspace>, issue #<issue>.` as its first prompt. The workspace is the one in the coordinator's base (`tools/config.py base`) whose `repo.json` names the repository. The coordination session then presents the workflow for approval in that window. `s` again, before the run is prepared, goes back to that window.

The IDE closes such a window when its issue is done: the issue is closed (asked of GitHub) and has no active run, and the window's screen has not changed for a minute, so a last report is not cut off. An issue can take several runs, so a run ending while its issue is open keeps the window. The windows are found by their `<workspace>#<issue>` name, so those opened before the IDE restarted are closed too, and so is one named that way by hand. The status line says when a window is closed; the session can be resumed with `claude --resume` in the coordinator clone.

## Tests

`pip install -e '.[test]'`, then `pytest` with the test files to run.

- **CI** (`.github/workflows/test.yml`) runs on every pull request and on main, and a pull request is merged when it passes. It runs only the tests of what the IDE reads from other tools: `tests/test_runs.py` (the coordinator's `status.py` output and `VERSION`), `tests/test_issues.py` (gh output), `tests/test_focus.py`, `tests/test_start.py` and `tests/test_windows.py` (the coordinator's `config.py` and the tmux commands). They run on output recorded from those tools (`tests/fixtures/README.md` says from which versions), so they are what breaks when a tool changes.
- **Screen tests** (`tests/test_app.py`) are not in CI. A pull request runs the ones of what it changes.
- **One-off checks**, such as replaying a recorded run against a real `status.py`, are not kept as tests. The pull request states what was checked and the result.
- A new test goes into CI only when it checks what the IDE reads from another tool, on recorded output.

## License

[Apache License 2.0](LICENSE)
