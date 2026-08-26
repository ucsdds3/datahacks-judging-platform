/**
 * seed.js — upload projects + judges from assignments.local.json.
 *
 * Usage:
 *   node seed.js [assignments.local.json] [--commit] [--yes]
 *
 * DRY RUN BY DEFAULT — pass --commit to actually write.
 */

import { readFileSync, existsSync } from "fs";
import path from "path";
import { db } from "./scripts/lib/admin.js";
import { ChangePlan, FLAGS_HELP, gate, parseArgs } from "./scripts/lib/cli.js";

const args = parseArgs();
const inputPath = path.resolve(process.cwd(), args.positionals[0] || "assignments.local.json");

if (!existsSync(inputPath)) {
  console.error(`\n[seed] Could not find ${inputPath}${FLAGS_HELP}`);
  process.exit(1);
}

const { projects, judges } = JSON.parse(readFileSync(inputPath, "utf8"));

const projectDocs = projects.map((p) => ({
  id: p.id,
  data: { name: p.name, tracks: p.tracks, tableNumber: p.tableNumber },
}));

const judgeDocs = judges.map((j) => ({
  id: j.id,
  data: {
    name: j.name,
    email: j.email,
    track: j.track,
    assignedProjects: j.assignedProjects,
  },
}));

// ── Plan ─────────────────────────────────────────────────────────────────────
const plan = new ChangePlan(`seed from ${path.basename(inputPath)}`);

const existingProjects = new Set((await db.collection("projects").get()).docs.map((d) => d.id));
const existingJudges = new Set((await db.collection("judges").get()).docs.map((d) => d.id));

for (const { id, data } of projectDocs) {
  const where = existingProjects.has(id) ? "update" : "create";
  plan[where](`projects/${id}`, `— ${data.name}`);
}
for (const { id, data } of judgeDocs) {
  const where = existingJudges.has(id) ? "update" : "create";
  plan[where](`judges/${id}`, `— ${data.name} (${(data.assignedProjects || []).length} projects)`);
}

if (!(await gate(args, plan))) process.exit(0);

// ── Commit ───────────────────────────────────────────────────────────────────
console.log(`Uploading ${projectDocs.length} projects...`);
for (const { id, data } of projectDocs) {
  await db.collection("projects").doc(id).set(data);
}

console.log(`Uploading ${judgeDocs.length} judges...`);
for (const { id, data } of judgeDocs) {
  await db.collection("judges").doc(id).set(data);
}

console.log("DONE ✅");
process.exit(0);
