# trigger-eval

Measures whether a skill's `description` actually makes an agent load it.

A skill that never triggers is indistinguishable from a skill that doesn't
exist — and you won't notice, because nothing errors. This is the silent
failure mode of skill authoring, and it's separate from the one the
`evals/evals.json` behavioral suites cover:

| Failure | Symptom | Caught by |
|---|---|---|
| Description doesn't fire | Skill is never loaded; agent answers without it | `trigger-eval` (this tool) |
| Body is wrong | Skill loads, then behaves incorrectly | `<skill>/evals/evals.json` |

Both matter. A perfect body behind a description that never fires is dead code.

## Run it

```bash
python3 tools/trigger-eval/trigger_eval.py \
    --skill gha-optimizer \
    --eval-set gha-optimizer/evals/triggers.json \
    --git   # gha-optimizer declares a git/GitHub precondition — see --git below
```

Exits non-zero if any case fails, so it drops into CI as-is.

`--git` is in the example on purpose: `gha-optimizer`'s `compatibility` requires a
git repo with a GitHub remote, and per the flag table a preconditioned skill must
be probed with `--git` or you measure the empty fixture, not the description.

| Flag | Default | Why |
|---|---|---|
| `--runs` | 5 | Triggering is stochastic. See the variance note below. |
| `--threshold` | 0.5 | Fire rate that counts as a pass. |
| `--timeout` | 90 | Per query. A query that doesn't trigger has no natural end. |
| `--workers` | 6 | Parallel probes. |
| `--git` | off | Probe inside a git repo with a GitHub remote. **Required** for any skill with a git/GitHub precondition — see below. |

## Variance: a single run is a rumor, not a reading

Triggering is stochastic, and **borderline queries have high variance**. A rate
that reads 100% over 3 runs can read 15% over 7 — same query, same description,
same model. Observed on `gha-optimizer`:

| Query | 3 runs | 7 runs |
|---|---|---|
| `"I think our CI builds twice"` | 100% | 14% |
| `"fix the eslint config"` (negative) | 0% | 86% |

Both flipped verdict between run counts. Nothing changed but the sample size.

Consequences worth internalizing:

- **`--runs` defaults to 5, a floor for a read — not a verdict.** Treat a rate
  near the threshold as "unknown", not as a pass or a fail. Re-run a borderline
  case at `--runs 15+` before you touch the description over it.
- **Don't tune against noise.** A "failing" case that flips to passing on the
  next run had no defect to fix — editing the description to chase it just
  spends the character budget on nothing. A real trigger defect is stable
  across run counts (e.g. `pr-first-workflow` firing 0% on marketing copy, every
  time). Confirm stability before you call it a defect.
- **A pass/fail count is a summary, not the data.** `29/30` at `--runs 3` hides
  which cases were 50/50 coin flips. Read the per-query rates, not just the
  total.

## `--git`, and why the fixture can lie

The scratch project is the context the agent judges the description against. If
the skill's description states a precondition the fixture doesn't satisfy, the
agent declines for a reason that has nothing to do with the description, and
the harness reports a description defect that isn't there.

This is not hypothetical — it happened on the first run of this tool:

| Skill | Bare fixture | `--git` fixture |
|---|---|---|
| `codex-review-loop` | `"run the review loop on this PR"` fired **33%** | **100%** |
| `pr-first-workflow` | `"fix the typo and commit it"` fired **0%** | **100%** |

Both descriptions were fine. The probe was running in an empty directory with
no repo, so "this PR" referred to nothing. Acting on that first result would
have meant rewriting two healthy descriptions to satisfy a broken fixture.

The tell was a correlation: the two failing skills were exactly the two that
name git/GitHub in their preconditions, while the skills with no repo
dependency scored clean. **If a skill states a precondition, satisfy it in the
fixture or you are measuring the fixture.**

Each probe spawns a real `claude -p` subprocess, so a full run costs quota.
A 6-query set at `--runs 3` is 18 invocations.

## How it works

For each query it builds a throwaway project containing *only* the skill under
test, runs `claude -p <query>` there, and watches the streamed events for the
agent reaching for that skill — either a `Skill` tool call or a direct `Read`
of its `SKILL.md`. Both count: reading the file is loading the skill by another
route. The subprocess is killed the moment a trigger appears, since the
question is whether the agent *decided* to load the skill, not what it does
afterwards. That also keeps the spawned agent from doing real work.

Isolating the skill is deliberate. Testing it alongside its neighbours would
measure the routing contest between them, not this description's own pulling
power — worth measuring, but a different question.

Isolation takes **two** mechanisms, because a temp project alone is not enough:

1. The throwaway project holds only the skill under test — this drops other
   *project* skills.
2. `--setting-sources project` is passed to every `claude -p` — this drops the
   developer's *personal* skills (`~/.claude/skills/`), which otherwise load in
   every session regardless of the working directory and compete for routing.

Without (2) the probe silently measures "does this description win against
everything I happen to have installed" — non-reproducible, and a source of false
negatives when a same-domain personal skill grabs the query. With it, the tool
measures what this file claims: the description's pulling power in isolation.
This mattered in practice — several `gha-optimizer` negative cases that "failed"
against a full session passed once the session was isolated; the interference
was other skills, not this description.

## Writing an eval set

A JSON list of `{"query", "should_trigger"}`.

```json
[
  { "query": "our GitHub Actions bill jumped to $180 and I don't know why", "should_trigger": true },
  { "query": "add a workflow that runs vitest on every PR", "should_trigger": false }
]
```

Two rules earn their keep:

**Write the positive cases the way a user actually talks.** If every positive
query names the skill, you've tested nothing — the name always matches itself.
The cases that matter describe the *problem* and never name the tool.

**Negative cases are not filler.** A description that fires on everything is as
broken as one that never fires, and it's the more expensive failure: it drags
irrelevant instructions into context on unrelated work. The negatives worth
writing are near-misses — same domain, wrong intent. `gha-optimizer` is a
read-only audit skill, so "add a workflow that runs vitest" belongs in the
negative set: same subject, opposite verb.
