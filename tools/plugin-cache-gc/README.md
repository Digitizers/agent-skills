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
- Empty plugin/marketplace directories are pruned after their versions go.

## Tests

```bash
python3 tools/plugin-cache-gc/test_plugin_cache_gc.py
```

Builds synthetic cache trees in a temp dir — the tests can never reach a live
`~/.claude`, which matters for a tool whose job is deleting directories.
