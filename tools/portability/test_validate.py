from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).parent
SPEC = importlib.util.spec_from_file_location("portability_validate", HERE / "validate.py")
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load portability validator module")
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


def make_skill(repo: Path, name: str = "widget") -> Path:
    skill = repo / "skills" / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Portable widget.\n---\n\n"
        "# Widget\n\n[Reference](REFERENCE.md)\n",
        encoding="utf-8",
    )
    (skill / "REFERENCE.md").write_text("# Reference\n", encoding="utf-8")
    evals = skill / "evals"
    evals.mkdir()
    (evals / "triggers.json").write_text(
        json.dumps(
            [
                {"query": "use widget", "should_trigger": True},
                {"query": "do something else", "should_trigger": False},
            ]
        ),
        encoding="utf-8",
    )
    return skill


class PortabilityValidationTests(unittest.TestCase):
    def test_valid_public_repo_with_cloud_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            make_skill(repo)
            links = repo / ".claude" / "skills"
            links.mkdir(parents=True)
            (links / "widget").symlink_to("../../skills/widget")
            self.assertEqual(
                [],
                VALIDATOR.validate_repo(
                    repo, visibility="public", require_cloud_links=True
                ),
            )

    def test_rejects_name_mismatch_and_broken_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "SKILL.md").write_text(
                "---\nname: other\ndescription: Wrong.\n---\n\n[Missing](NOPE.md)\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="private", require_cloud_links=False
            )
            self.assertTrue(any("name must match" in error for error in errors))
            self.assertTrue(any("does not resolve" in error for error in errors))
            self.assertTrue(all(tmp not in error for error in errors))

    def test_accepts_angle_bracketed_reference_with_spaces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "My File.md").write_text("# Fine\n", encoding="utf-8")
            (skill / "SKILL.md").write_text(
                "---\nname: widget\ndescription: Fine.\n---\n\n"
                "[Reference](<My File.md>)\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="private", require_cloud_links=False
            )
            self.assertFalse(any("reference" in error for error in errors), errors)

    def test_validates_reference_style_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "SKILL.md").write_text(
                "---\nname: widget\ndescription: Fine.\n---\n\n"
                "[Reference][details]\n\n[details]: MISSING.md\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="private", require_cloud_links=False
            )
            self.assertTrue(any("does not resolve" in error for error in errors))

            (skill / "SKILL.md").write_text(
                "---\nname: widget\ndescription: Fine.\n---\n\n"
                "[Reference][details]\n\n[details]: REFERENCE.md\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="private", require_cloud_links=False
            )
            self.assertFalse(any("reference" in error for error in errors), errors)

    def test_rejects_skill_directory_without_skill_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            make_skill(repo)
            (repo / "skills" / "unfinished").mkdir()
            errors = VALIDATOR.validate_repo(
                repo, visibility="private", require_cloud_links=False
            )
            self.assertTrue(any("missing SKILL.md" in error for error in errors))

    def test_rejects_absolute_or_wrong_cloud_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            make_skill(repo)
            links = repo / ".claude" / "skills"
            links.mkdir(parents=True)
            (links / "widget").symlink_to(repo / "skills" / "widget")
            errors = VALIDATOR.validate_repo(
                repo, visibility="private", require_cloud_links=True
            )
            self.assertTrue(any("expected symlink target" in error for error in errors))

    def test_rejects_extra_cloud_skill_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            make_skill(repo)
            links = repo / ".claude" / "skills"
            links.mkdir(parents=True)
            (links / "widget").symlink_to("../../skills/widget")
            (links / "removed-skill").mkdir()
            errors = VALIDATOR.validate_repo(
                repo, visibility="private", require_cloud_links=True
            )
            self.assertTrue(any("noncanonical cloud skill entry" in error for error in errors))

    def test_rejects_malformed_trigger_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "evals" / "triggers.json").write_text(
                '[{"query":"only positive","should_trigger":true}]',
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="private", require_cloud_links=False
            )
            self.assertTrue(any("positive and negative" in error for error in errors))

    def test_public_boundary_rejects_private_identifiers_and_absolute_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "REFERENCE.md").write_text(
                "See Digitizers/"
                "marketing-skills at /"
                "Users/operator/private.\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="public", require_cloud_links=False
            )
            self.assertTrue(any("private identifier" in error for error in errors))
            self.assertTrue(any("absolute path" in error for error in errors))

    def test_public_boundary_scans_suffixless_files_and_rejects_env_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            make_skill(repo)
            (repo / "LICENSE").write_text(
                "Internal path: /" "root/.claude/private\n", encoding="utf-8"
            )
            (repo / ".env.local").write_text("TOKEN=secret\n", encoding="utf-8")
            errors = VALIDATOR.validate_repo(
                repo, visibility="public", require_cloud_links=False
            )
            self.assertTrue(any("LICENSE" in error and "absolute path" in error for error in errors))
            self.assertTrue(any(".env.local" in error and "environment file" in error for error in errors))

    def test_public_boundary_allows_placeholder_env_templates_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            make_skill(repo)
            template = repo / ".env.example"
            template.write_text(
                "API_TOKEN=<replace-me>\nAPI_URL=${API_URL}\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="public", require_cloud_links=False
            )
            self.assertFalse(any(".env.example" in error for error in errors), errors)

            template.write_text("API_TOKEN=real-looking-value\n", encoding="utf-8")
            errors = VALIDATOR.validate_repo(
                repo, visibility="public", require_cloud_links=False
            )
            self.assertTrue(any("non-placeholder env value" in error for error in errors))

    def test_public_boundary_rejects_windows_home_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "REFERENCE.md").write_text(
                "Local file: C:" "\\Users\\operator\\private.txt\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="public", require_cloud_links=False
            )
            self.assertTrue(any("absolute path" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
