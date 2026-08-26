/**
 * create-leaderboard-user.js — create the organizer/leaderboard Auth account.
 *
 * Usage:
 *   node scripts/create-leaderboard-user.js [--commit] [--yes]
 *
 * DRY RUN BY DEFAULT — pass --commit to actually create the account.
 */

import { auth } from "./lib/admin.js";
import { ChangePlan, gate, parseArgs } from "./lib/cli.js";
import { requireSecret } from "./lib/secrets.js";

const args = parseArgs();

// This is the account firestore.rules grants organizer access to -- it can read
// every evaluation in the database. Its password is read from the environment
// and never stored in source. See scripts/lib/secrets.js.
const EMAIL = process.env.ORGANIZER_EMAIL || "ds3@datahacks2026.ucsd";

const plan = new ChangePlan("create leaderboard user");

let exists = false;
try {
  const existing = await auth.getUserByEmail(EMAIL);
  exists = true;
  console.log(`${EMAIL} already exists (uid: ${existing.uid}).`);
} catch (err) {
  if (err.code !== "auth/user-not-found") throw err;
  plan.create(`auth/${EMAIL}`);
}

if (exists) process.exit(0);

if (!(await gate(args, plan, { target: "production Firebase Auth" }))) process.exit(0);

// Read the secret only once we are actually committing, so a dry run works
// without any environment setup.
const PASSWORD = requireSecret("ORGANIZER_PASSWORD");

const user = await auth.createUser({ email: EMAIL, password: PASSWORD });
console.log(`Created: ${EMAIL}  (uid: ${user.uid})`);
process.exit(0);
