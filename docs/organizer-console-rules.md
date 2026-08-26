# Firestore rules needed by the Organizer Console

The Organizer Console (`/organizer`) introduces two collections that the current
`firestore.rules` does not mention. Because that file ends in a default-deny
`match /{document=**}`, **both collections are currently denied outright** — the
console will render a red "Permission denied by Firestore rules" banner until
the blocks below are merged.

`firestore.rules` was deliberately not edited here (another change is in flight
in that file). Paste the two blocks below **inside**
`match /databases/{database}/documents { … }`, above the final
`match /{document=**}` catch-all. They rely only on the `isOrganizer()` and
`signedIn()` helpers that already exist in the file.

---

## 1. `checkins` — judge arrival log

One document per judge, keyed by the **judge document ID** (`judges/{id}`, e.g.
`AarushiBajaj`). The console never mutates `judges` docs.

```
    // ── checkins ─────────────────────────────────────────────────────────────
    // Written by organizers during check-in. Doc ID is the judges/{id} doc ID
    // (today a username; the same key after the UID migration rekeys judges).
    //
    // Judges may read their own row so a future "you're checked in" state can
    // be shown in the app; only organizers write. Deletes are ALLOWED here —
    // undoing a check-in is a normal, frequent operation at the door, and a
    // check-in carries no scoring value worth auditing.

    match /checkins/{judgeId} {
      allow get, list: if isOrganizer() || signedIn();

      allow create, update: if isOrganizer()
        && request.resource.data.checkedIn is bool
        && request.resource.data.keys().hasOnly([
             'judgeId', 'name', 'email', 'track',
             'checkedIn', 'checkedInAt', 'floater', 'cutoffAt', 'checkedInBy'
           ])
        && (!('judgeId' in request.resource.data.keys())
             || request.resource.data.judgeId == judgeId)
        && (!('floater' in request.resource.data.keys())
             || request.resource.data.floater is bool);

      allow delete: if isOrganizer();
    }
```

If you would rather not let every signed-in judge list the check-in roster,
tighten the read to:

```
      allow get: if isOrganizer() || judgeId == request.auth.uid;
      allow list: if isOrganizer();
```

The console only needs the organizer branch.

### Fields written

| Field | Type | Notes |
|---|---|---|
| `judgeId` | string | Mirrors the doc ID |
| `name`, `email`, `track` | string | Denormalized from the judge doc so the console can render check-ins without a join |
| `checkedIn` | bool | Always `true` on write. Undo **deletes** the doc; the field exists so a soft-undo stays possible without a rules change |
| `checkedInAt` | timestamp | `serverTimestamp()` — never a client clock |
| `floater` | bool | `true` when checked in after the cutoff. Organizers can toggle it afterwards |
| `cutoffAt` | timestamp \| null | The cutoff that was in force at check-in time, so the floater decision stays auditable |
| `checkedInBy` | string \| null | Organizer email |

`floater` is computed and frozen at write time rather than derived on read — a
later edit to the cutoff must not silently reclassify people who already
arrived.

---

## 2. `runs` — assignment run reports

Written **only** by the Python job via the admin SDK, which bypasses rules
entirely. So the rule is read-only for everyone, including organizers.

```
    // ── runs ─────────────────────────────────────────────────────────────────
    // Assignment run reports written by pipeline/assignment/ through the admin
    // SDK (which bypasses these rules). Read-only to every client; the
    // Organizer Console must never be able to fake a passing preflight.
    // Shape: docs/runs-contract.md

    match /runs/{runId} {
      allow get, list: if isOrganizer();
      allow write: if false;
    }
```

---

## Merged placement

```
service cloud.firestore {
  match /databases/{database}/documents {

    // … existing helpers: signedIn(), isOrganizer(), ownJudgeDoc(), … …

    match /judges/{judgeId}          { … existing … }
    match /projects/{projectId}      { … existing … }
    match /evaluations/{evaluationId}{ … existing … }

    match /checkins/{judgeId}        { … block 1 above … }
    match /runs/{runId}              { … block 2 above … }

    match /{document=**} {
      allow read, write: if false;
    }
  }
}
```

Deploy with the existing script:

```
npm run deploy:rules
```

---

## Verifying

The console also reads `judges`, `projects` and `evaluations` in full via
`onSnapshot`. Those `list` operations are already permitted for organizers by
the existing rules (`allow get, list: if isOrganizer() || …`), so no change is
needed there.

A quick emulator check for the two new collections:

| Actor | Operation | Expected |
|---|---|---|
| Organizer (`admin` claim or `ds3@datahacks2026.ucsd`) | `set checkins/AarushiBajaj` | allow |
| Organizer | `delete checkins/AarushiBajaj` | allow |
| Ordinary judge | `set checkins/AarushiBajaj` | **deny** |
| Ordinary judge | `list checkins` | allow (or deny, under the tightened variant) |
| Organizer | `list runs` | allow |
| Organizer | `set runs/anything` | **deny** |
| Ordinary judge | `get runs/anything` | **deny** |

---

## Related

- `docs/runs-contract.md` — the `runs/{runId}` document shape and the Python
  writer.
- `src/components/OrganizerRoute.jsx` — the client-side gate (`admin` custom
  claim, falling back to the organizer email). It is a UX guard only; these
  rules are the actual security boundary.
