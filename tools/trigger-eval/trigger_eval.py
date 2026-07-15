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


class ProbeError(Exception):
    """The claude subprocess failed, so its run is not a usable measurement."""


def assert_not_shadowed(skill_name: str, skill_dir: Path) -> None:
    """Refuse to probe a skill that a personal skill of the same name overrides.

    Personal skills in ~/.claude/skills apply to every project and take
    precedence over a project skill with the same name. So a stale personal
    copy silently wins the probe, and the harness reports confident numbers for
    a description that is not the one in the working tree — the exact silent
    failure this tool exists to catch, turned on itself.

    The common case is benign: the personal skill is a symlink to the working
    tree under test, so both names resolve to the same file and the probe is
    valid. Resolve and compare rather than banning the setup outright.
    """
    personal = Path.home() / ".claude" / "skills" / skill_name
    if not personal.exists():
        return
    if personal.resolve() == skill_dir.resolve():
        return  # same file by another name — the probe measures what we think
    raise SystemExit(
        f"refusing to probe {skill_name!r}: a personal skill shadows it and would win.\n"
        f"  personal: {personal} -> {personal.resolve()}\n"
        f"  under test: {skill_dir.resolve()}\n"
        f"Personal skills override project skills of the same name, so these results "
        f"would describe the personal copy, not the working tree.\n"
        f"Remove or re-point the personal skill, or pass --allow-shadow if you accept that."
    )


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
        # True isolation needs three cuts, not one. The temp project drops other
        # *project* skills; --setting-sources project (below) drops *personal*
        # skills; and disableBundledSkills drops the built-ins (/code-review,
        # /loop, /review, …) that load in every session regardless. Without this
        # last one a bundled skill can win the routing contest and the probe
        # reports a false negative for the skill under test.
        (project / ".claude" / "settings.json").write_text(
            json.dumps({"disableBundledSkills": True})
        )
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
            # Load only project settings. Without this the probe runs against the
            # developer's full session — every personal skill in ~/.claude/skills
            # — so a differently-named personal skill in the same domain can win
            # routing and the harness reports a false negative for the skill under
            # test. The temp project holds only the skill under test, so
            # project-only sources isolate it. (assert_not_shadowed handles the
            # same-name case; this handles the different-name case.)
            "--setting-sources", "project",
        ]
        if model:
            cmd += ["--model", model]

        # stderr goes to a file, not DEVNULL: a probe that dies before streaming
        # a trigger (expired auth, an unsupported flag, an unavailable model)
        # must not be silently scored as "did not trigger" — that turns a broken
        # measurement into data, the exact failure this tool exists to catch. A
        # file rather than a PIPE avoids a buffer-fill deadlock while we read
        # stdout. (Kept small: we only ever look at the tail.)
        errf = project / "stderr.log"
        timed_out = threading.Event()

        def on_timeout() -> None:
            timed_out.set()
            proc.kill()

        with errf.open("w+") as err:
            proc = subprocess.Popen(
                cmd, cwd=project, env=env,
                stdout=subprocess.PIPE, stderr=err, text=True,
            )
            # A non-triggering query has no natural end — the agent just keeps
            # working. Without this the probe blocks on stdout forever.
            watchdog = threading.Timer(timeout, on_timeout)
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
                    if _is_trigger(event, skill_name, project):
                        return True  # decided; the finally kills the process
            finally:
                watchdog.cancel()
                proc.kill()
                proc.wait(timeout=10)

            if timed_out.is_set():
                return False  # ran the full window without triggering — a real "no"

            # stdout closed on its own. returncode 0 = the agent finished without
            # loading the skill (a legitimate no-trigger). Non-zero = the probe
            # itself failed, and a failed probe is not a data point.
            if proc.returncode not in (0, None):
                err.seek(0)
                tail = err.read().strip().splitlines()[-5:]
                raise ProbeError(
                    f"claude exited {proc.returncode} on query {query!r} without a trigger.\n"
                    + "\n".join(f"    {l}" for l in tail)
                )
            return False


def _same_skill(emitted: object, skill_name: str) -> bool:
    """Exact-match the emitted skill name against the one under test.

    A substring test (`skill_name in emitted`) is wrong: probing a skill named
    `review` would count a fire of the bundled `code-review`, and `deploy` would
    count `apps/web:deploy`. Compare the leaf name exactly, after dropping any
    `plugin:` / directory namespace prefix Claude Code may attach.
    """
    leaf = str(emitted).split(":")[-1].strip()
    return leaf == skill_name


def _is_trigger(event: dict, skill_name: str, project: Path) -> bool:
    content = (event.get("message") or {}).get("content") or []
    if not isinstance(content, list):
        return False
    # Anchor to the copy under test. Matching a bare "/<name>/SKILL.md" would
    # also count a Read of ~/.claude/skills/<name>/SKILL.md — crediting the
    # probe to a file we are not testing.
    under_test = str((project / ".claude" / "skills" / skill_name / "SKILL.md").resolve())
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = block.get("name")
        args = block.get("input") or {}
        if name == "Skill" and _same_skill(args.get("skill", ""), skill_name):
            return True
        # A direct Read of the skill file is a load by another route.
        if name == "Read":
            path = str(args.get("file_path", ""))
            if path and Path(path).resolve(strict=False) == Path(under_test):
                return True
    return False


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--skill", required=True, help="Skill directory name, e.g. gha-optimizer")
    p.add_argument("--eval-set", required=True, help="Path to the eval set JSON")
    p.add_argument("--runs", type=int, default=5,
                   help="Runs per query. Triggering is stochastic and borderline queries have "
                        "high variance — a rate that reads 100%% at 3 runs can be 15%% at 7. "
                        "5 is a floor for a read, not a verdict; use more to characterize a "
                        "borderline case.")
    p.add_argument("--threshold", type=float, default=0.5, help="Trigger rate that counts as a pass")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--timeout", type=int, default=90)
    p.add_argument("--model", default=None)
    p.add_argument("--git", action="store_true",
                   help="Probe inside a git repo with a GitHub remote. Required for any skill "
                        "whose description states a git/GitHub precondition — without it you "
                        "measure the empty fixture, not the description.")
    p.add_argument("--allow-shadow", action="store_true",
                   help="Probe even when a differing personal skill of the same name would "
                        "override the one under test. The numbers then describe the personal "
                        "copy; you almost never want this.")
    args = p.parse_args()

    skill_dir = REPO_ROOT / args.skill
    if not (skill_dir / "SKILL.md").is_file():
        print(f"no SKILL.md in {skill_dir}", file=sys.stderr)
        return 2

    if not args.allow_shadow:
        assert_not_shadowed(args.skill, skill_dir)

    eval_set = json.loads(Path(args.eval_set).read_text())

    jobs = [(item, i) for item in eval_set for i in range(args.runs)]
    hits: dict[str, list[bool]] = {}

    probe_errors: list[str] = []
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
            except ProbeError as e:
                probe_errors.append(str(e))
            except Exception as e:  # noqa: BLE001 — anything unexpected is also not data
                probe_errors.append(repr(e))

    # A broken probe is not a zero — reporting a rate built on failed subprocesses
    # would be the silent-failure this tool exists to prevent. Abort loud instead.
    if probe_errors:
        print(f"\n{args.skill}: {len(probe_errors)} probe(s) failed — results withheld.\n",
              file=sys.stderr)
        for e in probe_errors[:5]:
            print(f"  {e}", file=sys.stderr)
        return 2

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
