# Design

## Goal

Work on a repository issue by issue from one terminal screen. The operator picks an issue, approves the workflow the coordination session proposes, watches the run and decides the merge. The IDE shows these on one screen and moves between them; the coordination session and work sessions do the work.

## Layers

| Layer | Repository | Role |
|---|---|---|
| Screen | autoflow-ide | Shows issues, runs and sessions; starts runs; moves focus between sessions |
| Coordination | autoflow-coordinator | Proposes the workflow, prepares runs, opens work sessions, judges results |
| Modules | autoflow-module | The modules a work session runs |

The IDE talks to the coordinator only through the coordinator's command-line tools and the files they write:

- `tools/status.py` prints where each run stands as JSON (runs, steps, verdicts, sessions). The IDE reads it to draw the run view.
- The terminal operations (open a session, send a line, read a screen, rename) are the coordinator's. The IDE does not open work sessions itself.
- What the IDE needs and the coordinator lacks is raised as an issue in autoflow-coordinator.

Exceptions, for browser control (#86), each until the coordinator has the operation (an autoflow-coordinator issue asks for them, as above):

- The IDE runs tmux itself for the browser. While a page shows a session, it reads that pane's screen with its colours, its cursor and its size every half second (`capture-pane -p -e`, `display-message`); this attaches no tmux client, so no window's size changes. It enters a message into a coordination session as the coordinator's `submit` does (`load-buffer`, `paste-buffer -r`, Enter), and only after the coordinator's checks, which it copies: not in copy mode, an empty input area, at most 700 bytes. It sends the keys that answer a prompt (a digit, the arrow keys, Enter, Esc) to a session whose screen shows one. It opens the window of a coordination session started from the browser with `new-window -d`, so the operator's window stays. It keeps the session id it starts a coordination session with on that session's pane (`set-option -p @autoflow-session-id`). The coordinator's terminal operations have no operation that streams a pane, sends raw keys or answers a prompt, and `tools/session.py send` reaches only the work sessions of `sessions.tsv`.
- The IDE reads Claude Code's session record files itself, for the browser. A work session's are found from the session id its run's `sessions.tsv` gives and the folder the coordinator's `tools/session.py` reads (`~/.claude/projects/<checkout, each character other than a letter or digit as ->/<id>.jsonl`, and `<id>/subagents/*.jsonl`). A coordination session's are found from the id the IDE started it with (`claude --session-id`), in the coordinator clone's folder. This reaches past the coordinator's tools and the files they write.

This keeps the IDE replaceable: a change of the agent runtime under the coordinator does not reach the IDE, except its browser control's session records (above), until `tools/status.py` reports each session's transcript.

## Runtime shape

The IDE runs in a tmux pane. The coordination session and the work sessions are tmux panes the coordinator opens beside it. The IDE moves the operator between them with tmux and reads their state from the coordinator's tools. With browser control on, the IDE also runs a web server in its own process, bound to 127.0.0.1, which serves one page and acts through the same code as the screen.

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

- Issues: open issues of the workspace's repository, from GitHub. An issue with a run shows its run. An issue with several coordination sessions shows each under it: a session is one coordination pane (the pane its window opened with, the run's `COORD_SURFACE` once prepared), made on each read from the windows named `<workspace>#<issue>`, the active runs of the issue and the panes opened from the IDE, and a pane holding several runs is one session per run.
- Run: the selected run's steps from `status.py`, with state and verdict: the highlighted session's run, and none on the row of an issue with several sessions.
- Sessions: the run's sessions with their state (working, waiting-input, stall and the rest), from `status.py`. Selecting one moves tmux focus to its pane, by the `surface` that `status.py` reports. The coordinator's terminal operations have no focus operation, so the IDE calls tmux for this one; adding it to the coordinator is a coordinator issue. `status.py` lists the work sessions of `sessions.tsv`; the coordination session's surface (`COORD_SURFACE` in `run.env`) is not in its output, so moving focus to the coordination session needs the coordinator to report it.

## Supported versions

The IDE works against a fixed version of autoflow-coordinator. Improvements to the coordinator for a version go up as `-rcN`. The IDE supports a coordinator version without its `-rcN`, as the coordinator's `prepare.py` compares versions. It reads the coordinator's tool output, such as the JSON of `status.py`, in the shape of that version: an rc adds to the output, and following a newer version is its own change.

| Repository | Supported version |
|---|---|
| autoflow-coordinator | v0.8.0 (`VERSION` 0.8.0 or 0.8.0-rcN) |
| autoflow-module | any commit: the coordinator does not check module versions, and each run records the commit it used |

With `--coordinator`, the IDE reads the clone's `VERSION` and shows on the status line when, without `-rcN`, it differs from the supported one.

## Stack

Python with [Textual](https://textual.textualize.io/). The coordinator already needs Python 3, so the IDE adds a package, not a runtime. Browser control adds aiohttp (an optional extra, `browser`), on the IDE's own event loop, and a page that draws a session's screen with xterm.js (`@xterm/xterm`, MIT), whose files are kept in the package.

## Milestones

1. IDE shell: the three-pane layout, key bindings and status line, with placeholder data.
2. Issues pane: open issues from GitHub through `gh`.
3. Run pane: runs and steps from the coordinator's `status.py`, refreshed periodically.
4. Sessions pane: list the run's sessions and move tmux focus to one.
5. Start a run: open the coordination session for the selected issue from the IDE.
6. Browser control: a localhost page with the IDE's screen, its start and go-to, a session's view, records and input (#86).
