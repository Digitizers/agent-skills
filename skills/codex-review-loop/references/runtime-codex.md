# Codex local review adapter

Use the native branch-review capability offered by the current Codex host, or the documented `codex review --base <default-branch>` CLI when available. Check the installed CLI help before selecting flags. Do not invoke Claude plugin slash commands or plugin internals in Codex.

If an independent local reviewer is unavailable, disclose that Round 0 was skipped and use the existing GitHub review loop. A self-check by the implementing session is useful but is not an independent reviewer. Keep the existing ten-minute bound for an optional local review.

A GitHub connector may replace `gh` only when it can collect complete reviews, inline comments and issue comments with the same IDs, bodies and commit anchors. An incomplete or failed collection stays pending. Preserve the convergence rules in SKILL.md; a local clean result never replaces the cloud verdict.
