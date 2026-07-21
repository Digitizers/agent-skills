#!/usr/bin/env python3
"""Delete Claude Code plugin-cache directories no installed plugin points at.

Plugin caches are append-only in practice: every marketplace auto-update writes a
new version directory and leaves the previous one behind. Nothing garbage-collects
them, so the cache grows without bound — one machine here had accumulated five
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
import os
import shutil
import sys
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
CACHE_DIR = CLAUDE_DIR / "plugins" / "cache"
MANIFEST = CLAUDE_DIR / "plugins" / "installed_plugins.json"


def norm(p: Path | str) -> Path:
    """Canonical form for comparing paths.

    The manifest stores `installPath` as a string written by another program; the
    cache walk builds paths itself. Those can name the same directory in different
    ways (`~`, a symlinked home, `/private/var` vs `/var` on macOS). Comparing raw
    strings would classify a live install as an orphan — and then delete it — so
    both sides are normalized before they ever meet.
    """
    return Path(p).expanduser().resolve(strict=False)


def is_within(candidate: Path, root: Path) -> bool:
    """True if `candidate` resolves to something inside `root`."""
    try:
        return os.path.commonpath([str(root), str(candidate)]) == str(root)
    except ValueError:  # different drives / not comparable
        return False


def read_manifest(manifest: Path) -> dict:
    return json.loads(manifest.read_text())


def live_install_paths(manifest: Path) -> set[Path]:
    """Every normalized path an installed plugin currently resolves to."""
    data = read_manifest(manifest)
    return {
        norm(install["installPath"])
        for installs in data.get("plugins", {}).values()
        for install in installs
        if install.get("installPath")
    }


def plugin_count(manifest: Path) -> int:
    """Number of installed plugins — not the number of installPath entries.

    A plugin can record several installs, and distinct plugins can resolve to the
    same path, so the size of the live-path set is not a plugin count. Reporting
    one as the other misstates how much of the install base survived a sweep.
    """
    return len(read_manifest(manifest).get("plugins", {}))


def assert_installs_present(manifest: Path) -> list[str]:
    """Names of plugins whose installPath is already missing from disk.

    Run BEFORE deleting anything. If the manifest already disagrees with the disk,
    the cache is in a state this tool did not create and should not compound — the
    safe move is to stop and let a human look, not to delete more.
    """
    data = read_manifest(manifest)
    return [
        name
        for name, installs in data.get("plugins", {}).items()
        for install in installs
        if install.get("installPath") and not norm(install["installPath"]).exists()
    ]


def orphan_dirs(cache: Path, live: set[Path]) -> tuple[list[Path], list[str]]:
    """Unreachable `cache/<marketplace>/<plugin>/<version>` dirs, plus any warnings.

    Orphans occur at three levels and an early version of this walk missed two of
    them, leaving directories behind:

      version     — plugin updated, previous version dir stranded
      plugin      — every version of one plugin is dead (e.g. the same plugin name
                    is now installed from a *different* marketplace)
      marketplace — nothing from that marketplace is installed at all

    Collecting leaf version dirs and filtering by `live` covers all three uniformly:
    a dead plugin or marketplace is simply one whose every leaf is unreachable.

    Symlinked containers are refused, not followed. `Path.is_dir()` follows a
    symlink, so a symlinked marketplace or plugin directory would yield "cache"
    paths whose real targets live elsewhere on disk — and rmtree would then delete
    those external directories. Only a symlink at the *version* level fails loudly
    on its own, so the containers must be rejected explicitly.
    """
    orphans: list[Path] = []
    warnings: list[str] = []
    cache_root = norm(cache)
    if not cache_root.is_dir():
        return orphans, warnings

    for marketplace in sorted(cache_root.iterdir()):
        if marketplace.is_symlink():
            warnings.append(f"skipped symlinked marketplace: {marketplace.name}")
            continue
        if not marketplace.is_dir():
            continue
        for plugin in sorted(marketplace.iterdir()):
            if plugin.is_symlink():
                warnings.append(f"skipped symlinked plugin dir: {plugin.relative_to(cache_root)}")
                continue
            if not plugin.is_dir():
                continue
            for version in sorted(plugin.iterdir()):
                if version.is_symlink():
                    warnings.append(
                        f"skipped symlinked version dir: {version.relative_to(cache_root)}")
                    continue
                if not version.is_dir():
                    continue
                resolved = norm(version)
                if resolved in live:
                    continue
                # Belt and braces: never hand rmtree a path that escapes the cache.
                if not is_within(resolved, cache_root):
                    warnings.append(f"skipped out-of-cache path: {version}")
                    continue
                orphans.append(version)
    return orphans, warnings


def dir_size_mb(path: Path) -> float:
    """Size of a directory, never following symlinks out of it."""
    total = 0
    for root, dirs, files in os.walk(path, followlinks=False):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
        for f in files:
            fp = os.path.join(root, f)
            try:
                if not os.path.islink(fp):
                    total += os.path.getsize(fp)
            except OSError:
                pass
    return total / (1024 * 1024)


def prune_empty(cache: Path) -> None:
    """Drop plugin/marketplace containers left empty once their versions are gone.

    Only ever touches the two container levels — cache/<marketplace>/<plugin> and
    cache/<marketplace>. It must NOT walk inside a version directory: live plugins
    legitimately contain empty directories (git keeps `.git/refs/tags` and
    `.git/objects/info` empty, and a live install on the machine this was written
    for had exactly those), and deleting them corrupts a working install.

    An earlier version used `cache.rglob("*")`, which recursed into live versions
    and would have removed those git internals — invisibly, because the
    post-delete check only verifies the version root still exists.
    """
    if not cache.is_dir():
        return
    for marketplace in list(cache.iterdir()):          # plugin level
        if marketplace.is_symlink() or not marketplace.is_dir():
            continue
        for plugin in list(marketplace.iterdir()):
            if plugin.is_symlink() or not plugin.is_dir():
                continue
            if not any(plugin.iterdir()):
                plugin.rmdir()
    for marketplace in list(cache.iterdir()):          # marketplace level
        if marketplace.is_symlink() or not marketplace.is_dir():
            continue
        if not any(marketplace.iterdir()):
            marketplace.rmdir()


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

    plugins = plugin_count(MANIFEST)
    live = live_install_paths(MANIFEST)
    orphans, warnings = orphan_dirs(CACHE_DIR, live)

    for w in warnings:
        print(f"  warning: {w}", file=sys.stderr)

    if not orphans:
        print(f"  {plugins} plugins live, 0 orphans — nothing to do")
        return 0

    total = 0.0
    print(f"  {plugins} plugins live, {len(orphans)} orphan version dir(s):\n")
    cache_root = norm(CACHE_DIR)
    for o in orphans:
        mb = dir_size_mb(o)
        total += mb
        print(f"    {mb:7.1f}MB  {norm(o).relative_to(cache_root)}")

    if not args.delete:
        print(f"\n  {total:.0f}MB reclaimable. Re-run with --delete to remove.\n")
        return 0

    # Re-read the manifest immediately before deleting. A plugin update running
    # concurrently can promote a previously stale version to current while we were
    # measuring sizes; deleting from the older snapshot would then remove a live
    # install. Re-validating here shrinks that window to the loop below — it is
    # not a lock, but it turns "delete what was dead a moment ago" into "delete
    # what is still dead now".
    live_now = live_install_paths(MANIFEST)
    promoted = [o for o in orphans if norm(o) in live_now]
    if promoted:
        for p in promoted:
            print(f"  skipping {norm(p).relative_to(cache_root)} — became live during the scan",
                  file=sys.stderr)
        orphans = [o for o in orphans if norm(o) not in live_now]

    # No ignore_errors: a delete that fails on permissions, a busy file, or a
    # symlink must not be swallowed and then reported as reclaimed space.
    failures: list[str] = []
    for o in orphans:
        resolved = norm(o)
        if resolved in live_now or not is_within(resolved, cache_root):
            continue  # re-checked per item; never delete outside the cache
        try:
            shutil.rmtree(o)
        except OSError as e:
            failures.append(f"{resolved.relative_to(cache_root)}: {e}")
    prune_empty(CACHE_DIR)

    still_missing = assert_installs_present(MANIFEST)
    remaining, _ = orphan_dirs(CACHE_DIR, live_now)
    freed = total - sum(dir_size_mb(o) for o in remaining if o.exists())

    print(f"\n  freed {freed:.0f}MB · "
          f"{plugins - len(set(still_missing))}/{plugins} plugins intact · "
          f"{len(remaining)} orphans left")

    if still_missing:
        print("\n  ERROR: an installed plugin's directory is gone after pruning:",
              file=sys.stderr)
        for m in sorted(set(still_missing)):
            print(f"    {m}", file=sys.stderr)
        return 1

    if failures:
        print(f"\n  ERROR: {len(failures)} orphan(s) could not be removed:", file=sys.stderr)
        for f in failures:
            print(f"    {f}", file=sys.stderr)
        return 1

    if remaining:
        # Nothing raised, yet orphans survive — report it rather than exit 0 on a
        # cleanup that silently did not finish.
        print(f"\n  ERROR: {len(remaining)} orphan(s) still present after deletion:",
              file=sys.stderr)
        for r in remaining:
            print(f"    {norm(r).relative_to(cache_root)}", file=sys.stderr)
        return 1

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
