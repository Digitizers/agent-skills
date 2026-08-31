---
name: codex-review-loop
description: Drive a pull request to convergence through the Codex AI reviewer — build → local Round 0 pre-review → PR → @codex review → verify each finding against HEAD → fix the real ones with regression tests → re-trigger until clean → human reviews last. Use when a PR is open or just pushed and should be reviewed, when a branch is built and tested and a PR is about to be opened (Round 0 local pre-review), when the user mentions "codex", "@codex review", "the review loop", "ultrareview", or asks to iterate a PR to green.
compatibility: Requires a git repository with a GitHub remote, the `gh` CLI authenticated, and the Codex GitHub reviewer enabled on the repo.
---

# Codex Review Loop

Claude develops, Codex reviews, Claude fixes — **in a loop** — the human reviews **once at the end**. The two reviewers catch different defect classes, and Codex reliably finds real bugs *inside the fixes* for earlier findings. **3+ rounds per change is normal, not a smell** — it's the loop catching the-fix-has-a-bug class, the most expensive class to ship.

## The loop

1. Build on a branch → tests green.
2. **Round 0 — local pre-review of the built branch diff, when the Codex plugin is installed** (see below). If the PR **already exists**, skip this step and continue at step 3 — Round 0 is the pre-PR round only. Otherwise: fix its real findings, re-run the relevant test suite until green again — fixes invalidate step 1's green — then **push the post-Round-0 HEAD** and open the PR. A PR created by a non-pushing flow from the stale remote SHA omits the reviewed fixes.
3. Trigger: `gh pr comment <PR> -R <owner>/<repo> --body "@codex review"`.
4. Pull findings from **all three surfaces** (see REFERENCE) — and from **every reviewer bot on the PR, not just Codex** (Copilot and friends post to the same surfaces; see "Other reviewer bots"). **Verify each against HEAD** — Codex re-posts stale + false-positive findings every round.
5. Fix the **real, in-scope** ones — each with a regression test, its own commit. React 👍 to real findings, 👎 to false positives (so the end-of-loop human review sees they were examined, not missed). A real finding that is *outside this PR's scope* gets a follow-up issue, not a commit — see [Scope boundaries](#scope-boundaries--the-prs-subject-is-the-diff).
6. Re-trigger and repeat 3–5 until Codex says **"Didn't find any major issues"** *against the current HEAD*.
7. **Human reviews once**, at the end. Never auto-merge a substantial PR without a nod.

## Round 0 — local Codex pre-review

If the OpenAI Codex plugin for Claude Code (`openai/codex-plugin-cc`) is installed — check for the `/codex:review` command or `codex:setup` skill — run a **local** review round on the branch diff *before* opening the PR:

```text
/codex:review --wait --base <default-branch> --scope branch
```

(`/codex:adversarial-review` accepts custom focus text and emits schema-validated findings.)

Round 0 runs through the plugin's slash commands only. Do **not** shell out to `codex-companion.mjs` directly from another skill: `${CLAUDE_PLUGIN_ROOT}` resolves to the plugin whose own component is executing — never the Codex plugin — and even with the cache path resolved manually, a raw headless invocation has been observed to hang indefinitely (50+ minutes, no CPU) because the companion expects the slash-command session wiring.

**Round 0 is an accelerator, never a blocker.** If the review has not returned within ~10 minutes, or the plugin's commands are unavailable in the session, kill it and fall back to the no-Round-0 path: push the branch, open the PR (noting the skip in its body), then continue the cloud loop from step 3 — the cloud reviewer remains the convergence gate either way.

Triage its findings exactly like cloud findings: verify against the code, fix the real ones with regression tests, ignore false positives. Re-run the relevant test suite until it is green again — fixes invalidate the pre-Round-0 green. Then push the post-Round-0 HEAD, open the PR, and continue from step 3.

**Why:** the cloud bot's round-trip is minutes per round, and its early rounds are dominated by findings a local pass catches in seconds. The local and cloud reviewers share a model family, so a local pre-pass mostly *de-duplicates* the first cloud rounds rather than adding a new defect class — that is exactly the point: spend the cheap reviewer first.

**What Round 0 is NOT:**

- **Not a convergence gate.** It reviews local state, not the PR, and emits no authoritative clean verdict. Convergence is decided **only** by the cloud bot's clean verdict at HEAD, per the Convergence section — a clean local review never justifies skipping the cloud loop or merging.
- **Not a substitute for the fix rules.** Round-0 fixes follow the same discipline: regression test per code fix, fix-the-rule-not-the-line, own commit.

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

  **Count findings by SOURCE, not just by rule — "different bugs" is not a clean bill of health.** The test above clears you when each round finds something new, and that is the hole: findings can be genuinely distinct and still all trace to one artifact, in which case the artifact is the bug. Observed: seven rounds on a skill doc produced a mismatched sort, a `NULL` concatenation, a `nullglob` hole, and a swallowed exit status — four unrelated bugs by any normal reading, so the "different bugs = working" test said keep going. Six of the seven traced to **one optional shell snippet**, and all six were one class (a stage failing open). Deleting the snippet retired the class in a single commit; six rounds of patching had not. So tally each round's findings against the file, function, or block they came from — when one source keeps producing them, ask what that source is *for* and whether it earns its place, instead of fixing the next instance.
- **Stay inside the project's constraints.** Match its language/runtime version matrix, lint rules, framework, and conventions. A "fix" that breaks the CI matrix (e.g. a newer-language builtin on an older runtime) is itself a new finding — check the CI config before writing the fix.
- **Surface owner decisions; don't guess.** A finding whose fix is a product / design / security / API tradeoff goes to the human, not an autonomous guess. So does any fix that would widen the PR — see [Scope boundaries](#scope-boundaries--the-prs-subject-is-the-diff).
- **Escalate the mechanism by round 3–4, not round 7.** The signal is a *repeat*: a second round patching the same invariant, or a new finding sharing a **failure class** with an earlier one (both fail open, both trust an unchecked input, both re-derive the same unsound proof). Note it the round you see it, and if the next round confirms the pattern, put the decision to the human — by round 3–4, not round 7. **Co-location alone is not the signal**: two unrelated bugs in one file usually just means a small PR, and the "different bugs = the loop working" test above still governs. What escalates is a repeated class or a proof that cannot hold, never a shared line range. When it is real, ask whether the **proof mechanism** is wrong rather than the patch: patching an unsound mechanism converges slowly or never, while replacing it converges in one commit. The redesign itself is the human's call — deleting or restructuring someone's code is the one decision the loop cannot make for itself, and noticing that a mechanism is wrong is not permission to replace it (see [Scope boundaries](#scope-boundaries--the-prs-subject-is-the-diff)). This applies to any change, not only the distributed-state kind below: a doc that ships a paste-able command owns that command's failure modes exactly the way code does, and one fail-open surface per pipeline stage is a mechanism problem, not a series of typos.
- **Fixes get their own commit, naming the round**, e.g. `fix(auth): register category before abilities (Codex round-3 P1)` — keeps the loop auditable.

## Scope boundaries — the PR's subject is the diff

The loop's strength is also its failure mode: a reviewer asked "what is wrong
here?" always answers something, and answering everything turns a three-file
fix into a redesign. **The scope of the PR is fixed when the PR opens.** Every
round after that spends the budget the PR already has; it does not raise it.

**In scope** — a defect the diff *introduces*, or one that makes the PR's own
stated goal untrue. That is the whole list.

**Out of scope by default** — file it, don't build it:

| Finding | What the loop does |
|---|---|
| A pre-existing bug the diff merely sits next to | 👍, open a follow-up issue, reply with its number |
| A refactor / rename / restructure "while we're here" | issue, not this PR |
| A new feature, option, env knob or config surface the change didn't need | issue — new surface is new scope, however small |
| Hardening against a failure mode the change did not create | issue, unless the PR's goal is that hardening |
| A reviewer *preference* with no defect behind it | 👎 with a one-line rationale — a preference is not a finding |
| Docs beyond the behaviour this PR changes | issue |

Filing is a real outcome, not a dodge: `gh issue create` takes a minute, keeps
the finding from being lost, and leaves the PR reviewable. Say so in the reply
so the human sees the finding was *judged*, not dropped.

**Keep each fix inside the blast radius of the change it repairs.** A fix that
touches files the PR never touched, adds an abstraction, or is larger than the
original change is not a fix — it is a second PR wearing a fix's commit
message. Stop and put it to the human.

### The tells, and what to do about them

Check these at the end of every round — they are cheap and they catch drift
while it is still one commit:

- **The PR body no longer describes the diff.** The single most reliable
  signal. Re-read the body you wrote at open; if it now under-sells what the
  branch does, scope crept — either revert the excess or (if it is genuinely
  required) say so explicitly to the human and update the body.
- **`git diff --stat main...HEAD` grows every round.** Fixes shrink or hold
  the diff as often as they grow it. A monotonically growing diff across 3+
  rounds is expansion, not convergence.
- **New files, new dependencies, or new configuration appear after round 1.**
  Almost always scope; the change did not need them at open.
- **You are writing design rationale in a fix commit.** If the commit needs a
  paragraph arguing for a new approach, it is a design decision — human's
  call, per the rule below.

### Redesign is proposed, never performed

The "escalate the mechanism by round 3–4" rule above says when to *notice*
that patching won't converge. It does not authorise the rewrite. When the
mechanism looks wrong: **stop the loop, write at most a paragraph** — what
keeps failing, why the current mechanism cannot hold, what you would replace
it with, and what it costs — and hand it to the human. Then do what they say.
Deleting or restructuring working code, and expanding the change to reach a
better design, are the two decisions the loop is not allowed to make for
itself.

The same boundary applies to the **fix-the-rule-not-the-line sweep**: the
sweep covers every place that teaches *the same claim the finding is about*.
It is not licence for a general cleanup of the files it visits.

### Rounds are for defects, not for polish

Convergence means no actionable finding at a blocking severity (P0/P1/P2) —
not that the reviewer has run out of suggestions. A reviewer will keep
producing nits indefinitely; a PR that only accumulates non-blocking polish
across a round is done, and the remaining suggestions belong in an issue.
**Round 0 obeys every rule in this section too** — a local review before the
PR even exists is where an unbounded "improve it" pass is cheapest to start
and most expensive to notice.

## Reviewer failure modes

The reviewer is not an oracle — three failure modes will mislead the loop if you trust its latest word blindly:

- **Right diagnosis, wrong prescription — verify the FIX, not just the finding.** Codex is much better at spotting that something is broken than at knowing what this codebase should do instead. Its suggested remedy is a hypothesis; treat it exactly like its findings and check it against reality before you type it.

  Observed: it correctly warned that a cadence probe reading *all* runs would misclassify legitimate `schedule` workflows — a real bug — and prescribed filtering to `--event push`. Running that against the actual repos showed the two robot-backups it was meant to catch fire as event **`dynamic`** (default-setup code scanning), not `push`: the prescription would have silently deleted the *only* finding that was costing money. The correct fix was the inverse — *exclude* `schedule`, keep everything a push can trigger.

  So: accept the finding on evidence, then **derive the fix yourself from the code**. A remedy you can't reproduce a reason for is a remedy you haven't verified. Say so in the commit when you deviate — "Codex proposed X; checked against the repo, X drops the real case; did Y instead" — so the human review sees the reasoning, not a silent override.

- **Codex contradicts its own earlier verdict (flip-flop).** It can flag a value one round, and the *next* round flag the fix you just made — sometimes reversing itself outright (e.g. "change 1 → 5", then "change 5 → 1"). **A reversal is not automatically correct.** Re-verify against the code at HEAD, not Codex's newest claim; if the current value is what the code actually enforces, it's a false positive — 👎 with a one-line rationale and hold. Do **not** ping-pong the value to appease successive reviews.
- **Transient errors are not verdicts.** `Codex Review: Something went wrong. Try again later…` (and similar) means the review **didn't run** — it is neither "clean" nor a finding. Re-trigger with `@codex review`; never count it toward convergence, and don't conclude Codex is down after one. Your convergence match must require the actual clean-verdict text, so a transient message can't be mistaken for either outcome.

## Other reviewer bots (Copilot etc.) — sweep them, don't gate on them

A repo often has more than one reviewer bot. **Filter your polls by nothing narrower than "every bot that commented"** — enumerate the distinct `user.login` values on the PR's comment surfaces and triage each bot's live findings. Observed: a poll filtered to `codex|chatgpt` silently ignored **24 live Copilot comments** across a 19-round loop, including one that refuted a convergence argument the fixes relied on; several (a stderr-corrupts-JSON class, a doctrine hole) would have saved whole rounds had they been read when posted.

Division of roles:

- **Codex is the only convergence gate.** Its explicit clean verdict at HEAD ends the loop — nothing else does.
- **Copilot (and similar) are findings sources, never gates.** They emit no clean-verdict signal — silence is indistinguishable from "hasn't reviewed" — so they cannot prove convergence. But every live finding of theirs must be triaged (fix / 👍 / 👎-with-rationale) **before merge**, same as a Codex finding. Add their triage to the convergence checklist, not to the convergence definition.

## Design the evidence model before the code (distributed-state work)

When the change orchestrates **distributed state** — an external registry with no state query, suppressed webhook/event delivery, cancellations, re-runs — the review loop will grind through every hole in an improvised design one round at a time. Observed: a release-pipeline PR spent ~8 of 19 rounds retrofitting what an upfront hour would have specified. Before writing such code, write down:

- **What durable artifact proves each state?** ("a release exists" proved nothing; a marker written only after the irreversible step did.)
- **Which evidence classes may trigger an irreversible action?** Deterministic proof only; a failed command is *not* proof the remote didn't commit (two-generals).
- **What does ambiguity do?** Always preserve, never delete; sticky across retries — a later guard-failure never launders an earlier ambiguous attempt.

The **"escalate the mechanism by round 3–4"** rule above is at its sharpest here, because an improvised evidence model is precisely an unsound proof mechanism: every round retrofits one more hole, and the redesign that ends it is one commit.

## Polling cadence

**Poll the first time ~60–90 s after the trigger, not four minutes later.** Codex often answers in about a minute. A fixed 4-minute wait optimises the wrong variable: it saves a little prompt cache and spends *human* time — the reviewer finishes, the PR sits idle, and the person watching sees the review land before you do and has to prod you. If the first poll is empty, back off (90 s → 2 min → 4 min); don't busy-poll a reviewer that is genuinely still thinking.

Measure, don't assume — but measure the moment the round becomes **readable**, not the moment something first appears. `reviews[].submitted_at` is the wrong clock for both outcomes: on a findings run it timestamps the *wrapper*, which lands before the inline comments (the race above), and on a clean run there is **no review object at all**. Calibrate the delay from your trigger comment to whichever signal actually ends the round:

- clean pass → the **issue comment's** `created_at`;
- findings → the `created_at` of the **last inline comment** in the stable set.

If a push isn't auto-re-reviewed (Codex reviews reliably on PR-open, less so on later pushes), re-trigger with a `@codex review` comment.

**A single poll never decides the round.** Because of the review-object/inline-comment race above, one poll showing "review at HEAD, no inline findings" is indistinguishable from "the findings haven't posted yet." Treat a round as read **only** after the clean-verdict issue comment, or after two consecutive polls (≥90 s apart) return the **same** live-finding set.

See [REFERENCE.md](REFERENCE.md) for the exact gh commands — verify-vs-HEAD, the three finding surfaces, triggering, reacting — and a worked round.
