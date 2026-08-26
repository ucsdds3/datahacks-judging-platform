/**
 * lib/cli.js — shared safety rail for destructive scripts.
 *
 * Contract used by every script that writes, overwrites or deletes:
 *   - DRY RUN IS THE DEFAULT. Nothing is written unless --commit is passed.
 *   - A change summary (create / update / delete counts) is printed before any
 *     write happens.
 *   - Interactive confirmation is required before committing, unless --yes.
 */

import readline from "readline";

/**
 * Parse the shared flags off process.argv.
 * Returns { commit, dryRun, yes, positionals, flags }.
 */
export function parseArgs(argv = process.argv.slice(2)) {
  const flags = new Set();
  const positionals = [];
  for (const arg of argv) {
    if (arg.startsWith("--")) flags.add(arg);
    else positionals.push(arg);
  }

  const commit = flags.has("--commit");
  return {
    commit,
    dryRun: !commit,
    yes: flags.has("--yes") || flags.has("-y"),
    positionals,
    flags,
  };
}

/**
 * Tallies planned writes so a script can print an honest summary before doing
 * anything. Every planned mutation goes through here.
 */
export class ChangePlan {
  constructor(label = "") {
    this.label = label;
    this.creates = [];
    this.updates = [];
    this.deletes = [];
  }

  create(pathStr, detail) { this.creates.push({ path: pathStr, detail }); }
  update(pathStr, detail) { this.updates.push({ path: pathStr, detail }); }
  delete(pathStr, detail) { this.deletes.push({ path: pathStr, detail }); }

  get total() {
    return this.creates.length + this.updates.length + this.deletes.length;
  }

  /** Print the counts, plus a sample of each bucket. */
  printSummary({ sample = 10 } = {}) {
    const head = this.label ? `\n── ${this.label} ` : "\n── Planned changes ";
    console.log(head.padEnd(78, "─"));
    console.log(`  create: ${this.creates.length}`);
    console.log(`  update: ${this.updates.length}`);
    console.log(`  delete: ${this.deletes.length}`);
    console.log(`  total : ${this.total}`);

    for (const [name, rows] of [
      ["CREATE", this.creates],
      ["UPDATE", this.updates],
      ["DELETE", this.deletes],
    ]) {
      if (!rows.length) continue;
      console.log(`\n  ${name}`);
      for (const row of rows.slice(0, sample)) {
        console.log(`    ${row.path}${row.detail ? `  ${row.detail}` : ""}`);
      }
      if (rows.length > sample) console.log(`    … and ${rows.length - sample} more`);
    }
    console.log("");
  }
}

/** Ask the user to type a confirmation word. Resolves true/false. */
export function confirm(question, expected = "yes") {
  if (!process.stdin.isTTY) {
    console.error(
      `\n[safety] Refusing to write: no TTY for confirmation and --yes was not passed.\n`
    );
    return Promise.resolve(false);
  }
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  return new Promise((resolve) => {
    rl.question(`${question} (type "${expected}") `, (answer) => {
      rl.close();
      resolve(answer.trim().toLowerCase() === expected.toLowerCase());
    });
  });
}

/**
 * The gate every destructive script must pass through.
 * Returns true only when it is safe to write.
 */
export async function gate({ commit, yes }, plan, { target = "production Firestore" } = {}) {
  plan.printSummary();

  if (plan.total === 0) {
    console.log("Nothing to do.\n");
    return false;
  }

  if (!commit) {
    console.log(
      `DRY RUN — nothing was written. Re-run with --commit to apply these ${plan.total} changes.\n`
    );
    return false;
  }

  if (yes) {
    console.log(`--yes given, applying ${plan.total} changes to ${target}…\n`);
    return true;
  }

  const ok = await confirm(
    `About to apply ${plan.total} changes to ${target}. Continue?`
  );
  if (!ok) {
    console.log("Aborted. Nothing was written.\n");
    return false;
  }
  return true;
}

/** Standard usage banner footer for the shared flags. */
export const FLAGS_HELP = `
Flags:
  --commit   actually write (default is a dry run that writes nothing)
  --yes      skip the interactive confirmation (for CI)
`;
