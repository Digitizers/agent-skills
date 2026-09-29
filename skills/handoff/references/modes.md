# handoff — modes

The document contract in `SKILL.md` is the whole document. This file is what
each mode adds, and why.

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

Another agent, same machine. Add:

- **Uncommitted work** — which files are dirty and why they are not committed.
- **Unwritten conventions** — what the user corrected you on that is not in
  `CLAUDE.md`. This is the knowledge that dies with the session.

Also a single file.

## cross-workspace

Another machine or another checkout. Add a **Setup** section:

- Repository, branch, and the **exact sha** the work is based on.
- Where to put it and any per-machine paths.
- Environment variable **names** (never values) and where they come from.
- Required MCP servers, plugins, and skills.
- One command that must pass before the agent touches anything — the proof
  the setup worked.

Delivered as a directory with `PROMPT.txt`. The gate requires the file to
exist for this mode, the same as for `quota` — a directory delivery always
comes with a paste-ready prompt, because the reader is starting cold.

## quota

The 5-hour window or the weekly limit is running out. Everything from the
mode that otherwise applies, plus:

- **When the window resets** — from the guard's message. This is what decides
  between waiting and switching accounts.
- **`PROMPT.txt`** — paste-ready, self-contained.

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
