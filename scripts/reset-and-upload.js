/**
 * reset-and-upload.js
 * Wipes projects/judges/evaluations, then re-uploads from saved CSVs.
 *
 * Usage:
 *   node scripts/reset-and-upload.js <judges_csv> [--commit] [--yes]
 *
 * DRY RUN BY DEFAULT — pass --commit to actually wipe and upload.
 *
 * ⚠️  This deletes the `evaluations` collection. Those are the scores judges
 *     entered during the event and there is no undo. Read the printed summary.
 *
 * Projects are read from src/assets/synthetic_projects_generated.csv
 */

import { readFileSync } from "fs";
import { db } from "./lib/admin.js";
import { ChangePlan, confirm, parseArgs } from "./lib/cli.js";

const TRACK_TEAMS = {
  "AI/ML":                             [4,4,4,4,4,4,4,4],
  "Entrepreneurship & Product":        [4,4,4,4],
  "Analytics":                         [4,4,4],
  "Cloud":                             [3,3,3,3],
  "UI/UX & Web Dev":                   [2,2,2,2,2],
  "Mechanical Design & Biotechnology": [3,4],
  "Hardware & IoT":                    [2,2],
  "Economics":                         [1,1,1],
};

// ── Helpers ──────────────────────────────────────────────────────────────────
function parseCSV(text) {
  const lines = text.split(/\r?\n/);
  const parseRow = (line) => {
    const cols = []; let cur = ""; let inQ = false;
    for (let i = 0; i < line.length; i++) {
      const ch = line[i];
      if (ch === '"') { if (inQ && line[i+1] === '"') { cur += '"'; i++; } else inQ = !inQ; }
      else if (ch === ',' && !inQ) { cols.push(cur.trim()); cur = ""; }
      else cur += ch;
    }
    cols.push(cur.trim());
    return cols;
  };
  const headers = parseRow(lines[0]).map(h => h.replace(/^"|"$/g, ""));
  return lines.slice(1).filter(l => l.trim()).map(l => {
    const cols = parseRow(l);
    const obj = {};
    headers.forEach((h, j) => { obj[h] = (cols[j] || "").replace(/^"|"$/g, "").trim(); });
    return obj;
  });
}

function toSlug(str) {
  return str.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

async function countCollection(name) {
  const snap = await db.collection(name).get();
  return snap.docs.map(d => d.id);
}

async function deleteCollection(name) {
  const snap = await db.collection(name).get();
  // Admin SDK batches cap at 500 writes.
  let deleted = 0;
  for (let i = 0; i < snap.docs.length; i += 400) {
    const batch = db.batch();
    for (const d of snap.docs.slice(i, i + 400)) batch.delete(d.ref);
    await batch.commit();
    deleted += Math.min(400, snap.docs.length - i);
  }
  console.log(`  deleted ${deleted} docs from ${name}`);
}

// ── Assignment ────────────────────────────────────────────────────────────────
function assignGroups(judges, projects) {
  const judgesByTrack = {};
  for (const j of judges) {
    const t = j["Tracks"]?.trim();
    if (!t) continue;
    if (!judgesByTrack[t]) judgesByTrack[t] = [];
    judgesByTrack[t].push(j);
  }

  const projectsByTrack = {};
  for (const p of projects) {
    const t = p.tracks[0];
    if (!t) continue;
    if (!projectsByTrack[t]) projectsByTrack[t] = [];
    projectsByTrack[t].push(p);
  }

  const judgeProjects = {};  // slug → Set<projectId>
  const projectJudges = {};  // projectId → slug[]

  for (const [track, teamSizes] of Object.entries(TRACK_TEAMS)) {
    const jList = judgesByTrack[track] || [];
    const pList = projectsByTrack[track] || [];
    if (!jList.length || !pList.length) continue;

    // Form teams in order
    const teams = [];
    let ji = 0;
    for (const size of teamSizes) {
      const team = [];
      for (let s = 0; s < size && ji < jList.length; s++, ji++) team.push(jList[ji]);
      teams.push(team);
    }
    while (ji < jList.length) teams[teams.length - 1].push(jList[ji++]);

    // Slice projects evenly across teams
    const slice = Math.ceil(pList.length / teams.length);
    teams.forEach((team, t) => {
      const slice_projects = pList.slice(t * slice, (t + 1) * slice);
      for (const judge of team) {
        const slug = toSlug(judge["Name"]);
        if (!judgeProjects[slug]) judgeProjects[slug] = new Set();
        for (const p of slice_projects) {
          judgeProjects[slug].add(p.id);
          if (!projectJudges[p.id]) projectJudges[p.id] = [];
          if (!projectJudges[p.id].includes(slug)) projectJudges[p.id].push(slug);
        }
      }
    });

    console.log(`  ${track}: ${jList.length} judges → ${teams.length} teams → ${pList.length} projects (~${slice}/team)`);
  }

  return { judgeProjects, projectJudges };
}

// ── Main ──────────────────────────────────────────────────────────────────────
const args = parseArgs();
const judgesPath = args.positionals[0];
if (!judgesPath) {
  console.error("Usage: node scripts/reset-and-upload.js <judges_csv_path> [--commit] [--yes]");
  process.exit(1);
}

// 1. Load projects from saved CSV
console.log("\nLoading projects from synthetic_projects_generated.csv…");
const projectRows = parseCSV(readFileSync("src/assets/synthetic_projects_generated.csv", "utf8"));
const projects = projectRows.map(r => ({
  id: toSlug(r["Project Title"]),
  name: r["Project Title"],
  tracks: [r["What's The First Track You'd Like To Submit To?"], r["What's The Second Track You'd Like To Submit To?"]].filter(Boolean),
  tableNumber: parseInt(r["Table Number"]) || null,
  builtWith: r["Built With"] || "",
  submissionUrl: r["Submission Url"] || "",
  description: r["About The Project"] || "",
}));
console.log(`  ${projects.length} projects loaded`);

// 2. Load judges from CSV
console.log("\nLoading judges from CSV…");
const judgeRows = parseCSV(readFileSync(judgesPath, "utf8"))
  .filter(r => r["Name"]?.trim() && r["Tracks"]?.trim() && !/^\d+$/.test(r["Tracks"]));
console.log(`  ${judgeRows.length} judges loaded`);

// 3. Assign
console.log("\nForming teams…");
const { judgeProjects, projectJudges } = assignGroups(judgeRows, projects);

// 4. Plan (what the wipe + upload would do)
const plan = new ChangePlan("reset-and-upload");
for (const name of ["projects", "judges", "evaluations"]) {
  const ids = await countCollection(name);
  for (const id of ids) plan.delete(`${name}/${id}`);
}
for (const p of projects) plan.create(`projects/${p.id}`, `— ${p.name}`);
for (const j of judgeRows) {
  const slug = toSlug(j["Name"]);
  plan.create(`judges/${slug}`, `— ${j["Name"].trim()} (${(judgeProjects[slug] || new Set()).size} projects)`);
}

plan.printSummary();

const evalCount = plan.deletes.filter(d => d.path.startsWith("evaluations/")).length;
if (evalCount) {
  console.log(
    `⚠️  ${evalCount} evaluation rows will be PERMANENTLY DELETED. These are judge-entered scores.\n`
  );
}

if (!args.commit) {
  console.log(
    `DRY RUN — nothing was written. Re-run with --commit to apply these ${plan.total} changes.\n`
  );
  process.exit(0);
}

if (!args.yes) {
  const ok = await confirm(
    `About to DELETE ${plan.deletes.length} docs (including ${evalCount} evaluations) and write ${plan.creates.length} to production Firestore. Continue?`,
    "delete"
  );
  if (!ok) {
    console.log("Aborted. Nothing was written.\n");
    process.exit(0);
  }
}

// 5. Wipe Firestore
console.log("\nClearing Firestore…");
await deleteCollection("projects");
await deleteCollection("judges");
await deleteCollection("evaluations");

// 6. Upload projects
console.log("\nUploading projects…");
for (const p of projects) {
  const { id, ...data } = p;
  await db.collection("projects").doc(id).set({ ...data, assignedJudges: projectJudges[id] || [] });
  process.stdout.write(".");
}

// 7. Upload judges
console.log("\n\nUploading judges…");
for (const j of judgeRows) {
  const slug = toSlug(j["Name"]);
  await db.collection("judges").doc(slug).set({
    name: j["Name"].trim(),
    email: j["Email"] || `${slug}@judge.datahacks`,
    track: j["Tracks"].trim(),
    company: j["Company"] || "",
    role: j["Role"] || "",
    assignedProjects: Array.from(judgeProjects[slug] || []),
  });
  process.stdout.write(".");
}

console.log("\n\nDone ✅");
console.log(`  ${projects.length} projects | ${judgeRows.length} judges uploaded`);
process.exit(0);
