# Handoff — three modes, three guards (design)

Date: 2026-09-29
Status: approved in chat, not yet implemented
Skill: `skills/handoff`

## Why

The handoff skill writes one document for one situation: a session that is
ending. Three situations actually produce handoffs, and they have different
readers:

1. **Compaction** — the reader is *this same agent*, moments later, with its
   memory erased but its workspace and tools untouched.
2. **Another agent** — same workspace (same machine, different agent) or a
   different workspace (different machine or checkout).
3. **Subscription quota** — the 5-hour window or the weekly limit is running
   out, and work continues in another session or another account, possibly on
   another machine.

Today all three produce the same document, and two of them have no trigger at
all: the only automatic trigger is one context threshold.

Reference for the quota case: [ofeklevy11/claude-handoff](https://github.com/ofeklevy11/claude-handoff)
— a Claude Code plugin that stops the session when the 5-hour or weekly limit
is nearly spent. We adopt its findings (the statusline bridge, the mechanical
gate, a paste-ready prompt) and build our own, inside this skill, rather than
depending on it.

## What was verified, not assumed

Measured on Claude Code 2.1.x by running a throwaway `claude -p` session with
its own settings file whose hooks dumped stdin:

- **Hook payloads carry no rate-limit data.** `UserPromptSubmit` delivers
  `cwd, hook_event_name, permission_mode, prompt, prompt_id, scratchpad_dir,
  session_id, transcript_path`; `Stop` adds `background_tasks, effort,
  last_assistant_message, session_crons, stop_hook_active`. No `rate_limits`
  in either. A statusline bridge is therefore **required** for quota
  detection, exactly as the reference plugin found.
- **`rate_limits` reaches the statusline command** (`five_hour`, `seven_day`,
  and `spend_limit` behind a gateway; each with `used_percentage` and
  `resets_at`), per the statusline documentation, and only for
  subscription accounts after the first API response.
- **No `get_usage` tool** appears in the documented tool list. It may exist
  undocumented in the desktop app; nothing here may depend on it.
- **Compaction cannot be triggered programmatically**, and the auto-compact
  threshold cannot be read at runtime. Asking the user to compact is the only
  available action, not a design preference.
- **`PreCompact` can block** (exit 2), which makes it usable as a safety net.
- **`CLAUDE_CODE_SESSION_ATTENDED` distinguishes attended from headless runs**:
  `1` in an interactive session, `0` in a `claude -p` run. Measured the same
  way — the headless child overwrote the value it inherited, so the variable
  is set per session rather than passed down. This settles the spec's only
  open question.

## Design

### Components

| Component | Status | Role |
|---|---|---|
| `scripts/context-guard.py` | exists, extended | two thresholds instead of one |
| `scripts/quota-guard.py` | new | reads the bridge's state file, warns and stops |
| `scripts/statusline-bridge.sh` | new | wraps the user's statusline, writes `rate_limits` to a state file, passes through |
| `scripts/handoff-gate.py` | new | mechanical check before any handoff is delivered |
| `SKILL.md` | extended | one document contract + a mode table |
| `references/modes.md` | new | per-mode blocks, delivery layout, prompt templates |
| `REFERENCE.md` | extended | installing the second threshold, the bridge, and the PreCompact net |

### Triggers

| Trigger | Detected by | Injected instruction |
|---|---|---|
| 70% context used (30% free) | `context-guard.py`, first threshold | write the handoff now, keep working |
| 80% context used (20% free) | same script, second threshold, **own marker** | finish the current action, refresh the handoff, stop, ask the user to compact |
| quota warn (5h ≥ 70%) | `quota-guard.py` | do not start long work without a save point |
| quota act (5h ≥ 80%, weekly ≥ 93%) | `quota-guard.py` | stop, write a quota handoff with a paste-ready prompt |
| compaction starting anyway | `PreCompact` hook | ensure a current handoff exists first |
| handing to another agent | the user says so | ask same-workspace or different-workspace, then write that mode |

Each threshold keeps its **own marker file**. A 70% firing must not disarm
80% — the same class of bug already fixed in this script between "assumed"
and "proven" windows.

### The document

The existing four-part contract is unchanged. A mode **adds** a block; it
never replaces the core. What each mode adds is decided by what its reader
cannot know:

| Mode | Adds |
|---|---|
| compaction | what is mid-action right now; the user's constraints and decisions verbatim; **approaches already tried and rejected**; a verification command that re-establishes state (branch, sha, `git status`) |
| other agent, same workspace | the above, plus what is uncommitted and the user's unwritten conventions |
| other agent, different workspace | the above, plus setup: repository, branch, exact sha, environment variable **names**, required MCP servers and skills, and one command that must pass before touching code |
| quota | a paste-ready prompt, and when the window resets (`resets_at`), so the user can choose between waiting and switching accounts |

**Approaches already tried and rejected** is the most valuable of these and is
absent from today's contract. It is the first thing compaction destroys and
the reason a post-compaction agent walks into the same wall.

### Delivery

The durable-location rule is unchanged: `~/.claude/handoffs/<project-slug>/`,
never `/tmp`, `$TMPDIR` or the session scratchpad. Modes that need sibling
files get a directory instead of a single file:

```
~/.claude/handoffs/<project>/2026-09-29-1412-quota/
├── HANDOFF.md
├── PROMPT.txt        # quota and different-workspace modes only
└── workspace.zip     # only when the user asks
```

Compaction and same-workspace modes stay a single file — there is no one to
paste a prompt to.

The zip is opt-in, covers only work not already in git, and never includes
`.env`, credentials or `node_modules`.

### The gate

`handoff-gate.py` runs before delivery and fails on: an empty section,
unnumbered next steps, a path named in the document that does not exist, a
required block missing for the current mode, or a secret (keys, tokens,
connection strings). A failing gate means the handoff is not delivered; it is
fixed. The scan covers `HANDOFF.md`, `PROMPT.txt` and the zip's file list.

### The bridge

`statusline-bridge.sh` wraps whatever statusline command the user already has:
it captures `rate_limits` into a state file and passes the payload through
unchanged, so the visible statusline does not change. It touches
`settings.json`, so the skill **prints the exact JSON and asks once** — never
silent installation — and ships an off switch that restores the previous
setting. Without consent, quota mode still works manually and says so.

### Configuration

Environment variables in the settings `env` block, consistent with today's
`HANDOFF_THRESHOLD_PCT`; no new config file.

| Variable | Default | Meaning |
|---|---|---|
| `HANDOFF_THRESHOLD_PCT` | 70 | write the handoff |
| `HANDOFF_STOP_PCT` | 80 | stop and ask to compact |
| `QUOTA_WARN_PCT` | 70 | 5-hour window warning |
| `QUOTA_ACT_PCT` | 80 | 5-hour window stop |
| `QUOTA_WEEKLY_ACT_PCT` | 93 | weekly limit stop |

### Unattended runs

Headless `-p` runs and scheduled tasks are never stopped and never asked to
compact — nobody is there to answer, and stopping only kills the task. They
still get a handoff written silently. Detection is
`CLAUDE_CODE_SESSION_ATTENDED == "0"` (measured above) — a session is
unattended only when the value is exactly `"0"`; a missing or unrecognised
value is treated as attended, since an unrecognised value most likely means a
future Claude Code changed the variable, and treating that as attended keeps
the handoff nudge working instead of silently dropping it for everyone —
with `HANDOFF_UNATTENDED=1` as a manual override for anything the variable
misses.

## Testing

All offline, no Claude session, no token cost — the CI for this repository is
public.

- `test-context-guard.sh`, extended: the second threshold fires; each
  threshold has its own marker; a 70% firing does not disarm 80%.
- `test-quota-guard.sh`, new: reads a synthetic bridge state file; weekly vs
  5-hour; a missing or corrupt file stays silent instead of crashing.
- `test-handoff-gate.sh`, new: a valid document passes; an empty section, an
  invented path and a planted fake secret each fail it.
- `evals/evals.json` and `evals/triggers.json`: one case per mode, phrased in
  both Hebrew and English.

Deliberately **not** built: a real end-to-end Claude session like the
reference plugin's `e2e.py`. It costs tokens on every run.

## Out of scope

- A separate plugin or repository — this stays `skills/handoff` in
  `agent-skills`.
- Stopping unattended runs.
- Any dependency on the reference plugin at runtime.
