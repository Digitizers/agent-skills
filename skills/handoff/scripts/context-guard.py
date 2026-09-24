#!/usr/bin/env python3
"""context-guard — Claude Code hook: nudge the agent to run the handoff
skill once the context window crosses a usage threshold.

Works as a UserPromptSubmit and/or PostToolUse hook. Reads the hook input
JSON on stdin, measures token usage from the session transcript, and when
usage >= threshold prints hookSpecificOutput.additionalContext telling the
agent to invoke the handoff skill. Fires once per session per window
(marker file): a session that moves to a model with a different declared or
proven window can be warned again, because that is a new crossing.

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
  the window grows to the smallest known tier that fits it. The evidence is
  the **most recent call only**, never a historical maximum: a transcript
  outlives the settings it was written under (a resume can change the model,
  or the same model's window mode, under the same session id), and evidence
  that outlives its window is the original lie with the sign flipped — the
  guard goes quiet at the real ceiling. The latest call cannot outlive
  anything: whatever context it carried, the window in force right now is at
  least that big.

One thing the transcript cannot do (#40): prove a large window EARLY. A 1M
session is indistinguishable from a 200k one until a call passes 200k, and the
70% threshold of the 200k default (140k) always lands inside that blind zone —
one false alarm per 1M session, guaranteed. The model id does not settle it
either: the same id runs in more than one window mode, so a table of "1M
models" baked in here would be a guess that goes stale. `CONTEXT_WINDOW_BY_MODEL`
lets the operator state it instead, keyed by the model of the LATEST assistant
line (latest for the same reason as the evidence above). And when the window is
neither stated nor proven, the message says "assumed" so the reader can check
it rather than obey it.

Env:
  HANDOFF_THRESHOLD_PCT     default 70
  CONTEXT_WINDOW_TOKENS     default 200000 (a floor — see above)
  CONTEXT_WINDOW_BY_MODEL   "model-id=tokens,model-id=tokens" — a per-model
                            floor; beats CONTEXT_WINDOW_TOKENS for that model
"""
import hashlib
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
    """Widen a configured window that the latest call has already disproved."""
    if observed <= configured:
        return configured
    for tier in WINDOW_TIERS:
        if tier >= observed and tier > configured:
            return tier
    return observed


def parse_model_windows(raw: str) -> dict:
    """"model=tokens,model=tokens" -> {model: tokens}. Malformed or
    non-positive entries are dropped one by one: this runs on every prompt,
    so a typo in settings must never break the session."""
    windows = {}
    for entry in raw.split(","):
        model, sep, value = entry.partition("=")
        model, value = model.strip(), value.strip()
        if not sep or not model:
            continue
        try:
            tokens = int(value)
        except ValueError:
            continue
        if tokens > 0:
            windows[model] = tokens
    return windows


def main() -> None:
    inp = json.load(sys.stdin)
    transcript = inp.get("transcript_path") or ""
    session_id = inp.get("session_id") or "unknown"
    event = inp.get("hook_event_name") or "UserPromptSubmit"

    threshold = float(os.environ.get("HANDOFF_THRESHOLD_PCT", "70"))
    configured = os.environ.get("CONTEXT_WINDOW_TOKENS")
    window = int(configured or "200000")
    model_windows = parse_model_windows(
        os.environ.get("CONTEXT_WINDOW_BY_MODEL", "")
    )

    marker = os.path.join(tempfile.gettempdir(), f"handoff-guard-{session_id}")
    # An alarm raised against an ASSUMED window tells the agent the figure may
    # be a guess, so it must not disarm the guard for the rest of the session
    # (Codex r1 on #41): it gets its own marker, and the session marker is
    # written only by an alarm on a declared or proven window.
    # Keyed by the active model too (Codex r2): after a resume onto another
    # unmapped model, the earlier model's assumed alarm must not silence it.
    if not transcript or not os.path.exists(transcript):
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
    # Window evidence is the LATEST call's context, never a maximum over the
    # transcript. Nothing in a transcript states the window size, and every
    # attempt to keep an older observation alive needs an identity the file
    # does not carry: a resume can change the model, or the same model's window
    # mode, and it may reuse the session id while doing so. So no historical
    # observation can be trusted to describe the window in force now — but the
    # most recent call always does, because it fit.
    latest_context = 0
    # Same rule for the model: the latest assistant line's, never any line's.
    latest_model = ""
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
            model = message.get("model")
            # "<synthetic>" marks a locally generated message, not a model.
            if isinstance(model, str) and model and not model.startswith("<"):
                latest_model = model
            usage = message.get("usage")
            if usage:
                latest_context = context_sent(usage)
                tokens = latest_context + usage.get("output_tokens", 0)
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

    declared = model_windows.get(latest_model)
    if declared:
        window = declared
    floor = window
    window = fit_window(window, latest_context)
    # Nobody stated this window and no call has proven it: it is a default.
    assumed = not declared and not configured and window == floor

    # Every marker names the window it was raised against (Codex r3 on #44):
    # a tier inferred from evidence is only a lower bound, so when a later call
    # proves a wider window the guard must be able to fire again against it.
    # A declared or configured window never changes, so it still fires once.
    # Any widening is inferred — including one that disproved a stale
    # declaration (Codex r5): only the tier's lower bound is known.
    inferred = window > floor
    # An inferred alarm names its model too (Codex r4): its lower-bound tier
    # must not share a marker with a declared or configured window of the
    # same size after a resume onto another model.
    # Model ids are hashed, not sanitised: `provider/a` and `provider_a` must
    # not share a marker (Codex r5).
    model_key = hashlib.sha256(latest_model.encode("utf-8")).hexdigest()[:16]
    provenance = f"inferred-{model_key}" if inferred else "known"
    # Above the largest known tier fit_window() returns the observed context
    # itself, which grows every call — key it as one bucket, or every hook
    # would alarm again (Codex r6).
    window_key = f"w{window}" if window <= WINDOW_TIERS[-1] else f"above-w{WINDOW_TIERS[-1]}"
    window_marker = f"{marker}-{provenance}-{window_key}"
    if not assumed and os.path.exists(window_marker):
        return
    assumed_marker = f"{marker}-assumed-{model_key}"
    if assumed and os.path.exists(assumed_marker):
        return

    raw_tokens = tokens
    pct = tokens * 100.0 / window
    if pct < threshold:
        return
    # The tail/payload estimate is a deliberate over-count, so a number above
    # 100% is an artefact of the floor, not a measurement. Report the fact
    # (past the threshold) without the impossible figure.
    pct = min(pct, 100.0)
    tokens = min(tokens, window)

    open(assumed_marker if assumed else window_marker, "w").close()
    msg = (
        f"Context window is at ~{pct:.0f}% of {window} tokens (~{tokens} "
        f"used, estimated), past the {threshold:.0f}% handoff threshold. "
    )
    if inferred:
        msg += (
            f"{window} is the smallest window this transcript proves (a lower "
            "bound — the real window may be larger; set CONTEXT_WINDOW_BY_MODEL "
            "to state it). "
        )
    if assumed:
        # An agent once obeyed "70% of 200000" on a 1M session (#40). Say what
        # is not known, and name the setting that settles it.
        # Never "this alarm is false" (Codex r1 on #41): the estimate can
        # be large enough to matter on the larger window too, so give the
        # figure against it and let the agent judge.
        largest = WINDOW_TIERS[-1]
        msg += (
            f"That {window} is an assumed default — neither configured nor "
            "proven by this transcript — so this may be a false alarm: the "
            f"same ~{min(raw_tokens, largest)} tokens are "
            f"~{min(raw_tokens * 100.0 / largest, 100.0):.0f}% "
            f"of a {largest}-token window. If this model's window is larger, "
            "judge against it, and set CONTEXT_WINDOW_BY_MODEL or "
            "CONTEXT_WINDOW_TOKENS so the guard knows. The guard stays armed "
            "for a declared or proven window. If the window really is "
            f"{window}: "
        )
    msg += (
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
