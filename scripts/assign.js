/**
 * assign.js — DataHacks judge assignment pipeline
 *
 * Usage:
 *   node scripts/assign.js <judges_csv_path> <submissions_csv_path>
 *
 * Example:
 *   node scripts/assign.js \
 *     "src/assets/Mentors_Judges Calendar & Sign-Up - 4_19 Judge.csv" \
 *     "src/assets/datahacks_2026_synthetic_submissions.csv"
 *
 * What it does:
 *   1. Parses judges CSV  →  { name, email, track }
 *   2. Parses Devpost submissions CSV  →  { id, name, tracks, tableNumber, ... }
 *   3. Assigns judges to projects by matching primary track, round-robin
 *   4. Uploads /projects and /judges collections to Firestore
 *
 * Judge document IDs are email-slugs (e.g. "john-doe-gmail-com").
 * Dashboard falls back to an email query so judges are found when they log in.
 */

import { initializeApp } from "firebase/app";
import { getFirestore, doc, setDoc } from "firebase/firestore";
import { readFileSync } from "fs";

// ── Firebase config (same project as seed.js) ──────────────────────────────
const firebaseConfig = {
  apiKey: "AIzaSyCGu1nmbD7arFk6E7j4TGZRSb5mau1Uv-A",
  authDomain: "dh-judge-platform.firebaseapp.com",
  projectId: "dh-judge-platform",
};

// ── Tunable knobs ───────────────────────────────────────────────────────────
const JUDGES_PER_PROJECT = 3; // target number of judges per project

// ── Helpers ─────────────────────────────────────────────────────────────────
function parseCSV(text) {
  const lines = text.split(/\r?\n/);
  const parseRow = (line) => {
    const cols = [];
    let cur = "";
    let inQ = false;
    for (let i = 0; i < line.length; i++) {
      const ch = line[i];
      if (ch === '"') {
        if (inQ && line[i + 1] === '"') { cur += '"'; i++; }
        else inQ = !inQ;
      } else if (ch === "," && !inQ) {
        cols.push(cur.trim());
        cur = "";
      } else {
        cur += ch;
      }
    }
    cols.push(cur.trim());
    return cols;
  };
  const headers = parseRow(lines[0]).map((h) => h.replace(/^"|"$/g, ""));
  const rows = [];
  for (let i = 1; i < lines.length; i++) {
    if (!lines[i].trim()) continue;
    const cols = parseRow(lines[i]);
    const obj = {};
    headers.forEach((h, j) => { obj[h] = (cols[j] || "").replace(/^"|"$/g, "").trim(); });
    rows.push(obj);
  }
  return rows;
}

function emailToId(email) {
  return email.toLowerCase().replace(/[^a-z0-9]/g, "-").replace(/-+/g, "-").replace(/^-|-$/g, "");
}

function titleToId(title) {
  return title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40);
}

// ── Parse judges CSV ─────────────────────────────────────────────────────────
function parseJudges(path) {
  const text = readFileSync(path, "utf8");
  const rows = parseCSV(text);
  const judges = [];
  for (const row of rows) {
    const name = row["Name"];
    const track = row["Tracks"];
    // Skip summary rows (no name or track, or track looks like a number)
    if (!name || !track || /^\d+$/.test(track)) continue;
    // Email column may be absent — derive a placeholder from name
    const email = row["Email"] || `${name.toLowerCase().replace(/\s+/g, ".")}@judge.datahacks`;
    judges.push({ name: name.trim(), email, track: track.trim(), company: row["Company"] || "", role: row["Role"] || "" });
  }
  return judges;
}

// ── Parse Devpost submissions CSV ────────────────────────────────────────────
function parseProjects(path) {
  const text = readFileSync(path, "utf8");
  const rows = parseCSV(text);
  const projects = [];
  for (const row of rows) {
    const title = row["Project Title"];
    if (!title) continue;
    if (row["Project Status"] === "Draft") continue;
    const tracks = [
      row["What's The First Track You'd Like To Submit To?"],
      row["What's The Second Track You'd Like To Submit To?"],
    ].map((t) => t?.trim()).filter(Boolean);
    const tableRaw = row["Table Number"];
    const tableNumber = tableRaw ? parseInt(tableRaw, 10) : null;
    projects.push({
      id: titleToId(title),
      name: title,
      tracks,
      tableNumber: isNaN(tableNumber) ? null : tableNumber,
      submissionUrl: row["Submission Url"] || "",
      description: row["About The Project"] || "",
      builtWith: row["Built With"] || "",
      githubUrl: row["Project Github Repository Url"] || "",
      videoUrl: row["Video Demo Link"] || "",
      sponsorChallenges: row["Which Sponsor Challenges Are You Submitting To?"] || "",
      optInPrizes: row["Opt-In Prizes"] || "",
      teamUniversities: row["Team Colleges/Universities"] || "",
    });
  }
  return projects;
}

// ── Assignment algorithm ──────────────────────────────────────────────────────
// Groups projects by primary track. For each track, round-robin assigns
// JUDGES_PER_PROJECT judges to each project from the matching judge pool.
// Also records which projects each judge owns (assignedProjects on judge doc).
function assignJudges(judges, projects) {
  // Build track → judge list map
  const judgesByTrack = {};
  for (const j of judges) {
    const t = j.track;
    if (!judgesByTrack[t]) judgesByTrack[t] = [];
    judgesByTrack[t].push(j);
  }

  // Build track → project list map (by primary track)
  const projectsByTrack = {};
  for (const p of projects) {
    const t = p.tracks[0];
    if (!t) continue;
    if (!projectsByTrack[t]) projectsByTrack[t] = [];
    projectsByTrack[t].push(p);
  }

  // assignedProjects per judge
  const judgeProjects = {}; // judgeId → Set<projectId>
  // assignedJudges per project
  const projectJudges = {}; // projectId → judgeId[]

  const tracks = new Set([...Object.keys(judgesByTrack), ...Object.keys(projectsByTrack)]);

  for (const track of tracks) {
    const jList = judgesByTrack[track] || [];
    const pList = projectsByTrack[track] || [];

    if (!jList.length || !pList.length) {
      if (pList.length) console.warn(`  ⚠  No judges for track "${track}" (${pList.length} projects unassigned)`);
      if (jList.length && !pList.length) console.log(`  ·  No projects for track "${track}" — judges idle`);
      continue;
    }

    console.log(`  ${track}: ${jList.length} judges → ${pList.length} projects`);

    // Round-robin: iterate through judges repeatedly to fill JUDGES_PER_PROJECT slots per project
    let jIdx = 0;
    for (const p of pList) {
      projectJudges[p.id] = [];
      let assigned = 0;
      let attempts = 0;
      while (assigned < JUDGES_PER_PROJECT && attempts < jList.length * 2) {
        const judge = jList[jIdx % jList.length];
        const jid = emailToId(judge.email);
        jIdx++;
        attempts++;
        if (projectJudges[p.id].includes(jid)) continue; // already assigned this judge
        projectJudges[p.id].push(jid);
        if (!judgeProjects[jid]) judgeProjects[jid] = new Set();
        judgeProjects[jid].add(p.id);
        assigned++;
      }
    }
  }

  return { judgeProjects, projectJudges };
}

// ── Main ──────────────────────────────────────────────────────────────────────
async function main() {
  const [,, judgesPath, submissionsPath] = process.argv;
  if (!judgesPath || !submissionsPath) {
    console.error("Usage: node scripts/assign.js <judges_csv> <submissions_csv>");
    process.exit(1);
  }

  console.log("Parsing CSVs…");
  const judges = parseJudges(judgesPath);
  const projects = parseProjects(submissionsPath);
  console.log(`  ${judges.length} judges, ${projects.length} projects`);

  console.log("\nAssigning by track…");
  const { judgeProjects, projectJudges } = assignJudges(judges, projects);

  const app = initializeApp(firebaseConfig);
  const db = getFirestore(app);

  console.log("\nUploading projects…");
  for (const p of projects) {
    const { id, ...data } = p;
    await setDoc(doc(db, "projects", id), {
      ...data,
      assignedJudges: projectJudges[id] || [],
    });
    process.stdout.write(".");
  }

  console.log("\n\nUploading judges…");
  for (const j of judges) {
    const jid = emailToId(j.email);
    await setDoc(doc(db, "judges", jid), {
      name: j.name,
      email: j.email,
      track: j.track,
      company: j.company,
      role: j.role,
      assignedProjects: Array.from(judgeProjects[jid] || []),
    });
    process.stdout.write(".");
  }

  console.log("\n\nDone ✅");
  console.log(`  ${projects.length} projects uploaded`);
  console.log(`  ${judges.length} judges uploaded`);
  const assigned = judges.filter((j) => (judgeProjects[emailToId(j.email)]?.size || 0) > 0).length;
  console.log(`  ${assigned} judges have ≥1 project assigned`);
}

main().catch((err) => { console.error(err); process.exit(1); });
