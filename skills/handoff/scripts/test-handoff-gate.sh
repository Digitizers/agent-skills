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
echo "ALL PASS"
