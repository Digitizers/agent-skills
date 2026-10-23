# Codex onboarding

Use this path for Codex desktop and CLI. The [Claude instructions](../ONBOARDING.md)
remain separate; their `/plugin` commands and cloud environment setup do not
configure Codex. A skill provides instructions; an MCP connection provides tools.
Verify both before calling a tool ready.

## Install the developer skills

Install git, GitHub CLI (`gh`) and a Codex CLI with `codex plugin` support.
Authenticate GitHub with `gh auth login --web --git-protocol https` if needed.
Do not change global Git URL rewrites just to install this HTTPS source.

```bash
codex plugin marketplace add https://github.com/Digitizers/agent-skills
codex plugin add agent-skills@digitizer-skills
```

For an existing local checkout, replace the marketplace source with its absolute
path. Reuse an existing `digitizer-skills` registration instead of creating
another copy. `codex plugin marketplace --help` describes the installed CLI's
refresh commands. Claude's auto-update setting does not configure Codex updates.

In desktop, inspect the plugin's installed/enabled state and skill inventory.
In CLI, open `/plugins`. Start a new chat/session and verify that all ten skills
are available from the intended source. Installing into an existing chat does
not refresh that chat's skill catalog. These steps verify discovery, not that
every workflow or external service has been exercised.

The team lead supplies private marketplace instructions separately. Do not put
private repositories, endpoint URLs, client names or local audit reports in this
public onboarding document.

## Keep other agents working

Use one Codex discovery path for each skill. Before replacing a legacy install,
record the affected plugin/skill names and Codex settings without their secrets.
Disable duplicates in Codex's plugin UI or `skills.config` only. Retain shared
`~/.agents/skills` files and symlinks: Claude, OpenClaw or another host may use them.
Keep Claude manifests, `.claude/skills`, hooks and config unchanged.

For rollback, restore only the Codex entries changed during this installation.
Keep the previous source commit and a reversible record of those entries; a
whole config backup can contain credentials. Restart a chat and check discovery
again. Do not remove another agent's install to fix a Codex duplicate.

## Connect services separately

Prefer an already connected service or supported native plugin. Inspect its
actual tool inventory in the current chat. Do not register a second connection
just because its name differs from a Claude MCP prefix.

For a custom MCP server, Codex uses `[mcp_servers.<name>]` in
`~/.codex/config.toml` (or trusted project configuration). A repository's Claude
`.mcp.json` and `CLAUDE_PLUGIN_ROOT` expressions are not a user-scope Codex setup.
A native plugin can bundle MCP configuration, but verify its manifest, installed
state and tool discovery before relying on it. The eight service entries in the
Claude install list are not a verified Codex install list.

For an approved HTTP endpoint with bearer authentication, use the environment
variable name, never the token value:

```toml
[mcp_servers.service]
url = "https://mcp.example.invalid/mcp" # Replace with the approved non-secret URL.
bearer_token_env_var = "SERVICE_API_TOKEN"
```

For an approved local launcher:

```toml
[mcp_servers.service]
command = "/absolute/path/to/approved-mcp-launcher"
env_vars = ["SERVICE_API_TOKEN"]
```

These are alternatives, not two entries to paste together. Ben supplies secrets
in existing mode-600 files and makes the required variables available to the
Codex process. A desktop launch may not inherit a terminal's environment.
Do not copy values from Claude config, paste tokens into commands or logs, or
print raw config / `codex mcp get` output when collecting evidence. Some service
URLs contain credentials; those must also stay out of transcripts and reports.
Use `codex mcp login <name>` only when that service supports OAuth; it does not
repair an incorrect endpoint or replace a missing API key.

| Toolbox service | Connection requirement |
| --- | --- |
| [Cloudways](https://github.com/Digitizers/cloudways-mcp) | Existing approved account connection or hosted MCP endpoint; keep account boundaries. A credential-bearing URL is secret. |
| [Hostinger](https://github.com/Digitizers/hostinger-mcp) | Approved server launcher and `HOSTINGER_API_TOKEN`; choose the required category using the service's installation guide. |
| [Aura](https://github.com/Digitizers/aura-mcp) | Approved endpoint and `AURA_MCP_TOKEN`; installing its skill grants no control-plane access. |
| [WordPress](https://github.com/Digitizers/wordpress-api-pro) | Site-specific connection described by its skill; do not select a client site during onboarding. |
| [Elementor](https://github.com/Digitizers/siteagent-elementor-studio) | Approved MCP launcher and the `WP_URL`, `WP_USERNAME`, `WP_APP_PASSWORD` set; shares the intended site with WordPress. |
| [Meta Ads](https://github.com/Digitizers/meta-ads-mcp) | Supported connected ads service; verify identity and capabilities rather than equating it with any similarly named ads plugin. |
| [SUMIT](https://github.com/Digitizers/sumit-mcp) | Built server launcher and required `SUMIT_*` variables; retain the service's fail-closed configuration and charge gates. |
| [Bunny](https://github.com/Digitizers/bunny-mcp) | Approved launcher and `BUNNY_API_KEY`; keep write access closed. Stream/storage capabilities require their own configuration. |

Follow each service's canonical installation guide for launch arguments and
credential provisioning. These requirements do not assert that every service
has a native Codex plugin or is connected on a particular machine.

## Verify without operating production

1. Open `/mcp` in Codex CLI, or inspect the desktop chat's tool inventory.
   Check each expected server, its discovery error and available tools.
2. Distinguish **configured**, **catalog discovered**, **connected in this chat**
   and **an approved operation succeeded**. A cached catalog or a saved auth
   method alone proves neither a live connection nor account permissions.
   `authStatus: unsupported` alone is not a discovery failure or a success
   verdict. A missing runtime status is unverified.
3. Collect only server names, tool counts, status/error categories and time.
   Keep local reports private; omit endpoints, arguments, headers, environment
   values and raw errors. Label checks skipped or failed instead of reporting
   the entire toolbox connected.
4. For onboarding, stop at MCP initialization/tool discovery. Do not list client
   resources, touch production, call billing/write tools or send a message as a
   connection test. Exercise business operations only in their separately
   authorized task. Authentication that needs Ben stays pending.

If a server fails discovery, distinguish a connection/configuration problem
from a server defect before proposing changes. Track it in the existing work
item; a confirmed defect in a Digitizer tool belongs in that tool's repository.
Check the same service from its existing host before changing shared settings.

Official references: [plugins](https://learn.chatgpt.com/docs/plugins),
[Codex MCP configuration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).
CLI examples were checked against the installed `codex plugin` and `codex mcp`
help on 2026-10-24; re-check help when a machine has a different CLI version.
