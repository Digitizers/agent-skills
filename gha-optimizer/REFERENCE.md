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
- **Waste %** — `conclusion` in (`cancelled`, `failure`, `timed_out`) ÷ total. Cancelled runs that a `concurrency` group would have prevented are the clearest quick win.

**Actual billing (private repos, needs admin/owner scope — often unavailable, say so if it 404s):**

```bash
gh api /repos/{owner}/{repo}/actions/cache/usage      # cache size
gh api /users/{user}/settings/billing/actions          # or /orgs/{org}/settings/billing/actions
```

**Billing multipliers** (billable minutes = wall minutes × multiplier, rounded up per job):

| Runner | Multiplier |
|---|---|
| `ubuntu-*` | ×1 |
| `windows-*` | ×2 |
| `macos-*` | ×10 |

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
    node: ${{ github.ref == 'refs/heads/main' && fromJSON('[18, 20, 22]') || fromJSON('[22]') }}
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
