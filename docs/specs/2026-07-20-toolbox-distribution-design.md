# Digitizers Toolbox Distribution — Design

**Date:** 2026-07-20
**Status:** Approved (design review with Ben, section by section)
**Scope:** Distribution of all Digitizers-owned agent tooling (skills + MCP configs) to every device of every team member — Ben (laptop, desktop, phone/cloud) and Avi (partner, one machine + phone/cloud), and any future member.

## Problem

Digitizers agent tooling is spread across 12+ repos with four inconsistent skill layouts (`skills/`, `files/SKILL.md`, root `SKILL.md`, `.claude/skills/<name>/`). Only two repos (`agent-skills`, `digitizer-os`) are installable as plugins today. Everything else loads only when working inside its own repo. There is no team story: Avi has Claude Code (Max/Pro) but no GitHub account and no access to any of it. Stale local copies of repo skills (`elementor-mcp`, `wordpress-api-pro`) have already caused version drift on Ben's machine.

## Decision

**Federated plugin marketplace (approach A) + committed project settings (approach C) as a complementary layer.**

Skills stay in their source repos — no vendoring, no mirroring (rejected approach B: it recreates the drift we just cleaned up). Each tool repo becomes a self-contained Claude Code plugin. The existing `digitizer-skills` marketplace (served from `agent-skills`) indexes them all. Devices install once and track `main` via marketplace auto-update. Every Digitizers repo also commits `.claude/settings.json` so cloud/phone sessions load the full toolbox with zero device setup.

## Inventory

### In the toolbox (9 plugins, one marketplace)

| Plugin | Repo | Visibility | Current skill location | Work needed |
|---|---|---|---|---|
| `agent-skills` | agent-skills | public | `skills/` (6 skills incl. freepik after PR #13) | none |
| `digitizer` | digitizer-os | **private** | root `SKILL.md` | none |
| `cloudways-mcp` | cloudways-mcp | public | `.claude/skills/cloudways-mcp/` | plugin-ify |
| `hostinger-mcp` | hostinger-mcp | public | `.claude/skills/hostinger-mcp/` | plugin-ify |
| `aura-mcp` | aura-mcp | public | `files/SKILL.md` + `files/references/` | plugin-ify |
| `wordpress-api-pro` | wordpress-api-pro | public | `wordpress-api-pro/` | plugin-ify |
| `siteagent-elementor-studio` | siteagent-elementor-studio | public | `files/SKILL.md` | plugin-ify |
| `meta-ads-mcp` | meta-ads-mcp | public | `meta-ads-mcp/SKILL.md` | plugin-ify |
| `sumit-mcp` | sumit-mcp | public | `.claude/skills/sumit-mcp/` + `.mcp.json.example` | plugin-ify + MCP config |

### Excluded, with reasons

- `icp_tool` — unmodified fork of someone else's work; not Digitizers tooling.
- `elementor-mcp`, `emcp` — their skills (`wp-plugin-dev`, `wp-plugin-review`) are dev aids for building those WordPress plugins, not tools for using them. Stay project-scoped.
- `novamira` — its skill lives inside product source (`novamira-visual/src/skills/`); part of the product, not distributable.
- `seo-skills`, `digitizer-studio` — empty (README only). When they gain content they join via the same pattern.
- Third-party skills on Ben's machines (ClaudeKit `ckm:*`, mattpocock/vercel/upstash, `ui-ux-pro-max`) — not Digitizers-owned; they have their own sync mechanisms (`ck init`, `.skill-lock.json` + installer, plugin marketplace respectively).
- `notary-memory` MCP — Ben-personal memory runtime; never distributed.

## Per-repo plugin pattern

One PR per tool repo (7 repos), identical shape:

```text
.claude-plugin/plugin.json          # name, description, author
skills/<skill-name>/SKILL.md        # canonical location (git mv from current spot)
.claude/skills/<name> -> ../../skills/<name>   # symlink so cloud sessions auto-load (agent-skills#12 pattern)
```

Rules:

- **`git mv`, never copy.** Single source of truth is preserved. Anything referencing the old path (e.g. siteagent-elementor-studio's installer referencing `files/SKILL.md`) is updated in the same PR.
- **Skill name = plugin name.** Guarantees no collisions across the 9 plugins.
- **No version bumps required.** Marketplace auto-update tracks commits; `plugin.json` version is cosmetic.

## Marketplace

`agent-skills/.claude-plugin/marketplace.json` gains 7 entries of the existing form:

```json
{ "name": "cloudways-mcp", "source": { "source": "github", "repo": "Digitizers/cloudways-mcp" } }
```

**Ordering constraint:** the 7 repo PRs merge first; the marketplace PR merges last, so the marketplace never advertises a plugin that does not exist yet.

## Cloud / phone layer

Every toolbox repo commits `.claude/settings.json` with the identical full-toolbox block:

```json
{
  "extraKnownMarketplaces": {
    "digitizer-skills": {
      "source": { "source": "github", "repo": "Digitizers/agent-skills" }
    }
  },
  "enabledPlugins": {
    "agent-skills@digitizer-skills": true,
    "digitizer@digitizer-skills": true,
    "cloudways-mcp@digitizer-skills": true,
    "hostinger-mcp@digitizer-skills": true,
    "aura-mcp@digitizer-skills": true,
    "wordpress-api-pro@digitizer-skills": true,
    "siteagent-elementor-studio@digitizer-skills": true,
    "meta-ads-mcp@digitizer-skills": true,
    "sumit-mcp@digitizer-skills": true
  }
}
```

Effects: any cloud/phone session in any Digitizers repo loads the whole toolbox; anyone opening a repo locally gets a one-time prompt to enable the plugins (self-onboarding). The same block is published in `agent-skills` README as a snippet for future repos and client projects.

Known limitation: `digitizer@digitizer-skills` resolves only for users whose GitHub auth can read the private `digitizer-os` — by design.

## Avi onboarding

Org side (Ben, once):

1. Avi creates a GitHub account.
2. Add as org **member**, team `Dev` (exists).
3. Grant team `Dev` **read** on `digitizer-os` — the only private grant needed; everything else is public.
4. Enforce org-wide **2FA** (the strategic brain is now reachable from two accounts; verify if already on).

Device side (Avi, once per machine):

```bash
gh auth login --web --git-protocol https
git config --global url."https://github.com/".insteadOf "git@github.com:"
```

Then in Claude Code: `/plugin marketplace add Digitizers/agent-skills`, install all 9 plugins, enable marketplace auto-update.

**`ONBOARDING.md` in `agent-skills`** collects all of the above as a single copy-paste page, including the optional env vars list (e.g. `FREEPIK_API_KEY`) — only for tools actually used. Phone/cloud for Avi needs no extra steps once his GitHub is connected to Claude (settings.json layer covers it).

Open item (resolve during implementation): if the CLI supports bulk-install from a marketplace, use it; otherwise ONBOARDING.md lists the 9 install commands explicitly.

## MCP servers

Principle: **config travels, secrets never.**

| Server | Handling |
|---|---|
| `sumit-mcp` | Plugin ships `.mcp.json` with `${VAR}` placeholders only (derived from existing `.mcp.json.example`). Values live in each machine's env. |
| Cloudways hosted MCP (`cloudways-s1`/`s5`) | URL embeds a per-account token — sharing the URL is sharing access. Never committed. Each person provisions their own from the Cloudways dashboard; ONBOARDING.md documents how. |
| `elementor-mcp` | Per-site (WP REST + app password). Stays project-level config; the `siteagent-elementor-studio` skill documents the wiring. |
| `notary-memory` | Ben-personal. Out of scope. |

Every plugin that references an MCP server documents its required env vars in its README. No secret values in git anywhere, including private repos.

## Cleanup (Ben's machines)

- Delete stale copies from `~/.claude/skills`: `elementor-mcp` (stale ancestor of siteagent-elementor-studio v1.3.2), `wordpress-api-pro` (3.5.1 vs repo 3.8.2), `ui-ux-pro-max` (duplicate of installed plugin).
- Merge agent-skills PR #13 (freepik) after codex review.
- Desktop machine gets the standard device setup (same as Avi's device steps, minus GitHub signup).

## Verification

1. After each plugin-ification PR: install locally from the branch, confirm the skill loads in a fresh session.
2. After the marketplace PR: `marketplace update` + install all 9 on Ben's laptop; confirm all load.
3. Cloud session from phone in a repo carrying `settings.json`: confirm toolbox loads.
4. **Acceptance test: Avi completes ONBOARDING.md unassisted.** If he gets through without questions, the design works.

## Risks

- **Path moves break external references.** Mitigated: each plugin-ification PR greps the repo (and its installer/scripts) for old skill paths and updates them; codex review loop on every PR.
- **Marketplace advertises before plugin exists.** Mitigated by merge ordering (repos first, marketplace last).
- **Private-repo clone failures on new machines (SSH publickey).** Known failure mode, hit on Ben's laptop; the `insteadOf` rewrite + `gh auth login` is part of standard device setup.
- **Secret leakage via MCP configs.** Mitigated: placeholders-only policy, secret scan before every commit (already practiced in PR #13).
