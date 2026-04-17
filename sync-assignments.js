import fs from "node:fs";
import path from "node:path";
import { initializeApp } from "firebase/app";
import { collection, doc, getDocs, getFirestore, setDoc } from "firebase/firestore";

const firebaseConfig = {
  apiKey: "AIzaSyCGu1nmbD7arFk6E7j4TGZRSb5mau1Uv-A",
  authDomain: "dh-judge-platform.firebaseapp.com",
  projectId: "dh-judge-platform"
};

const app = initializeApp(firebaseConfig);
const db = getFirestore(app);

const inputPath = path.resolve(
  process.cwd(),
  process.argv[2] || "assignments.local.json"
);

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
  const projectSnapshot = await getDocs(collection(db, "projects"));
  const existingProjectIds = new Set(projectSnapshot.docs.map((project) => project.id));

  const unknownProjectIds = [...assignedJudgesByProject.keys()].filter(
    (projectId) => !existingProjectIds.has(projectId)
  );

  if (unknownProjectIds.length > 0) {
    fail(
      `These assigned project ids do not exist in Firestore: ${unknownProjectIds.join(", ")}`
    );
  }

  console.log(`[assignments] Syncing ${judges.length} judges from ${path.basename(inputPath)}...`);

  for (const judge of judges) {
    await setDoc(
      doc(db, "judges", judge.id),
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
    await setDoc(
      doc(db, "projects", projectId),
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
