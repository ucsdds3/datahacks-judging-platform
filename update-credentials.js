import admin from "firebase-admin";
import { createRequire } from "module";
import { readFileSync } from "fs";

const require = createRequire(import.meta.url);
const serviceAccount = require("./src/assets/serviceAccount.json");

admin.initializeApp({ credential: admin.credential.cert(serviceAccount) });
const auth = admin.auth();

// ── STEP 1: DELETE ALL NON-ADMIN USERS ───────────────────────
// Both organizer accounts must survive. ds3@datahacks2026.ucsd is the one
// actually in use -- it is what firestore.rules grants organizer access to and
// what Login.jsx routes to the leaderboard. Preserving only ds3@ucsd.edu (which
// has never signed in) would delete the working leaderboard account.
const PRESERVE_EMAILS = new Set([
  "ds3@datahacks2026.ucsd",
  "ds3@ucsd.edu",
]);

console.log("Fetching existing users...");
let deleted = 0;
let pageToken;
do {
  const result = await auth.listUsers(1000, pageToken);
  const toDelete = result.users
    .filter(u => !PRESERVE_EMAILS.has(u.email))
    .map(u => u.uid);
  if (toDelete.length) {
    await auth.deleteUsers(toDelete);
    deleted += toDelete.length;
    console.log(`  deleted ${toDelete.length} users`);
  }
  pageToken = result.pageToken;
} while (pageToken);
console.log(`✅ Cleared ${deleted} accounts\n`);

// ── STEP 2: CREATE CHECKED-IN JUDGE ACCOUNTS ─────────────────
// CSV columns: Tracks, Name, Checked-In, Email, Username, (empty), Password
const lines = readFileSync("src/assets/password_judges - Checked-In.csv", "utf8")
  .replace(/\r/g, "")
  .trim()
  .split("\n");

const judges = lines.slice(1)
  .map(line => {
    const cols = line.split(",");
    return {
      name:      (cols[1] ?? "").trim(),
      checkedIn: (cols[2] ?? "").trim(),
      username:  (cols[4] ?? "").trim(),
      pin:       (cols[6] ?? "").trim(),
    };
  })
  .filter(j => j.checkedIn === "TRUE" && j.username && j.pin);

console.log(`Creating ${judges.length} checked-in judge accounts...`);
let created = 0, failed = 0;
for (const j of judges) {
  const email = `${j.username.toLowerCase()}@datahacks2026.ucsd`;
  // Pad PIN to 4 digits so DH+PIN is always ≥ 6 chars (Firebase minimum)
  const paddedPin = j.pin.padStart(4, "0");
  const password = `DH${paddedPin}`;
  try {
    await auth.createUser({ email, password, displayName: j.name });
    created++;
    console.log(`  created: ${j.name} (${j.username})`);
  } catch (err) {
    failed++;
    console.error(`  FAILED:  ${j.name} — ${err.message}`);
  }
}

console.log(`\nDone. created=${created} failed=${failed}`);
process.exit(0);
