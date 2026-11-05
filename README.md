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
- [autoflow-coordinator](https://github.com/Munsik-Park/autoflow-coordinator) v0.7.0-rc2 and its requirements, including Claude Code and the AutoFlow plugin v0.7.0
- `gh`, signed in, for the issue list

## Run

```
pip install -e .
autoflow-ide --repo owner/name          # open issues of a repository
autoflow-ide --workspace <workspace>    # the repository named in a coordinator workspace's repo.json
autoflow-ide --demo                     # placeholder data
```

Add `--coordinator <autoflow-coordinator clone>` to show the active runs: the IDE reads the coordinator's `tools/status.py` every 5 seconds and shows each run under its issue. In the sessions pane, Enter moves tmux focus to that session's pane.

`r` reloads the issues.

Tests: `pip install -e '.[test]'` and `pytest`.

## License

[Apache License 2.0](LICENSE)
