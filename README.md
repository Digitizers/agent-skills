# agent-skills

Portable, project-agnostic skills for **Claude Code** and **OpenClaw** — small,
reusable developer-workflow tools that work in any repo. Cloned once, symlinked
into your agent's skills directory, and versioned here so they're available on
every machine.

Each skill is a self-contained folder with a `SKILL.md` (the agent reads its
frontmatter `description` to decide when to load it) and optional
`REFERENCE.md` / `EXAMPLES.md` / `scripts/`.

## Skills

| Skill | What it does |
|---|---|
| [`codex-review-loop`](codex-review-loop/) | Drive a PR to convergence through the Codex AI reviewer — build → PR → `@codex review` → verify each finding against HEAD → fix the real ones with regression tests → re-trigger until clean → human reviews last. |
| [`pr-first-workflow`](pr-first-workflow/) | Default to a branch + PR for every change (code and docs); branch → commit → PR → review → merge → return to the default branch; direct-to-main only on an explicit OK. Pairs with `codex-review-loop`. |
| [`safe-prod-db-write`](safe-prod-db-write/) | Run one-off writes/backfills against a production DB safely — pull the connection into an `mktemp` file, dry-run, get explicit human authorization, execute, verify counts/invariants, clean up. |

## Install

**On any machine — clone once, then run the installer:**

```bash
git clone https://github.com/Digitizers/agent-skills.git ~/Documents/GitHub/agent-skills
cd ~/Documents/GitHub/agent-skills && ./install.sh
```

`install.sh` symlinks every skill folder into `~/.claude/skills/` (Claude Code).
It's idempotent and safe — it replaces a stale symlink and skips any real
directory of the same name, so it never clobbers unrelated skills. Pass a
different target for other agents:

```bash
./install.sh ~/.agents/skills     # OpenClaw, or any custom skills path
```

The symlink pattern keeps this repo the single source of truth — edit a skill
here, and every machine's agent picks it up. On a new machine: `git clone` +
`./install.sh` and every skill is back.

## Adding a skill

Drop a new `skill-name/SKILL.md` folder in, keep `SKILL.md` under ~100 lines,
give the `description` a clear "Use when …" trigger, and commit. Split detail
into `REFERENCE.md` when it grows.

## License

MIT.
