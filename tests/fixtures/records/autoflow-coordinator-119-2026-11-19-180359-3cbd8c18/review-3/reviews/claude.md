# Review Summary

No blocking findings. The PR meets all four Scope items and the "Done when" criteria of #119:

- `tools/config.py`, `SETTINGS.md` and `README.md` now show the default `~/work/autoflow-module`, which is outside the default base `~/work/coordinator`.
- `tools/modules.py` and `tools/prepare.py` stop on a modules/base overlap. `tools/prepare.py` also stops on a modules/workspace overlap. All of these stops come before anything of the run is created, and each message names both paths.
- `VERSION` rises to rc2.
- The README row uses the module interface's wording. It matches `docs/module-interface.md:28` of autoflow-module: "Module: a unit that takes on one piece of work. It is defined in the form of this document."

What I traced:

- **`overlaps`** (`tools/modules.py:150-164`):
  - It resolves the inner path with `realpath`. It then walks the ancestors and compares each with the outer path by `samefile`, in both directions.
  - It therefore catches the same folder, nesting either way, case variants and symlinked spellings. A nonexistent path returns False, as documented.
  - Every ancestor of an existing resolved path can be stat'ed, so `samefile` does not raise there.
- **Ordering in `tools/prepare.py`** (`tools/prepare.py:204-212`):
  - Both checks run after the settings and `--base-confirmed` check and before `read_repo` and `read_token`.
  - That means the "inside the git worktree" stop in `read_token` (`tools/prepare.py:94-97`) cannot preempt them.
  - No run directory or git configuration is touched before them: `set_git_auth` and `mkdir` come later.
- **Import of `modules`** (`import modules as module_tool`):
  - The import is safe. `tools/modules.py` has no top-level effects beyond constants, and `main()` is guarded by `__main__` (`tools/modules.py:216`).
  - The alias avoids a clash with the local `modules` variable at `tools/prepare.py:216`.
- **`tools/invocation.py` left unchanged:** the stated reason holds. It takes `MODULES` only from `run.env`, which `tools/prepare.py` has already checked.
- **"A run prepares as before" verified only up to `read_token`:** the reason holds. The diff adds only stops before that point, and nothing after it changes.

One non-blocking `Low` finding (a comment) follows.

# Findings

1. **Severity: Low** — the module docstring of `tools/prepare.py:5` names another file's path.
   - **What:** the docstring now reads "…neither lies inside the other (tools/modules.py, overlaps), nor the clone and the workspace as resolved…". A comment does not carry a reference to another file's path or contract. This repository is a target repository, not the AutoFlow framework's own source repository, so the comment rule applies.
   - **Why it breaks:** if `overlaps` is renamed or moved, or `prepare.py` stops using it, the sentence goes stale while the code it sits on is unchanged.
   - **Evidence:** the diff hunk at `tools/prepare.py:4-6`. The rest of the docstring also gained an irregular line wrap ("resolved, repo.json, the workspace's gh-token" / "against the caller's GH_TOKEN…"). That wrap is style only.
   - **Fix direction:** drop "(tools/modules.py, overlaps)". The docstring can state the check itself: the modules clone and the base, and the modules clone and the resolved workspace, are neither the same folder nor nested. Reflow the paragraph.

# Inline Comment Candidates

- `tools/prepare.py:5` — "Drop the cross-file reference `(tools/modules.py, overlaps)`. The docstring should state the check, not where it is implemented (Low, comment rule)."

# Missing Tests

None that affect confidence. The repository has no executable test suite, and the PR states manual scenarios. One stated behavior has no listed scenario: the modules/workspace stop when the workspace is a symlink under the base that resolves to the modules clone or inside it (review-2 R2-1). A static trace confirms it works. `workspace` is `realpath(base/ws)` (`tools/prepare.py:208`), and `overlaps(config['modules'], workspace)` then matches through `samefile`.

# Low Confidence

None.
