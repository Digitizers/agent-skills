# plugin-cache-gc

Deletes Claude Code plugin-cache directories that no installed plugin points at.

```bash
python3 tools/plugin-cache-gc/plugin_cache_gc.py            # dry run — reports only
python3 tools/plugin-cache-gc/plugin_cache_gc.py --delete   # remove them
```

Stdlib only. Dry run is the default: this removes directories the user did not
name, and there is no undo.

## Why this exists

`~/.claude/plugins/cache` is append-only in practice. Every marketplace
auto-update writes a new version directory and leaves the previous one behind,
and nothing collects them. On the machine this was written for, that had
accumulated to 45MB across 22 dead directories — one plugin alone had five
stale versions.

Disk is the smaller cost. The reason to keep the cache honest is that a stale
directory is indistinguishable from a live one by inspection, so anyone (or any
agent) cleaning up by hand is one wrong `rm` away from deleting a plugin that is
actually installed.

## The rule that makes it safe

**`installed_plugins.json` is the only authority.** Each installed plugin records
an exact `installPath`. A version directory that is not one of those paths is
unreachable — whatever its name suggests.

Never infer liveness from a version string, a sort order, or an mtime. A plugin
that rolled *back* has its live code in the lower-numbered directory, and a
name-based guess deletes exactly the wrong one. `test_plugin_cache_gc.py` pins
this case.

## Orphans occur at three levels

The first version of this walk special-cased the upper levels, missed two of
them, and left four directories behind on a real machine:

| Level | How it happens |
|---|---|
| version | plugin updated; the previous version dir is stranded |
| plugin | every version of one plugin is dead — e.g. the same plugin name is now installed from a *different* marketplace |
| marketplace | nothing from that marketplace is installed at all |

Collecting **leaf version directories** and filtering by `installPath` covers all
three uniformly: a dead plugin or marketplace is simply one whose every leaf is
unreachable. Special-casing the upper levels is what produced the miss.

## Safety checks

- **Pre-flight:** if any installed plugin's `installPath` is *already* missing
  from disk, the tool refuses to run. The cache is then in a state it did not
  create, and deleting more would compound it — that wants a human, not a sweep.
- **Post-delete:** re-reads the manifest and fails loudly if any installed
  plugin's directory disappeared. A sweep that damaged a live install must not
  exit 0.
- **Deletion failures are never swallowed.** No `ignore_errors`: a permission
  error or busy file surfaces, and the tool exits non-zero rather than printing
  reclaimed space it did not reclaim. It also exits non-zero if the final scan
  still finds orphans, even when nothing raised.
- **Symlinks are refused, not deleted and not failed on.** A symlinked
  marketplace, plugin, or version directory is skipped with a warning on stderr
  and does not by itself change the exit code — following it could resolve
  outside the cache, and deleting "through" it would destroy the target. Every
  candidate is additionally verified to resolve beneath the cache root before
  `rmtree` sees it.
- **Freed space is measured from what was actually deleted** — each directory is
  sized immediately before its removal and counted only if the removal succeeds.
  Skipped or failed candidates never inflate the number.
- **Per-item liveness re-check.** The manifest is re-read for each candidate
  immediately before its deletion, so a plugin update that promotes a stale
  version mid-run causes a skip, not a lost install. Not a lock — the window is
  one manifest read to one `rmtree` — but that is as small as it gets without
  file locking.
- **Container-only pruning.** Empty `<marketplace>/<plugin>` and `<marketplace>`
  directories are removed after their versions go — but pruning never descends
  into a version directory. See below.

## Never prune inside a live install

Live plugins legitimately contain empty directories. Git keeps `.git/refs/tags`
and `.git/objects/info` empty, and two live installs on the machine this was
written for had exactly those.

The first implementation pruned with `cache.rglob("*")`, which walked into live
version directories and deleted those empty git dirs — corrupting a working
install's repository. The post-delete check could not see it: that only verifies
the version *root* still exists, and it did.

So pruning is restricted to the two container levels and never recurses. The
regression test builds a live version containing `.git/refs/tags`,
`.git/objects/info`, and an empty `assets/`, and asserts all three survive.

The original fixtures missed this because every synthetic directory contained a
file, so no empty directory ever existed inside a "live" install — the fixture
was too clean to expose the bug.

## Tests

```bash
python3 tools/plugin-cache-gc/test_plugin_cache_gc.py
```

Builds synthetic cache trees in a temp dir — the tests can never reach a live
`~/.claude`, which matters for a tool whose job is deleting directories.
