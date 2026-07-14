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

Two independent ways a repo can have **$0 GitHub Actions spend** — check both before declaring the exercise moot:

1. **Self-hosted runners are free.** GitHub bills nothing for `runs-on: [self-hosted, …]` — the cost lives in *your own* infra, not GitHub's meter. A repo whose expensive jobs run self-hosted has no GitHub minutes to save no matter how often it runs. Two traps here:
   - **A custom runner *group* (`runs-on: { group: … }`) is NOT a free signal by itself.** Runner groups can contain GitHub-hosted **larger** runners, which *are* billed (even on public repos). Don't declare $0 off the `group:` syntax — check what the group actually contains, or let the billing `net`/`gross` decide.
   - **Don't confuse self-hosted with *larger GitHub-hosted* runners.** `runs-on: ubuntu-latest-4-core` is billed; `runs-on: self-hosted` is not.

   Where GitHub genuinely bills $0 (true self-hosted), time/queue optimization can still be worth reporting; **dollar** savings are zero.
2. **Public repo on standard GitHub-hosted runners.** Actions is free and unlimited there — but *only* there. So for a public repo confirm both:
   - `runs-on` is a standard label (`ubuntu-latest`, `windows-latest`, `macos-latest`) — not a larger-runner label, **and**
   - its billing `net` is 0 (org path → REFERENCE §1).

Either case true → say it up front: no cost to save (concurrency-cancel and friends are still worth reporting for **queue time**, not money). A public repo on **larger** runners is a **real cost target** — treat it like a private one. When in doubt, let the usage report's `net`/`gross` decide — a repo that bills $0 across a full month has nothing to optimize for money.

**Auditing a whole org? Two traps, in order.** (Recipe → REFERENCE §1.)

1. **Public repos are listed too, usually at $0.** Ranking by raw *minutes* floats free repos to the top and sends you optimizing work that cannot save a cent. Annotate `visibility` and drop them — **but only the ones billed at zero**. Public is free on **standard** runners only; **larger runners are billed on public repos too**, so a public repo with `net > 0` has real spend and stays in scope.
2. **Then rank the private/internal repos by `gross` (list-price cost) — not by `net`, not by raw `minutes`.** `net` is order-dependent: the included minutes are a **shared org pool**, so once it's drained whatever runs next gets billed — `net` fingers whichever repo happened to go last, not the one that ate the pool (observed: the org's **highest bill** was **72 Linux minutes**, while two backup repos burning **1,640 min/month between them** showed a smaller `net`). Raw `minutes` is order-independent but SKU-blind — 200 macOS min outcost 1,000 Linux min. `gross` (pre-discount `pricePerUnit × quantity`) is both SKU-weighted and order-independent, so it names the real culprit; on an all-Linux org it collapses to the minutes ranking. Treat `net > 0` as an **org-level alarm** ("the pool is empty"), never as a per-repo verdict.

## Phase 1 — Measure, don't guess

- Read **every** file in `.github/workflows/`.
- If the `gh` CLI (or the GitHub MCP tools) is available, pull real run data and compute per workflow: average duration, run frequency, and the **superseded rate**. Treat raw `cancelled ÷ total` as an **upper bound**, not measured waste — the `cancelled` conclusion also covers manual cancellations and runs an *existing* concurrency group already killed; confirm a newer run on the **same workflow + ref** before counting a cancel as superseded (a failing run that catches a bug is CI *working*, not waste). Exact commands → [REFERENCE.md](REFERENCE.md) §1.
- **Ask the API for billed minutes.** The usage report gives real charged minutes and dollars per repo/SKU — the only source with multipliers and per-job rounding already applied. Per-run duration comes from the **jobs** endpoint (`started_at→completed_at`); the older run-*timing* endpoint is closing down (billing-platform migration, 2025-04-01) — legacy fallback only. Either way the per-job number is **raw job time**: apply the OS multiplier (**Linux ×1, Windows ×2, macOS ≈×10**) and per-job rounding yourself when estimating from it — and real SKUs (`Actions macOS 3-core` at $0.062/min vs Linux $0.006) and larger runners have their own rates. Watch two zero/low cases the ×1 Linux rate hides: **self-hosted runners are free** ($0), and **`ubuntu-slim` (1-core, $0.002) / arm64 ($0.005)** cost less than the $0.006 catch-all — full SKU table in REFERENCE §Multipliers.
- **No `gh` / no billing access? Say so explicitly.** Never invent minute numbers — label every figure "estimate based on workflow content only" and derive it from trigger frequency × step count, clearly marked as such.

## Phase 2 — Redundancy audit (the highest-value pass)

Look for work a deploy platform already does:

- **Vercel / Netlify / Cloudflare Pages** build every push automatically. A workflow that runs `next build` / `npm run build` for deploy purposes is a double-build → **CRITICAL**. Detect via `vercel.json`, `.vercel/`, `netlify.toml`, `wrangler.toml` — and **verify** the platform is actually connected (config file alone isn't proof; check for platform bot comments on PRs, deployment statuses, or ask the user) before declaring the CI build redundant. A build that exists only to *gate* (typecheck/test) is not redundant — but then it shouldn't upload/deploy artifacts.
- **Cloudways / git-pull deployments**: a workflow doing rsync/scp/ssh deploy that the platform could do via a git webhook → flag as replaceable.
- **Docker registry builds**: CI building an image the platform (or another pipeline) also builds → flag.

**Then ask a different question: is a *robot* pulling the trigger?**

The workflow can be perfectly configured and still be pure waste — because the thing pushing isn't a person. Mirror, backup, sync and archive repos take automated pushes on a fixed cadence, and **every push re-runs the full CI or security scan**, forever, on code nobody is developing.

Cheapest tell: **the inter-run jitter is negligible *relative to the gap*.** A cron fires on a near-constant interval; a human doesn't. Measure the median gap between **distinct pushes** — collapse same-commit runs first (CI + CodeQL fire off one push seconds apart; measuring *those* gaps makes an hourly robot look bursty) — and the mean deviation from it — `rel-jitter < 5%` is a machine. (Do **not** bucket by exact minute-of-hour: a backup that drifts across `:00`/`:01`/`:02` escapes it, and a bursty human repo trips it. And do **not** trust the commit author — an automated backup usually pushes under a *human's* name.) Detection command → REFERENCE §2.

Real case: a 300 MB backup repo received an hourly automated push; each one triggered a full CodeQL scan — **24 scans/day of code no one was writing**, and it was the only repo in the org actually being billed. The fix wasn't caching. It was *stop scanning a backup*.

The remedy is rarely "delete the workflow": it's **disable it on this repo**, or narrow the trigger (`on: push` → a weekly `schedule`), or scan the **source** repo instead of the mirror. ⚠️ If the thing you're switching off is security scanning (CodeQL, dependency review), say so explicitly and confirm the *source* repo is still covered — see Hard limits.

## Phase 3 — Standard optimization checklist

Per workflow, check and report each item (details and canonical diffs → REFERENCE.md §3):

- [ ] `concurrency` group with `cancel-in-progress: true`
- [ ] `paths` / `paths-ignore` filters on triggers (job-level gating if the check is required — REFERENCE §3)
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
- Public repo → free Actions **on standard runners only**; confirm that (not just the visibility) before leading with it (Phase 0). A public repo on larger runners bills like a private one.

## Red flags — stop

| Thought | Reality |
|---|---|
| "I'll add caching to every workflow" | Phase 2 first. The biggest win is a job that stops running. |
| "There's a vercel.json, so the CI build is redundant" | Verify the platform is connected and building. Then it's CRITICAL. |
| "Roughly 400 minutes/month" (no data) | Measured, or labeled "estimate based on workflow content only". Never in between. |
| "This nightly workflow looks unused, deleting" | It might be security scanning. Warn explicitly; the human decides. |
| "I'll just apply the obvious fixes" | Report + diffs only. Edits happen after explicit approval. |
| "Top consumer: 1,735 minutes — start there" | Check `visibility` first. Public **on standard runners** → those minutes are **free**; you'd be optimizing $0. (Public on *larger* runners still bills — check `net`.) |
| "This repo has the biggest bill — that's the problem" | `net` is order-dependent: the included minutes are a **shared pool**, so it fingers whoever ran *after* it drained. Rank **private/internal repos by `gross`** (SKU-weighted list price, order-independent — not raw minutes, which is SKU-blind); treat `net > 0` as an org-level alarm. |
| "This repo runs CI constantly — it must be busy" | Measure jitter **relative to the median gap**. Under ~5%? That's a **cron pushing to a mirror**, not a team. (Don't bucket by minute-of-hour — a cron that drifts across `:00`/`:01` escapes it.) Ask whether it should run at all. |
| "The commits are from a real person, so it's active" | An automated backup pushes under a **human's** name. Cadence is the tell; authorship lies. |
