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

# 38. Codex round 2, P1 — THE HOLE THE ARCHIVE EXCLUSION OPENED. is_archive()
#     excluded every archive from the text scan while the entry-name check
#     still keyed on a case-sensitive ".zip", so a `.tar.gz` bundle was
#     checked by NEITHER path and carried a .env straight through. A
#     non-zip archive is now a failure in its own right: the gate cannot
#     read its entry names, so it cannot say the bundle is clean.
mkgood "$WORK/targz"
python3 - "$WORK/targz/workspace.TAR.GZ" <<'PY'
import io, sys, tarfile
with tarfile.open(sys.argv[1], "w:gz") as tf:
    data = b"KEY=value\n"
    info = tarfile.TarInfo(".env")
    info.size = len(data)
    tf.addfile(info, io.BytesIO(data))
PY
OUT="$(python3 "$GATE" "$WORK/targz" --mode compaction || true)"
python3 "$GATE" "$WORK/targz" --mode compaction && fail "REGRESSION (P1): a .tar.gz bundle was checked by neither the entry-name pass nor the text scan"
echo "$OUT" | grep -q "workspace.TAR.GZ" || fail "the unverifiable archive is not named: $OUT"
echo "$OUT" | grep -q "could not be verified" || fail "the finding does not say the contents are unverified: $OUT"
echo "$OUT" | grep -q "repackage it as a .zip" || fail "the finding does not name the way out: $OUT"
echo "PASS a non-zip archive fails as unverifiable, naming the way out"

# 39. ...and the entry-name check is case-insensitive: WORKSPACE.ZIP is a zip.
mkgood "$WORK/upperzip"
python3 - "$WORK/upperzip/WORKSPACE.ZIP" <<'PY'
import zipfile, sys
with zipfile.ZipFile(sys.argv[1], "w") as zf:
    zf.writestr(".env", "KEY=value\n")
PY
OUT="$(python3 "$GATE" "$WORK/upperzip" --mode compaction || true)"
python3 "$GATE" "$WORK/upperzip" --mode compaction && fail "REGRESSION (P1): WORKSPACE.ZIP holding a .env passed the gate"
echo "$OUT" | grep -q "WORKSPACE.ZIP contains .env" || fail "the uppercase zip was not checked by entry name: $OUT"
echo "$OUT" | grep -q "could not be verified" && fail "a real zip must be read by entry name, not reported as unverifiable: $OUT"
echo "PASS uppercase WORKSPACE.ZIP is checked by entry name"

# 40. ...and a clean lowercase zip still passes: tightening the archive rules
#     must not fail an ordinary, correct bundle.
mkgood "$WORK/cleanzip"
python3 - "$WORK/cleanzip/workspace.zip" <<'PY'
import zipfile, sys
with zipfile.ZipFile(sys.argv[1], "w") as zf:
    zf.writestr("src/main.py", "print('hi')\n")
    zf.writestr("notes/scratch.md", "unfinished thought\n")
PY
OUT="$(python3 "$GATE" "$WORK/cleanzip" --mode compaction)" || fail "a clean workspace.zip was rejected: $OUT"
echo "$OUT" | grep -q "GATE: PASS" || fail "no PASS line for a clean zip: $OUT"
echo "PASS a clean workspace.zip still passes"

# 41. Codex round 2, P2 — the MODE_FILES check was existence-only, so a
#     zero-byte PROMPT.txt printed GATE: PASS and the handoff shipped without
#     the one artifact quota and cross-workspace modes exist to produce.
mkgood "$WORK/emptyprompt"
: > "$WORK/emptyprompt/PROMPT.txt"
OUT="$(python3 "$GATE" "$WORK/emptyprompt" --mode quota || true)"
python3 "$GATE" "$WORK/emptyprompt" --mode quota && fail "REGRESSION (P2): an empty PROMPT.txt passed the gate"
echo "$OUT" | grep -q "empty file for quota mode: PROMPT.txt" || fail "the empty PROMPT.txt is not reported as empty: $OUT"
echo "PASS an empty PROMPT.txt fails, reported as empty"

# 42. ...and so does a DIRECTORY named PROMPT.txt, reported as the different
#     mistake it is — the writer needs to know whether to fill it in or
#     replace it.
mkgood "$WORK/dirprompt"
mkdir -p "$WORK/dirprompt/PROMPT.txt"
OUT="$(python3 "$GATE" "$WORK/dirprompt" --mode quota || true)"
python3 "$GATE" "$WORK/dirprompt" --mode quota && fail "REGRESSION (P2): a directory named PROMPT.txt passed the gate"
echo "$OUT" | grep -q "PROMPT.txt for quota mode is not a regular file" || fail "a directory PROMPT.txt is not reported as such: $OUT"
echo "$OUT" | grep -q "empty file for quota mode" && fail "a directory must not be reported as an empty file: $OUT"
echo "PASS a directory named PROMPT.txt fails, reported as not a regular file"

# 43. ...and a normal, non-empty PROMPT.txt still passes.
mkgood "$WORK/goodprompt"
echo "Continue the migration from step 3; the branch is already pushed." > "$WORK/goodprompt/PROMPT.txt"
OUT="$(python3 "$GATE" "$WORK/goodprompt" --mode quota)" || fail "a normal PROMPT.txt was rejected: $OUT"
echo "$OUT" | grep -q "GATE: PASS" || fail "no PASS line for a normal PROMPT.txt: $OUT"
echo "PASS a normal non-empty PROMPT.txt still passes"

# 44. Codex round 3, P1 — TARGET RESOLUTION vs THE STORAGE LAYOUT. The skill
#     saves single-file handoffs as
#     ~/.claude/handoffs/<project-slug>/handoff-<slug>-<date>-<HHMM>.md, so the
#     per-project directory holds MANY handoffs and no HANDOFF.md. Telling the
#     caller to gate "the directory" therefore failed a perfectly good handoff,
#     and would have scanned unrelated older handoffs in the same directory.
#     The FILE target is the correct call for a single-file mode.
mkdir -p "$WORK/project-slug"
mkgood "$WORK/mkgood-src"
cp "$WORK/mkgood-src/HANDOFF.md" "$WORK/project-slug/handoff-alpha-2026-09-29-1412.md"
# An unrelated OLDER handoff sharing the directory, carrying a secret of its
# own: the file target must not look at it.
printf '# Handoff — beta (2026-08-01)\n\nsk-ant-api03-%s\n' "$(python3 -c 'print("A"*95)')" \
  > "$WORK/project-slug/handoff-beta-2026-08-01-0900.md"
OUT="$(python3 "$GATE" "$WORK/project-slug/handoff-alpha-2026-09-29-1412.md" --mode compaction)" \
  || fail "REGRESSION (P1): a single-file handoff in the shared project directory was rejected: $OUT"
echo "$OUT" | grep -q "GATE: PASS" || fail "no PASS line for the file target: $OUT"
echo "$OUT" | grep -q "handoff-beta" && fail "REGRESSION (P1): the unrelated older handoff was scanned"
echo "PASS a single-file handoff is gated by its own path, not its directory"

# 45. ...and pointing the gate at that shared directory fails with guidance
#     that teaches the right call, instead of a bare "no handoff document".
OUT="$(python3 "$GATE" "$WORK/project-slug" --mode compaction || true)"
python3 "$GATE" "$WORK/project-slug" --mode compaction && fail "a directory with no HANDOFF.md passed"
echo "$OUT" | grep -q "pass the document's path for a single-file handoff" \
  || fail "the failure does not teach the single-file call: $OUT"
echo "$OUT" | grep -q "bundle directory containing HANDOFF.md" \
  || fail "the failure does not teach the bundle call: $OUT"
echo "PASS a directory with no HANDOFF.md fails with usage guidance"

# 46. ...while a BUNDLE directory — the per-handoff directory holding
#     HANDOFF.md and PROMPT.txt — is still gated as a directory and still
#     passes. The two calls are not interchangeable, and both must work.
mkgood "$WORK/bundlemode"
echo "Continue from step 3." > "$WORK/bundlemode/PROMPT.txt"
OUT="$(python3 "$GATE" "$WORK/bundlemode" --mode quota)" || fail "a bundle directory was rejected: $OUT"
echo "$OUT" | grep -q "GATE: PASS" || fail "no PASS line for the bundle directory: $OUT"
echo "PASS a bundle directory is still gated as a directory"

# 47. Codex round 4, P1 — THE COMMENT WAS AHEAD OF THE CODE. A round-5 note
#     beside GENERIC_CRED_RX claimed `*_AUTH` labels were covered, but `auth`
#     was never in the alternation, so `BASIC_AUTH=<opaque value>` — a real
#     credential — got GATE: PASS and the gate certified it.
mkgood "$WORK/basicauth"
python3 - "$WORK/basicauth/HANDOFF.md" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read().replace(
    "## Details\n", "## Details\n" + "BASIC_AU" + "TH=" + "ZHVtbXk6" + "c2VjcmV0" + "\n", 1)
open(p, "w").write(s)
PY
OUT="$(python3 "$GATE" "$WORK/basicauth" --mode compaction || true)"
python3 "$GATE" "$WORK/basicauth" --mode compaction && fail "REGRESSION (P1): BASIC_AUTH=<opaque value> passed the gate"
echo "$OUT" | grep -q "heuristic" || fail "the auth finding does not name itself a heuristic: $OUT"
echo "PASS BASIC_AUTH= with an opaque value fails"

# 48. ...and an HTTP Authorization header value, where the credential sits
#     AFTER a scheme word. The value group used to stop at the space and
#     capture "Basic" — five characters, under the length floor — so the
#     real credential went through. The scheme is now matched separately.
mkgood "$WORK/authheader"
python3 - "$WORK/authheader/HANDOFF.md" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read().replace(
    "## Details\n",
    "## Details\n" + "AUTHORIZA" + "TION=Basic " + "ZHVtbXk6" + "c2VjcmV0" + "\n", 1)
open(p, "w").write(s)
PY
python3 "$GATE" "$WORK/authheader" --mode compaction && fail "REGRESSION (P1): AUTHORIZATION=Basic <value> passed the gate"
echo "PASS AUTHORIZATION=Basic <value> fails despite the scheme word"

# 49. ...and a passphrase, another label the alternation was missing.
mkgood "$WORK/passphrase"
python3 - "$WORK/passphrase/HANDOFF.md" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read().replace(
    "## Details\n",
    "## Details\n" + "SSH_PASS" + "PHRASE=" + "correct-horse" + "-battery" + "\n", 1)
open(p, "w").write(s)
PY
python3 "$GATE" "$WORK/passphrase" --mode compaction && fail "REGRESSION (P1): SSH_PASSPHRASE=<value> passed the gate"
echo "PASS SSH_PASSPHRASE= fails"

# 50. ...and the placeholder exemptions still work on every new label: the
#     skill tells the writer to keep the NAME and redact the value, and
#     widening the alternation must not turn that instruction into a failure.
mkgood "$WORK/authplaceholder"
python3 - "$WORK/authplaceholder/HANDOFF.md" <<'PY'
import sys
p = sys.argv[1]
lines = "\n".join([
    "AUTH=REDACTED",
    "AUTHORIZATION=Bearer REDACTED",
    "SSH_PASSPHRASE=REDACTED_DO_NOT_COMMIT",
    "SESSION_COOKIE=<paste-here>",
    "AWS_ACCESS_KEY=xxxxxxxxxxxx",
    "DEPLOY_PRIVATE_KEY=your-key-here",
])
s = open(p).read().replace("## Details\n", f"## Details\n{lines}\n", 1)
open(p, "w").write(s)
PY
OUT="$(python3 "$GATE" "$WORK/authplaceholder" --mode compaction)" || fail "placeholders on the new labels were rejected: $OUT"
echo "$OUT" | grep -q "GATE: PASS" || fail "no PASS line for placeholders on the new labels: $OUT"
echo "PASS placeholder values still pass on every newly covered label"

# 51. ...and prose mentioning auth with no assignment is still prose.
mkgood "$WORK/authprose"
python3 - "$WORK/authprose/HANDOFF.md" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read().replace(
    "## Details\n",
    "## Details\nThe auth flow is broken; basic auth was removed last week.\n"
    "Set up authorization before running the sync, and clear the cookie jar.\n",
    1,
)
open(p, "w").write(s)
PY
OUT="$(python3 "$GATE" "$WORK/authprose" --mode compaction)" || fail "prose mentioning auth was rejected: $OUT"
echo "$OUT" | grep -q "GATE: PASS" || fail "no PASS line for prose mentioning auth: $OUT"
echo "PASS prose mentioning auth with no assignment still passes"

# 52. ...and the failure message's label list is GENERATED from the
#     alternation, so the text a writer is told to rename away from can never
#     drift from the labels the scan actually uses again. This is the
#     structural half of the fix — four rounds running, a claim about
#     coverage was ahead of the code.
OUT="$(python3 - "$GATE" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("gate", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
expected = "/".join(k.replace("[_-]?", "_") for k in m.GENERIC_CRED_KEYWORDS)
missing = [k for k in m.GENERIC_CRED_KEYWORDS
           if k.replace("[_-]?", "_") not in m.GENERIC_CRED_LABEL_LIST]
print("OK" if expected == m.GENERIC_CRED_LABEL_LIST and not missing else "DRIFT")
PY
)"
[ "$OUT" = "OK" ] || fail "the printed label list drifted from the alternation: $OUT"
# ...and the message really prints it.
mkgood "$WORK/labellist"
python3 - "$WORK/labellist/HANDOFF.md" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read().replace(
    "## Details\n", "## Details\n" + "MY_SESSION_CO" + "OKIE=" + "abcdef012345" + "6789abcd" + "\n", 1)
open(p, "w").write(s)
PY
OUT="$(python3 "$GATE" "$WORK/labellist" --mode compaction || true)"
echo "$OUT" | grep -q "authorization" || fail "the failure message does not print the generated label list: $OUT"
echo "$OUT" | grep -q "passphrase" || fail "the failure message's label list is missing passphrase: $OUT"
echo "PASS the failure message's label list is generated from the alternation"

# 53. Codex round 5, P2 — HEADINGS INSIDE A FENCE ARE NOT SECTIONS. sections()
#     treated any line starting with "#" as a heading, fences included, and
#     this skill's own documentation tells authors to paste a TEMPLATE in a
#     code block. So a handoff whose only `## Tried and rejected` was inside a
#     fenced example could omit the real section entirely and still print
#     GATE: PASS — the gate certifying sample text.
mkgood "$WORK/fencedonly"
python3 - "$WORK/fencedonly/HANDOFF.md" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read()
# Remove the REAL section and leave it only inside a fenced template.
s = s.replace(
    "## Tried and rejected\n- Patching the caller: the bug is in the callee.\n\n",
    "")
s = s.replace(
    "## Open issues\n",
    "The template every handoff follows:\n\n"
    "```markdown\n"
    "## Tried and rejected\n"
    "- <approach> — rejected because <evidence>\n"
    "```\n\n"
    "## Open issues\n",
    1,
)
open(p, "w").write(s)
PY
grep -q "## Tried and rejected" "$WORK/fencedonly/HANDOFF.md" || fail "test setup: the fenced heading is not in the document"
OUT="$(python3 "$GATE" "$WORK/fencedonly" --mode compaction || true)"
python3 "$GATE" "$WORK/fencedonly" --mode compaction && fail "REGRESSION (P2): a heading that exists only inside a fenced example passed as a real section"
echo "$OUT" | grep -q "Tried and rejected" || fail "the missing section is not reported: $OUT"
echo "PASS a heading inside a fenced block does not count as a section"

# 54. ...and the same document WITH a real section outside the fence passes:
#     tracking fences must not make a fenced template poisonous.
mkgood "$WORK/fencedplus"
python3 - "$WORK/fencedplus/HANDOFF.md" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read().replace(
    "## Open issues\n",
    "The template every handoff follows:\n\n"
    "```markdown\n"
    "## Tried and rejected\n"
    "- <approach> — rejected because <evidence>\n"
    "## Suggested skills\n"
    "```\n\n"
    "## Open issues\n",
    1,
)
open(p, "w").write(s)
PY
OUT="$(python3 "$GATE" "$WORK/fencedplus" --mode compaction)" || fail "a fenced template alongside the real sections was rejected: $OUT"
echo "$OUT" | grep -q "GATE: PASS" || fail "no PASS line for a document carrying a fenced template: $OUT"
echo "PASS a fenced template passes when the real sections are outside it"

# 55. ...and the fence forms that matter all close correctly: tilde fences,
#     runs longer than three, and an info string on the opener. A fence left
#     unclosed by a mis-parse would swallow every heading after it and fail a
#     valid handoff, so this pins the other direction too.
mkgood "$WORK/fencevariants"
python3 - "$WORK/fencevariants/HANDOFF.md" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read().replace(
    "## Current state\n",
    "~~~\n## not a heading (tilde fence)\n~~~\n\n"
    "````markdown\n## not a heading (four backticks, info string)\n```\nstill inside\n````\n\n"
    "   ```sh\n   ## not a heading (indented fence)\n   ```\n\n"
    "## Current state\n",
    1,
)
open(p, "w").write(s)
PY
OUT="$(python3 "$GATE" "$WORK/fencevariants" --mode compaction)" || fail "fence variants broke a valid handoff: $OUT"
echo "$OUT" | grep -q "GATE: PASS" || fail "no PASS line with tilde/longer/indented fences: $OUT"
echo "PASS tilde, longer-run and indented fences are tracked correctly"

# 56. ...and content inside a fence is still SCANNED for secrets: hiding a
#     credential in a code block must not become the new way past the gate.
mkgood "$WORK/fencedsecret"
python3 - "$WORK/fencedsecret/HANDOFF.md" "sk-ant-api03-$(python3 -c 'print("A"*95)')" <<'PY'
import sys
p, value = sys.argv[1], sys.argv[2]
s = open(p).read().replace(
    "## Details\n", f"## Details\n```\n{value}\n```\n", 1)
open(p, "w").write(s)
PY
OUT="$(python3 "$GATE" "$WORK/fencedsecret" --mode compaction || true)"
python3 "$GATE" "$WORK/fencedsecret" --mode compaction && fail "a secret inside a fenced block passed the gate"
echo "$OUT" | grep -q "Anthropic API key found in" || fail "the fenced secret was not reported: $OUT"
echo "PASS a secret inside a fenced block is still caught"

# 57. Codex round 6, P1 — THE FILENAME IS PART OF THE HANDOFF. The patterns
#     were applied to file CONTENTS only, so a bundle holding a regular file
#     NAMED sk-ant-<value>.md printed GATE: PASS and the credential shipped
#     as the filename.
mkgood "$WORK/namesecret"
printf 'nothing secret in here\n' > "$WORK/namesecret/sk-ant-api03-$(python3 -c 'print("A"*95)').md"
OUT="$(python3 "$GATE" "$WORK/namesecret" --mode compaction || true)"
python3 "$GATE" "$WORK/namesecret" --mode compaction && fail "REGRESSION (P1): a credential in a FILE NAME passed the gate"
echo "$OUT" | grep -q "FILE NAME" || fail "the finding does not say the credential is in the file name: $OUT"
echo "$OUT" | grep -q "rename the file" || fail "the finding does not name the way out: $OUT"
echo "$OUT" | grep -q "sk-ant-api03-AAAA" && fail "the credential in the file name was printed verbatim: $OUT"
echo "PASS a credential in a file NAME fails, and is redacted in the finding"

# 58. ...and the generic label=value shape in a filename too.
mkgood "$WORK/namegeneric"
printf 'notes\n' > "$WORK/namegeneric/$(python3 -c 'print("BUNNY_API" + "_KEY=" + "7fd93ba21c0e" + "4b8aa1c2")').md"
OUT="$(python3 "$GATE" "$WORK/namegeneric" --mode compaction || true)"
python3 "$GATE" "$WORK/namegeneric" --mode compaction && fail "REGRESSION (P1): BUNNY_API_KEY=<value> as a FILE NAME passed the gate"
echo "$OUT" | grep -q "FILE NAME" || fail "the generic filename finding does not say FILE NAME: $OUT"
echo "PASS a generic credential shape in a file NAME fails"

# 59. ...and ordinary file names still pass: the name scan must not start
#     failing every bundle.
mkgood "$WORK/nameok"
echo "paste me" > "$WORK/nameok/PROMPT.txt"
mkdir -p "$WORK/nameok/context"
printf 'plain notes\n' > "$WORK/nameok/context/session-notes-2026-09-29.md"
printf 'more\n' > "$WORK/nameok/api-design.md"
python3 - "$WORK/nameok/workspace.zip" <<'PY'
import zipfile, sys
with zipfile.ZipFile(sys.argv[1], "w") as zf:
    zf.writestr("src/main.py", "print('hi')\n")
PY
OUT="$(python3 "$GATE" "$WORK/nameok" --mode quota)" || fail "ordinary file names were rejected: $OUT"
echo "$OUT" | grep -q "GATE: PASS" || fail "no PASS line for ordinary file names: $OUT"
echo "PASS ordinary file names still pass"

# 60. Codex round 6, P2 — THE SAME FENCE HOLE IN A SECOND PLACE. The
#     numbered-steps check read the RAW section text, so a `What to do next`
#     whose only `1. …` line sat inside a fenced template passed with no real
#     steps. The document is de-fenced ONCE now and every structural check
#     reads that.
mkgood "$WORK/fencedsteps"
python3 - "$WORK/fencedsteps/HANDOFF.md" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read().replace(
    "## What to do next\n1. Run the suite.\n2. Open the PR.\n",
    "## What to do next\n"
    "Follow the usual shape:\n\n"
    "```markdown\n"
    "1. <first concrete step>\n"
    "2. <second concrete step>\n"
    "```\n",
    1,
)
open(p, "w").write(s)
PY
grep -q "^1\. <first concrete step>" "$WORK/fencedsteps/HANDOFF.md" || fail "test setup: the fenced numbered line is not in the document"
OUT="$(python3 "$GATE" "$WORK/fencedsteps" --mode compaction || true)"
python3 "$GATE" "$WORK/fencedsteps" --mode compaction && fail "REGRESSION (P2): numbered steps that exist only inside a fence passed"
echo "$OUT" | grep -q "What to do next is not a numbered list" || fail "the missing steps are not reported: $OUT"
echo "PASS numbered steps inside a fence do not satisfy the check"

# 61. ...and a real numbered list outside a fence still passes, fenced
#     template and all.
mkgood "$WORK/fencedstepsok"
python3 - "$WORK/fencedstepsok/HANDOFF.md" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read().replace(
    "## What to do next\n1. Run the suite.\n",
    "## What to do next\n"
    "```markdown\n"
    "1. <first concrete step>\n"
    "```\n"
    "1. Run the suite.\n",
    1,
)
open(p, "w").write(s)
PY
OUT="$(python3 "$GATE" "$WORK/fencedstepsok" --mode compaction)" || fail "a real numbered list next to a fenced template was rejected: $OUT"
echo "$OUT" | grep -q "GATE: PASS" || fail "no PASS line for a real numbered list beside a fenced template: $OUT"
echo "PASS a real numbered list outside the fence still passes"

# 62. ...and a section whose whole body is a fenced block is NOT read as
#     empty: de-fencing neutralises structure, it does not delete content.
mkgood "$WORK/fencedbody"
python3 - "$WORK/fencedbody/HANDOFF.md" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read().replace(
    "## Current state\nTests pass.\n",
    "## Current state\n```\n$ pytest -q\n42 passed\n```\n",
    1,
)
open(p, "w").write(s)
PY
OUT="$(python3 "$GATE" "$WORK/fencedbody" --mode compaction)" || fail "a section whose body is a code block was called empty: $OUT"
echo "$OUT" | grep -q "GATE: PASS" || fail "no PASS line for a code-block-only section: $OUT"
echo "PASS a section whose whole body is a fenced block is not empty"

# 63. The same "scan names, not just contents" rule, ONE LEVEL DOWN: a zip's
#     ENTRY names ship in the archive's index whether or not anyone opens the
#     archive, and they were matched only against the credential-FILE list
#     (.env, id_rsa, ...), never against the value patterns. Entry names only
#     — nothing is extracted.
mkgood "$WORK/zipentryname"
python3 - "$WORK/zipentryname/workspace.zip" "sk-ant-api03-$(python3 -c 'print("A"*95)')" <<'PY'
import zipfile, sys
with zipfile.ZipFile(sys.argv[1], "w") as zf:
    zf.writestr("notes/%s.md" % sys.argv[2], "nothing secret in the body\n")
PY
OUT="$(python3 "$GATE" "$WORK/zipentryname" --mode compaction || true)"
python3 "$GATE" "$WORK/zipentryname" --mode compaction && fail "REGRESSION: a credential in a ZIP ENTRY NAME passed the gate"
echo "$OUT" | grep -q "FILE NAME" || fail "the zip entry finding is not reported in the FILE NAME style: $OUT"
echo "$OUT" | grep -q "workspace.zip" || fail "the finding does not name the archive it came from: $OUT"
echo "$OUT" | grep -q "sk-ant-api03-AAAA" && fail "the credential in the entry name was printed verbatim: $OUT"
echo "$OUT" | grep -q "\[redacted\]" || fail "the credential in the entry name was not masked: $OUT"
echo "PASS a credential in a zip ENTRY NAME fails, redacted, naming the archive"

# 64. ...and the generic label=value shape in an entry name too.
mkgood "$WORK/zipentrygeneric"
python3 - "$WORK/zipentrygeneric/workspace.zip" "$(python3 -c 'print("BUNNY_API" + "_KEY=" + "7fd93ba21c0e" + "4b8aa1c2")')" <<'PY'
import zipfile, sys
with zipfile.ZipFile(sys.argv[1], "w") as zf:
    zf.writestr("%s.txt" % sys.argv[2], "notes\n")
PY
OUT="$(python3 "$GATE" "$WORK/zipentrygeneric" --mode compaction || true)"
python3 "$GATE" "$WORK/zipentrygeneric" --mode compaction && fail "REGRESSION: BUNNY_API_KEY=<value> as a ZIP ENTRY NAME passed the gate"
echo "$OUT" | grep -q "FILE NAME" || fail "the generic entry-name finding is not in the FILE NAME style: $OUT"
echo "PASS a generic credential shape in a zip ENTRY NAME fails"

# 65. ...and a zip with ordinary entry names still passes: the entry-name
#     value scan must not start failing every bundle.
mkgood "$WORK/zipentryok"
python3 - "$WORK/zipentryok/workspace.zip" <<'PY'
import zipfile, sys
with zipfile.ZipFile(sys.argv[1], "w") as zf:
    zf.writestr("src/main.py", "print('hi')\n")
    zf.writestr("docs/api-design.md", "notes\n")
    zf.writestr("scratch/session-notes-2026-09-29.txt", "notes\n")
PY
OUT="$(python3 "$GATE" "$WORK/zipentryok" --mode compaction)" || fail "ordinary zip entry names were rejected: $OUT"
echo "$OUT" | grep -q "GATE: PASS" || fail "no PASS line for ordinary zip entry names: $OUT"
echo "PASS ordinary zip entry names still pass"

# 66. ...and the pre-existing credential-FILE rule on entry names is
#     untouched: a .env entry still fails on the entry name, as before.
mkgood "$WORK/zipenvstill"
python3 - "$WORK/zipenvstill/workspace.zip" <<'PY'
import zipfile, sys
with zipfile.ZipFile(sys.argv[1], "w") as zf:
    zf.writestr("config/.env", "KEY=value\n")
PY
OUT="$(python3 "$GATE" "$WORK/zipenvstill" --mode compaction || true)"
python3 "$GATE" "$WORK/zipenvstill" --mode compaction && fail "a zip holding a .env entry passed the gate"
echo "$OUT" | grep -q "credentials never travel in the zip" || fail "the .env entry rule changed wording or stopped firing: $OUT"
echo "PASS the .env entry-name rule still fires unchanged"

# 67. THE FOURTH PLACE A NAME TRAVELS: the handoff's OWN name. A bundle
#     directory called after a token keeps that name when it is copied or
#     zipped up and sent, and nothing scanned it — iter_files yields paths
#     RELATIVE to that directory, so its own name was never in the list.
mkdir -p "$WORK/outer"
DIRNAME="$(python3 -c 'print("handoff-" + "BUNNY_API" + "_KEY=" + "7fd93ba21c0e" + "4b8aa1c2")')"
mkgood "$WORK/outer/$DIRNAME"
OUT="$(python3 "$GATE" "$WORK/outer/$DIRNAME" --mode compaction || true)"
python3 "$GATE" "$WORK/outer/$DIRNAME" --mode compaction && fail "REGRESSION: a credential in the handoff DIRECTORY's own name passed the gate"
echo "$OUT" | grep -q "FILE NAME" || fail "the directory-name finding is not in the FILE NAME style: $OUT"
echo "$OUT" | grep -q "rename the handoff directory" || fail "the finding does not name the way out for a directory: $OUT"
echo "PASS a credential in the handoff directory's own name fails"

# 68. ...but only the target's BASENAME: the directories above it belong to
#     the sender's machine, are none of the handoff's business, and reporting
#     them would fail every run made from a credential-shaped home directory.
mkdir -p "$WORK/$(python3 -c 'print("home-" + "BUNNY_API" + "_KEY=" + "7fd93ba21c0e" + "4b8aa1c2")')"
PARENT="$WORK/$(python3 -c 'print("home-" + "BUNNY_API" + "_KEY=" + "7fd93ba21c0e" + "4b8aa1c2")')"
mkgood "$PARENT/clean-bundle"
OUT="$(python3 "$GATE" "$PARENT/clean-bundle" --mode compaction)" \
  || fail "a credential-shaped ANCESTOR directory failed an otherwise clean handoff: $OUT"
echo "$OUT" | grep -q "GATE: PASS" || fail "no PASS line for a clean bundle under a credential-shaped parent: $OUT"
echo "PASS only the target's own name is scanned, not the directories above it"

echo "ALL PASS"
