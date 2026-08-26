/**
 * create-sample-judges.js — seed a handful of demo projects + judge accounts.
 *
 * Usage: node scripts/create-sample-judges.js [--commit] [--yes]
 *
 * DRY RUN BY DEFAULT — pass --commit to actually write.
 */

import { auth, db } from "./lib/admin.js";
import { ChangePlan, gate, parseArgs } from "./lib/cli.js";

const args = parseArgs();

// ── Projects to seed (from synthetic CSV) ───────────────────────────────────
const projects = [
  // AI/ML
  { id: "medscanai",    name: "MedScanAI",    tracks: ["AI/ML"],                              tableNumber: 2,  submissionUrl: "https://datahacks-2026.devpost.com/submissions/500037-medscanai" },
  { id: "neuralchef",   name: "NeuralChef",   tracks: ["AI/ML"],                              tableNumber: 5,  submissionUrl: "https://datahacks-2026.devpost.com/submissions/500148-neuralchef" },
  { id: "deepdiagnose", name: "DeepDiagnose", tracks: ["AI/ML", "UI/UX & Web Dev"],           tableNumber: 7,  submissionUrl: "https://datahacks-2026.devpost.com/submissions/500222-deepdiagnose" },
  { id: "signspeak",    name: "SignSpeak",     tracks: ["AI/ML"],                              tableNumber: 10, submissionUrl: "https://datahacks-2026.devpost.com/submissions/500333-signspeak" },
  // Analytics (secondary track for judge 1)
  { id: "campuspulse",  name: "CampusPulse",  tracks: ["Analytics", "AI/ML"],                 tableNumber: 3,  submissionUrl: "https://datahacks-2026.devpost.com/submissions/500074-campuspulse" },
  { id: "rentradar",    name: "RentRadar",    tracks: ["Analytics"],                          tableNumber: 14, submissionUrl: "https://datahacks-2026.devpost.com/submissions/500481-rentradar" },
  // Entrepreneurship & Product
  { id: "pitchpair",    name: "PitchPair",    tracks: ["Entrepreneurship & Product", "AI/ML"],tableNumber: 12, submissionUrl: "https://datahacks-2026.devpost.com/submissions/500407-pitchpair" },
  { id: "foundrsdb",    name: "FoundrsDB",    tracks: ["Entrepreneurship & Product", "Analytics"], tableNumber: 15, submissionUrl: "https://datahacks-2026.devpost.com/submissions/500518-foundrsdb" },
  { id: "flowstate",    name: "FlowState",    tracks: ["UI/UX & Web Dev", "Entrepreneurship & Product"], tableNumber: 11, submissionUrl: "https://datahacks-2026.devpost.com/submissions/500370-flowstate" },
  // Hardware & IoT
  { id: "smarthydro",   name: "SmartHydro",   tracks: ["Hardware & IoT", "Cloud"],            tableNumber: 16, submissionUrl: "https://datahacks-2026.devpost.com/submissions/500555-smarthydro" },
  { id: "airsense",     name: "AirSense",     tracks: ["Hardware & IoT", "Mechanical Design & Biotechnology"], tableNumber: 20, submissionUrl: "https://datahacks-2026.devpost.com/submissions/500703-airsense" },
  // Mechanical Design & Biotechnology (secondary for judge 3)
  { id: "bioprintr",    name: "BioPrintr",    tracks: ["Mechanical Design & Biotechnology", "Hardware & IoT"], tableNumber: 4, submissionUrl: "https://datahacks-2026.devpost.com/submissions/500111-bioprintr" },
];

// ── Judge definitions ────────────────────────────────────────────────────────
const judges = [
  {
    // From CSV: Manoj Krishna Mohan, Amazon, SDE2, AI/ML
    email: "manoj.krishnamohan@datahacks.judge",
    password: "manoj1234",
    name: "Manoj Krishna Mohan",
    company: "Amazon",
    role: "SDE2",
    tracks: ["AI/ML", "Analytics"],
    assignedProjects: ["medscanai", "neuralchef", "deepdiagnose", "signspeak", "campuspulse", "rentradar"],
  },
  {
    // From CSV: Mira Shah, Amazon, Sr. Product Manager-Tech, Entrepreneurship & Product
    email: "mira.shah@datahacks.judge",
    password: "mira1234",
    name: "Mira Shah",
    company: "Amazon",
    role: "Sr. Product Manager - Tech",
    tracks: ["Entrepreneurship & Product"],
    assignedProjects: ["pitchpair", "foundrsdb", "flowstate"],
  },
  {
    // From CSV: Sridhar Pavithrapu, Apple, Senior Software Engineer, Hardware & IoT
    email: "sridhar.pavithrapu@datahacks.judge",
    password: "sridhar1234",
    name: "Sridhar Pavithrapu",
    company: "Apple",
    role: "Senior Software Engineer",
    tracks: ["Hardware & IoT", "Mechanical Design & Biotechnology"],
    assignedProjects: ["smarthydro", "airsense", "bioprintr"],
  },
];

// ── Plan ─────────────────────────────────────────────────────────────────────
const plan = new ChangePlan("create sample judges + demo projects");

const existingProjects = new Set((await db.collection("projects").get()).docs.map((d) => d.id));
for (const { id, ...data } of projects) {
  plan[existingProjects.has(id) ? "update" : "create"](`projects/${id}`, `— ${data.name}`);
}

const existingUids = new Map();
for (const judge of judges) {
  try {
    const u = await auth.getUserByEmail(judge.email);
    existingUids.set(judge.email, u.uid);
    const snap = await db.collection("judges").doc(u.uid).get();
    plan[snap.exists ? "update" : "create"](`judges/${u.uid}`, `— ${judge.name}`);
  } catch (err) {
    if (err.code !== "auth/user-not-found") throw err;
    plan.create(`auth/${judge.email}`);
    plan.create(`judges/<new uid>`, `— ${judge.name} (${judge.assignedProjects.length} projects)`);
  }
}

if (!(await gate(args, plan))) process.exit(0);

// ── Upload projects ──────────────────────────────────────────────────────────
console.log("Uploading projects…");
for (const { id, ...data } of projects) {
  await db.collection("projects").doc(id).set(data);
  console.log(`  ✓ ${data.name} (table ${data.tableNumber})`);
}

// ── Create Auth accounts + judge docs ───────────────────────────────────────
console.log("\nCreating judge accounts…");
for (const judge of judges) {
  const { email, password, assignedProjects, ...profile } = judge;
  let uid = existingUids.get(email);
  if (uid) {
    console.log(`  · ${judge.name} already exists, skipping Auth creation`);
  } else {
    uid = (await auth.createUser({ email, password, displayName: judge.name })).uid;
  }
  await db.collection("judges").doc(uid).set({
    ...profile,
    email,
    assignedProjects,
  });
  console.log(`  ✓ ${judge.name} (${email}) → ${assignedProjects.length} projects`);
}

console.log("\nDone ✅");
console.log("\nLogin credentials:");
for (const j of judges) {
  console.log(`  ${j.name.padEnd(28)} ${j.email}  /  ${j.password}`);
}
process.exit(0);
