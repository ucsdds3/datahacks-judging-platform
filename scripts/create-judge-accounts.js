/**
 * create-judge-accounts.js
 * Creates Firebase Auth accounts for all judges in judge_logins.csv
 *
 * Usage: node scripts/create-judge-accounts.js [--commit] [--yes]
 *
 * DRY RUN BY DEFAULT — pass --commit to actually create accounts.
 */

import { readFileSync } from "fs";
import { auth } from "./lib/admin.js";
import { ChangePlan, gate, parseArgs } from "./lib/cli.js";

const args = parseArgs();

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

const judges = parseCSV(readFileSync("src/assets/judge_logins.csv", "utf8"));
console.log(`Read ${judges.length} rows from judge_logins.csv\n`);

// This script expects Email + Password columns. The CSV that currently ships in
// src/assets has Name,Track,Username,Password instead — with a 4-digit PIN that
// is below Firebase's 6-character password minimum. In that shape every row
// silently failed here. update-credentials.js is the script that understands the
// username/PIN format (email = <username>@datahacks2026.ucsd, password = DH<pin>).
if (judges.length && !("Email" in judges[0])) {
  console.error(
    `[create-judge-accounts] judge_logins.csv has no "Email" column ` +
      `(found: ${Object.keys(judges[0]).join(", ")}).\n` +
      `  This script cannot create accounts from that shape.\n` +
      `  Use update-credentials.js for the username/PIN CSV format.\n`
  );
  process.exit(1);
}

// ── Plan: which of these already exist in Auth? ──────────────────────────────
const plan = new ChangePlan("create judge auth accounts");
const toCreate = [];
let alreadyExist = 0;

for (const j of judges) {
  if (!j.Email) continue;
  try {
    await auth.getUserByEmail(j.Email);
    alreadyExist++;
  } catch (err) {
    if (err.code !== "auth/user-not-found") throw err;
    plan.create(`auth/${j.Email}`);
    toCreate.push(j);
  }
}
console.log(`  ${alreadyExist} accounts already exist and will be skipped.`);

if (!(await gate(args, plan, { target: "production Firebase Auth" }))) process.exit(0);

let created = 0, failed = 0;
for (const j of toCreate) {
  try {
    await auth.createUser({ email: j.Email, password: j.Password });
    created++;
    process.stdout.write(".");
  } catch (err) {
    failed++;
    console.error(`\n  ✗ ${j.Email}: ${err.message}`);
  }
}

console.log(`\n\nDone ✅`);
console.log(`  ${created} created | ${alreadyExist} already existed | ${failed} failed`);
process.exit(0);
