/**
 * reset-and-upload.js
 * Wipes projects/judges/evaluations, then re-uploads from saved CSVs.
 *
 * Usage:
 *   node scripts/reset-and-upload.js <judges_csv>
 *
 * Projects are read from src/assets/synthetic_projects_generated.csv
 */

import { initializeApp } from "firebase/app";
import { getFirestore, collection, getDocs, deleteDoc, doc, setDoc } from "firebase/firestore";
import { readFileSync } from "fs";

const firebaseConfig = {
  apiKey: "AIzaSyCGu1nmbD7arFk6E7j4TGZRSb5mau1Uv-A",
  authDomain: "dh-judge-platform.firebaseapp.com",
  projectId: "dh-judge-platform",
};

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

async function deleteCollection(db, name) {
  const snap = await getDocs(collection(db, name));
  await Promise.all(snap.docs.map(d => deleteDoc(doc(db, name, d.id))));
  console.log(`  deleted ${snap.docs.length} docs from ${name}`);
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
const [,, judgesPath] = process.argv;
if (!judgesPath) {
  console.error("Usage: node scripts/reset-and-upload.js <judges_csv_path>");
  process.exit(1);
}

const app = initializeApp(firebaseConfig);
const db = getFirestore(app);

// 1. Wipe Firestore
console.log("\nClearing Firestore…");
await deleteCollection(db, "projects");
await deleteCollection(db, "judges");
await deleteCollection(db, "evaluations");

// 2. Load projects from saved CSV
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

// 3. Load judges from CSV
console.log("\nLoading judges from CSV…");
const judgeRows = parseCSV(readFileSync(judgesPath, "utf8"))
  .filter(r => r["Name"]?.trim() && r["Tracks"]?.trim() && !/^\d+$/.test(r["Tracks"]));
console.log(`  ${judgeRows.length} judges loaded`);

// 4. Assign
console.log("\nForming teams…");
const { judgeProjects, projectJudges } = assignGroups(judgeRows, projects);

// 5. Upload projects
console.log("\nUploading projects…");
for (const p of projects) {
  const { id, ...data } = p;
  await setDoc(doc(db, "projects", id), { ...data, assignedJudges: projectJudges[id] || [] });
  process.stdout.write(".");
}

// 6. Upload judges
console.log("\n\nUploading judges…");
for (const j of judgeRows) {
  const slug = toSlug(j["Name"]);
  await setDoc(doc(db, "judges", slug), {
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
