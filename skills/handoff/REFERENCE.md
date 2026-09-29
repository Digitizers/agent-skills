# handoff — reference

## Auto-triggering the handoff at a context-usage threshold

Skills are loaded by the model when their description matches the task — a
skill cannot observe the context window on its own, and Claude Code has no
built-in "context reached N%" event. The `PreCompact` hook is the closest
native signal, but it fires only when compaction actually starts, which is
later than you want a handoff written.

The supported way to get "run handoff at 70%" is a **hook** that measures
usage from the session transcript and injects a reminder the model then acts
on:

1. Hook script ([scripts/context-guard.sh](scripts/context-guard.sh)) runs on
   `UserPromptSubmit` (and optionally `PostToolUse` for long autonomous
   turns).
2. It reads the hook input JSON (`transcript_path`, `session_id`), scans the
   transcript JSONL for the **latest assistant message's `usage` block**, and
   sums `input_tokens + cache_read_input_tokens + cache_creation_input_tokens
   + output_tokens`. Because that block only counts context sent into the
   *previous* model call, it also adds a conservative floor estimate for
   the transcript tail recorded after it and for the current hook payload
   (prompt / tool_response): **UTF-8 bytes ÷ 2**. A floor, not an average —
   ASCII counts 2 chars/token, Hebrew 1:1, CJK ~0.67, emoji ~2 per code
   point — so English prose counts ~2x high and the nudge can only fire
   early, never late. Any residual undercount (tokenizer byte-fallback on
   pathological input) self-heals: the next hook event reads a usage block
   that already prices this content exactly.
3. A `system` / `compact_boundary` record resets the running total: an
   auto-compact leaves the pre-compact conversation in the transcript file
   but not in the window, and pricing it after a restart is what made the
   hook report ">100%" on the first prompt of a resumed session (#35).
4. The window size is evidence-led. `CONTEXT_WINDOW_TOKENS` is treated as a
   floor: the largest context any single call in the transcript actually
   carried proves the window is at least that big, so a 1M-context session
   left on the 200k default is measured against 1M instead of being read as
   300% full. The evidence is the **most recent call only**, never a maximum
   over the transcript: a transcript outlives the settings it was written
   under — a resume can change the model, or the same model's window mode,
   and may keep the same session id doing it — and an observation that
   outlives its window is the same lie with the sign flipped, the guard going
   quiet at the real ceiling. The latest call cannot outlive anything: the
   window in force now is at least as big as the context it just carried.
   A session whose latest call is small is measured against the configured
   window, so on a 1M model the setting is still worth getting right. The reported percentage is also capped at 100 — the byte-floor
   estimate deliberately over-counts, and an impossible figure is a lie even
   when the nudge itself is warranted.
5. At `>= HANDOFF_THRESHOLD_PCT` (default 70) of `CONTEXT_WINDOW_TOKENS`
   (default 200000) it emits `additionalContext` instructing the agent to
   invoke the handoff skill, and drops a per-session marker file so it fires
   **once per session**.

The chain is: hook (deterministic measurement) → injected instruction →
model invokes the skill. The final hop is still model-performed, but an
explicit injected instruction naming the skill is a reliable trigger.

### Installation

Add to `~/.claude/settings.json` (user-wide) or `.claude/settings.json`
(per-project):

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash /path/to/agent-skills/skills/handoff/scripts/context-guard.sh"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "bash /path/to/agent-skills/skills/handoff/scripts/context-guard.sh"
          }
        ]
      }
    ]
  }
}
```

`UserPromptSubmit` alone is the low-noise option (checks once per user
message). Add the `PostToolUse` entry as well if sessions run long autonomous
turns where context can cross the threshold between user messages.

Tune via env (e.g. in the same settings file's `env` block):

```json
{ "env": { "HANDOFF_THRESHOLD_PCT": "70", "CONTEXT_WINDOW_TOKENS": "200000" } }
```

If you move between models with different windows, state the window per model
instead — it beats `CONTEXT_WINDOW_TOKENS` for the models it names, and any
other model falls back to it:

```json
{ "env": { "CONTEXT_WINDOW_BY_MODEL": "claude-fable-5-1=1000000,claude-opus-5=200000" } }
```

The key is the model id of the **latest** assistant line in the transcript (the
`message.model` field), so a session resumed on another model is measured
against that model's entry. Malformed entries are ignored one by one. There is
deliberately no built-in table of "1M models": the same model id can run in
more than one window mode, so only you know which one your sessions use.

### Caveats

- `CONTEXT_WINDOW_TOKENS` is a floor, not a fact — the hook widens it to the
  smallest known tier (200k / 500k / 1M) that fits the largest context the
  transcript proves was sent. Setting it correctly is still worth doing: the
  evidence only arrives once a call has actually carried that much context,
  so early in a 1M session the default can still fire early — at ~140k, 70% of
  the 200k default, which is 14% of the real window. Declare the window
  (`CONTEXT_WINDOW_BY_MODEL` or `CONTEXT_WINDOW_TOKENS`) to prevent it. When
  the window is neither declared nor proven, the nudge says the figure is an
  **assumed default** and that the alarm is false on a larger-window model, so
  an agent can check instead of obeying — as a *possible* false alarm, with the same figure against a 1M window. An alarm on an assumed window uses its own marker, so it never disarms the guard: once a later call proves the real window, the nudge can still fire against it. The same holds for a tier *inferred* from evidence (a 350k call proves at least 500k, not exactly 500k): the nudge says the figure is a lower bound, and every marker names the window it was raised against, so a later call that proves a wider window re-arms the guard. The guard fires once per session **per window**: moving to a model with a different declared or proven window is a new crossing and can be warned again. Above the largest known tier (1M) the window is one bucket, so a growing context alarms once, not on every call.
- Auto-compaction may summarize the conversation before any user prompt if a
  single turn overshoots — the `PostToolUse` variant closes most of that gap.
  After a compaction the count restarts from the boundary, so the hook can
  legitimately fire a second time later in a long session (the marker is per
  session, so in practice it fires once).
- The marker file lives in the OS temp dir and is keyed by session id;
  deleting it re-arms the hook for the same session.

### The second threshold

`HANDOFF_STOP_PCT` (default 80) is the point where the agent stops instead of
writing and continuing. Both thresholds are served by the same hook; each
keeps its own marker, so the 70% nudge never disarms the 80% stop.

```json
{ "env": { "HANDOFF_THRESHOLD_PCT": "70", "HANDOFF_STOP_PCT": "80" } }
```

Unattended sessions (`claude -p`, scheduled runs) are warned but never
stopped: there is nobody to run `/compact`, and stopping only kills the task.
Detection (`handoff_common.py`'s `attended()`) is stricter than a simple
"not 1" check: a session counts as unattended only when
`CLAUDE_CODE_SESSION_ATTENDED` is exactly `"0"`, or `HANDOFF_UNATTENDED=1` is
set. A missing or unrecognised value is treated as **attended** — an
unrecognised value most likely means a future Claude Code changed the
variable, and treating that as attended keeps the handoff nudge working
instead of silently dropping it for everyone.

### Quota detection — the statusline bridge

Hook payloads carry **no** rate-limit data. Measured on Claude Code 2.1.x:
`UserPromptSubmit` delivers `cwd, hook_event_name, permission_mode, prompt,
prompt_id, scratchpad_dir, session_id, transcript_path`, and `Stop` adds
`background_tasks, effort, last_assistant_message, session_crons,
stop_hook_active`. Neither carries `rate_limits`. The statusline command does.

So quota detection is two pieces: a bridge that records what the statusline
receives, and a hook that reads it.

```json
{
  "statusLine": {
    "type": "command",
    "command": "bash /path/to/agent-skills/skills/handoff/scripts/statusline-bridge.sh"
  },
  "hooks": {
    "UserPromptSubmit": [
      { "hooks": [ { "type": "command",
        "command": "bash /path/to/agent-skills/skills/handoff/scripts/quota-guard.sh" } ] }
    ]
  },
  "env": {
    "HANDOFF_STATUSLINE_INNER": "<your previous statusline command>",
    "QUOTA_WARN_PCT": "70",
    "QUOTA_ACT_PCT": "80",
    "QUOTA_WEEKLY_ACT_PCT": "93"
  }
}
```

The bridge passes the payload through to `HANDOFF_STATUSLINE_INNER` unchanged,
so the statusline looks exactly as it did. Removing the two entries restores
the previous setup; nothing else is touched.

**Ask before installing it.** It edits the user's `settings.json`. Print the
JSON, explain what the bridge does, and let them decide. Without it, quota
mode still works when the user asks for it by name.

`rate_limits` is present only for subscription accounts, and only after the
first response of a session. An API-key account never populates it, and the
guard stays silent rather than reading its absence as 0%.

### The PreCompact net

`PreCompact` fires when compaction starts — too late to be the main trigger,
which is why the thresholds above exist, but exactly right as a backstop for
the case where the 80% stop was ignored.

Two things make that backstop real rather than decorative, and both are in
`context-guard.py`. In the very case it is for, the session already holds a
`stop` marker (the stop fired and was ignored), so the ordinary once-per-
session check would return silently — under `PreCompact` the guard therefore
bypasses the markers and leaves none behind. And `PreCompact` does not consume
`hookSpecificOutput.additionalContext`, so it emits `systemMessage` instead.
The text is written for a session that is already compacting: it reports that
compaction started with no handoff and tells the resumed session to invoke the
handoff skill first, rather than asking for a stop that can no longer happen.

```json
{
  "hooks": {
    "PreCompact": [
      { "hooks": [ { "type": "command",
        "command": "bash /path/to/agent-skills/skills/handoff/scripts/context-guard.sh" } ] }
    ]
  }
}
```

### A note on the gate's credential check

`handoff-gate.py`'s generic credential check is a **heuristic**, not a secret
scanner: any `label = value` whose label CONTAINS one of the credential words
and whose value isn't an obvious placeholder trips it — including a benign
identifier whose label happens to contain one of those words, such as `trace_token: 8f14e45f-...` (a real UUID
correlation id, not a secret). The gate's own failure message says so. A
`GATE: FAIL` on this check is a list of things to look at, not proof of a
leak — if the flagged line isn't actually a secret, rename the label or
remove the line rather than treating the failure as a false negative in the
gate.

**Where the label list lives.** `GENERIC_CRED_KEYWORDS` in `handoff-gate.py`
is the single source: the regex is built from it and the failure message's
"rename the label so it does not contain ..." text is generated from it, so
the two cannot drift. At the time of writing it holds `password`, `passwd`, `pwd`,
`passphrase`, `secret`, `credential`, `bearer`, `token`, `cookie`,
`authorization`, `auth`, `api_key`, `access_key`, `private_key` and
`session_key` — but read the constant or a `GATE: FAIL` line, not this sentence, which is
the kind of second copy that was wrong for four review rounds running. An
HTTP scheme word before the value (`Authorization: Basic <value>`) is matched
separately, so the credential after it is what gets length-checked and
placeholder-checked.

### What else the gate checks, and what it deliberately does not

**Paths.** A path the document names must exist, or the handoff sends its
reader somewhere that isn't there. An absolute or `~`-rooted path is checked
as written. A backtick-quoted **relative** path — the shape the skill itself
asks for, since authors are told to reference specs and plans by path instead
of restating them — is resolved against *both* the handoff document's own
directory and the current working directory, and only fails if it exists in
neither; the failure names both places that were searched, so a typo is
distinguishable from a path that is real but lives somewhere else. To keep
false positives down, a relative candidate counts as a path only if it
contains a `/` and either ends in `/` or carries a file extension: a branch
name in backticks (`feat/handoff-three-modes`) is left alone.

**Archives.** A zip (`.zip`, and the zip containers `.jar`/`.whl`/`.egg`,
matched case-insensitively) is checked by **entry name only** and is never
read as text and never extracted. Extracting is how a gate gets made to write
outside its directory; reading the compressed bytes as UTF-8 would pull an
arbitrarily large bundle into memory and match the credential patterns against
compression noise. So a zip fails the gate when it carries an entry named
`.env`, `.npmrc`, `.pypirc`, `id_rsa` or `id_ed25519` — and the contents of
the files inside it are the author's responsibility, not the gate's.

Any **other** archive — `.tar`, `.tar.gz`, `.tgz`, `.tar.bz2`, `.tar.xz`,
`.gz`, `.bz2`, `.xz`, `.7z`, `.rar` — **fails the gate outright**, with a
finding that says its contents could not be verified and names the two ways
out: repackage it as a `.zip`, or drop it from the handoff. This used to be an
unchecked pass, and briefly a hole: once archives were excluded from the text
scan, a `.tar.gz` (or an uppercase `WORKSPACE.ZIP`) was examined by neither
path and could carry a `.env` straight through. One predicate now governs both
sides — whatever is excluded from the text scan gets archive treatment, and
archive treatment is either an entry-name read or a refusal.

**Its own output.** Every `GATE: FAIL` line is passed through a redaction
step before printing: any substring matching one of the specific credential
patterns, or the value half of a generic `label = value` match, is replaced
with `[redacted]`. The gate echoes material drawn from the files it scans —
a path candidate is repeated verbatim — and a path or a filename can itself
contain a credential. Printing it would write the secret into the terminal
scrollback, the CI log and the session transcript, which is the exact leak
this gate exists to stop.

**The mode's named file.** `quota` and `cross-workspace` mode require a
`PROMPT.txt`, and the requirement is a **non-empty regular file**, not merely
a name that exists: an empty `PROMPT.txt`, or a directory called `PROMPT.txt`,
fails, and the message says which of the three it is (missing, not a regular
file, or empty) so the writer knows whether to create it, replace it or fill
it in. A paste-ready prompt is the one artifact those two modes exist to
produce; a `GATE: PASS` over a zero-byte file would be the gate certifying
its absence.

**What to point the gate at.** The single-file modes (`compaction`,
`same-workspace`) are gated by the **document's own path**; the bundle modes
(`cross-workspace`, `quota`) are gated by the **bundle directory**. This is
not interchangeable, because the skill's storage layout puts single-file
handoffs at `~/.claude/handoffs/<project-slug>/handoff-<slug>-<date>-<HHMM>.md`
— one reused project directory holding many handoffs and no `HANDOFF.md`. A
directory target there finds nothing and, if an older bundle happens to be in
the same directory, scans handoffs that are not the one being delivered. When
the gate is handed a directory with no `HANDOFF.md` it now says which of the
two calls to make instead of only reporting the missing file.
