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

# 8. Token-dense ASCII (base64-like) is covered by the 2-chars/token floor
#    (Codex round-4 P2): 130k baseline + 32k high-entropy ASCII chars is
#    ~12-16k real tokens; the old 4:1 ratio added only 8k and stayed silent.
mk_transcript "$WORK/b64.jsonl" 130000
printf '{"transcript_path":"%s","session_id":"cg-test-b64","hook_event_name":"PostToolUse","tool_response":{"stdout":"%s"}}' "$WORK/b64.jsonl" "$(python3 -c 'import base64,os; print(base64.b64encode(os.urandom(24000)).decode())')" > "$WORK/in-b64.json"
OUT="$(run_guard "$WORK/in-b64.json")"
echo "$OUT" | grep -q "additionalContext" || fail "token-dense ASCII payload under-counted"
echo "PASS base64-payload conservative floor"

# 9. Emoji-heavy payloads are covered by the byte-based floor (Codex
#    round-5 P2): 10k emoji code points can be 10-20k real tokens; the
#    per-char //2 path added only 5k and stayed silent past the threshold.
mk_transcript "$WORK/emoji.jsonl" 130000
printf '{"transcript_path":"%s","session_id":"cg-test-emoji","hook_event_name":"PostToolUse","tool_response":{"stdout":"%s"}}' "$WORK/emoji.jsonl" "$(python3 -c 'print("\U0001F680" * 10000)')" > "$WORK/in-emoji.json"
OUT="$(run_guard "$WORK/in-emoji.json")"
echo "$OUT" | grep -q "additionalContext" || fail "emoji payload under-counted"
echo "PASS emoji-payload byte floor"

# 10. A compact boundary drops everything above it (#35): 600k of recorded
#     pre-compact context followed by a compact_boundary and a small tail is
#     a ~0% window, not the ">100%" the hook used to report on the first
#     prompt after a restart.
mk_transcript "$WORK/compact.jsonl" 600000
python3 - "$WORK/compact.jsonl" <<'PYX'
import json, sys
with open(sys.argv[1], "a") as f:
    f.write(json.dumps({"type": "system", "subtype": "compact_boundary",
                        "compactMetadata": {"trigger": "auto",
                                            "preTokens": 600000}}) + "\n")
    f.write(json.dumps({"type": "user", "message": {"content": "resumed"}}) + "\n")
PYX
printf '{"transcript_path":"%s","session_id":"cg-test-compact","hook_event_name":"UserPromptSubmit"}' "$WORK/compact.jsonl" > "$WORK/in-compact.json"
OUT="$(run_guard "$WORK/in-compact.json")"
[ -z "$OUT" ] || fail "fired on pre-compaction context after a compact boundary"
echo "PASS compact-boundary resets the baseline"

# 11. Post-compaction usage is still measured (#35): the reset must not
#     blind the hook — 150k recorded AFTER the boundary still fires. The
#     pre-compact side stays inside the 200k window here, so this tests the
#     reset alone and not the window-widening of test 12.
mk_transcript "$WORK/compact-hi.jsonl" 190000
python3 - "$WORK/compact-hi.jsonl" <<'PYX'
import json, sys
with open(sys.argv[1], "a") as f:
    f.write(json.dumps({"type": "system", "subtype": "compact_boundary"}) + "\n")
    f.write(json.dumps({"message": {"usage": {"input_tokens": 150000,
                                              "cache_read_input_tokens": 0,
                                              "cache_creation_input_tokens": 0}}}) + "\n")
PYX
printf '{"transcript_path":"%s","session_id":"cg-test-compact-hi","hook_event_name":"UserPromptSubmit"}' "$WORK/compact-hi.jsonl" > "$WORK/in-compact-hi.json"
OUT="$(run_guard "$WORK/in-compact-hi.json")"
echo "$OUT" | grep -q "additionalContext" || fail "compact boundary suppressed a real post-compaction threshold crossing"
echo "PASS post-compaction usage still fires"

# 12. A 1M-context session with the 200k default configured (#35): a single
#     call carrying 600k of context proves the window is bigger than the
#     setting says, so 600k is 60% of 1M — silent, not ">= 300%".
mk_transcript "$WORK/wide.jsonl" 600000
printf '{"transcript_path":"%s","session_id":"cg-test-wide","hook_event_name":"UserPromptSubmit"}' "$WORK/wide.jsonl" > "$WORK/in-wide.json"
OUT="$(run_guard "$WORK/in-wide.json")"
[ -z "$OUT" ] || fail "misread a 1M-context session as past the threshold"
echo "PASS window widened by observed evidence"

# 13. The reported figure is never impossible (#35): the over-counting byte
#     floor may exceed the window, but the message must not claim >100%.
mk_transcript "$WORK/over.jsonl" 199000
python3 - "$WORK/over.jsonl" <<'PYX'
import json, sys
with open(sys.argv[1], "a") as f:
    f.write(json.dumps({"type": "user", "message": {"content": "z" * 400000}}) + "\n")
PYX
printf '{"transcript_path":"%s","session_id":"cg-test-over","hook_event_name":"UserPromptSubmit"}' "$WORK/over.jsonl" > "$WORK/in-over.json"
OUT="$(run_guard "$WORK/in-over.json")"
echo "$OUT" | grep -q "additionalContext" || fail "did not fire when well past the threshold"
python3 - "$OUT" <<'PYX'
import json, re, sys
msg = json.loads(sys.argv[1])["hookSpecificOutput"]["additionalContext"]
pct = float(re.search(r"~(\d+)% of \d+ tokens", msg).group(1))
used = float(re.search(r"~(\d+) used", msg).group(1))
window = float(re.search(r"of (\d+) tokens", msg).group(1))
assert pct <= 100, msg
assert used <= window, msg
PYX
echo "PASS never reports an impossible percentage"

# 14. A model switch re-narrows the window (Codex round-1 P2): a session that
#     carried 600k on a 1M model and then switched to a 200k model must be
#     measured against 200k again — 150k on the small model is 75%, not 15%.
mk_transcript "$WORK/swap.jsonl" 1
python3 - "$WORK/swap.jsonl" <<'PYX'
import json, sys
def usage(model, tokens):
    return json.dumps({"message": {"model": model,
                                   "usage": {"input_tokens": tokens,
                                             "cache_read_input_tokens": 0,
                                             "cache_creation_input_tokens": 0}}})
with open(sys.argv[1], "w") as f:
    f.write(usage("claude-opus-5-1m", 600000) + "\n")
    f.write(usage("claude-sonnet-5", 150000) + "\n")
PYX
printf '{"transcript_path":"%s","session_id":"cg-test-swap","hook_event_name":"UserPromptSubmit"}' "$WORK/swap.jsonl" > "$WORK/in-swap.json"
OUT="$(run_guard "$WORK/in-swap.json")"
echo "$OUT" | grep -q "additionalContext" || fail "kept a wider model's window after switching to a smaller model"
echo "PASS model switch re-narrows the window"

# 15. ...and switching back reads the wide window again: a 600k call on the 1M
#     model is 60% of 1M, not 300% of 200k.
python3 - "$WORK/swap.jsonl" <<'PYX'
import json, sys
with open(sys.argv[1], "a") as f:
    f.write(json.dumps({"message": {"model": "claude-opus-5-1m",
                                    "usage": {"input_tokens": 600000,
                                              "cache_read_input_tokens": 0,
                                              "cache_creation_input_tokens": 0}}}) + "\n")
PYX
printf '{"transcript_path":"%s","session_id":"cg-test-swap-back","hook_event_name":"UserPromptSubmit"}' "$WORK/swap.jsonl" > "$WORK/in-swap-back.json"
OUT="$(run_guard "$WORK/in-swap-back.json")"
[ -z "$OUT" ] || fail "dropped the active model's own window evidence"
echo "PASS switching back reads the wide window again"

# 16. Evidence does not outlive the session that produced it (Codex round-2
#     P2): the same model id covers both a 1M and a 200k window mode, and
#     a 600k call logged under a PREVIOUS session must not widen the window
#     for this one — 150k is 75% of 200k.
python3 - "$WORK/mode.jsonl" <<'PYX'
import json, sys
def usage(session, tokens):
    return json.dumps({"sessionId": session,
                       "message": {"model": "claude-opus-5",
                                   "usage": {"input_tokens": tokens,
                                             "cache_read_input_tokens": 0,
                                             "cache_creation_input_tokens": 0}}})
with open(sys.argv[1], "w") as f:
    f.write(usage("cg-test-oldrun", 600000) + "\n")
    f.write(usage("cg-test-mode", 150000) + "\n")
PYX
printf '{"transcript_path":"%s","session_id":"cg-test-mode","hook_event_name":"UserPromptSubmit"}' "$WORK/mode.jsonl" > "$WORK/in-mode.json"
OUT="$(run_guard "$WORK/in-mode.json")"
echo "$OUT" | grep -q "additionalContext" || fail "carried a previous session's window evidence across a restart"
echo "PASS a previous session's call is not window evidence"

# 17. ...and this session's own evidence still counts: the same 600k call,
#     logged under the CURRENT session, is 60% of 1M and stays silent.
python3 - "$WORK/mode-own.jsonl" <<'PYX'
import json, sys
with open(sys.argv[1], "w") as f:
    f.write(json.dumps({"sessionId": "cg-test-mode-own",
                        "message": {"model": "claude-opus-5",
                                    "usage": {"input_tokens": 600000,
                                              "cache_read_input_tokens": 0,
                                              "cache_creation_input_tokens": 0}}}) + "\n")
PYX
printf '{"transcript_path":"%s","session_id":"cg-test-mode-own","hook_event_name":"UserPromptSubmit"}' "$WORK/mode-own.jsonl" > "$WORK/in-mode-own.json"
OUT="$(run_guard "$WORK/in-mode-own.json")"
[ -z "$OUT" ] || fail "ignored this session's own window evidence"
echo "PASS a live 600k call still widens the window"

# 18. Evidence is the LATEST call, not a maximum (Codex round-3 P2): a resume
#     into a smaller window mode can keep the SAME session id and the SAME
#     model id, so nothing in the transcript distinguishes it. A 600k call
#     earlier in this very session must not widen the window for a current
#     150k call — 150k is 75% of 200k and must fire.
python3 - "$WORK/resume.jsonl" <<'PYX'
import json, sys
def usage(tokens):
    return json.dumps({"sessionId": "cg-test-resume",
                       "message": {"model": "claude-opus-5",
                                   "usage": {"input_tokens": tokens,
                                             "cache_read_input_tokens": 0,
                                             "cache_creation_input_tokens": 0}}})
with open(sys.argv[1], "w") as f:
    f.write(usage(600000) + "\n")
    f.write(usage(150000) + "\n")
PYX
printf '{"transcript_path":"%s","session_id":"cg-test-resume","hook_event_name":"UserPromptSubmit"}' "$WORK/resume.jsonl" > "$WORK/in-resume.json"
OUT="$(run_guard "$WORK/in-resume.json")"
echo "$OUT" | grep -q "additionalContext" || fail "kept a pre-resume observation as window evidence"
echo "PASS evidence is the latest call, not a maximum"

echo "all context-guard tests passed"
