/**
 * migrate-judge-identity.js — unify the three judge identifiers on Auth UID.
 *
 * Production currently carries three incompatible ways of naming a judge:
 *
 *   evaluations.judgeId     Firebase Auth UID          ← the only trustworthy one
 *   judges/{docId}          username ("AarushiBajaj") or name-slug ("alex-cloninger")
 *   projects.assignedJudges name-slug ("anuj-jain")
 *
 * It also holds two overlapping generations of judge docs:
 *
 *   "real"   @datahacks2026.ucsd, docId = username        — the shipped event data
 *   "stale"  @judge.datahacks,    docId = name-slug       — synthetic pre-event data
 *
 * This script resolves every judge doc to a UID via Firebase Auth email lookup,
 * then plans:
 *
 *   1. rekey  judges/{username} → judges/{uid}   (for docs that resolve)
 *   2. rewrite projects.assignedJudges to UIDs   (only where a token resolves)
 *   3. quarantine unresolvable docs into judges_archive/{docId} — NEVER deleted
 *      outright, so nothing is lost if a resolution turns out to be wrong.
 *
 * Nothing is guessed. A judge doc resolves only if its stored email maps to a
 * real Auth account. Docs that don't resolve are reported by name and reason.
 *
 * Usage:
 *   node scripts/migrate-judge-identity.js                 # dry run (default)
 *   node scripts/migrate-judge-identity.js --commit --yes  # apply
 *
 * Optional:
 *   --bridge-names   also resolve projects.assignedJudges name-slugs through the
 *                    real generation's judge NAMES. This is an inference across
 *                    two data generations, so it is off by default. Read the
 *                    dry-run report before enabling it.
 *   --archive-assigned-judges
 *                    clear projects.assignedJudges on projects whose tokens all
 *                    belong to the archived generation, instead of leaving dead
 *                    identifiers in place.
 *
 * DRY RUN BY DEFAULT. Only --commit writes.
 */

import { auth, db } from "./lib/admin.js";
import { ChangePlan, gate, parseArgs } from "./lib/cli.js";

const args = parseArgs();
const BRIDGE_NAMES = args.flags.has("--bridge-names");
const ARCHIVE_ASSIGNED = args.flags.has("--archive-assigned-judges");

const REAL_DOMAIN = "datahacks2026.ucsd";
const STALE_DOMAIN = "judge.datahacks";

const toSlug = (s) =>
  (s || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

const domainOf = (email) => ((email || "").toLowerCase().split("@")[1] || "");

const line = (label) => console.log(`\n── ${label} `.padEnd(78, "─"));

// ── Load ─────────────────────────────────────────────────────────────────────
console.log("Reading production Firestore + Auth (read-only)…");

const [judgeSnap, projectSnap, evalSnap] = await Promise.all([
  db.collection("judges").get(),
  db.collection("projects").get(),
  db.collection("evaluations").get(),
]);

const judgeDocs = judgeSnap.docs.map((d) => ({ id: d.id, data: d.data() }));

// All Auth users, indexed by uid — used to audit evaluations.judgeId.
const authUsers = [];
let pageToken;
do {
  const page = await auth.listUsers(1000, pageToken);
  authUsers.push(...page.users);
  pageToken = page.pageToken;
} while (pageToken);
const authByUid = new Map(authUsers.map((u) => [u.uid, u]));
const knownUids = new Set(authByUid.keys());

console.log(
  `  judges=${judgeDocs.length}  projects=${projectSnap.size}  evaluations=${evalSnap.size}  authUsers=${authUsers.length}`
);

// ── Step 1: resolve every judge doc to a UID by email ────────────────────────
// Batched form of auth.getUserByEmail — same lookup, 100 at a time.
async function resolveEmails(emails) {
  const found = new Map();
  for (let i = 0; i < emails.length; i += 100) {
    const chunk = emails.slice(i, i + 100);
    const res = await auth.getUsers(chunk.map((email) => ({ email })));
    for (const u of res.users) found.set((u.email || "").toLowerCase(), u.uid);
  }
  return found;
}

const emailsToResolve = [
  ...new Set(
    judgeDocs.map((j) => (j.data.email || "").toLowerCase()).filter((e) => e.includes("@"))
  ),
];
const uidByEmail = await resolveEmails(emailsToResolve);

const resolved = []; // { id, data, uid }
const unresolved = []; // { id, data, reason }

for (const j of judgeDocs) {
  const email = (j.data.email || "").toLowerCase();
  if (!email.includes("@")) {
    unresolved.push({ ...j, reason: "judge doc has no email field" });
    continue;
  }
  const uid = uidByEmail.get(email);
  if (uid) resolved.push({ ...j, uid, email });
  else
    unresolved.push({
      ...j,
      email,
      reason:
        domainOf(email) === STALE_DOMAIN
          ? "stale generation — placeholder email, no Auth account ever existed"
          : "no Auth account for this email (never created, or deleted)",
    });
}

line("Step 1 — judge doc → Auth UID");
console.log(`  judge docs total       : ${judgeDocs.length}`);
console.log(`  RESOLVED to a UID      : ${resolved.length}`);
console.log(`  UNRESOLVABLE           : ${unresolved.length}`);

const byDomain = (arr) => {
  const out = {};
  for (const j of arr) {
    const d = domainOf(j.data.email) || "(no email)";
    out[d] = (out[d] || 0) + 1;
  }
  return out;
};
console.log(`  resolved by domain     :`, byDomain(resolved));
console.log(`  unresolvable by domain :`, byDomain(unresolved));

// Collision guard: two judge docs must never rekey onto the same UID.
const docsByUid = new Map();
for (const r of resolved) {
  if (!docsByUid.has(r.uid)) docsByUid.set(r.uid, []);
  docsByUid.get(r.uid).push(r);
}
const collisions = [...docsByUid.entries()].filter(([, v]) => v.length > 1);
if (collisions.length) {
  console.error(`\n  ✗ REFUSING TO PLAN: ${collisions.length} UID collisions`);
  for (const [uid, docs] of collisions) {
    console.error(`      ${uid} ← ${docs.map((d) => d.id).join(", ")}`);
  }
  process.exit(1);
}

line("Step 1b — judge docs that CANNOT be resolved (not guessed, not deleted)");
const unresolvedReal = unresolved.filter((j) => domainOf(j.data.email) === REAL_DOMAIN);
const unresolvedStale = unresolved.filter((j) => domainOf(j.data.email) === STALE_DOMAIN);
const unresolvedOther = unresolved.filter(
  (j) => ![REAL_DOMAIN, STALE_DOMAIN].includes(domainOf(j.data.email))
);

console.log(
  `  ${unresolvedReal.length} REAL-generation judges (@${REAL_DOMAIN}) have no Auth account.`
);
console.log(`     These are real people who cannot sign in. Sample:`);
for (const j of unresolvedReal.slice(0, 15)) {
  console.log(
    `       ${j.id.padEnd(34)} ${j.data.email}  (${(j.data.assignedProjects || []).length} assigned projects)`
  );
}
if (unresolvedReal.length > 15) console.log(`       … and ${unresolvedReal.length - 15} more`);

console.log(
  `\n  ${unresolvedStale.length} STALE-generation judges (@${STALE_DOMAIN}) — synthetic, never had accounts.`
);
if (unresolvedOther.length) {
  console.log(`\n  ${unresolvedOther.length} other unresolvable docs:`);
  for (const j of unresolvedOther) {
    console.log(`       ${j.id.padEnd(34)} ${JSON.stringify(j.data).slice(0, 90)}  — ${j.reason}`);
  }
}

// ── Step 2: plan the projects.assignedJudges rewrite ─────────────────────────
// Resolution tiers, strictest first.
const uidByDocId = new Map(resolved.map((r) => [r.id, r.uid]));

// Name bridge: real-generation judge docs indexed by name-slug. Only unambiguous
// names are usable; a slug that matches two real docs is left unresolved.
const realBySlug = new Map();
for (const r of resolved) {
  if (domainOf(r.data.email) !== REAL_DOMAIN) continue;
  const s = toSlug(r.data.name);
  if (!realBySlug.has(s)) realBySlug.set(s, []);
  realBySlug.get(s).push(r);
}

const archivedDocIds = new Set(unresolved.map((j) => j.id));

const tokenStats = { alreadyUid: 0, viaDocId: 0, viaNameBridge: 0, unresolved: 0 };
const unresolvedTokens = new Map(); // token → why

function resolveToken(token) {
  if (knownUids.has(token)) {
    tokenStats.alreadyUid++;
    return token;
  }
  const direct = uidByDocId.get(token);
  if (direct) {
    tokenStats.viaDocId++;
    return direct;
  }
  if (BRIDGE_NAMES) {
    const cands = realBySlug.get(token);
    if (cands?.length === 1) {
      tokenStats.viaNameBridge++;
      return cands[0].uid;
    }
    if (cands?.length > 1) {
      tokenStats.unresolved++;
      unresolvedTokens.set(token, `ambiguous: matches ${cands.map((c) => c.id).join(" & ")}`);
      return null;
    }
  }
  tokenStats.unresolved++;
  unresolvedTokens.set(
    token,
    archivedDocIds.has(token)
      ? "points at a judge doc being archived (no Auth account)"
      : "no judge doc or Auth account matches this token"
  );
  return null;
}

const projectRewrites = []; // { id, from, to, allDead }
for (const p of projectSnap.docs) {
  const current = p.data().assignedJudges;
  if (!Array.isArray(current) || current.length === 0) continue;

  const next = [];
  let changed = false;
  let liveCount = 0;
  for (const token of current) {
    const uid = resolveToken(token);
    if (uid) {
      liveCount++;
      if (uid !== token) changed = true;
      if (!next.includes(uid)) next.push(uid);
    } else {
      next.push(token); // keep unknown tokens rather than silently dropping data
    }
  }

  const allDead = liveCount === 0;
  if (allDead && ARCHIVE_ASSIGNED) {
    projectRewrites.push({ id: p.id, from: current, to: [], allDead });
  } else if (changed) {
    projectRewrites.push({ id: p.id, from: current, to: next, allDead });
  }
}

line("Step 2 — projects.assignedJudges rewrite");
const projectsWithAssigned = projectSnap.docs.filter(
  (p) => (p.data().assignedJudges || []).length
).length;
console.log(`  projects carrying assignedJudges : ${projectsWithAssigned} / ${projectSnap.size}`);
console.log(`  token resolutions:`);
console.log(`     already a UID          : ${tokenStats.alreadyUid}`);
console.log(`     resolved via judge docId: ${tokenStats.viaDocId}`);
console.log(
  `     resolved via name bridge: ${tokenStats.viaNameBridge}${BRIDGE_NAMES ? "" : "  (--bridge-names not set)"}`
);
console.log(`     UNRESOLVED             : ${tokenStats.unresolved}`);
console.log(`  projects whose assignedJudges would change: ${projectRewrites.length}`);

if (unresolvedTokens.size) {
  console.log(`\n  distinct unresolved tokens: ${unresolvedTokens.size}`);
  for (const [t, why] of [...unresolvedTokens].slice(0, 12)) {
    console.log(`     ${t.padEnd(34)} — ${why}`);
  }
  if (unresolvedTokens.size > 12) console.log(`     … and ${unresolvedTokens.size - 12} more`);
}

// ── Step 3: audit evaluations (report only — never rewritten) ────────────────
line("Step 3 — evaluations audit (read-only, nothing is written to evaluations)");
const evalUids = new Map();
for (const e of evalSnap.docs) {
  const id = e.data().judgeId;
  evalUids.set(id, (evalUids.get(id) || 0) + 1);
}
const orphanUids = [...evalUids.keys()].filter((u) => !knownUids.has(u));
const orphanRows = orphanUids.reduce((n, u) => n + evalUids.get(u), 0);
const resolvedUidSet = new Set(resolved.map((r) => r.uid));
const evalNoJudgeDoc = [...evalUids.keys()].filter((u) => !resolvedUidSet.has(u));

console.log(`  evaluation rows              : ${evalSnap.size}`);
console.log(`  distinct judgeId             : ${evalUids.size} (all Auth-UID shaped)`);
console.log(`  judgeIds with NO Auth account: ${orphanUids.length}  (${orphanRows} rows orphaned)`);
for (const u of orphanUids) console.log(`     ${u}  — ${evalUids.get(u)} rows, account deleted`);
console.log(
  `  judgeIds with no surviving judge doc after migration: ${evalNoJudgeDoc.length}`
);

// ── Build the change plan ────────────────────────────────────────────────────
const plan = new ChangePlan("judge identity migration");

let rekeyed = 0, alreadyKeyed = 0;
for (const r of resolved) {
  if (r.id === r.uid) {
    alreadyKeyed++;
    continue;
  }
  rekeyed++;
  plan.create(`judges/${r.uid}`, `← was judges/${r.id}  (${r.data.name})`);
  plan.delete(`judges/${r.id}`, `— rekeyed to ${r.uid}`);
}

for (const j of unresolved) {
  plan.create(`judges_archive/${j.id}`, `— ${j.data.name || "(no name)"} · ${j.reason}`);
  plan.delete(`judges/${j.id}`, `— quarantined, not destroyed`);
}

for (const rw of projectRewrites) {
  plan.update(
    `projects/${rw.id}`,
    `— assignedJudges ${rw.from.length} → ${rw.to.length}${rw.allDead ? " (all tokens dead)" : ""}`
  );
}

line("Summary");
console.log(`  judge docs already keyed by UID : ${alreadyKeyed}`);
console.log(`  judge docs to rekey to UID      : ${rekeyed}`);
console.log(`  judge docs to quarantine        : ${unresolved.length}`);
console.log(`  projects to rewrite             : ${projectRewrites.length}`);
console.log(
  `  judges collection after migration: ${resolved.length} docs (from ${judgeDocs.length})`
);

if (!(await gate(args, plan))) process.exit(0);

// ── Commit ───────────────────────────────────────────────────────────────────
const now = new Date().toISOString();
let writes = 0;
let batch = db.batch();
const flush = async () => {
  if (writes === 0) return;
  await batch.commit();
  batch = db.batch();
  writes = 0;
};
const stage = async (fn) => {
  fn(batch);
  if (++writes >= 400) await flush();
};

console.log("\nRekeying judge docs…");
for (const r of resolved) {
  if (r.id === r.uid) continue;
  await stage((b) => {
    b.set(db.collection("judges").doc(r.uid), {
      ...r.data,
      _legacyDocId: r.id,
      _migratedAt: now,
    });
  });
  await stage((b) => b.delete(db.collection("judges").doc(r.id)));
}

console.log("Quarantining unresolvable judge docs into judges_archive…");
for (const j of unresolved) {
  await stage((b) => {
    b.set(db.collection("judges_archive").doc(j.id), {
      ...j.data,
      _legacyDocId: j.id,
      _archiveReason: j.reason,
      _archivedAt: now,
    });
  });
  await stage((b) => b.delete(db.collection("judges").doc(j.id)));
}

console.log("Rewriting projects.assignedJudges…");
for (const rw of projectRewrites) {
  await stage((b) =>
    b.set(db.collection("projects").doc(rw.id), { assignedJudges: rw.to }, { merge: true })
  );
}

await flush();

console.log("\nDone ✅");
console.log(`  ${rekeyed} judge docs rekeyed to UID`);
console.log(`  ${unresolved.length} judge docs quarantined in judges_archive`);
console.log(`  ${projectRewrites.length} projects rewritten`);
console.log(
  `\n  NOTE: judges_archive is NOT covered by firestore.rules — the default-deny\n` +
    `        rule at the bottom of the ruleset blocks all client access to it,\n` +
    `        which is what we want. Admin SDK reads still work.`
);
process.exit(0);
