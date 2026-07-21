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

Inside any Claude Code session:

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
| freepik (in agent-skills) | `FREEPIK_API_KEY` | `~/.claude/freepik.env`, perms 600 |
| sumit-mcp | `SUMIT_DEFAULT_ACCOUNT`, `SUMIT_MAIN_COMPANY_ID`, `SUMIT_MAIN_API_KEY`, `SUMIT_ALLOW_CHARGE`, `SUMIT_MAX_CHARGE`, `SUMIT_CONFIRM_SECRET` | shell env / launchd; see the repo's references/installation.md |
| Cloudways hosted MCP | per-account server URL | provision from the Cloudways dashboard (URL embeds your token — never share or commit it) |

## 5. Team members

If you were added to the Digitizers GitHub org, your team lead will point you
at one additional internal install step.

## 6. Phone / web (claude.ai)

The marketplace only reaches machines — cloud sessions never run plugin
installs. What a cloud session *does* load is the skills of every repo listed
as a **source** of its environment (cloned fresh from `main` on session start,
so always current). One-time setup:

1. claude.ai → Settings → Connectors → connect GitHub and grant it the
   Digitizers repos you can read (separate from `gh auth login`).
2. In Claude Code, open the environment picker → ⚙️ on your cloud environment
   (keep a single environment so there's one to maintain) → add the toolbox
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
dual-path pattern in any new tool repo.
