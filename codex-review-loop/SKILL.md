---
name: codex-review-loop
description: Drive a pull request to convergence through the Codex AI reviewer — build → PR → @codex review → verify each finding against HEAD → fix the real ones with regression tests → re-trigger until clean → human reviews last. Use when a PR is open or just pushed and should be reviewed, when the user mentions "codex", "@codex review", "the review loop", "ultrareview", or asks to iterate a PR to green. Works in any git + GitHub repo with the gh CLI and the Codex GitHub reviewer enabled.
---

# Codex Review Loop

Claude develops, Codex reviews, Claude fixes — **in a loop** — the human reviews **once at the end**. The two reviewers catch different defect classes, and Codex reliably finds real bugs *inside the fixes* for earlier findings. **3+ rounds per change is normal, not a smell** — it's the loop catching the-fix-has-a-bug class, the most expensive class to ship.

## The loop

1. Build on a branch → tests green → open PR.
2. Trigger: `gh pr comment <PR> -R <owner>/<repo> --body "@codex review"`.
3. Pull findings from **all three surfaces** (see REFERENCE). **Verify each against HEAD** — Codex re-posts stale + false-positive findings every round.
4. Fix the **real** ones — each with a regression test, its own commit. React 👍 to real findings, 👎 to false positives (so the end-of-loop human review sees they were examined, not missed).
5. Re-trigger and repeat 2–4 until Codex says **"Didn't find any major issues"** *against the current HEAD*.
6. **Human reviews once**, at the end. Never auto-merge a substantial PR without a nod.

## Convergence

Converged = Codex's latest review is against **current HEAD** *and* raises no new actionable finding at any blocking severity (P0/P1/P2). Do **not** declare convergence off a single comment surface — a PR clean on `/reviews` can still carry an un-triaged finding on the inline or issue surface.

**Codex posts its outcome on different surfaces depending on the result — poll BOTH or you will misread the loop:**

| Outcome | Where it lands | API |
|---|---|---|
| **Has findings** | a PR **review** + inline **review-comments** | `pulls/N/reviews` + `pulls/N/comments` |
| **Clean** ("Didn't find any major issues") | a top-level **issue comment** — *no* review object, *no* `commit_id`, *no* inline comment | `issues/N/comments` |

A clean pass emits **only** an issue comment. If your poll watches `pulls/N/reviews` for a HEAD-matching `commit_id`, a clean PR reads as **"never reviewed" forever** — you'll re-trigger endlessly and wrongly conclude Codex is down/rate-capped. **Convergence check = an `issues/N/comments` Codex comment matching `/didn.t find any major issues/i` on/after your last push, OR a `reviews` entry at HEAD with no new blocking finding.** Never gate convergence on the `/reviews` surface alone.

## Rules that keep it correct

- **Verify vs HEAD first.** A finding whose commit is a *strict ancestor* of HEAD is **stale** (a later commit already fixed it) — read the code at HEAD, confirm the fix, do **not** re-fix (re-fixing churns the PR and restarts the loop). A finding on HEAD is **current** — triage it. If it's on HEAD but wrong, it's a **false positive** — verify, 👎, leave it. Only a *real finding on HEAD* re-enters the fix loop. (ancestor check + queries → REFERENCE.md)
- **Tell stale from new by id + line.** `line: null` or a re-anchored (unchanged) comment id = outdated/already-handled. A **new** comment id on the latest commit = a new finding.
- **Every fix ships a regression test** — encode the failure mode so a later round can't silently re-break it. This is what stops the loop oscillating.
- **Stay inside the project's constraints.** Match its language/runtime version matrix, lint rules, framework, and conventions. A "fix" that breaks the CI matrix (e.g. a newer-language builtin on an older runtime) is itself a new finding — check the CI config before writing the fix.
- **Surface owner decisions; don't guess.** A finding whose fix is a product / design / security / API tradeoff goes to the human, not an autonomous guess.
- **Fixes get their own commit, naming the round**, e.g. `fix(auth): register category before abilities (Codex round-3 P1)` — keeps the loop auditable.

## Polling cadence

Codex takes a few minutes per review. Poll **~every 4 minutes (240–270s)** to stay inside the prompt-cache window — don't busy-poll. If a push isn't auto-re-reviewed (Codex reviews reliably on PR-open, less so on later pushes), re-trigger with a `@codex review` comment.

See [REFERENCE.md](REFERENCE.md) for the exact gh commands — verify-vs-HEAD, the three finding surfaces, triggering, reacting — and a worked round.
