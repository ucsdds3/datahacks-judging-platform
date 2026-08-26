/**
 * secrets.js — read credentials from the environment, never from source.
 *
 * Passwords used to be hardcoded in create-leaderboard-user.js and
 * create-test-judge.js. Both are now in git history, and Chrome flagged one of
 * them as present in a public breach corpus. Anything secret belongs in .env.
 *
 * IMPORTANT: do NOT give these variables a VITE_ prefix. Vite inlines every
 * VITE_-prefixed variable into the client bundle, which would publish the
 * password to anyone who opens the site.
 *
 * Usage:
 *   node --env-file=.env scripts/create-leaderboard-user.js --commit
 *   ORGANIZER_PASSWORD='...' node scripts/create-leaderboard-user.js --commit
 */

const MIN_LENGTH = 12;

// Passwords that have appeared in this repo's history or are trivially
// guessable. Refusing them stops the old value being pasted straight back in.
const KNOWN_COMPROMISED = new Set([
  "ds3datahacks",
  "12341234",
  "password",
  "datahacks",
]);

/**
 * Read a required secret from the environment.
 *
 * Exits with an actionable message rather than throwing a stack trace, and
 * rejects weak or previously-leaked values outright.
 */
export function requireSecret(name, { minLength = MIN_LENGTH } = {}) {
  const value = process.env[name];

  if (!value) {
    console.error(
      `\nMissing ${name}.\n\n` +
        `  Add it to .env (no VITE_ prefix -- that would ship it to the browser):\n` +
        `      ${name}=<a long random password>\n\n` +
        `  Then run with:\n` +
        `      node --env-file=.env ${process.argv[1].split("/").pop()} --commit\n`
    );
    process.exit(1);
  }

  if (KNOWN_COMPROMISED.has(value.toLowerCase())) {
    console.error(
      `\n${name} is set to a password that has leaked from this repo.\n` +
        `  Pick a new one -- reusing it re-creates the account that Chrome flagged.\n`
    );
    process.exit(1);
  }

  if (value.length < minLength) {
    console.error(
      `\n${name} is only ${value.length} characters. Use at least ${minLength}.\n` +
        `  This account can read every evaluation in the database.\n`
    );
    process.exit(1);
  }

  return value;
}
