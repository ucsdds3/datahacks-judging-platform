# `runs/{runId}` — assignment run contract

The judge→project solver lives in `pipeline/assignment/` and runs as a Python
job. There is no backend server, so the SPA cannot invoke it. Instead the job
**writes one document per solve** into the `runs` collection, and the Organizer
Console (`/organizer` → *Assignment status*) reads it.

The console never writes to `runs`. It only reads.

---

## Identity and ordering

| | |
|---|---|
| Collection | `runs` |
| Document ID | `runId` — anything sortable and unique. Recommended: `2026-04-19T09-32-11Z` or `run_20260419_093211`. |
| Ordering | The console queries `orderBy("generatedAt", "desc"), limit(20)`. **A document without `generatedAt` is invisible to the console.** |
| "Current" run | The newest `generatedAt` wins. There is no `runs/current` pointer to keep in sync. |

Writes must come from the admin SDK (a service account). See
`docs/organizer-console-rules.md` — client writes to `runs` are denied.

---

## Document shape

Everything except `generatedAt` is optional; the console degrades to "—" or an
explicit "not reported" state rather than crashing. But the fields marked
**required for the gate** are the ones that make the page worth looking at.

```jsonc
{
  // ── identity ────────────────────────────────────────────────────────────
  "runId":        "2026-04-19T09-32-11Z",   // mirror of the doc ID
  "generatedAt":  Timestamp,                 // REQUIRED. Firestore Timestamp preferred;
                                             // an ISO-8601 UTC string also works
                                             // (parsed client-side, sorts correctly).
  "generatedBy":  "map@ucsd.edu",            // who ran it
  "source":       "pipeline.assignment.solver@2026.1",
  "status":       "ok",                      // "ok" | "failed" | "running"

  // ── inputs ──────────────────────────────────────────────────────────────
  "judgeCount":   63,     // judges the solver actually solved for.
                          // MUST be the checked-in count, not the roster count.
                          // The console compares this against live check-ins and
                          // shows a "roster drift" warning when they differ —
                          // that gap (97 vs 63) is what broke 2026.
  "projectCount": 372,
  "checkinSnapshotCount": 63,   // how many checkins docs existed when the job read them

  "config": {
    "minJudgesPerProject":   3,      // drives the target used by Live coverage too
    "anchorsPerTrack":       3,
    "maxProjectsPerJudge":   null,
    "balanceMultiTrack":     true,
    "topUpUndersizedTracks": true
  },

  // ── the gate ────────────────────────────────────────────────────────────
  "connectivityOk": true,   // REQUIRED FOR THE GATE.
                            // false renders a full-width red "do not judge
                            // against this assignment" banner.
                            // Omitting it renders an amber "connectivity not
                            // reported — treat as unverified" banner.

  // ── coverage ────────────────────────────────────────────────────────────
  "coverage": {
    "histogram": { "0": 0, "1": 0, "2": 4, "3": 350, "4": 18 },  // judges-per-project → project count
    "min": 2, "median": 3, "mean": 3.05, "max": 4,
    "nProjects": 372,
    "unjudged": ["some-project-id"],
    "belowTarget": [
      { "projectId": "tai", "name": "TAI", "judges": 2, "tableNumber": 41 }
    ]
  },

  "pairOverlapPct": 41.2,             // all judge pairs sharing >=1 project
  "pairOverlapWithinTrackPct": 88.6,  // the number that actually matters

  "workload": {
    "min": 4, "median": 6, "mean": 6.1, "max": 8, "stdev": 0.9,
    "idleJudges": []                  // judge ids with zero projects
  },

  // ── per-track preflight ─────────────────────────────────────────────────
  "tracks": [
    {
      "track": "AI/ML",
      "judges": 21,
      "projects": 118,
      "connected": true,
      "components": 1,                 // islands; > 1 means DISCONNECTED
      "isolatedJudges": [],            // judge ids with no projects at all
      "overlapPct": 91.4,
      "anchors": ["neuralscan", "3sl", "comomo"]
    }
  ],

  // ── free text ───────────────────────────────────────────────────────────
  "warnings": [
    "Cloud: only 3 judges for 31 eligible projects; 12 projects topped up from AI/ML."
  ],
  "errors": [],

  // ── optional but strongly recommended ───────────────────────────────────
  "judgeIndex": {
    "gR7YXbnEZMbh23fq0Wx6MwDdZuH3": { "judgeDocId": "ManojKrishnaMohan", "name": "Manoj Krishna Mohan", "track": "AI/ML" }
  }
}
```

### Why `judgeIndex` matters

`evaluations.judgeId` is a **Firebase Auth UID**. `judges/{id}` is keyed by
**username** (`AarushiBajaj`). Nothing in Firestore joins the two, so the
console cannot say for certain which checked-in judge has submitted nothing.

It currently falls back to inference (a UID's evaluated projects must be a
subset of exactly one judge's `assignedProjects`, iterated to a fixed point) and
reports whatever stays ambiguous as *unattributed* rather than guessing. If you
publish `judgeIndex`, attribution becomes exact and that caveat disappears.

The solver already keys judges by Auth UID (`Judge.id`), so it has the mapping
in hand. A `judges/{id}.uid` field written by the seeding script works equally
well — the console prefers it over everything else.

Keep `judgeIndex` under a few hundred entries; the 1 MB document limit is the
only ceiling (210 judges ≈ 30 KB).

---

## Writing it from Python

`validate.preflight()` already produces almost this exact structure. The
translation is mechanical:

```python
from datetime import datetime, timezone
from google.cloud import firestore

from pipeline.assignment.solver import solve, SolverConfig

result = solve(judges=checked_in_judges, projects=projects, config=SolverConfig())
report = result.preflight()

conn = report["connectivity"]
cov = report["coverage"]
projects_by_id = {p.id: p for p in result.projects}

run_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")

doc = {
    "runId": run_id,
    "generatedAt": firestore.SERVER_TIMESTAMP,
    "generatedBy": operator_email,
    "source": "pipeline.assignment.solver",
    "status": "ok" if report["ok"] else "failed",

    "judgeCount": len(result.judges),
    "projectCount": len(result.projects),
    "checkinSnapshotCount": len(checked_in_judges),

    "config": {
        "minJudgesPerProject": result.config.min_judges_per_project,
        "anchorsPerTrack": result.config.anchors_per_track,
        "maxProjectsPerJudge": result.config.max_projects_per_judge,
        "balanceMultiTrack": result.config.balance_multi_track,
        "topUpUndersizedTracks": result.config.top_up_undersized_tracks,
    },

    "connectivityOk": conn["ok"],

    "coverage": {
        # Firestore map keys must be strings.
        "histogram": {str(k): v for k, v in cov["histogram"].items()},
        "min": cov["min"], "median": cov["median"],
        "mean": cov["mean"], "max": cov["max"],
        "nProjects": cov["n_projects"],
        "unjudged": cov["unjudged"],
        "belowTarget": [
            {
                "projectId": pid,
                "name": getattr(projects_by_id.get(pid), "name", pid),
                "judges": n,
                "tableNumber": getattr(projects_by_id.get(pid), "table_number", None),
            }
            for pid, n in report["projects_below_target"]
        ],
    },

    "pairOverlapPct": report["pair_overlap"]["pct"],
    "pairOverlapWithinTrackPct": report["pair_overlap_within_track"]["pct"],

    "workload": {
        **{k: report["workload"]["overall"][k]
           for k in ("min", "median", "mean", "max", "stdev")},
        "idleJudges": report["workload"]["idle_judges"],
    },

    "tracks": [
        {
            "track": track,
            "judges": info["n_judges"],
            "projects": info["n_projects"],
            "connected": info["connected"],
            "components": info["n_components"],
            "isolatedJudges": info["isolated_judges"],
            "overlapPct": report["pair_overlap"]["per_track"].get(track, {}).get("pct", 0.0),
            "anchors": result.anchors.get(track, []),
        }
        for track, info in conn["tracks"].items()
    ],

    "warnings": list(result.warnings),
    "errors": [],

    "judgeIndex": {
        j.id: {"judgeDocId": judge_doc_ids[j.id], "name": j.name, "track": j.track}
        for j in result.judges
    },
}

firestore.Client().collection("runs").document(run_id).set(doc)
```

### Failed runs

If `solve()` raises `DisconnectedAssignmentError`, **still write a run
document** — a failure the organizer can see beats silence:

```python
doc = {
    "runId": run_id,
    "generatedAt": firestore.SERVER_TIMESTAMP,
    "status": "failed",
    "connectivityOk": False,
    "judgeCount": len(checked_in_judges),
    "projectCount": len(projects),
    "tracks": [...],           # from check_connectivity(), so the console can name the islands
    "errors": [str(exc)],
    "warnings": [],
}
```

The console renders `connectivityOk: false` as a red, unmissable banner naming
each disconnected track, its island count, and the stranded judges by name.
That banner is the check that would have caught 2026 before judging opened.

---

## What the console does with each field

| Field | Effect in the UI |
|---|---|
| `generatedAt` | "Generated" stat; ordering; late-arrival comparison |
| `judgeCount` | Compared live against checked-in count → "roster drift" warning |
| `connectivityOk` | Green OK banner / red FAILED banner / amber "not reported" |
| `tracks[].connected`, `.components`, `.isolatedJudges` | Contents of the red banner + per-track preflight table |
| `coverage.histogram` | Bar chart, bars below target rendered red |
| `coverage.belowTarget` | Count line under the histogram |
| `config.minJudgesPerProject` | Target used by the *Live coverage* tab as well |
| `config.anchorsPerTrack`, `.maxProjectsPerJudge` | Solver configuration stats |
| `warnings[]` | Warnings card |
| `errors[]` | Red errors banner |
| `judgeIndex` | Exact judge↔UID attribution in *Live coverage* |
