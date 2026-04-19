import { readFileSync, writeFileSync } from "fs";

// ── PARSE CSVs ────────────────────────────────────────────────
function parseCSV(path) {
  const lines = readFileSync(path, "utf8").trim().replace(/\r/g, "").split("\n");
  // Parse each field: quoted or unquoted
  function parseLine(line) {
    const cols = [];
    let i = 0;
    while (i < line.length) {
      if (line[i] === '"') {
        const end = line.indexOf('"', i + 1);
        cols.push(line.slice(i + 1, end));
        i = end + 2;
      } else {
        const end = line.indexOf(',', i);
        cols.push(end === -1 ? line.slice(i) : line.slice(i, end));
        i = end === -1 ? line.length : end + 1;
      }
    }
    return cols;
  }
  const headers = parseLine(lines[0]);
  return lines.slice(1).map(line => {
    const cols = parseLine(line);
    return Object.fromEntries(headers.map((h, i) => [h, (cols[i] ?? "").trim()]));
  });
}

const TRACK_MAP = {
  "Machine Learning & Bio-AI":                            "AI/ML",
  "Data Analytics":                                       "Analytics",
  "Cloud Development":                                    "Cloud",
  "Entrepreneurship & Product Management":                "Entrepreneurship & Product",
  "UI/UX Design & Web Development":                       "UI/UX & Web Dev",
  "Hardware & IoT":                                       "Hardware & IoT",
  "Mechanical Design & Biotechnology":                    "Mechanical Design & Biotechnology",
  "Economics":                                            "Economics",
  // malformed row — two tracks merged into one cell
  "Entrepreneurship & Product Management Hardware & IoT": "Entrepreneurship & Product",
};

const projectRows = parseCSV("src/assets/Final_project_info - Sheet1.csv");
const judgeRows   = parseCSV("src/assets/judge_logins.csv");

// ── BUILD PROJECTS ────────────────────────────────────────────
const projects = projectRows.map(r => ({
  id: r["Project Title"].toLowerCase().replace(/[^a-z0-9]/g, "-"),
  name: r["Project Title"],
  tableNumber: parseInt(r["Table Number"], 10),
  tracks: [
    r["What's The First Track You'd Like To Submit To?"],
    r["What's The Second Track You'd Like To Submit To?"]
  ].filter(Boolean).map(t => TRACK_MAP[t] ?? t),
}));

// ── BUILD JUDGES ──────────────────────────────────────────────
const judges = judgeRows.map(r => ({
  id: r["Username"],
  name: r["Name"],
  email: `${r["Username"].toLowerCase()}@datahacks2026.ucsd`,
  track: r["Track"],
  assignedProjects: [],
}));

// ── RANDOMIZE ASSIGNMENTS ─────────────────────────────────────
function shuffle(arr) {
  const a = [...arr];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

// Group judges by track
const judgesByTrack = {};
for (const j of judges) {
  if (!judgesByTrack[j.track]) judgesByTrack[j.track] = [];
  judgesByTrack[j.track].push(j);
}

// Round-robin assign track projects to judges in that track
for (const [track, trackJudges] of Object.entries(judgesByTrack)) {
  const trackProjectIds = shuffle(
    projects.filter(p => p.tracks.includes(track)).map(p => p.id)
  );
  trackProjectIds.forEach((pid, i) => {
    trackJudges[i % trackJudges.length].assignedProjects.push(pid);
  });
}

// ── WRITE OUTPUT ──────────────────────────────────────────────
const output = { projects, judges };
writeFileSync("assignments.local.json", JSON.stringify(output, null, 2));

console.log(`✅ assignments.local.json written`);
console.log(`   ${projects.length} projects, ${judges.length} judges`);
for (const [track, tj] of Object.entries(judgesByTrack)) {
  const total = projects.filter(p => p.tracks.includes(track)).length;
  console.log(`   ${track}: ${tj.length} judges, ${total} projects`);
}
