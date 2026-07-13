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

Then per run, duration comes from the run detail (`gh run view <id> --json ...` is slow at 50 runs; the timing endpoint is cheaper):

```bash
gh api repos/{owner}/{repo}/actions/runs/<id>/timing --jq .run_duration_ms
```

For a 50-run sample it's usually enough to fetch timing for the **top 3–5 workflows by run count**. Compute per workflow:

- **Average duration** (minutes) — from `run_duration_ms`
- **Frequency** — runs in the sample window ÷ window days, ×30 for monthly
- **Superseded %** — `cancelled` ÷ total. This is the only unambiguous waste: a run that a `concurrency` group would have prevented from ever starting. It's the clearest quick win.
- **Failure %** — `failure` / `timed_out` ÷ total. **This is NOT waste by itself.** A run that fails because it caught a bug is CI doing its job; cutting it saves minutes by removing the safety net. Treat a high failure rate as a *signal* (flaky job? broken main? timeout too tight?) and investigate — never as a reason to delete the job.

**Actual billing — use the enhanced-billing usage report.** This is the best data available: real billed minutes **per repo, per SKU, per month, with dollars**. Needs admin/owner scope; say so if it 403s.

```bash
gh api "/organizations/{org}/settings/billing/usage" \
  --jq '[.usageItems[] | select(.product=="actions")]
        | group_by(.repositoryName)
        | map({repo: .[0].repositoryName,
               minutes: ((map(select(.unitType=="Minutes") | .quantity) | add) // 0),
               net:     ((map(.netAmount) | add) // 0)})
        | sort_by(-.minutes)'
```

The `// 0` matters: a repo can have Actions **storage** rows and no Minutes rows, so `add` returns `null` and `sort_by(-.minutes)` dies with `cannot negate: null`. Output is ranked biggest-consumer-first:

```json
[{"minutes":4775,"net":0.618,"repo":"openclaw-workspace"},
 {"minutes":3200,"net":0,"repo":"openclaw"}, ...]
```

`net > 0` means that repo is past the included allowance and actually costing money — start there.

Optional filters: `?year=2026&month=7`. For a personal account: `/users/{user}/settings/billing/usage`.
Cache size (separate): `gh api /repos/{owner}/{repo}/actions/cache/usage`.

> **The old endpoints are GONE.** `GET /orgs/{org}/settings/billing/actions` now returns **HTTP 410 — "This endpoint has been moved"**, and the `/users/{user}/...` twin 404s. Do not use them; they were replaced by the usage report above.

**Prefer the API's own billable numbers over hand-multiplying.** Two sources already give them to you:

- The **timing** endpoint returns a per-OS `billable` block:
  `{"billable":{"UBUNTU":{"total_ms":…},"MACOS":{"total_ms":…}}, "run_duration_ms":…}` — note `run_duration_ms` is **wall clock**, while `billable.*.total_ms` is what you're charged for.
- The **usage report** returns actual billed `quantity` (minutes) and `netAmount` ($) per SKU.

**Multipliers** — keep for intuition when you have no API data, not as the arithmetic of record:

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
| Vercel | `vercel.json`, `.vercel/project.json` | `vercel[bot]` deployment comments/statuses on recent PRs; `gh api repos/{o}/{r}/deployments --jq '.[].creator.login'` |
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
