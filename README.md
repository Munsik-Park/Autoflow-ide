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
- [autoflow-coordinator](https://github.com/Munsik-Park/autoflow-coordinator) and its requirements, including Claude Code and the AutoFlow plugin
- `gh`, signed in, for the issue list

## License

[Apache License 2.0](LICENSE)
