# Codex Review Loop — Reference

Placeholders: `<owner>/<repo>` (e.g. `acme/app`), `<PR>` (number). All commands
use the `gh` CLI. `cwd` can reset between tool calls — `cd` into the repo
explicitly in every git/gh command if you rely on the working directory.

Requires Bash, `gh` and `jq`. Capture all reviewers' evidence before selecting
records to read. Identify the actual Codex bot from the repository's review
integration; a username merely containing `codex` or `chatgpt` is not proof of
who wrote a verdict. Other bots are finding sources too, never clean gates.

---

## 1. Trigger a review

```bash
gh pr comment <PR> -R <owner>/<repo> --body "@codex review"
```

Codex auto-reviews on PR-open reliably; on later pushes, re-trigger explicitly.

---

## 2. Pull findings — all THREE surfaces

Take a complete, read-only snapshot each round. It includes full bodies from
inline/file-level comments, review submissions, and issue comments, with no
commit, line, author, or severity filter. `--paginate --slurp` collects every
page; `jq` flattens the pages only after validating their shape. Failures must
stay failures, never turn into an empty finding list.

Run this Bash block after substituting the repository and PR. It prints the
snapshot path only on success. Keep that path as `SNAPSHOT` for the read
commands below; do not reuse a previous snapshot after a failed poll.

<!-- review-snapshot:start -->
```bash
(
  set -euo pipefail
  REPO='<owner>/<repo>'
  PR='<PR>'
  POLL_DIR=$(mktemp -d)
  trap 'rm -rf "$POLL_DIR"' EXIT
  HEAD_BEFORE=$(gh api "repos/$REPO/pulls/$PR" --jq '.head.sha')
  if [[ ! "$HEAD_BEFORE" =~ ^[0-9a-f]{40}$ ]]; then
    echo 'Missing or invalid PR HEAD; discard this poll.' >&2
    exit 1
  fi
  for SURFACE in inline reviews issues; do
    case "$SURFACE" in
      inline) ENDPOINT="repos/$REPO/pulls/$PR/comments" ;;
      reviews) ENDPOINT="repos/$REPO/pulls/$PR/reviews" ;;
      issues) ENDPOINT="repos/$REPO/issues/$PR/comments" ;;
    esac
    if ! gh api --paginate --slurp "$ENDPOINT" |
      jq -e 'if type == "array" and length > 0 and all(.[]; type == "array")
             then add else error("Expected paginated arrays") end' > "$POLL_DIR/$SURFACE.json"; then
      echo "Could not collect $SURFACE; discard this poll." >&2
      exit 1
    fi
  done
  HEAD_AFTER=$(gh api "repos/$REPO/pulls/$PR" --jq '.head.sha')
  if [[ "$HEAD_BEFORE" != "$HEAD_AFTER" ]]; then
    echo 'HEAD changed during collection; discard this poll and re-fetch.' >&2
    exit 1
  fi
  jq -n --arg head "$HEAD_AFTER" \
    --slurpfile inline "$POLL_DIR/inline.json" \
    --slurpfile reviews "$POLL_DIR/reviews.json" \
    --slurpfile issues "$POLL_DIR/issues.json" \
    '{head: $head, inline: $inline[0], reviews: $reviews[0], issues: $issues[0]}' \
    > "$POLL_DIR/snapshot.json"
  trap - EXIT
  printf '%s\n' "$POLL_DIR/snapshot.json"
)
```
<!-- review-snapshot:end -->

The snapshot is **evidence, not a convergence verdict**. Read all three
surfaces; if a tool display truncates the output, read smaller record batches
from this file rather than shortening bodies or silently skipping records.
For example, after setting `SNAPSHOT` to the successful command's path:

```bash
# Inventory every author, including bots that only post review or issue bodies.
jq '[.inline[], .reviews[], .issues[]] | map(.user.login) | unique' "$SNAPSHOT"
# Full records and full bodies, one surface at a time. Narrow by specific IDs
# for navigation only, after accounting for every finding in the inventory.
jq '.inline[]' "$SNAPSHOT"
jq '.reviews[]' "$SNAPSHOT"
jq '.issues[]' "$SNAPSHOT"
```

**Interpretation rules:**

- `line: null` is not a resolved finding. File-level comments have no line by
  design; outdated line anchors also need verification at HEAD. Use the path,
  full body, `original_commit_id`, and `original_line` to locate the claim.
- An unchanged ID means the same record, not the same contents or a fixed
  defect. Compare IDs **and content/update metadata** on all three surfaces
  across polls. A new ID detects a re-post; an edited body can matter without
  a new ID. Re-read current code before reusing an earlier terminal state.
- A current-HEAD review with the generic suggestions wrapper and no findings
  is pending, even across two empty polls. Wait for actual current-round
  findings to arrive and stabilize (≥90 s apart), or for an explicit clean
  verdict naming HEAD. Stability is a delivery heuristic, never proof of
  resolution or protection against arbitrarily late comments.
- A clean candidate must come from the verified Codex author and current
  review round. Read the complete body and resolve its explicit `Reviewed
  commit` to the full PR HEAD (a unique abbreviated SHA is acceptable; if it
  cannot be resolved, the evidence is incomplete). A timestamp alone, or a
  HEAD substring elsewhere in the body, does not establish the reviewed SHA.
  Do not select an older clean comment over a newer pending/failed review.
- No command here prints `converged`. A matching clean verdict does not excuse
  untriaged findings on another surface. Apply the checklist below, including
  CI, then re-fetch the snapshot and HEAD before handoff. New/edited findings
  need triage; a new HEAD needs fresh review evidence.

Keep the finding ID, verified HEAD, disposition, and evidence/reason together
in your triage record. Only current-HEAD code evidence can establish that a
finding is fixed/stale or refuted; out-of-scope tracking needs a durable link.

---

## 3. Verify a finding against HEAD (stale vs current)

> **⚠️ Ancestry can prove a finding CURRENT. It can never prove one STALE.**
> `original_commit_id == HEAD` ⇒ Codex raised this against the code you have now ⇒ **current**.
> But `original_commit_id` being a *strict ancestor* of HEAD means only that **some** commit landed
> afterwards — not that that commit touched this code, and not that it fixed the bug. An unrelated
> push, or an attempted fix that missed, leaves the defect live while the ancestor test happily
> prints `STALE`. **Auto-skipping there is how you ship the bug you were told about.** Ancestry is a
> *hint about what to read*, never a verdict. Only **reading the code at HEAD** settles whether the defect remains.
> `line == null` can mean an outdated anchor or a file-level finding, not a fix.
>
> **⚠️ And use `original_commit_id`, NOT `commit_id`, for that hint.** GitHub **re-anchors** an
> inline comment onto current HEAD as the branch moves: `commit_id`/`line` are *mutable*;
> `original_commit_id`/`original_line` are *immutable*. Feeding `commit_id` in makes **every**
> finding look like it was raised at HEAD. Observed live: a comment raised at `b1d3d3f` (line 85)
> reported `commit_id=855997d` (HEAD, line 88) one push later.

Read the finding's full record from the successful snapshot in §2. Compare
its `original_commit_id` with the snapshot's full `head` only to choose where
to inspect; a missing anchor is unknown, never an equality match. Then read
the relevant file at that exact PR HEAD and verify the claimed defect:

- Defect still present → current; triage its validity, severity and scope.
- Defect demonstrably fixed → stale/fixed; record the current-HEAD evidence.
- Claimed defect disproved → refuted; record the rationale.

Never fix from the finding text alone (Codex can re-post findings against
commits that already fixed them, or anchor to stale line numbers), and never
dismiss one from ancestry or anchor metadata alone.

**The currency signals, in order of trust:** (1) **the code at HEAD still exhibits the problem** —
the only authority; (2) a **new comment `id`** you have not seen before; (3) `original_commit_id`
== HEAD ⇒ current. An older `original_commit_id` is *not* signal (3) inverted — it is **no
signal**, and sends you to (1). `commit_id` proves nothing at all.

---

## 4. React to a finding (audit trail for the human)

```bash
# 👍 a real (now-fixed) finding, 👎 a verified false-positive
gh api -X POST repos/<owner>/<repo>/pulls/comments/<comment_id>/reactions -f content=+1
gh api -X POST repos/<owner>/<repo>/pulls/comments/<comment_id>/reactions -f content=-1
```

Optionally reply in-thread with a one-line reason (esp. for a re-posted FP:
"verified stale — the gate it cites no longer exists on HEAD (moved to X)").

---

## 5. Wait for CI before declaring a round done

```bash
gh pr checks <PR> -R <owner>/<repo>
```

A round isn't done until CI is green AND Codex is clean on that HEAD — or,
in the one documented exception, every finding it still returns at that HEAD
at P0/P1/P2 is already in a terminal triage state, re-verified this round
(SKILL.md -> Convergence).

---

## A worked round

1. Push commit `abc123` to the PR branch.
2. `@codex review`.
3. Poll (first at ~60–90 s; back off if empty): surface (a) shows `id=555 commit=abc123 [src/x.ts:48] "P1: str_contains is PHP 8-only; CI runs 7.4"`. Surface (b) review on `abc123`.
4. Verify: `abc123 == HEAD` → **current**. Read `src/x.ts:48` — confirmed, and `.github/workflows` runs a 7.4 job → **real** (matrix constraint).
5. Fix with the project's older-runtime-safe idiom, add a regression test, commit `fix(x): 7.4-compat strpos (Codex P1)`. Push.
6. 👍 comment 555. `@codex review`.
7. Poll: surface (c) shows "Didn't find any major issues. Reviewed commit: `def456`" and `def456 == HEAD`, all three surfaces fully read and triaged, CI green on that HEAD, and final re-fetch unchanged → **converged**.
8. Hand to the human review queue; don't self-merge a substantial PR.

## Convergence checklist

- [ ] Codex's **latest round** reviewed PR **HEAD**, proven by the explicit reviewed SHA or review commit; a wrapper with no findings is still pending.
- [ ] All three surfaces fully paginated, full bodies read, API/parse errors absent, and HEAD unchanged through collection and the final re-fetch.
- [ ] Every blocking (P0/P1/P2) finding live at HEAD is in a terminal state — fixed/stale (verified in the code at HEAD), refuted with evidence, or tracked with a durable reference (SKILL.md → Convergence).
- [ ] **Every OTHER reviewer bot's live findings triaged** — they don't gate convergence, but merging over an untriaged one ships it unexamined.
- [ ] CI green on HEAD.
- [ ] Any owner-decision findings escalated to the human, not guessed.
- [ ] **Out-of-scope findings tracked, not built** — each recorded somewhere that outlives this checkout, and its own thread (or, for one raised in Round 0, the PR body) saying where it went.
- [ ] **The stated goal still describes the diff** — if the branch outgrew its own description, scope crept: a human widens it, you don't.
- [ ] → human review (once, at the end of the batch — not per round).

### Tracking an out-of-scope finding

Record the defect where it will outlive this checkout — `gh issue create`, the
project's tracker, or (failing both) a comment on the PR quoting the finding
and handing it to the human — then answer the finding's own thread with that
reference, so the end-of-loop human review sees the decision. Reply in-thread
for an inline comment (`pulls/<PR>/comments/<id>/replies`); a review body or a
top-level issue comment has no reply thread, so answer with `gh pr comment`
quoting the finding. React 👍 where the surface takes reactions — inline
comments and issue comments do, review bodies do not, and there the written
reply is the audit trail. During **Round 0** no PR exists yet: record it
against the branch and name it in the PR body you write next.
