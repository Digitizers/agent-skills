# Codex handoff adapter

Use the same handoff modes, content contract and gate as the shared skill. Store the document under the current Codex home in `handoffs/<project-slug>/`, outside a repository, and link the absolute persistent path in the reply. Do not overwrite an earlier handoff.

A handoff transfers state; it does not authorize another chat to start or send messages. Use chat coordination only when the user authorized that action. Record the exact branch, commit, uncommitted state and verification results needed by the next session.

Do not install the Claude context-guard hook into Codex or parse Codex transcripts as Claude JSONL. A hook or percentage is usable only when this host documents and supplies it. Without a trustworthy context signal, respond to the user's handoff request or the host's compaction notice; do not invent remaining context or quota. Existing Claude hook behavior is unchanged.
