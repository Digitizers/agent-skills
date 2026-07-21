#!/usr/bin/env python3
"""Delete Claude Code plugin-cache directories no installed plugin points at.

Plugin caches are append-only in practice: every marketplace auto-update writes a
new version directory and leaves the previous one behind. Nothing garbage-collects
them, so the cache grows without bound — one repo here had accumulated five
versions of a single plugin.

Authority is `installed_plugins.json`: each installed plugin records an exact
`installPath`. A version directory that is not one of those paths is unreachable,
whatever its name suggests. Never infer liveness from a version string or an mtime
— an updated plugin's *older* directory looks just as plausible as its current one.

    python3 tools/plugin-cache-gc/plugin_cache_gc.py            # dry run
    python3 tools/plugin-cache-gc/plugin_cache_gc.py --delete   # actually remove

Dry run is the default: this removes files under a directory the user did not name
and cannot undo it.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
CACHE_DIR = CLAUDE_DIR / "plugins" / "cache"
MANIFEST = CLAUDE_DIR / "plugins" / "installed_plugins.json"


def live_install_paths(manifest: Path) -> set[Path]:
    """Every path an installed plugin currently resolves to."""
    data = json.loads(manifest.read_text())
    paths: set[Path] = set()
    for installs in data.get("plugins", {}).values():
        for install in installs:
            p = install.get("installPath")
            if p:
                paths.add(Path(p))
    return paths


def assert_installs_present(manifest: Path) -> list[str]:
    """Return plugins whose installPath is already missing from disk.

    Run BEFORE deleting anything. If the manifest already disagrees with the disk,
    the cache is in a state this tool did not create and should not compound — the
    safe move is to stop and let a human look, not to delete more.
    """
    data = json.loads(manifest.read_text())
    missing = []
    for name, installs in data.get("plugins", {}).items():
        for install in installs:
            p = install.get("installPath")
            if p and not Path(p).exists():
                missing.append(name)
    return missing


def orphan_dirs(cache: Path, live: set[Path]) -> list[Path]:
    """Version directories under cache/<marketplace>/<plugin>/<version> not in `live`.

    Orphans occur at three levels and an early version of this walk missed two of
    them, leaving directories behind:

      version     — plugin updated, previous version dir stranded
      plugin      — every version of one plugin is dead (e.g. the same plugin name
                    is now installed from a *different* marketplace)
      marketplace — nothing from that marketplace is installed at all

    Collecting leaf version dirs and filtering by `live` covers all three uniformly:
    a dead plugin or marketplace is simply one whose every leaf is unreachable.
    Special-casing the upper levels is what produced the miss.
    """
    orphans: list[Path] = []
    if not cache.is_dir():
        return orphans
    for marketplace in sorted(cache.iterdir()):
        if not marketplace.is_dir():
            continue
        for plugin in sorted(marketplace.iterdir()):
            if not plugin.is_dir():
                continue
            for version in sorted(plugin.iterdir()):
                if version.is_dir() and version not in live:
                    orphans.append(version)
    return orphans


def dir_size_mb(path: Path) -> float:
    total = 0
    for f in path.rglob("*"):
        try:
            if f.is_file() and not f.is_symlink():
                total += f.stat().st_size
        except OSError:
            pass
    return total / (1024 * 1024)


def prune_empty(cache: Path) -> None:
    """Drop plugin/marketplace dirs left empty once their versions are gone."""
    for _ in range(2):  # plugin level, then marketplace level
        for d in sorted(cache.rglob("*"), key=lambda p: -len(p.parts)):
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--delete", action="store_true",
                    help="Actually remove the orphans. Without this, only reports.")
    args = ap.parse_args()

    if not MANIFEST.is_file():
        print(f"no manifest at {MANIFEST}", file=sys.stderr)
        return 2

    missing = assert_installs_present(MANIFEST)
    if missing:
        print("refusing to run: these installed plugins are already missing from disk —",
              file=sys.stderr)
        for m in sorted(set(missing)):
            print(f"    {m}", file=sys.stderr)
        print("the cache is in an unexpected state; investigate before pruning it.",
              file=sys.stderr)
        return 2

    live = live_install_paths(MANIFEST)
    orphans = orphan_dirs(CACHE_DIR, live)

    if not orphans:
        print(f"  {len(live)} plugins live, 0 orphans — nothing to do")
        return 0

    total = 0.0
    print(f"  {len(live)} plugins live, {len(orphans)} orphan version dir(s):\n")
    for o in orphans:
        mb = dir_size_mb(o)
        total += mb
        print(f"    {mb:7.1f}MB  {o.relative_to(CACHE_DIR)}")

    if not args.delete:
        print(f"\n  {total:.0f}MB reclaimable. Re-run with --delete to remove.\n")
        return 0

    for o in orphans:
        shutil.rmtree(o, ignore_errors=True)
    prune_empty(CACHE_DIR)

    # Re-read: the delete must not have touched anything still installed.
    still_missing = assert_installs_present(MANIFEST)
    if still_missing:
        print("\n  ERROR: an installed plugin's directory is gone after pruning:",
              file=sys.stderr)
        for m in sorted(set(still_missing)):
            print(f"    {m}", file=sys.stderr)
        return 1

    remaining = orphan_dirs(CACHE_DIR, live)
    print(f"\n  freed {total:.0f}MB · {len(live)}/{len(live)} plugins intact · "
          f"{len(remaining)} orphans left\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
