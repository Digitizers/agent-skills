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

run_guard() { # $1=payload-file -> stdout
  bash "$GUARD" < "$1"
}

# 1. Below threshold -> silent
mk_transcript "$WORK/low.jsonl" 50000
printf '{"transcript_path":"%s","session_id":"cg-test-low","hook_event_name":"UserPromptSubmit"}' "$WORK/low.jsonl" > "$WORK/in-low.json"
[ -z "$(run_guard "$WORK/in-low.json")" ] || fail "fired below threshold"
echo "PASS below-threshold silent"

# 2. Above threshold -> fires with additionalContext
mk_transcript "$WORK/high.jsonl" 150000
printf '{"transcript_path":"%s","session_id":"cg-test-high","hook_event_name":"UserPromptSubmit"}' "$WORK/high.jsonl" > "$WORK/in-high.json"
OUT="$(run_guard "$WORK/in-high.json")"
echo "$OUT" | grep -q "additionalContext" || fail "did not fire above threshold"
echo "PASS above-threshold fires"

# 3. Same session again -> marker suppresses
[ -z "$(run_guard "$WORK/in-high.json")" ] || fail "fired twice in one session"
echo "PASS once-per-session marker"

# 4. Huge hook payload on stdin (>1 MB tool_response) -> must not crash with
#    "Argument list too long" and must still fire (Codex round-1 P2).
BIG="$(python3 -c 'print("x" * (1024 * 1024))')"
printf '{"transcript_path":"%s","session_id":"cg-test-big","hook_event_name":"PostToolUse","tool_response":{"stdout":"%s"}}' "$WORK/high.jsonl" "$BIG" > "$WORK/in-big.json"
OUT="$(run_guard "$WORK/in-big.json")"
echo "$OUT" | grep -q "additionalContext" || fail "large stdin payload broke the hook"
echo "PASS 1MB stdin payload"

echo "all context-guard tests passed"
