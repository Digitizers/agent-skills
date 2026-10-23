# Codex shadow evaluation

Run from this repository with an installed, authenticated Codex CLI:

```bash
python3 tools/codex-eval/run.py \
  --spec tools/codex-eval/cases.json --runs 1 \
  --output /path/to/new-report.json
```

Use `--model` to pin a model when comparing runs. Without it, the report
explicitly records the CLI default, not an inferred model identity. Model
calls are manual and bounded (1-20 runs/case, timeout at most 300 seconds).
One run is a smoke check, not a stochastic reliability estimate.

The runner ignores user configuration, disables host skill discovery and
interactive tools, and uses an ephemeral read-only session. It retains the
CLI's existing auth lookup; it never reads, copies or prints credentials.
No Claude probes are run. An unsupported CLI flag, timeout, failed turn,
unknown tool event or missing final answer aborts without a report. CI uses
fake CLI events; no model quota is spent there.

Fixtures have schema_version 1 and a cases array. Each case has a unique id,
a prompt, explicit local Markdown file paths relative to the fixture, and a
nonempty expected object. The final JSON answer must contain those exact
fields/values; expectations are not included in the model prompt.
Only put approved, nonsecret policy/scenarios in fixtures. These are shadow
decision probes, not instructions to execute a production or client task.

Reports identify backend, mode, CLI version, requested model, fixture hash,
input hashes, prompt hashes and instrument hash. Each is new and independent:
no carry-forward, overwrite, cross-backend comparison or certification of
Claude/OpenClaw/Hermes results. Changed input during a run withholds results.
Keep private cross-repository reports outside this public repository.

Native activation must be verified separately in a fresh host session, using
its displayed skill inventory and observable skill loading; a simulated
routing answer cannot prove that discovery or activation worked.
