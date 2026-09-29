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
    (re.compile(r"(?i)\b(password|secret|token|api[_-]?key)\s*[=:]\s*['\"]?[A-Za-z0-9/+_-]{12,}"),
     "credential assignment"),
)

# Paths the document mentions. Quoted in backticks or bare, absolute or ~-rooted.
PATH_RX = re.compile(r"`([^`\n]+)`|(?<![\w`])((?:~|/)[\w./~-]{3,})")


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
    for quoted, bare in PATH_RX.findall(text):
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

    # The zip is checked by entry NAME, never extracted: a gate that unpacks
    # an archive is a gate that can be made to write outside its directory.
    for name in sorted(os.listdir(directory)) if os.path.isdir(directory) else []:
        if not name.endswith(".zip"):
            continue
        try:
            import zipfile
            with zipfile.ZipFile(os.path.join(directory, name)) as zf:
                entries = zf.namelist()
        except (OSError, zipfile.BadZipFile):
            problems.append(f"unreadable archive: {name}")
            continue
        for entry in entries:
            base = os.path.basename(entry.rstrip("/"))
            if base in (".env", ".npmrc", ".pypirc", "id_rsa", "id_ed25519") or \
                    base.startswith(".env."):
                problems.append(f"{name} contains {entry} — credentials never travel in the zip")

    # Every file in the handoff directory is scanned, not just the document:
    # PROMPT.txt is the file most likely to be pasted into another account.
    for name in sorted(os.listdir(directory)) if os.path.isdir(directory) else [os.path.basename(doc)]:
        full = os.path.join(directory, name)
        if not os.path.isfile(full):
            continue
        try:
            body = open(full, "r", encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for pattern, label in SECRET_PATTERNS:
            if pattern.search(body):
                problems.append(f"{label} found in {name} — redact the value, keep the name")

    if problems:
        for problem in problems:
            print(f"GATE: FAIL — {problem}")
        return 1
    print("GATE: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
