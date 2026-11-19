# Review report — Munsik-Park/autoflow-coordinator#120 (round 1)

- Pull request: Munsik-Park/autoflow-coordinator#120, "modules: default outside the base, and a stop when the modules clone and the base overlap (#119)", base `main`, head branch `issue-119-2026-11-19-180359-3cbd8c18`.
- PR head: `53948479f97fbf77b5abe4240944a51e544cbff9` (5394847) at hand-off, at reviewer start (18:09 KST) and after both reviews (`gh pr view 120 --json headRefOid`, 18:12 KST). It did not move during the review.
- Head each reviewer saw:
  - claude: 5394847. Its review names no commit. It ran 18:09:31–18:11:34 KST from the run worktree, whose HEAD is 5394847 (`git rev-parse HEAD`), and read the PR through gh while the PR head was 5394847.
  - codex: 5394847. Its log shows `git log -1 --oneline` → `5394847 modules: default outside the base, …` in the run worktree, and `gh pr diff 120`.
- Criteria: issue #119's Scope, Out of scope and Done when, and the workspace CLAUDE.md rule-document practice (current method only, a rule enforced by a tool, no line beyond the Scope).
- Reviews as written: `reviews/claude.md`, `reviews/codex.md`.

## Verdict

**Highest severity among findings that hold: Low.** No Critical, High or Medium finding. Every Done-when item is met (checks below).

## Findings

### 1. coordinator-role.md's tool reference does not list the new base-overlap stop — Low — holds

- Reviewer: claude (finding 1).
- Claim: `coordinator-role.md:144` lists what stops `tools/modules.py`, and `coordinator-role.md:145` lists what stops `tools/prepare.py`. Neither line names the stop this PR adds when the modules clone and the base overlap (`tools/modules.py:199-201`, `tools/prepare.py:203-206`).
- Weighed: confirmed at 5394847.
  - Line 144 ends: "a missing default location, a user location that is not a folder …, or `modules` and `<home>/modules` resolving to the same folder stops it."
  - Line 145: "It stops without creating it when the `modules` clone is not a git clone or is the same folder as `<home>/modules`, or on the workspace's `gh-token` …"
  - Line 144 is written as a complete list, so the PR leaves the coordination session's reference out of date. That breaks "current method only".
- Severity: Low. The stop message names both paths and says how to fix the setting.
- Scope note: Scope item 2 asks for the tool check. Updating the two lines that describe what the tools stop on is part of that check, not a line beyond the Scope.

### 2. Implementation detail in the new docstrings — Low — holds in part

- Reviewer: codex (finding 1).
- Claim: the `overlaps` docstring narrates its traversal (`tools/modules.py:150-153`). The `prepare.py` module docstring names another file and helper, "(tools/modules.py, overlaps)" (`tools/prepare.py:4-5`).
- Part that holds: the `overlaps` docstring says "each ancestor of one is compared with the other by os.path.samefile". That sentence restates the loop. The contract around it ("compared by file identity, … so that a spelling differing only in case …, or through a symlink, is caught. A path that does not exist overlaps nothing") is the behavior `SETTINGS.md` > Module locations also states. It should stay.
- Part that does not hold: the `prepare.py` reference follows the file's existing practice of naming the tool that defines a value or rule. At 5394847, `tools/prepare.py:24` reads "and the user location (tools/config.py user-modules) as USER_MODULES". The reference names where the rule lives, like that line. The rule codex applies is its own reviewer rule, not one the workspace CLAUDE.md or the issue sets.
- Severity: Low. The fix touches comment text only (coordinator-role.md:71, comment-only fix).

## Checks made in weighing (5394847, TMPDIR under the work folder, sandbox HOME)

- Done when 1, the `modules` default:
  - `HOME=<tmp>/home python3 tools/config.py` prints `modules <tmp>/home/work/autoflow-module (default)` beside `base <tmp>/home/work/coordinator (default)`.
  - `SETTINGS.md` and the `README.md` Settings table both show `~/work/autoflow-module`.
  - README Install step 2 clones beside the coordinator clone, outside the base.
- Done when 2, the stop:
  - With base `<tmp>/home/work/base`, `tools/prepare.py autoflow-module 1` stopped with modules `<base>/autoflow-module`, and `tools/prepare.py x 1` stopped with modules `<tmp>/home/work`, a parent of the base.
  - In both cases the message is "modules <path> and base <path> are the same folder or one lies inside the other …", and nothing was created under the coordination root.
  - `overlaps()` also returns True for a symlinked path and a case-variant path into the base, and False for a sibling directory and for a path that does not exist.
- Done when 3, prepares as before:
  - With modules `<tmp>/home/work/mods`, outside the base, `tools/prepare.py` got past the check and went on to `repo.json`.
  - The code after the check is unchanged in the diff.
- Done when 4, the README row:
  - The autoflow-module row reads "units that each take on one piece of work, defined in the form of the module interface".
  - `docs/module-interface.md:28` of the local autoflow-module clone (469ec2c) reads "Module: a unit that takes on one piece of work. It is defined in the form of this document."
- Scope item 2, `tools/invocation.py`: unchanged, and rightly so. It takes `MODULES` only from `run.env` (`tools/invocation.py:148-150`), which `tools/prepare.py` writes after its check.
- Scope item 3: `VERSION` 0.8.0-rc1 → 0.8.0-rc2.
- Out of scope: the diff touches neither the `<home>/modules` rules nor autoflow-module.

Nothing was posted on the PR. The worktree is as found: clean, HEAD 5394847, and the pre-existing ignored `tools/__pycache__` unchanged (mtime 18:06:42).
