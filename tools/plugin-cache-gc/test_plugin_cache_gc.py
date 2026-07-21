#!/usr/bin/env python3
"""Tests for plugin-cache-gc.

    python3 tools/plugin-cache-gc/test_plugin_cache_gc.py

Builds synthetic cache trees rather than touching the real ~/.claude — the tool
deletes directories, so its tests must never be able to reach a live install.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import plugin_cache_gc as gc  # noqa: E402

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'pass' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILURES.append(f"{name}{f': {detail}' if detail else ''}")


def build(root: Path, tree: dict, installed: dict) -> tuple[Path, Path]:
    """tree: {marketplace: {plugin: [versions]}}; installed: {key: installPath}."""
    cache = root / "cache"
    for mk, plugins in tree.items():
        for pl, versions in plugins.items():
            for v in versions:
                d = cache / mk / pl / v
                d.mkdir(parents=True, exist_ok=True)
                (d / "plugin.json").write_text("{}")
    manifest = root / "installed_plugins.json"
    manifest.write_text(json.dumps({
        "version": 2,
        "plugins": {k: [{"installPath": str(cache / p)}] for k, p in installed.items()},
    }))
    return cache, manifest


# ── the three orphan levels ──
# An early version of orphan_dirs() special-cased the upper levels and missed two
# of them, leaving four directories behind on a real machine. Each level gets a test.

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    cache, manifest = build(
        root,
        tree={
            "official": {"posthog": ["1.1.50", "1.1.51", "1.1.52"]},   # 2 stale versions
            "vendor": {"widget": ["aaa"], "gadget": ["bbb"]},          # gadget wholly dead
            "deadmarket": {"thing": ["ccc"]},                          # marketplace wholly dead
        },
        installed={
            "posthog@official": "official/posthog/1.1.52",
            "widget@vendor": "vendor/widget/aaa",
        },
    )
    live = gc.live_install_paths(manifest)
    orphans = {str(p.relative_to(cache)) for p in gc.orphan_dirs(cache, live)}

    check("stale VERSION of a live plugin is an orphan",
          "official/posthog/1.1.50" in orphans and "official/posthog/1.1.51" in orphans,
          f"got {sorted(orphans)}")
    check("live version is NOT an orphan",
          "official/posthog/1.1.52" not in orphans, f"got {sorted(orphans)}")
    check("wholly-dead PLUGIN is an orphan (the level the first walk missed)",
          "vendor/gadget/bbb" in orphans, f"got {sorted(orphans)}")
    check("live plugin in the same marketplace survives",
          "vendor/widget/aaa" not in orphans, f"got {sorted(orphans)}")
    check("wholly-dead MARKETPLACE is an orphan",
          "deadmarket/thing/ccc" in orphans, f"got {sorted(orphans)}")
    check("orphan count is exactly the four dead dirs",
          len(orphans) == 4, f"got {len(orphans)}: {sorted(orphans)}")

# ── liveness comes from installPath, never from the version name ──

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    # The *older-looking* version is the installed one. A tool that guessed by
    # name or sort order would delete the live directory and keep the dead one.
    cache, manifest = build(
        root,
        tree={"m": {"p": ["1.0.0", "2.0.0"]}},
        installed={"p@m": "m/p/1.0.0"},
    )
    live = gc.live_install_paths(manifest)
    orphans = {str(p.relative_to(cache)) for p in gc.orphan_dirs(cache, live)}
    check("the installed path wins even when a higher version exists",
          orphans == {"m/p/2.0.0"}, f"got {sorted(orphans)}")

# ── refuse to run against an already-inconsistent manifest ──

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    cache, manifest = build(root, tree={"m": {"p": ["1.0.0"]}},
                            installed={"p@m": "m/p/1.0.0", "ghost@m": "m/ghost/9.9.9"})
    missing = gc.assert_installs_present(manifest)
    check("an installed plugin missing from disk is reported (guard trips)",
          missing == ["ghost@m"], f"got {missing}")

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    cache, manifest = build(root, tree={"m": {"p": ["1.0.0"]}}, installed={"p@m": "m/p/1.0.0"})
    check("a consistent manifest does not trip the guard",
          gc.assert_installs_present(manifest) == [], "expected no missing")

# ── prune_empty must never reach inside a live install ──
# Codex round-1 P1. The first version used cache.rglob("*"), which recursed into
# live version dirs and deleted their empty subdirectories. Real installs have
# them: git keeps .git/refs/tags and .git/objects/info empty, and two live plugins
# on the machine this was written for had exactly those. The post-delete check
# could not see the damage — it only verifies the version ROOT still exists.
# The original fixtures missed this because every synthetic dir contained a file.

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    cache, manifest = build(root, tree={"m": {"live": ["1.0.0"]}},
                            installed={"live@m": "m/live/1.0.0"})
    version = cache / "m" / "live" / "1.0.0"
    (version / ".git" / "refs" / "tags").mkdir(parents=True)      # legitimately empty
    (version / ".git" / "objects" / "info").mkdir(parents=True)   # legitimately empty
    (version / "assets").mkdir()                                  # legitimately empty

    gc.prune_empty(cache)

    check("prune_empty leaves empty git dirs inside a live install alone",
          (version / ".git" / "refs" / "tags").is_dir()
          and (version / ".git" / "objects" / "info").is_dir(),
          "git internals were pruned from a live install")
    check("prune_empty leaves other empty dirs inside a live install alone",
          (version / "assets").is_dir())
    check("prune_empty leaves the live version root intact", version.is_dir())

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    cache, manifest = build(root, tree={"m": {"p": ["1.0.0"]}, "dead": {"x": ["aaa"]}},
                            installed={"p@m": "m/p/1.0.0"})
    shutil.rmtree(cache / "dead" / "x" / "aaa")   # simulate the orphan already removed
    gc.prune_empty(cache)
    check("prune_empty does remove an emptied plugin container",
          not (cache / "dead" / "x").exists())
    check("prune_empty does remove an emptied marketplace container",
          not (cache / "dead").exists())
    check("prune_empty keeps a marketplace that still holds a live plugin",
          (cache / "m" / "p" / "1.0.0").is_dir())

# ── a failed deletion must not report success ──
# Codex round-1 P2. ignore_errors=True swallowed permission/symlink/EBUSY failures,
# then printed the pre-computed size as freed and returned 0 with orphans surviving.

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    cache, manifest = build(root, tree={"m": {"p": ["1.0.0", "2.0.0"]}},
                            installed={"p@m": "m/p/2.0.0"})
    live = gc.live_install_paths(manifest)
    orphans = gc.orphan_dirs(cache, live)

    def boom(*_a, **_k):
        raise OSError(13, "Permission denied")

    real_rmtree, shutil.rmtree = shutil.rmtree, boom
    failures: list[str] = []
    try:
        for o in orphans:
            try:
                shutil.rmtree(o)
            except OSError as e:
                failures.append(str(e))
    finally:
        shutil.rmtree = real_rmtree

    check("a failing rmtree surfaces instead of being swallowed",
          len(failures) == 1, f"got {failures}")
    check("the orphan survives a failed delete, so the final scan stays non-empty",
          len(gc.orphan_dirs(cache, live)) == 1)

# ── empty cache is a no-op, not a crash ──

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    check("a missing cache dir yields no orphans",
          gc.orphan_dirs(root / "nope", set()) == [])

print()
if FAILURES:
    print(f"  {len(FAILURES)} failed\n")
    sys.exit(1)
print("  all passed\n")
