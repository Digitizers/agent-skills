#!/usr/bin/env python3
"""Deterministically validate a repository of portable agent skills."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PureWindowsPath
from urllib.parse import unquote

try:
    import yaml
except ImportError:
    sys.exit(
        "validate.py needs PyYAML to parse skill frontmatter. "
        "Install it with: pip install pyyaml"
    )


NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
REFERENCE_USE_RE = re.compile(r"!?\[([^\]]+)\]\[([^\]]*)\]")
REFERENCE_DEF_RE = re.compile(
    r"^[ \t]{0,3}\[([^\]]+)\]:[ \t]*(.+)$",
    re.MULTILINE,
)
ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
FENCED_CODE_START_RE = re.compile(r"^([ \t]*)(`{3,}|~{3,})")
LIST_ITEM_RE = re.compile(r"^[ \t]*(?:[-+*]|\d+[.)])[ \t]+")
ENV_TEMPLATE_SUFFIXES = (".env.example", ".env.sample", ".env.template")
PRIVATE_MARKERS = (
    "Digitizers/" + "marketing-skills",
    "Digitizers/" + "digitizer-os",
    "digitizer-" + "private",
)
POSIX_HOME_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:/Users|/home|/root)/[^\s`'\"]+"
)
WINDOWS_HOME_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:[A-Z]:[\\/]Users[\\/]|"
    r"(?<!http:)(?<!https:)(?:\\\\|//)[^\\/\s]+[\\/]Users[\\/])"
    r"[^\s`'\"]+"
)
LOCAL_ABSOLUTE_RE = re.compile(
    r"(?<![A-Za-z0-9])/(?:workspace|workspaces|opt)"
    r"(?:/[^\s`'\"]+)?(?=$|[\s`'\"),.;:!?])"
)
URI_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def fail(errors: list[str], path: Path, message: str) -> None:
    errors.append(f"{path.as_posix()}: {message}")


def contains_private_marker(text: str) -> str | None:
    folded = text.casefold()
    for marker in PRIVATE_MARKERS:
        if marker.casefold() in folded:
            return marker
    return None


def contains_machine_specific_path(text: str) -> bool:
    return bool(
        POSIX_HOME_RE.search(text)
        or WINDOWS_HOME_RE.search(text)
        or LOCAL_ABSOLUTE_RE.search(text)
    )


def frontmatter(
    path: Path, display_path: Path, errors: list[str]
) -> dict[str, object] | None:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        fail(errors, display_path, "missing opening frontmatter delimiter")
        return None
    try:
        end = lines[1:].index("---") + 1
    except ValueError:
        fail(errors, display_path, "missing standalone closing frontmatter delimiter")
        return None
    try:
        parsed = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as exc:
        fail(errors, display_path, f"invalid YAML frontmatter: {exc}")
        return None
    if not isinstance(parsed, dict):
        fail(errors, display_path, "frontmatter must be an object")
        return None
    return parsed


def markdown_target(raw: str) -> str | None:
    raw = raw.strip()
    if raw.startswith("<"):
        closing = raw.find(">")
        return None if closing < 0 else raw[1:closing]
    return raw.split(maxsplit=1)[0] if raw else ""


def inline_link_targets(text: str) -> list[str]:
    targets: list[str] = []
    index = 0
    while index < len(text):
        if text[index] == "!":
            if is_escaped(text, index):
                index += 1
                continue
            label_start = index + 2 if index + 1 < len(text) and text[index + 1] == "[" else 0
        else:
            label_start = (
                index + 1
                if text[index] == "[" and not is_escaped(text, index)
                else 0
            )
        if not label_start:
            index += 1
            continue

        label_end = closing_bracket(text, label_start)
        if label_end < 0 or label_end + 1 >= len(text) or text[label_end + 1] != "(":
            index += 1
            continue

        start = label_end + 2
        depth = 1
        escaped = False
        for index in range(start, len(text)):
            character = text[index]
            if escaped:
                escaped = False
                continue
            if character == "\\":
                escaped = True
                continue
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0:
                    targets.append(text[start:index])
                    break
        index += 1
    return targets


def is_escaped(text: str, index: int) -> bool:
    backslashes = 0
    index -= 1
    while index >= 0 and text[index] == "\\":
        backslashes += 1
        index -= 1
    return backslashes % 2 == 1


def closing_bracket(text: str, start: int) -> int:
    depth = 1
    escaped = False
    for index in range(start, len(text)):
        character = text[index]
        if escaped:
            escaped = False
            continue
        if character == "\\":
            escaped = True
            continue
        if character == "[":
            depth += 1
        elif character == "]":
            depth -= 1
            if depth == 0:
                return index
    return -1


def reference_link_uses(text: str) -> list[tuple[str, str]]:
    uses: list[tuple[str, str]] = []
    index = 0
    while index < len(text):
        bracket = index + 1 if text[index] == "!" else index
        if bracket >= len(text) or text[bracket] != "[" or is_escaped(text, bracket):
            index += 1
            continue
        label_end = closing_bracket(text, bracket + 1)
        if label_end < 0 or label_end + 1 >= len(text) or text[label_end + 1] != "[":
            index += 1
            continue
        reference_end = closing_bracket(text, label_end + 2)
        if reference_end < 0:
            index += 1
            continue
        uses.append((text[bracket + 1 : label_end], text[label_end + 2 : reference_end]))
        index = reference_end + 1
    return uses


def text_width(text: str) -> int:
    width = 0
    for character in text:
        width = width + (4 - width % 4 if character == "\t" else 1)
    return width


def without_block_quote_prefix(line: str) -> str:
    remaining = line
    while True:
        match = re.match(r"^[ ]{0,3}>[ \t]?", remaining)
        if not match:
            return remaining
        remaining = remaining[match.end() :]


def strip_fenced_code(text: str) -> str:
    lines = text.splitlines(keepends=True)
    kept: list[str] = []
    fence_marker: str | None = None
    fence_length = 0
    fence_indent = 0
    fence_close_indent = 3
    in_list_item = False
    fence_in_list = False
    fence_in_quote = False
    list_code_indent = 8
    for line in lines:
        markdown_line = without_block_quote_prefix(line)
        if fence_marker is None:
            content = markdown_line.rstrip("\r\n")
            list_match = LIST_ITEM_RE.match(content)
            indent = text_width(content[: len(content) - len(content.lstrip())])
            if list_match and (
                indent < 4 or (in_list_item and indent < list_code_indent)
            ):
                in_list_item = True
                list_code_indent = text_width(list_match.group(0)) + 4
            elif (
                content.strip()
                and indent < 4
                and not FENCED_CODE_START_RE.match(markdown_line)
            ):
                in_list_item = False
            match = FENCED_CODE_START_RE.match(markdown_line)
            opener_indent = text_width(match.group(1)) if match else 0
            if match and (
                opener_indent <= 3
                or (in_list_item and opener_indent < list_code_indent)
            ):
                fence_indent = opener_indent
                fence_close_indent = opener_indent + 3 if in_list_item else 3
                fence_in_list = in_list_item
                fence_in_quote = markdown_line != line
                fence_marker = match.group(2)[0]
                fence_length = len(match.group(2))
                kept.append("\n" if line.endswith("\n") else "")
                continue
            kept.append(line)
            continue
        close = markdown_line.rstrip("\r\n")
        if (
            (fence_in_quote and markdown_line == line)
            or (
                fence_in_list
                and close.strip()
                and text_width(close[: len(close) - len(close.lstrip())])
                < fence_indent
            )
        ):
            fence_marker = None
            kept.append(line)
            continue
        close_match = re.fullmatch(
            rf"([ \t]*){re.escape(fence_marker)}{{{fence_length},}}[ \t]*",
            close,
        )
        if close_match and text_width(close_match.group(1)) <= fence_close_indent:
            fence_marker = None
            fence_length = 0
        kept.append("\n" if line.endswith("\n") else "")
    return "".join(kept)


def strip_inline_code(text: str) -> str:
    kept: list[str] = []
    index = 0
    while index < len(text):
        if text[index] != "`" or is_escaped(text, index):
            kept.append(text[index])
            index += 1
            continue
        opener_end = index
        while opener_end < len(text) and text[opener_end] == "`":
            opener_end += 1
        run_length = opener_end - index
        search = opener_end
        closing_start = -1
        while search < len(text):
            if text[search] != "`":
                search += 1
                continue
            closing_end = search
            while closing_end < len(text) and text[closing_end] == "`":
                closing_end += 1
            if closing_end - search == run_length:
                closing_start = search
                break
            search = closing_end
        if closing_start < 0:
            kept.append(text[index:opener_end])
            index = opener_end
            continue
        removed = text[index : closing_start + run_length]
        kept.append("\n" * removed.count("\n"))
        index = closing_start + run_length
    return "".join(kept)


def validate_one_link(
    repo: Path, path: Path, raw: str, errors: list[str]
) -> None:
    target = markdown_target(raw)
    if target is None:
        fail(
            errors,
            path.relative_to(repo),
            f"angle-bracketed reference is not closed: {raw}",
        )
        return
    windows_drive = re.match(r"^[A-Za-z]:[\\/]", target)
    if (
        not target
        or target.startswith("#")
        or (URI_SCHEME_RE.match(target) and not windows_drive)
    ):
        return
    decoded = unquote(target.split("#", 1)[0].split("?", 1)[0])
    candidate = (path.parent / decoded).resolve()
    try:
        candidate.relative_to(repo)
    except ValueError:
        fail(
            errors,
            path.relative_to(repo),
            f"reference escapes repository: {target}",
        )
        return
    if not candidate.exists():
        fail(
            errors,
            path.relative_to(repo),
            f"reference does not resolve: {target}",
        )


def validate_links(repo: Path, path: Path, errors: list[str]) -> None:
    text = path.read_text(encoding="utf-8")
    text = strip_fenced_code(text)
    text = strip_inline_code(text)
    for raw in inline_link_targets(text):
        raw = raw.strip()
        validate_one_link(repo, path, raw, errors)

    def normalized_label(label: str) -> str:
        return " ".join(label.split()).casefold()

    definitions: dict[str, str] = {}
    reference_text = "\n".join(
        without_block_quote_prefix(line).lstrip()
        for line in text.splitlines()
    )
    for label, target in REFERENCE_DEF_RE.findall(reference_text):
        definitions.setdefault(normalized_label(label), target)
    for label, target in definitions.items():
        validate_one_link(repo, path, target, errors)
    for text_label, explicit_label in reference_link_uses(text):
        label = normalized_label(explicit_label or text_label)
        if label not in definitions:
            fail(
                errors,
                path.relative_to(repo),
                f"reference label is not defined: {label}",
            )


def validate_triggers(
    path: Path, display_path: Path, errors: list[str]
) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(errors, display_path, f"invalid JSON: {exc}")
        return
    if not isinstance(payload, list) or not payload:
        fail(errors, display_path, "trigger spec must be a non-empty array")
        return
    outcomes: set[bool] = set()
    for index, item in enumerate(payload):
        if not isinstance(item, dict) or set(item) != {"query", "should_trigger"}:
            fail(
                errors,
                display_path,
                f"item {index} must contain only query and should_trigger",
            )
            continue
        if not isinstance(item["query"], str) or not item["query"].strip():
            fail(
                errors,
                display_path,
                f"item {index} query must be a non-empty string",
            )
        if not isinstance(item["should_trigger"], bool):
            fail(
                errors,
                display_path,
                f"item {index} should_trigger must be boolean",
            )
        else:
            outcomes.add(item["should_trigger"])
    if outcomes != {False, True}:
        fail(
            errors,
            display_path,
            "trigger spec must include positive and negative examples",
        )


def validate_public_boundary(repo: Path, errors: list[str]) -> None:
    for path in public_boundary_paths(repo):
        if ".git" in path.parts or "__pycache__" in path.parts:
            continue
        relative = path.relative_to(repo)
        marker = contains_private_marker(relative.as_posix())
        if marker is not None:
            fail(errors, relative, f"private identifier exposed: {marker}")
        is_env_template = path.name.endswith(ENV_TEMPLATE_SUFFIXES)
        is_env_file = (
            path.name == ".env"
            or path.name == ".envrc"
            or path.name.endswith(".env")
            or ".env." in path.name
        )
        if is_env_file and not is_env_template:
            fail(
                errors,
                relative,
                "committed environment file is forbidden in public repos",
            )
            continue
        if path.is_symlink():
            target = path.readlink()
            target_text = target.as_posix()
            resolved = (path.parent / target).resolve()
            escapes_repo = False
            try:
                resolved.relative_to(repo)
            except ValueError:
                escapes_repo = True
            if (
                target.is_absolute()
                or PureWindowsPath(target_text).is_absolute()
                or escapes_repo
                or contains_private_marker(target_text) is not None
                or contains_machine_specific_path(target_text)
            ):
                fail(errors, relative, "symlink target escapes public repository boundary")
            continue
        if not path.is_file():
            continue
        raw = path.read_bytes()
        if b"\0" in raw:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if is_env_template:
            for number, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if "=" not in stripped:
                    fail(errors, relative, f"invalid env template line {number}")
                    continue
                name, value = stripped.split("=", 1)
                if not ENV_NAME_RE.fullmatch(name):
                    fail(errors, relative, f"invalid env name on line {number}")
                placeholder = value.strip()
                allowed_placeholder = (
                    not placeholder
                    or re.fullmatch(r"\$\{[A-Z][A-Z0-9_]*\}", placeholder)
                    or re.fullmatch(r"<[^>\r\n]+>", placeholder)
                    or placeholder.casefold()
                    in {"changeme", "example", "placeholder", "replace_me", "xxx"}
                    or placeholder.casefold().startswith(("your-", "your_"))
                )
                if not allowed_placeholder:
                    fail(errors, relative, f"non-placeholder env value on line {number}")
        marker = contains_private_marker(text)
        if marker is not None:
            fail(errors, relative, f"private identifier exposed: {marker}")
        if contains_machine_specific_path(text):
            fail(errors, relative, "machine-specific absolute path exposed")


def public_boundary_paths(repo: Path) -> list[Path]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "-z"],
            check=True,
            capture_output=True,
            text=False,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return fallback_public_boundary_paths(repo)
    tracked = [
        repo / item.decode("utf-8")
        for item in result.stdout.split(b"\0")
        if item
    ]
    return sorted(tracked)


def fallback_public_boundary_paths(repo: Path) -> list[Path]:
    paths: list[Path] = []
    for root, dirnames, filenames in os.walk(repo, followlinks=False):
        current = Path(root)
        paths.extend(current / name for name in dirnames)
        paths.extend(current / name for name in filenames)
    return sorted(paths)


def validate_repo(
    repo: Path, *, visibility: str, require_cloud_links: bool
) -> list[str]:
    repo = repo.resolve()
    errors: list[str] = []
    skills_root = repo / "skills"
    skill_directories = (
        sorted(path for path in skills_root.iterdir() if path.is_dir())
        if skills_root.is_dir()
        else []
    )
    skills: list[Path] = []
    for path in skill_directories:
        if not (path / "SKILL.md").is_file():
            fail(
                errors,
                path.relative_to(repo),
                "skill directory is missing SKILL.md",
            )
            continue
        skills.append(path)
    if not skills:
        return ["skills: no canonical skills found"]

    for skill in skills:
        relative_skill = skill.relative_to(repo)
        if not NAME_RE.fullmatch(skill.name):
            fail(errors, relative_skill, "directory name is not a valid skill name")
        metadata = frontmatter(
            skill / "SKILL.md", relative_skill / "SKILL.md", errors
        )
        if metadata is not None:
            if metadata.get("name") != skill.name:
                fail(errors, relative_skill / "SKILL.md", "frontmatter name must match directory")
            description = metadata.get("description")
            if not isinstance(description, str) or not description.strip():
                fail(errors, relative_skill / "SKILL.md", "description must be non-empty text")

        for doc in sorted(skill.rglob("*.md")):
            validate_links(repo, doc, errors)
        trigger_spec = skill / "evals" / "triggers.json"
        if trigger_spec.exists():
            validate_triggers(trigger_spec, trigger_spec.relative_to(repo), errors)

        if require_cloud_links:
            link = repo / ".claude" / "skills" / skill.name
            expected = Path("../..") / "skills" / skill.name
            if not link.is_symlink():
                fail(errors, link.relative_to(repo), "required relative cloud symlink is missing")
            elif link.readlink() != expected:
                fail(errors, link.relative_to(repo), f"expected symlink target {expected}")

    if require_cloud_links:
        cloud_root = repo / ".claude" / "skills"
        canonical_names = {skill.name for skill in skills}
        if cloud_root.is_dir():
            for entry in sorted(cloud_root.iterdir()):
                if entry.name not in canonical_names:
                    fail(
                        errors,
                        entry.relative_to(repo),
                        "noncanonical cloud skill entry",
                    )

    if visibility == "public":
        validate_public_boundary(repo, errors)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--visibility", choices=("public", "private"), required=True)
    parser.add_argument("--require-cloud-links", action="store_true")
    args = parser.parse_args()
    errors = validate_repo(
        args.repo,
        visibility=args.visibility,
        require_cloud_links=args.require_cloud_links,
    )
    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    if errors:
        print(f"portability validation failed: {len(errors)} error(s)", file=sys.stderr)
        return 1
    count = sum(
        1
        for path in (args.repo / "skills").iterdir()
        if path.is_dir() and (path / "SKILL.md").is_file()
    )
    print(f"portability OK: {count} skills ({args.visibility})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
