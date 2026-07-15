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

# ── trigger_eval: a personal skill must not silently win the probe ──
# Codex round-1 P2. Personal skills in ~/.claude/skills override project skills
# of the same name, so a stale personal copy answers the probe while the harness
# reports numbers for the working tree. Same silent failure, turned on the tool.

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    home_skills = root / "home" / ".claude" / "skills"
    under_test = skill_at(root / "repo", "widget", "name: widget\ndescription: A widget.")

    real_home = Path.home
    Path.home = staticmethod(lambda: root / "home")  # type: ignore[method-assign]
    try:
        # No personal skill of that name — nothing to shadow.
        try:
            trigger_eval.assert_not_shadowed("widget", under_test)
            check("assert_not_shadowed allows an unshadowed skill", True)
        except SystemExit as e:
            check("assert_not_shadowed allows an unshadowed skill", False, str(e))

        # Personal skill symlinked to the working tree: same file, probe is valid.
        home_skills.mkdir(parents=True, exist_ok=True)
        (home_skills / "widget").symlink_to(under_test)
        try:
            trigger_eval.assert_not_shadowed("widget", under_test)
            check("assert_not_shadowed allows a symlink to the tree under test", True)
        except SystemExit as e:
            check("assert_not_shadowed allows a symlink to the tree under test", False, str(e))

        # Personal skill is a DIFFERENT copy: it would win, so refuse.
        (home_skills / "widget").unlink()
        skill_at(home_skills, "widget", "name: widget\ndescription: A STALE widget.")
        try:
            trigger_eval.assert_not_shadowed("widget", under_test)
            check("assert_not_shadowed refuses a differing personal copy", False,
                  "should have raised SystemExit")
        except SystemExit:
            check("assert_not_shadowed refuses a differing personal copy", True)
    finally:
        Path.home = real_home  # type: ignore[method-assign]

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
