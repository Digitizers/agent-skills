#!/usr/bin/env python3
"""Regression tests for the skill tooling.

    python3 tools/trigger-eval/test_tools.py

Each test pins a defect that was actually shipped and then caught in review.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import trigger_eval  # noqa: E402
import validate_spec  # noqa: E402

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'pass' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILURES.append(f"{name}{f': {detail}' if detail else ''}")


def skill_at(root: Path, name: str, frontmatter: str) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n\n# {name}\n\nBody.\n")
    return d


# ── validate_spec: malformed YAML must be rejected, not regex-approximated ──
# Codex round-1 P2. The regex parser accepted `description: [unterminated`.
# Claude Code loads malformed frontmatter as EMPTY metadata, so the skill ships
# with no description and can never trigger — the validator was green-lighting
# precisely the failure it exists to prevent.

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)

    bad = skill_at(root, "bad-yaml", "name: bad-yaml\ndescription: [unterminated")
    errors, _ = validate_spec.check(bad)
    check("validate_spec rejects unterminated YAML flow sequence",
          any("not valid YAML" in e for e in errors), f"got {errors}")

    tabbed = skill_at(root, "tabbed", "name: tabbed\n\tdescription: tabs are illegal in YAML")
    errors, _ = validate_spec.check(tabbed)
    check("validate_spec rejects tab-indented frontmatter", bool(errors), f"got {errors}")

    good = skill_at(root, "good", "name: good\ndescription: Does a thing. Use when the user wants a thing.")
    errors, warnings = validate_spec.check(good)
    check("validate_spec accepts valid frontmatter", not errors, f"got {errors}")

    # Codex round-6 P2: YAML coerces unquoted scalars — `description: no` is the
    # boolean False, `description: 123` an int. str()-ing them green-lit metadata
    # the author never wrote. Require actual strings.
    boolish = skill_at(root, "boolish", "name: boolish\ndescription: no")
    errors, _ = validate_spec.check(boolish)
    check("validate_spec rejects an unquoted boolean description",
          any("not text" in e for e in errors), f"got {errors}")

    numish = skill_at(root, "numish", "name: numish\ndescription: 123")
    errors, _ = validate_spec.check(numish)
    check("validate_spec rejects an unquoted numeric description",
          any("not text" in e for e in errors), f"got {errors}")

    quoted = skill_at(root, "quoted", 'name: quoted\ndescription: "no"')
    errors, _ = validate_spec.check(quoted)
    check("validate_spec accepts a quoted 'no' description", not errors, f"got {errors}")

# ── validate_spec: every skill needs its .claude/skills cloud symlink ──
# Codex #12 round-1 P2. Cloud sessions auto-load only .claude/skills/, so a
# skill added under skills/ without a matching symlink ships in the plugin but
# is silently absent on web/mobile — the exact drift this check pins.

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    widget = skill_at(root / "skills", "widget", "name: widget\ndescription: Does a thing.")

    errors = validate_spec.check_cloud_link(root, widget)
    check("check_cloud_link flags a skill with no .claude/skills symlink",
          any("missing" in e for e in errors), f"got {errors}")

    cloud = root / ".claude" / "skills"
    cloud.mkdir(parents=True)
    (cloud / "widget").symlink_to("../../skills/widget")
    errors = validate_spec.check_cloud_link(root, widget)
    check("check_cloud_link accepts a correct relative symlink", not errors, f"got {errors}")

    other = skill_at(root / "skills", "other", "name: other\ndescription: Another thing.")
    (cloud / "other").symlink_to("../../skills/widget")   # wrong target
    errors = validate_spec.check_cloud_link(root, other)
    check("check_cloud_link flags a symlink pointing at the wrong skill",
          bool(errors), f"got {errors}")

    # Codex #12 round-2 P2: an absolute target resolves on the machine that
    # created it but is committed verbatim, so it dangles in every other clone.
    # Resolved-path comparison green-lit exactly that; require the literal
    # relative target instead.
    absw = skill_at(root / "skills", "absw", "name: absw\ndescription: Abs thing.")
    (cloud / "absw").symlink_to(root / "skills" / "absw")   # absolute, resolves locally
    errors = validate_spec.check_cloud_link(root, absw)
    check("check_cloud_link flags an absolute symlink even though it resolves",
          bool(errors), f"got {errors}")

    # Codex round-7 P3: length was measured after collapsing whitespace, so a
    # description that is over-budget raw but under-budget collapsed slipped
    # through. The runtime sees the raw parsed value; validate against that.
    padded = skill_at(root, "padded",
                      'name: padded\ndescription: "' + "a" * 1000 + " " * 40 + 'b"')
    errors, _ = validate_spec.check(padded)
    check("validate_spec measures length on the raw value, not the collapsed one",
          any("1024" in e for e in errors),
          f"raw len {1041}, collapsed {1002}; got {errors}")

    over = skill_at(root, "over", f"name: over\ndescription: {'x' * 1025}")
    errors, _ = validate_spec.check(over)
    check("validate_spec fails a description over the 1024 limit",
          any("1024" in e for e in errors), f"got {errors}")

    near = skill_at(root, "near", f"name: near\ndescription: {'x' * 1000}")
    errors, warnings = validate_spec.check(near)
    check("validate_spec warns before the description budget runs out",
          not errors and bool(warnings), f"errors={errors} warnings={warnings}")

    mismatch = skill_at(root, "mismatch", "name: other-name\ndescription: Mismatched directory name.")
    errors, _ = validate_spec.check(mismatch)
    check("validate_spec fails when name does not match the directory",
          any("directory" in e for e in errors), f"got {errors}")

    # Codex round-10 P3: an empty compatibility is malformed — the spec requires
    # 1–500 chars if the field is provided.
    empty_compat = skill_at(root, "emptycompat",
                            'name: emptycompat\ndescription: Fine desc. Use when testing.\ncompatibility: ""')
    errors, _ = validate_spec.check(empty_compat)
    check("validate_spec rejects an empty compatibility",
          any("compatibility" in e and "empty" in e for e in errors), f"got {errors}")

    null_compat = skill_at(root, "nullcompat",
                           'name: nullcompat\ndescription: Fine desc. Use when testing.\ncompatibility:')
    errors, _ = validate_spec.check(null_compat)
    check("validate_spec rejects a null compatibility",
          any("compatibility" in e and "empty" in e for e in errors), f"got {errors}")

    ok_compat = skill_at(root, "okcompat",
                         'name: okcompat\ndescription: Fine desc. Use when testing.\ncompatibility: Requires git and jq.')
    errors, _ = validate_spec.check(ok_compat)
    check("validate_spec accepts a filled compatibility", not errors, f"got {errors}")

    # Codex round-11 P3: the spec also constrains metadata (mapping) and
    # allowed-tools (space-separated string).
    bad_meta = skill_at(root, "badmeta",
                        'name: badmeta\ndescription: Fine desc. Use when testing.\nmetadata: just a string')
    errors, _ = validate_spec.check(bad_meta)
    check("validate_spec rejects non-mapping metadata",
          any("metadata" in e for e in errors), f"got {errors}")

    bad_tools = skill_at(root, "badtools",
                         'name: badtools\ndescription: Fine desc. Use when testing.\nallowed-tools:\n  - Read\n  - Bash')
    errors, _ = validate_spec.check(bad_tools)
    check("validate_spec rejects list-valued allowed-tools",
          any("allowed-tools" in e for e in errors), f"got {errors}")

    ok_opt = skill_at(root, "okopt",
                      'name: okopt\ndescription: Fine desc. Use when testing.\n'
                      'metadata:\n  author: me\nallowed-tools: Read Bash')
    errors, _ = validate_spec.check(ok_opt)
    check("validate_spec accepts a mapping metadata + string allowed-tools", not errors, f"got {errors}")

    # Codex round-12 P3: a whitespace-only description is blank — nothing to
    # trigger on — but passed a truthiness check.
    blank_desc = skill_at(root, "blankdesc", 'name: blankdesc\ndescription: "   "')
    errors, _ = validate_spec.check(blank_desc)
    check("validate_spec rejects a whitespace-only description",
          any("description" in e for e in errors), f"got {errors}")

    # Codex round-13 P3: metadata values must be strings; YAML coerces `version: 1.0`.
    coerced_meta = skill_at(root, "coercedmeta",
                            'name: coercedmeta\ndescription: Fine desc. Use when testing.\n'
                            'metadata:\n  version: 1.0')
    errors, _ = validate_spec.check(coerced_meta)
    check("validate_spec rejects a non-string metadata value",
          any("metadata" in e for e in errors), f"got {errors}")

    str_meta = skill_at(root, "strmeta",
                        'name: strmeta\ndescription: Fine desc. Use when testing.\n'
                        'metadata:\n  version: "1.0"')
    errors, _ = validate_spec.check(str_meta)
    check("validate_spec accepts string metadata values", not errors, f"got {errors}")

# (The assert_not_shadowed preflight and its tests were removed once the probe
# became fully isolated — personal skills are no longer loaded into the measured
# session, so a same-name personal skill cannot win and there is nothing to guard.
# See the note in trigger_eval.py.)

# ── trigger_eval: the Read check must anchor to the copy under test ──

with tempfile.TemporaryDirectory() as tmp:
    project = Path(tmp)
    (project / ".claude" / "skills" / "widget").mkdir(parents=True)
    (project / ".claude" / "skills" / "widget" / "SKILL.md").write_text("---\nname: widget\n---\n")

    def read_event(path: str) -> dict:
        return {"message": {"content": [{"type": "tool_use", "name": "Read",
                                         "input": {"file_path": path}}]}}

    hit = str(project / ".claude" / "skills" / "widget" / "SKILL.md")
    check("Read of the skill under test counts as a trigger",
          trigger_eval._is_trigger(read_event(hit), "widget", project))

    elsewhere = str(Path.home() / ".claude" / "skills" / "widget" / "SKILL.md")
    check("Read of a same-named skill OUTSIDE the fixture does NOT count",
          not trigger_eval._is_trigger(read_event(elsewhere), "widget", project))

    # A real Skill tool_use carries the name in input.skill (verified against a
    # live `claude -p` stream: {"skill": "gha-optimizer", "args": "..."}).
    # Codex round-3 claimed the field was `command`; it is not — `command`
    # belongs to the Bash tool. This pins the actual format so that refuted
    # finding can't quietly come back.
    skill_event = {"message": {"content": [{"type": "tool_use", "name": "Skill",
                                            "input": {"skill": "widget", "args": "..."}}]}}
    check("Skill tool_use with input.skill counts as a trigger",
          trigger_eval._is_trigger(skill_event, "widget", project))

    bash_event = {"message": {"content": [{"type": "tool_use", "name": "Bash",
                                           "input": {"command": "widget --run"}}]}}
    check("a Bash call whose command mentions the name does NOT count",
          not trigger_eval._is_trigger(bash_event, "widget", project))

# ── trigger_eval: skill-name match must be exact, not a substring ──
# Codex round-4 P2. `skill_name in emitted` counted a fire of bundled `code-review`
# as a trigger for a skill named `review`, and `apps/web:deploy` for `deploy`.

check("exact name matches", trigger_eval._same_skill("review", "review"))
check("substring does NOT match (code-review vs review)",
      not trigger_eval._same_skill("code-review", "review"))
check("namespaced leaf matches (apps/web:deploy vs deploy)",
      trigger_eval._same_skill("apps/web:deploy", "deploy"))
check("namespaced leaf substring does NOT match (apps/web:deploy vs epl)",
      not trigger_eval._same_skill("apps/web:deploy", "epl"))

# ── trigger_eval: a failed subprocess must raise, not read as "did not trigger" ──
# Codex round-5 P2. stderr was DEVNULL'd and an early exit returned False, so a
# broken probe (expired auth, bad flag) scored as a clean negative.

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    under_test = skill_at(root / "repo", "widget", "name: widget\ndescription: A widget.")

    real_run = trigger_eval.subprocess.Popen

    class FakePopen:
        """A claude that dies immediately with a non-zero code and an error on stderr."""
        def __init__(self, *a, **k):
            self.stdout = iter(())  # no events streamed
            self.returncode = 1
            errf = k.get("stderr")
            if errf and hasattr(errf, "write"):
                errf.write("Invalid API key · Please run /login\n")
                errf.flush()
        def kill(self): pass
        def wait(self, timeout=None): return 1

    trigger_eval.subprocess.Popen = FakePopen  # type: ignore[assignment]
    try:
        raised = False
        try:
            trigger_eval.probe(under_test, "widget", "any query", timeout=5, model=None)
        except trigger_eval.ProbeError as e:
            raised = "exited 1" in str(e)
        check("a non-zero claude exit raises ProbeError, not a silent False", raised)
    finally:
        trigger_eval.subprocess.Popen = real_run  # type: ignore[assignment]

print()
if FAILURES:
    print(f"  {len(FAILURES)} failed\n")
    sys.exit(1)
print("  all passed\n")
