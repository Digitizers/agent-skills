#!/usr/bin/env python3
"""Measure whether a skill's `description` actually makes the agent load it.

A skill that never triggers is indistinguishable from a skill that does not
exist. This tests the description, not the body: it installs the real skill
into a throwaway project, runs `claude -p <query>` there, and watches the
stream for the agent reaching for that skill.

Eval set is a JSON list of {"query": str, "should_trigger": bool}. Negative
cases matter as much as positive ones — a description that fires on everything
is as broken as one that never fires.

    python3 tools/trigger-eval/trigger_eval.py \
        --skill gha-optimizer \
        --eval-set gha-optimizer/evals/triggers.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def make_git_fixture(project: Path) -> None:
    """Turn the scratch project into a plausible git + GitHub repo.

    Skills whose description states a precondition ("works in any git + GitHub
    repo") are judged against the context they land in. Probed in a bare temp
    directory they can score badly for a reason that has nothing to do with the
    description — the agent simply sees no repo. Without this, the harness
    measures its own fixture.
    """
    run = lambda *a: subprocess.run(a, cwd=project, check=True,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    (project / "README.md").write_text("# demo\n\nThis project recieves webhooks.\n")
    (project / "src").mkdir(exist_ok=True)
    (project / "src" / "index.ts").write_text("export const hello = () => 'hi'\n")
    run("git", "init", "-q", "-b", "main")
    run("git", "config", "user.email", "eval@example.com")
    run("git", "config", "user.name", "eval")
    run("git", "add", "-A")
    run("git", "commit", "-q", "-m", "init")
    run("git", "remote", "add", "origin", "https://github.com/acme/demo.git")


def probe(skill_dir: Path, skill_name: str, query: str, timeout: int, model: str | None,
          git: bool = False) -> bool:
    """Run one query against a scratch project holding only this skill.

    Returns True if the agent invoked the skill (Skill tool) or read its
    SKILL.md directly — both mean the description won the agent's attention.
    Kills the subprocess the moment it sees a trigger: we are measuring the
    decision to load, not whatever the skill goes on to do. That also keeps
    the spawned agent from doing real work.
    """
    with tempfile.TemporaryDirectory(prefix=f"trigeval-{skill_name}-") as tmp:
        project = Path(tmp)
        shutil.copytree(skill_dir, project / ".claude" / "skills" / skill_name)
        if git:
            make_git_fixture(project)

        # `claude` refuses to nest inside a Claude Code session unless CLAUDECODE
        # is cleared. The guard exists for interactive terminal conflicts; a
        # captured subprocess is fine.
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

        cmd = [
            "claude", "-p", query,
            "--output-format", "stream-json",
            "--verbose",
            "--include-partial-messages",
        ]
        if model:
            cmd += ["--model", model]

        proc = subprocess.Popen(
            cmd, cwd=project, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
        # A non-triggering query has no natural end — the agent just keeps
        # working. Without this the probe blocks on stdout forever.
        watchdog = threading.Timer(timeout, proc.kill)
        watchdog.start()
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if _is_trigger(event, skill_name):
                    return True
            return False
        except Exception:
            return False
        finally:
            watchdog.cancel()
            proc.kill()
            proc.wait(timeout=10)


def _is_trigger(event: dict, skill_name: str) -> bool:
    content = (event.get("message") or {}).get("content") or []
    if not isinstance(content, list):
        return False
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = block.get("name")
        args = block.get("input") or {}
        if name == "Skill" and skill_name in str(args.get("skill", "")):
            return True
        # A direct Read of the skill file is a load by another route.
        if name == "Read" and f"/{skill_name}/SKILL.md" in str(args.get("file_path", "")):
            return True
    return False


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--skill", required=True, help="Skill directory name, e.g. gha-optimizer")
    p.add_argument("--eval-set", required=True, help="Path to the eval set JSON")
    p.add_argument("--runs", type=int, default=3, help="Runs per query; triggering is stochastic")
    p.add_argument("--threshold", type=float, default=0.5, help="Trigger rate that counts as a pass")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--timeout", type=int, default=90)
    p.add_argument("--model", default=None)
    p.add_argument("--git", action="store_true",
                   help="Probe inside a git repo with a GitHub remote. Required for any skill "
                        "whose description states a git/GitHub precondition — without it you "
                        "measure the empty fixture, not the description.")
    args = p.parse_args()

    skill_dir = REPO_ROOT / args.skill
    if not (skill_dir / "SKILL.md").is_file():
        print(f"no SKILL.md in {skill_dir}", file=sys.stderr)
        return 2

    eval_set = json.loads(Path(args.eval_set).read_text())

    jobs = [(item, i) for item in eval_set for i in range(args.runs)]
    hits: dict[str, list[bool]] = {}

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(probe, skill_dir, args.skill, item["query"], args.timeout,
                        args.model, args.git): item
            for item, _ in jobs
        }
        for f in as_completed(futures):
            item = futures[f]
            try:
                hits.setdefault(item["query"], []).append(f.result())
            except Exception as e:
                print(f"query failed: {e}", file=sys.stderr)
                hits.setdefault(item["query"], []).append(False)

    failures = 0
    print(f"\n{args.skill}\n")
    for item in eval_set:
        runs = hits.get(item["query"], [])
        rate = sum(runs) / len(runs) if runs else 0.0
        want = item["should_trigger"]
        ok = rate >= args.threshold if want else rate < args.threshold
        failures += 0 if ok else 1
        print(f"  {'pass' if ok else 'FAIL'}  {rate:>4.0%} fired  "
              f"{'should' if want else 'should NOT'}  {item['query'][:64]}")

    total = len(eval_set)
    print(f"\n  {total - failures}/{total} passed\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
