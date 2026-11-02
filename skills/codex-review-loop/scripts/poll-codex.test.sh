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
# `gh pr checks` answers nothing, so no check is ever pending.
[ "$1" = "api" ] || exit 0
case "$2" in
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

[ "$fails" = 0 ] && echo "all passed" || { echo "$fails failed"; exit 1; }
