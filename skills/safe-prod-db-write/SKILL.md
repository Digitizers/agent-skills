---
name: safe-prod-db-write
description: Safely run a one-off write, backfill, or data-mutating script against a PRODUCTION database — pull the connection from the platform, dry-run, get explicit human authorization, execute, verify, clean up. Use before running any script that inserts/updates/deletes prod data (generating codes, backfills, one-off fixes, seed data), when the user asks to write/mutate production data, or when a task needs a real prod DB connection. Also use when adding a DB model/table/migration, when applying a migration to a hosted project through a dashboard or MCP tool (Supabase `apply_migration`, Neon, PlanetScale) — see "Migrations applied by a hosted tool" — or when setting up a CI guard that enforces a per-table invariant (RLS enabled, tenant column, required index) — see "Enforce schema invariants in CI". Assumes a Neon/Vercel-style setup with the platform CLI, but the protocol generalizes.
compatibility: Needs a way to reach the production database — a deployment platform CLI (e.g. `vercel`) to pull the connection string plus a client (`psql`, `prisma`), or a hosted tool that executes SQL directly (e.g. the Supabase MCP server). The verification steps assume you can run arbitrary reads against the target.
---

# Safe production DB write

**Never write prod blind.** Every production mutation follows the same protocol: pull the connection → dry-run → authorize → execute → verify → clean up. Skipping any step is how a one-off script silently corrupts live data.

## The protocol

1. **Pull the connection into a private temp file.** Create it with `mktemp` — never a predictable path like `/tmp/op.env`, which can collide with a concurrent run or be a planted symlink on a shared runner — and arm a cleanup trap **up front** so the creds file is removed on every exit path:
   ```bash
   ENVFILE=$(mktemp); trap 'rm -f "$ENVFILE"' EXIT
   vercel env pull "$ENVFILE" --environment=production -y   # or your platform's equivalent
   ```
   - **Neon + Vercel gotcha:** `DATABASE_URL` / `DIRECT_DATABASE_URL` are often marked *Sensitive*, so `vercel env pull` returns them **empty** — the run then has no connection. Use `DATABASE_URL_UNPOOLED` (Neon's direct, non-sensitive URL), which pulls fine, and map it into `DATABASE_URL` for the command. Don't un-mark the sensitive vars (that widens exposure).
2. **Dry-run first.** If the script has `--dry-run`, run it and **read the exact rows/output that WOULD be written**. No dry-run flag? Preview with a rolled-back transaction or a `SELECT` that shows the effect. Confirm the target (table / batch / id range) is in the expected **pre-state** — e.g. the batch count is `0` before you insert.
3. **Get explicit human authorization for the real write.** State precisely: what operation, **how many rows**, which table, which env. Approval of a dry-run is **not** approval of the write — ask again for the live run.
4. **Execute.** Capture stdout to a file if it *is* the deliverable (e.g. a codes CSV). Keep the command identical to the dry-run minus the flag.
5. **Verify post-state with a read.** Row count == intended, and key invariants hold (uniqueness, flags set correctly, `redeemedBy IS NULL`, etc.). A write you didn't verify isn't done.
6. **Clean up.** The `EXIT` trap from step 1 removes the temp creds file on every exit path — including failure. If you didn't arm one, `rm -f "$ENVFILE"` now. Never leave a prod-credentials file on disk.

## Rules

- **Least blast radius.** Scope every mutation by batch / id / explicit filter. Never an unbounded `UPDATE`/`DELETE` — add the `WHERE` and prove it selects only what you intend (count it first).
- **Idempotent + unique.** Use `skipDuplicates` / unique keys / random tokens so a re-run or partial failure can't double-insert or collide.
- **Know the undo before you run.** If you can't state how to reverse it, you're not ready to run it.
- **Separate generation from distribution — and test on a *different* batch.** Burn throwaway/smoke-test rows from a batch you are **not** shipping, so the live batch you hand off stays pristine.
- **The connection is a secret.** Never echo the URL, never commit the pulled env file, never paste creds into chat.

## Migrations applied by a hosted tool: verify the ledger, not just the schema

When a managed platform applies a migration for you — a dashboard button, an MCP tool, a hosted API — **it may record it under a version it chose rather than your filename.** Observed with Supabase's `apply_migration`: a file named `20260726270000_order_where_the_limit_is.sql` landed in `supabase_migrations.schema_migrations` as version `20260726231221`, the wall clock at apply time. It recurred on *every* apply — three times in one session — so this is a per-apply check, not a once-per-project one.

The reason it slips past step 5 is that it splits two things a single read conflates:

| | after a drifted apply |
|---|---|
| Database catalog | **correct** — the DDL ran, the function/table/policy is what you wrote |
| Migration ledger | **wrong** — names a version that has no file in the repo |

Verify both, separately:

1. **The change is really in the catalog.** Introspect for the specific thing you altered, don't infer it from "the tool returned success" — e.g. `select pg_get_functiondef(oid) ...`, `pg_class.relrowsecurity`, `pg_indexes`.
2. **The row you just wrote carries the version you intended.** Query it **by name** — the identity you control — and compare against the version in your filename:

   ```sql
   select version from <ledger> where name = '<the migration you just applied>';
   ```

   Do **not** check this by comparing the newest N of each side. A stamped version can sort *below* migrations already present (the wall clock is behind your filename's timestamp), so the drifted row and its repo file can both fall outside their respective windows — the two tails then match and the check passes on a drifted ledger. Any positional window has this hole; keying on the name does not.

For a whole-ledger reconciliation, diff the complete **sets**, never tails. Order stops mattering once nothing is truncated:

```bash
diff <(psql -Atc "select version || '_' || name from <ledger> order by 1") \
     <(ls migrations/*.sql | sed 's|.*/||; s|\.sql$||' | sort)
```

### Before you `UPDATE` the ledger, prove it is only a label

A version mismatch has several causes and **only one of them is fixed by relabelling.** The catalog check proves a schema object exists; it does *not* establish which ledger row corresponds to which file. Rewriting a version on a wrong assumption marks unrelated DDL as applied and corrupts every future diff and push — a worse state than the drift, and harder to see.

| The mismatch means | Correct action |
|---|---|
| Same migration, wrong version label (name matches, its DDL is in the catalog) | Relabel — below |
| A repo file genuinely never applied | Apply it. **Never** relabel to hide it |
| Ledger rows from another branch or environment | Reconcile the branches; not a local fix |
| The migration was renamed | Fix the name, and decide which side is authoritative first |

Only in the first row, and only after confirming the name matches *and* its DDL is present in the catalog, relabel. The **"least blast radius" rule applies in full** — it is easy to skip here precisely because the change feels clerical:

```sql
update <ledger> set version = '<version from the repo filename>'
 where version = '<the stamped version>' and name = '<migration name>';
-- expect exactly: UPDATE 1
```

Nothing needs re-running: the catalog is already correct, and the label is all that moves. Leaving drift costs more than it looks — nothing reads the ledger until someone runs a schema diff or a `db push`, and *that* person sees one orphan version beside one apparently-unapplied file, with no way to tell whether the DDL ran.

## Enforce schema invariants in CI, not by memory

When **every** table/model must satisfy a rule — RLS enabled, a tenant column, a required index, a `createdAt`, a soft-delete flag — don't trust humans to remember it on each new migration. Add a **static CI guard** (no database needed) that reconciles the ORM schema against the migrations and **fails the build** when any model is missing the invariant:

- Parse the schema for model→table names, honoring name overrides (Prisma `@@map`, Rails `table_name`, etc.) — the table name, not the model name, is what the DB rule applies to.
- Scan the migration SQL for the invariant, matching the real statement shape — for Postgres RLS the table comes **before** the clause: `ALTER TABLE "<table>" ENABLE ROW LEVEL SECURITY` (capture the quoted identifier immediately preceding `ENABLE ROW LEVEL SECURITY`). Build the set of covered tables.
- Diff the two; exit non-zero listing any uncovered model. Wire it as a fail-fast CI step + a `db:check-*` script.
- **Derive the *final* state, not mere presence.** A plain "does any migration mention it" scan is a false pass in long histories: a table enabled early then later `DISABLE`d (or an index since dropped) still reads as covered. Replay statements in order so a later removal wins — or, for full correctness, run the migrations against a throwaway DB and **introspect the live catalog** (`pg_class.relrowsecurity`, `pg_indexes`) instead of parsing SQL. Presence-scan is the cheap first-order guard; introspection is the exact one.

This catches the gap at **PR time** instead of in production, and it's portable (pure file parsing). Prove it both ways: green on the current schema, and **red when you add a throwaway model** without the invariant. Caveat: the guard only proves the invariant is *declared* — runtime enforcement (real RLS *policies*, a working index plan) is a separate concern, so don't let a green guard imply the behavior is actually enforced.

## Red flags — stop

| Thought | Reality |
|---|---|
| "It's a small update, I'll just run it" | Small unbounded writes corrupt the most. Dry-run + `WHERE` + count. |
| "The dry-run looked fine, running it" | Re-confirm the live write with the human — dry-run approval ≠ write approval. |
| "Connection came back empty, I'll un-mark Sensitive" | Use `DATABASE_URL_UNPOOLED` instead; don't widen credential exposure. |
| "Done — it inserted" | Not done until a read verifies count + invariants. |
| "The migration tool returned success" | It reports that *it* ran, not what landed. Check the catalog **and** that the recorded version matches your filename. |
| "I'll clean up the env file later" | Clean up now, on every exit path — it holds prod creds. |
