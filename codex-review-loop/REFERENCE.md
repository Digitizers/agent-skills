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
gh pr view <PR> -R <owner>/<repo> --json comments \
  --jq '[.comments[]|select(.author.login|test("codex|chatgpt";"i"))]|last|.body[0:160]'
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
# Emit `id` (the stability key + what you need to react/audit) and `commit_id`
# (the FINDING_COMMIT for the ancestor check) — never just path/line/body.
gh api --paginate repos/<owner>/<repo>/pulls/<PR>/comments \
  --jq '.[] | select(.user.login|test("codex|chatgpt";"i")) | select((.line//null)!=null) |
    "id="+(.id|tostring)+" commit="+(.commit_id[0:8])+" ["+.path+":"+((.line)|tostring)+"] "+(.body[0:160])'
```

**Compare rounds by the set of comment `id`s, not by path/line/body.** Codex re-posts an
identical-looking finding with a *new* id; a text-only diff makes a fresh blocking finding look
like last round's stable set.

---

## 3. Verify a finding against HEAD (stale vs current)

```bash
HEAD=$(gh api repos/<owner>/<repo>/pulls/<PR> --jq '.head.sha')
FINDING_COMMIT=<commit from surface (a)/(b)>

# A commit is its own ancestor, so the equality case MUST be excluded — else a
# finding ON HEAD gets mis-labelled stale and skipped.
if [ "$FINDING_COMMIT" != "${HEAD:0:${#FINDING_COMMIT}}" ] \
   && git merge-base --is-ancestor "$FINDING_COMMIT" "$HEAD" 2>/dev/null; then
  echo "STALE — predates HEAD; read the code at HEAD, confirm the fix, do NOT re-fix"
else
  echo "ON HEAD — current finding; triage (real → fix loop; wrong → verify + 👎)"
fi
```

Then **read the actual code at HEAD** to confirm. Never fix from the finding
text alone — Codex sometimes re-posts a finding against a commit that already
fixed it, and sometimes anchors to a stale line number.

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
