from __future__ import annotations

import importlib.util
import json
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
            # One error per unique broken target per document: both labels
            # point at MISSING.md, and a used reference definition would
            # otherwise double-report through link + definition.
            self.assertEqual(
                1,
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

    def test_undefined_reference_label_is_literal_text_not_an_error(self) -> None:
        # Per CommonMark an undefined label renders as literal text — it is
        # not a link, so the parser-backed validator has nothing to check.
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "SKILL.md").write_text(
                "---\nname: widget\ndescription: Fine.\n---\n\n"
                "![Diagram][architecture]\n\n[Text][no-such-label]\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="private", require_cloud_links=False
            )
            self.assertFalse(any("reference" in error for error in errors), errors)

    def test_validates_unused_reference_definitions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "SKILL.md").write_text(
                "---\nname: widget\ndescription: Fine.\n---\n\n"
                "No link uses this definition.\n\n[orphan]: MISSING.md\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="private", require_cloud_links=False
            )
            self.assertTrue(any("does not resolve" in error for error in errors), errors)

    def test_finds_links_in_nested_lists_and_blockquotes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "SKILL.md").write_text(
                "---\nname: widget\ndescription: Fine.\n---\n\n"
                "- outer\n  - inner [broken](nested-missing.md)\n\n"
                "> quoted [also broken](quoted-missing.md)\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="private", require_cloud_links=False
            )
            self.assertTrue(any("nested-missing.md" in error for error in errors), errors)
            self.assertTrue(any("quoted-missing.md" in error for error in errors), errors)

    def test_ignores_links_in_indented_code_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "SKILL.md").write_text(
                "---\nname: widget\ndescription: Fine.\n---\n\n"
                "Paragraph.\n\n    [example](indented-not-real.md)\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="private", require_cloud_links=False
            )
            self.assertFalse(any("indented-not-real.md" in error for error in errors), errors)

    def test_flags_backslash_windows_drive_destination(self) -> None:
        # markdown-it percent-encodes the backslashes (C:%5C...), which must
        # not be mistaken for a "C:" URI scheme and silently accepted.
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "SKILL.md").write_text(
                "---\nname: widget\ndescription: Fine.\n---\n\n"
                "[guide](C:\\Projects\\guide.md)\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="private", require_cloud_links=False
            )
            self.assertTrue(
                any(
                    "does not resolve" in error or "escapes repository" in error
                    for error in errors
                ),
                errors,
            )

    def test_escaped_destination_and_autolink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "SKILL.md").write_text(
                "---\nname: widget\ndescription: Fine.\n---\n\n"
                "[escaped](REFERENCE\\.md)\n\n<https://example.com/x?y=1>\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="private", require_cloud_links=False
            )
            self.assertFalse(any("REFERENCE" in error for error in errors), errors)
            self.assertFalse(any("example.com" in error for error in errors), errors)

    def test_ignores_markdown_links_inside_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "SKILL.md").write_text(
                "---\nname: widget\ndescription: Fine.\n---\n\n"
                "`[inline](not-real.md)`\n\n"
                "```markdown\n[example](also-not-real.md)\n```\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(
                repo, visibility="private", require_cloud_links=False
            )
            self.assertFalse(any("does not resolve" in error for error in errors), errors)

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
            (repo / ".ENV").write_text("TOKEN=secret\n", encoding="utf-8")
            (repo / "production.ENV").write_text("TOKEN=secret\n", encoding="utf-8")
            errors = VALIDATOR.validate_repo(
                repo, visibility="public", require_cloud_links=False
            )
            self.assertTrue(any(".envrc" in error for error in errors))
            self.assertTrue(any("prod.env" in error for error in errors))
            self.assertTrue(any(".ENV" in error for error in errors))
            self.assertTrue(any("production.ENV" in error for error in errors))

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

    def test_public_boundary_rejects_bare_local_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "REFERENCE.md").write_text(
                "cd /workspace\ncd /opt\n", encoding="utf-8"
            )
            errors = VALIDATOR.validate_repo(repo, visibility="public", require_cloud_links=False)
            self.assertTrue(any("absolute path" in error for error in errors), errors)

    def test_public_boundary_rejects_trailing_slash_local_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            workspace_root = "/" + "workspace/"
            opt_root = "/" + "opt/"
            (skill / "REFERENCE.md").write_text(
                f"cd {workspace_root}\ncd {opt_root}\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(repo, visibility="public", require_cloud_links=False)
            self.assertTrue(any("absolute path" in error for error in errors), errors)

    def test_public_boundary_allows_urls_with_local_root_hostnames(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "REFERENCE.md").write_text(
                "https://workspace.google.com/docs\nhttps://opt.example.com/tool\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(repo, visibility="public", require_cloud_links=False)
            self.assertFalse(any("absolute path" in error for error in errors), errors)

    def test_public_boundary_rejects_bracket_delimited_local_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            local_root = "/" + "workspace"
            (skill / "REFERENCE.md").write_text(
                f"[{local_root}] {{{local_root}}} <{local_root}>\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(repo, visibility="public", require_cloud_links=False)
            self.assertTrue(any("absolute path" in error for error in errors), errors)

    def test_public_boundary_rejects_bare_roots_before_punctuation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "REFERENCE.md").write_text(
                "Use (/" "workspace). Install under /" "opt, then continue.\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(repo, visibility="public", require_cloud_links=False)
            self.assertTrue(any("absolute path" in error for error in errors), errors)

    def test_public_boundary_rejects_windows_absolute_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            make_skill(repo)
            (repo / "windows-link").symlink_to(r"C:\Projects\private-skills")
            errors = VALIDATOR.validate_repo(repo, visibility="public", require_cloud_links=False)
            self.assertTrue(any("symlink target escapes" in error for error in errors), errors)

    def test_rejects_windows_drive_markdown_destinations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "REFERENCE.md").write_text(
                "[guide](C:/Projects/guide.md)\n", encoding="utf-8"
            )
            errors = VALIDATOR.validate_repo(repo, visibility="private", require_cloud_links=False)
            self.assertTrue(any("does not resolve" in error for error in errors), errors)

    def test_public_boundary_rejects_forward_slash_windows_homes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            user_home = "/" + "Users/alice/private"
            drive_home = "C:" + user_home
            unc_home = "//server" + user_home
            (skill / "REFERENCE.md").write_text(
                f"{drive_home}\n{unc_home}\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(repo, visibility="public", require_cloud_links=False)
            self.assertTrue(any("absolute path" in error for error in errors), errors)

    def test_public_boundary_allows_uri_authorities_with_users_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "REFERENCE.md").write_text(
                "https://server/Users/alice/guide\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(repo, visibility="public", require_cloud_links=False)
            self.assertFalse(any("absolute path" in error for error in errors), errors)

    def test_public_boundary_rejects_windows_homes_after_colons(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            user_home = "Users" + r"\alice\secret"
            drive_home = "home:C:" + "\\" + user_home
            unc_home = "home:" + "//server/" + user_home.replace("\\", "/")
            (skill / "REFERENCE.md").write_text(
                f"{drive_home}\n{unc_home}\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(repo, visibility="public", require_cloud_links=False)
            self.assertTrue(any("absolute path" in error for error in errors), errors)

    def test_public_boundary_rejects_env_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            make_skill(repo)
            (repo / ".env.example").write_text("API_TOKEN=<replace-me>\n", encoding="utf-8")
            (repo / ".env").symlink_to(".env.example")
            errors = VALIDATOR.validate_repo(repo, visibility="public", require_cloud_links=False)
            self.assertTrue(any(".env" in error and "forbidden" in error for error in errors), errors)

    def test_public_boundary_rejects_env_template_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            make_skill(repo)
            config = repo / "config"
            config.mkdir()
            (config / "secrets.txt").write_text(
                "API_TOKEN=real-secret-value\n",
                encoding="utf-8",
            )
            (repo / ".env.example").symlink_to("config/secrets.txt")
            errors = VALIDATOR.validate_repo(repo, visibility="public", require_cloud_links=False)
            self.assertTrue(
                any(".env.example" in error and "regular file" in error for error in errors),
                errors,
            )

    def test_accepts_query_strings_on_resolving_local_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "SKILL.md").write_text(
                "---\nname: widget\ndescription: Fine.\n---\n"
                "[ref](REFERENCE.md?raw=1#details)\n",
                encoding="utf-8",
            )
            self.assertEqual([], VALIDATOR.validate_repo(repo, visibility="private", require_cloud_links=False))

    def test_ignores_escaped_markdown_link_openers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "REFERENCE.md").write_text(r"\[label](missing.md)", encoding="utf-8")
            self.assertEqual([], VALIDATOR.validate_repo(repo, visibility="private", require_cloud_links=False))

    def test_ignores_links_inside_list_and_quote_fences(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "REFERENCE.md").write_text(
                "- Example:\n    ```\n    [x](missing.md)\n    ```\n"
                "> ```\n> [y](missing.md)\n> ```\n",
                encoding="utf-8",
            )
            self.assertEqual([], VALIDATOR.validate_repo(repo, visibility="private", require_cloud_links=False))

    def test_duplicate_reference_labels_use_first_definition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "EXISTS.md").write_text("ok\n", encoding="utf-8")
            (skill / "REFERENCE.md").write_text(
                "[guide]: MISSING.md\n[guide]: EXISTS.md\n[go][guide]\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(repo, visibility="private", require_cloud_links=False)
            self.assertTrue(any("MISSING.md" in error for error in errors), errors)

    def test_ignores_fences_in_nested_lists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "REFERENCE.md").write_text(
                "- outer\n    - inner\n        ```\n        [x](missing.md)\n        ```\n",
                encoding="utf-8",
            )
            self.assertEqual([], VALIDATOR.validate_repo(repo, visibility="private", require_cloud_links=False))

    def test_ignores_escaped_reference_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "REFERENCE.md").write_text(r"\[label][missing]", encoding="utf-8")
            self.assertEqual([], VALIDATOR.validate_repo(repo, visibility="private", require_cloud_links=False))

    def test_normalizes_reference_label_whitespace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "REFERENCE.md").write_text(
                "[a b]: SKILL.md\n[go][a   b]\n", encoding="utf-8"
            )
            self.assertEqual([], VALIDATOR.validate_repo(repo, visibility="private", require_cloud_links=False))

    def test_accepts_case_insensitive_external_uri_schemes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "REFERENCE.md").write_text(
                "[web](HTTPS://example.com)\n[phone](tel:+123)\n", encoding="utf-8"
            )
            self.assertEqual([], VALIDATOR.validate_repo(repo, visibility="private", require_cloud_links=False))

    def test_list_fence_closer_allows_relative_three_space_indent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "REFERENCE.md").write_text(
                "- item\n"
                "  ```\n"
                "  [example](missing.md)\n"
                "     ```\n"
                "[real](also-missing.md)\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate_repo(repo, visibility="private", require_cloud_links=False)
            self.assertFalse(any("missing.md" in error and "also-" not in error for error in errors), errors)
            self.assertTrue(any("also-missing.md" in error for error in errors), errors)

    def test_unterminated_quote_fence_ends_at_container_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "REFERENCE.md").write_text(
                "> ```\n> example\n[real](MISSING.md)\n", encoding="utf-8"
            )
            errors = VALIDATOR.validate_repo(repo, visibility="private", require_cloud_links=False)
            self.assertTrue(any("MISSING.md" in error for error in errors), errors)

    def test_reference_definitions_inside_quotes_resolve(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "REFERENCE.md").write_text(
                "> [ref]: SKILL.md\n> [go][ref]\n", encoding="utf-8"
            )
            self.assertEqual([], VALIDATOR.validate_repo(repo, visibility="private", require_cloud_links=False))

    def test_unmatched_backtick_runs_leave_links_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            skill = make_skill(repo)
            (skill / "REFERENCE.md").write_text(
                "``[real](MISSING.md)`\n", encoding="utf-8"
            )
            errors = VALIDATOR.validate_repo(repo, visibility="private", require_cloud_links=False)
            self.assertTrue(any("MISSING.md" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
