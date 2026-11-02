#!/usr/bin/env bash
# Wait until Codex has reviewed <sha7> on a PR and CI has settled, then print
# the verdict, the checks and any inline findings on that commit.
# A verdict is a receipt comment, a review on the commit, or (when a clean
# review posts nothing else) the summary's completed codex-review-state marker.
# Usage: poll-codex.sh <owner/repo> <pr> <sha7> [timeout_seconds]
# Tests: poll-codex.test.sh
set -u
repo=$1; pr=$2; sha=$3; limit=${4:-1800}
start=$(date +%s)
codex_comments='[.[]|select(.user.login|test("codex"))'
# The code_review states of the summary comments for this commit, finished only.
state="$codex_comments|.body|(try capture(\"codex-review-state:v1 (?<j>\\\\{.*\\\\}) -->\") catch null)|select(.)|(try (.j|fromjson|.code_review) catch null)|select(.)|select((.commit_sha // \"\")|startswith(\"$sha\"))|select(.status==\"completed\")]"
while :; do
  receipt=$(gh api "repos/$repo/issues/$pr/comments" --paginate \
    --jq "$codex_comments|select(.body|test(\"Reviewed commit:\\\\*\\\\* \`$sha\"))]|length")
  summary=$(gh api "repos/$repo/issues/$pr/comments" --paginate --jq "$state|length")
  reviews=$(gh api "repos/$repo/pulls/$pr/reviews" --paginate \
    --jq "[.[]|select(.user.login|test(\"codex\"))|select(.commit_id|startswith(\"$sha\"))]|length")
  pending=$(gh pr checks "$pr" -R "$repo" 2>/dev/null | awk -F'\t' '$2=="pending"' | wc -l | tr -d ' ')
  if { [ "$receipt" -gt 0 ] || [ "$reviews" -gt 0 ] || [ "$summary" -gt 0 ]; } && [ "$pending" = 0 ]; then break; fi
  if [ $(( $(date +%s) - start )) -gt "$limit" ]; then echo "TIMEOUT receipt=$receipt summary=$summary reviews=$reviews pending=$pending"; break; fi
  sleep 30
done
clean_receipts=$(gh api "repos/$repo/issues/$pr/comments" --paginate \
  --jq "$codex_comments|select(.body|test(\"Reviewed commit:\\\\*\\\\* \`$sha\"))|select(.body|test(\"Didn't find any major issues\"))]|length")
clean_summaries=$(gh api "repos/$repo/issues/$pr/comments" --paginate --jq "$state|map(select(.has_any_findings==false))|length")
clean=0
if [ "$clean_receipts" -gt 0 ] || [ "$clean_summaries" -gt 0 ]; then clean=1; fi
echo "head=$sha codex_reviews=$reviews clean_receipt=$clean"
gh pr checks "$pr" -R "$repo" 2>/dev/null | cut -f1,2
gh api "repos/$repo/pulls/$pr/comments" --paginate \
  --jq ".[]|select(.user.login|test(\"codex\"))|select(.original_commit_id|startswith(\"$sha\"))|\"\(.path):\(.line // .original_line) \(.body)\""
