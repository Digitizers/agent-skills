# handoff — modes

The document contract in `SKILL.md` is the whole document. This file is what
each mode adds, and why.

**The modes are exclusive, not cumulative.** Each section below is a
self-contained requirement, not an increment on the section above it —
`quota` does not inherit `cross-workspace`'s Setup section, even though both
sections' full content overlaps in practice (see "Both apply at once" under
quota). `handoff-gate.py --mode <mode>` checks only the block for the mode
you name: `--mode quota` never checks for Setup, and `--mode cross-workspace`
never checks for `PROMPT.txt`'s window-reset framing. If a handoff needs more
than one mode's guarantees, write the union of their content and run the gate
once per mode — see "Both apply at once" below.

## Tried and rejected (every mode)

A list of approaches already attempted and the evidence that killed each one.

This is the first thing compaction destroys and the reason a fresh agent
walks into the same wall an hour later. One line per attempt: what was tried,
what happened, and the artifact that proves it (a failing test name, an error
message, a command's output).

Bad: "tried a few caching approaches".
Good: "In-memory cache per worker — rejected: four workers, so a purge on one
leaves three stale; reproduced with `tests/test_purge.py::test_multi_worker`."

## compaction

The reader is this same agent after its memory is gone, on the same machine
with the same tools. Setup instructions would be noise. What it needs:

- **Mid-action state** — the file open, the command half-run, the PR mid-review.
- **Constraints verbatim** — what the user said, in their words. Paraphrase
  loses the constraint that was not obviously a constraint at the time.
- **A verification command** that re-establishes where things stand, e.g.
  `git -C <repo> status --short && git -C <repo> log --oneline -3`.

Delivered as a single file. There is no one to paste a prompt to.

## same-workspace

Another agent, same machine. This mode requires:

- **Mid-action state, constraints verbatim, Tried and rejected, a
  verification command** — the same content `compaction` requires; another
  agent on this machine still needs to know what was mid-flight and why, it
  just doesn't need Setup since the workspace and tools are already here.
- **Uncommitted work** — which files are dirty and why they are not committed.
- **Unwritten conventions** — what the user corrected you on that is not in
  `CLAUDE.md`. This is the knowledge that dies with the session.

Also a single file. `handoff-gate.py`'s `MODE_SECTIONS["same-workspace"]`
checks only for "Tried and rejected" — Uncommitted work and Unwritten
conventions are content the mode calls for, not headings the gate enforces.

## cross-workspace

Another machine or another checkout. This mode requires:

- **Mid-action state, constraints verbatim, Tried and rejected, a
  verification command, what is uncommitted, unwritten conventions** — the
  same content `same-workspace` requires; the reader on another machine still
  needs all of it, plus the one thing a same-machine reader doesn't:
- **Setup**, its own required section:
  - Repository, branch, and the **exact sha** the work is based on.
  - Where to put it and any per-machine paths.
  - Environment variable **names** (never values) and where they come from.
  - Required MCP servers, plugins, and skills.
  - One command that must pass before the agent touches anything — the proof
    the setup worked.

Delivered as a directory with `PROMPT.txt`, gated with `--mode
cross-workspace`. `MODE_SECTIONS["cross-workspace"]` checks for both "Tried
and rejected" and "Setup" — this is the only mode that gates on Setup.
`MODE_FILES` also requires `PROMPT.txt` to exist for this mode, the same as
for `quota` — a directory delivery always comes with a paste-ready prompt,
because the reader is starting cold.

## quota

The 5-hour window or the weekly limit is running out. This mode requires:

- **Mid-action state, constraints verbatim, Tried and rejected, a
  verification command** — the same content `compaction` requires; a quota
  cutoff destroys the session's memory exactly like compaction does.
- **When the window resets** — from the guard's message. This is what decides
  between waiting and switching accounts.
- **`PROMPT.txt`** — paste-ready, self-contained.

Delivered as a directory with `PROMPT.txt`, gated with `--mode quota`. This
mode does **not** require a Setup section — `handoff-gate.py`'s
`MODE_SECTIONS["quota"]` checks only for "Tried and rejected". If the reader
is also on another machine, see "Both apply at once" next.

### Both apply at once: quota-driven and bound for another machine

The common real case: the quota is running out *and* the next session will
run on a different machine (a fresh account, a different box). Neither mode
alone covers this reader — `quota` never checks for Setup, and
`cross-workspace` never requires `PROMPT.txt` or the window-reset line. Write
one document and directory that carries **both** sets of content — the
cross-workspace Setup section *and* the quota `PROMPT.txt` plus reset time —
then run the gate twice:

```
python3 <skill>/scripts/handoff-gate.py <dir> --mode cross-workspace
python3 <skill>/scripts/handoff-gate.py <dir> --mode quota
```

Both must print `GATE: PASS`. Neither run checks the other mode's
requirements, so one passing run is not evidence the document is complete —
only both are.

### PROMPT.txt template

```
Continue the work described in HANDOFF.md in this directory.

Read HANDOFF.md first. Do not start working: confirm the state matches what
it describes by running the verification command in it, tell me what you
found, and wait.

Repository: <repo> @ <branch> (<sha>)
Handoff: <absolute path to HANDOFF.md>
Stopped because: the <5-hour window|weekly limit> reached <N>%; it resets at <time>.
Next step when I say go: <step 1 from What to do next>
```

Nothing in `PROMPT.txt` may contain a credential value. The gate enforces it
the same way it scans every other file in the handoff — see the heuristic
note in `SKILL.md`'s Delivery section before treating a `GATE: FAIL` on this
file as proof of a real leak.

## The zip

Only when the user asks. It carries work that is not in git and not
reproducible — scratch files, uncommitted edits in a temporary workspace,
generated artifacts that cost real time. Never `.env`, credentials,
`node_modules`, or anything the repository already holds.
