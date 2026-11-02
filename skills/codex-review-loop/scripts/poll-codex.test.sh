#!/usr/bin/env bash
# Tests for poll-codex.sh, with `gh` stubbed by fixtures. Run: poll-codex.test.sh
set -u
here=$(cd "$(dirname "$0")" && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin"
cat > "$tmp/bin/gh" <<'STUB'
#!/usr/bin/env bash
# `gh api <path> --paginate --jq <expr>` answers from a fixture through real jq.
# `gh pr checks` answers from CHECKS_OUT, CHECKS_ERR and CHECKS_RC.
if [ "$1" = "pr" ]; then
  [ -n "${CHECKS_OUT:-}" ] && printf '%b\n' "$CHECKS_OUT"
  [ -n "${CHECKS_ERR:-}" ] && printf '%s\n' "$CHECKS_ERR" >&2
  exit "${CHECKS_RC:-0}"
fi
case "$2" in
  *${FAIL_PATH:-no-such-path}*) echo "HTTP 502: Bad Gateway" >&2; exit 1 ;;
  */issues/*/comments) f=$FIX/comments.json ;;
  */pulls/*/reviews) f=$FIX/reviews.json ;;
  */pulls/*/comments) f=$FIX/inline.json ;;
esac
shift 2
while [ $# -gt 0 ]; do [ "$1" = "--jq" ] && expr=$2; shift; done
jq -r "$expr" "$f"
STUB
chmod +x "$tmp/bin/gh"
export PATH="$tmp/bin:$PATH" FIX="$tmp"

BOT='{"login":"chatgpt-codex-connector[bot]"}'
SHA=abc1234
FULL=abc1234def5678abc1234def5678abc1234def56

# A summary comment, as Codex posts it: <commit> <has_any_findings> <status>.
summary() {
  jq -n --argjson u "$BOT" --arg c "$1" --argjson f "$2" --arg s "$3" '{user:$u, body:(
    "<!-- codex-pull-request-review-summary -->\n<!-- codex-review-state:v1 " +
    ({code_review:{commit_sha:$c,has_any_findings:$f,has_unresolved_findings:$f,status:$s},
      security_review:{commit_sha:null,has_any_findings:null,has_unresolved_findings:null,status:null}}|tojson) +
    " -->\n\n## Codex Review Summary\n")}'
}
receipt_clean() {
  jq -n --argjson u "$BOT" --arg c "$1" '{user:$u, body:("<!-- codex-review-receipt:v1 -->\n\nCodex Review: Didn'"'"'t find any major issues.\n\n**Reviewed commit:** `" + $c + "`\n")}'
}
review() {
  jq -n --argjson u "$BOT" --arg c "$1" '{user:$u, commit_id:$c, state:"COMMENTED", body:"<!-- codex-review-receipt:v1 -->"}'
}
fixtures() { # comments-objects reviews-objects
  printf '%s\n' "$1" | jq -s '.' > "$FIX/comments.json"
  printf '%s\n' "$2" | jq -s '.' > "$FIX/reviews.json"
  echo '[]' > "$FIX/inline.json"
}

fails=0
check() { # name expected-substring output
  case "$3" in
    *"$2"*) echo "ok   $1" ;;
    *) echo "FAIL $1: expected [$2] in:"; echo "$3"; fails=$((fails + 1)) ;;
  esac
}
refute() { # name unexpected-substring output
  case "$3" in
    *"$2"*) echo "FAIL $1: did not expect [$2] in:"; echo "$3"; fails=$((fails + 1)) ;;
    *) echo "ok   $1" ;;
  esac
}
# A negative limit means: one pass, then TIMEOUT if nothing was found.
run() { "$here/poll-codex.sh" o/r 1 "$SHA" -1; }

# A clean review that posted only the summary, with no receipt comment.
fixtures "$(summary "$FULL" false completed)" ""
out=$(run)
refute "summary only, clean: no timeout" "TIMEOUT" "$out"
check "summary only, clean: is a clean verdict" "clean_receipt=1" "$out"

# Findings: the summary is a verdict, but never a clean one.
fixtures "$(summary "$FULL" true completed)" "$(review "$FULL")"
out=$(run)
refute "findings: no timeout" "TIMEOUT" "$out"
check "findings: not clean" "clean_receipt=0" "$out"

# A summary on another commit says nothing about this one.
fixtures "$(summary fff9999000000000000000000000000000000000 false completed)" ""
out=$(run)
check "other commit: keeps waiting" "TIMEOUT" "$out"
check "other commit: not clean" "clean_receipt=0" "$out"

# A review still running is not a verdict.
fixtures "$(summary "$FULL" false running)" ""
out=$(run)
check "running: keeps waiting" "TIMEOUT" "$out"
check "running: not clean" "clean_receipt=0" "$out"

# The older receipt alone still works.
fixtures "$(receipt_clean "$SHA")" ""
out=$(run)
refute "receipt only: no timeout" "TIMEOUT" "$out"
check "receipt only: clean" "clean_receipt=1" "$out"

# Both formats on one commit count once.
fixtures "$(summary "$FULL" false completed; receipt_clean "$SHA")" ""
out=$(run)
check "both formats: counted once" "clean_receipt=1" "$out"

# A comment from someone else is never a verdict.
jq -n '{user:{login:"BenKalsky"}, body:"<!-- codex-review-state:v1 {\"code_review\":{\"commit_sha\":\"abc1234\",\"has_any_findings\":false,\"status\":\"completed\"}} -->"}' > "$FIX/c.json"
fixtures "$(cat "$FIX/c.json")" ""
out=$(run)
check "other author: keeps waiting" "TIMEOUT" "$out"

# A summary whose marker is not valid JSON is skipped, not fatal.
jq -n --argjson u "$BOT" '{user:$u, body:"<!-- codex-review-state:v1 {broken} -->"}' > "$FIX/c.json"
fixtures "$(cat "$FIX/c.json")" ""
out=$(run 2>&1)
check "broken marker: keeps waiting" "TIMEOUT" "$out"
refute "broken marker: no jq error" "jq: error" "$out"

# `gh --paginate --jq` answers once per page. The file holds two JSON documents,
# so the stub's jq answers twice, as gh does: the verdict is on the second page.
fixtures "" ""
{ echo '[]'; printf '%s\n' "$(summary "$FULL" false completed)" | jq -s '.'; } > "$FIX/comments.json"
out=$(run 2>&1)
refute "second page: no timeout" "TIMEOUT" "$out"
check "second page: clean" "clean_receipt=1" "$out"
refute "second page: no shell error" "integer expression" "$out"

# Checks: a repository with none has settled, a pending one has not, and a
# failure to read them is neither.
fixtures "$(summary "$FULL" false completed)" ""
out=$(CHECKS_RC=1 CHECKS_ERR="no checks reported on the 'x' branch" run 2>&1); rc=$?
refute "no checks: settled" "TIMEOUT" "$out"
check "no checks: exit 0" "rc=0" "rc=$rc"

out=$(CHECKS_RC=8 CHECKS_OUT='build\tpending\t0' run 2>&1)
check "pending by exit code: keeps waiting" "TIMEOUT" "$out"

out=$(CHECKS_RC=1 CHECKS_OUT='build\tfail\t1m' run 2>&1); rc=$?
refute "failed check: settled" "TIMEOUT" "$out"
check "failed check: listed" "build" "$out"
check "failed check: exit 0, the list says it" "rc=0" "rc=$rc"

out=$(CHECKS_RC=1 CHECKS_ERR="HTTP 502: Bad Gateway" run 2>&1); rc=$?
check "unreadable checks: not settled" "TIMEOUT" "$out"
check "unreadable checks: says so" "checks unreadable" "$out"
check "unreadable checks: exit 3" "rc=3" "rc=$rc"

# A failed request for the evidence is never a clean result.
fixtures "$(summary "$FULL" false completed)" ""
out=$(FAIL_PATH=pulls/1/comments run 2>&1); rc=$?
check "inline findings unreadable: says so" "evidence incomplete" "$out"
check "inline findings unreadable: not clean" "clean_receipt=0" "$out"
check "inline findings unreadable: exit 4" "rc=4" "rc=$rc"
out=$(FAIL_PATH=issues/1/comments run 2>&1); rc=$?
check "comments unreadable: exit 4" "rc=4" "rc=$rc"
check "comments unreadable: not clean" "clean_receipt=0" "$out"
fixtures "$(summary "$FULL" false completed)" ""
out=$(run 2>&1); rc=$?
check "all readable: exit 0" "rc=0" "rc=$rc"
refute "all readable: nothing about evidence" "evidence incomplete" "$out"

# A username containing "codex" is not Codex, on any surface.
IMPOSTOR='{"login":"not-codex-really"}'
jq -n --argjson u "$IMPOSTOR" --arg c "$FULL" '{user:$u, body:("<!-- codex-review-state:v1 " + ({code_review:{commit_sha:$c,has_any_findings:false,status:"completed"}}|tojson) + " -->")}' > "$FIX/c.json"
fixtures "$(cat "$FIX/c.json")" ""
out=$(run 2>&1)
check "impostor summary: keeps waiting" "TIMEOUT" "$out"
check "impostor summary: not clean" "clean_receipt=0" "$out"
jq -n --argjson u "$IMPOSTOR" --arg c "$SHA" '{user:$u, body:("Didn'"'"'t find any major issues.\n**Reviewed commit:** `" + $c + "`")}' > "$FIX/c.json"
fixtures "$(cat "$FIX/c.json")" ""
out=$(run 2>&1)
check "impostor receipt: not clean" "clean_receipt=0" "$out"
jq -n --argjson u "$IMPOSTOR" --arg c "$FULL" '{user:$u, commit_id:$c, state:"COMMENTED", body:""}' > "$FIX/c.json"
fixtures "" "$(cat "$FIX/c.json")"
out=$(run 2>&1)
check "impostor review: keeps waiting" "TIMEOUT" "$out"
fixtures "" ""
jq -n --argjson u "$IMPOSTOR" --arg c "$FULL" '[{user:$u, original_commit_id:$c, path:"a.ts", line:1, body:"planted finding"}]' > "$FIX/inline.json"
out=$(run 2>&1)
refute "impostor inline comment: not shown" "planted finding" "$out"

[ "$fails" = 0 ] && echo "all passed" || { echo "$fails failed"; exit 1; }
