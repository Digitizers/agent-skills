---
name: gha-optimizer
description: Audit and cut GitHub Actions minutes consumption in a repo — measure real usage first, hunt REDUNDANT work before optimizing existing work (does this job need to run at all? at this frequency? only then tune it), detect double-builds against deploy platforms (Vercel/Netlify/Cloudflare Pages/Cloudways), then propose exact diffs grouped by delete/reduce-frequency/optimize. Use when the user asks to reduce GitHub Actions minutes, audit CI cost/billing, asks why Actions usage is high, mentions "gha-optimizer", or wants a CI-efficiency review of `.github/workflows/`. Read-only by default — never edits workflows without explicit approval.
---

# GitHub Actions Optimizer

**The order is the skill.** For every job, ask in this order — and do not skip ahead:

1. **Does this job need to run at all?** (redundant with a deploy platform, dead, obsolete)
2. **Does it need to run this often / on these triggers?** (push+PR double-runs, unfiltered paths, stale schedules)
3. **Only then — how do we make it faster?** (cache, concurrency, runner choice)

Jumping straight to caching a job that shouldn't exist optimizes waste. A deleted job saves 100%; a cached one saves 30%.

## Phase 0 — Is this even a problem?

Check if the repo is **public**: GitHub Actions on standard runners is free and unlimited for public repos. If so, say that up front — the entire minutes-saving exercise is moot (hygiene findings like concurrency-cancel are still worth reporting for queue-time, not cost).

## Phase 1 — Measure, don't guess

- Read **every** file in `.github/workflows/`.
- If the `gh` CLI (or the GitHub MCP tools) is available, pull real run data and compute per workflow: average duration, run frequency, and the **superseded rate** (`cancelled` — the one unambiguous waste; a failing run that catches a bug is CI *working*, not waste). Exact commands → [REFERENCE.md](REFERENCE.md) §1.
- **Ask the API for billed minutes; don't hand-multiply.** The usage report gives real minutes and dollars per repo/SKU, and the run-timing endpoint returns a per-OS `billable` block. Multipliers (**Linux ×1, Windows ×2, macOS ≈×10**) are for intuition when you have no API data — real SKUs (`Actions macOS 3-core` at $0.062/min vs Linux $0.006) and larger runners have their own rates.
- **No `gh` / no billing access? Say so explicitly.** Never invent minute numbers — label every figure "estimate based on workflow content only" and derive it from trigger frequency × step count, clearly marked as such.

## Phase 2 — Redundancy audit (the highest-value pass)

Look for work a deploy platform already does:

- **Vercel / Netlify / Cloudflare Pages** build every push automatically. A workflow that runs `next build` / `npm run build` for deploy purposes is a double-build → **CRITICAL**. Detect via `vercel.json`, `.vercel/`, `netlify.toml`, `wrangler.toml` — and **verify** the platform is actually connected (config file alone isn't proof; check for platform bot comments on PRs, deployment statuses, or ask the user) before declaring the CI build redundant. A build that exists only to *gate* (typecheck/test) is not redundant — but then it shouldn't upload/deploy artifacts.
- **Cloudways / git-pull deployments**: a workflow doing rsync/scp/ssh deploy that the platform could do via a git webhook → flag as replaceable.
- **Docker registry builds**: CI building an image the platform (or another pipeline) also builds → flag.

## Phase 3 — Standard optimization checklist

Per workflow, check and report each item (details and canonical diffs → REFERENCE.md §3):

- [ ] `concurrency` group with `cancel-in-progress: true`
- [ ] `paths` / `paths-ignore` filters on triggers
- [ ] Double-run on `push` + `pull_request` for the same commit
- [ ] `actions/cache` present and keyed correctly (lockfile-hash key + restore-keys)
- [ ] `runs-on`: Windows/macOS without justification (×2 / ×10)
- [ ] Matrix builds — necessary, or trimmable (e.g. one OS × one Node LTS on PRs, full matrix on main/release)
- [ ] `fetch-depth: 0` on checkout without a reason (slow, rarely needed)
- [ ] Long steps that belong in a separate conditional job
- [ ] `on: schedule` workflows — still relevant? Running on a fork/stale repo?

## Phase 4 — Report, then wait for approval

Output a table:

| Workflow | Est. minutes/month | Finding | Potential saving | Severity |
|---|---|---|---|---|

Group recommendations into three buckets, in this order:

1. **Delete / disable** — fully redundant work
2. **Reduce frequency** — trigger changes (paths filters, drop push-on-branch, thin the schedule)
3. **Optimize** — cache, concurrency, runner downgrade

For every recommendation show the **exact diff**. **Never edit workflow files without explicit approval** — this skill's output is the report and the diffs, not applied changes.

## Hard limits

- **Never delete or disable** security-scanning or dependency-update workflows (CodeQL, Dependabot, `npm audit`, Renovate) without an explicit, separate warning — cost is not the only axis.
- **Never assume the CI build is redundant** until you've verified the platform actually builds this repo (Phase 2). "There's a `vercel.json`" is a lead, not proof.
- **Never fabricate minutes.** No data → say so, and mark estimates as estimates.
- Public repo → free Actions; lead with that (Phase 0).

## Red flags — stop

| Thought | Reality |
|---|---|
| "I'll add caching to every workflow" | Phase 2 first. The biggest win is a job that stops running. |
| "There's a vercel.json, so the CI build is redundant" | Verify the platform is connected and building. Then it's CRITICAL. |
| "Roughly 400 minutes/month" (no data) | Measured, or labeled "estimate based on workflow content only". Never in between. |
| "This nightly workflow looks unused, deleting" | It might be security scanning. Warn explicitly; the human decides. |
| "I'll just apply the obvious fixes" | Report + diffs only. Edits happen after explicit approval. |
