/**
 * cleanup-seed.js — remove stale docs left behind by the original seed.js.
 *
 * Usage:
 *   node scripts/cleanup-seed.js [--commit] [--yes]
 *
 * DRY RUN BY DEFAULT — pass --commit to actually delete.
 */

import { db } from "./lib/admin.js";
import { ChangePlan, gate, parseArgs } from "./lib/cli.js";

const args = parseArgs();

// Stale IDs from seed.js that assign.js never overwrote
const staleProjects = ["proj1", "proj2", "proj3", "proj4", "proj5"];
const staleJudges = ["judge2"]; // seed.js test judge

const plan = new ChangePlan("cleanup stale seed docs");

for (const id of staleProjects) {
  const snap = await db.collection("projects").doc(id).get();
  if (snap.exists) plan.delete(`projects/${id}`);
  else console.log(`  (already absent) projects/${id}`);
}
for (const id of staleJudges) {
  const snap = await db.collection("judges").doc(id).get();
  if (snap.exists) plan.delete(`judges/${id}`);
  else console.log(`  (already absent) judges/${id}`);
}

if (!(await gate(args, plan))) process.exit(0);

for (const { path: docPath } of plan.deletes) {
  const [collection, id] = docPath.split("/");
  await db.collection(collection).doc(id).delete();
  console.log(`deleted ${docPath}`);
}

console.log("Done ✅");
process.exit(0);
