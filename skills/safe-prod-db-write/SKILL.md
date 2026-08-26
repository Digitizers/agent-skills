---
name: safe-prod-db-write
description: Safely run a one-off write, backfill, or data-mutating script against a PRODUCTION database — pull the connection from the platform, dry-run, get explicit human authorization, execute, verify, clean up. Use before running any script that inserts/updates/deletes prod data (generating codes, backfills, one-off fixes, seed data), when the user asks to write/mutate production data, or when a task needs a real prod DB connection. Also use when adding a DB model/table/migration, when applying a migration to a hosted project through a dashboard or MCP tool (Supabase `apply_migration`, Neon, PlanetScale) — see "Migrations applied by a hosted tool" — or when setting up a CI guard that enforces a per-table invariant (RLS enabled, tenant column, required index) — see "Enforce schema invariants in CI". Also use when a permission layer blocks a production write you were authorized to make — see "When the harness refuses the write" — and whenever the project keeps a dev twin of production (a Neon/Supabase branch, a staging cluster), since naming the target host is step 0. Assumes a Neon/Vercel-style setup with the platform CLI, but the protocol generalizes.
compatibility: Needs a way to reach the production database — a deployment platform CLI (e.g. `vercel`) to pull the connection string plus a client (`psql`, `prisma`), or a hosted tool that executes SQL directly (e.g. the Supabase MCP server) — in which case protocol steps 1 and 6 do not apply, see the note under the protocol. The verification steps assume you can run arbitrary reads against the target.
---

# Safe production DB write

**Never write prod blind.** Every production mutation follows the same protocol: pull the connection → dry-run → authorize → execute → verify → clean up. Skipping any step is how a one-off script silently corrupts live data.

## The protocol

0. **Name the target host out loud before you read or write anything.** Modern
   setups keep a *dev twin* of production — a Neon branch, a Supabase branch, a
   staging cluster — and the local `.env` usually points at the twin, by design.
   So "the database" is ambiguous in both directions: a write you meant for
   production can land in the twin and look like it worked, and a destructive
   local command can land in production if the twin is younger than the habit.
   Before acting, state which host you are on and how you know — the endpoint
   id from the connection string, not the name of the env file. Say it in the
   message where you ask for authorization, so the human is approving a *target*
   and not just an operation.

   The corollary for verification: after a production write, a read through
   local tooling proves nothing if that tooling points at the twin. Verify
   through the same path you wrote through.

1. **Pull the connection into a private temp file.** Create it with `mktemp` — never a predictable path like `/tmp/op.env`, which can collide with a concurrent run or be a planted symlink on a shared runner — and arm a cleanup trap **up front** so the creds file is removed on every exit path:
   ```bash
   ENVFILE=$(mktemp); trap 'rm -f "$ENVFILE"' EXIT
   vercel env pull "$ENVFILE" --environment=production -y   # or your platform's equivalent
   ```
   - **Neon + Vercel gotcha:** `DATABASE_URL` / `DIRECT_DATABASE_URL` are often marked *Sensitive*, so `vercel env pull` returns them **empty** — the run then has no connection. Use `DATABASE_URL_UNPOOLED` (Neon's direct, non-sensitive URL), which pulls fine, and map it into `DATABASE_URL` for the command. Don't un-mark the sensitive vars (that widens exposure).
2. **Dry-run first.** If the script has `--dry-run`, run it and **read the exact rows/output that WOULD be written**. No dry-run flag? For a **data write**, preview with a `SELECT` over the same `WHERE`, or a transaction you roll back. For a **migration (DDL)**, neither substitutes — see the hosted-tool note below; the preview belongs on a disposable database, never as a rolled-back trial against production. Confirm the target (table / batch / id range) is in the expected **pre-state** — e.g. the batch count is `0` before you insert.
3. **Get explicit human authorization for the real write.** State precisely: what operation, **how many rows**, which table, which env. Approval of a dry-run is **not** approval of the write — ask again for the live run.
4. **Execute.** Capture stdout to a file if it *is* the deliverable (e.g. a codes CSV). Keep the command identical to the dry-run minus the flag.
5. **Verify post-state with a read.** Row count == intended, and key invariants hold (uniqueness, flags set correctly, `redeemedBy IS NULL`, etc.). A write you didn't verify isn't done.
6. **Clean up.** The `EXIT` trap from step 1 removes the temp creds file on every exit path — including failure. If you didn't arm one, `rm -f "$ENVFILE"` now. Never leave a prod-credentials file on disk.

**When a hosted tool executes the SQL** (a Supabase MCP server, a dashboard console, a platform API), **steps 1 and 6 do not apply** — the tool holds the credentials, nothing is pulled and nothing lands on disk to delete. Every other step applies unchanged, and they get *easier to skip*, not less necessary: there is no `--dry-run` flag to reach for and no command echo to eyeball, so step 5 is the only thing between you and an unverified write.

Step 2 on this path splits by what you are running, and **the two halves are not interchangeable**:

- **A data write** (`UPDATE`/`INSERT`/`DELETE`) — a `SELECT` over the same `WHERE` *is* the dry-run. It shows the exact rows about to change.
- **A migration (DDL)** — a `SELECT` is **not** a dry-run and must not be presented as one. It reads the pre-state; it never executes the migration, so it cannot surface invalid SQL, a constraint that will reject, a trigger side effect, or lock behavior. The only real preview executes the statements, and it belongs on a **throwaway database carrying the same migration history** — never on production. `begin; … rollback;` is a preview technique *for that disposable database* (Postgres has transactional DDL, `CREATE INDEX CONCURRENTLY` and friends excepted); it is **not** a way to make a production trial safe. A rolled-back transaction still took the locks for its duration, and a migration can reach outside the transaction entirely. If no throwaway database exists, the honest answer is that this migration has no dry-run — say so at step 3 and let the human authorize it on those terms, rather than executing against production to find out.

## Rules

- **Least blast radius.** Scope every mutation by batch / id / explicit filter. Never an unbounded `UPDATE`/`DELETE` — add the `WHERE` and prove it selects only what you intend (count it first).
- **Idempotent + unique.** Use `skipDuplicates` / unique keys / random tokens so a re-run or partial failure can't double-insert or collide.
- **Know the undo before you run.** If you can't state how to reverse it, you're not ready to run it.
- **A feature that issues credentials needs a revoke path before it issues the first one.** Codes, tokens, API keys, invites, coupons — anything a third party can later present for value. Ask where the `revokedAt` / `expiresAt` / disable switch is *while the generator is being written*, because the moment you need it, you need it urgently and it does not exist. Observed: 2,000 lifetime-deal codes were generated, handed to a marketplace, and then had to be neutralized when the deal fell through — the table had no revoke column and the admin surface only generated and listed, so the only available move was a manual production `DELETE` of the unredeemed rows. That worked because none had been redeemed; had even one been, there would have been no clean answer at all.
- **A verification step must fail LOUD, never produce an answer from missing input.** The dangerous bug in a check is not that it breaks — it is that a failed input silently becomes an empty one, and the check then reports a confident result derived from nothing. An empty query result reads as "no rows match"; an empty file list reads as "nothing exists"; an error on stderr is invisible in a pipeline. Guard the inputs and let errors print, because here the false branch prescribes a **production write**.
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
   -- `name` is the name PORTION only: no version prefix, no .sql extension.
   -- For 20260726270000_order_where_the_limit_is.sql that is
   -- 'order_where_the_limit_is', not the filename.
   select version from <ledger> where name = '<migration name>';
   ```

   Getting that wrong returns **zero rows**, which reads as "never applied" and sends you to the wrong branch of the diagnosis table below — so match the ledger's own column, not the filename.

   Do **not** check this by comparing the newest N of each side. A stamped version can sort *below* migrations already present (the wall clock is behind your filename's timestamp), so the drifted row and its repo file can both fall outside their respective windows — the two tails then match and the check passes on a drifted ledger. Any positional window has this hole; keying on the name does not.

A **whole-ledger reconciliation** — every `version_name` in the ledger against every migration filename — is occasionally worth doing, after a branch merge or when inheriting a project. Compare complete **sets**, never tails, and if you write it as a shell one-liner, know that four things will silently turn "the check failed" into "everything is drifted", each of which points at the production `UPDATE` below:

- `name` is **nullable** in Supabase's `schema_migrations` (`version` is not), so `version || '_' || name` is `NULL` for a legacy unnamed row and `psql -At` prints it as a **blank line** — the row stops being itself and shifts the whole comparison. `coalesce` it, and reconcile unnamed rows by `version` alone.
- An unmatched glob is either the literal pattern (counting as one match) or, under `nullglob`, nothing at all — which degrades `ls <glob>` to a bare `ls` listing the **current directory**. Not an empty set: the wrong set, shaped like a real answer. Assert on a match count.
- Process substitution **does not propagate exit status**. `diff <(psql …)` with a failing query reads an empty pseudo-file, and `diff` exits 1 — which per its own docs means only "inputs differ", the same status as genuine drift. Materialize the query and check it first.
- Errors land on **stderr**, invisible in a pipeline, while the wrong answer goes to stdout.

That list is the reason this is prose and not a snippet to paste: each stage of such a pipeline is a place to fail open, and a command in a document is never executed, so nothing catches the next one. If you need this routinely, put it in a script under version control where it can be tested — and see the fail-loud rule above.

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

## When the harness refuses the write

A permission layer — a classifier, a sandbox, an approval gate — can deny the
production write even after the human authorized it. That denial is not an
obstacle to solve. **Hand the operation to the human; do not reach for another
tool that would accomplish the same mutation.** Using `psql` because the MCP
tool was blocked is not a workaround, it is a bypass, and the fact that it is
technically available is exactly what the denial was expressing distrust of.

Hand off properly — the human is now the executor, so give them what an
executor needs, in one message:

- the exact statement, scoped and copy-pasteable;
- the **pre-check** to run first, with the result that means "safe to proceed"
  (`expect 2000 / 0`) and an instruction to stop and report if it differs;
- the **post-check** and its expected result;
- where to run it — the console, the project, and *which branch or host*.

**Expect the denial to be sticky.** After a blocked write, the same tool may
refuse subsequent *read-only* queries against that database too. Plan for
verification to move to the human as well, rather than promising a confirmation
you will not be able to produce. Say plainly that you cannot verify it yourself
and ask for the output — a write reported as done on the strength of the
human's "ran it" is a write nobody checked.

## Red flags — stop

| Thought | Reality |
|---|---|
| "It's a small update, I'll just run it" | Small unbounded writes corrupt the most. Dry-run + `WHERE` + count. |
| "The dry-run looked fine, running it" | Re-confirm the live write with the human — dry-run approval ≠ write approval. |
| "Connection came back empty, I'll un-mark Sensitive" | Use `DATABASE_URL_UNPOOLED` instead; don't widen credential exposure. |
| "Done — it inserted" | Not done until a read verifies count + invariants. |
| "The migration tool returned success" | It reports that *it* ran, not what landed. Check the catalog **and** that the recorded version matches your filename. |
| "I'll clean up the env file later" | Clean up now, on every exit path — it holds prod creds. |
| "The tool blocked it, I'll use psql instead" | That's a bypass, not a workaround. Hand the statement to the human with pre/post checks. |
| "The local admin/console shows the change" | Local tooling usually points at the dev twin. Verify through the path you wrote through. |
| "I'll generate the codes now, revocation can come later" | If it can be presented for value, the revoke path ships first. |
