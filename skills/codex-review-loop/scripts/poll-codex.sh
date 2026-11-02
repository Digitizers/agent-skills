#!/usr/bin/env bash
# Wait until Codex has reviewed <sha7> on a PR and CI has settled, then print
# the verdict, the checks and any inline findings on that commit.
# A verdict is a receipt comment, a review on the commit, or (when a clean
# review posts nothing else) the summary's completed codex-review-state marker.
# Usage: poll-codex.sh <owner/repo> <pr> <sha7> [timeout_seconds]
# Exit 3 when the checks could not be read, so CI's state is unknown.
# Exit 4 when a request for the review evidence failed, so it is incomplete.
# Tests: poll-codex.test.sh
set -u -o pipefail
repo=$1; pr=$2; sha=$3; limit=${4:-1800}
start=$(date +%s)
codex_comments='[.[]|select(.user.login|test("codex"))'
# The code_review states of the summary comments for this commit, finished only.
state="$codex_comments|.body|(try capture(\"codex-review-state:v1 (?<j>\\\\{.*\\\\}) -->\") catch null)|select(.)|(try (.j|fromjson|.code_review) catch null)|select(.)|select((.commit_sha // \"\")|startswith(\"$sha\"))|select(.status==\"completed\")]"
receipts="$codex_comments|select(.body|test(\"Reviewed commit:\\\\*\\\\* \`$sha\"))"

# `--paginate --jq` answers once per page, so add the pages up. Fails when gh does.
count() { # <api path> <jq expression>
  gh api "$1" --paginate --jq "$2" | awk '{s+=$1} END{print s+0}'
}

# Sets checks_out, and checks_state to settled, pending or unreadable. A failed
# check is settled. A repository with no checks is settled. Anything else that
# exits nonzero with no rows is a failure to read them, never "nothing pending".
read_checks() {
  local err rc
  err=$(mktemp)
  checks_out=$(gh pr checks "$pr" -R "$repo" 2>"$err"); rc=$?
  if [ "$rc" = 8 ] || printf '%s\n' "$checks_out" | awk -F'\t' '$2=="pending"{f=1} END{exit !f}'; then
    checks_state=pending
  elif [ "$rc" = 0 ] || [ -n "$checks_out" ] || grep -q "no checks reported" "$err"; then
    checks_state=settled
  else
    checks_state=unreadable
  fi
  rm -f "$err"
}

while :; do
  receipt=$(count "repos/$repo/issues/$pr/comments" "$receipts]|length")
  summary=$(count "repos/$repo/issues/$pr/comments" "$state|length")
  reviews=$(count "repos/$repo/pulls/$pr/reviews" \
    "[.[]|select(.user.login|test(\"codex\"))|select(.commit_id|startswith(\"$sha\"))]|length")
  read_checks
  if { [ "$receipt" -gt 0 ] || [ "$reviews" -gt 0 ] || [ "$summary" -gt 0 ]; } && [ "$checks_state" = settled ]; then break; fi
  if [ $(( $(date +%s) - start )) -gt "$limit" ]; then echo "TIMEOUT receipt=$receipt summary=$summary reviews=$reviews checks=$checks_state"; break; fi
  sleep 30
done
# A failed request here means the evidence is incomplete, so it cannot be clean.
evidence=complete
clean_receipts=$(count "repos/$repo/issues/$pr/comments" "$receipts|select(.body|test(\"Didn't find any major issues\"))]|length") || evidence=incomplete
clean_summaries=$(count "repos/$repo/issues/$pr/comments" "$state|map(select(.has_any_findings==false))|length") || evidence=incomplete
reviews=$(count "repos/$repo/pulls/$pr/reviews" \
  "[.[]|select(.user.login|test(\"codex\"))|select(.commit_id|startswith(\"$sha\"))]|length") || evidence=incomplete
findings=$(gh api "repos/$repo/pulls/$pr/comments" --paginate \
  --jq ".[]|select(.user.login|test(\"codex\"))|select(.original_commit_id|startswith(\"$sha\"))|\"\(.path):\(.line // .original_line) \(.body)\"") || evidence=incomplete
clean=0
if [ "$evidence" = complete ] && { [ "$clean_receipts" -gt 0 ] || [ "$clean_summaries" -gt 0 ]; }; then clean=1; fi
echo "head=$sha codex_reviews=$reviews clean_receipt=$clean"
[ -n "$checks_out" ] && printf '%s\n' "$checks_out" | cut -f1,2
[ -n "$findings" ] && printf '%s\n' "$findings"
if [ "$evidence" = incomplete ]; then
  echo "evidence incomplete: a request for the review evidence failed"
  exit 4
fi
if [ "$checks_state" = unreadable ]; then
  echo "checks unreadable: CI state unknown"
  exit 3
fi
exit 0
