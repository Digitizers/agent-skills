#!/usr/bin/env python3
"""handoff-gate — refuse to deliver a handoff that would fail its reader.

The skill's rules ("no empty sections", "redact secrets") were instructions to
a model. This is the mechanical check: run it before telling the user the
handoff is ready, and fix whatever it names. A handoff that does not print
GATE: PASS is not delivered.
"""

import argparse
import os
import re
import sys
import zipfile

REQUIRED_SECTIONS = (
    "Project overview",
    "Details",
    "Suggested skills",
    "Current state",
    "Open issues",
    "What to do next",
)

# Per-mode extra requirements: (section heading or filename, human reason).
MODE_SECTIONS = {
    "compaction": (("Tried and rejected", "what compaction destroys first"),),
    "same-workspace": (("Tried and rejected", "what compaction destroys first"),),
    "cross-workspace": (
        ("Tried and rejected", "what compaction destroys first"),
        ("Setup", "the reader is on another machine"),
    ),
    "quota": (("Tried and rejected", "what compaction destroys first"),),
}
MODE_FILES = {
    "cross-workspace": ("PROMPT.txt",),
    "quota": ("PROMPT.txt",),
}

SECRET_PATTERNS = (
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"), "Anthropic API key"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "GitHub token"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "GitHub fine-grained token"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key id"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key"),
    (re.compile(r"\b[a-z+]+://[^/\s:@]+:[^/\s:@]+@"), "connection string with a password"),
)

# The generic "label = value" pattern is intentionally separate from
# SECRET_PATTERNS above: unlike the specific formats (sk-ant-, gh*_, AKIA...,
# a private-key block, a URL with an embedded password), it needs its value
# checked against PLACEHOLDER_RX before it counts as a finding — the skill's
# own instructions tell a handoff's author to write
# `BUNNY_API_KEY=REDACTED_DO_NOT_COMMIT`, keeping the credential's *name* and
# never its value, and that must not itself fail the gate.
#
# Fix-round-3 pin: the keyword used to require `\b` immediately before it, so
# `BUNNY_API_KEY=...` was invisible to this pattern — `_` is a word character,
# so there is no boundary between it and `API`, and every credential name in
# this project's own toolbox is `<VENDOR>_<THING>_KEY` or `<VENDOR>_TOKEN`.
# The keyword may now be preceded by any run of label characters
# (letters/digits/underscore/hyphen); nothing else about the pattern changed.
# This does not widen it into prose: the assignment character (`=` or `:`)
# must still follow the keyword directly (only whitespace between), so
# "the API key is stored in 1Password" and "secret: the build is slow" still
# don't match — there is no `[=:]` right after the keyword in the first, and
# the value after `:` in the second is only 3 characters, short of the
# pattern's 4-character minimum.
GENERIC_CRED_RX = re.compile(
    r"(?i)[A-Za-z0-9_-]*(?:password|secret|token|api[_-]?key)\s*[=:]\s*"
    r"(['\"]?)([A-Za-z0-9/+_<>{}$\[\].-]{4,})\1"
)

# Fix-round-2 pin: a placeholder must be recognisable as a WHOLE value, never
# as a prefix. The round-1 version ended every alternative in `\S*`, so a
# *real* secret that merely started with an allowlisted word — e.g.
# `TOKEN=your-actual-prod-db-password-Xk29fLp3Q7vZ` — matched `your[-_]?\S*`
# in full and was waved through. Every branch below is bounded: either every
# separator-delimited word of the value is itself one of a fixed list, or the
# whole value is a bracketed placeholder, a pure repetition mask, or one of a
# fixed set of canonical dummy strings matched exactly.
_REDACTION_WORDS = (
    "REDACTED", "REDACT", "OMITTED", "HIDDEN", "SANITIZED", "PLACEHOLDER",
    "EXAMPLE", "DUMMY", "FAKE", "CHANGEME", "TODO", "TBD", "NONE", "NULL",
    "EMPTY", "DO", "NOT", "COMMIT",
)
_WORD_ALT = "|".join(_REDACTION_WORDS)
_CANONICAL_DUMMIES = (
    "your-key-here", "your_key_here", "yourkeyhere",
    "paste-here", "paste_here", "insert-key-here",
)
_DUMMY_ALT = "|".join(re.escape(d) for d in _CANONICAL_DUMMIES)
PLACEHOLDER_RX = re.compile(
    r"(?i)^(?:"
    r"(?:" + _WORD_ALT + r")(?:[-_ .](?:" + _WORD_ALT + r"))*"  # REDACTED_DO_NOT_COMMIT
    r"|<[^<>]*>"                                                 # <anything>
    r"|\{\{[^{}]*\}\}"                                           # {{anything}}
    r"|\$\{[^{}]*\}"                                             # ${anything}
    r"|\[[^\[\]]*\]"                                             # [anything]
    r"|[xX]{3,}|\*{3,}|\.{3,}|0{3,}"                              # repetition masks
    r"|" + _DUMMY_ALT +                                          # canonical dummies, exact
    r")$"
)

# A URL is masked out before path-scanning: "https://github.com/org/repo/pull/9"
# starts a bare-path-looking match right after the "https:" scheme, since the
# bare-path alternative below only excludes a preceding word character or
# backtick, not a colon. Masking preserves length (so match spans in the
# unmasked text still line up) without touching path text that happens to sit
# in backticks next to a URL.
URL_RX = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://\S+")

# Paths the document mentions. Quoted in backticks or bare, absolute or ~-rooted.
PATH_RX = re.compile(r"`([^`\n]+)`|(?<![\w`])((?:~|/)[\w./~-]{3,})")


def iter_files(directory: str):
    """Every regular file under directory, recursively, in a stable order.

    A handoff directory scans every file in it — a secret or a zip is just as
    real in a nested `context/notes.md` as it is at the top level.
    """
    for root, dirs, files in os.walk(directory):
        dirs.sort()
        for name in sorted(files):
            full = os.path.join(root, name)
            yield os.path.relpath(full, directory), full


def sections(text: str) -> dict:
    out, current = {}, None
    for line in text.splitlines():
        if line.startswith("#"):
            current = line.lstrip("#").strip()
            # "Handoff — project (date)" and "Tools" are headings too; keep
            # them all, the required list decides which ones matter.
            out[current] = []
        elif current is not None:
            out[current].append(line)
    return {k: "\n".join(v).strip() for k, v in out.items()}


def check_paths(text: str, base: str) -> list:
    problems = []
    masked = URL_RX.sub(lambda m: "\0" * len(m.group()), text)
    for quoted, bare in PATH_RX.findall(masked):
        candidate = (quoted or bare).strip()
        if not candidate.startswith(("/", "~")):
            continue
        # A command, not a path: `git -C /repo status`.
        if " " in candidate:
            continue
        resolved = os.path.expanduser(candidate)
        if not os.path.exists(resolved):
            problems.append(f"path does not exist: {candidate}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--mode", required=True,
                    choices=sorted(MODE_SECTIONS))
    args = ap.parse_args()

    if os.path.isdir(args.path):
        directory = args.path
        doc = os.path.join(directory, "HANDOFF.md")
    else:
        directory = os.path.dirname(args.path) or "."
        doc = args.path

    problems = []
    if not os.path.exists(doc):
        print(f"GATE: FAIL — no handoff document at {doc}")
        return 1
    try:
        text = open(doc, "r", encoding="utf-8", errors="replace").read()
    except OSError as exc:
        print(f"GATE: FAIL — could not read {doc}: {exc}")
        return 1
    found = sections(text)

    for name in REQUIRED_SECTIONS:
        match = next((v for k, v in found.items() if k.lower() == name.lower()), None)
        if match is None:
            problems.append(f"missing section: {name}")
        elif not match:
            problems.append(f"empty section: {name}")

    for name, why in MODE_SECTIONS[args.mode]:
        if not any(k.lower() == name.lower() and v for k, v in found.items()):
            problems.append(f"missing section for {args.mode} mode: {name} ({why})")

    for filename in MODE_FILES.get(args.mode, ()):
        if not os.path.exists(os.path.join(directory, filename)):
            problems.append(f"missing file for {args.mode} mode: {filename}")

    steps = next((v for k, v in found.items()
                  if k.lower() == "what to do next"), "")
    if steps and not re.search(r"^\s*\d+[.)]\s+\S", steps, re.M):
        problems.append("What to do next is not a numbered list")

    problems.extend(check_paths(text, directory))

    if os.path.isdir(directory):
        all_files = list(iter_files(directory))
    else:
        all_files = [(os.path.basename(doc), doc)]

    # The zip is checked by entry NAME, never extracted: a gate that unpacks
    # an archive is a gate that can be made to write outside its directory.
    for rel, full in all_files:
        if not rel.endswith(".zip"):
            continue
        try:
            with zipfile.ZipFile(full) as zf:
                entries = zf.namelist()
        except (OSError, zipfile.BadZipFile):
            problems.append(f"unreadable archive: {rel}")
            continue
        for entry in entries:
            base = os.path.basename(entry.rstrip("/"))
            if base in (".env", ".npmrc", ".pypirc", "id_rsa", "id_ed25519") or \
                    base.startswith(".env."):
                problems.append(f"{rel} contains {entry} — credentials never travel in the zip")

    # Every file in the handoff directory is scanned, not just the document:
    # PROMPT.txt is the file most likely to be pasted into another account.
    for rel, full in all_files:
        if not os.path.isfile(full):
            continue
        try:
            body = open(full, "r", encoding="utf-8", errors="replace").read()
        except OSError as exc:
            # A file the gate could not read is indistinguishable from a
            # clean one unless it says so — that defeats the whole check.
            problems.append(f"unreadable file: {rel} ({exc}) — cannot confirm it holds no secrets")
            continue
        for pattern, label in SECRET_PATTERNS:
            if pattern.search(body):
                problems.append(f"{label} found in {rel} — redact the value, keep the name")
        for match in GENERIC_CRED_RX.finditer(body):
            value = match.group(2)
            if len(value) >= 12 and not PLACEHOLDER_RX.match(value):
                # Fix-round-4: this line is a HEURISTIC — any label ending in
                # password/secret/token/api_key with an assignment-shaped
                # value trips it, including benign non-secrets (a UUID
                # correlation id, a 40-hex build hash). Its wording says so
                # and gives both ways out, and its leading phrase is
                # deliberately distinct from the specific-pattern message
                # below, so a reader scanning GATE: FAIL lines can tell
                # "this is definitely a key" from "this looks like one."
                problems.append(
                    f"possible credential (heuristic match) in {rel} — "
                    "if this is a real secret, redact the value and keep "
                    "the name; if it isn't, remove it from the document or "
                    "rename the label so it doesn't end in "
                    "password/secret/token/api_key"
                )

    if problems:
        for problem in problems:
            print(f"GATE: FAIL — {problem}")
        return 1
    print("GATE: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
