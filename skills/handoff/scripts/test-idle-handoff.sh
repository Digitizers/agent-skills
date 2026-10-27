#!/usr/bin/env bash
# Regression tests for idle-handoff.sh.
set -euo pipefail
unset IDLE_HANDOFF IDLE_HANDOFF_MINUTES IDLE_HANDOFF_MIN_TOKENS HANDOFF_UNATTENDED
SCRIPT="$(cd "$(dirname "$0")" && pwd)/idle-handoff.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
export IDLE_HANDOFF_STATE_DIR="$WORK/state" CLAUDE_CODE_SESSION_ATTENDED=1
fail() { echo "FAIL: $1" >&2; exit 1; }

transcript() { # $1=file $2=context tokens of the latest call
  printf '{"type":"assistant","message":{"usage":{"input_tokens":2,"cache_read_input_tokens":%s,"cache_creation_input_tokens":0}}}\n' "$2" > "$1"
}
prompt() { # $1=session $2=prompt text $3=transcript
  python3 -c 'import json,sys; print(json.dumps({"hook_event_name":"UserPromptSubmit","session_id":sys.argv[1],"prompt":sys.argv[2],"transcript_path":sys.argv[3]}))' "$1" "$2" "$3" \
    | bash "$SCRIPT" || fail "hook exited non-zero"
}
age() { # $1=session $2=minutes since the last user prompt
  python3 - "$WORK/state/idle-$1.json" "$2" <<'PY'
import json, sys, time
p, m = sys.argv[1], float(sys.argv[2])
d = json.load(open(p)); d["last_prompt_at"] = time.time() - m * 60
json.dump(d, open(p, "w"))
PY
}
job() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("job",""))' "$WORK/state/idle-$1.json"; }

transcript "$WORK/big.jsonl" 300000
transcript "$WORK/small.jsonl" 20000

# 1. First user prompt -> arm instructions naming CronCreate and the check tag
OUT="$(prompt s1 "hello" "$WORK/big.jsonl")"
echo "$OUT" | grep -q CronCreate || fail "no arm instructions"
echo "$OUT" | grep -q 'idle-handoff-check' || fail "arm prompt lacks the check tag"
echo "PASS first prompt arms"

# 2. Once armed, prompts are silent
bash "$SCRIPT" armed s1 job-1
[ -z "$(prompt s1 "next" "$WORK/big.jsonl")" ] || fail "armed session got instructions again"
echo "PASS armed is silent"

# 3. Job-enqueued prompts do not count as user activity
age s1 60
prompt s1 "[idle-handoff-check] Run: ..." "$WORK/big.jsonl" >/dev/null
bash "$SCRIPT" check s1 | grep -q '^WRITE' || fail "check prompt reset the idle clock"
echo "PASS check prompt is not activity"

# 4. Recent user activity -> WAIT
age s1 10
bash "$SCRIPT" check s1 | grep -q '^WAIT' || fail "fired while the user was active"
echo "PASS active waits"

# 5. Idle and large -> WRITE naming the job; done -> STOP and re-arm on next prompt
age s1 50
V="$(bash "$SCRIPT" check s1)"
echo "$V" | grep -q '^WRITE' || fail "no WRITE after 50 idle minutes"
echo "$V" | grep -q 'job-1' || fail "WRITE does not name the job"
bash "$SCRIPT" done s1
bash "$SCRIPT" check s1 | grep -q '^STOP' || fail "second handoff in the same idle period"
prompt s1 "back" "$WORK/big.jsonl" | grep -q CronCreate || fail "not re-armed after the handoff"
echo "PASS write once, then re-arm"

# 6. Idle but small -> STOP and forget the job
prompt s2 "hi" "$WORK/small.jsonl" >/dev/null
bash "$SCRIPT" armed s2 job-2
age s2 50
bash "$SCRIPT" check s2 | grep -q '^STOP' || fail "small context did not stop"
[ -z "$(job s2)" ] || fail "STOP kept the job"
echo "PASS small context stops"

# 7. Restart forgets the in-memory job
bash "$SCRIPT" armed s2 job-3
echo '{"hook_event_name":"SessionStart","session_id":"s2","source":"resume"}' | bash "$SCRIPT"
[ -z "$(job s2)" ] || fail "resume kept a job that died with the process"
echo '{"hook_event_name":"SessionStart","session_id":"s2","source":"compact"}' | bash "$SCRIPT"
echo "PASS restart unarms"

# 8. Off switch, headless runs and garbage input are silent
[ -z "$(IDLE_HANDOFF=0 prompt s3 "x" "$WORK/big.jsonl")" ] || fail "IDLE_HANDOFF=0 armed"
[ -z "$(CLAUDE_CODE_SESSION_ATTENDED=0 prompt s3 "x" "$WORK/big.jsonl")" ] || fail "headless run armed"
[ -z "$(echo 'not json' | bash "$SCRIPT")" ] || fail "garbage produced output"
echo "PASS silent cases"

# 9. Bad command usage exits 2
set +e; bash "$SCRIPT" armed s1 >/dev/null 2>&1; rc=$?; set -e
[ "$rc" = 2 ] || fail "armed without a job id exited $rc"
echo "PASS usage errors"

# 10. Generated commands survive a path with a space
SPACED="$WORK/agent skills"; mkdir -p "$SPACED"
cp "$(dirname "$SCRIPT")"/idle-handoff.py "$(dirname "$SCRIPT")"/handoff_common.py "$SPACED"/
OUT="$(python3 -c 'import json,sys; print(json.dumps({"hook_event_name":"UserPromptSubmit","session_id":"s4","prompt":"hi","transcript_path":sys.argv[1]}))' "$WORK/big.jsonl" | python3 "$SPACED/idle-handoff.py")"
CMD="$(printf '%s' "$OUT" | python3 -c 'import json,sys,re; c=json.load(sys.stdin)["hookSpecificOutput"]["additionalContext"]; print(re.search(r"`([^`]*) <job id>`", c).group(1))')"
eval "$CMD job-4" || fail "generated armed command failed under a spaced path"
[ "$(job s4)" = job-4 ] || fail "spaced-path armed command did not record the job"
echo "PASS spaced install path"

echo "ALL PASS"
