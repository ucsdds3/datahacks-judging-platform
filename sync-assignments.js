/**
 * sync-assignments.js — push judge → project assignments from a local JSON file.
 *
 * Usage:
 *   node sync-assignments.js [assignments.local.json] [--commit] [--yes]
 *
 * DRY RUN BY DEFAULT — pass --commit to actually write.
 */

import fs from "node:fs";
import path from "node:path";
import { db } from "./scripts/lib/admin.js";
import { ChangePlan, gate, parseArgs } from "./scripts/lib/cli.js";

const args = parseArgs();

const inputPath = path.resolve(process.cwd(), args.positionals[0] || "assignments.local.json");

const fail = (message) => {
  console.error(`\n[assignments] ${message}`);
  process.exit(1);
};

if (!fs.existsSync(inputPath)) {
  fail(
    `Could not find ${path.basename(inputPath)}. Copy assignments.template.json to assignments.local.json and edit it first.`
  );
}

const raw = fs.readFileSync(inputPath, "utf8");
const parsed = JSON.parse(raw);

if (!Array.isArray(parsed.judges) || parsed.judges.length === 0) {
  fail("Expected a non-empty judges array in the assignments file.");
}

const judges = parsed.judges.map((judge) => {
  if (!judge.id) {
    fail("Every judge entry needs an id field that matches the Firebase Auth uid.");
  }

  return {
    id: judge.id,
    name: judge.name || "",
    email: judge.email || "",
    track: judge.track || "",
    assignedProjects: Array.isArray(judge.assignedProjects)
      ? [...new Set(judge.assignedProjects.filter(Boolean))]
      : []
  };
});

const judgeIds = new Set();
for (const judge of judges) {
  if (judgeIds.has(judge.id)) {
    fail(`Duplicate judge id found: ${judge.id}`);
  }
  judgeIds.add(judge.id);
}

const assignedJudgesByProject = new Map();
for (const judge of judges) {
  for (const projectId of judge.assignedProjects) {
    if (!assignedJudgesByProject.has(projectId)) {
      assignedJudgesByProject.set(projectId, []);
    }
    assignedJudgesByProject.get(projectId).push(judge.id);
  }
}

const syncAssignments = async () => {
  const projectSnapshot = await db.collection("projects").get();
  const existingProjectIds = new Set(projectSnapshot.docs.map((project) => project.id));

  const unknownProjectIds = [...assignedJudgesByProject.keys()].filter(
    (projectId) => !existingProjectIds.has(projectId)
  );

  if (unknownProjectIds.length > 0) {
    fail(
      `These assigned project ids do not exist in Firestore: ${unknownProjectIds.join(", ")}`
    );
  }

  // ── Plan ───────────────────────────────────────────────────────────────────
  const plan = new ChangePlan(`sync assignments from ${path.basename(inputPath)}`);

  const existingJudgeIds = new Set(
    (await db.collection("judges").get()).docs.map((d) => d.id)
  );

  for (const judge of judges) {
    const op = existingJudgeIds.has(judge.id) ? "update" : "create";
    plan[op](`judges/${judge.id}`, `— ${judge.assignedProjects.length} projects (merge)`);
  }

  for (const projectId of existingProjectIds) {
    const next = assignedJudgesByProject.get(projectId) || [];
    const current = projectSnapshot.docs.find((d) => d.id === projectId)?.data()
      ?.assignedJudges || [];
    // Only an update if the value actually changes.
    const same =
      current.length === next.length && current.every((v, i) => v === next[i]);
    if (!same) {
      plan.update(
        `projects/${projectId}`,
        `— assignedJudges ${current.length} → ${next.length}`
      );
    }
  }

  if (!(await gate(args, plan))) return;

  // ── Commit ─────────────────────────────────────────────────────────────────
  console.log(`[assignments] Syncing ${judges.length} judges from ${path.basename(inputPath)}...`);

  for (const judge of judges) {
    await db.collection("judges").doc(judge.id).set(
      {
        name: judge.name,
        email: judge.email,
        track: judge.track,
        assignedProjects: judge.assignedProjects
      },
      { merge: true }
    );
  }

  console.log(`[assignments] Updating ${existingProjectIds.size} projects with assigned judges...`);

  for (const projectId of existingProjectIds) {
    await db.collection("projects").doc(projectId).set(
      {
        assignedJudges: assignedJudgesByProject.get(projectId) || []
      },
      { merge: true }
    );
  }

  console.log("[assignments] Done.");
};

syncAssignments().catch((error) => {
  console.error("\n[assignments] Sync failed:", error);
  process.exit(1);
});
