import admin from "firebase-admin";
import { createRequire } from "module";
import { readFileSync } from "fs";

const require = createRequire(import.meta.url);
const serviceAccount = require("./src/assets/serviceAccount.json");

admin.initializeApp({ credential: admin.credential.cert(serviceAccount) });
const auth = admin.auth();

// ── STEP 1: DELETE ALL NON-ADMIN USERS ───────────────────────
console.log("Fetching existing users...");
let deleted = 0;
let pageToken;
do {
  const result = await auth.listUsers(1000, pageToken);
  const toDelete = result.users
    .filter(u => u.email !== "ds3@ucsd.edu")
    .map(u => u.uid);
  if (toDelete.length) {
    await auth.deleteUsers(toDelete);
    deleted += toDelete.length;
    console.log(`  deleted ${toDelete.length} users`);
  }
  pageToken = result.pageToken;
} while (pageToken);
console.log(`✅ Cleared ${deleted} accounts\n`);

// ── STEP 2: CREATE JUDGE ACCOUNTS ────────────────────────────
const raw = readFileSync("src/assets/judge_logins.csv", "utf8").trim().split("\n");
const judges = raw.slice(1).map(line => {
  const cols = [...line.matchAll(/"([^"]*)"/g)].map(m => m[1]);
  return { name: cols[0], track: cols[1], username: cols[2], pin: cols[3] };
});

console.log(`Creating ${judges.length} judge accounts...`);
let created = 0, failed = 0;
for (const j of judges) {
  const email = `${j.username.toLowerCase()}@datahacks2026.ucsd`;
  try {
    await auth.createUser({ email, password: `DH${j.pin}`, displayName: j.name });
    created++;
    console.log(`  created: ${j.name}`);
  } catch (err) {
    failed++;
    console.error(`  FAILED:  ${j.name} — ${err.message}`);
  }
}

console.log(`\nDone. created=${created} failed=${failed}`);
process.exit(0);
