/**
 * Security rules tests for the judging platform.
 *
 * Run:  npm run test:rules
 *
 * These run entirely against the local Firestore emulator. Production is never
 * contacted. The first test is the one that matters most — before these rules
 * existed, an anonymous caller could read every evaluation in the database.
 */

import {
  initializeTestEnvironment,
  assertFails,
  assertSucceeds
} from "@firebase/rules-unit-testing";
import { doc, getDoc, setDoc, deleteDoc, collection, getDocs, query, where } from "firebase/firestore";
import { readFileSync } from "fs";

const JUDGE_A = "uidJudgeA";
const JUDGE_B = "uidJudgeB";
const ORGANIZER_EMAIL = "ds3@datahacks2026.ucsd";

const validScores = { technical_rigor: 8, data_mastery: 7, demo_quality: 9, innovation: 6, impact: 10 };

const evaluation = (judgeId, projectId, overrides = {}) => ({
  judgeId,
  projectId,
  track: "AI/ML",
  scores: validScores,
  comment: "Solid work.",
  timestamp: new Date(),
  ...overrides
});

let testEnv;
let failures = 0;
let passes = 0;

async function check(name, fn) {
  try {
    await fn();
    passes++;
    console.log(`  PASS  ${name}`);
  } catch (err) {
    failures++;
    console.log(`  FAIL  ${name}\n        ${err.message.split("\n")[0]}`);
  }
}

testEnv = await initializeTestEnvironment({
  projectId: "dh-judge-platform",
  firestore: {
    rules: readFileSync("firestore.rules", "utf8"),
    host: "127.0.0.1",
    port: 8080
  }
});

// Seed baseline data with rules bypassed.
await testEnv.withSecurityRulesDisabled(async (ctx) => {
  const db = ctx.firestore();
  await setDoc(doc(db, "judges", JUDGE_A), {
    name: "Judge A", email: "judgea@datahacks2026.ucsd", track: "AI/ML", assignedProjects: ["p1"]
  });
  await setDoc(doc(db, "judges", JUDGE_B), {
    name: "Judge B", email: "judgeb@datahacks2026.ucsd", track: "AI/ML", assignedProjects: ["p1"]
  });
  await setDoc(doc(db, "projects", "p1"), { name: "Test Project", tracks: ["AI/ML"], tableNumber: 12 });
  await setDoc(doc(db, "evaluations", `${JUDGE_A}_p1`), evaluation(JUDGE_A, "p1"));
  await setDoc(doc(db, "evaluations", `${JUDGE_B}_p1`), evaluation(JUDGE_B, "p1"));
});

const anon = testEnv.unauthenticatedContext().firestore();
const judgeA = testEnv.authenticatedContext(JUDGE_A, { email: "judgea@datahacks2026.ucsd" }).firestore();
const organizer = testEnv.authenticatedContext("uidOrganizer", { email: ORGANIZER_EMAIL }).firestore();

console.log("\nAnonymous access (the vulnerability these rules close)");
await check("anonymous CANNOT read the evaluations collection", () =>
  assertFails(getDocs(collection(anon, "evaluations"))));
await check("anonymous CANNOT read a single evaluation", () =>
  assertFails(getDoc(doc(anon, "evaluations", `${JUDGE_A}_p1`))));
await check("anonymous CANNOT write an evaluation", () =>
  assertFails(setDoc(doc(anon, "evaluations", "anon_p1"), evaluation("anon", "p1"))));
await check("anonymous CANNOT read projects", () =>
  assertFails(getDocs(collection(anon, "projects"))));
await check("anonymous CANNOT read judges", () =>
  assertFails(getDocs(collection(anon, "judges"))));

console.log("\nJudge isolation");
await check("judge CAN read their own evaluation", () =>
  assertSucceeds(getDoc(doc(judgeA, "evaluations", `${JUDGE_A}_p1`))));
await check("judge CANNOT read another judge's evaluation", () =>
  assertFails(getDoc(doc(judgeA, "evaluations", `${JUDGE_B}_p1`))));
await check("judge CANNOT list the whole evaluations collection", () =>
  assertFails(getDocs(collection(judgeA, "evaluations"))));
await check("judge CAN query their own evaluations by judgeId", () =>
  assertSucceeds(getDocs(query(collection(judgeA, "evaluations"), where("judgeId", "==", JUDGE_A)))));

await check("judge CAN get an evaluation that does NOT exist yet (first score)", () =>
  assertSucceeds(getDoc(doc(judgeA, "evaluations", `${JUDGE_A}_neverScoredYet`))));
await check("judge CANNOT probe another judge's not-yet-existing evaluation", () =>
  assertFails(getDoc(doc(judgeA, "evaluations", `${JUDGE_B}_neverScoredYet`))));

console.log("\nJudge writes");
await check("judge CAN upsert their own evaluation", () =>
  assertSucceeds(setDoc(doc(judgeA, "evaluations", `${JUDGE_A}_p1`), evaluation(JUDGE_A, "p1", { comment: "Updated." }))));
await check("judge CANNOT write an evaluation attributed to someone else", () =>
  assertFails(setDoc(doc(judgeA, "evaluations", `${JUDGE_B}_p2`), evaluation(JUDGE_B, "p2"))));
await check("judge CANNOT delete an evaluation", () =>
  assertFails(deleteDoc(doc(judgeA, "evaluations", `${JUDGE_A}_p1`))));

console.log("\nScore validation");
await check("score above 10 is rejected", () =>
  assertFails(setDoc(doc(judgeA, "evaluations", `${JUDGE_A}_p1`), evaluation(JUDGE_A, "p1", { scores: { ...validScores, innovation: 11 } }))));
await check("non-numeric score is rejected", () =>
  assertFails(setDoc(doc(judgeA, "evaluations", `${JUDGE_A}_p1`), evaluation(JUDGE_A, "p1", { scores: { ...validScores, innovation: "ten" } }))));
await check("score of 0 is accepted (rubric defines a 0-2 band)", () =>
  assertSucceeds(setDoc(doc(judgeA, "evaluations", `${JUDGE_A}_p1`), evaluation(JUDGE_A, "p1", { scores: { ...validScores, innovation: 0 } }))));
await check("unexpected extra field is rejected", () =>
  assertFails(setDoc(doc(judgeA, "evaluations", `${JUDGE_A}_p1`), evaluation(JUDGE_A, "p1", { adminOverride: true }))));

console.log("\nReference data is read-only to clients");
await check("judge CAN read projects", () =>
  assertSucceeds(getDoc(doc(judgeA, "projects", "p1"))));
await check("judge CANNOT modify a project", () =>
  assertFails(setDoc(doc(judgeA, "projects", "p1"), { name: "Hacked" })));
await check("judge CANNOT modify a judge record", () =>
  assertFails(setDoc(doc(judgeA, "judges", JUDGE_A), { track: "Economics" })));

console.log("\nOrganizer access");
await check("organizer CAN read all evaluations", () =>
  assertSucceeds(getDocs(collection(organizer, "evaluations"))));
await check("organizer CAN read all judges", () =>
  assertSucceeds(getDocs(collection(organizer, "judges"))));

console.log("\nCheck-ins (organizer console)");
await testEnv.withSecurityRulesDisabled(async (ctx) => {
  await setDoc(doc(ctx.firestore(), "checkins", JUDGE_B), {
    judgeId: JUDGE_B, name: "Judge B", email: "judgeb@datahacks2026.ucsd",
    track: "AI/ML", checkedIn: true, checkedInAt: new Date(), floater: false,
    cutoffAt: new Date(), checkedInBy: ORGANIZER_EMAIL
  });
});

const checkin = (judgeId, overrides = {}) => ({
  judgeId, name: "Judge A", email: "judgea@datahacks2026.ucsd", track: "AI/ML",
  checkedIn: true, checkedInAt: new Date(), floater: false,
  cutoffAt: new Date(), checkedInBy: ORGANIZER_EMAIL, ...overrides
});

await check("organizer CAN check a judge in", () =>
  assertSucceeds(setDoc(doc(organizer, "checkins", JUDGE_A), checkin(JUDGE_A))));
await check("organizer CAN mark a late arrival as a floater", () =>
  assertSucceeds(setDoc(doc(organizer, "checkins", JUDGE_A), checkin(JUDGE_A, { floater: true }))));
await check("organizer CAN undo a check-in", () =>
  assertSucceeds(deleteDoc(doc(organizer, "checkins", JUDGE_A))));
await check("organizer CAN list all check-ins", () =>
  assertSucceeds(getDocs(collection(organizer, "checkins"))));

await check("judge CANNOT check themselves in", () =>
  assertFails(setDoc(doc(judgeA, "checkins", JUDGE_A), checkin(JUDGE_A))));
await check("judge CANNOT list the check-in roster", () =>
  assertFails(getDocs(collection(judgeA, "checkins"))));
await check("judge CANNOT read another judge's check-in", () =>
  assertFails(getDoc(doc(judgeA, "checkins", JUDGE_B))));
await check("anonymous CANNOT read check-ins", () =>
  assertFails(getDocs(collection(anon, "checkins"))));

await check("check-in with a mismatched judgeId is rejected", () =>
  assertFails(setDoc(doc(organizer, "checkins", JUDGE_A), checkin(JUDGE_B))));
await check("check-in with an unexpected field is rejected", () =>
  assertFails(setDoc(doc(organizer, "checkins", JUDGE_A), checkin(JUDGE_A, { isAdmin: true }))));
await check("check-in with a non-boolean floater is rejected", () =>
  assertFails(setDoc(doc(organizer, "checkins", JUDGE_A), checkin(JUDGE_A, { floater: "yes" }))));

console.log("\nAssignment runs (solver output)");
await testEnv.withSecurityRulesDisabled(async (ctx) => {
  await setDoc(doc(ctx.firestore(), "runs", "run-001"), {
    runId: "run-001", generatedAt: new Date(), judgeCount: 42,
    projectCount: 36, connectivityOk: true, warnings: []
  });
});

await check("organizer CAN read assignment runs", () =>
  assertSucceeds(getDocs(collection(organizer, "runs"))));
await check("judge CANNOT read assignment runs", () =>
  assertFails(getDoc(doc(judgeA, "runs", "run-001"))));
await check("anonymous CANNOT read assignment runs", () =>
  assertFails(getDoc(doc(anon, "runs", "run-001"))));
await check("even an ORGANIZER cannot forge a passing connectivity result", () =>
  assertFails(setDoc(doc(organizer, "runs", "run-002"), {
    runId: "run-002", generatedAt: new Date(), connectivityOk: true
  })));

await testEnv.cleanup();

console.log(`\n${passes} passed, ${failures} failed\n`);
process.exit(failures > 0 ? 1 : 0);
