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

Converged = Codex's latest review is against **current HEAD**, its findings have **fully landed** (see the race below), *and* none is a new actionable finding at any blocking severity (P0/P1/P2). Do **not** declare convergence off a single comment surface — a PR clean on `/reviews` can still carry an un-triaged finding on the inline or issue surface.

**Codex posts its outcome on different surfaces depending on the result — poll BOTH or you will misread the loop:**

| Outcome | Where it lands | API |
|---|---|---|
| **Has findings** | a PR **review** + inline **review-comments** | `pulls/N/reviews` + `pulls/N/comments` |
| **Clean** ("Didn't find any major issues") | a top-level **issue comment** — *no* review object, *no* `commit_id`, *no* inline comment | `issues/N/comments` |

A clean pass emits **only** an issue comment. If your poll watches `pulls/N/reviews` for a HEAD-matching `commit_id`, a clean PR reads as **"never reviewed" forever** — you'll re-trigger endlessly and wrongly conclude Codex is down/rate-capped. **Convergence requires EITHER** (a) an `issues/N/comments` Codex comment matching `/didn.t find any major issues/i` on/after your last push — *the only unambiguous clean signal* — **or** (b) a `reviews` entry at HEAD whose inline findings you have actually **enumerated and triaged**. Never gate convergence on the `/reviews` surface alone.

### ⚠️ The review-object / inline-comment race — this WILL bite you

Codex posts the **review object first** (state `COMMENTED`, body = a generic *"💡 Codex Review — Here are some automated review suggestions"* wrapper) and its **inline review-comments land seconds-to-minutes later**. A poll that fires inside that window sees *a review at HEAD with zero inline comments*, which looks exactly like a clean pass. **It is not.** Merging there ships the findings unfixed — including P0/P1s.

- A review whose body is the **generic suggestions wrapper means findings exist**. Go find them. An empty inline list at that moment is a race, not a verdict.
- **Never conclude "0 findings" from a single poll.** Either wait for the explicit clean-verdict issue comment, or re-poll ≥90 s later and require the live set to be **stable across two consecutive polls** — compared by the set of comment **`id`s**, not by path/line/body. Codex re-posts an identical-looking finding with a **new id**, so a text-only diff hides a fresh blocking finding inside a "stable" set.
- **Always `--paginate`, on BOTH comment surfaces.** `pulls/N/comments` (inline) pages at **30**, and `gh pr view --json comments` silently truncates to `comments(first: 100)` — so its `last` is not the newest comment on a busy PR. Read the verdict from `gh api --paginate repos/<o>/<r>/issues/<PR>/comments` instead. In a multi-round review the newest blocking finding routinely lands past page 1, so an un-paginated fetch reads a converged PR that isn't one — the same false-convergence failure wearing a different disguise.
- **Never filter inline comments by `commit_id`.** Fetch *all* of `pulls/N/comments` and partition by `line`: `line != null` = **live finding**; `line == null` = stale/outdated (already handled in an earlier round). A live finding can carry a sha your filter didn't expect, and the commit filter drops it **silently**.
- **`commit_id` and `line` are re-anchored; `original_commit_id` and `original_line` are not.** GitHub moves an inline comment onto the current HEAD as the branch advances. So `finding.commit_id == HEAD` does **not** mean the finding is fresh — it may be an already-fixed comment that followed you. Use **`original_commit_id`** as the "raised at" anchor (REFERENCE §3), and treat an **unchanged comment `id`** as "not a new finding" even when its line moved.
- **Ancestry proves CURRENT, never STALE.** `original_commit_id == HEAD` ⇒ current, triage it. But an *older* `original_commit_id` only says some commit landed after — not that it touched this code, and not that it fixed the bug. An unrelated push, or a fix that missed, leaves the defect live. **Auto-skipping on ancestry is how you ship the bug Codex handed you.** When the anchor predates HEAD and `line != null`, **read the code at HEAD** — that is the only thing that settles it.
- **A convergence check that can print nothing is broken.** On a findings-only PR there is no Codex issue comment at all, so `[…] | last // empty` empties the jq stream and the whole `if/else` never runs — the poll outputs **silence**, which reads identically to "the query is broken". Default the body (`last.body // ""`) so the NOT-CLEAN branch always fires. Same false-convergence bug, wearing silence instead of a wrong answer.
- **Poll surface (c) in the same breath as (a).** A clean pass emits *only* an issue comment — no review object, no inline comment. If your poll watches inline findings alone, a green PR looks "still in review" forever and you never converge.
- **Select the record inside `jq`; never `tail` raw body text.** Codex bodies are multi-line, so `--jq '…|.body' | tail -1` tails *physical lines*, not comments — it drops the `Didn't find any major issues` text and prints the trailing `<details>` block, so the verdict can never match. Do `last` inside jq and flatten newlines.
- **Make sure your poll command actually runs.** `gh`'s built-in `--jq` accepts one jq expression, **not** jq CLI flags like `--arg` — passing it exits 1 and the check fails *silently*, so you read "no verdict" forever. Pipe `gh`'s JSON into the real `jq` binary. A convergence check that can't fail loudly is worse than none.
- **Never `2>/dev/null` a convergence poll.** Suppressing stderr converts the failure above — and any `gh`/auth/network/`jq` error — into *false silence*: an empty result that reads exactly like "no findings" and merges the bug unfixed. This is the real-world trigger of every false-convergence variant above. Let the poll's errors print and eyeball them; a convergence check must fail **loud**, never quiet. If you must separate streams, capture stderr and assert it's empty — don't discard it.
- **The clean verdict must name the CURRENT HEAD.** Codex's clean comment prints `Reviewed commit: <sha>`. A PR clean on commit `A` that then receives commit `B` still shows `A`'s verdict — and `B` has no inline findings yet *because Codex hasn't reviewed it*. Pairing those two reads as "converged". **Compare the verdict's SHA to HEAD; never trust the text alone.**
- Corollary: **never merge on a premature zero.** If you have not seen either the clean-verdict text or a stable, triaged inline set, the review is still in flight.

## Rules that keep it correct

- **Verify vs HEAD first — by reading the code, not by arithmetic on shas.** A finding raised **on** HEAD (`original_commit_id == HEAD`) is **current** — triage it. A finding raised on a *strict ancestor* of HEAD is **undecided**: a later commit may have fixed it, or may have been unrelated, or may have missed. Open the file at HEAD and look. Still exhibits the defect → **current**, fix it. Genuinely fixed → **stale**, do not re-fix (re-fixing churns the PR and restarts the loop). Present at HEAD but wrong → **false positive** — verify, 👎, leave it. Only a *real, still-live* finding re-enters the fix loop. (queries → REFERENCE.md §3)
- **Tell stale from new by id + line.** `line: null` or a re-anchored (unchanged) comment id = outdated/already-handled. A **new** comment id on the latest commit = a new finding.
- **Every code fix ships a regression test** — encode the failure mode so a later round can't silently re-break it. This is what stops the loop oscillating. *Test where applicable:* doc / copy / config-flag fixes have no unit test — don't invent a meaningless one.
- **Fix the RULE, not the line — then grep to prove it.** When a finding is about a *claim, invariant or convention* (a doc statement, a validation rule, a naming convention, a security caveat), the flagged line is one **instance**, not the bug. The bug is that the rule is taught in N places and you just fixed one. Before committing, grep every place that teaches the same rule and fix them all in the same commit — then re-grep and paste the empty result as your proof.

  This is the most expensive mistake in the loop, and the "no test → the next Codex pass is the check" instinct is exactly what causes it: it outsources the sweep to the reviewer, so you pay **a full round per instance**. Observed: a public-repos-are-free caveat was corrected in the one place Codex flagged, four rounds running — a single grep found **five** stale copies, including a `REFERENCE` line that directly contradicted a bucket added two commits earlier.

  **A high round count on the *same invariant* is the tell.** 3+ rounds finding *different* bugs is the loop working. 3+ rounds re-finding *the same rule* means you are patching pointwise — stop, sweep, and land it in one commit.
- **Stay inside the project's constraints.** Match its language/runtime version matrix, lint rules, framework, and conventions. A "fix" that breaks the CI matrix (e.g. a newer-language builtin on an older runtime) is itself a new finding — check the CI config before writing the fix.
- **Surface owner decisions; don't guess.** A finding whose fix is a product / design / security / API tradeoff goes to the human, not an autonomous guess.
- **Fixes get their own commit, naming the round**, e.g. `fix(auth): register category before abilities (Codex round-3 P1)` — keeps the loop auditable.

## Reviewer failure modes

The reviewer is not an oracle — three failure modes will mislead the loop if you trust its latest word blindly:

- **Right diagnosis, wrong prescription — verify the FIX, not just the finding.** Codex is much better at spotting that something is broken than at knowing what this codebase should do instead. Its suggested remedy is a hypothesis; treat it exactly like its findings and check it against reality before you type it.

  Observed: it correctly warned that a cadence probe reading *all* runs would misclassify legitimate `schedule` workflows — a real bug — and prescribed filtering to `--event push`. Running that against the actual repos showed the two robot-backups it was meant to catch fire as event **`dynamic`** (default-setup code scanning), not `push`: the prescription would have silently deleted the *only* finding that was costing money. The correct fix was the inverse — *exclude* `schedule`, keep everything a push can trigger.

  So: accept the finding on evidence, then **derive the fix yourself from the code**. A remedy you can't reproduce a reason for is a remedy you haven't verified. Say so in the commit when you deviate — "Codex proposed X; checked against the repo, X drops the real case; did Y instead" — so the human review sees the reasoning, not a silent override.

- **Codex contradicts its own earlier verdict (flip-flop).** It can flag a value one round, and the *next* round flag the fix you just made — sometimes reversing itself outright (e.g. "change 1 → 5", then "change 5 → 1"). **A reversal is not automatically correct.** Re-verify against the code at HEAD, not Codex's newest claim; if the current value is what the code actually enforces, it's a false positive — 👎 with a one-line rationale and hold. Do **not** ping-pong the value to appease successive reviews.
- **Transient errors are not verdicts.** `Codex Review: Something went wrong. Try again later…` (and similar) means the review **didn't run** — it is neither "clean" nor a finding. Re-trigger with `@codex review`; never count it toward convergence, and don't conclude Codex is down after one. Your convergence match must require the actual clean-verdict text, so a transient message can't be mistaken for either outcome.

## Polling cadence

**Poll the first time ~60–90 s after the trigger, not four minutes later.** Codex often answers in about a minute. A fixed 4-minute wait optimises the wrong variable: it saves a little prompt cache and spends *human* time — the reviewer finishes, the PR sits idle, and the person watching sees the review land before you do and has to prod you. If the first poll is empty, back off (90 s → 2 min → 4 min); don't busy-poll a reviewer that is genuinely still thinking.

Measure, don't assume — but measure the moment the round becomes **readable**, not the moment something first appears. `reviews[].submitted_at` is the wrong clock for both outcomes: on a findings run it timestamps the *wrapper*, which lands before the inline comments (the race above), and on a clean run there is **no review object at all**. Calibrate the delay from your trigger comment to whichever signal actually ends the round:

- clean pass → the **issue comment's** `created_at`;
- findings → the `created_at` of the **last inline comment** in the stable set.

If a push isn't auto-re-reviewed (Codex reviews reliably on PR-open, less so on later pushes), re-trigger with a `@codex review` comment.

**A single poll never decides the round.** Because of the review-object/inline-comment race above, one poll showing "review at HEAD, no inline findings" is indistinguishable from "the findings haven't posted yet." Treat a round as read **only** after the clean-verdict issue comment, or after two consecutive polls (≥90 s apart) return the **same** live-finding set.

See [REFERENCE.md](REFERENCE.md) for the exact gh commands — verify-vs-HEAD, the three finding surfaces, triggering, reacting — and a worked round.
