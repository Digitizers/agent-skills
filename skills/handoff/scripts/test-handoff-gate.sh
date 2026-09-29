#!/usr/bin/env bash
# Regression tests for handoff-gate.py.
set -euo pipefail
GATE="$(cd "$(dirname "$0")" && pwd)/handoff-gate.py"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
fail() { echo "FAIL: $1" >&2; exit 1; }

mkgood() { # $1=dir
  mkdir -p "$1"
  cat > "$1/HANDOFF.md" <<EOF
# Handoff — demo (2026-09-29)

## Project overview
A demo project.

### Tools
- pytest — \`pytest -q\`

## Details
Conventions live in $WORK/conventions.md

## Suggested skills
- pr-first-workflow — before landing anything

## Current state
Tests pass.

## Tried and rejected
- Patching the caller: the bug is in the callee.

## Open issues
None.

## What to do next
1. Run the suite.
2. Open the PR.
EOF
  echo "conventions" > "$WORK/conventions.md"
}

# 1. A complete document passes
mkgood "$WORK/good"
OUT="$(python3 "$GATE" "$WORK/good" --mode compaction)" || fail "valid handoff rejected: $OUT"
echo "$OUT" | grep -q "GATE: PASS" || fail "no PASS line"
echo "PASS valid handoff"

# 2. An empty section fails
mkgood "$WORK/empty"
python3 - "$WORK/empty/HANDOFF.md" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read().replace("Tests pass.", "")
open(p, "w").write(s)
PY
python3 "$GATE" "$WORK/empty" --mode compaction && fail "empty section passed"
echo "PASS empty section fails"

# 3. Prose next steps fail (Review Focus 3)
mkgood "$WORK/prose"
python3 - "$WORK/prose/HANDOFF.md" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read().replace("1. Run the suite.\n2. Open the PR.", "Run the suite and open the PR.")
open(p, "w").write(s)
PY
python3 "$GATE" "$WORK/prose" --mode compaction && fail "unnumbered next steps passed"
echo "PASS prose next steps fail"

# 4. A path named in the document that does not exist fails
mkgood "$WORK/badpath"
python3 - "$WORK/badpath/HANDOFF.md" "$WORK" <<'PY'
import sys
p, work = sys.argv[1], sys.argv[2]
s = open(p).read().replace(f"{work}/conventions.md", f"{work}/does-not-exist.md")
open(p, "w").write(s)
PY
python3 "$GATE" "$WORK/badpath" --mode compaction && fail "nonexistent path passed"
echo "PASS missing path fails"

# 5. A secret fails — and is caught in PROMPT.txt, not just HANDOFF.md
#    (Review Focus 4). The token below is a syntactically valid fake.
mkgood "$WORK/secret"
printf 'Continue the work. Use sk-ant-api03-%s\n' "$(python3 -c 'print("A"*95)')" > "$WORK/secret/PROMPT.txt"
OUT="$(python3 "$GATE" "$WORK/secret" --mode quota || true)"
echo "$OUT" | grep -q "PROMPT.txt" || fail "secret in PROMPT.txt not reported: $OUT"
python3 "$GATE" "$WORK/secret" --mode quota && fail "secret passed the gate"
echo "PASS secret in PROMPT.txt fails"

# 6. Mode-specific required block: quota mode needs PROMPT.txt
mkgood "$WORK/noprompt"
python3 "$GATE" "$WORK/noprompt" --mode quota && fail "quota mode passed without PROMPT.txt"
echo "PASS quota mode requires PROMPT.txt"

# 7. A zip is scanned by name list: an .env inside it fails the gate.
#    Built with Python's zipfile module (not the `zip` CLI) so the suite
#    runs on a minimal CI image where `zip` may not be installed.
mkgood "$WORK/zip"
mkdir -p "$WORK/zip"
python3 - "$WORK/zip/workspace.zip" <<'PY'
import zipfile, sys
with zipfile.ZipFile(sys.argv[1], "w") as zf:
    zf.writestr(".env", "KEY=value\n")
PY
python3 "$GATE" "$WORK/zip" --mode compaction && fail "zip containing .env passed"
echo "PASS zip with .env fails"

# 8. Cross-workspace mode needs a setup block
mkgood "$WORK/nosetup"
echo "paste me" > "$WORK/nosetup/PROMPT.txt"
python3 "$GATE" "$WORK/nosetup" --mode cross-workspace && fail "cross-workspace passed without setup"
echo "PASS cross-workspace requires setup"

# 9. Fix-round-1 finding 1: a bare URL in prose is not mistaken for a path.
#    "https://github.com/org/repo/pull/9" begins a bare-path-looking match
#    right after the "https:" scheme unless URLs are masked out first.
mkgood "$WORK/url"
python3 - "$WORK/url/HANDOFF.md" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read().replace(
    "## Details\n",
    "## Details\nSee https://github.com/org/repo/pull/9 for the discussion.\n",
    1,
)
open(p, "w").write(s)
PY
OUT="$(python3 "$GATE" "$WORK/url" --mode compaction)" || fail "PR URL in prose rejected as a path: $OUT"
echo "$OUT" | grep -q "GATE: PASS" || fail "no PASS line for URL-in-prose case"
echo "PASS bare URL in prose is not treated as a path"

# 10. Fix-round-1 finding 2: redacted placeholders the skill itself tells the
#     writer to leave behind must not fail the gate.
mkgood "$WORK/placeholders"
python3 - "$WORK/placeholders/HANDOFF.md" <<'PY'
import sys
p = sys.argv[1]
lines = "\n".join([
    "BUNNY_API_KEY=REDACTED_DO_NOT_COMMIT",
    "TOKEN=your-key-here",
    "PASSWORD=<paste-here>",
    "SECRET=xxxxxxxxxxxx",
])
s = open(p).read().replace("## Details\n", f"## Details\n{lines}\n", 1)
open(p, "w").write(s)
PY
OUT="$(python3 "$GATE" "$WORK/placeholders" --mode compaction)" || fail "placeholder credentials rejected: $OUT"
echo "$OUT" | grep -q "GATE: PASS" || fail "no PASS line for placeholder credentials"
echo "PASS placeholder credential values pass the gate"

# 11. Fix-round-1 finding 2, negative case: a real-looking secret behind a
#     prefixed label must still fail — the placeholder allowlist must not
#     soften the specific secret patterns.
mkgood "$WORK/realcred"
python3 - "$WORK/realcred/HANDOFF.md" <<'PY'
import sys
p = sys.argv[1]
value = "sk-ant-api03-" + "A" * 95
s = open(p).read().replace("## Details\n", f"## Details\nBUNNY_API_KEY={value}\n", 1)
open(p, "w").write(s)
PY
python3 "$GATE" "$WORK/realcred" --mode compaction && fail "real secret behind a prefixed label passed"
echo "PASS real secret behind a prefixed label still fails"

# 12. Fix-round-1 finding 3: a file the gate cannot read must be reported,
#     not silently skipped — an unreadable file is indistinguishable from a
#     clean one otherwise. chmod 000 does not block root, so skip there.
if [ "$(id -u)" -eq 0 ]; then
  echo "SKIP unreadable file is reported (running as root, chmod 000 has no effect)"
else
  mkgood "$WORK/unreadable"
  echo "secret content irrelevant" > "$WORK/unreadable/locked.md"
  chmod 000 "$WORK/unreadable/locked.md"
  OUT="$(python3 "$GATE" "$WORK/unreadable" --mode compaction)" && RC=0 || RC=$?
  # rm -rf can still remove an unreadable file given write access to its
  # parent directory, so no need to restore permissions before the EXIT trap.
  [ "${RC:-0}" -ne 0 ] || fail "unreadable file passed the gate"
  echo "$OUT" | grep -q "locked.md" || fail "unreadable file not reported: $OUT"
  echo "PASS unreadable file is reported, not skipped"
fi

# 13. Fix-round-1 finding 4: every file in the handoff directory is scanned,
#     recursively — a secret in a nested subdirectory must fail the gate too.
mkgood "$WORK/nested"
mkdir -p "$WORK/nested/context"
printf 'sk-ant-api03-%s\n' "$(python3 -c 'print("A"*95)')" > "$WORK/nested/context/notes.md"
python3 "$GATE" "$WORK/nested" --mode compaction && fail "secret in a nested subdirectory passed"
echo "PASS secret in a nested subdirectory fails"

# 14. Fix-round-2 finding 2, THE BYPASS this round exists to close: round 1's
#     PLACEHOLDER_RX ended every alternative in `\S*`, so a real secret that
#     merely STARTS with an allowlisted word (here "your") matched
#     your[-_]?\S* in full and the gate printed GATE: PASS on a leaked prod
#     DB password. A placeholder must be recognised as a whole value, not a
#     prefix — this pins that regression.
mkgood "$WORK/prefixbypass"
python3 - "$WORK/prefixbypass/HANDOFF.md" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read().replace(
    "## Details\n",
    "## Details\n" + "TO" + "KEN=" + "your-actual-prod-db-" + "password-Xk29fLp3Q7vZ" + "\n",
    1,
)
open(p, "w").write(s)
PY
python3 "$GATE" "$WORK/prefixbypass" --mode compaction && fail "REGRESSION: a real secret prefixed with an allowlisted word (your-...) bypassed the gate"
echo "PASS real secret prefixed with an allowlisted word still fails"

# 15. Fix-round-2 finding 2, a second prefix-bypass shape: a real secret that
#     starts with a genuine redaction word (REDACTED) but is not made ENTIRELY
#     of redaction words must still fail.
mkgood "$WORK/wordprefixbypass"
python3 - "$WORK/wordprefixbypass/HANDOFF.md" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read().replace(
    "## Details\n",
    "## Details\n" + "SEC" + "RET=" + "REDACTED_BUT_ALSO_" + "hunter2SuperRealValue" + "\n",
    1,
)
open(p, "w").write(s)
PY
python3 "$GATE" "$WORK/wordprefixbypass" --mode compaction && fail "REGRESSION: a real secret with a redaction-word prefix bypassed the gate"
echo "PASS real secret with a redaction-word prefix still fails"

# 16. Fix-round-3, THE PREFIXED-LABEL REGRESSION this round exists to close:
#     GENERIC_CRED_RX required a `\b` immediately before the keyword, so
#     `BUNNY_API_KEY=...` was invisible to the scan — `_` is a word character,
#     so there is no boundary between it and `API`. Every credential name in
#     this project's own toolbox is `<VENDOR>_<THING>_KEY` or
#     `<VENDOR>_TOKEN`, so this was the single most likely real leak shape.
mkgood "$WORK/prefixedlabel"
python3 - "$WORK/prefixedlabel/HANDOFF.md" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read().replace(
    "## Details\n",
    "## Details\n" + "BUNNY_API" + "_KEY=" + "7fd93ba21c0e" + "4b8aa1c2" + "\n",
    1,
)
open(p, "w").write(s)
PY
python3 "$GATE" "$WORK/prefixedlabel" --mode compaction && fail "REGRESSION: BUNNY_API_KEY=<real value> bypassed the gate"
echo "PASS prefixed label (BUNNY_API_KEY=) with a real value still fails"

# 17. Fix-round-3: a second prefixed-label shape, different vendor/shape.
mkgood "$WORK/prefixedlabel2"
python3 - "$WORK/prefixedlabel2/HANDOFF.md" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read().replace(
    "## Details\n",
    "## Details\n" + "SUMIT_MAIN_API" + "_KEY=" + "9d0e1f2a3b4c" + "5d6e7f80" + "\n",
    1,
)
open(p, "w").write(s)
PY
python3 "$GATE" "$WORK/prefixedlabel2" --mode compaction && fail "REGRESSION: SUMIT_MAIN_API_KEY=<real value> bypassed the gate"
echo "PASS prefixed label (SUMIT_MAIN_API_KEY=) with a real value still fails"

# 18. Fix-round-3: a prefixed TOKEN label, not just *_KEY shapes.
mkgood "$WORK/prefixedlabel3"
python3 - "$WORK/prefixedlabel3/HANDOFF.md" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read().replace(
    "## Details\n",
    "## Details\n" + "MY_SERVICE_TO" + "KEN=" + "abcdef012345" + "6789abcd" + "\n",
    1,
)
open(p, "w").write(s)
PY
python3 "$GATE" "$WORK/prefixedlabel3" --mode compaction && fail "REGRESSION: MY_SERVICE_TOKEN=<real value> bypassed the gate"
echo "PASS prefixed label (MY_SERVICE_TOKEN=) with a real value still fails"

# 19. Fix-round-3: the placeholder allowlist must survive the prefix change —
#     BUNNY_API_KEY=REDACTED_DO_NOT_COMMIT is now visible to the generic scan
#     (unlike before round 3) and must still pass via the placeholder check.
mkgood "$WORK/prefixedplaceholder"
python3 - "$WORK/prefixedplaceholder/HANDOFF.md" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read().replace(
    "## Details\n",
    "## Details\nBUNNY_API_KEY=REDACTED_DO_NOT_COMMIT\n",
    1,
)
open(p, "w").write(s)
PY
OUT="$(python3 "$GATE" "$WORK/prefixedplaceholder" --mode compaction)" || fail "BUNNY_API_KEY=REDACTED_DO_NOT_COMMIT rejected: $OUT"
echo "$OUT" | grep -q "GATE: PASS" || fail "no PASS line for BUNNY_API_KEY=REDACTED_DO_NOT_COMMIT"
echo "PASS prefixed label with a placeholder value still passes"

# 20. Fix-round-3: prose that names a credential's label with no value must
#     not be flagged — there is no assignment for the scan to key off of.
mkgood "$WORK/prosewithlabel"
python3 - "$WORK/prosewithlabel/HANDOFF.md" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read().replace(
    "## Details\n",
    "## Details\nConfigure BUNNY_API_KEY before running the site sync.\n",
    1,
)
open(p, "w").write(s)
PY
OUT="$(python3 "$GATE" "$WORK/prosewithlabel" --mode compaction)" || fail "prose naming a credential label rejected: $OUT"
echo "$OUT" | grep -q "GATE: PASS" || fail "no PASS line for prose naming a credential label"
echo "PASS prose naming a credential label with no value still passes"

# 21. Fix-round-3: a short, non-credential-shaped value after "secret:" must
#     not be flagged — the widened prefix must not turn this into prose-wide
#     matching.
mkgood "$WORK/shortvalue"
python3 - "$WORK/shortvalue/HANDOFF.md" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read().replace(
    "## Details\n",
    "## Details\nsecret: the build is slow\n",
    1,
)
open(p, "w").write(s)
PY
OUT="$(python3 "$GATE" "$WORK/shortvalue" --mode compaction)" || fail "short non-credential value after 'secret:' rejected: $OUT"
echo "$OUT" | grep -q "GATE: PASS" || fail "no PASS line for 'secret: the build is slow'"
echo "PASS short non-credential-shaped value after 'secret:' still passes"

# 22. Fix-round-4: the known false positive from round 3's prefix widening
#     (a benign correlation id whose label ends in "token") still fails the
#     gate on purpose — the coordinator ruled the mechanism stays best-effort
#     rather than punching an exemption hole for UUID/hex/base64 shapes — but
#     the failure line must now say so and name both ways out, so this is a
#     documented, known behaviour rather than a silent trap for the writer.
mkgood "$WORK/falsepositive"
python3 - "$WORK/falsepositive/HANDOFF.md" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read().replace(
    "## Details\n",
    "## Details\n" + "trace_to" + "ken: " + "3fa85f64-5717-4562" + "-b3fc-2c963f66afa6" + "\n",
    1,
)
open(p, "w").write(s)
PY
OUT="$(python3 "$GATE" "$WORK/falsepositive" --mode compaction || true)"
python3 "$GATE" "$WORK/falsepositive" --mode compaction && fail "trace_token correlation id passed the gate (should still fail — known, documented behaviour)"
echo "$OUT" | grep -q "heuristic" || fail "generic finding does not name itself a heuristic: $OUT"
echo "$OUT" | grep -q "redact the value" || fail "generic finding does not mention redacting: $OUT"
echo "$OUT" | grep -qE "remove it|rename the label" || fail "generic finding does not name the non-credential way out: $OUT"
echo "PASS documented false positive (trace_token: <uuid>) still fails, with guidance"

# 23. Fix-round-4: a real sk-ant- value must fail with the SPECIFIC-pattern
#     wording ("Anthropic API key found in ..."), not the heuristic wording —
#     a reader must be able to tell "this is definitely a key" from "this
#     looks like an assignment" at a glance.
mkgood "$WORK/specificwording"
python3 - "$WORK/specificwording/HANDOFF.md" "sk-ant-api03-$(python3 -c 'print("A"*95)')" <<'PY'
import sys
p, value = sys.argv[1], sys.argv[2]
s = open(p).read().replace("## Details\n", f"## Details\n{value}\n", 1)
open(p, "w").write(s)
PY
OUT="$(python3 "$GATE" "$WORK/specificwording" --mode compaction || true)"
python3 "$GATE" "$WORK/specificwording" --mode compaction && fail "real sk-ant- secret passed the gate"
echo "$OUT" | grep -q "Anthropic API key found in" || fail "specific-pattern wording missing: $OUT"
echo "$OUT" | grep -q "heuristic" && fail "specific pattern must not use heuristic wording: $OUT"
echo "PASS real sk-ant- secret fails with specific-pattern wording, not heuristic wording"

# 24. Fix-round-5 / C1 attack 1: a credential in a MARKDOWN TABLE CELL. The
#     generic scan cannot see it — `|` is not an assignment character — so
#     this is what the new specific Stripe prefix is for, and it must be
#     reported with the specific wording, not as a heuristic.
mkgood "$WORK/c1stripe"
python3 - "$WORK/c1stripe/HANDOFF.md" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read().replace(
    "## Details\n",
    "## Details\n| STRIPE_SEC" + "RET_KEY | " + "sk_" + "live_" + "51H8xQ2KZvNqRtYbW3pLmD" + " |\n",
    1,
)
open(p, "w").write(s)
PY
OUT="$(python3 "$GATE" "$WORK/c1stripe" --mode compaction || true)"
python3 "$GATE" "$WORK/c1stripe" --mode compaction && fail "REGRESSION (C1.1): a Stripe live key in a table cell passed the gate"
echo "$OUT" | grep -q "Stripe live secret key found in" || fail "Stripe key not reported with specific wording: $OUT"
echo "$OUT" | grep -q "heuristic" && fail "a specific pattern must not call itself a heuristic: $OUT"
echo "PASS C1.1 Stripe key in a markdown table cell fails"

# 25. C1 attack 2: a password whose punctuation used to truncate the captured
#     value below the 4-character minimum, so the whole line vanished.
mkgood "$WORK/c1punct"
python3 - "$WORK/c1punct/HANDOFF.md" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read().replace(
    "## Details\n",
    "## Details\n" + "DB_PASS" + "WORD=" + "p@ssw0rd!" + "Xy29KqZ" + "\n", 1)
open(p, "w").write(s)
PY
python3 "$GATE" "$WORK/c1punct" --mode compaction && fail "REGRESSION (C1.2): a punctuation-bearing password passed the gate"
echo "PASS C1.2 punctuation-bearing password fails"

# 26. C1 attack 3: the keyword used to have to be the label's TAIL, so
#     `AWS_SECRET_ACCESS_KEY`, `*_CREDENTIALS`, `*_AUTH` and `*_PWD` were
#     invisible to the scan.
mkgood "$WORK/c1midlabel"
python3 - "$WORK/c1midlabel/HANDOFF.md" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read().replace(
    "## Details\n",
    "## Details\n" + "AWS_SEC" + "RET_ACCESS_KEY: " + "wJalrUtnFEMIK7MDENGb" + "PxRfiCYEXAMPLEKEY" + "\n",
    1,
)
open(p, "w").write(s)
PY
python3 "$GATE" "$WORK/c1midlabel" --mode compaction && fail "REGRESSION (C1.3): a keyword in the middle of the label bypassed the gate"
echo "PASS C1.3 keyword mid-label (AWS_SECRET_ACCESS_KEY) fails"

# 27. C1 attack 4: `passwd` was not in the keyword alternation at all.
mkgood "$WORK/c1passwd"
python3 - "$WORK/c1passwd/HANDOFF.md" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read().replace(
    "## Details\n",
    "## Details\n" + "DB_PAS" + "SWD=" + "hunter2hunter" + "2hunter" + "\n", 1)
open(p, "w").write(s)
PY
python3 "$GATE" "$WORK/c1passwd" --mode compaction && fail "REGRESSION (C1.4): DB_PASSWD= bypassed the gate"
echo "PASS C1.4 DB_PASSWD= fails"

# 28. I3: pointing the gate at a FILE must scan that file and the mode's named
#     siblings only — not walk the whole parent directory. A handoff document
#     saved in a populated directory (a repo root, or ~) used to make the gate
#     report every secret-shaped string in every neighbouring file.
mkgood "$WORK/populated"
printf 'sk-ant-api03-%s\n' "$(python3 -c 'print("A"*95)')" > "$WORK/populated/unrelated-notes.md"
OUT="$(python3 "$GATE" "$WORK/populated/HANDOFF.md" --mode compaction)" || fail "REGRESSION (I3): a file target scanned its neighbours: $OUT"
echo "$OUT" | grep -q "GATE: PASS" || fail "no PASS line for a file target in a populated directory: $OUT"
echo "$OUT" | grep -q "unrelated-notes.md" && fail "REGRESSION (I3): a neighbouring file was reported"
# ...and the same directory as a DIRECTORY target still catches it.
python3 "$GATE" "$WORK/populated" --mode compaction && fail "a directory target missed a secret in the directory"
echo "PASS I3 a file target scans the file, a directory target scans the tree"

# 29. I3, the other half: a file target still scans the mode's named sibling
#     files — PROMPT.txt is the file most likely to be pasted elsewhere, and
#     narrowing the scan must not stop covering it.
mkgood "$WORK/siblings"
printf 'Continue. Use sk-ant-api03-%s\n' "$(python3 -c 'print("A"*95)')" > "$WORK/siblings/PROMPT.txt"
OUT="$(python3 "$GATE" "$WORK/siblings/HANDOFF.md" --mode quota || true)"
python3 "$GATE" "$WORK/siblings/HANDOFF.md" --mode quota && fail "REGRESSION (I3): a secret in the mode sibling PROMPT.txt was not scanned"
echo "$OUT" | grep -q "PROMPT.txt" || fail "sibling PROMPT.txt not named in the finding: $OUT"
echo "PASS I3 a file target still scans the mode sibling PROMPT.txt"

# 30. Re-review of the I3 fix: narrowing a file target to the document plus
#     MODE_FILES left sibling ARCHIVES unscanned — MODE_FILES lists PROMPT.txt
#     and nothing else — so a cross-workspace bundle validated BY FILE PATH
#     printed GATE: PASS over a workspace.zip holding a .env, while the exact
#     same bundle validated as a directory failed. The zip's entry-name check
#     is the one part of the gate a reader cannot redo by eye, so a file target
#     now picks up sibling *.zip files too.
mkgood "$WORK/bundle"
python3 - "$WORK/bundle/HANDOFF.md" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read().replace(
    "## Current state\n", "## Setup\nClone the repo and install.\n\n## Current state\n", 1)
open(p, "w").write(s)
PY
echo "paste me" > "$WORK/bundle/PROMPT.txt"
python3 - "$WORK/bundle/workspace.zip" <<'PY'
import zipfile, sys
with zipfile.ZipFile(sys.argv[1], "w") as zf:
    zf.writestr(".env", "KEY=value\n")
PY
# The directory target must still catch it (the pre-existing behaviour).
python3 "$GATE" "$WORK/bundle" --mode cross-workspace && fail "directory target missed the .env in workspace.zip"
# ...and so must the FILE target.
OUT="$(python3 "$GATE" "$WORK/bundle/HANDOFF.md" --mode cross-workspace || true)"
python3 "$GATE" "$WORK/bundle/HANDOFF.md" --mode cross-workspace && fail "REGRESSION: a file target skipped the sibling workspace.zip, which holds a .env"
echo "$OUT" | grep -q "workspace.zip" || fail "the finding does not name the sibling archive: $OUT"
echo "$OUT" | grep -q ".env" || fail "the finding does not name the .env entry: $OUT"
echo "PASS a file target checks sibling archives by entry name"

# 31. ...and picking up sibling archives must NOT re-widen the scan: a
#     neighbouring ordinary file in the same directory is still none of the
#     gate's business when a file is the target (I3 stays fixed).
mkgood "$WORK/bundle2"
printf 'sk-ant-api03-%s\n' "$(python3 -c 'print("A"*95)')" > "$WORK/bundle2/unrelated-notes.md"
python3 - "$WORK/bundle2/clean.zip" <<'PY'
import zipfile, sys
with zipfile.ZipFile(sys.argv[1], "w") as zf:
    zf.writestr("src/main.py", "print('hi')\n")
PY
OUT="$(python3 "$GATE" "$WORK/bundle2/HANDOFF.md" --mode compaction)" || fail "a clean sibling archive or a neighbour broke a file target: $OUT"
echo "$OUT" | grep -q "GATE: PASS" || fail "no PASS line: $OUT"
echo "$OUT" | grep -q "unrelated-notes.md" && fail "REGRESSION (I3): a neighbouring ordinary file was scanned again"
echo "PASS sibling archives do not re-widen the scan to ordinary neighbours"

# 32. CodeQL py/clear-text-logging-sensitive-data: the gate echoes content
#     derived from the scanned files — check_paths repeats a matched path
#     candidate verbatim — and a path can itself contain a credential. Every
#     GATE: FAIL line now goes through redact(), so the token must not reach
#     stdout (terminal scrollback, CI log, session transcript).
mkgood "$WORK/logredact"
TOKEN="$(python3 -c 'print("AKIA" + "IOSFODNN7EXAMPLE")')"
python3 - "$WORK/logredact/HANDOFF.md" "/tmp/handoff-gate-missing-$TOKEN/notes.md" <<'PY'
import sys
p, path = sys.argv[1], sys.argv[2]
s = open(p).read().replace(
    "## Details\n", f"## Details\nThe deploy notes live at {path}\n", 1)
open(p, "w").write(s)
PY
OUT="$(python3 "$GATE" "$WORK/logredact" --mode compaction || true)"
python3 "$GATE" "$WORK/logredact" --mode compaction && fail "a credential-bearing missing path passed the gate"
echo "$OUT" | grep -q "path does not exist" || fail "the missing path was not reported at all: $OUT"
echo "$OUT" | grep -q "$TOKEN" && fail "REGRESSION: the credential in the path was printed verbatim to stdout"
echo "$OUT" | grep -q "\[redacted\]" || fail "the credential in the path was not replaced by a marker: $OUT"
echo "PASS credential-shaped token inside a reported path is redacted before printing"

# 33. P2 (relative paths): a relative path that resolves against the CURRENT
#     WORKING DIRECTORY is real and must pass. Until this round every relative
#     path was captured and then silently dropped, so a handoff could cite a
#     spec that does not exist and still print GATE: PASS — and the skill tells
#     authors to reference specs and plans by path rather than restate them, so
#     this is the common citation shape.
mkdir -p "$WORK/cwdrel/docs" "$WORK/cwdrel/hd"
mkgood "$WORK/cwdrel/hd"
echo "the plan" > "$WORK/cwdrel/docs/plan.md"
python3 - "$WORK/cwdrel/hd/HANDOFF.md" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read().replace(
    "## Details\n", "## Details\nThe plan is in `docs/plan.md`.\n", 1)
open(p, "w").write(s)
PY
OUT="$(cd "$WORK/cwdrel" && python3 "$GATE" "$WORK/cwdrel/hd" --mode compaction)" \
  || fail "a relative path that exists relative to cwd was rejected: $OUT"
echo "$OUT" | grep -q "GATE: PASS" || fail "no PASS line for a cwd-relative path: $OUT"
echo "PASS relative path resolving against the working directory passes"

# 34. ...and one that resolves BESIDE THE HANDOFF DOCUMENT passes too: a
#     bundle carries its own `notes/spec.md`, and the reader opens it from the
#     bundle, not from wherever the gate happened to be run.
mkgood "$WORK/beside"
mkdir -p "$WORK/beside/notes"
echo "the spec" > "$WORK/beside/notes/spec.md"
python3 - "$WORK/beside/HANDOFF.md" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read().replace(
    "## Details\n", "## Details\nThe spec ships with this bundle: `notes/spec.md`.\n", 1)
open(p, "w").write(s)
PY
OUT="$(cd "$WORK" && python3 "$GATE" "$WORK/beside" --mode compaction)" \
  || fail "a relative path that exists beside the document was rejected: $OUT"
echo "$OUT" | grep -q "GATE: PASS" || fail "no PASS line for a document-relative path: $OUT"
echo "PASS relative path resolving beside the handoff document passes"

# 35. ...and one that exists in NEITHER place fails, with a message that names
#     both places that were searched — the reader has to be able to tell a
#     typo from a path that is real but lives somewhere else.
mkgood "$WORK/relmissing"
python3 - "$WORK/relmissing/HANDOFF.md" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read().replace(
    "## Details\n",
    "## Details\nSee `docs/definitely-missing-spec-xyz.md` for the contract.\n",
    1,
)
open(p, "w").write(s)
PY
OUT="$(cd "$WORK" && python3 "$GATE" "$WORK/relmissing" --mode compaction || true)"
(cd "$WORK" && python3 "$GATE" "$WORK/relmissing" --mode compaction) \
  && fail "REGRESSION (P2): a cited relative path that exists nowhere passed the gate"
echo "$OUT" | grep -q "docs/definitely-missing-spec-xyz.md" || fail "the missing relative path is not named: $OUT"
echo "$OUT" | grep -q "$WORK/relmissing" || fail "the message does not name the document's own directory: $OUT"
# os.getcwd() resolves symlinks (on macOS $TMPDIR is /var -> /private/var),
# so compare against the physical path.
REALWORK="$(cd "$WORK" && pwd -P)"
echo "$OUT" | grep -q "current working directory $REALWORK" || fail "the message does not name the working directory: $OUT"
echo "PASS missing relative path fails and names both search locations"

# 36. P2 (archives in the text loop): a directory target's all_files includes
#     the .zip, and the secret loop used to read it as UTF-8 text — the whole
#     bundle into memory, and regex matches out of compressed bytes, which
#     contradicts the documented entry-name-only policy. The archive below is
#     STORED (uncompressed), so a credential-shaped string is literally present
#     in its bytes; it must produce no secret finding.
mkgood "$WORK/zipbytes"
python3 - "$WORK/zipbytes/workspace.zip" "sk-ant-api03-$(python3 -c 'print("A"*95)')" <<'PY'
import zipfile, sys
with zipfile.ZipFile(sys.argv[1], "w", zipfile.ZIP_STORED) as zf:
    zf.writestr("notes.txt", sys.argv[2] + "\n")
PY
grep -q "sk-ant-api03" "$WORK/zipbytes/workspace.zip" || fail "test setup: the archive bytes do not carry the fixture string"
OUT="$(python3 "$GATE" "$WORK/zipbytes" --mode compaction)" \
  || fail "REGRESSION (P2): a credential-shaped string in an archive's bytes was reported: $OUT"
echo "$OUT" | grep -q "GATE: PASS" || fail "no PASS line for an archive with credential-shaped bytes: $OUT"
echo "PASS archive bytes are not scanned as text"

# 37. ...while the entry-NAME check on the very same kind of archive still
#     fails: excluding archives from the text loop must not disarm the one
#     archive check the gate does make.
mkgood "$WORK/zipentry"
python3 - "$WORK/zipentry/workspace.zip" <<'PY'
import zipfile, sys
with zipfile.ZipFile(sys.argv[1], "w", zipfile.ZIP_STORED) as zf:
    zf.writestr("config/.env", "KEY=value\n")
PY
OUT="$(python3 "$GATE" "$WORK/zipentry" --mode compaction || true)"
python3 "$GATE" "$WORK/zipentry" --mode compaction && fail "a zip holding a .env entry passed the gate"
echo "$OUT" | grep -q "config/.env" || fail "the .env entry is not named: $OUT"
echo "PASS zip entry named .env still fails on the entry name"

echo "ALL PASS"
