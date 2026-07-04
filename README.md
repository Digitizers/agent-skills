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

## Install

Clone once, then symlink the skills you want into your agent's skills directory
(the symlink pattern keeps the repo the single source of truth):

```bash
git clone https://github.com/Digitizers/agent-skills.git ~/Documents/GitHub/agent-skills

# Claude Code (user-level skills):
ln -s ~/Documents/GitHub/agent-skills/codex-review-loop ~/.claude/skills/codex-review-loop

# OpenClaw (adjust to your skills path):
ln -s ~/Documents/GitHub/agent-skills/codex-review-loop ~/.agents/skills/codex-review-loop
```

On a new machine, `git clone` + re-create the symlinks and every skill is back.

## Adding a skill

Drop a new `skill-name/SKILL.md` folder in, keep `SKILL.md` under ~100 lines,
give the `description` a clear "Use when …" trigger, and commit. Split detail
into `REFERENCE.md` when it grows.

## License

MIT.
