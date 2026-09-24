---
name: handoff
description: >-
  Use when the current session's work must continue in a fresh agent session —
  the context window is nearly full or about to be compacted, the user is
  ending a work session and will resume later, the work is being handed to
  another agent, machine, or teammate, or the user asks for a "handoff",
  "handoff document", "session summary to continue from", or to "prepare this
  for the next session". Also fires when a hook or system reminder reports
  high context usage and asks for a handoff. Not for writing project
  documentation for humans (README, docs, changelogs, reports) — only for
  transferring this session's state to a future agent session.
argument-hint: "What will the next session focus on?"
---

# Handoff

Write a handoff document summarising the current conversation so a fresh
agent can continue the work with zero access to this session's context.

The next agent knows **nothing**: not the codenames you invented mid-session,
not which files you touched, not why a decision was made. Write for that
reader.

## Output contract

Produce **one markdown document**. It has exactly these parts, in this order:

1. **Project overview** — open with the project's purpose, background,
   resources, and a plain description. List the tools in play (CLIs, MCP
   servers, scripts, services) and *how to use each one* — commands,
   entry points, and any non-obvious invocation details.
2. **Details** — policies, conventions, data structures, schemas, API
   contracts, environment specifics. Everything the next agent must hold as
   ground truth while working.
3. **Suggested skills** — a named section listing the skills the next agent
   should invoke, each with one line on when/why.
4. **Current state → open issues → what to do next** — the document ends
   with these three, in this order. State what is done and verified, what is
   unresolved or blocked, and the concrete next steps.

Close the document with an explicit instruction to the next agent: *remember
this information and wait for further instructions — do not start working on
anything.* The handoff transfers state, it does not assign work.

## Rules

- **Language:** write the document in the primary language of the current
  chat (Hebrew chat → Hebrew document; code identifiers, commands, and paths
  stay as-is).
- **No duplication:** content already captured in other artifacts — specs,
  plans, ADRs, issues, commits, diffs — is referenced by path or URL, never
  restated. The handoff carries only what lives nowhere else.
- **Redaction:** redact API keys, tokens, passwords, connection strings, and
  personally identifiable information. Keep credential *names* (`STRIPE_KEY`)
  so the next agent knows what to load; never the values.
- **Arguments:** if the user passed arguments, treat them as a description of
  what the next session will focus on and weight the document accordingly —
  expand the sections that session needs, compress the rest.

## Delivery

- **CLI / IDE sessions:** save the file to a **durable** directory under the
  user's home, outside any repository — by default
  `~/.claude/handoffs/<project-slug>/handoff-<slug>-<date>-<HHMM>.md` (another
  agent uses its own config directory under home the same way). Never
  overwrite an earlier handoff: if the path exists, add a suffix (`-2`, `-3`, …)
  — a second handoff the same day must not destroy the first one's state.
  If that directory is itself inside a git working tree (someone versions
  `~` or `~/.claude`), ignore it through that repository's local exclude file
  (`git -C <dir> rev-parse --git-path info/exclude`) so the handoff never
  becomes untracked state there either.
  Give the user the full path and say the file is persistent.
- **Never** make `/tmp`, `/private/tmp`, `$TMPDIR` or the session scratchpad
  the only copy. On macOS all of them sit under `/private/tmp`, which the OS
  purges of files untouched for about three days — and resuming a handoff days
  later is the normal case, not the exception.
- **Inside the project** only when the user asks for it, or when the project is
  not a git repository: a `.handoffs/` folder. In a git repository ignore it
  through the repository's local exclude file — resolve it with
  `git rev-parse --git-path info/exclude`, since `.git` is a file, not a
  directory, in a linked worktree or submodule — never by editing the tracked
  `.gitignore` —
  otherwise the handoff changes tracked state after all.
- **Recovery:** if an earlier handoff under a temporary path is gone and the
  harness keeps a session transcript (Claude Code:
  `~/.claude/projects/<project>/<session>.jsonl`), rebuild it by replaying the
  Write/Edit calls that created it, then save it to the durable location.
- **Chat UIs with downloads/artifacts:** deliver the markdown so it is
  downloadable from within the chat body (artifact or file attachment), and
  also state where it was saved if a filesystem exists.

## Skeleton

```markdown
# Handoff — <project / task name> (<date>)

## Project overview
<purpose, background, resources, description>

### Tools
- <tool> — <how to use: command / endpoint / auth env-var name>

## Details
<policies, data structures, schemas, conventions>

## Suggested skills
- <skill-name> — <when/why to invoke>

## Current state
## Open issues
## What to do next

---
*To the next agent: remember this information and wait for further
instructions. Do not start working on anything yet.*
```

## Auto-trigger at high context usage

A skill cannot watch the context window by itself — triggering above a
usage threshold is done with a hook that measures the transcript and injects
a reminder to invoke this skill. See [REFERENCE.md](REFERENCE.md) for the
ready-made hook script and installation.
