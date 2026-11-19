# Review report — Munsik-Park/autoflow-coordinator#120 (round 2, after fix 1)

- Pull request: Munsik-Park/autoflow-coordinator#120, base `main`, head branch `issue-119-2026-11-19-180359-3cbd8c18`.
- PR head: `9a46918b426ac7e1d9830c9b081f442ee2f88af1` (9a46918). It was the head at hand-off, at reviewer start (18:15 KST) and after both reviews (`gh pr view 120 --json headRefOid`, 18:18 KST), so it did not move during the round.
- Head each reviewer saw:
  - claude: 9a46918. Its review names no commit. It ran 18:15:36–18:17:32 KST from the run worktree, whose HEAD is 9a46918, while the PR head was 9a46918.
  - codex: 9a46918. Its log shows `From 9a46918b426ac7e1d9830c9b081f442ee2f88af1` in `gh pr diff 120 --patch`, and `git log -3 --oneline` → `9a46918 coordinator-role.md > Tools: …` in the run worktree.
- Criteria: issue #119's Scope, Out of scope and Done when, and the workspace CLAUDE.md rule-document practice.
- Previous report: `../review-1/report.md`.
- Reviews as written: `reviews/claude.md`, `reviews/codex.md`.

## Verdict

**Highest severity among findings that hold: Medium** (finding R2-1). One Done-when item is not met for a workspace that is a symbolic link.

## Round 1 findings

| Round 1 | Severity | Status | Grounds |
|---|---|---|---|
| F1: coordinator-role.md > Tools does not list the base-overlap stop | Low | **Closed** | The 9a46918 diff adds the stop to the `tools/modules.py` line ("or the `modules` clone and the base being the same folder or either lying inside the other ([SETTINGS.md](SETTINGS.md) > Module locations) stops it") and to the `tools/prepare.py` line ("when the `modules` clone and the base are the same folder or either lies inside the other …"). It changes no other line. |
| F2: implementation detail in new docstrings (held in part: the `overlaps` loop narration) | Low | **Not closed: kept by the coordinator's disposition** | `tools/modules.py:150-153` is unchanged at 9a46918. The coordinator kept it because the docstring states the identity comparison that callers rely on. Neither reviewer raised it this round. The part of F2 that did not hold, the `prepare.py` cross-reference, was raised again by claude; see R2-2. |

## Round 2 findings

### R2-1. A workspace that is a symbolic link passes the overlap check while the modules clone lies inside it — Medium — holds

- Reviewer: codex (finding 1).
- Claim: `tools/prepare.py:204` compares `modules` only with the configured base. Line 207 resolves the workspace (`os.path.realpath(os.path.join(config['base'], args.workspace))`) after that check, and nothing compares the resolved workspace with `modules`. So when `<base>/<ws>` is a symlink to a folder outside the base that holds the modules clone, the run is prepared.
- Weighed: reproduced at 9a46918 with a sandbox HOME under `review-2/tmp/sym`:
  - Layout: `base/ws -> outside`. `outside/autoflow-module` is a git clone and is set as `modules`. `outside/repo.json` names the scratch clone `outside/clone`. `outside/gh-token` is mode 600, and `GH_TOKEN` matches it.
  - Command: `env -u GIT_SSH_COMMAND HOME=<sandbox> GH_TOKEN=dummytoken python3 tools/prepare.py ws 1`.
  - Output: `…/outside/clone: no remote origin` / `origin must be the workspace's repository over HTTPS (WORKSPACE.md > gh-token)`, exit 1.
  - The run got past the overlap check, `read_repo`, `read_token` (`outside` is in no git worktree), the terminal check, `modules_clone` and the clone's git settings (`include.path=gh-token.gitconfig` was written into the scratch clone). It stopped only because the scratch clone has no origin, a gap a real workspace clone does not have.
- Criterion it breaks: Done when, "`tools/prepare.py` stops before creating the run when `modules` resolves to, or inside, `<base>/<workspace>` … and the message names both paths". Here `<base>/ws` resolves to `outside`, and `modules` resolves inside it.
- Severity: Medium. Mitigating facts:
  - The layout is not one the workspace rules produce. `WORKSPACE.md` > Finding lists workspaces with `find … -mindepth 2 -maxdepth 2 -type f -name repo.json`, which does not descend through a symlinked `<base>/ws`. A symlinked workspace is reached only when the operator names it.
  - The run's writes land in `outside/` (`CHECKOUT` = `<workspace>/<repo>-wt-…`), beside the modules clone, not inside it.
  - When the symlink points at the modules clone itself, `read_token` stops: the workspace is then inside a git worktree, though the message is not the both-paths one.
  - Even so, prepare.py accepts this configuration and an issue acceptance criterion is not met for it.
- Fix direction (codex): before `read_repo`/`read_token`, also compare the resolved workspace with `modules` using `overlaps`, and name both paths. Keep the base-level check.

### R2-2. `tools/prepare.py` docstring names another file's function — Low — does not hold

- Reviewer: claude (finding 1).
- Claim: "(tools/modules.py, overlaps)" at `tools/prepare.py:4-5` names another file's path, which "the comment rule" does not allow, and goes stale if `overlaps` moves.
- Grounds that it does not hold: the rule claude applies is its own reviewer rule. Neither the issue nor the workspace CLAUDE.md sets it, and the repository's tool docstrings name other tools as a matter of course. At 9a46918:
  - `tools/prepare.py:24`: "and the user location (tools/config.py user-modules) as USER_MODULES".
  - `tools/modules.py:4`: "both taken from tools/config.py".
  - `tools/invocation.py:23`: "is written beside them by tools/watch.py".
- This is the same claim as the part of round 1 F2 that did not hold, on the same grounds.

## Housekeeping

- Nothing was posted on the PR.
- A reviewer's run left new ignored bytecode in the worktree at 18:16:19 KST (`tools/__pycache__/` approval, approve, cleanup, config, ended, invocation, merge, prepare, session, status, terminal_check, terminal, usage and watch `.pyc`; `tools/terminals/__pycache__/` cmux and tmux `.pyc`). I removed those files. The worktree is now as found: clean, HEAD 9a46918, with only the pre-existing `modules`, `remotes` and `terminals/__init__` `.pyc` files from 18:06:42.
- My reproduction ran with `PYTHONDONTWRITEBYTECODE=1`, and all its files are under `review-2/tmp`.
