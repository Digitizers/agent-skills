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
    # Fix-round-5: four more vendor prefixes that are as unambiguous as the
    # five above — each one is a credential by its shape alone, so they are
    # reported with the SPECIFIC wording ("... found in ..."), never as a
    # heuristic. They matter most in a table cell or prose, where there is no
    # `label = value` for the generic scan to key off.
    (re.compile(r"\bsk_live_[A-Za-z0-9]{16,}\b"), "Stripe live secret key"),
    (re.compile(r"\bsk-proj-[A-Za-z0-9_-]{16,}"), "OpenAI project API key"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"), "Slack token"),
    (re.compile(r"\bAIza[A-Za-z0-9_-]{20,}"), "Google API key"),
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
# The keyword may be preceded by any run of label characters
# (letters/digits/underscore/hyphen) — and, since round 5, followed by one
# too. This does not widen it into prose: the assignment character (`=` or
# `:`) must still follow the LABEL directly (only whitespace between), so
# "the API key is stored in 1Password" and "secret: the build is slow" still
# don't match — there is no `[=:]` right after the keyword in the first, and
# the value after `:` in the second is only 3 characters, short of the
# pattern's 4-character minimum.
# Fix-round-5 pin: three separate holes, all of them realistic.
#   * The keyword had to be the label's TAIL, so `AWS_SECRET_ACCESS_KEY:`,
#     `*_CREDENTIALS`, `*_AUTH` and `*_PWD` were invisible — the keyword may
#     now be followed by label characters as well as preceded by them.
#   * `passwd`, `pwd`, `credential` and `bearer` were not in the alternation,
#     so `DB_PASSWD=hunter2hunter2hunter` read as prose.
#   * The value character class excluded `@ ! # % & * ( ) , ;`, so a password
#     with punctuation early (`p@ssw0rd!Xy29KqZ`) truncated to `p` and fell
#     under the 4-character minimum. The value is now "everything up to
#     whitespace", minus the quote characters that delimit it and the pipe
#     that delimits a markdown table cell.
# What did NOT change: the assignment character must still follow the label
# directly (only whitespace between), the value still has a length floor, and
# it is still checked against PLACEHOLDER_RX before it counts — so
# `BUNNY_API_KEY=REDACTED_DO_NOT_COMMIT`, `<paste-here>`, `xxxxxxxx` and
# `your-key-here` still pass, and "secret: the build is slow" still does not
# match (`the` is 3 characters).
GENERIC_CRED_RX = re.compile(
    r"(?i)[A-Za-z0-9_-]*"
    r"(?:password|passwd|pwd|secret|credential|bearer|token|api[_-]?key)"
    r"[A-Za-z0-9_-]*\s*[=:]\s*"
    r"(['\"]?)([^\s'\"`|]{4,})\1"
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

# A backtick-quoted RELATIVE path (`docs/spec/handoff.md`). The skill tells
# authors to reference specs and plans BY PATH instead of restating them, so
# this is the common citation shape — and until this round it was captured by
# PATH_RX and then silently dropped, which let a handoff cite
# `docs/missing-spec.md` and still print GATE: PASS.
#
# Deliberately conservative about what counts as a path, because a backtick
# span holds all sorts of things: the candidate must contain a "/", must be
# made only of path characters, and must either end in "/" or carry a file
# extension. That keeps `docs/missing-spec.md` and `apps/app/` in scope while
# leaving a branch name (`feat/handoff-three-modes`) and an extension-less
# directory reference (`docs/spec`) alone — a missed check is a lesser harm
# here than a gate that fails every handoff naming a branch.
RELATIVE_PATH_RX = re.compile(r"^[\w.~][\w./~-]*$")
RELATIVE_PATH_TAIL_RX = re.compile(r"\.[A-Za-z0-9]{1,8}$")

# Archives are checked by ENTRY NAME only (see the zip loop below) and must
# never be read into the text scan: a bundle is arbitrarily large, and regex
# matches out of compressed bytes are noise, not findings.
ARCHIVE_SUFFIXES = (
    ".zip", ".jar", ".whl", ".egg", ".tar", ".tgz", ".tar.gz",
    ".tar.bz2", ".tbz2", ".tar.xz", ".txz", ".gz", ".bz2", ".xz", ".7z",
)


def is_archive(name: str) -> bool:
    lowered = name.lower()
    return any(lowered.endswith(suffix) for suffix in ARCHIVE_SUFFIXES)


def redact(message: str) -> str:
    """Return message with anything credential-shaped replaced by [redacted].

    Every GATE: FAIL line goes through this before it is printed. The gate
    echoes content derived from the scanned files — check_paths repeats a
    matched path candidate verbatim — and a path or a filename can itself
    carry a credential (`/tmp/deploy-AKIA.../notes.md`, a checkout named
    after a token). Printing that to stdout writes the secret into the
    terminal scrollback, the CI log and any transcript of the session, which
    is precisely what this gate exists to prevent.
    """
    out = message
    for pattern, _label in SECRET_PATTERNS:
        out = pattern.sub("[redacted]", out)

    def _mask_value(match):
        whole = match.group(0)
        prefix = whole[: match.start(2) - match.start(0)]
        return prefix + "[redacted]" + (match.group(1) or "")

    return GENERIC_CRED_RX.sub(_mask_value, out)


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


def check_paths(text: str, base_dir: str) -> list:
    problems = []
    cwd = os.getcwd()
    beside = os.path.abspath(base_dir or ".")
    masked = URL_RX.sub(lambda m: "\0" * len(m.group()), text)
    for quoted, bare in PATH_RX.findall(masked):
        candidate = (quoted or bare).strip()
        if not candidate:
            continue
        # A command, not a path: `git -C /repo status`.
        if " " in candidate:
            continue
        if candidate.startswith(("/", "~")):
            if not os.path.exists(os.path.expanduser(candidate)):
                problems.append(f"path does not exist: {candidate}")
            continue
        # Only a backtick-quoted span can be a relative path: the bare
        # alternative of PATH_RX is anchored to "/" or "~" by construction.
        if not quoted or "/" not in candidate:
            continue
        if not RELATIVE_PATH_RX.match(candidate):
            continue
        if not (candidate.endswith("/") or RELATIVE_PATH_TAIL_RX.search(candidate)):
            continue
        if os.path.exists(os.path.join(beside, candidate)):
            continue
        if os.path.exists(os.path.join(cwd, candidate)):
            continue
        # Name BOTH places that were searched: the reader has to be able to
        # tell a typo from a path that is real but lives somewhere else.
        problems.append(
            f"relative path does not exist: {candidate} — looked beside the "
            f"handoff document in {beside} and in the current working "
            f"directory {cwd}"
        )
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
        print(f"GATE: FAIL — {redact(f'no handoff document at {doc}')}")
        return 1
    try:
        text = open(doc, "r", encoding="utf-8", errors="replace").read()
    except OSError as exc:
        print(f"GATE: FAIL — {redact(f'could not read {doc}: {exc}')}")
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

    # I3: pointing the gate at a FILE used to walk that file's whole parent
    # directory — run against a document in a repository root or in `~`, it
    # scanned everything there and reported findings that were no part of the
    # handoff. A file target now scans that file, the mode's named sibling
    # files (PROMPT.txt) and sibling ARCHIVES, and nothing else; pass the
    # directory to scan the tree.
    #
    # The archives are in that list because the zip check is the one part of
    # the gate a reader cannot redo by eye, and a cross-workspace bundle is
    # HANDOFF.md + PROMPT.txt + workspace.zip in one directory: narrowing to
    # MODE_FILES alone made `handoff-gate.py HANDOFF.md --mode cross-workspace`
    # print GATE: PASS over a zip holding a `.env`, while the same bundle
    # passed as a directory failed. A security control that is only correct
    # when it is called correctly is not a control.
    if os.path.isdir(args.path):
        all_files = list(iter_files(directory))
    else:
        seen = {os.path.abspath(doc)}
        all_files = [(os.path.basename(doc), doc)]

        def add_sibling(filename):
            sibling = os.path.join(directory, filename)
            full = os.path.abspath(sibling)
            if os.path.isfile(sibling) and full not in seen:
                seen.add(full)
                all_files.append((filename, sibling))

        for filename in MODE_FILES.get(args.mode, ()):
            add_sibling(filename)
        try:
            neighbours = sorted(os.listdir(directory))
        except OSError:
            neighbours = []
        for filename in neighbours:
            if filename.endswith(".zip"):
                add_sibling(filename)

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
        # An archive was already checked by entry name above. Reading it here
        # as UTF-8 text would pull the whole bundle into memory and match the
        # patterns against compressed bytes — noise, not findings, and it
        # contradicts the documented entry-name-only policy.
        if is_archive(rel):
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
                    "rename the label so it does not contain "
                    "password/passwd/pwd/secret/credential/bearer/token/api_key"
                )

    if problems:
        for problem in problems:
            print(f"GATE: FAIL — {redact(problem)}")
        return 1
    print("GATE: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
