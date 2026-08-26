/**
 * create-test-judge.js — create one throwaway judge account + judge doc.
 *
 * Usage:
 *   node scripts/create-test-judge.js [--commit] [--yes]
 *
 * DRY RUN BY DEFAULT — pass --commit to actually write.
 */

import { auth, db } from "./lib/admin.js";
import { ChangePlan, gate, parseArgs } from "./lib/cli.js";
import { requireSecret } from "./lib/secrets.js";

const args = parseArgs();

// Password comes from the environment -- see scripts/lib/secrets.js. Even a
// throwaway account gets a real judge doc and can read project data.
const TEST_EMAIL = process.env.TEST_JUDGE_EMAIL || "mohak@gmail.com";
const ASSIGNED = ["serverlesssync", "medscanai", "campuspulse", "neuralchef", "deepdiagnose"];

const plan = new ChangePlan("create test judge");

let existingUid = null;
try {
  existingUid = (await auth.getUserByEmail(TEST_EMAIL)).uid;
} catch (err) {
  if (err.code !== "auth/user-not-found") throw err;
}

if (existingUid) {
  console.log(`Auth account ${TEST_EMAIL} already exists (uid: ${existingUid}).`);
  const snap = await db.collection("judges").doc(existingUid).get();
  plan[snap.exists ? "update" : "create"](`judges/${existingUid}`, `— Mohak`);
} else {
  plan.create(`auth/${TEST_EMAIL}`);
  plan.create(`judges/<new uid>`, `— Mohak (${ASSIGNED.length} projects)`);
}

if (!(await gate(args, plan))) process.exit(0);

const uid = existingUid ?? (await auth.createUser({
  email: TEST_EMAIL,
  password: requireSecret("TEST_JUDGE_PASSWORD"),
  displayName: "Mohak",
})).uid;

await db.collection("judges").doc(uid).set({
  name: "Mohak",
  email: TEST_EMAIL,
  track: "AI/ML",
  assignedProjects: ASSIGNED,
});

console.log(`Created judge: ${TEST_EMAIL} (uid: ${uid})`);
console.log(`Assigned ${ASSIGNED.length} test projects.`);
process.exit(0);
