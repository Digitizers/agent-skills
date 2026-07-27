from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
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
    def test_missing_pyyaml_has_actionable_error(self) -> None:
        result = subprocess.run(
            [sys.executable, "-S", str(HERE / "validate.py"), "--help"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("needs PyYAML", result.stderr)
        self.assertIn("pip install pyyaml", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

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

    def test_accepts_balanced_parentheses_in_inline_link_destination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "REFERENCE(old).md").write_text("# Old\n", encoding="utf-8")
            (skill / "SKILL.md").write_text(
                "---\nname: widget\ndescription: Fine.\n---\n\n"
                "[Old reference](REFERENCE(old).md)\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="private", require_cloud_links=False
            )
            self.assertFalse(any("reference" in error for error in errors), errors)

    def test_accepts_external_uri_schemes_case_insensitively(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "SKILL.md").write_text(
                "---\nname: widget\ndescription: Fine.\n---\n\n"
                "[Docs](HTTPS://example.com)\n"
                "[Call](tel:+123)\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="private", require_cloud_links=False
            )
            self.assertFalse(any("reference" in error for error in errors), errors)

    def test_ignores_escaped_markdown_link_openers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "SKILL.md").write_text(
                "---\nname: widget\ndescription: Fine.\n---\n\n"
                "\\[literal](not-a-link.md)\n"
                "\\[literal][missing-label]\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="private", require_cloud_links=False
            )
            self.assertFalse(any("reference" in error for error in errors), errors)

    def test_ignores_escaped_markdown_image_openers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "SKILL.md").write_text(
                "---\nname: widget\ndescription: Fine.\n---\n\n"
                "\\![literal](not-a-link.md)\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="private", require_cloud_links=False
            )
            self.assertFalse(any("reference" in error for error in errors), errors)

    def test_validates_inline_links_with_brackets_in_label(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "SKILL.md").write_text(
                "---\nname: widget\ndescription: Fine.\n---\n\n"
                "[Use `arr[0]`](MISSING.md)\n"
                "[See [legacy] notes](MISSING.md)\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="private", require_cloud_links=False
            )
            self.assertEqual(
                2,
                sum("reference does not resolve: MISSING.md" in error for error in errors),
                errors,
            )

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

    def test_validates_reference_style_image_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "SKILL.md").write_text(
                "---\nname: widget\ndescription: Fine.\n---\n\n"
                "![Diagram][architecture]\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="private", require_cloud_links=False
            )
            self.assertTrue(any("reference label is not defined" in error for error in errors))

    def test_ignores_markdown_links_inside_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "SKILL.md").write_text(
                "---\nname: widget\ndescription: Fine.\n---\n\n"
                "`[inline](not-real.md)`\n\n"
                "```markdown\n[example](also-not-real.md)\n```\n\n"
                "    [indented](also-not-real.md)\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="private", require_cloud_links=False
            )
            self.assertFalse(any("does not resolve" in error for error in errors), errors)

    def test_ignores_list_like_text_inside_top_level_indented_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "SKILL.md").write_text(
                "---\nname: widget\ndescription: Fine.\n---\n\n"
                "    - [literal](not-real.md)\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="private", require_cloud_links=False
            )
            self.assertFalse(any("not-real.md" in error for error in errors), errors)

    def test_ignores_markdown_links_inside_longer_closing_fence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "SKILL.md").write_text(
                "---\nname: widget\ndescription: Fine.\n---\n\n"
                "````markdown\n[example](also-not-real.md)\n`````\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="private", require_cloud_links=False
            )
            self.assertFalse(any("does not resolve" in error for error in errors), errors)

    def test_ignores_markdown_links_inside_list_nested_fences(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "SKILL.md").write_text(
                "---\nname: widget\ndescription: Fine.\n---\n\n"
                "- Example:\n"
                "    ```markdown\n"
                "    [literal](not-real.md)\n"
                "    ```\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="private", require_cloud_links=False
            )
            self.assertFalse(any("not-real.md" in error for error in errors), errors)

    def test_ignores_markdown_links_inside_multiline_inline_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "SKILL.md").write_text(
                "---\nname: widget\ndescription: Fine.\n---\n\n"
                "`[literal](not-real.md)\n"
                "still code`\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="private", require_cloud_links=False
            )
            self.assertFalse(any("not-real.md" in error for error in errors), errors)

    def test_closing_code_fence_allows_at_most_three_spaces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "SKILL.md").write_text(
                "---\nname: widget\ndescription: Fine.\n---\n\n"
                "```\n[example](not-real.md)\n"
                "    ```\n[real](also-not-real.md)\n```\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="private", require_cloud_links=False
            )
            self.assertFalse(any("not-real.md" in error for error in errors), errors)
            self.assertFalse(any("also-not-real.md" in error for error in errors), errors)

    def test_three_space_indented_closing_fence_ends_top_level_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "SKILL.md").write_text(
                "---\nname: widget\ndescription: Fine.\n---\n\n"
                "```\n[example](not-real.md)\n"
                "   ```\n"
                "[real](also-not-real.md)\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="private", require_cloud_links=False
            )
            self.assertFalse(
                any(error.endswith("reference does not resolve: not-real.md") for error in errors),
                errors,
            )
            self.assertTrue(
                any(
                    error.endswith("reference does not resolve: also-not-real.md")
                    for error in errors
                ),
                errors,
            )

    def test_validates_links_in_indented_list_continuations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "SKILL.md").write_text(
                "---\nname: widget\ndescription: Fine.\n---\n\n"
                "- References:\n"
                "    [Docs](MISSING.md)\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="private", require_cloud_links=False
            )
            self.assertTrue(any("reference does not resolve: MISSING.md" in error for error in errors))

    def test_validates_links_after_multiple_indented_list_continuations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "SKILL.md").write_text(
                "---\nname: widget\ndescription: Fine.\n---\n\n"
                "- Details:\n"
                "    explanatory continuation\n"
                "    [Docs](MISSING.md)\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="private", require_cloud_links=False
            )
            self.assertTrue(any("reference does not resolve: MISSING.md" in error for error in errors))

    def test_validates_links_in_nested_list_continuations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "SKILL.md").write_text(
                "---\nname: widget\ndescription: Fine.\n---\n\n"
                "- Outer:\n"
                "    - Inner:\n"
                "        [Docs](MISSING.md)\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="private", require_cloud_links=False
            )
            self.assertTrue(
                any("reference does not resolve: MISSING.md" in error for error in errors),
                errors,
            )

    def test_ignores_links_inside_indented_code_nested_in_lists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "SKILL.md").write_text(
                "---\nname: widget\ndescription: Fine.\n---\n\n"
                "- Example:\n"
                "        [literal](not-real.md)\n"
                "    [Docs](MISSING.md)\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="private", require_cloud_links=False
            )
            self.assertFalse(any("not-real.md" in error for error in errors), errors)
            self.assertTrue(any("reference does not resolve: MISSING.md" in error for error in errors))

    def test_derives_nested_code_indent_from_list_marker_width(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "SKILL.md").write_text(
                "---\nname: widget\ndescription: Fine.\n---\n\n"
                "- Example:\n"
                "\n"
                "      [literal](not-real.md)\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="private", require_cloud_links=False
            )
            self.assertFalse(any("not-real.md" in error for error in errors), errors)

    def test_rejects_skill_directory_without_skill_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            make_skill(repo)
            (repo / "skills" / "unfinished").mkdir()
            errors = VALIDATOR.validate_repo(
                repo, visibility="private", require_cloud_links=False
            )
            self.assertTrue(any("missing SKILL.md" in error for error in errors))

    @unittest.skipUnless(shutil.which("git"), "git binary is required for this test")
    def test_git_repo_ignores_untracked_skill_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            make_skill(repo)
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "add", "skills/widget"], cwd=repo, check=True)

            unfinished = repo / "skills" / "unfinished"
            unfinished.mkdir()
            (unfinished / "README.md").write_text("# Local draft\n", encoding="utf-8")
            errors = VALIDATOR.validate_repo(
                repo, visibility="private", require_cloud_links=False
            )
            self.assertFalse(any("missing SKILL.md" in error for error in errors), errors)

            subprocess.run(["git", "add", "skills/unfinished"], cwd=repo, check=True)
            errors = VALIDATOR.validate_repo(
                repo, visibility="private", require_cloud_links=False
            )
            self.assertTrue(any("missing SKILL.md" in error for error in errors), errors)

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
            self.assertTrue(all(tmp not in error for error in errors))

    def test_frontmatter_errors_use_repository_relative_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "SKILL.md").write_text(
                "---\nname: [unterminated\n---\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="private", require_cloud_links=False
            )
            self.assertTrue(any("invalid YAML frontmatter" in error for error in errors))
            self.assertTrue(all(tmp not in error for error in errors))

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

    def test_public_boundary_rejects_private_markers_in_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            make_skill(repo)
            private_path = repo / "Digitizers" / "marketing-" "skills"
            private_path.mkdir(parents=True)
            (private_path / "README.md").write_text("# Private name\n", encoding="utf-8")
            errors = VALIDATOR.validate_repo(
                repo, visibility="public", require_cloud_links=False
            )
            self.assertTrue(
                any("Digitizers/marketing-" "skills" in error for error in errors),
                errors,
            )

    def test_public_boundary_rejects_common_local_absolute_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "REFERENCE.md").write_text(
                "Local checkout: /work" "space/acme/private\n"
                "Install dir: /o" "pt/acme/tool\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="public", require_cloud_links=False
            )
            self.assertTrue(any("absolute path" in error for error in errors), errors)

    def test_public_boundary_rejects_credential_values_in_config_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "SKILL.md").write_text(
                "---\nname: widget\ndescription: Fine.\n---\n\n"
                "```bash\nAPI_TOKEN=sk-live-secret-value\n```\n",
                encoding="utf-8",
            )
            (repo / ".mcp.json").write_text(
                '{"API_TOKEN": "sk-live-secret-value"}\n',
                encoding="utf-8",
            )
            (repo / "deploy.sh").write_text(
                "API_TOKEN=sk-live-secret-value\n",
                encoding="utf-8",
            )
            (repo / "settings.py").write_text(
                'API_TOKEN = "sk-live-secret-value"\n',
                encoding="utf-8",
            )
            (repo / "client.py").write_text(
                'SDK(api_key="sk-live-secret-value")\n'
                'dict(API_TOKEN="sk-live-secret-value")\n',
                encoding="utf-8",
            )
            (repo / "notes.py").write_text(
                '# API_TOKEN = "sk-live-secret-value"\n'
                '"""Example:\nAPI_TOKEN = "sk-live-secret-value"\n"""\n',
                encoding="utf-8",
            )
            (repo / "config.yml").write_text(
                "client_secret: <replace-me>\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="public", require_cloud_links=False
            )
            self.assertTrue(
                any(".mcp.json" in error and "credential value" in error for error in errors),
                errors,
            )
            self.assertTrue(
                any("SKILL.md" in error and "credential value" in error for error in errors),
                errors,
            )
            self.assertTrue(
                any("deploy.sh" in error and "credential value" in error for error in errors),
                errors,
            )
            self.assertTrue(
                any("settings.py" in error and "credential value" in error for error in errors),
                errors,
            )
            self.assertEqual(
                2,
                sum("client.py" in error and "credential value" in error for error in errors),
                errors,
            )
            self.assertEqual(
                2,
                sum("notes.py" in error and "credential value" in error for error in errors),
                errors,
            )
            self.assertFalse(
                any("config.yml" in error and "credential value" in error for error in errors),
                errors,
            )

    def test_public_boundary_rejects_private_key_and_byte_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            make_skill(repo)
            (repo / "credentials.py").write_text(
                'PRIVATE_KEY = "live-private-key"\n'
                'API_TOKEN = b"live-byte-token"\n',
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="public", require_cloud_links=False
            )
            self.assertEqual(
                2,
                sum(
                    "credentials.py" in error and "credential value" in error
                    for error in errors
                ),
                errors,
            )

    def test_public_boundary_rejects_spaced_credential_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "REFERENCE.md").write_text(
                "API key: sk-live-secret-value\n"
                "access key: another-real-value\n"
                "private key: embedded-private-value\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="public", require_cloud_links=False
            )
            self.assertEqual(
                3,
                sum(
                    "REFERENCE.md" in error and "credential value" in error
                    for error in errors
                ),
                errors,
            )

    def test_public_boundary_accepts_placeholder_yaml_block_scalars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            make_skill(repo)
            (repo / "config.yaml").write_text(
                "api_token: |\n"
                "  ${API_TOKEN}\n"
                "private_key: >-\n"
                "  <your-private-key>\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="public", require_cloud_links=False
            )
            self.assertFalse(any("credential value" in error for error in errors), errors)

    def test_public_boundary_rejects_literal_yaml_block_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            make_skill(repo)
            (repo / "config.yaml").write_text(
                "api_token: |\n"
                "  sk-live-secret-value\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="public", require_cloud_links=False
            )
            self.assertTrue(
                any("config.yaml" in error and "credential value" in error for error in errors),
                errors,
            )

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

    def test_public_boundary_rejects_common_environment_file_variants(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            make_skill(repo)
            (repo / ".envrc").write_text("TOKEN=secret\n", encoding="utf-8")
            (repo / "prod.env").write_text("TOKEN=secret\n", encoding="utf-8")
            errors = VALIDATOR.validate_repo(
                repo, visibility="public", require_cloud_links=False
            )
            self.assertTrue(any(".envrc" in error for error in errors))
            self.assertTrue(any("prod.env" in error for error in errors))

    @unittest.skipUnless(shutil.which("git"), "git binary is required for this test")
    def test_public_boundary_ignores_untracked_local_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            make_skill(repo)
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "add", "skills"], cwd=repo, check=True)
            (repo / ".env.local").write_text("TOKEN=secret\n", encoding="utf-8")
            errors = VALIDATOR.validate_repo(
                repo, visibility="public", require_cloud_links=False
            )
            self.assertFalse(any(".env.local" in error for error in errors), errors)

            subprocess.run(["git", "add", ".env.local"], cwd=repo, check=True)
            errors = VALIDATOR.validate_repo(
                repo, visibility="public", require_cloud_links=False
            )
            self.assertTrue(any(".env.local" in error for error in errors), errors)

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

    def test_public_boundary_validates_prefixed_env_templates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            make_skill(repo)
            template = repo / "app.env.example"
            template.write_text("API_TOKEN=real-looking-value\n", encoding="utf-8")
            errors = VALIDATOR.validate_repo(
                repo, visibility="public", require_cloud_links=False
            )
            self.assertTrue(
                any("app.env.example" in error and "non-placeholder" in error for error in errors)
            )

    def test_public_boundary_matches_private_markers_case_insensitively(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "REFERENCE.md").write_text(
                "https://github.com/digitizers/"
                "marketing-skills\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="public", require_cloud_links=False
            )
            self.assertTrue(any("private identifier" in error for error in errors))

    def test_public_boundary_matches_private_markers_in_symlink_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            make_skill(repo)
            (repo / "skill-link").symlink_to("digitizers/marketing-" "skills")
            errors = VALIDATOR.validate_repo(
                repo, visibility="public", require_cloud_links=False
            )
            self.assertTrue(any("symlink target" in error for error in errors), errors)

    def test_fallback_public_boundary_walk_does_not_follow_symlink_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            external = Path(f"{tmp}-external")
            external.mkdir()
            (external / "leak.md").write_text("/work" "space/acme/private\n", encoding="utf-8")
            (repo / "link").symlink_to(external, target_is_directory=True)
            paths = VALIDATOR.fallback_public_boundary_paths(repo)
            self.assertIn(repo / "link", paths)
            self.assertNotIn(repo / "link" / "leak.md", paths)

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

    def test_public_boundary_rejects_external_symlink_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            make_skill(repo)
            (repo / "external").symlink_to("/" "root/private-skills")
            errors = VALIDATOR.validate_repo(
                repo, visibility="public", require_cloud_links=False
            )
            self.assertTrue(any("symlink target escapes" in error for error in errors))

    def test_git_tracked_paths_decodes_filesystem_bytes(self) -> None:
        original_run = VALIDATOR.subprocess.run

        class Result:
            stdout = b"skills/\xff/SKILL.md\0"

        try:
            VALIDATOR.subprocess.run = lambda *args, **kwargs: Result()
            paths = VALIDATOR.git_tracked_paths(Path("/repo"))
        finally:
            VALIDATOR.subprocess.run = original_run

        self.assertEqual([Path("/repo") / "skills" / "\udcff" / "SKILL.md"], paths)

    def test_no_valid_skills_preserves_invalid_skill_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "skills" / "unfinished").mkdir(parents=True)
            errors = VALIDATOR.validate_repo(
                repo, visibility="private", require_cloud_links=False
            )
            self.assertTrue(any("missing SKILL.md" in error for error in errors), errors)
            self.assertTrue(any("no canonical skills found" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
