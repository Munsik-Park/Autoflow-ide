# Review findings — Munsik-Park/autoflow-sandbox#8 (issue #4)

- **Pull request**: `Munsik-Park/autoflow-sandbox#8`, `issue-4-2026-11-05-220513-295dbab3` → `main`, draft, closes #4.
- **Head the reviews saw**: `c91b443f68d10a68a66ecd24ecb3d670a99e3e7d`. Both records name this head, and `gh pr view 8 --json headRefOid` gives the same value after both runs.
- **Reviewers**: `claude` and `codex`, from `check-review-backend.sh` → `reviewers: claude codex` (`source: user ~/.autoflow/config.json`, `user-config: ok`).
  - `claude` (model=opus, effort=inherit) → `reviews/claude.md`. Wrapper exit 0 (`local/claude-wrapper.log`).
  - `codex` (model=gpt-6-sol, effort=inherit) → `reviews/codex.md`.
    - The first run failed with `ERROR: Selected model is at capacity` and wrote no record (`[review] codex completed for PR #8 (exit=1)`, `local/codex-wrapper.log`).
    - One retry completed with exit 0 (`local/codex-wrapper-retry-1.log`).
- **Aggregation**: done by the session itself. Two records and one finding did not justify a spawn.
- **Highest severity among findings that hold: Medium.**

## Instructions applied

The invocation's `instructions` were applied in full. They reached both reviewers through `--invocation`, and the codex prompt in `local/codex-wrapper.log` shows them between the begin and end markers. They read:

> Review the pull request to its goal. The report must give each finding's severity and name the head commit the reviews saw.

This report gives each finding's severity and names the head above. The instructions dispose of no finding.

## Findings

### F1 — Medium — holds — terminal control sequences from the input file reach stdout

- **Raised by**: `codex` (Medium). `claude` raised no finding.
- **Where**: `textstats/__main__.py:17-18` at `c91b443`.
  - `top_words` keeps every non-whitespace character that is not one of the ten strip characters (`textstats/__init__.py:25`). ESC, BEL and the rest of a control sequence therefore survive.
  - The CLI prints each word unescaped.
- **Reproduced**: `audit-1/local/cli-output-probe-c91b443.log`, case 1, at HEAD `c91b443`, exit 0. stdout carries the clear-screen sequence and the window-title sequence raw: `top words: \x1b[2j\x1b]0;pwned\x07 (2), plain (1)`. codex's own witness `\x1b[?25l` (hide cursor) is the same class.
- **Why it holds**: the exposure is new with this change.
  - At base `0702976`, `textstats/__main__.py:14-16` print only fixed labels and numbers. No byte of the input file reached stdout.
  - The earlier deferral rested on "the same exposure as `cat` on the file" (`deferred.json`, plan-1 recommendation 6). That does not answer the finding: a statistics command did not echo the file's content before this change.
  - No source the finding cites says otherwise. No rule it relies on is misread.
- **Severity**: Medium, as raised.
  - Impact: a crafted UTF-8 file can drive the terminal of anyone who runs `python -m textstats FILE`. That includes clearing the screen, setting the window title, hiding the cursor and moving it to overwrite or spoof the statistics lines above.
  - Precondition: the file is untrusted and stdout is an interactive terminal.
  - No grounds support a lower level. The earlier Low ratings below carry no evidence beyond the `cat` comparison answered above.
- **Repeated complaint**: this is the same property and the same witness class as earlier records. Each one rated it Low.
  - plan-1 recommendation 6 (`plan-1/evaluation.json`).
  - audit-1's Low case (`audit-1/local/cli-output-probe-c91b443.log`, case 1).
  - quality-1's Security recommendation (`quality-1/evaluation.json`).
  - `deferred.json` deferred it as a known limit. The PR body lists it under *Known differences and limits*.
  - No ledger entry decides it: `issue-4-ledger.md` does not exist. So this review does not re-litigate a settled decision; it disagrees with an unrecorded deferral.
  - The module that acts on this report decides between fixing it and keeping the deferral. Because the reviewer and the deferral disagree, that is a point to put to the advisor (`role-contracts.md` > Advisor).
- **Fix direction (codex)**: escape non-printing and control characters where the CLI formats words (`textstats/__main__.py:17`). `top_words`'s return values stay as the API defines them, so the criteria's printable literals are unaffected.
- **Missing test (codex, part of F1)**: a CLI subprocess test with a frequent token carrying `\x1b[?25l`, asserting that stdout carries a visible escaped form and not the raw ESC byte.

## Reviewer records with no finding

- `claude`: `Findings: None`, `Missing Tests: None`, `Low Confidence: None`.
  - It checked every rule and *Done when* item, Python 3.9, and each verification disposition, against `c91b443` and the build records.
  - It notes the word-definition leading-edge test gap already listed in the PR body (deferred, Low) and raises no finding on it.

## Disposition summary

| # | Finding | Reviewers | Severity | Holds |
|---|---|---|---|---|
| F1 | Control sequences from the input file printed raw by the `top words` line | codex | Medium | yes |

No finding was rejected or disposed of by the instructions.
