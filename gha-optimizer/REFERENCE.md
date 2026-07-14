# gha-optimizer — REFERENCE

Exact commands, detection heuristics, and canonical diffs. Load when actually running the audit.

## §1 Measuring real usage

**Is the repo public?** (free Actions on standard runners if so)

```bash
gh repo view --json visibility --jq .visibility
```

**Pull recent runs and aggregate:**

```bash
gh run list --limit 50 --json name,conclusion,createdAt,databaseId,workflowName,event
```

Then per run, duration comes from the timing endpoint (`gh run view <id> --json ...` is slow at 50 runs) — take the **billable** block, not the wall time:

```bash
gh api repos/{owner}/{repo}/actions/runs/<id>/timing \
  --jq '{billable_ms: ((.billable // {}) | map_values(.total_ms)), wall_ms: .run_duration_ms}'
```

For a 50-run sample it's usually enough to fetch timing for the **top 3–5 workflows by run count**. Compute per workflow:

- **Billable-eligible minutes** — from `billable.*.total_ms`, per OS. This is **raw job runtime**: the endpoint applies **neither** OS multipliers **nor** per-job minute rounding, so to estimate the charge compute per job `ceil(minutes) × OS rate` (table below; per-job detail in `billable.*.job_runs[].duration_ms`). Never use `run_duration_ms` (wall clock) for cost — with parallel or matrix jobs it undercounts even the raw job time: four parallel 5-minute jobs = ~20 job-minutes, walls 5. Wall time is for queue-latency questions only. (If `billable` comes back empty on your plan, fall back to summing per-job `started_at→completed_at` from `/actions/runs/<id>/jobs` — still per job, not per run.)
- **Frequency** — runs in the sample window ÷ window days, ×30 for monthly
- **Superseded %** — `cancelled` ÷ total. This is the only unambiguous waste: a run that a `concurrency` group would have prevented from ever starting. It's the clearest quick win.
- **Failure %** — `failure` / `timed_out` ÷ total. **This is NOT waste by itself.** A run that fails because it caught a bug is CI doing its job; cutting it saves minutes by removing the safety net. Treat a high failure rate as a *signal* (flaky job? broken main? timeout too tight?) and investigate — never as a reason to delete the job.

**Actual billing — use the enhanced-billing usage report.** This is the best data available: real billed minutes **per repo, per SKU, per month, with dollars**. Needs admin/owner scope; say so if it 403s.

```bash
OWNER=$(gh repo view --json owner --jq .owner.login)
gh api "/organizations/$OWNER/settings/billing/usage?year=$(date +%Y)&month=$(date +%-m)" \
  --jq '[.usageItems[] | select(.product | ascii_downcase == "actions")]
        | group_by(.repositoryName)
        | map({repo: .[0].repositoryName,
               minutes: ((map(select(.unitType | ascii_downcase == "minutes") | .quantity) | add) // 0),
               net:     ((map(.netAmount) | add) // 0)})
        | map(select(.repo != ""))
        | sort_by(-.net)' \
| jq -c '.[]' | while read -r row; do
    repo=$(jq -r .repo <<<"$row")
    vis=$(gh repo view "$OWNER/$repo" --json visibility --jq .visibility 2>/dev/null || echo UNKNOWN)
    jq -c --arg v "$vis" '. + {visibility:$v}' <<<"$row"
  done
```

Match `product`/`unitType` **case-insensitively** (`ascii_downcase`): GitHub's docs show `"Actions"`/`"minutes"` while live responses have been observed returning `"actions"`/`"Minutes"` — an exact-case filter silently returns zero rows on the other casing. The `// 0` matters too: a repo can have Actions **storage** rows and no minutes rows, so `add` returns `null` and `sort_by(-.net)` dies with `cannot negate: null`.

### Rank by money, not minutes — and carry `visibility`

**The usage report bills public repos at zero, but it still reports their minutes.** Ranking by `minutes` therefore puts free repos at the top of your audit and sends you optimizing work that costs nothing — contradicting Phase 0. Real output from an org, ranked correctly:

```json
{"minutes":4792,"net":0.72,"repo":"openclaw-workspace","visibility":"PRIVATE"}   <- the only one costing money
{"minutes":214, "net":0,   "repo":"aura",              "visibility":"PRIVATE"}   <- private, still inside the allowance
{"minutes":1735,"net":0,   "repo":"sumit-api",         "visibility":"PUBLIC"}    <- 1,735 min and FREE
{"minutes":1619,"net":0,   "repo":"siteagent",         "visibility":"PUBLIC"}    <- 1,619 min and FREE
```

By minutes, `sumit-api` and `siteagent` look like top consumers. They cost **$0**. Optimizing them saves nothing.

So the triage order is:

1. **`visibility: PRIVATE` and `net > 0`** — past the included allowance, actually billing. This is the entire cost problem; start and usually finish here.
2. **`PRIVATE` and `net == 0`** — inside the allowance. Worth watching (it's what tips into #1), not worth cutting yet.
3. **`PUBLIC`** — free on standard runners. Only ever a *hygiene* finding (queue time, cancelled runs), never a cost one. Say so explicitly instead of reporting its minutes as if they mattered.

If the loop can't read a repo's visibility it emits `UNKNOWN` — don't assume; check before you rank it as a cost target.

The `month` filter is load-bearing: **omitting it returns year-to-date**, not the current month — an unfiltered sum presented as "minutes/month" overstates by up to 12× late in the year. Filter explicitly, or label the number YTD. For a personal account: `gh api "/users/$OWNER/settings/billing/usage?year=$(date +%Y)&month=$(date +%-m)"`.
Cache size (separate): `gh api /repos/{owner}/{repo}/actions/cache/usage`.

Placeholder gotcha: `gh api` auto-expands only `{owner}`, `{repo}`, and `{branch}` (from the current repo). Anything else — `{org}`, `{user}` — is sent **literally** and the request fails; substitute those with a real value or a shell variable like `$OWNER` above.

> **The old endpoints are GONE.** `GET /orgs/{org}/settings/billing/actions` now returns **HTTP 410 — "This endpoint has been moved"**, and the `/users/{user}/...` twin 404s. Do not use them; they were replaced by the usage report above.

**Where the truth lives — two sources, only one is charged money:**

- The **usage report** (above) is the only source of **actually-charged** numbers: billed `quantity` (minutes, with multipliers and per-job rounding already applied) and `netAmount` ($) per SKU. Prefer it whenever you have access.
- The **timing** endpoint returns a per-OS `billable` block:
  `{"billable":{"UBUNTU":{"total_ms":…},"MACOS":{"total_ms":…}}, "run_duration_ms":…}` — `run_duration_ms` is **wall clock**; `billable.*.total_ms` is **raw billable-eligible job time**, with **no OS multiplier and no per-job rounding applied**. Estimating cost from it means doing that arithmetic yourself, per job.

**Multipliers / rates** — the arithmetic you must apply to timing data (the usage report never needs them — it's already charged):

| Runner | Multiplier | Real SKU / rate (list price) |
|---|---|---|
| `ubuntu-*` | ×1 | `Actions Linux` — $0.006/min |
| `windows-*` | ×2 | `Actions Windows` — $0.010/min |
| `macos-*` | ~×10 | `Actions macOS 3-core` — $0.062/min (≈10.3×) |

Rounded up per job. Larger/multi-core runners are separate SKUs at their own rates — another reason to read the usage report rather than assume a multiplier.

No `gh` and no MCP GitHub tools? State it, and estimate only from workflow content: (triggers × typical push volume the user confirms) × (step count as a duration proxy). Label every such number **"estimate based on workflow content only"**.

## §2 Redundancy detection

| Platform | Config evidence | Stronger proof it actually builds |
|---|---|---|
| Vercel | `vercel.json`, `.vercel/project.json` | `vercel[bot]` deployment comments/statuses on recent PRs; `gh api repos/{owner}/{repo}/deployments --jq '.[].creator.login'` |
| Netlify | `netlify.toml` | `netlify[bot]` checks on PRs |
| Cloudflare Pages | `wrangler.toml` (pages config), CF dash | `cloudflare-pages[bot]` on PRs |
| Cloudways / git-pull hosts | workflow steps using `rsync`/`scp`/`ssh`/`appleboy/ssh-action` | The platform side supports "git deployment via webhook" — the workflow can shrink to nothing or a webhook `curl` |
| Docker double-build | workflow runs `docker build`/`docker/build-push-action` | Registry (GHCR/Docker Hub) shows tags also pushed by another pipeline, or the deploy platform builds from Dockerfile itself |

Judgment calls:

- CI `build` that **gates** (fails PRs on type errors) is legitimate even when Vercel also builds — but prefer `tsc --noEmit` + lint + test over a full `next build`, and never artifact-upload/deploy from it.
- Vercel already fails the deployment on build errors and shows it on the PR — for many solo/small projects that IS the gate, and the CI build job can go entirely.

## §3 Canonical diffs

**Concurrency cancel (near-always safe; per-branch group):**

```diff
 on:
   pull_request:
+
+concurrency:
+  group: ${{ github.workflow }}-${{ github.ref }}
+  cancel-in-progress: true
```

(For deploy workflows on main, prefer `cancel-in-progress: false` or a queue — cancelling a half-done deploy is worse than paying for it.)

**Paths filter:**

```diff
 on:
   push:
     branches: [main]
+    paths:
+      - "src/**"
+      - "package.json"
+      - "package-lock.json"
+      - ".github/workflows/ci.yml"
```

Or `paths-ignore: ["**.md", "docs/**"]` when the exclude list is shorter. Include the workflow file itself in `paths` so workflow edits still get CI.

> **Required-check trap.** If this workflow feeds a **required status check** (branch protection / rulesets), workflow-level `paths` can make non-matching PRs **unmergeable**: a workflow skipped by path filtering leaves its required check `Pending` forever, while a job skipped by an `if:` condition reports `Success`. Before recommending this diff, check the required checks (`gh api "repos/{owner}/{repo}/branches/$(gh repo view --json defaultBranchRef --jq .defaultBranchRef.name)/protection" --jq .required_status_checks.contexts`, or rulesets). For required workflows, gate at the **job level** instead — a cheap change-detection job (e.g. `dorny/paths-filter`) + `if:` on the expensive jobs — so the check always completes.

**push + pull_request double-run — keep PR runs, restrict push to main:**

```diff
 on:
-  push:
   pull_request:
+  push:
+    branches: [main]
```

**Cache with lockfile key (setup-node has it built in — prefer this over raw actions/cache):**

```diff
       - uses: actions/setup-node@v4
         with:
           node-version: 22
+          cache: "npm"
```

Raw form when needed (non-node, or caching build output):

```yaml
- uses: actions/cache@v4
  with:
    path: ~/.npm
    key: ${{ runner.os }}-node-${{ hashFiles('**/package-lock.json') }}
    restore-keys: ${{ runner.os }}-node-
```

Key must hash the **lockfile**, not `package.json`; `restore-keys` gives partial hits. A `key: v1-cache` constant is a stale-forever cache — flag it.

**Shallow checkout:**

```diff
       - uses: actions/checkout@v4
-        with:
-          fetch-depth: 0
```

`fetch-depth: 0` is only justified for tools that need history (changelog generation, `git describe`, sonar blame). Ask what consumes it before cutting.

**Runner downgrade:** `windows-latest`/`macos-latest` is justified only for OS-specific builds/tests (native modules, .app/.exe packaging, Safari). A generic Node/TS test suite on macOS is a ×10 bill for nothing.

**Matrix trim — full matrix only on main:**

```yaml
strategy:
  matrix:
    node: ${{ github.ref == 'refs/heads/main' && fromJSON('[20, 22, 24]') || fromJSON('[22]') }}
```

(Or two workflows: slim PR CI, full main/release CI.)

**Schedule thinning:** `cron: "0 * * * *"` → does hourly matter? Daily is a 24× cut. Also: scheduled workflows keep running on abandoned repos forever — check last meaningful commit vs. schedule.

## §4 Report format

```markdown
## GitHub Actions audit — <repo>

Data source: [gh run list, N runs over D days | workflow content only — no run data available]
Repo visibility: [public — Actions free, findings are hygiene only | private]

| Workflow | Est. min/month | Finding | Potential saving | Severity |
|---|---|---|---|---|

### 1. Delete / disable
### 2. Reduce frequency
### 3. Optimize

<one exact diff block per recommendation>
```

Severity scale: **CRITICAL** (redundant job, double-build), **HIGH** (no concurrency + high cancel rate, macOS unjustified), **MEDIUM** (missing paths filter, missing cache), **LOW** (fetch-depth, minor).
