# Digitizers Toolbox — Device Onboarding

One-time setup per machine. Prereqs: git, GitHub CLI (`gh`), Claude Code v2.1.142+.

## 1. GitHub auth (HTTPS)

```bash
gh auth login --web --git-protocol https
git config --global url."https://github.com/".insteadOf "git@github.com:"
```

The rewrite matters: plugin installs clone over SSH URLs by default, and a
machine without registered SSH keys fails with `Permission denied (publickey)`.

## 2. Install the toolbox

Inside a Claude Code session **on the machine itself** (CLI/desktop/IDE —
cloud sessions don't run plugin installs; phone/web is covered in §6):

```
/plugin marketplace add Digitizers/agent-skills
/plugin install agent-skills@digitizer-skills
/plugin install cloudways-mcp@digitizer-skills
/plugin install hostinger-mcp@digitizer-skills
/plugin install aura-mcp@digitizer-skills
/plugin install wordpress-api-pro@digitizer-skills
/plugin install siteagent-elementor-studio@digitizer-skills
/plugin install meta-ads-mcp@digitizer-skills
/plugin install sumit-mcp@digitizer-skills
```

Then `/plugin` → Marketplaces → `digitizer-skills` → **enable auto-update**.
From then on every commit to a tool repo reaches every machine automatically.

## 3. Windows note

The toolbox plugins ship their skills through git **symlinks**. On Windows,
enable Developer Mode and set `git config --global core.symlinks true`
**before** installing — the plugin cache clone inherits it. macOS/Linux and
WSL need nothing.

## 4. Optional env vars (only for tools you use)

| Tool | Vars | Where |
|---|---|---|
| magnific-studio (in agent-skills) | `FREEPIK_API_KEY` | `~/.claude/freepik.env`, perms 600 (Freepik API key — file name kept for compatibility) |
| sumit-mcp | `SUMIT_DEFAULT_ACCOUNT`, `SUMIT_MAIN_COMPANY_ID`, `SUMIT_MAIN_API_KEY`, `SUMIT_ALLOW_CHARGE`, `SUMIT_MAX_CHARGE`, `SUMIT_CONFIRM_SECRET` | shell env / launchd; see the repo's references/installation.md |
| Cloudways hosted MCP | per-account server URL | provision from the Cloudways dashboard (URL embeds your token — never share or commit it) |

## 5. Team members

If you were added to the Digitizers GitHub org, your team lead will point you
at one additional internal install step.

## 6. Phone / web (claude.ai/code)

The marketplace only reaches machines — cloud sessions never run plugin
installs. What a cloud session *does* load is the skills of every repo listed
as a **source** of its environment (cloned fresh from `main` on session start,
so always current). One-time setup:

1. claude.ai → Settings → Connectors → connect GitHub and grant it the
   Digitizers repos you can read (separate from `gh auth login`).
2. In Claude Code, open the environment picker → ⚙️ on your cloud environment
   — create one first via "Add cloud environment…" if you don't have any.
   Keep a single environment so there's one to maintain. Add the toolbox
   repos as sources:

   ```
   Digitizers/agent-skills
   Digitizers/cloudways-mcp
   Digitizers/hostinger-mcp
   Digitizers/aura-mcp
   Digitizers/wordpress-api-pro
   Digitizers/siteagent-elementor-studio
   Digitizers/meta-ads-mcp
   Digitizers/sumit-mcp
   ```

   …plus any internal repos from step 5.

Every **new** phone/web session then opens with all toolbox skills loaded —
open sessions don't refresh. This works because each repo commits a
`.claude/skills/<name>` symlink alongside its plugin layout; keep that
dual-path pattern in any new tool repo. To make the sessions actually
**operate** the tools (not just know them), add the env vars in §7.

## 7. Cloud environment variables — connecting the tools

Skills make a cloud session *know* the tools; the connections themselves come up from
env vars. Cloud/phone sessions load each toolbox repo's committed `.mcp.json` from the clone.
Those files keep secrets as `${VAR:-}` env placeholders — the real values come from the
cloud environment's **environment variables** (the §6 environment → edit →
Environment variables). The `:-` defaults keep the configs valid when a variable is
unset; that tool's connection then just shows as unavailable in `/mcp` (it can't
authenticate) until you set the variable — harmless, so set only what you use. The one
exception is sumit-mcp, whose required `SUMIT_*` placeholders are deliberately bare: unset
means Claude Code refuses the config and the billing server never launches (fail closed).

| Tool | Env vars |
|---|---|
| cloudways-mcp | `CLOUDWAYS_ACCESS_TOKEN` (Access Token from platform.cloudways.com → API; minimum role that works). Connection name: `cloudways-env` — avoids shadowing the documented user-scope names (`cloudways`, `cloudways-<client>`) |
| hostinger-mcp | `HOSTINGER_API_TOKEN` (hPanel → API); optional `HOSTINGER_MCP_BINARY` to load one category binary (e.g. `hostinger-vps-mcp`) instead of all 127 tools |
| aura-mcp | `AURA_MCP_TOKEN` (`aura_…` management token from Aura → Fleet → Agent Tokens) |
| sumit-mcp | the `SUMIT_*` set from §4 |
| siteagent-elementor-studio (Elementor MCP) | `WP_URL`, `WP_USERNAME`, `WP_APP_PASSWORD` — same trio wordpress-api-pro reads, so one environment targets one client site with both toolkits |

Notes:

- **Environment variables are visible to anyone who can edit the environment** (there is
  no dedicated secrets store yet) — use minimum-role, revocable tokens, one per purpose.
- Restricted network policies must allow the tool endpoints: `mcp.cloudways.com`,
  `app.my-aura.app`, `api.hostinger.com`, `api.sumit.co.il`, your WordPress site, and the
  npm registry (the Hostinger and Elementor connections launch via `npx`).
- The same `.mcp.json` files work on devices too — export the vars in your shell and the
  connections come up without any `claude mcp add`.
