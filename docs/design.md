# Design

## Goal

Work on a repository issue by issue from one terminal screen. The operator picks an issue, approves the workflow the coordination session proposes, watches the run and decides the merge. The IDE shows these on one screen and moves between them; the coordination session and work sessions do the work.

## Layers

| Layer | Repository | Role |
|---|---|---|
| Screen | autoflow-ide | Shows issues, runs and sessions; starts runs; moves focus between sessions |
| Coordination | autoflow-coordinator | Proposes the workflow, prepares runs, opens work sessions, judges results |
| Modules | autoflow | The contract modules a work session runs |

The IDE talks to the coordinator only through the coordinator's command-line tools and the files they write:

- `tools/status.py` prints where each run stands as JSON (runs, steps, verdicts, sessions). The IDE reads it to draw the run view.
- The terminal operations (open a session, send a line, read a screen, rename) are the coordinator's. The IDE does not open work sessions itself.
- What the IDE needs and the coordinator lacks is raised as an issue in autoflow-coordinator.

This keeps the IDE replaceable: a change of the agent runtime under the coordinator does not reach the IDE.

## Runtime shape

The IDE runs in a tmux pane. The coordination session and the work sessions are tmux panes the coordinator opens beside it. The IDE moves the operator between them with tmux and reads their state from the coordinator's tools.

## Screen

```
+----------------+--------------------------------+------------------+
| Issues         | Run                            | Sessions         |
|                |                                |                  |
| #12 ...        | step  module   state  verdict  | coordination  *  |
| #15 ...  (run) | 1     analysis pass   ...      | work-1   running |
| #18 ...        | 2     build    running         | work-2   idle    |
|                |                                |                  |
+----------------+--------------------------------+------------------+
| status line: repository, run, key hints                            |
+--------------------------------------------------------------------+
```

- Issues: open issues of the workspace's repository, from GitHub. An issue with a run shows its run.
- Run: the selected run's steps from `status.py`, with state and verdict.
- Sessions: the run's sessions with their state (working, waiting-input, stall and the rest), from `status.py`. Selecting one moves tmux focus to its pane, by the `surface` that `status.py` reports. The coordinator's terminal operations have no focus operation, so the IDE calls tmux for this one; adding it to the coordinator is a coordinator issue. `status.py` lists the work sessions of `sessions.tsv`; the coordination session's surface (`COORD_SURFACE` in `run.env`) is not in its output, so moving focus to the coordination session needs the coordinator to report it.

## Supported versions

The IDE works against fixed versions of autoflow-coordinator and AutoFlow, as the coordinator works against the AutoFlow version in its `VERSION`. The IDE reads the coordinator's tool output, such as the JSON of `status.py`, in the shape of the supported coordinator version. Following a newer version is its own change.

| Repository | Supported version |
|---|---|
| autoflow-coordinator | v0.7.0-rc1 (`VERSION` 0.7.0-rc1) |
| autoflow | v0.7.0, the version that coordinator supports |

With `--coordinator`, the IDE reads the clone's `VERSION` and shows on the status line when it differs from the supported one.

## Stack

Python with [Textual](https://textual.textualize.io/). The coordinator already needs Python 3, so the IDE adds a package, not a runtime.

## Milestones

1. IDE shell: the three-pane layout, key bindings and status line, with placeholder data.
2. Issues pane: open issues from GitHub through `gh`.
3. Run pane: runs and steps from the coordinator's `status.py`, refreshed periodically.
4. Sessions pane: list the run's sessions and move tmux focus to one.
5. Start a run: open the coordination session for the selected issue from the IDE.
