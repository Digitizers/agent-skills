---
name: pr-first-workflow
description: Default to a branch + pull request for every change — code, docs, config, content, and marketing copy alike — instead of committing to main. Branch → commit → PR → review → merge → return to main; direct-to-main only when the human explicitly says so. Use when starting any change in a git + GitHub repo, when deciding whether something can skip a PR, when the user mentions branching / PR workflow, and whenever a request implies landing a change — "commit it", "ship it", "push it", "get it into main", "publish it", "update the copy / the page / the README" — however small the edit sounds. Pairs with codex-review-loop, which drives the opened PR to green.
---

# PR-first workflow

**Every change rides a branch + PR by default — including docs.** A PR is the reviewed path and a revert point; committing straight to main skips both. The cost of a PR is ~2 minutes; the cost of an unreviewed bad commit on main is much more.

## The loop

1. **Start from a clean default branch.** Check out the repo's **default branch** — usually `main`, but may be `master` / `trunk` / `develop`. Get the **bare** branch name (not the `origin/…` remote-tracking name, or you'll detach HEAD) with `git remote show origin | sed -n '/HEAD branch/s/.*: //p'` (or `git symbolic-ref refs/remotes/origin/HEAD | sed 's@^refs/remotes/origin/@@'`). Then `git checkout <branch> && git pull` before branching. Never branch off stale or dirty state. (Substitute your repo's default branch wherever this skill says `main`.)
2. **Branch per logical change.** `type/scope-slug` (e.g. `fix/redeem-stacking`, `docs/roadmap-ph-idea`). One logical change per branch — don't bundle unrelated edits.
3. **Commit** with a clear message; **push** the branch.
4. **Open the PR** with a body saying what + why. Hand it to **codex-review-loop** (or your reviewer) to drive to green.
5. **Merge** once reviewed (squash keeps main linear), **delete the branch**.
6. **Return to the default branch:** check it out and `git pull`. Don't leave the local checkout on a merged branch.

## When a change may skip the PR

Only on an **explicit** human "commit direct" for that change. Otherwise:

| Change | Path |
|---|---|
| Any code (app / lib / schema / CI / config) | **always a PR** — no exceptions |
| Docs / README / comments-only | **PR by default**; direct-to-main only if told |
| A personal/solo repo with its own stated convention | follow that repo's convention (some intentionally commit to main) |

"It's just a one-liner" / "it's only docs" is **not** a reason to skip — small changes break things too, and the PR is the audit trail.

## Rules

- **One logical change per PR.** A reviewer (human or AI) can only reason about a focused diff. Mixing a refactor + a fix + a doc tweak hides the real change.
- **Docs that describe code go with the code.** If a doc claims what the code does, change both in the same PR so they can't drift.
- **Keep main releasable.** Never push a half-done change to main "to save a PR" — that's what the branch is for.
- **Don't `git stash` to juggle branches.** A stray stash from other work can resurface and cause conflicts; commit to your branch instead. Check out main cleanly before branching.
- **After merge, sync.** Pull main so the next branch starts current — stale branches cause avoidable conflicts.

## Red flags — stop

| Thought | Reality |
|---|---|
| "I'll just commit this to main quickly" | Branch first. Direct-to-main needs an explicit go-ahead. |
| "It's only docs, no PR needed" | Docs ride a PR by default too — it's the review path and revert point. |
| "I'll put the fix and the refactor in one PR" | Split them. One logical change per PR. |
| "I'm on the merged branch, I'll branch from here" | Check out the default branch + pull first — never branch off stale/merged state. |
| "I'll stash to switch branches" | Commit to your branch instead; stray stashes resurface as conflicts. |
