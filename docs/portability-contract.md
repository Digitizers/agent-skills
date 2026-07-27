# Cross-runtime skill portability contract

This repository is the public reference implementation for skills consumed by
Claude Code, OpenClaw and Hermes. A sibling skill repository may reuse
`tools/portability/validate.py`; runtime-specific copies are never canonical.

## Canonical layout

- Each canonical skill lives at `skills/<name>/SKILL.md`.
- Frontmatter `name` equals the containing directory.
- Markdown references are relative, remain inside the repository and resolve.
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

Adapters may expose the same folders but must not copy or edit them. A runtime
rollback removes the adapter and leaves the checkout unchanged.

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
and configuration languages is outside this contract.
