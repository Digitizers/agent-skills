# gha-optimizer — REFERENCE

Exact commands, detection heuristics, and canonical diffs. Load when actually running the audit.

## §1 Measuring real usage

**Is the repo public, or on self-hosted runners?** (Actions is free on public + *standard* runners, and always free on self-hosted — GitHub bills $0 either way. Larger GitHub-hosted runners bill even on public repos.)

```bash
gh repo view --json visibility --jq .visibility
```

**Pull recent runs and aggregate:**

```bash
# headBranch + headSha are needed downstream: headSha to collapse same-push runs
# before the cadence/jitter test (§2), headBranch to confirm "same workflow + ref"
# before counting a `cancelled` run as genuinely superseded (see Superseded % below).
gh run list --limit 50 --json name,conclusion,createdAt,databaseId,workflowName,event,headBranch,headSha
```

Then per run, duration comes from the timing endpoint (`gh run view <id> --json ...` is slow at 50 runs) — take the **billable** block, not the wall time:

```bash
gh api repos/{owner}/{repo}/actions/runs/<id>/timing \
  --jq '{billable: ((.billable // {}) | map_values({total_ms, job_ms: [.job_runs[]?.duration_ms]})),
        wall_ms: .run_duration_ms}'
```

For a 50-run sample it's usually enough to fetch timing for the **top 3–5 workflows by run count**. Compute per workflow:

- **Billable-eligible minutes** — from `billable.*.total_ms`, per OS. This is **raw job runtime**: the endpoint applies **neither** OS multipliers **nor** per-job minute rounding, so to estimate the charge compute per job `ceil(minutes) × OS rate` (table below) — round each entry of `job_ms` separately, never the per-OS total once. Never use `run_duration_ms` (wall clock) for cost — with parallel or matrix jobs it undercounts even the raw job time: four parallel 5-minute jobs = ~20 job-minutes, walls 5. Wall time is for queue-latency questions only. (If `billable` comes back empty on your plan, fall back to summing per-job `started_at→completed_at` from `/actions/runs/<id>/jobs` — still per job, not per run.)
- **Frequency** — runs in the sample window ÷ window days, ×30 for monthly
- **Superseded %** — an *upper bound* on the waste a `concurrency` group would remove, not a measured figure. The raw `cancelled` ÷ total **overstates** it: the `cancelled` conclusion also covers manual cancellations and runs an *existing* concurrency group already killed — neither of which a new concurrency rule would prevent. To count a cancellation as genuinely superseded, confirm a **newer run on the same workflow + ref** exists (`gh run list --workflow <wf> --branch <ref>` and compare `createdAt`). What survives that filter is the clearest quick win; the raw ratio is a first-glance signal only.
- **Failure %** — `failure` / `timed_out` ÷ total. **This is NOT waste by itself.** A run that fails because it caught a bug is CI doing its job; cutting it saves minutes by removing the safety net. Treat a high failure rate as a *signal* (flaky job? broken main? timeout too tight?) and investigate — never as a reason to delete the job.

**Actual billing — use the enhanced-billing usage report.** This is the best data available: real billed minutes **per repo, per SKU, per month, with dollars**. Needs admin/owner scope; say so if it 403s.

```bash
OWNER=$(gh repo view --json owner --jq .owner.login)
# %-m (strip leading zero) is a GNU-ism — BSD/macOS `date` errors on it. Use the
# portable base-10 strip so this pastes cleanly on the common macOS environment.
YEAR=$(date +%Y); MONTH=$((10#$(date +%m)))
gh api "/organizations/$OWNER/settings/billing/usage?year=$YEAR&month=$MONTH" \
  --jq '[.usageItems[] | select(.product | ascii_downcase == "actions")]
        | group_by(.repositoryName)
        | map({repo: .[0].repositoryName,
               minutes: ((map(select(.unitType | ascii_downcase == "minutes") | .quantity) | add) // 0),
               # net MUST be scoped to minute rows. The Actions product also bills
               # storage (artifacts/cache, in GigabyteHours) — summing every
               # netAmount would let storage spend masquerade as runner-minute
               # spend, flagging a standard-runner public repo as "paid larger
               # runners" and tripping the over-allowance alarm on storage alone.
               net:     ((map(select(.unitType | ascii_downcase == "minutes") | .netAmount) | add) // 0),
               # gross = list-price cost of the minute rows BEFORE the shared
               # allowance is applied. This is the ranking key: it is SKU-weighted
               # (unlike raw minutes — 200 macOS min at $0.062 outcost 1,000 Linux
               # min at $0.006) AND order-independent (unlike net, which only shows
               # what spilled past the pool and so depends on run order). Fall back
               # to pricePerUnit*quantity if grossAmount is ever absent.
               gross:   ((map(select(.unitType | ascii_downcase == "minutes") | (.grossAmount // (.pricePerUnit * .quantity))) | add) // 0),
               net_storage: ((map(select(.unitType | ascii_downcase != "minutes") | .netAmount) | add) // 0)})
        | map(select(.repo != "" and .minutes > 0))' \
| jq -c '.[]' | while read -r row; do
    repo=$(jq -r .repo <<<"$row")
    # repositoryName has been observed bare ("my-repo"), but the docs show the
    # "owner/repo" form — prefixing blindly would build "owner/owner/repo" and
    # silently degrade every row to UNKNOWN. Normalize instead of assuming.
    case "$repo" in */*) full="$repo" ;; *) full="$OWNER/$repo" ;; esac
    vis=$(gh repo view "$full" --json visibility --jq .visibility 2>/dev/null || echo UNKNOWN)
    jq -c --arg v "$vis" '. + {visibility:$v}' <<<"$row"
  done \
| jq -s '{
    org_is_over_allowance: (map(.net) | add > 0),   # org-level alarm, NOT a per-repo verdict
    # Everything that is NOT public-and-free drains the shared pool. Select by
    # exclusion, never by listing "PRIVATE" — GitHub Enterprise also returns
    # INTERNAL, which is billed like private. Whitelisting PRIVATE would make
    # every INTERNAL repo VANISH from the report, top consumers included.
    cost_targets: (map(select(.visibility != "PUBLIC" and .visibility != "UNKNOWN")) | sort_by(-.gross)),
    # PUBLIC is free ONLY on standard runners. A public repo with net > 0 is
    # paying for larger runners — real spend, must NOT be dropped.
    paid_public:  (map(select(.visibility == "PUBLIC" and .net > 0)) | sort_by(-.net)),
    free_ignore:  (map(select(.visibility == "PUBLIC" and .net == 0)) | map(.repo)),
    unknown:      (map(select(.visibility == "UNKNOWN")) | map(.repo))
  }
  # Every row must land in exactly one bucket — if these disagree, a visibility
  # value you did not anticipate is being silently dropped.
  | . + {rows_accounted: ((.cost_targets|length) + (.paid_public|length) + (.free_ignore|length) + (.unknown|length))}'
```

**Bucket by exclusion, not by whitelist.** `visibility` is not just `PUBLIC`/`PRIVATE` — GitHub Enterprise returns **`INTERNAL`** (org-visible, and billed exactly like private). A `select(.visibility == "PRIVATE")` silently drops every internal repo out of the report entirely, so a genuine top consumer can simply not appear. Anything that isn't public-and-free is a cost target; `rows_accounted` is there to catch it if a new visibility value ever shows up.

Two things the partition is doing:

- **The sort happens *after* visibility, and on `gross` (list-price cost) — not `minutes`, not `net`.** `minutes` ignores SKU rates (200 macOS min outcost 1,000 Linux min); `net` (the other obvious move) ranks the pool's last user first. `gross` is both SKU-weighted and order-independent; see below.
- **"Public = free" is only true on *standard* runners.** Larger runners are billed on public repos too, so a public repo with `net > 0` has genuine spend — it goes to `paid_public`, not to the ignore pile. Dropping every public row would silently hide it.

Match `product`/`unitType` **case-insensitively** (`ascii_downcase`): GitHub's docs show `"Actions"`/`"minutes"` while live responses have been observed returning `"actions"`/`"Minutes"` — an exact-case filter silently returns zero rows on the other casing. The `// 0` matters too: a repo can have Actions **storage** rows and no minutes rows, so `add` returns `null` and the later `sort_by(-.gross)` dies with `cannot negate: null`.

### Rank by money, not minutes — and carry `visibility`

**The usage report bills standard-runner public repos at zero, but it still reports their minutes.** Ranking by `minutes` therefore puts those free repos at the top of your audit and sends you optimizing work that costs nothing — contradicting Phase 0. (A public repo on *larger* runners does bill; that's what `paid_public` is for.) Real output from an org, ranked correctly:

```json
{"minutes":4792,"net":0.72,"repo":"openclaw-workspace","visibility":"PRIVATE"}   <- the only one costing money
{"minutes":214, "net":0,   "repo":"aura",              "visibility":"PRIVATE"}   <- private, still inside the allowance
{"minutes":1735,"net":0,   "repo":"sumit-api",         "visibility":"PUBLIC"}    <- 1,735 min and FREE
{"minutes":1619,"net":0,   "repo":"siteagent",         "visibility":"PUBLIC"}    <- 1,619 min and FREE
```

By minutes, `sumit-api` and `siteagent` look like top consumers. They cost **$0**. Optimizing them saves nothing.

### `net` names the victim, not the culprit

The obvious next move — "rank by `net`, that's the money" — is **also wrong**, and it took real data to see why.

The included minutes are a **shared org pool**. Every private repo draws from it; once it's exhausted, whatever runs *next* gets charged. So `net` is an artifact of **ordering**, not of consumption. Measured in one org:

```text
repo                minutes   gross    net       visibility
Aura                  1,155   $6.93    $0        PRIVATE     <- biggest consumer, billed nothing (inside allowance)
openclaw-workspace      851   $5.11    $0.198    PRIVATE     <- robot backup
hermes-workspace        789   $4.73    $0.162    PRIVATE     <- robot backup
FamilyOS                 72   $0.43    $0.396    PRIVATE     <- HIGHEST net, 72 minutes, plain Linux
```

`FamilyOS` shows the largest **`net`** bill in the org off **72 Linux minutes**. It did nothing wrong — it merely ran after the pool was empty. Rank by `net` and you send the audit at an innocent bystander while the two robot backups (**1,640 min ≈ 53% of all private minutes**) sit below it, untouched. Rank by **`gross`** (the `$` column above; here all-Linux at $0.006/min, so it tracks minutes) and the culprits sort to the top where they belong — and on a mixed-SKU org `gross` would still be right where a raw-minutes sort would mis-rank a macOS-heavy repo.

**The correct model:**

1. **Drop the `PUBLIC` rows billed at zero** (`free_ignore`) — free on standard runners, never a cost finding, only ever hygiene (queue time, cancelled runs). Say so; don't report their minutes as if they mattered. **Do not drop public rows with `net > 0`** — those are larger-runner spend (`paid_public`) and stay in scope.
2. **Rank the `PRIVATE`/`INTERNAL` repos by `gross` (list-price cost), not raw `minutes`.** `gross` weights each minute by its SKU rate, so a mostly-macOS/larger-runner repo doesn't hide behind a Linux-heavy one with more raw minutes. It is computed pre-discount, so — unlike `net` — it reflects true consumption regardless of pool-drain order. (On an all-Linux org `gross` ranking and `minutes` ranking coincide; the difference only appears under mixed SKUs.) This is the cause, and this is where the savings are.
3. **Treat `net > 0` as an org-level alarm, not a per-repo verdict** — it says *"the pool is exhausted, marginal minutes now cost money"*. Which repo it happened to land on is noise.

If the loop can't read a repo's visibility it emits `UNKNOWN` — don't assume; check before you rank it as a cost target.

The `month` filter is load-bearing: **omitting it returns year-to-date**, not the current month — an unfiltered sum presented as "minutes/month" overstates by up to 12× late in the year. Filter explicitly, or label the number YTD. For a personal account (same portable month — `%-m` is GNU-only): `MONTH=$((10#$(date +%m))); gh api "/users/$OWNER/settings/billing/usage?year=$(date +%Y)&month=$MONTH"`.
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
| `ubuntu-*` (2-core x64) | ×1 | `Actions Linux` — $0.006/min |
| `ubuntu-slim` (1-core) | — | `Actions Linux` — **$0.002/min** |
| `ubuntu-*-arm` (arm64) | — | `Actions Linux arm64` — **$0.005/min** |
| `windows-*` | ×2 | `Actions Windows` — $0.010/min |
| `macos-*` | ~×10 | `Actions macOS 3-core` — $0.062/min (≈10.3×) |
| `self-hosted` (true self-hosted machines) | — | **$0 — GitHub bills nothing** (you pay your own infra) |
| `runs-on: { group: … }` | ? | **Depends** — a runner group can hold self-hosted ($0) *or* GitHub-hosted larger runners (billed). Check the group, don't assume $0. |

Rounded up per job. **The `$0.006` catch-all over-prices `ubuntu-slim` (1-core) and arm64 by up to 3×** — don't apply it to every `ubuntu-*` label blindly. Larger/multi-core runners are separate SKUs at their own rates, and self-hosted is free. This is exactly why the **usage report (§1) is authoritative** — it carries the real per-SKU `pricePerUnit`; only fall back to this multiplier table when you have no billing access, and label the result an estimate.

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

### The trigger is a robot

The workflow can be flawless and still be pure waste, because **the pusher isn't a person**. Mirror / backup / sync / archive repos take automated pushes on a fixed cadence, and every push re-runs the full CI or security scan — forever, on code nobody is developing.

**The tell is cadence regularity — measured as jitter relative to the gap.** A cron fires on a near-constant interval; a human doesn't:

```bash
# EXCLUDE `schedule` runs. A scheduled workflow is SUPPOSED to be periodic —
# perfectly regular cadence there is the design, not a smell, and feeding it to
# this test would flag every legitimate nightly as a "robot mirror" to delete.
# Do NOT narrow to `--event push` either: default-setup code scanning fires as
# event `dynamic`, and that is exactly what the real backup repos were burning
# minutes on. Filter out schedule; keep everything a push can trigger.
# COLLAPSE BY COMMIT before measuring cadence. Several workflows fire off the SAME
# push seconds apart (the mirror/backup case is precisely CI + CodeQL together), so
# a raw run stream interleaves sub-minute gaps between workflows with the hour-scale
# gaps between pushes — and the jitter test then reads a perfectly hourly robot as
# "bursty / human". Group by `headSha`, take the earliest run per commit, and measure
# the gaps between DISTINCT pushes.
gh run list -R "$OWNER/$REPO" -L 60 --json createdAt,event,headSha \
| jq -r '
    map(select(.event != "schedule" and .event != "workflow_dispatch"))
    | (group_by(.event) | map("\(.[0].event)=\(length)") | join(" ")) as $events
    | (group_by(.headSha) | map([.[].createdAt] | min) | sort) as $t
    | [ range(1; ($t|length)) | (($t[.]|fromdate) - ($t[.-1]|fromdate)) ] | map(select(. > 0))
    | if length < 5 then "too few distinct pushes" else
        (sort | .[length/2|floor]) as $m
      | ((map(($m - .)|fabs) | add / length) / (if $m>0 then $m else 1 end)) as $rel
      | "events[\($events)]  pushes=\($t|length)  gap=\(($m/60)|round)m  rel-jitter=\((($rel*100)|round))%  -> \(if $rel < 0.05 then "ROBOT (automated push)" else "human / bursty" end)"
      end'
```

Measured across one org:

```text
openclaw-workspace  gap=60m  relative-jitter=0%     -> ROBOT      (hourly backup push)
hermes-workspace    gap=60m  relative-jitter=1%     -> ROBOT      (hourly backup push)
FamilyOS            gap=11m  relative-jitter=23%    -> human / bursty
Aura                gap=5m   relative-jitter=2345%  -> human / bursty
```

**Use *relative* jitter, not an exact minute-of-hour bucket.** The obvious version of this check — group runs by minute-of-hour and look for one bucket — is too brittle: the second backup above jittered across `:00`/`:01`/`:02` and the bucket test nearly missed it, while a bursty human repo (11-minute median gap) would trip an absolute-seconds threshold. A cron's jitter is negligible *relative to its interval*; that ratio is what separates them.

Corroborate cheaply:

```bash
gh api "repos/$OWNER/$REPO" --jq '{pushed_at, size_mb: (.size/1024|floor), open_issues: .open_issues_count, forks: .forks_count}'
```

A repo that is large, pushed minutes ago, and has **no issues, no forks, no PRs** is a backup, not a project.

> **Do NOT use the commit author as the signal.** An automated backup usually pushes under a *human's* name — in the case below every commit read `BenKalsky` while the push was entirely machine-driven. Cadence is the tell; authorship lies.

**Worked case.** A 300 MB private backup repo received an hourly automated push. Each push triggered a full **CodeQL** scan → 24 scans/day of code nobody was writing. Across the whole org it was the *only* repo actually being billed. The fix was not caching, not concurrency: it was **stop scanning a backup**.

Remedies, in order of preference:

1. **Disable the workflow/scan on that repo** (leave the source repo's scanning alone).
2. **Narrow the trigger** — `on: push` → a weekly `schedule`, so the mirror is still checked occasionally.
3. **Scan the source, not the mirror** — the code is identical; pay once.

⚠️ **If what you're switching off is security scanning** (CodeQL, dependency review, secret scanning), that is a Hard-limit item: say it out loud, and confirm the **source** repo is still covered. Turning off the mirror's scan is fine *only* because the original is scanned. If the mirror is the only copy, you have just stopped scanning that code.

## §3 Canonical diffs

> **`main` below is a placeholder — resolve the real default branch first.**
> On a repo whose default is `master` / `trunk` / `develop`, pasting these verbatim restricts `push` CI to a branch that doesn't exist: PR runs keep working, so it looks fine, while **post-merge coverage silently disappears**. The matrix example is worse — `github.ref == 'refs/heads/main'` just never matches, so the "full matrix on the default branch" quietly never runs.
>
> ```bash
> DEFAULT=$(gh repo view --json defaultBranchRef --jq .defaultBranchRef.name)
> ```
>
> Substitute `$DEFAULT` everywhere `main` appears in this section before proposing the diff.

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
+    branches: [main]  # ← substitute $DEFAULT (see the placeholder caveat above); literal "main" breaks master/trunk/develop repos
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
Repo visibility: [self-hosted runners — GitHub bills $0, findings are time/hygiene only
                 | public + standard runners — Actions free, findings are hygiene only
                 | public + larger runners — BILLED, treat as a cost target
                 | private / internal — billed]

| Workflow | Est. min/month | Finding | Potential saving | Severity |
|---|---|---|---|---|

### 1. Delete / disable
### 2. Reduce frequency
### 3. Optimize

<one exact diff block per recommendation>
```

Severity scale: **CRITICAL** (redundant job, double-build), **HIGH** (no concurrency + high cancel rate, macOS unjustified), **MEDIUM** (missing paths filter, missing cache), **LOW** (fetch-depth, minor).
