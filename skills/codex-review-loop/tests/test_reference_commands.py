#!/usr/bin/env python3
"""Execute the documented read-only poll against a fixture gh, never GitHub.

Run: python3 -m unittest discover -s skills/codex-review-loop/tests -p 'test_*.py'
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

REFERENCE = Path(__file__).resolve().parents[1] / "REFERENCE.md"
HEAD = "a" * 40
OLDER = "b" * 40

# Behave like gh pagination: without --paginate return only the first page;
# without --slurp emit separate page arrays. This exposes dropped pages in the
# actual copyable command rather than asserting that it contains flag text.
FAKE_GH = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
root = pathlib.Path(os.environ["FIXTURE_ROOT"])
f = json.loads((root / "fixture.json").read_text())
args = sys.argv[1:]
with (root / "calls.jsonl").open("a") as log:
    log.write(json.dumps(args) + "\n")
endpoint = next(a for a in args if a.startswith("repos/"))
if endpoint == "repos/acme/app/pulls/42":
    counter = root / "head-reads"
    n = int(counter.read_text()) if counter.exists() else 0
    counter.write_text(str(n + 1))
    key = "head_before" if n == 0 else "head_after"
    output = f[key]
else:
    key = {"repos/acme/app/pulls/42/comments": "inline",
           "repos/acme/app/pulls/42/reviews": "reviews",
           "repos/acme/app/issues/42/comments": "issues"}[endpoint]
    pages = f[key]
    if "--paginate" not in args:
        pages = pages[:1]
    output = json.dumps(pages) if "--slurp" in args else "\n".join(map(json.dumps, pages))
if f.get("malformed") == key:
    output = "{broken json"
if f.get("wrong_shape") == key:
    output = json.dumps({"message": "API error disguised as JSON"})
if f.get("empty_stream") == key:
    output = ""
print(output)
if f.get("fail") == key:
    print("fixture: request failed for " + key, file=sys.stderr)
    sys.exit(1)
'''


def comment(number: int, **fields: object) -> dict:
    return {"id": number, "body": "context " * 90 + "\nP1: the actual finding is here",
            "user": {"login": "codex-bot", "type": "Bot"},
            "path": "app.py", "line": 10, "subject_type": "line",
            "commit_id": HEAD, "original_commit_id": OLDER, "original_line": 5,
            "updated_at": "2026-01-01T00:00:00Z", **fields}


class ReferencePollTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not shutil.which("bash") or not shutil.which("jq"):
            raise RuntimeError("The documented poll requires bash and jq for its tests")
        section = REFERENCE.read_text().split("<!-- review-snapshot:start -->")[1]
        cls.command = re.search(r"```bash\n(.*?)\n```", section, re.S).group(1)
        cls.command = cls.command.replace("<owner>/<repo>", "acme/app").replace("<PR>", "42")

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "bin").mkdir()
        gh = self.root / "bin/gh"
        gh.write_text(FAKE_GH)
        gh.chmod(0o755)
        (self.root / "tmp").mkdir()
        self.fixture = {"head_before": HEAD, "head_after": HEAD,
                        "inline": [[]], "reviews": [[]], "issues": [[]]}

    def run_poll(self) -> subprocess.CompletedProcess:
        (self.root / "fixture.json").write_text(json.dumps(self.fixture))
        (self.root / "head-reads").unlink(missing_ok=True)
        return subprocess.run(["bash", "-c", self.command], text=True, capture_output=True,
                              env={**os.environ, "PATH": str(self.root / "bin") + os.pathsep + os.environ["PATH"],
                                   "FIXTURE_ROOT": str(self.root), "TMPDIR": str(self.root / "tmp")})

    def snapshot(self) -> dict:
        result = self.run_poll()
        self.assertEqual(result.returncode, 0, result.stderr)
        path = Path(result.stdout.strip())
        self.assertTrue(path.is_file(), result.stdout)
        return json.loads(path.read_text())

    def test_all_pages_and_full_bodies_on_every_surface(self) -> None:
        for surface in ("inline", "reviews", "issues"):
            self.fixture[surface] = [[comment(i) for i in range(100)], [comment(100)]]
        snapshot = self.snapshot()
        self.assertEqual(snapshot["head"], HEAD)
        for surface in ("inline", "reviews", "issues"):
            self.assertEqual(snapshot[surface], sum(self.fixture[surface], []))

    def test_no_filter_drops_file_outdated_other_bot_or_human_findings(self) -> None:
        records = [comment(1, line=None, subject_type="file"),
                   comment(2, line=None),
                   comment(3, user={"login": "other-reviewer", "type": "Bot"}),
                   comment(4, user={"login": "human", "type": "User"}),
                   comment(5, commit_id=OLDER)]
        self.fixture["inline"] = [records]
        self.assertEqual(self.snapshot()["inline"], records)

    def test_same_id_keeps_edited_body_and_old_anchor(self) -> None:
        record = comment(1, line=None)
        self.fixture["inline"] = [[record]]
        before = self.snapshot()
        record.update(body="P1: the attempted fix did not fix this", updated_at="2026-01-02T00:00:00Z")
        after = self.snapshot()
        self.assertEqual(before["inline"][0]["id"], after["inline"][0]["id"])
        self.assertNotEqual(before["inline"][0]["body"], after["inline"][0]["body"])
        self.assertEqual(after["inline"][0], record)

    def test_empty_poll_and_wrapper_are_evidence_not_a_clean_verdict(self) -> None:
        self.assertEqual(self.snapshot(), {"head": HEAD, "inline": [], "reviews": [], "issues": []})
        wrapper = comment(7, body="Here are some automated review suggestions", commit_id=HEAD)
        self.fixture["reviews"] = [[wrapper]]
        for _ in range(2):
            self.assertEqual(self.snapshot(), {"head": HEAD, "inline": [], "reviews": [wrapper], "issues": []})

    def test_stale_verdict_and_substantive_review_bodies_are_preserved(self) -> None:
        self.fixture["issues"] = [[comment(3, body="Didn't find any major issues. Reviewed commit: " + OLDER + "\nCompare link: " + HEAD)]]
        self.fixture["reviews"] = [[comment(4, body="P1: review-body-only finding", commit_id=HEAD)]]
        snapshot = self.snapshot()
        self.assertEqual(snapshot["issues"], self.fixture["issues"][0])
        self.assertEqual(snapshot["reviews"], self.fixture["reviews"][0])

    def assert_failed_without_snapshot(self) -> None:
        result = self.run_poll()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(result.stdout, "")
        self.assertTrue(result.stderr)
        self.assertEqual(list((self.root / "tmp").iterdir()), [])

    def test_each_api_failure_stays_failure_even_with_valid_stdout(self) -> None:
        for surface in ("head_before", "inline", "reviews", "issues", "head_after"):
            with self.subTest(surface=surface):
                self.fixture["fail"] = surface
                self.assert_failed_without_snapshot()

    def test_bad_json_and_wrong_response_shapes_fail_closed(self) -> None:
        for mode in ("malformed", "wrong_shape", "empty_stream"):
            for surface in ("inline", "reviews", "issues"):
                with self.subTest(mode=mode, surface=surface):
                    self.fixture[mode] = surface
                    self.assert_failed_without_snapshot()
                    del self.fixture[mode]

    def test_head_change_invalidates_snapshot(self) -> None:
        self.fixture["head_after"] = OLDER
        self.assert_failed_without_snapshot()

    def test_invalid_head_never_produces_evidence(self) -> None:
        self.fixture["head_before"] = "null"
        self.assert_failed_without_snapshot()


if __name__ == "__main__":
    unittest.main()
