# Review report — Munsik-Park/autoflow-coordinator#120 (round 3, after fix 2)

- Pull request: Munsik-Park/autoflow-coordinator#120, base `main`, head branch `issue-119-2026-11-19-180359-3cbd8c18`.
- PR head: `101c511cbd3fc67cdbd7a5947eee66453477f0a0` (101c511). It was the head at hand-off, at reviewer start (18:22 KST) and after both reviews (`gh pr view 120 --json headRefOid`, 18:25 KST), so it did not move during the round.
- Head each reviewer saw:
  - claude: 101c511. Its review names no commit. It ran 18:22:49–18:24:58 KST from the run worktree, whose HEAD is 101c511, while the PR head was 101c511. It quotes the 101c511 docstring text "nor the clone and the workspace as resolved".
  - codex: 101c511. Its log shows `headRefOid: 101c511cbd3f…` (`gh pr view`), `From 101c511cbd3f…` in the patch, and `git log` → `101c511 prepare.py: also stop when …` in the run worktree.
- Criteria: issue #119's Scope, Out of scope and Done when, and the workspace CLAUDE.md rule-document practice.
- Previous reports: `../review-1/report.md`, `../review-2/report.md`.
- Reviews as written: `reviews/claude.md`, `reviews/codex.md`.

## Verdict

**Highest severity among findings that hold: Low** (finding R3-1, a comment). No Critical, High or Medium finding, and every Done-when item is met.

## Earlier findings

| Finding | Severity | Status | Grounds |
|---|---|---|---|
| R1 F1: coordinator-role.md > Tools does not list the base-overlap stop | Low | **Closed** (round 2) | 9a46918. At 101c511 the `tools/prepare.py` line also names the workspace overlap: "when the `modules` clone and the base, or the workspace as resolved, are the same folder or either lies inside the other". |
| R1 F2: `overlaps` docstring narrates its loop | Low | **Not closed: kept by the coordinator's disposition** | `tools/modules.py:150-153` is unchanged. Neither reviewer raised it this round. |
| R2-1: a symlinked workspace holding the modules clone passes the check | Medium | **Closed** | See the reproduction below. |
| R2-2: `prepare.py` docstring names another file's function | Low | **Did not hold** (round 2) | Raised again by claude this round as R3-2, and it does not hold on the same grounds. |

R2-1 was re-run at 101c511 with the round 2 sandbox layout under `review-3/tmp/sym`, using `env -u GIT_SSH_COMMAND HOME=<sandbox> GH_TOKEN=dummytoken python3 tools/prepare.py <ws> 1`:

| Case | Result | Exit |
|---|---|---|
| `base/ws -> outside`, modules `outside/autoflow-module` | "modules …/outside/autoflow-module and the workspace …/outside are the same folder or one lies inside the other: set `modules` so that the modules clone lies outside the workspace, or point the workspace elsewhere (SETTINGS.md)" | 1 |
| Same link, modules `outside`, so the workspace resolves to the modules clone | The same stop, naming both paths | 1 |
| Modules `mods2/am`, outside both | Gets past the new check and stops later, at the scratch clone's missing origin | 1 |

- The new check sits at `tools/prepare.py:209`, after `workspace` is resolved (line 208) and before `read_repo`/`read_token`.
- No run directory was created in any case (`<sandbox>/root` absent).
- `SETTINGS.md` > Module locations, `coordinator-role.md` > Tools and the `prepare.py` docstring state the new stop.

## Round 3 findings

### R3-1. The inline comment at `tools/prepare.py:209` says a symlinked workspace resolves outside the base — Low — holds

- Reviewer: codex (finding 1).
- Claim: the comment reads "# a workspace that is a symlink resolves outside the base". A symlink `<base>/ws` can point to another directory under the base, so the statement is not true in general.
- Weighed: holds. `os.path.realpath` of `<base>/ws -> <base>/other` is `<base>/other`, which is inside the base. In that layout the base check at line 205 already covers a modules clone inside it. The new check is needed only when the link resolves outside the base, which is what the comment means to say. Its wording ("resolves") states it as always true.
- Severity: Low. It is comment text only, and the code is right. Fix direction: "can resolve outside the base".

### R3-2. The `tools/prepare.py` docstring names another file's function — Low — does not hold

- Reviewer: claude (finding 1).
- Claim: "(tools/modules.py, overlaps)" at `tools/prepare.py:5` breaks the comment rule. The line wrap of the extended sentence is also irregular, which claude notes as style only.
- Grounds that it does not hold: these are round 2 R2-2 and round 1 F2, raised again with the same claim. The rule is the reviewer's own, not one the issue or the workspace CLAUDE.md sets. The repository's tool docstrings name other tools as a matter of course: `tools/prepare.py:25` "(tools/config.py user-modules)", `tools/modules.py:4` "both taken from tools/config.py", `tools/invocation.py:23` "by tools/watch.py".
- The uneven wrap claude notes (`tools/prepare.py:5-7`) is real but changes nothing a reader or tool relies on. It is recorded here as a style note, not as a finding.

## Done when at 101c511

- `tools/config.py` prints the `modules` default outside the default base. `SETTINGS.md` and the README show the same value. Unchanged since round 1.
- `tools/prepare.py` stops before creating the run, naming both paths, for each of these cases:
  - `modules` at or inside `<base>/<workspace>`: covered by the base check.
  - `modules` inside a symlinked workspace's target: the new check.
  - `<base>/<workspace>` resolving inside `modules`: the base check when the base lies inside `modules`, and the new check when a symlinked workspace resolves there.
- A run prepares as before when `modules` lies outside the base: the "outside both" case gets past both checks.
- The README row uses the module interface's words, unchanged since round 1.

## Housekeeping

- Nothing was posted on the PR.
- At the start of this round the worktree was clean with no ignored files. During the round a reviewer's run created `tools/__pycache__/` (config, modules and prepare `.pyc`, 18:23:56 KST) despite `PYTHONDONTWRITEBYTECODE=1` in the wrapper's environment. I removed it.
- `git status --porcelain --ignored` is now empty, and HEAD is 101c511.
- My reproduction ran with `PYTHONDONTWRITEBYTECODE=1`, and all its files are under `review-3/tmp`.
