#!/usr/bin/env python3
"""context-guard — Claude Code hook: nudge the agent to run the handoff
skill once the context window crosses a usage threshold.

Works as a UserPromptSubmit and/or PostToolUse hook. Reads the hook input
JSON on stdin, measures token usage from the session transcript, and when
usage >= threshold prints hookSpecificOutput.additionalContext telling the
agent to invoke the handoff skill. Fires once per session (marker file).

The hook JSON is read from stdin, never argv: a large prompt or
tool_response would exceed the OS per-argument limit ("Argument list too
long") exactly on the large context-growing operations this hook exists to
catch.

Two things keep the measurement honest across a restart (#35):

* A `system` / `compact_boundary` record means everything before it left the
  context window. Baseline and tail are reset there, so the first hook event
  after an auto-compact prices the compacted conversation, not the 600k one
  it replaced.
* The window size is evidence-led. `CONTEXT_WINDOW_TOKENS` is a floor, not a
  fact: a session on a 1M-context model whose settings still say 200000 was
  reported at ">= 300%". The largest context any single call in this
  transcript actually carried is a proven lower bound on the real window, so
  the window grows to the smallest known tier that fits it. That evidence is
  kept **per model** and read for the model of the most recent call: a
  session that switches from a 1M model to a 200k one must not keep the 1M
  denominator, or the guard goes quiet at the smaller model's ceiling.

Env:
  HANDOFF_THRESHOLD_PCT   default 70
  CONTEXT_WINDOW_TOKENS   default 200000 (a floor — see above)
"""
import json
import os
import sys
import tempfile

# Estimation policy: a conservative FLOOR, not an average — UTF-8 bytes / 2.
# Character-class ratios (prose ~4 chars/token, Hebrew ~2, base64 ~2-2.7,
# CJK ~1, multi-token emoji) lose to the next token-dense counterexample by
# construction; byte length dominates them all: ASCII counts 2 chars/token,
# Hebrew/Arabic/Cyrillic 1:1 per char, CJK ~0.67 chars/token, emoji ~2
# tokens per code point. Prose overcounts ~2x — on the tail/payload only,
# the usage-block baseline stays exact — so the nudge fires early, never
# late. The one residual undercount (tokenizer byte-fallback on pathological
# input, up to 1 token/byte) is accepted by design because it self-heals:
# the once-per-session marker is written only when the nudge fires, and the
# next hook event reads a usage block that prices this content exactly.


# Known context-window tiers, ascending. A transcript proves the window is at
# least as large as the biggest context a single call carried; the real window
# is then the smallest tier that fits that evidence.
WINDOW_TIERS = (200_000, 500_000, 1_000_000)


def estimate_tokens(text: str) -> int:
    return len(text.encode("utf-8", "replace")) // 2


def context_sent(usage: dict) -> int:
    """Tokens sent INTO a call — its own output excluded. This is the part
    bounded by the context window, so it is what proves the window's size."""
    return (
        usage.get("input_tokens", 0)
        + usage.get("cache_read_input_tokens", 0)
        + usage.get("cache_creation_input_tokens", 0)
    )


def fit_window(configured: int, observed: int) -> int:
    """Widen a configured window that the transcript has already disproved."""
    if observed <= configured:
        return configured
    for tier in WINDOW_TIERS:
        if tier >= observed and tier > configured:
            return tier
    return observed


def main() -> None:
    inp = json.load(sys.stdin)
    transcript = inp.get("transcript_path") or ""
    session_id = inp.get("session_id") or "unknown"
    event = inp.get("hook_event_name") or "UserPromptSubmit"

    threshold = float(os.environ.get("HANDOFF_THRESHOLD_PCT", "70"))
    window = int(os.environ.get("CONTEXT_WINDOW_TOKENS", "200000"))

    marker = os.path.join(tempfile.gettempdir(), f"handoff-guard-{session_id}")
    if os.path.exists(marker) or not transcript or not os.path.exists(transcript):
        return

    # The latest assistant usage block counts only the context sent INTO that
    # model call — it excludes that call's own output_tokens and anything
    # appended to the transcript since (new user prompt, tool results). A
    # large tail can cross the threshold silently, so estimate it with the
    # byte floor above and add the current hook payload the same way.
    # Double counting between tail and payload only makes the nudge
    # fire earlier, never later — the safe direction for a handoff reminder.
    tokens = 0
    tail_tokens = 0
    # Window evidence is a fact about a MODEL, not about a conversation: it
    # survives compaction, but it does not transfer to a different model. A
    # session that drops from a 1M model to a 200k one would otherwise keep
    # the 1M denominator and stay silent at the smaller model's ceiling.
    # Records with no model share one bucket, so a transcript that never
    # names a model still accumulates evidence normally.
    observed_by_model = {}
    last_model = ""
    with open(transcript, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                tail_tokens += estimate_tokens(line)
                continue
            if rec.get("subtype") == "compact_boundary":
                # Everything above this line is gone from the window. Keeping
                # it is the restart lie: the pre-compact usage block priced a
                # context that no longer exists.
                tokens = 0
                tail_tokens = 0
                continue
            message = rec.get("message") or {}
            usage = message.get("usage")
            if usage:
                last_model = message.get("model") or ""
                observed_by_model[last_model] = max(
                    observed_by_model.get(last_model, 0), context_sent(usage)
                )
                tokens = context_sent(usage) + usage.get("output_tokens", 0)
                tail_tokens = 0
            else:
                tail_tokens += estimate_tokens(line)

    payload_tokens = estimate_tokens(inp.get("prompt") or "")
    tool_response = inp.get("tool_response")
    if tool_response is not None:
        payload_tokens += estimate_tokens(
            json.dumps(tool_response, ensure_ascii=False)
        )

    tokens += tail_tokens + payload_tokens

    window = fit_window(window, observed_by_model.get(last_model, 0))

    pct = tokens * 100.0 / window
    if pct < threshold:
        return
    # The tail/payload estimate is a deliberate over-count, so a number above
    # 100% is an artefact of the floor, not a measurement. Report the fact
    # (past the threshold) without the impossible figure.
    pct = min(pct, 100.0)
    tokens = min(tokens, window)

    open(marker, "w").close()
    msg = (
        f"Context window is at ~{pct:.0f}% of {window} tokens (~{tokens} "
        f"used, estimated), past the {threshold:.0f}% handoff threshold. "
        "Invoke the handoff skill NOW to write a handoff document before "
        "context is compacted, then continue the current task."
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": msg,
        }
    }))


if __name__ == "__main__":
    main()
