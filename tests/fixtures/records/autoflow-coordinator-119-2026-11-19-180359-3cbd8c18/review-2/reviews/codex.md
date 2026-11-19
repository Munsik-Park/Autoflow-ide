# Review Summary

PR #120 has one blocking Medium finding: the new overlap check misses a supported workspace symlink layout, so run preparation can proceed even though the modules clone is inside the resolved workspace. The default-path, version, and documentation changes otherwise match issue #119. The verification does not cover this path.

# Findings

1. **Severity: Medium** — `tools/prepare.py:204` (with `tools/modules.py:150-169`).
   - **What breaks:** `prepare.py` can prepare a run when the modules clone is inside `<base>/<workspace>` after that workspace resolves through a symlink. Issue #119 requires it to stop before creating the run and name both paths.
   - **Why:** `base_overlap` compares only the configured modules path with the configured base. `prepare.py` resolves the actual workspace separately at line 207, after the check, and never compares that resolved path with the modules path. If `<base>/ws` links to a directory outside the base that contains the modules clone, the base and clone do not overlap, but the workspace and clone do.
   - **Failure preconditions:** Workspace names are accepted without a symlink restriction at lines 195-196, and line 207 explicitly resolves the workspace with `realpath`. The target can hold a valid `repo.json` and `gh-token` outside any Git worktree; `read_token` checks the workspace directory itself, not nested clones. A local reproduction with `base/ws -> outside` and `outside/autoflow-module` as a Git clone returned `base_overlap(...) == None`, while the resolved workspace contained the clone and `git -C outside rev-parse --show-toplevel` failed (so the Git-worktree guard would not reject it).
   - **Suggested fix:** Compare the resolved workspace with the modules path before `read_repo` and `read_token`, while retaining the base-level check if the broader prohibition is intended. Report the modules and workspace paths in the stop message.

# Inline Comment Candidates

- `tools/prepare.py:204`: This check compares `modules` only with `base`. When `<base>/<workspace>` is a symlink to an outside directory containing the modules clone, it returns no overlap; the resolved workspace at line 207 still contains that clone. Compare against the resolved workspace before continuing.

# Missing Tests

- Add a regression case where `<base>/<workspace>` is a symlink to an outside directory containing the modules clone, and assert that `prepare.py` stops before creating a run with a message naming both paths.

# Low Confidence

None.
