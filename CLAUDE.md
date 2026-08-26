# DataHacks Judging Platform

Judge-facing scoring app for the UCSD DataHacks hackathon, plus the data pipeline
that assigns judges to projects and normalizes the resulting scores.

**Last event:** DataHacks 2026, 19–20 April 2026. System has been cold since.
**Next:** a fall hackathon (date TBD) as a rehearsal, then DataHacks 2027.

---

## Stack

| Layer | Tech |
|---|---|
| Frontend | React 19, Vite 5, react-router 7 |
| Auth + serving | Firebase Auth (email/password), Firestore |
| Pipeline | Python 3.9+ (`pipeline/`) |
| Warehouse | Databricks Free Edition, Unity Catalog `datahacks.judging` |
| Seeding / admin | Node scripts (`scripts/`), `firebase-admin` |

Firebase project: `dh-judge-platform`.

**Firebase stays the serving tier.** Databricks cannot host judge auth (workspace
SSO can't provision walk-up judges with 4-digit PINs) and Delta `MERGE` is wrong
for a judge tapping Submit on a phone. Databricks owns everything before and
after the event; Firebase owns the ~8 hours of the event itself.

---

## Layout

```
src/                      React app
  pages/                  Login, Dashboard, Evaluate, Leaderboard, Admin*
  components/             route guards
  config/                 rubric definitions + track aliases
  assets/                 CSVs, rubrics.json, serviceAccount.json  (gitignored)
pipeline/                 Python data pipeline
  tracks.py               track-name reconciliation  (26 tests)
  assignment/             judge->project solver + connectivity gate  (48 tests)
  normalization/          score normalization model  (14 tests)
  etl/                    ingestion  (in progress)
scripts/                  Node seeding / migration scripts
tests/                    Firestore security rules tests  (21 tests)
firestore.rules           security rules  -- NOT YET DEPLOYED
```

`Admin.jsx` is 715 lines of **unrouted dead code**. It is being replaced by the
organizer console; don't invest in it.

---

## Commands

```bash
npm run dev              # vite dev server
npm run build            # production build
npm run lint             # eslint
npm run test:rules       # Firestore rules tests (needs Java, uses emulator)
npm run deploy:rules     # deploy security rules -- REQUIRES YOUR FIREBASE LOGIN

# Python pipeline (venv at .venv-pipeline/, gitignored)
./.venv-pipeline/bin/python -m pytest pipeline/ -q
```

---

## Data model

```
judges/{id}        name, email, track, assignedProjects[]
projects/{id}      name, tracks[], tableNumber, assignedJudges[], description
evaluations/{id}   judgeId, projectId, track, scores{criterion: int}, comment, timestamp
```

Each track's rubric is 5 criteria × 10 points = **50 max**. Rubric definitions
live in `src/assets/rubrics.json`, surfaced through `src/config/trackRubrics.js`.

### New evaluations use a deterministic ID

`evaluations/{judgeUid}_{projectId}`. This makes scoring idempotent — a double
submit, a second tab, or a retry after a dropped connection all resolve to the
same document. 2026 rows still carry random IDs and are updated in place.

---

## Three naming systems for the same track

This has caused real data loss. Keep all three in sync.

| DevPost / submission form | Firestore + app (**authoritative**) | rubrics.json |
|---|---|---|
| Machine Learning & Bio-AI | AI/ML | Machine Learning / AI |
| Data Analytics | Analytics | Data Analytics |
| Cloud Development | Cloud | Cloud |
| Entrepreneurship & Product Management | Entrepreneurship & Product | Entrepreneurship & Product |
| UI/UX Design & Web Development | UI/UX & Web Dev | UI/UX |
| Hardware & IoT | Hardware & IoT | Hardware & IoT |
| Mechanical Design & Biotechnology | Mechanical Design & Biotechnology | Mechanical Design & Biotechnology |
| Economics | Economics | Economics |

- DevPost → Firestore: `pipeline/tracks.py`
- Firestore → rubrics.json: `TRACK_ALIASES` in `src/config/trackRubrics.js`

**Why it matters:** in 2026 a judge opened "Tidal Wave", whose DevPost tracks were
`Data Analytics` / `Cloud Development`. Neither matched the app's track list, so
the rubric lookup failed, the app silently fell back to a generic rubric, and the
evaluation was saved with `track: null` — which the leaderboard then dropped
entirely. Four evaluations were lost this way.

An unmapped track now **raises** rather than returning `None`. Keep it that way.

---

## What broke in 2026 (don't repeat)

1. **Hardcoded roster.** Assignments were generated from a fixed list of 97
   judges. 63 showed. Teams filled in order, so late teams stayed empty while
   projects were still divided across the full team count. 44 projects got one
   judge.
2. **Judges never overlapped.** 251 of 254 project-track slots had exactly one
   judge. Only 5 judge pairs in the entire event scored the same project under
   the same rubric. Score normalization is mathematically impossible on this data
   — judge harshness cannot be separated from project quality.
3. **Silent failures everywhere.** Failed score writes showed nothing. Failed
   track lookups fell back to a generic rubric. Missing projects spun forever.
4. **No security rules.** Anyone with the public web config — which necessarily
   ships in the JS bundle — could read and write every score.
5. **Three judge identity schemes.** `evaluations.judgeId` is an Auth UID,
   `judges/{docId}` is a username, `projects.assignedJudges` holds name-slugs.
   Nothing joins. 210 judge docs exist for ~63 real judges.
6. **Synthetic data in production.** 212 of 372 project docs are dry-run
   leftovers.

**Publish raw averages for 2026.** The normalization model was fitted and came
out degenerate — one track produced an impossible 52.9/50, four tracks reported
zero judge variance, two tracks disagreed about where variation lived. Two-thirds
of projects moved rank. Large movement from unidentifiable parameters is worse
than no correction at all.

---

## Design rules for 2027

From a simulation sweep with planted judge leniency:

| Judges/project | Anchors | Islands | Leniency error | Rank agreement |
|---|---|---|---|---|
| 1 *(2026)* | 0 | 20.0 | 3.22 | 0.74 |
| 1 | 2 | 1.0 | 1.66 | 0.84 |
| 2 | 0 | 1.0 | 1.64 | 0.92 |
| 3 | 1 | 1.0 | 1.17 | 0.95 |

- **≥2 judges per project, 3 preferred.** The dominant lever.
- **1–2 anchor projects per track** — projects every judge in that track scores.
  Makes the graph connected *structurally* rather than by luck.
- **Generate assignments from who actually checked in**, never a roster.
- **Run the connectivity check before judging starts.** After the event a broken
  design cannot be repaired.

Even at 3 judges/project the truly-best project wins only ~half the time.
Hackathon judging is inherently noisy — don't promise precision.

---

## Safety rules for anyone (human or agent) working here

- **Production Firestore holds real scores from a real event.** Read freely.
  Never write without an explicit human decision.
- Every destructive or bulk script must **default to dry-run** and require
  `--commit`.
- `firestore.rules` denies all client writes to `judges` and `projects`. Seeding
  goes through `firebase-admin`, which bypasses rules. Do not loosen the rules to
  make a script work.
- Never commit `src/assets/serviceAccount.json` or `.env` (both gitignored).
  The Firebase web API key was previously hardcoded across ~9 scripts; it now
  lives in one place.
- **Secrets come from the environment, never from source.** `scripts/lib/secrets.js`
  reads them and rejects short or previously-leaked values. Scripts needing one:

  ```
  ORGANIZER_PASSWORD=...     # scripts/create-leaderboard-user.js
  TEST_JUDGE_PASSWORD=...    # scripts/create-test-judge.js
  ```

  Run with `node --env-file=.env scripts/<name>.js --commit`. **Do not use a
  `VITE_` prefix** — Vite inlines those into the client bundle, which would
  publish the password to anyone who opens the site.

---

## Current status

**Done and tested — 36 rules tests + 207 pipeline tests:**
- `firestore.rules` — closes anonymous read/write; covers `evaluations`,
  `judges`, `projects`, `checkins`, `runs`
- `Evaluate.jsx` — silent save failures, duplicate writes, wrong-rubric fallback,
  unreachable 0 score, infinite spinner
- `Login.jsx` PIN padding; `Leaderboard.jsx` impure state updater
- `pipeline/tracks.py`, `pipeline/assignment/`, `pipeline/normalization/`,
  `pipeline/etl/`
- `src/pages/Organizer.jsx` — check-in, assignment status, live coverage
- All 10 seed scripts moved to `firebase-admin`, dry-run by default

**Blocked on the user:**
- Deploy the rules: `npm run test:rules` then `npm run deploy:rules`
- Restrict the Firebase web API key in Google Cloud Console (see below)

**The web API key is NOT a secret.** Firebase browser keys are public by design —
they ship in every client bundle and only identify the project. It appears in 4
commits of git history; that is expected, not a breach. Security comes from
`firestore.rules` + Auth, not from hiding the key. The worthwhile hardening is
*restricting* it (HTTP referrers + API restrictions in Google Cloud Console), not
rotating it.

The service account (`src/assets/serviceAccount.json`) and `.env` are the real
secrets. Both are gitignored and **verified never committed** — keep it that way.

### Databricks

Workspace is live. Catalog `datahacks`, schema `judging`, serverless warehouse
`1f67d9d641d91724` (auto-stops after 10 min; first query after idle takes ~18s).

| Table | Rows |
|---|---|
| `judges` | 210 |
| `projects` | 372 |
| `evaluations` | 262 (258 analysable, 4 flagged `include_in_analysis=false`) |
| `assignments` | 0 — written by `publish_run.py --commit` |
| `runs` | 0 — same |

**Free Edition has no classic clusters and no job scheduler.** It is serverless
only, so there is no Spark session to attach to: use the SQL Statement Execution
API and keep transforms in pandas. `publish_run.py` runs from a laptop or a
notebook — there is nothing to schedule it on.

Ingest is a Delta `MERGE` on the primary key; re-running is a no-op.

**Done:** organizer account password changed.

**Decided — do not revisit:**
- *The 42 real judges with no Auth account:* left alone. The event has passed and
  recreating logins serves no purpose. They stay in the archive bucket.
- *2026 rankings:* publish raw averages. Normalization is not identifiable on
  this data.
- *The DevPost export is not required for cleanup.* Project classification is
  fully solvable from the two CSVs already in the repo (see below). The export is
  only needed for AI artifact scoring, which needs video/description text.

### Project classification (resolved without DevPost)

Of 372 project docs: **212 synthetic** (present in
`synthetic_projects_generated.csv`) and **160 real**. Nothing is unresolved.

146 real projects match `Final_project_info - Sheet1.csv` directly. A further 14
match once punctuation is normalized — the old slugger left trailing separators:

| Firestore doc id | Real title |
|---|---|
| `3-sharks-1-blue---` | 3 Sharks 1 Blue 🧿 |
| `house-m-d-` | House M.D. |
| `panic--at-the-dataset` | Panic! At the Dataset |
| `o-anxiety-2-` | O(anxiety^2) |
| `ctrl---c` | CTRL + C |
| `-` | ㄖ |

(plus `bs--biotech-and-science-`, `depths-of-dev---`, `git-push---git-pull-day`,
`hyvs--hives-`, `lizzzzard----`, `solo---tech`, `sunflower---leaf`)

`nah--i-d-lose` is **"nah, I'd lose"**, line 138 of the real project list. The old
slugger turned both the comma and the space into separators. It was briefly
recorded here as an unknown; it is not.

Use `pipeline/tracks.py`-style normalization (collapse repeated separators, strip
leading/trailing ones) when matching titles to doc ids. The naive slugger that
produced these ids is the bug.

**Nothing has been deployed to Firebase or Databricks. Production is untouched:
judges 210, projects 372, evaluations 262, checkins 0, runs 0.**

### Identity migration — DONE (Aug 2026)

`judges` is now keyed by Firebase Auth UID, matching `evaluations.judgeId`.

| | Before | After |
|---|---|---|
| judge docs | 211 | **65** |
| keyed by UID | 0 | **65** |
| quarantined in `judges_archive` | — | 146 |
| eval judgeIds that join to a judge | 0% | **94%** |

The remaining 6% are the four dry-run testers whose Auth accounts were deleted.
`judges_archive` is not listed in `firestore.rules`, so the default-deny rule
blocks all client access; admin SDK reads still work. Nothing was destroyed.

`docs/judge-id-map.csv` maps every name to its UID.

Synthetic projects were also archived: **372 -> 166** (206 moved to
`projects_archive`, 6 kept because they carry real evaluations).

### Next structural change: namespace by event

Everything currently lives in flat top-level collections, so 2026's data sits
in the same place the fall event will. That is how a judge could be served a
stale assignment. Planned shape:

```
events/{eventId}                     name, date, status
  judges/{uid}  projects/{id}  evaluations/{uid}_{projectId}
  checkins/{uid}  runs/{runId}
```

Two things this buys beyond tidiness:
- an `archived` status makes a past event read-only *structurally*, via rules,
  rather than relying on every query remembering to filter
- one pointer document names the live event; the judge app reads only that

Rejected alternative: keeping flat collections and adding an `eventId` field.
Easier to migrate to, but every query must remember to filter, and forgetting
once serves stale data — precisely the failure being designed out.

Do this before the fall event. It touches every collection and the rules.

### KNOWN GAP: nothing publishes assignments to judges

`publish_run.py` writes `runs/{runId}` and the Delta tables. It does **not**
write `judges.assignedProjects`, which is what the judge app actually reads.
So the solver can report a green connectivity check while judges still see
stale assignments. Verified: the last run produced 623 assignments while
`judges.assignedProjects` still held 312 refs from 2026.

Closing this needs dry-run, a printed diff, and a guard against overwriting
once judging has started.

### Known data problems still open

| Problem | Count |
|---|---|
| Real judges with assignments but **no login** | 42 (decided: leave) |
| Judges with no email at all | 6 |
| Junk emails (`Puligundla`, `?`) | 2 |
| Orphaned evaluations (Auth account deleted) | 8 |

`Shreya Yembarwar` (roster) vs `Shreyas Yembarwar` (credentials) is a
one-character typo breaking that join.
