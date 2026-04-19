/**
 * generate-and-assign.js
 *
 * 1. Generates ~200 synthetic projects proportional to judge track sizes
 * 2. Reads judges CSV
 * 3. Splits judges into pre-defined teams (per judge_dist.csv)
 * 4. Splits each track's projects into equal slices — one slice per team
 * 5. Each judge in a team gets all projects in their team's slice
 * 6. Uploads projects + judges to Firestore
 *
 * Usage: node scripts/generate-and-assign.js <judges_csv_path>
 */

import { initializeApp } from "firebase/app";
import { getFirestore, doc, setDoc } from "firebase/firestore";
import { readFileSync, writeFileSync } from "fs";

const firebaseConfig = {
  apiKey: "AIzaSyCGu1nmbD7arFk6E7j4TGZRSb5mau1Uv-A",
  authDomain: "dh-judge-platform.firebaseapp.com",
  projectId: "dh-judge-platform",
};

// ── Team structure from judge_dist.csv ──────────────────────────────────────
// Each entry: how many teams and how many judges per team
const TRACK_TEAMS = {
  "AI/ML":                             [{ size: 4 }, { size: 4 }, { size: 4 }, { size: 4 }, { size: 4 }, { size: 4 }, { size: 4 }, { size: 4 }], // 8 × 4
  "Entrepreneurship & Product":        [{ size: 4 }, { size: 4 }, { size: 4 }, { size: 4 }],                                                     // 4 × 4
  "Analytics":                         [{ size: 4 }, { size: 4 }, { size: 4 }],                                                                   // 3 × 4
  "Cloud":                             [{ size: 3 }, { size: 3 }, { size: 3 }, { size: 3 }],                                                      // 4 × 3
  "UI/UX & Web Dev":                   [{ size: 2 }, { size: 2 }, { size: 2 }, { size: 2 }, { size: 2 }],                                        // 5 × 2
  "Mechanical Design & Biotechnology": [{ size: 3 }, { size: 4 }],                                                                               // 1×3 + 1×4
  "Hardware & IoT":                    [{ size: 2 }, { size: 2 }],                                                                               // 2 × 2 (1 judge leftover goes to first team)
  "Economics":                         [{ size: 1 }, { size: 1 }, { size: 1 }],                                                                   // 3 × 1
};

// Projects per track — proportional to judge count, totalling ~200
const TRACK_PROJECT_COUNTS = {
  "AI/ML":                             66,
  "Entrepreneurship & Product":        34,
  "Analytics":                         26,
  "Cloud":                             26,
  "UI/UX & Web Dev":                   22,
  "Mechanical Design & Biotechnology": 10,
  "Hardware & IoT":                    10,
  "Economics":                         6,
};
const ALL_TRACK_COUNTS = { ...TRACK_PROJECT_COUNTS };

// ── Project name generators ──────────────────────────────────────────────────
const WORDS_A = ["Neural","Smart","Eco","Vision","Edge","Rapid","Deep","Open","Cloud","Auto","Bio","Flux","Pulse","Nexus","Hyper","Clarity","Logic","Orbit","Apex","Nova","Synth","Geo","Meta","Nano","Quant","Helix","Velo","Prism","Surge","Arc","Flow","Lens","Core","Shift","Beam","Link","Trace","Wave","Mesh","Grid"];
const WORDS_B = ["Scan","Forge","Watch","Track","Pilot","Sense","Cast","Map","Sim","Lab","Net","Route","Chain","Mind","Base","Lens","Hub","Sync","Stream","Bot","Guard","Vault","Sight","Dash","Kit","Works","Stack","Desk","Feed","Aid","Code","Cure","Crew","Shop","Mark","Rank","Span","View","Lift","Dial"];

const used = new Set();
function uniqueName() {
  let name;
  let tries = 0;
  do {
    const a = WORDS_A[Math.floor(Math.random() * WORDS_A.length)];
    const b = WORDS_B[Math.floor(Math.random() * WORDS_B.length)];
    name = a + b;
    tries++;
  } while (used.has(name) && tries < 500);
  used.add(name);
  return name;
}

const BUILT_WITH_BY_TRACK = {
  "AI/ML":                             ["python,pytorch,transformers,fastapi","python,langchain,openai,pinecone","tensorflow,keras,flask,react","yolov8,opencv,streamlit","python,scikit-learn,pandas,dash"],
  "Analytics":                         ["tableau,sql,bigquery,dbt","r,shiny,ggplot2,postgres","python,pandas,plotly,streamlit","looker,dbt,snowflake","excel,python,seaborn,flask"],
  "Cloud":                             ["aws,lambda,dynamodb,terraform","gcp,kubernetes,docker,go","azure,functions,cosmosdb,react","pulumi,aws,fargate,next.js","serverless,s3,cloudfront,react"],
  "Entrepreneurship & Product":        ["nextjs,supabase,stripe,tailwind","typescript,prisma,postgres,trpc","react,firebase,stripe,vercel","flutter,dart,firebase","vue,node,postgres,heroku"],
  "UI/UX & Web Dev":                   ["react,tailwind,framer-motion,nextjs","vue,three.js,gsap,vite","svelte,d3,css,vercel","figma,react,storybook,chromatic","webgl,glsl,threejs,webpack"],
  "Mechanical Design & Biotechnology": ["solidworks,raspberrypi,opencv,c++","arduino,python,mqtt,ros","fusion360,esp32,micropython","labview,matlab,altium","kicad,freecad,python,opencv"],
  "Hardware & IoT":                    ["arduino,c++,esp32,firebase","raspberrypi,python,mqtt,react","zigbee,home-assistant,python","stm32,freertos,c","lorawan,ttn,python,grafana"],
  "Economics":                         ["python,pandas,quantlib,plotly","r,tidyverse,ggplot2,shiny","julia,dataframes,makie","stata,python,scipy","matlab,python,dash,statsmodels"],
};

const SECOND_TRACKS = {
  "AI/ML":                             ["Analytics","Cloud","UI/UX & Web Dev"],
  "Analytics":                         ["AI/ML","Cloud","Economics"],
  "Cloud":                             ["AI/ML","Economics","Analytics"],
  "Entrepreneurship & Product":        ["AI/ML","UI/UX & Web Dev","Analytics"],
  "UI/UX & Web Dev":                   ["Entrepreneurship & Product","AI/ML"],
  "Mechanical Design & Biotechnology": ["Hardware & IoT","AI/ML"],
  "Hardware & IoT":                    ["Mechanical Design & Biotechnology","Cloud"],
  "Economics":                         ["Analytics","Cloud"],
};

function randomItem(arr) { return arr[Math.floor(Math.random() * arr.length)]; }

function generateProjects() {
  const projects = [];
  let tableNum = 1;

  for (const [track, count] of Object.entries(ALL_TRACK_COUNTS)) {
    const secondOptions = SECOND_TRACKS[track] || [];
    const builtOptions = BUILT_WITH_BY_TRACK[track] || ["python,react"];

    for (let i = 0; i < count; i++) {
      const name = uniqueName();
      const id = name.toLowerCase();
      const hasSecond = secondOptions.length > 0 && Math.random() < 0.55;
      const tracks = hasSecond ? [track, randomItem(secondOptions)] : [track];

      projects.push({
        id,
        name,
        tracks,
        tableNumber: tableNum++,
        builtWith: randomItem(builtOptions),
        submissionUrl: `https://datahacks-2026.devpost.com/submissions/${500000 + tableNum}-${id}`,
        description: `A ${track.toLowerCase()} project submitted to DataHacks 2026.`,
      });
    }
  }
  return projects;
}

// ── CSV parser ───────────────────────────────────────────────────────────────
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

function emailToId(name) {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

// ── Group-based assignment ───────────────────────────────────────────────────
function assignGroups(judges, projects) {
  // Group judges by track
  const judgesByTrack = {};
  for (const j of judges) {
    const t = j["Tracks"];
    if (!t) continue;
    if (!judgesByTrack[t]) judgesByTrack[t] = [];
    judgesByTrack[t].push(j);
  }

  // Group projects by primary track
  const projectsByTrack = {};
  for (const p of projects) {
    const t = p.tracks[0];
    if (!projectsByTrack[t]) projectsByTrack[t] = [];
    projectsByTrack[t].push(p);
  }

  const judgeAssignments = {}; // judgeId → Set<projectId>
  const projectAssignments = {}; // projectId → judgeId[]

  for (const [track, teamDefs] of Object.entries(TRACK_TEAMS)) {
    const trackJudges = judgesByTrack[track] || [];
    const trackProjects = projectsByTrack[track] || [];
    if (!trackJudges.length || !trackProjects.length) continue;

    const numTeams = teamDefs.length;

    // Form teams — fill each team slot in order
    const teams = teamDefs.map(() => []);
    let jIdx = 0;
    for (let t = 0; t < teamDefs.length; t++) {
      for (let s = 0; s < teamDefs[t].size && jIdx < trackJudges.length; s++, jIdx++) {
        teams[t].push(trackJudges[jIdx]);
      }
    }
    // Any leftover judges go into the last team
    while (jIdx < trackJudges.length) {
      teams[numTeams - 1].push(trackJudges[jIdx++]);
    }

    // Split projects into equal slices — one per team
    const slice = Math.ceil(trackProjects.length / numTeams);
    for (let t = 0; t < numTeams; t++) {
      const teamProjects = trackProjects.slice(t * slice, (t + 1) * slice);
      for (const judge of teams[t]) {
        const jid = emailToId(judge["Name"]);
        if (!judgeAssignments[jid]) judgeAssignments[jid] = new Set();
        for (const p of teamProjects) {
          judgeAssignments[jid].add(p.id);
          if (!projectAssignments[p.id]) projectAssignments[p.id] = [];
          if (!projectAssignments[p.id].includes(jid)) projectAssignments[p.id].push(jid);
        }
      }
    }

    console.log(`  ${track}: ${trackJudges.length} judges → ${numTeams} teams → ${trackProjects.length} projects (${slice} per team)`);
  }

  return { judgeAssignments, projectAssignments };
}

// ── Main ─────────────────────────────────────────────────────────────────────
async function main() {
  const [,, judgesPath] = process.argv;
  if (!judgesPath) {
    console.error("Usage: node scripts/generate-and-assign.js <judges_csv_path>");
    process.exit(1);
  }

  console.log("Generating projects…");
  const projects = generateProjects();
  console.log(`  ${projects.length} projects across ${Object.keys(ALL_TRACK_COUNTS).length} tracks`);

  console.log("\nParsing judges CSV…");
  const rows = parseCSV(readFileSync(judgesPath, "utf8"));
  const judges = rows.filter(r => r["Name"]?.trim() && r["Tracks"]?.trim() && !/^\d+$/.test(r["Tracks"]));
  console.log(`  ${judges.length} judges`);

  console.log("\nForming teams and assigning projects…");
  const { judgeAssignments, projectAssignments } = assignGroups(judges, projects);

  const app = initializeApp(firebaseConfig);
  const db = getFirestore(app);

  // Save projects to CSV
  const csvHeader = "Project Title,Table Number,What's The First Track You'd Like To Submit To?,What's The Second Track You'd Like To Submit To?,Built With,Submission Url,About The Project";
  const csvRows = projects.map(p =>
    [p.name, p.tableNumber, p.tracks[0] || "", p.tracks[1] || "", p.builtWith, p.submissionUrl, p.description]
      .map(v => `"${String(v).replace(/"/g, '""')}"`)
      .join(",")
  );
  const csvPath = "src/assets/synthetic_projects_generated.csv";
  writeFileSync(csvPath, [csvHeader, ...csvRows].join("\n"), "utf8");
  console.log(`\nSaved CSV → ${csvPath}`);

  console.log("\nUploading projects…");
  for (const p of projects) {
    const { id, ...data } = p;
    await setDoc(doc(db, "projects", id), { ...data, assignedJudges: projectAssignments[id] || [] });
    process.stdout.write(".");
  }

  console.log("\n\nUploading judges…");
  for (const j of judges) {
    const jid = emailToId(j["Name"]);
    await setDoc(doc(db, "judges", jid), {
      name: j["Name"].trim(),
      email: j["Email"] || `${jid}@judge.datahacks`,
      track: j["Tracks"].trim(),
      company: j["Company"] || "",
      role: j["Role"] || "",
      assignedProjects: Array.from(judgeAssignments[jid] || []),
    });
    process.stdout.write(".");
  }

  console.log("\n\nDone ✅");
  console.log(`  ${projects.length} projects | ${judges.length} judges`);
  const assigned = judges.filter(j => (judgeAssignments[emailToId(j["Name"])]?.size || 0) > 0).length;
  console.log(`  ${assigned}/${judges.length} judges have projects assigned`);
}

main().catch(err => { console.error(err); process.exit(1); });
