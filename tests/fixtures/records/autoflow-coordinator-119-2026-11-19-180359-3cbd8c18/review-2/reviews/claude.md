I found no blocking findings in PR #120. There is one Low finding (a docstring that names another file) and no material test gaps. I wrote the review in English, as the review rules require.

# Review Summary

The PR does what issue #119 asks:
- **Default:** the built-in `modules` default moves outside the default base (`tools/config.py:22`, `SETTINGS.md:26`, `README.md:78`).
- **Stop:** `tools/modules.py` and `tools/prepare.py` now stop, naming both paths, when the modules clone and the base are the same folder or either lies inside the other.
- **VERSION and README:** `VERSION` goes to rc2, and the README's autoflow-module row uses the module interface's wording.

**Coverage of the acceptance criteria.** The check compares the modules clone with the base, not with one workspace, so it is stricter than the criteria. Workspaces are created with `mkdir` and found by `find` without following symlinks (`WORKSPACE.md` > Creating / Finding). So `<base>/<ws>` resolves to `realpath(base)/ws`, and both criteria cases are covered:
- `modules` at or under `<base>/<ws>` means the modules clone lies inside the base.
- `<base>/<ws>` inside `modules` means either `modules == <base>/<ws>` or the base lies inside `modules`.

**Function behaviour.**
- `overlaps()` resolves the inner path, walks its ancestors and compares each with the other path using `os.path.samefile`, in both directions. That handles symlinks, and spellings that differ only in case on a case-insensitive volume. This matters because `os.path.realpath` does not fix case.
- A path that does not exist returns `False`. Later checks still stop on a missing clone: `modules.py:194` and `prepare.py:213` via `modules_clone`.

**Placement in `prepare.py`.** The check sits at `tools/prepare.py:203-206`. It runs before `read_repo`, `read_token` and the run-directory `mkdir`, so nothing is created before the stop. The import alias `modules as module_tool` avoids a clash with the local `modules` variable at `prepare.py:213`. Importing `modules.py` has no side effects because `main()` is guarded by `__name__`.

**Shared state.** The diff adds no read-modify-write on shared state. The new check only reads where configured paths sit on disk, nothing serializes it, and nothing is written depending on its result.

**Verification dispositions in the PR body.** These were checked by hand runs; the repository has no test suite. The one reduction is "a run prepares as before when modules lies outside the base". It was shown only up to `read_token`, because the scratch workspace had to sit inside the git worktree. That reason holds: the only new code in `prepare.py` is the import and the check at lines 203-206, and that path is unchanged after the check.

# Findings

1. **Severity: Low**
   - **File and line:** `tools/prepare.py:4-5` (module docstring).
   - **What breaks:** The added docstring text includes a pointer to another file, "(tools/modules.py, overlaps)". That is another file's path, which the comment rule does not allow in a comment. If `overlaps` is renamed or moved, the sentence becomes false while `prepare.py` itself is unchanged.
   - **Why:** Comments may name only things about the code they sit on. A reference to another file's path or contract is excluded. This repository is autoflow-coordinator, not the AutoFlow framework's own source, so the rule applies.
   - **Evidence for the precondition:** The diff adds `folder and neither lies inside the other (tools/modules.py, overlaps), repo.json, ...`.
   - **Fix direction:** Drop the parenthetical. The call at `prepare.py:204` (`module_tool.base_overlap`) already shows where the check lives.

# Inline Comment Candidates

- `tools/prepare.py:5`: "(tools/modules.py, overlaps)" names another file's function in this docstring. It goes stale if that function moves or is renamed, and the call at line 204 already shows the dependency. Suggest removing the parenthetical.

# Missing Tests

None. The repository has no test suite, and the PR body reports manual runs for:
- the same folder;
- `modules` inside the base and inside a workspace;
- the base inside `modules`;
- case-variant spellings of both paths;
- a base that does not exist;
- `modules` outside the base.

Together these cover each branch of `overlaps()` and both call sites.

# Low Confidence

None.
