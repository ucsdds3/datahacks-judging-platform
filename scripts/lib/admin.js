/**
 * lib/admin.js — single shared firebase-admin initialisation.
 *
 * Every seed/migration/admin script imports `db` and `auth` from here. The
 * admin SDK authenticates with the service account and BYPASSES firestore.rules
 * entirely, which is why the rules can deny all client writes to /judges and
 * /projects without breaking any of these scripts.
 *
 * The service account lives at src/assets/serviceAccount.json and is gitignored.
 * Never inline the web apiKey/firebaseConfig in a script again — the client SDK
 * is for src/ only.
 */

import admin from "firebase-admin";
import { createRequire } from "module";
import { existsSync } from "fs";
import path from "path";
import { fileURLToPath } from "url";

const require = createRequire(import.meta.url);

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, "..", "..");
const serviceAccountPath = path.join(repoRoot, "src", "assets", "serviceAccount.json");

if (!existsSync(serviceAccountPath)) {
  console.error(
    `\n[admin] Missing service account at ${serviceAccountPath}\n` +
      `        Download it from Firebase console → Project settings → Service accounts.\n` +
      `        It is gitignored on purpose; never commit it.\n`
  );
  process.exit(1);
}

const serviceAccount = require(serviceAccountPath);

// Guard against double-init when several modules import this file.
if (!admin.apps.length) {
  admin.initializeApp({ credential: admin.credential.cert(serviceAccount) });
}

export const app = admin.app();
export const db = admin.firestore();
export const auth = admin.auth();
export const projectId = serviceAccount.project_id;
export { admin };

/** FieldValue/Timestamp helpers, re-exported so scripts need not import admin. */
export const { FieldValue, Timestamp } = admin.firestore;
