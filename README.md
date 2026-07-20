# agent-skills

Portable, project-agnostic skills for **Claude Code** and **OpenClaw** — small,
reusable developer-workflow tools that work in any repo. This repo is both a
**Claude Code plugin** (all skills under [`skills/`](skills/)) and a **plugin
marketplace** (`digitizer-skills`) that also serves the private
[`digitizer`](https://github.com/Digitizers/digitizer-os) skill — so every
device installs from one place and stays up to date from git.

Each skill is a self-contained folder with a `SKILL.md` (the agent reads its
frontmatter `description` to decide when to load it) and optional
`REFERENCE.md` / `EXAMPLES.md` / `scripts/`.

## Skills

| Skill | What it does |
|---|---|
| [`codex-review-loop`](skills/codex-review-loop/) | Drive a PR to convergence through the Codex AI reviewer — build → PR → `@codex review` → verify each finding against HEAD → fix the real ones with regression tests → re-trigger until clean → human reviews last. |
| [`fable-mode`](skills/fable-mode/) | Strict judgment/planning/verification/inference discipline — verified-vs-inferred-vs-assumed labeling, rival hypotheses before diagnosis, premise checks, confirm-before-irreversible (a criterion is a filter, not an authorization), and proportionally short answers on trivia. Triggers on "fable-mode" / "מצב פייבל" and proactively on high-stakes tasks. |
| [`freepik`](skills/freepik/) | Generate and upscale AI imagery via the Freepik / Magnific REST API without an MCP server. Reads `FREEPIK_API_KEY` from `~/.claude/freepik.env`, so it works in any session. |
| [`gha-optimizer`](skills/gha-optimizer/) | Audit and cut GitHub Actions minutes — measure real usage, hunt redundant work before optimizing (does the job need to run at all? at this frequency? only then cache/tune), detect double-builds vs. deploy platforms (Vercel/Netlify/Cloudways), output exact diffs grouped by delete/reduce-frequency/optimize. Read-only until explicitly approved. |
| [`pr-first-workflow`](skills/pr-first-workflow/) | Default to a branch + PR for every change (code and docs); branch → commit → PR → review → merge → return to the default branch; direct-to-main only on an explicit OK. Pairs with `codex-review-loop`. |
| [`safe-prod-db-write`](skills/safe-prod-db-write/) | Run one-off writes/backfills against a production DB safely — pull the connection into an `mktemp` file, dry-run, get explicit human authorization, execute, verify counts/invariants, clean up. |

The marketplace also serves one plugin from outside this repo:

| Plugin | Source | What it does |
|---|---|---|
| `digitizer` | [`Digitizers/digitizer-os`](https://github.com/Digitizers/digitizer-os) (private) | Digitizer Internal Strategic Brain — deals, pricing, services, market analysis, growth strategy. |

## Install — Claude Code (recommended)

One-time per machine, inside any Claude Code session (needs Claude Code
v2.1.142+ for the `digitizer` root-level skill):

```
/plugin marketplace add Digitizers/agent-skills
/plugin install agent-skills@digitizer-skills
/plugin install digitizer@digitizer-skills
```

Then open `/plugin`, find the `digitizer-skills` marketplace, and **enable
auto-update** (third-party marketplaces have it off by default). From then on
Claude Code refreshes the marketplace in the background and pulls new commits —
edit a skill here, and every machine picks it up. Manual refresh:
`/plugin marketplace update digitizer-skills`.

The CLI and the IDE extensions (VS Code / JetBrains) share the same `~/.claude`
user scope, so one install covers both. These repos are private, so the machine
needs git auth for GitHub (it already does if you can clone them).

### Claude Code on the web / mobile (claude.ai/code)

Cloud sessions in the environment that has `agent-skills` + `digitizer-os` as
sources load the skills automatically: `digitizer-os` via its root `SKILL.md`,
and this repo via `.claude/skills/` — committed relative symlinks into
`skills/`, so the plugin layout stays the single source of truth. Sessions
clone fresh from `main`, so they're always current. For sessions in **other**
repos, commit this to that repo's `.claude/settings.json`:

```json
{
  "extraKnownMarketplaces": {
    "digitizer-skills": {
      "source": { "source": "github", "repo": "Digitizers/agent-skills" }
    }
  },
  "enabledPlugins": {
    "agent-skills@digitizer-skills": true,
    "digitizer@digitizer-skills": true
  }
}
```

## Install — OpenClaw / plain symlinks (fallback)

```bash
git clone https://github.com/Digitizers/agent-skills.git ~/Documents/GitHub/agent-skills
cd ~/Documents/GitHub/agent-skills && ./install.sh ~/.agents/skills
```

`install.sh` symlinks every folder under `skills/` into the target (default
`~/.claude/skills/`). It's idempotent and safe — it replaces a stale symlink and
skips any real directory of the same name. Updates arrive via `git pull`.

> **Migrating from the old symlink install?** The skill folders moved from the
> repo root into `skills/`, so symlinks created before that point at dead paths.
> After installing the plugin, remove the old links so skills don't load twice:
> `find ~/.claude/skills -maxdepth 1 -type l ! -exec test -e {} \; -delete`
> (deletes only broken symlinks), or re-run `./install.sh` if you're staying on
> the symlink flow.

## Adding a skill

Drop a new `skills/skill-name/SKILL.md` folder in, keep `SKILL.md` under ~100
lines, give the `description` a clear "Use when …" trigger, **and add the
cloud symlink** so web/mobile sessions load it too:

```bash
ln -s ../../skills/skill-name .claude/skills/skill-name
```

Then commit both. `tools/trigger-eval/validate_spec.py` fails if the symlink
is missing or points at the wrong skill, so a forgotten link can't slip
through. Split detail into `REFERENCE.md` when it grows. Machines with the
plugin installed pick it up on the next marketplace refresh — no re-install.

## License

MIT.
