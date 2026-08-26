# `pipeline/databricks/`

Databricks side of the judging platform.

Firebase stays the serving tier — it owns the ~8 hours of the event itself,
because Databricks cannot host judge auth and Delta `MERGE` is the wrong shape
for a judge tapping **Submit** on a phone. Databricks owns everything before the
event (working out who judges what) and everything after it (the archive and the
analysis).

Nothing here is Spark. See [Free Edition](#what-free-edition-can-and-cannot-do).

---

## The four pieces

| File | What it does | Writes to |
|---|---|---|
| `connection.py` | talks to the SQL warehouse, returns rows / DataFrames | nothing |
| `schema.py` | creates the catalog, schema and five Delta tables | Databricks |
| `ingest_2026.py` | lands the 2026 event data from Firestore | Databricks |
| `publish_run.py` | solves an assignment, publishes the run | Firestore **+** Databricks |

**Firestore is read-only everywhere except one line.** `publish_run.py` writes
`runs/{runId}` and nothing else, only under `--commit`. Every other Firestore
call in this package is `.stream()` or `auth.list_users()`. Two tests enforce
that by reading the source of these modules — if a write ever lands somewhere it
should not, the suite fails.

---

## Setup

Credentials live in `.env` at the repo root (gitignored — never commit it,
never paste the token into a script):

```
DATABRICKS_HOST=https://dbc-xxxxxxxx-xxxx.cloud.databricks.com
DATABRICKS_TOKEN=dapi...
DATABRICKS_CATALOG=datahacks
DATABRICKS_SCHEMA=judging
DATABRICKS_WAREHOUSE_ID=          # optional; auto-discovered when unset
```

Get a token from the Databricks UI: **Settings → Developer → Access tokens →
Generate new token**.

Then check you can reach it:

```bash
./.venv-pipeline/bin/python -m pipeline.databricks.connection
```

```
settings: { "host": "...", "catalog": "datahacks", "schema": "judging", ... }
warehouse: 1f67d9d641d91724
connected: {'catalog': 'datahacks', 'user': 'you@ucsd.edu'}
```

If the first command takes 30–60 seconds, that is the warehouse waking up, not
a hang. See [cold starts](#cold-starts).

---

## Running each piece

### 1. Create the tables

```bash
./.venv-pipeline/bin/python -m pipeline.databricks.schema --create
```

Idempotent. Every statement is `CREATE ... IF NOT EXISTS`; re-running changes
nothing. There is no `DROP` and no `CREATE OR REPLACE` in this package.

```bash
./.venv-pipeline/bin/python -m pipeline.databricks.schema         # row counts only
./.venv-pipeline/bin/python -m pipeline.databricks.schema --sql    # print DDL, connect to nothing
```

### 2. Load the 2026 event data

```bash
# dry run — reads Firestore, writes nothing, prints what it found
./.venv-pipeline/bin/python -m pipeline.databricks.ingest_2026

# actually load Delta
./.venv-pipeline/bin/python -m pipeline.databricks.ingest_2026 --commit
```

Reading production takes a few seconds and needs the service account, so you can
snapshot it once and iterate offline:

```bash
./.venv-pipeline/bin/python -m pipeline.databricks.ingest_2026 --cache /tmp/dh.json
./.venv-pipeline/bin/python -m pipeline.databricks.ingest_2026 --cache /tmp/dh.json --offline
```

Loading is a Delta `MERGE` keyed on the document id, so running `--commit` twice
updates the same rows rather than appending a second copy.

### 3. Publish an assignment run

```bash
# dry run (the default) — solves and prints, writes nothing anywhere
./.venv-pipeline/bin/python -m pipeline.databricks.publish_run

# rehearse against the 2026 check-in sheet instead of the live checkins collection
./.venv-pipeline/bin/python -m pipeline.databricks.publish_run --checkins csv

# publish: Firestore runs/{runId} + the Delta assignments and runs tables
./.venv-pipeline/bin/python -m pipeline.databricks.publish_run --commit
```

Useful flags: `--min-judges 3 --anchors 3 --max-projects-per-judge 12`,
`--out run.json` (save the document without publishing it), `--no-firestore` /
`--no-delta` (publish to one side only).

---

## The tables

All five live in `datahacks.judging`.

### `judges`
One row per `judges/{id}` document — **all generations**. 210 documents exist for
roughly 63 real 2026 judges, so the interesting columns are the ones that tell
them apart:

| column | notes |
|---|---|
| `judge_id` | the Firestore document id |
| `source_generation` | `username_2026` (real) / `legacy_judge_datahacks` / `legacy_slug` |
| `is_real` | true only for `username_2026` |
| `auth_uid` | Firebase Auth UID, when the email resolves to an account |

`evaluations.judge_id` is an **Auth UID**, so joins go through `judges.auth_uid`,
not `judges.judge_id`. That mismatch is one of the three judge identity schemes
this data carries.

### `projects`
One row per `projects/{id}` document, with the synthetic-vs-real call **and its
evidence**:

| column | notes |
|---|---|
| `is_synthetic` | 212 of 372 are dry-run leftovers |
| `classification_source` | `docid_exact` / `docid_normalized` / `title_exact` / `unresolved` |
| `classification_evidence` | which CSV it matched |
| `has_built_with` | corroboration only — the synthetic seeder set this field |

### `evaluations`
One row per submitted score, **including the four 2026 rows with `track: null`**.
Those carry `include_in_analysis = false` and an `exclusion_reason`; the scores
are still there. Analysis queries filter on the flag:

```sql
SELECT track, AVG(total_score)
FROM datahacks.judging.evaluations
WHERE include_in_analysis
GROUP BY track;
```

Filtering them out at load time instead is exactly how they went unnoticed the
first time. `scores` is a `MAP<STRING, INT>` because criterion names differ per
track.

### `assignments`
One row per `(run_id, judge_id, project_id)` the solver produced. `is_anchor`
marks the projects every judge in a track scores — the anchors are what make the
judge–project graph connected on purpose rather than by luck.

### `runs`
One row per assignment run, mirroring `docs/runs-contract.md` (the `runs/{runId}`
document the organizer console reads). Scalars are real columns; the nested
blocks — coverage histogram, per-track preflight, solver config — are JSON
strings, so a change to the contract does not require a schema migration:

```sql
SELECT run_id, judge_count, connectivity_ok,
       get_json_object(coverage_json, '$.min') AS min_judges_per_project
FROM datahacks.judging.runs
ORDER BY generated_at DESC;
```

---

## How project classification works

Resolved entirely from two CSVs already in the repo — the DevPost export is
**not** needed:

* `src/assets/synthetic_projects_generated.csv` — the 212 synthetic ones
* `src/assets/Final_project_info - Sheet1.csv` — the real submission list

The document ids were produced by an old slugger that left repeated and trailing
separators behind, so matching happens in three passes and the pass that hit is
recorded in `classification_source`:

| pass | meaning | example |
|---|---|---|
| `docid_exact` | id already equals `slug(title)` | `tidal-wave` |
| `docid_normalized` | equal only after collapsing repeated separators and stripping trailing ones | `house-m-d-` → *House M.D.* |
| `title_exact` | matched on the document's own `name` field | |
| `unresolved` | in neither CSV — **kept**, marked real, flagged loudly | |

Nothing is ever dropped. A project that matches nothing is still loaded, because
deleting a row you cannot explain is how evidence disappears.

On the current production data all 372 documents resolve: **212 synthetic, 160
real, 0 unresolved**, and `has_built_with` agrees with every single call.

> Note for anyone reading `CLAUDE.md`: it lists 13 punctuation-damaged ids and
> calls `nah--i-d-lose` a genuine unknown. It is actually 14, and
> `nah--i-d-lose` is **not** unknown — *"nah, I'd lose"* is line 138 of
> `Final_project_info - Sheet1.csv`, table 138. The earlier analysis compared
> raw document ids; normalizing the separators resolves it. The conclusion is
> unchanged (treat it as real) but it is now evidence rather than assumption.

---

## What Free Edition can and cannot do

The workspace is **Databricks Free Edition**.

**Can:**
* Unity Catalog, and `CREATE CATALOG` — we verified this and landed on a
  dedicated `datahacks` catalog rather than sharing the built-in `workspace` one.
* One serverless SQL warehouse (`Serverless Starter Warehouse`, XXSMALL,
  auto-stop after 10 minutes, auto-resume on).
* Delta tables, `MERGE`, arrays, maps, JSON functions — everything this pipeline
  needs.

**Cannot:**
* **No classic clusters. Zero.** This tier is serverless-only, so there is
  nothing to attach a Spark session to. PySpark code written as
  `spark.read.table(...)` has no runtime here — that is why this package uses the
  SQL Statement Execution API and does its transforms in pandas locally. With
  ~372 projects and ~262 evaluations, Spark was never the right size of tool
  anyway.
* No jobs/workflows scheduler to hang this off — run it from a laptop or from a
  Databricks notebook.

### If `CREATE CATALOG` is ever denied

Set `DATABRICKS_CATALOG=workspace` in `.env` and re-run
`schema.py --create`. The `workspace` catalog exists in every Unity Catalog
workspace and nothing else has to change. `schema.py` prints exactly this
instruction if the `CREATE CATALOG` fails.

### Cold starts

The warehouse auto-stops after 10 idle minutes. The next statement auto-resumes
it, which takes about 30–60 seconds. `connection.py` waits it out and prints

```
  ... waiting for the SQL warehouse to wake up (auto-resume from STOPPED takes
      about 30-60s; this happens once)
```

so nobody mistakes it for a hang. It gives up after 300 seconds by default.

---

## Tests

```bash
./.venv-pipeline/bin/python -m pytest pipeline/ -q
```

Everything runs offline — the HTTP session is faked and the Firestore documents
are fixtures. The handful of tests marked `live` skip themselves when no
credentials are present. To skip them even when credentials *are* present:

```bash
./.venv-pipeline/bin/python -m pytest pipeline/ -q -m "not live"
```

---

## Safety rules

* **Firestore: reads only**, except `publish_run.py --commit` writing
  `runs/{runId}`.
* Every script defaults to dry-run. `--commit` is always explicit.
* Databricks DDL is idempotent and additive. Nothing in this package drops,
  truncates, or overwrites a table.
* Secrets come from the environment. The token is never logged — `Settings`
  excludes it from `repr()` and from `redacted()`, and there is a test for that.

## Known follow-up

`publish_run.solve_capturing()` temporarily wraps
`solver.enforce_connectivity` so that a run rejected by the connectivity gate
still has an assignment to describe — `DisconnectedAssignmentError` carries only
a message, and `solve()` raises it without returning anything. That wrapper
exists purely because `pipeline/assignment/` was out of scope to edit. Attaching
the assignment to the exception (two lines in `solver.py`) would let it be
deleted.
