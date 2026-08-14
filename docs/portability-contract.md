# Cross-runtime skill portability contract

This repository is the public reference implementation for skills consumed by
Claude Code, OpenClaw and Hermes. A sibling skill repository may reuse
`tools/portability/validate.py`; runtime-specific copies are never canonical.

## Canonical layout

- Each canonical skill lives at `skills/<name>/SKILL.md`.
- Frontmatter `name` equals the containing directory.
- Skill-doc links are enumerated with a real CommonMark parser
  (markdown-it-py): inline, reference and image destinations — including
  inside nested lists and blockquotes — are checked to remain inside the
  repository and resolve; fenced, inline and indented code never contribute
  targets. Undefined reference labels are literal text per CommonMark, not
  links, and are not flagged. One error is reported per unique broken target
  per document.
- `evals/triggers.json`, when present, is a non-empty JSON list of
  `{"query": <non-empty string>, "should_trigger": <boolean>}` with at least
  one positive and one negative example.
- Claude Code cloud discovery uses committed relative links at
  `.claude/skills/<name> -> ../../skills/<name>`.

## Runtime adapters

| Runtime | Adapter | Canonical source |
|---|---|---|
| Claude Code desktop | Marketplace/plugin manifest | Repository checkout |
| Claude Code web/mobile | Committed `.claude/skills` relative links | Repository checkout |
| OpenClaw | Symlinks created by `install.sh <target>` | Repository checkout |
| Hermes | `skills.external_dirs` pointing to `skills/` | Repository checkout |
| Agent Plugins 1.0 clients (VS Code, Cursor, GitHub Copilot, ChatGPT/Codex) | Committed root `plugin.json` manifest | Repository checkout |

Adapters may expose the same folders but must not copy or edit them. A runtime
rollback removes the adapter and leaves the checkout unchanged.

### Agent Plugins 1.0 manifest

The canonical `skills/<name>/SKILL.md` layout is already the discovery layout
Agent Plugins 1.0 specifies (immediate children of `skills/`, one `SKILL.md`
each), so the adapter is metadata only: a committed `plugin.json` at the
repository root carrying the spec's `$schema` and `name`. Committed rather
than generated on purpose — a plain `git clone` is then a complete, loadable
plugin in every 1.0 client, with no build step. Both this manifest and the
Claude manifest (`.claude-plugin/plugin.json`) describe the same `skills/`
tree; neither copies skill content, so the expose-not-copy rule holds. The
repo ships no MCP servers, so there is no `mcp.json`. Same versioning
convention as the Claude manifest: no `version` field — updates track git
commits.

`validate.py` checks the manifest whenever `plugin.json` exists at the repo
root, and `--require-agent-plugins-manifest` (used by this repo's CI) makes
its absence an error. The check is deliberately stricter than the spec in
one respect: unknown top-level fields are errors here, not warnings, so CI
stays deterministic.

## Public boundary

Before moving a skill into a public repository:

1. Remove private repository names, private marketplace identifiers and
   machine-specific absolute paths.
2. Keep credential **names** only; never commit values, tokens or local env
   files. The deterministic validator rejects committed env files and
   non-placeholder env templates, but it is not a general-purpose secret
   scanner. Repository secret scanning and human review remain required.
3. Keep Digitizer-specific identity, pricing, clients and internal operating
   context in private overlays.
4. Verify every reference and trigger fixture with the shared validator.
5. Review write-capable workflows for explicit approval and rollback gates.

Run:

```bash
python3 -m pip install -r tools/trigger-eval/requirements.txt
python3 tools/portability/validate.py \
  --repo . \
  --visibility public \
  --require-cloud-links
```

The validator is deterministic and performs no network calls. Its public
boundary checks are intentionally limited to committed env files and env
templates, known private identifiers, machine-specific absolute paths and
unsafe symlink targets. Parsing arbitrary credential values across programming
and configuration languages is outside this contract — credential *detection*
belongs to gitleaks, which CI runs against the full git history with the
pinned config in `.gitleaks.toml`. Division of labor: validator = structural
and bounded hygiene checks; gitleaks = secret detection; human review = final
gate.
Markdown link enumeration is parser-backed (markdown-it-py, CommonMark
preset) — see the canonical-layout section for exactly what that covers and
what is deliberately not flagged.
