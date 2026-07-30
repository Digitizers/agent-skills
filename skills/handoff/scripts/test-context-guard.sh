#!/usr/bin/env bash
# Regression tests for context-guard.sh. Run from anywhere: exits non-zero on
# first failure, prints PASS lines otherwise.
set -euo pipefail

GUARD="$(cd "$(dirname "$0")" && pwd)/context-guard.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"; rm -f "${TMPDIR:-/tmp}"/handoff-guard-cg-test-*' EXIT

fail() { echo "FAIL: $1" >&2; exit 1; }

mk_transcript() { # $1=file $2=tokens
  printf '{"message":{"usage":{"input_tokens":%d,"cache_read_input_tokens":0,"cache_creation_input_tokens":0}}}\n' "$2" > "$1"
}

run_guard() { # $1=payload-file -> stdout; fails the suite on nonzero exit
  bash "$GUARD" < "$1" || fail "context-guard.sh exited $? on $1"
}

# 1. Below threshold -> silent
mk_transcript "$WORK/low.jsonl" 50000
printf '{"transcript_path":"%s","session_id":"cg-test-low","hook_event_name":"UserPromptSubmit"}' "$WORK/low.jsonl" > "$WORK/in-low.json"
OUT="$(run_guard "$WORK/in-low.json")"
[ -z "$OUT" ] || fail "fired below threshold"
echo "PASS below-threshold silent"

# 2. Above threshold -> fires with additionalContext
mk_transcript "$WORK/high.jsonl" 150000
printf '{"transcript_path":"%s","session_id":"cg-test-high","hook_event_name":"UserPromptSubmit"}' "$WORK/high.jsonl" > "$WORK/in-high.json"
OUT="$(run_guard "$WORK/in-high.json")"
echo "$OUT" | grep -q "additionalContext" || fail "did not fire above threshold"
echo "PASS above-threshold fires"

# 3. Same session again -> marker suppresses
OUT="$(run_guard "$WORK/in-high.json")"
[ -z "$OUT" ] || fail "fired twice in one session"
echo "PASS once-per-session marker"

# 4. Huge hook payload on stdin (>1 MB tool_response) -> must not crash with
#    "Argument list too long" and must still fire (Codex round-1 P2).
BIG="$(python3 -c 'print("x" * (1024 * 1024))')"
printf '{"transcript_path":"%s","session_id":"cg-test-big","hook_event_name":"PostToolUse","tool_response":{"stdout":"%s"}}' "$WORK/high.jsonl" "$BIG" > "$WORK/in-big.json"
OUT="$(run_guard "$WORK/in-big.json")"
echo "$OUT" | grep -q "additionalContext" || fail "large stdin payload broke the hook"
echo "PASS 1MB stdin payload"

# 5. Transcript tail after the last usage block counts toward the estimate
#    (Codex round-2 P2): 130k recorded input + ~15k-token tail must cross the
#    default 140k threshold even though the last usage alone reads 65%.
mk_transcript "$WORK/tail.jsonl" 130000
python3 - "$WORK/tail.jsonl" <<'PY'
import json, sys
with open(sys.argv[1], "a") as f:
    f.write(json.dumps({"type": "user", "message": {"content": "y" * 60000}}) + "\n")
PY
printf '{"transcript_path":"%s","session_id":"cg-test-tail","hook_event_name":"UserPromptSubmit"}' "$WORK/tail.jsonl" > "$WORK/in-tail.json"
OUT="$(run_guard "$WORK/in-tail.json")"
echo "$OUT" | grep -q "additionalContext" || fail "transcript tail not counted"
echo "PASS transcript-tail counted"

# 6. Large hook payload alone can cross the threshold: 130k recorded input +
#    a 60k-char prompt in the hook input (not yet in the transcript).
mk_transcript "$WORK/mid.jsonl" 130000
printf '{"transcript_path":"%s","session_id":"cg-test-payload2","hook_event_name":"UserPromptSubmit","prompt":"%s"}' "$WORK/mid.jsonl" "$(python3 -c 'print("p" * 60000)')" > "$WORK/in-payload2.json"
OUT="$(run_guard "$WORK/in-payload2.json")"
echo "$OUT" | grep -q "additionalContext" || fail "hook payload not counted"
echo "PASS hook-payload counted"

# 7. Token-dense scripts are not diluted by the 4:1 ASCII ratio (Codex
#    round-3 P2): 130k baseline + 24k Hebrew chars is ~12k real tokens
#    (~2 chars/token) and must fire; the flat chars/4 estimate read it as
#    6k and stayed silent.
mk_transcript "$WORK/heb.jsonl" 130000
python3 - "$WORK/heb.jsonl" <<'PY'
import json, sys
with open(sys.argv[1], "a") as f:
    f.write(json.dumps({"type": "user", "message": {"content": "א" * 24000}}, ensure_ascii=False) + "\n")
PY
printf '{"transcript_path":"%s","session_id":"cg-test-hebrew","hook_event_name":"UserPromptSubmit"}' "$WORK/heb.jsonl" > "$WORK/in-heb.json"
OUT="$(run_guard "$WORK/in-heb.json")"
echo "$OUT" | grep -q "additionalContext" || fail "Hebrew tail under-counted"
echo "PASS hebrew-tail script-aware estimate"

echo "all context-guard tests passed"
