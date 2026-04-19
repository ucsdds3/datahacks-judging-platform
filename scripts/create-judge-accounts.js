/**
 * create-judge-accounts.js
 * Creates Firebase Auth accounts for all judges in judge_logins.csv
 * Usage: node scripts/create-judge-accounts.js
 */

import { initializeApp } from "firebase/app";
import { getAuth, createUserWithEmailAndPassword, signOut } from "firebase/auth";
import { readFileSync } from "fs";

const firebaseConfig = {
  apiKey: "AIzaSyCGu1nmbD7arFk6E7j4TGZRSb5mau1Uv-A",
  authDomain: "dh-judge-platform.firebaseapp.com",
  projectId: "dh-judge-platform",
};

const auth = getAuth(initializeApp(firebaseConfig));

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
console.log(`Creating ${judges.length} accounts…\n`);

let created = 0, skipped = 0, failed = 0;

for (const j of judges) {
  try {
    await createUserWithEmailAndPassword(auth, j.Email, j.Password);
    await signOut(auth);
    created++;
    process.stdout.write(".");
  } catch (err) {
    if (err.code === "auth/email-already-in-use") {
      skipped++;
      process.stdout.write("s");
    } else {
      failed++;
      console.error(`\n  ✗ ${j.Email}: ${err.message}`);
    }
  }
}

console.log(`\n\nDone ✅`);
console.log(`  ${created} created | ${skipped} already existed | ${failed} failed`);
process.exit(0);
