# Codex Review Loop — Reference

Placeholders: `<owner>/<repo>` (e.g. `acme/app`), `<PR>` (number). All commands
use the `gh` CLI. `cwd` can reset between tool calls — `cd` into the repo
explicitly in every git/gh command if you rely on the working directory.

Codex's bot login matches `codex|chatgpt` — the filters below select its
comments regardless of the exact bot handle.

---

## 1. Trigger a review

```bash
gh pr comment <PR> -R <owner>/<repo> --body "@codex review"
```

Codex auto-reviews on PR-open reliably; on later pushes, re-trigger explicitly.

---

## 2. Pull findings — all THREE surfaces

Query all three every round. A PR that looks clean on one can carry an
un-triaged finding on another.

```bash
# (a) Inline review comments — where most findings live. Emit each finding's
#     id + commit_id (the FINDING_COMMIT for the ancestor check) + line.
#     line:null = the anchored code changed → usually already handled/outdated.
#     --paginate is REQUIRED (endpoint pages at 30; late rounds land past page 1).
gh api --paginate repos/<owner>/<repo>/pulls/<PR>/comments \
  --jq '.[] | select(.user.login|test("codex|chatgpt";"i")) |
    "id="+(.id|tostring)+" commit="+(.commit_id[0:8])+" ["+.path+":"+((.line//0)|tostring)+"] "+(.body[0:200])'

# (b) Review bodies — the summary verdict + the commit Codex actually reviewed.
gh api repos/<owner>/<repo>/pulls/<PR>/reviews \
  --jq '.[] | select(.user.login|test("codex|chatgpt";"i")) |
    "commit="+(.commit_id[0:8])+" "+.state+"  "+(.submitted_at//"")'

# (c) Issue/conversation comments — Codex often posts its clean-pass verdict
#     ("Didn't find any major issues") HERE as an issue comment, with no formal
#     review object. Watching only /reviews makes a converged PR look un-reviewed.
#     Use the PAGINATED REST endpoint: `gh pr view --json comments` truncates at
#     the first 100 and its `last` is then not the newest comment.
#     Do the `last` selection INSIDE jq and flatten newlines — a Codex body is
#     multi-line, so `--jq ... | tail -1` tails PHYSICAL LINES, not comments, and
#     silently drops the "Didn't find any major issues" text (it prints the
#     trailing `<details>` block instead), so the verdict can never match.
#     `last // empty` on a findings-only PR (no Codex issue comment yet) empties
#     the stream, so nothing downstream runs and the command PRINTS NOTHING.
#     Silence then reads as "surface (c) is broken" instead of "not clean yet".
#     Default the body to a string so the negative branch always fires.
gh api --paginate repos/<owner>/<repo>/issues/<PR>/comments --jq '.[]' \
  | jq -r -s '[.[] | select(.user.login|test("codex|chatgpt";"i"))]
              | (last.body // "(no Codex issue comment yet)")
              | gsub("\r?\n"; " ") | .[0:200]'
```

> **⚠️ Race: (b) lands before (a).** The review object appears first (state `COMMENTED`, generic
> "here are some automated review suggestions" body); its inline comments in (a) arrive
> seconds-to-minutes later. So `(b) exists at HEAD` + `(a) empty` is **not** a clean pass — it's a
> poll that fired too early. Re-poll ≥90 s and require (a) to be **stable across two polls**, or
> wait for the (c) clean-verdict text. **Never add a `commit_id` filter to (a)** — a live finding
> can carry an unexpected sha and the filter drops it silently. Partition (a) by `line` instead:
> `line != null` = live finding, `line == null` = stale/outdated.

**Live vs stale in one query** (what you actually want each round):

```bash
# LIVE findings only — the ones that still need triage this round.
# --paginate is REQUIRED: this endpoint pages at 30, and in a multi-round review the
# newest blocking finding is often past the first page. Without it you will read a
# converged PR that isn't.
# Emit `id` (the stability key + what you need to react/audit) and
# `original_commit_id` (the IMMUTABLE FINDING_COMMIT for the ancestor check —
# `commit_id` is re-anchored to HEAD and proves nothing).
gh api --paginate repos/<owner>/<repo>/pulls/<PR>/comments \
  --jq '.[] | select(.user.login|test("codex|chatgpt";"i")) | select((.line//null)!=null) |
    "id="+(.id|tostring)+" raised_at="+(.original_commit_id[0:8])+" ["+.path+":"+((.line)|tostring)+"] "+(.body[0:160])'

# ALWAYS pair it with the clean-verdict check — a clean pass emits ONLY an issue
# comment (surface (c)), never a review object or an inline comment. Polling (a)
# alone can never observe convergence; you will wait forever on a green PR.
#
# CRITICAL: the verdict must match the CURRENT HEAD. Codex's clean comment prints
# "Reviewed commit: <sha>". A PR that was clean on A and then received commit B
# still shows A's verdict — pairing "no new inline findings" (Codex hasn't reviewed
# B yet) with A's stale "Didn't find..." text declares B converged. Same
# false-convergence bug, new disguise. Compare the SHA; don't just read the text.
# NOTE: `gh`'s built-in `--jq` takes ONE jq expression and does NOT accept jq CLI
# flags like `--arg`. Passing `--arg` there exits 1 and the check fails SILENTLY —
# you then see "no verdict" forever and never converge. Pipe gh's JSON into the
# real `jq` binary instead, which does support `--arg`.
# ALSO: `gh pr view --json comments` fetches `comments(first: 100)` — TRUNCATED.
# On a busy review-loop PR, `last` is the last item of that first page, not the
# newest comment, so the verdict reads stale or absent forever. Use the PAGINATED
# REST issue-comments endpoint.
# AND: a findings-only PR has NO Codex issue comment at all. `last // empty` on
# that empty array empties the jq stream, the if/else never runs, and the command
# prints NOTHING — so a not-clean PR is indistinguishable from a broken poll. A
# convergence check that can go silent is the same false-convergence bug again.
# Default the body to "" (`last.body // ""`) so the negative branch ALWAYS fires.
HEAD=$(gh api repos/<owner>/<repo>/pulls/<PR> --jq '.head.sha')
gh api --paginate repos/<owner>/<repo>/issues/<PR>/comments --jq '.[]' \
  | jq -r -s --arg H "${HEAD:0:10}" '
      [.[] | select(.user.login|test("codex|chatgpt";"i"))]
      | (last.body // "") | gsub("\r?\n"; " ")
      | if   (test("didn.t find any major issues";"i")) and (test($H))
        then "CLEAN @ HEAD — converged"
        elif (test("didn.t find any major issues";"i"))
        then "STALE VERDICT — clean, but for an older commit. Codex has not reviewed HEAD yet."
        else "NOT CLEAN — findings, or the review is still in flight."
        end'
```

**Compare rounds by the set of comment `id`s, not by path/line/body.** Codex re-posts an
identical-looking finding with a *new* id; a text-only diff makes a fresh blocking finding look
like last round's stable set. Conversely, an **unchanged id** across rounds is *not* a new
finding — even when its `commit_id`/`line` moved (GitHub re-anchored it).

---

## 3. Verify a finding against HEAD (stale vs current)

> **⚠️ Ancestry can prove a finding CURRENT. It can never prove one STALE.**
> `original_commit_id == HEAD` ⇒ Codex raised this against the code you have now ⇒ **current**.
> But `original_commit_id` being a *strict ancestor* of HEAD means only that **some** commit landed
> afterwards — not that that commit touched this code, and not that it fixed the bug. An unrelated
> push, or an attempted fix that missed, leaves the defect live while the ancestor test happily
> prints `STALE`. **Auto-skipping there is how you ship the bug you were told about.** Ancestry is a
> *hint about what to read*, never a verdict. Only two things settle it: `line == null` (GitHub lost
> the anchor entirely) and **reading the code at HEAD**.
>
> **⚠️ And use `original_commit_id`, NOT `commit_id`, for that hint.** GitHub **re-anchors** an
> inline comment onto current HEAD as the branch moves: `commit_id`/`line` are *mutable*;
> `original_commit_id`/`original_line` are *immutable*. Feeding `commit_id` in makes **every**
> finding look like it was raised at HEAD. Observed live: a comment raised at `b1d3d3f` (line 85)
> reported `commit_id=855997d` (HEAD, line 88) one push later.

```bash
HEAD=$(gh api repos/<owner>/<repo>/pulls/<PR> --jq '.head.sha')
# IMMUTABLE anchor — the commit the finding was RAISED AGAINST (not where it now points).
FINDING_COMMIT=$(gh api repos/<owner>/<repo>/pulls/comments/<comment_id> --jq '.original_commit_id')

# A commit is its own ancestor, so the equality case MUST be excluded — else a
# finding raised ON HEAD gets mis-labelled and skipped.
if [ "$FINDING_COMMIT" = "${HEAD:0:${#FINDING_COMMIT}}" ]; then
  echo "RAISED AT HEAD — definitely current. Triage now."
else
  echo "RAISED EARLIER — MAYBE fixed by a later commit, maybe not. READ THE CODE AT HEAD."
  echo "  Does the defect still exist there?  yes -> current finding, fix it."
  echo "                                       no -> stale, do NOT re-fix (re-fixing restarts the loop)."
fi
```

There is no shortcut around that second branch. Never fix from the finding text alone (Codex
re-posts findings against commits that already fixed them, and anchors to stale line numbers) —
and never *dismiss* one from the ancestry alone either.

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

A round isn't done until CI is green AND Codex is clean on that HEAD.

---

## A worked round

1. Push commit `abc123` to the PR branch.
2. `@codex review`.
3. Poll (~4 min): surface (a) shows `id=555 commit=abc123 [src/x.ts:48] "P1: str_contains is PHP 8-only; CI runs 7.4"`. Surface (b) review on `abc123`.
4. Verify: `abc123 == HEAD` → **current**. Read `src/x.ts:48` — confirmed, and `.github/workflows` runs a 7.4 job → **real** (matrix constraint).
5. Fix with the project's older-runtime-safe idiom, add a regression test, commit `fix(x): 7.4-compat strpos (Codex P1)`. Push.
6. 👍 comment 555. `@codex review`.
7. Poll: surface (c) shows "Didn't find any major issues. Reviewed commit: `def456`" and `def456 == HEAD`, no open inline findings → **converged**.
8. Hand to the human review queue; don't self-merge a substantial PR.

## Convergence checklist

- [ ] Codex's **latest** review commit == PR **HEAD**.
- [ ] Zero open inline findings (all `line:null`/re-anchored/verified-FP) at P0/P1/P2.
- [ ] CI green on HEAD.
- [ ] Any owner-decision findings escalated to the human, not guessed.
- [ ] → human review (once, at the end of the batch — not per round).
