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
    "## Details\nTOKEN=your-actual-prod-db-password-Xk29fLp3Q7vZ\n",
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
    "## Details\nSECRET=REDACTED_BUT_ALSO_hunter2SuperRealValue\n",
    1,
)
open(p, "w").write(s)
PY
python3 "$GATE" "$WORK/wordprefixbypass" --mode compaction && fail "REGRESSION: a real secret with a redaction-word prefix bypassed the gate"
echo "PASS real secret with a redaction-word prefix still fails"

echo "ALL PASS"
