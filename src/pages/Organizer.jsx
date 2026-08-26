import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { auth, db } from "../firebase";
import {
  collection,
  deleteDoc,
  doc,
  limit,
  onSnapshot,
  orderBy,
  query,
  serverTimestamp,
  setDoc,
  Timestamp,
  updateDoc,
} from "firebase/firestore";
import { signOut } from "firebase/auth";
import { useNavigate } from "react-router-dom";

/* ────────────────────────────────────────────────────────────────────────────
   Constants
   ──────────────────────────────────────────────────────────────────────────── */

const CUTOFF_STORAGE_KEY = "dh.organizer.checkinCutoff";
const DEFAULT_TARGET_JUDGES = 3;

const TABS = [
  { id: "checkin", label: "Check-in" },
  { id: "assignments", label: "Assignment status" },
  { id: "coverage", label: "Live coverage" },
];

const styles = `
  @import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=DM+Sans:wght@300;400;500;600;700&display=swap');
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  /* #root is a centered flex column with text-align:center (Vite default),
     so the console re-establishes normal document flow for itself. */
  .og-root {
    min-height: 100vh; width: 100%; background: #F7F5F0;
    font-family: 'DM Sans', sans-serif; color: #1a1a1a;
    text-align: left;
  }

  .og-root :focus-visible {
    outline: 2px solid #C9A96E; outline-offset: 2px; border-radius: 6px;
  }

  /* ── Top bar ── */
  .og-topbar {
    background: #fff; border-bottom: 1px solid #E8E4DC;
    padding: 0 40px; height: 60px;
    display: flex; align-items: center; justify-content: space-between;
    position: sticky; top: 0; z-index: 100;
  }
  .og-topbar-left { display: flex; align-items: center; gap: 12px; min-width: 0; }
  .og-topbar-icon {
    width: 30px; height: 30px; background: #1a1a1a; border-radius: 7px;
    display: flex; align-items: center; justify-content: center; flex-shrink: 0;
  }
  .og-topbar-icon svg { width: 16px; height: 16px; }
  .og-topbar-wordmark { font-size: 14px; font-weight: 600; letter-spacing: -0.01em; }
  .og-topbar-sep { width: 1px; height: 18px; background: #E0DBD2; }
  .og-topbar-badge {
    display: inline-flex; align-items: center; gap: 5px;
    font-size: 11px; font-weight: 600; letter-spacing: 0.07em; text-transform: uppercase;
    color: #7a5c2e; background: #FDF0DC; border: 1px solid #F0D9A8;
    border-radius: 100px; padding: 3px 10px; white-space: nowrap;
  }
  .og-topbar-badge-dot { width: 5px; height: 5px; border-radius: 50%; background: #C9A96E; }
  .og-btn-quiet {
    font-size: 13px; font-weight: 500; color: #5a534a;
    background: none; border: 1px solid #E0DBD2; border-radius: 8px;
    padding: 6px 14px; cursor: pointer; font-family: 'DM Sans', sans-serif;
    transition: background 0.15s, border-color 0.15s; white-space: nowrap;
  }
  .og-btn-quiet:hover { background: #F0EDE7; border-color: #ccc8c0; }

  /* ── Body ── */
  .og-body { max-width: 1080px; margin: 0 auto; padding: 32px 24px 96px; }

  .og-header { margin-bottom: 22px; animation: og-up 0.4s ease both; }
  .og-eyebrow {
    display: inline-flex; align-items: center; gap: 7px;
    font-size: 11px; font-weight: 600; letter-spacing: 0.1em; text-transform: uppercase;
    color: #8a7f6e; margin-bottom: 8px;
  }
  .og-eyebrow-dot { width: 5px; height: 5px; border-radius: 50%; background: #C9A96E; }
  .og-title { font-family: 'DM Serif Display', serif; font-size: 32px; letter-spacing: -0.02em; color: #111; }
  .og-sub { font-size: 13px; color: #a09488; margin-top: 4px; }

  /* ── Tabs ── */
  .og-tabs { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 26px; }
  .og-tab {
    font-size: 13px; font-weight: 500; padding: 7px 16px;
    border-radius: 100px; border: 1px solid #E0DBD2; background: #fff; color: #5a534a;
    cursor: pointer; font-family: 'DM Sans', sans-serif; transition: all 0.15s;
  }
  .og-tab:hover { background: #F0EDE7; border-color: #ccc8c0; }
  .og-tab[aria-selected="true"] {
    background: #1a1a1a; border-color: #1a1a1a; color: #fff; font-weight: 600;
  }
  .og-tab-count {
    display: inline-block; margin-left: 7px; font-size: 11px; font-weight: 600;
    opacity: 0.65;
  }

  /* ── Cards ── */
  .og-card {
    background: #fff; border: 1px solid #E8E4DC; border-radius: 14px;
    padding: 20px 22px; margin-bottom: 16px;
    animation: og-up 0.35s ease both;
  }
  .og-card-head {
    display: flex; align-items: center; justify-content: space-between;
    gap: 12px; flex-wrap: wrap; margin-bottom: 14px;
  }
  .og-card-title {
    font-size: 12px; font-weight: 700; letter-spacing: 0.08em;
    text-transform: uppercase; color: #8a7f6e;
  }
  .og-card-note { font-size: 12px; color: #a09488; }

  /* ── Stat grid ── */
  .og-stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 16px; }
  .og-stat {
    background: #fff; border: 1px solid #E8E4DC; border-radius: 12px;
    padding: 16px 18px; display: flex; flex-direction: column; gap: 5px;
  }
  .og-stat-value {
    font-family: 'DM Serif Display', serif; font-size: 26px;
    letter-spacing: -0.02em; color: #111; line-height: 1;
  }
  .og-stat-value.gold { color: #C9A96E; }
  .og-stat-value.green { color: #4a7c5f; }
  .og-stat-value.red { color: #b3261e; }
  .og-stat-label {
    font-size: 10px; font-weight: 600; letter-spacing: 0.07em;
    text-transform: uppercase; color: #a09488;
  }
  .og-stat-sub { font-size: 11px; color: #a09488; }

  /* ── Banners ── */
  .og-banner {
    border-radius: 12px; padding: 14px 16px; margin-bottom: 16px;
    font-size: 13px; line-height: 1.5; display: flex; gap: 12px; align-items: flex-start;
  }
  .og-banner-icon { flex-shrink: 0; font-size: 14px; line-height: 1.4; font-weight: 700; }
  .og-banner-body { min-width: 0; flex: 1; }
  .og-banner-title { font-weight: 600; margin-bottom: 3px; }
  .og-banner.err   { background: #FDEDEC; border: 1px solid #F5C2BE; color: #8c1d18; }
  .og-banner.warn  { background: #FEF7E6; border: 1px solid #F3DFA8; color: #6b4e10; }
  .og-banner.ok    { background: #E9F5EE; border: 1px solid #BAD5C4; color: #23543a; }
  .og-banner.info  { background: #F0EDE7; border: 1px solid #E0DBD2; color: #5a534a; }
  .og-banner-close {
    background: none; border: none; cursor: pointer; color: inherit;
    font-size: 16px; line-height: 1; padding: 2px 4px; font-family: inherit; opacity: 0.7;
  }
  .og-banner-close:hover { opacity: 1; }

  /* ── Cutoff ── */
  .og-cutoff {
    display: flex; align-items: center; justify-content: space-between;
    gap: 16px; flex-wrap: wrap;
  }
  .og-cutoff-main { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
  .og-cutoff-time {
    font-family: 'DM Serif Display', serif; font-size: 30px;
    letter-spacing: -0.02em; color: #111; line-height: 1.1;
  }
  .og-cutoff-state {
    display: inline-flex; align-items: center; gap: 6px;
    font-size: 11px; font-weight: 700; letter-spacing: 0.07em; text-transform: uppercase;
    border-radius: 100px; padding: 4px 12px;
  }
  .og-cutoff-state.open   { background: #E9F5EE; border: 1px solid #BAD5C4; color: #23543a; }
  .og-cutoff-state.passed { background: #FDEDEC; border: 1px solid #F5C2BE; color: #8c1d18; }
  .og-cutoff-edit { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }

  .og-label {
    display: block; font-size: 10px; font-weight: 600; letter-spacing: 0.07em;
    text-transform: uppercase; color: #a09488; margin-bottom: 5px;
  }
  .og-input {
    font-family: 'DM Sans', sans-serif; font-size: 13px; color: #1a1a1a;
    background: #fff; border: 1px solid #E0DBD2; border-radius: 9px;
    padding: 9px 12px; width: 100%; transition: border-color 0.15s;
  }
  .og-input:hover { border-color: #ccc8c0; }
  .og-input::placeholder { color: #b8b0a5; }

  /* ── Filter row ── */
  .og-filters { display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-end; margin-bottom: 14px; }
  .og-filters-search { flex: 1 1 260px; min-width: 0; }
  .og-pills { display: flex; flex-wrap: wrap; gap: 6px; }
  .og-pill {
    font-size: 12px; font-weight: 500; padding: 5px 12px; border-radius: 100px;
    border: 1px solid #E0DBD2; background: #fff; color: #5a534a;
    cursor: pointer; font-family: 'DM Sans', sans-serif; transition: all 0.15s;
  }
  .og-pill:hover { background: #F0EDE7; border-color: #ccc8c0; }
  .og-pill[aria-pressed="true"] { background: #F0EDE7; border-color: #C9A96E; color: #1a1a1a; font-weight: 600; }

  /* ── Judge rows ── */
  .og-list { display: flex; flex-direction: column; }
  .og-row {
    display: flex; align-items: center; gap: 12px;
    padding: 11px 4px; border-bottom: 1px solid #F0EDE7;
  }
  .og-row:last-child { border-bottom: none; }
  .og-row-main { flex: 1; min-width: 0; }
  .og-row-name { font-size: 14px; font-weight: 500; letter-spacing: -0.01em; }
  .og-row-meta {
    font-size: 12px; color: #a09488; margin-top: 2px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .og-row-right { display: flex; align-items: center; gap: 8px; flex-shrink: 0; flex-wrap: wrap; justify-content: flex-end; }

  .og-tag {
    display: inline-flex; align-items: center; gap: 5px;
    font-size: 10px; font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase;
    border-radius: 100px; padding: 3px 9px;
    background: #F5F2EB; border: 1px solid #E8E4DC; color: #6d6255; white-space: nowrap;
  }
  .og-tag.green  { background: #E9F5EE; border-color: #BAD5C4; color: #2d6a46; }
  .og-tag.gold   { background: #FDF0DC; border-color: #F0D9A8; color: #7a5c2e; }
  .og-tag.red    { background: #FDEDEC; border-color: #F5C2BE; color: #8c1d18; }
  .og-tag.blue   { background: #EEF2FF; border-color: #C7D2FE; color: #3730A3; }

  .og-btn {
    font-size: 12px; font-weight: 600; font-family: 'DM Sans', sans-serif;
    border-radius: 8px; padding: 7px 14px; cursor: pointer;
    border: 1px solid #1a1a1a; background: #1a1a1a; color: #fff;
    transition: opacity 0.15s, background 0.15s; white-space: nowrap;
  }
  .og-btn:hover:not(:disabled) { background: #333; }
  .og-btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .og-btn.secondary { background: #fff; color: #5a534a; border-color: #E0DBD2; }
  .og-btn.secondary:hover:not(:disabled) { background: #F0EDE7; }
  .og-btn.tiny { font-size: 11px; padding: 5px 10px; font-weight: 500; }

  /* ── Track breakdown ── */
  .og-track-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 10px; }
  .og-track-cell {
    border: 1px solid #E8E4DC; border-radius: 10px; padding: 12px 14px; background: #FAFAF8;
  }
  .og-track-cell-top {
    display: flex; align-items: baseline; justify-content: space-between; gap: 8px; margin-bottom: 8px;
  }
  .og-track-cell-name {
    font-size: 11px; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase;
    color: #5a534a; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .og-track-cell-val { font-size: 12px; font-weight: 600; color: #1a1a1a; white-space: nowrap; }
  .og-bar-track { height: 6px; background: #EDE9E2; border-radius: 100px; overflow: hidden; }
  .og-bar-fill {
    height: 100%; border-radius: 100px;
    background: linear-gradient(90deg, #C9A96E, #a8845a);
    transition: width 0.7s cubic-bezier(0.16, 1, 0.3, 1);
  }
  .og-bar-fill.complete { background: linear-gradient(90deg, #4a7c5f, #3a6b4f); }
  .og-bar-fill.danger { background: linear-gradient(90deg, #d9544d, #b3261e); }

  /* ── Tables ── */
  .og-table-wrap { overflow-x: auto; border: 1px solid #E8E4DC; border-radius: 12px; background: #fff; }
  .og-table { width: 100%; border-collapse: collapse; min-width: 520px; }
  .og-table thead tr { background: #FAFAF8; border-bottom: 1px solid #E8E4DC; }
  .og-table th {
    font-size: 10px; font-weight: 600; letter-spacing: 0.07em; text-transform: uppercase;
    color: #a09488; padding: 9px 12px; text-align: left; white-space: nowrap;
  }
  .og-table td { padding: 10px 12px; font-size: 12.5px; border-bottom: 1px solid #F0EDE7; vertical-align: middle; }
  .og-table tbody tr:last-child td { border-bottom: none; }
  .og-table tbody tr:hover { background: #FAFAF8; }
  .og-table .right { text-align: right; }
  .og-table .center { text-align: center; }
  .og-mono { font-family: 'SF Mono', ui-monospace, monospace; font-size: 11.5px; }

  /* ── Histogram ── */
  .og-hist { display: flex; flex-direction: column; gap: 7px; }
  .og-hist-row { display: flex; align-items: center; gap: 10px; font-size: 12px; }
  .og-hist-key {
    width: 78px; flex-shrink: 0; color: #5a534a; font-weight: 500; text-align: right;
  }
  .og-hist-bar-wrap { flex: 1; height: 18px; background: #F5F2EB; border-radius: 5px; overflow: hidden; min-width: 0; }
  .og-hist-bar { height: 100%; background: #C9A96E; border-radius: 5px; transition: width 0.6s ease; }
  .og-hist-bar.bad { background: #d9544d; }
  .og-hist-bar.good { background: #4a7c5f; }
  .og-hist-val { width: 46px; flex-shrink: 0; color: #8a7f6e; font-weight: 600; }

  /* ── Misc ── */
  .og-empty { padding: 32px 16px; text-align: center; color: #a09488; font-size: 13px; }
  .og-code {
    font-family: 'SF Mono', ui-monospace, monospace; font-size: 11.5px;
    background: #F5F2EB; border: 1px solid #E8E4DC; border-radius: 5px; padding: 1px 5px;
  }
  .og-ul { margin: 6px 0 0 18px; }
  .og-ul li { margin-bottom: 3px; }
  .og-inline-note { font-size: 12px; color: #a09488; margin-top: 10px; line-height: 1.5; }

  .og-loading {
    min-height: 100vh; display: flex; align-items: center; justify-content: center; background: #F7F5F0;
  }
  .og-loading-inner {
    display: flex; flex-direction: column; align-items: center; gap: 16px;
    color: #8a7f6e; font-size: 14px;
  }
  .og-loading-ring {
    width: 36px; height: 36px; border: 3px solid #E8E4DC;
    border-top-color: #C9A96E; border-radius: 50%; animation: og-spin 0.9s linear infinite;
  }
  .og-sr {
    position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
    overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0;
  }

  @keyframes og-spin { to { transform: rotate(360deg); } }
  @keyframes og-up { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }

  @media (max-width: 900px) {
    .og-stats { grid-template-columns: repeat(2, 1fr); }
  }
  @media (max-width: 600px) {
    .og-topbar { padding: 0 16px; }
    .og-topbar-wordmark, .og-topbar-sep { display: none; }
    .og-body { padding: 20px 12px 80px; }
    .og-title { font-size: 24px; }
    .og-card { padding: 16px 14px; border-radius: 12px; }
    .og-cutoff-time { font-size: 24px; }
    .og-row { flex-wrap: wrap; row-gap: 8px; }
    .og-row-right { width: 100%; justify-content: flex-start; }
    .og-hist-key { width: 66px; }
  }
  @media (max-width: 380px) {
    .og-stats { grid-template-columns: 1fr; }
  }
`;

/* ────────────────────────────────────────────────────────────────────────────
   Small helpers
   ──────────────────────────────────────────────────────────────────────────── */

/** Firestore Timestamp | Date | ISO string | epoch ms → Date (or null). */
function toDate(value) {
  if (!value) return null;
  if (typeof value.toDate === "function") {
    try {
      return value.toDate();
    } catch {
      return null;
    }
  }
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
  if (typeof value === "number") return new Date(value);
  if (typeof value === "string") {
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? null : d;
  }
  if (typeof value.seconds === "number") return new Date(value.seconds * 1000);
  return null;
}

function fmtDateTime(date) {
  if (!date) return "—";
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function fmtTime(date) {
  if (!date) return "—";
  return date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

/** "1h 24m" from a millisecond span. */
function fmtSpan(ms) {
  const total = Math.max(0, Math.round(ms / 60000));
  const h = Math.floor(total / 60);
  const m = total % 60;
  if (h === 0) return `${m}m`;
  return `${h}h ${m}m`;
}

/** Local Date → "YYYY-MM-DDTHH:mm" for <input type="datetime-local">. */
function toLocalInputValue(date) {
  const pad = (n) => String(n).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

function defaultCutoffValue() {
  const stored = typeof localStorage !== "undefined" ? localStorage.getItem(CUTOFF_STORAGE_KEY) : null;
  if (stored) return stored;
  const envValue = import.meta.env.VITE_CHECKIN_CUTOFF;
  if (envValue) {
    const d = new Date(envValue);
    if (!Number.isNaN(d.getTime())) return toLocalInputValue(d);
  }
  const d = new Date();
  d.setHours(10, 30, 0, 0);
  return toLocalInputValue(d);
}

function judgeTracksOf(judge) {
  if (Array.isArray(judge?.tracks) && judge.tracks.length) return judge.tracks;
  if (judge?.track) return [judge.track];
  return [];
}

function projectTracksOf(project) {
  if (Array.isArray(project?.tracks) && project.tracks.length) return project.tracks;
  if (project?.track) return [project.track];
  return ["(no track)"];
}

function errText(err) {
  if (!err) return "Unknown error";
  if (err.code === "permission-denied") {
    return "Permission denied by Firestore rules. See docs/organizer-console-rules.md — the `checkins` and `runs` rules may not be deployed yet.";
  }
  return err.message || String(err);
}

/**
 * Attribute evaluation rows (keyed by Auth UID) to judge documents (keyed by
 * username).
 *
 * There is no uid field on judge docs today, so this resolves in three passes,
 * strongest evidence first:
 *   1. `judges/{id}.uid` — authoritative, set it and everything below is moot.
 *   2. `runs/{runId}.judgeIndex` — the solver keys judges by Auth UID, so the
 *      run doc can publish the mapping. See docs/runs-contract.md.
 *   3. Inference: a UID's evaluated projects must be a subset of exactly one
 *      judge's assignedProjects, with a matching track. Iterated to a fixed
 *      point so unique matches prune the candidate sets of the rest.
 *
 * Anything still ambiguous is reported as unattributed rather than guessed —
 * a wrong attribution here would tell an organizer a judge is done when they
 * have not started.
 */
function attributeEvaluations(judges, evaluations, judgeIndex) {
  const byUid = new Map();
  for (const ev of evaluations) {
    const uid = ev.judgeId;
    if (!uid) continue;
    let entry = byUid.get(uid);
    if (!entry) {
      entry = { uid, projects: new Set(), tracks: new Set() };
      byUid.set(uid, entry);
    }
    if (ev.projectId) entry.projects.add(ev.projectId);
    if (ev.track) entry.tracks.add(ev.track);
  }

  const judgeToUid = new Map();
  const uidToJudge = new Map();

  const link = (judgeId, uid) => {
    if (judgeToUid.has(judgeId) || uidToJudge.has(uid)) return;
    judgeToUid.set(judgeId, uid);
    uidToJudge.set(uid, judgeId);
  };

  // Pass 0 — the judge doc is already keyed by Auth UID. True for any judge
  // rekeyed by scripts/migrate-judge-identity.js, and the end state once that
  // migration completes; then attribution is exact with no inference at all.
  for (const j of judges) {
    if (byUid.has(j.id)) link(j.id, j.id);
  }

  // Pass 1 — explicit uid on the judge doc.
  for (const j of judges) {
    if (typeof j.uid === "string" && j.uid) link(j.id, j.uid);
  }

  // Pass 2 — judgeIndex published by the assignment run.
  if (judgeIndex && typeof judgeIndex === "object") {
    const judgeIds = new Set(judges.map((j) => j.id));
    for (const [uid, info] of Object.entries(judgeIndex)) {
      const docId = typeof info === "string" ? info : info?.judgeDocId ?? info?.docId;
      if (docId && judgeIds.has(docId)) link(docId, uid);
    }
  }

  // Pass 3 — constraint propagation over assignedProjects.
  let changed = true;
  while (changed) {
    changed = false;
    for (const entry of byUid.values()) {
      if (uidToJudge.has(entry.uid)) continue;
      const candidates = [];
      for (const j of judges) {
        if (judgeToUid.has(j.id)) continue;
        const assigned = Array.isArray(j.assignedProjects) ? j.assignedProjects : null;
        if (!assigned) continue;
        if (entry.tracks.size > 0) {
          const tracks = judgeTracksOf(j);
          if (!tracks.some((t) => entry.tracks.has(t))) continue;
        }
        let ok = true;
        for (const p of entry.projects) {
          if (!assigned.includes(p)) {
            ok = false;
            break;
          }
        }
        if (ok) candidates.push(j.id);
        if (candidates.length > 1) break;
      }
      if (candidates.length === 1) {
        link(candidates[0], entry.uid);
        changed = true;
      }
    }
  }

  const unattributedUids = [...byUid.keys()].filter((uid) => !uidToJudge.has(uid));

  return {
    judgeToUid,
    submittedByJudge: new Map(
      [...judgeToUid.entries()].map(([judgeId, uid]) => [judgeId, byUid.get(uid)?.projects ?? new Set()])
    ),
    unattributedUids,
  };
}

/* ────────────────────────────────────────────────────────────────────────────
   Data subscriptions
   ──────────────────────────────────────────────────────────────────────────── */

const EMPTY = [];

function useOrganizerData() {
  const [data, setData] = useState({
    judges: null,
    projects: null,
    evaluations: null,
    checkins: null,
    runs: null,
  });
  const [errors, setErrors] = useState({});

  useEffect(() => {
    const sources = {
      judges: collection(db, "judges"),
      projects: collection(db, "projects"),
      evaluations: collection(db, "evaluations"),
      checkins: collection(db, "checkins"),
      runs: query(collection(db, "runs"), orderBy("generatedAt", "desc"), limit(20)),
    };

    const unsubs = Object.entries(sources).map(([key, ref]) =>
      onSnapshot(
        ref,
        (snap) => {
          const rows = snap.docs.map((d) => ({ id: d.id, ...d.data() }));
          setData((prev) => ({ ...prev, [key]: rows }));
          setErrors((prev) => (prev[key] ? { ...prev, [key]: null } : prev));
        },
        (err) => {
          console.error(`Organizer: ${key} subscription failed:`, err);
          setData((prev) => ({ ...prev, [key]: prev[key] ?? EMPTY }));
          setErrors((prev) => ({ ...prev, [key]: errText(err) }));
        }
      )
    );

    return () => unsubs.forEach((u) => u());
  }, []);

  return { data, errors };
}

/* ────────────────────────────────────────────────────────────────────────────
   Shared presentational bits
   ──────────────────────────────────────────────────────────────────────────── */

function Banner({ kind = "info", title, children, onDismiss }) {
  const icon = kind === "err" ? "!" : kind === "warn" ? "!" : kind === "ok" ? "✓" : "i";
  return (
    <div className={`og-banner ${kind}`} role={kind === "err" ? "alert" : "status"}>
      <span className="og-banner-icon" aria-hidden="true">
        {icon}
      </span>
      <div className="og-banner-body">
        {title && <div className="og-banner-title">{title}</div>}
        {children}
      </div>
      {onDismiss && (
        <button type="button" className="og-banner-close" onClick={onDismiss} aria-label="Dismiss message">
          ×
        </button>
      )}
    </div>
  );
}

function Stat({ value, label, sub, tone }) {
  return (
    <div className="og-stat">
      <span className={`og-stat-value${tone ? ` ${tone}` : ""}`}>{value}</span>
      <span className="og-stat-label">{label}</span>
      {sub && <span className="og-stat-sub">{sub}</span>}
    </div>
  );
}

function TrackBar({ name, done, total, dangerWhenIncomplete }) {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  const complete = total > 0 && done === total;
  const cls = complete ? "complete" : dangerWhenIncomplete && pct < 50 ? "danger" : "";
  return (
    <div className="og-track-cell">
      <div className="og-track-cell-top">
        <span className="og-track-cell-name" title={name}>
          {name}
        </span>
        <span className="og-track-cell-val">
          {done}/{total}
        </span>
      </div>
      <div className="og-bar-track">
        <div className={`og-bar-fill ${cls}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────────────────
   Section 1 — Check-in
   ──────────────────────────────────────────────────────────────────────────── */

const CHECKIN_FILTERS = ["All", "Not checked in", "Checked in", "Floaters"];

function CheckInSection({ judges, checkins, loading, loadError, cutoffValue, onCutoffChange, now }) {
  const [search, setSearch] = useState("");
  const [trackFilter, setTrackFilter] = useState("All tracks");
  const [statusFilter, setStatusFilter] = useState("All");
  const [pendingId, setPendingId] = useState(null);
  const [writeError, setWriteError] = useState(null);
  const [notice, setNotice] = useState(null);

  const cutoffDate = useMemo(() => {
    const d = new Date(cutoffValue);
    return Number.isNaN(d.getTime()) ? null : d;
  }, [cutoffValue]);

  const cutoffPassed = cutoffDate ? now >= cutoffDate.getTime() : false;

  const checkinById = useMemo(() => {
    const m = new Map();
    for (const c of checkins) m.set(c.id, c);
    return m;
  }, [checkins]);

  const activeCheckins = useMemo(
    () => checkins.filter((c) => c.checkedIn !== false),
    [checkins]
  );

  const trackStats = useMemo(() => {
    const m = new Map();
    for (const j of judges) {
      for (const t of judgeTracksOf(j).length ? judgeTracksOf(j) : ["(no track)"]) {
        const b = m.get(t) ?? { track: t, total: 0, checked: 0 };
        b.total += 1;
        const c = checkinById.get(j.id);
        if (c && c.checkedIn !== false) b.checked += 1;
        m.set(t, b);
      }
    }
    return [...m.values()].sort((a, b) => a.track.localeCompare(b.track));
  }, [judges, checkinById]);

  const allTracks = useMemo(() => ["All tracks", ...trackStats.map((t) => t.track)], [trackStats]);

  const floaterCount = activeCheckins.filter((c) => c.floater === true).length;

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return judges
      .filter((j) => {
        const c = checkinById.get(j.id);
        const isIn = !!c && c.checkedIn !== false;
        if (statusFilter === "Checked in" && !isIn) return false;
        if (statusFilter === "Not checked in" && isIn) return false;
        if (statusFilter === "Floaters" && !(isIn && c.floater === true)) return false;
        if (trackFilter !== "All tracks" && !judgeTracksOf(j).includes(trackFilter)) return false;
        if (!q) return true;
        return (
          (j.name || "").toLowerCase().includes(q) ||
          (j.email || "").toLowerCase().includes(q) ||
          j.id.toLowerCase().includes(q) ||
          judgeTracksOf(j).some((t) => t.toLowerCase().includes(q))
        );
      })
      .sort((a, b) => (a.name || a.id).localeCompare(b.name || b.id));
  }, [judges, checkinById, search, trackFilter, statusFilter]);

  const handleCheckIn = async (judge) => {
    setPendingId(judge.id);
    setWriteError(null);
    try {
      await setDoc(doc(db, "checkins", judge.id), {
        judgeId: judge.id,
        name: judge.name ?? "",
        email: judge.email ?? "",
        track: judge.track ?? judgeTracksOf(judge)[0] ?? "",
        checkedIn: true,
        checkedInAt: serverTimestamp(),
        floater: cutoffPassed,
        cutoffAt: cutoffDate ? Timestamp.fromDate(cutoffDate) : null,
        checkedInBy: auth.currentUser?.email ?? null,
      });
      setNotice(
        cutoffPassed
          ? `${judge.name || judge.id} checked in after cutoff — flagged as a floater.`
          : `${judge.name || judge.id} checked in.`
      );
    } catch (err) {
      console.error("Check-in write failed:", err);
      setWriteError(`Could not check in ${judge.name || judge.id}: ${errText(err)}`);
    } finally {
      setPendingId(null);
    }
  };

  const handleUndo = async (judge) => {
    setPendingId(judge.id);
    setWriteError(null);
    try {
      await deleteDoc(doc(db, "checkins", judge.id));
      setNotice(`Check-in for ${judge.name || judge.id} undone.`);
    } catch (err) {
      console.error("Check-in undo failed:", err);
      setWriteError(`Could not undo check-in for ${judge.name || judge.id}: ${errText(err)}`);
    } finally {
      setPendingId(null);
    }
  };

  const handleToggleFloater = async (judge, next) => {
    setPendingId(judge.id);
    setWriteError(null);
    try {
      await updateDoc(doc(db, "checkins", judge.id), { floater: next });
      setNotice(`${judge.name || judge.id} ${next ? "marked as" : "cleared as"} floater.`);
    } catch (err) {
      console.error("Floater toggle failed:", err);
      setWriteError(`Could not update floater flag for ${judge.name || judge.id}: ${errText(err)}`);
    } finally {
      setPendingId(null);
    }
  };

  if (loading) {
    return (
      <div className="og-card">
        <div className="og-empty">Loading judges…</div>
      </div>
    );
  }

  return (
    <>
      {loadError && (
        <Banner kind="err" title="Could not load check-in data">
          {loadError}
        </Banner>
      )}
      {writeError && (
        <Banner kind="err" title="Write failed" onDismiss={() => setWriteError(null)}>
          {writeError}
        </Banner>
      )}
      {notice && (
        <Banner kind="ok" onDismiss={() => setNotice(null)}>
          {notice}
        </Banner>
      )}

      {/* Cutoff */}
      <div className="og-card">
        <div className="og-card-head">
          <span className="og-card-title">Check-in cutoff</span>
        </div>
        <div className="og-cutoff">
          <div className="og-cutoff-main">
            <span className="og-cutoff-time">{cutoffDate ? fmtTime(cutoffDate) : "Not set"}</span>
            <span
              className={`og-cutoff-state ${cutoffPassed ? "passed" : "open"}`}
              aria-live="polite"
            >
              {!cutoffDate
                ? "Invalid"
                : cutoffPassed
                  ? `Passed ${fmtSpan(now - cutoffDate.getTime())} ago`
                  : `Open · ${fmtSpan(cutoffDate.getTime() - now)} left`}
            </span>
          </div>
          <div className="og-cutoff-edit">
            <div>
              <label className="og-label" htmlFor="og-cutoff-input">
                Cutoff time
              </label>
              <input
                id="og-cutoff-input"
                className="og-input"
                type="datetime-local"
                value={cutoffValue}
                onChange={(e) => onCutoffChange(e.target.value)}
              />
            </div>
          </div>
        </div>
        <p className="og-inline-note">
          {cutoffPassed
            ? "Anyone checked in from now on is flagged as a floater: they fill coverage gaps rather than entering the core assignment design."
            : "Judges checked in before the cutoff enter the core assignment design. Late arrivals are flagged as floaters."}{" "}
          The flag is written onto the check-in record at the moment of check-in, alongside the cutoff that was in force.
        </p>
      </div>

      {/* Counts */}
      <div className="og-stats" aria-live="polite">
        <Stat
          value={`${activeCheckins.length} of ${judges.length}`}
          label="Checked in"
          sub={judges.length ? `${Math.round((activeCheckins.length / judges.length) * 100)}% of expected roster` : undefined}
          tone="green"
        />
        <Stat value={judges.length - activeCheckins.length} label="Not yet arrived" tone="gold" />
        <Stat value={floaterCount} label="Floaters" sub="Arrived after cutoff" tone={floaterCount ? "gold" : undefined} />
        <Stat value={trackStats.length} label="Tracks represented" />
      </div>

      <div className="og-card">
        <div className="og-card-head">
          <span className="og-card-title">By track</span>
          <span className="og-card-note">checked in / expected</span>
        </div>
        {trackStats.length === 0 ? (
          <div className="og-empty">No judges in the roster yet.</div>
        ) : (
          <div className="og-track-grid">
            {trackStats.map((t) => (
              <TrackBar key={t.track} name={t.track} done={t.checked} total={t.total} />
            ))}
          </div>
        )}
      </div>

      {/* Roster */}
      <div className="og-card">
        <div className="og-card-head">
          <span className="og-card-title">Judge roster</span>
          <span className="og-card-note">
            {filtered.length} shown of {judges.length}
          </span>
        </div>

        <div className="og-filters">
          <div className="og-filters-search">
            <label className="og-label" htmlFor="og-judge-search">
              Search judges
            </label>
            <input
              id="og-judge-search"
              className="og-input"
              type="search"
              placeholder="Name, email or track…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <div>
            <label className="og-label" htmlFor="og-track-filter">
              Track
            </label>
            <select
              id="og-track-filter"
              className="og-input"
              value={trackFilter}
              onChange={(e) => setTrackFilter(e.target.value)}
            >
              {allTracks.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="og-pills" style={{ marginBottom: 14 }} role="group" aria-label="Filter by check-in status">
          {CHECKIN_FILTERS.map((f) => (
            <button
              key={f}
              type="button"
              className="og-pill"
              aria-pressed={statusFilter === f}
              onClick={() => setStatusFilter(f)}
            >
              {f}
            </button>
          ))}
        </div>

        {filtered.length === 0 ? (
          <div className="og-empty">
            {judges.length === 0
              ? "The judges collection is empty."
              : "No judges match these filters."}
          </div>
        ) : (
          <div className="og-list">
            {filtered.map((j) => {
              const c = checkinById.get(j.id);
              const isIn = !!c && c.checkedIn !== false;
              const busy = pendingId === j.id;
              const at = toDate(c?.checkedInAt);
              return (
                <div className="og-row" key={j.id}>
                  <div className="og-row-main">
                    <div className="og-row-name">{j.name || j.id}</div>
                    <div className="og-row-meta">
                      {judgeTracksOf(j).join(", ") || "no track"}
                      {j.email ? ` · ${j.email}` : ""}
                      {isIn && at ? ` · in at ${fmtTime(at)}` : ""}
                    </div>
                  </div>
                  <div className="og-row-right">
                    {isIn && c.floater === true && <span className="og-tag gold">Floater</span>}
                    {isIn ? (
                      <>
                        <span className="og-tag green">Checked in</span>
                        <button
                          type="button"
                          className="og-btn secondary tiny"
                          disabled={busy}
                          onClick={() => handleToggleFloater(j, c.floater !== true)}
                        >
                          {c.floater === true ? "Clear floater" : "Mark floater"}
                        </button>
                        <button
                          type="button"
                          className="og-btn secondary"
                          disabled={busy}
                          onClick={() => handleUndo(j)}
                        >
                          {busy ? "Working…" : "Undo"}
                        </button>
                      </>
                    ) : (
                      <button
                        type="button"
                        className="og-btn"
                        disabled={busy}
                        onClick={() => handleCheckIn(j)}
                      >
                        {busy ? "Working…" : "Check in"}
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </>
  );
}

/* ────────────────────────────────────────────────────────────────────────────
   Section 2 — Assignment status
   ──────────────────────────────────────────────────────────────────────────── */

function AssignmentSection({ runs, loading, loadError, judges, checkins }) {
  const judgeName = useCallback(
    (id) => {
      const j = judges.find((x) => x.id === id);
      return j?.name || id;
    },
    [judges]
  );

  const latest = runs[0] ?? null;
  const previous = runs.slice(1);

  const generatedAt = toDate(latest?.generatedAt);

  // Judges who checked in AFTER the run was generated are not in it. This is
  // the 2026 failure in its other direction — the roster the solver saw and the
  // people in the room drifting apart.
  const lateArrivals = useMemo(() => {
    if (!generatedAt) return [];
    return checkins.filter((c) => {
      if (c.checkedIn === false) return false;
      const at = toDate(c.checkedInAt);
      return at && at.getTime() > generatedAt.getTime();
    });
  }, [checkins, generatedAt]);

  const activeCheckinCount = checkins.filter((c) => c.checkedIn !== false).length;

  if (loading) {
    return (
      <div className="og-card">
        <div className="og-empty">Loading assignment runs…</div>
      </div>
    );
  }

  if (loadError) {
    return (
      <Banner kind="err" title="Could not load assignment runs">
        {loadError}
      </Banner>
    );
  }

  if (!latest) {
    return (
      <>
        <Banner kind="warn" title="No assignment run has been recorded">
          Nothing has been written to the <span className="og-code">runs</span> collection yet, so this
          console cannot tell you whether assignments exist or whether they are safe to judge against.
          <ul className="og-ul">
            <li>
              The Python solver in <span className="og-code">pipeline/assignment/</span> writes a{" "}
              <span className="og-code">runs/&#123;runId&#125;</span> document after each solve.
            </li>
            <li>
              The document shape is specified in <span className="og-code">docs/runs-contract.md</span>.
            </li>
            <li>
              This page never runs the solver — there is no backend to call it from. It only reads what
              the job reports.
            </li>
          </ul>
        </Banner>
        <div className="og-card">
          <div className="og-card-head">
            <span className="og-card-title">Currently in the room</span>
          </div>
          <div className="og-stats" style={{ marginBottom: 0 }}>
            <Stat value={activeCheckinCount} label="Judges checked in" />
            <Stat value={judges.length} label="Judges on roster" />
          </div>
          <p className="og-inline-note">
            Run the solver against the checked-in list — never against the full roster. That single
            substitution is what 2026 got wrong.
          </p>
        </div>
      </>
    );
  }

  const connectivityOk = latest.connectivityOk === true;
  const connectivityKnown = typeof latest.connectivityOk === "boolean";
  const coverage = latest.coverage ?? {};
  const histogram = coverage.histogram ?? {};
  const tracks = Array.isArray(latest.tracks) ? latest.tracks : [];
  const warnings = Array.isArray(latest.warnings) ? latest.warnings : [];
  const errorsList = Array.isArray(latest.errors) ? latest.errors : [];
  const config = latest.config ?? {};
  const belowTarget = Array.isArray(coverage.belowTarget) ? coverage.belowTarget : [];

  const histEntries = Object.entries(histogram)
    .map(([k, v]) => [Number(k), Number(v)])
    .filter(([k, v]) => Number.isFinite(k) && Number.isFinite(v))
    .sort((a, b) => a[0] - b[0]);
  const histMax = histEntries.reduce((m, [, v]) => Math.max(m, v), 0);
  const target = config.minJudgesPerProject ?? latest.minJudgesPerProject ?? DEFAULT_TARGET_JUDGES;

  const failingTracks = tracks.filter((t) => t.connected === false);

  return (
    <>
      {/* The headline gate */}
      {connectivityKnown && !connectivityOk ? (
        <Banner kind="err" title="CONNECTIVITY FAILED — do not judge against this assignment">
          The judge–project graph splits into islands, which means judge leniency cannot be estimated
          and cross-judge score normalization is mathematically impossible. Regenerate the assignment
          with more anchor projects per track before judging starts.
          {failingTracks.length > 0 && (
            <ul className="og-ul">
              {failingTracks.map((t) => (
                <li key={t.track}>
                  <strong>{t.track}</strong>: {t.components ?? "?"} islands across {t.judges ?? "?"}{" "}
                  judges / {t.projects ?? "?"} projects
                  {Array.isArray(t.isolatedJudges) && t.isolatedJudges.length > 0 && (
                    <>
                      {" — "}stranded with no projects at all:{" "}
                      {t.isolatedJudges.map(judgeName).join(", ")}
                    </>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Banner>
      ) : connectivityKnown ? (
        <Banner kind="ok" title="Connectivity OK">
          Every track's judge–project graph is a single connected component. Score normalization is
          possible.
        </Banner>
      ) : (
        <Banner kind="warn" title="Connectivity not reported">
          This run document has no <span className="og-code">connectivityOk</span> field, so the gate
          that would have caught 2026 did not run. Treat the assignment as unverified.
        </Banner>
      )}

      {errorsList.length > 0 && (
        <Banner kind="err" title="The run reported errors">
          <ul className="og-ul">
            {errorsList.map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        </Banner>
      )}

      {lateArrivals.length > 0 && (
        <Banner kind="warn" title={`${lateArrivals.length} judge(s) checked in after this run was generated`}>
          They are not in the assignment. Either regenerate, or deploy them as floaters onto the
          under-target projects listed under Live coverage.
          <ul className="og-ul">
            {lateArrivals.slice(0, 12).map((c) => (
              <li key={c.id}>
                {c.name || c.id}
                {c.track ? ` · ${c.track}` : ""} · in at {fmtTime(toDate(c.checkedInAt))}
              </li>
            ))}
            {lateArrivals.length > 12 && <li>…and {lateArrivals.length - 12} more</li>}
          </ul>
        </Banner>
      )}

      {typeof latest.judgeCount === "number" && latest.judgeCount !== activeCheckinCount && (
        <Banner kind="warn" title="Roster drift">
          This run was solved for <strong>{latest.judgeCount}</strong> judges, but{" "}
          <strong>{activeCheckinCount}</strong> are currently checked in. In 2026 the solver was fed 97
          judges while 63 showed up; that gap is exactly what fragmented the graph.
        </Banner>
      )}

      <div className="og-stats">
        <Stat value={fmtDateTime(generatedAt)} label="Generated" sub={latest.id} />
        <Stat value={latest.judgeCount ?? "—"} label="Judges in run" />
        <Stat value={latest.projectCount ?? "—"} label="Projects in run" />
        <Stat
          value={connectivityKnown ? (connectivityOk ? "PASS" : "FAIL") : "?"}
          label="Connectivity"
          tone={connectivityKnown ? (connectivityOk ? "green" : "red") : "gold"}
        />
      </div>

      <div className="og-card">
        <div className="og-card-head">
          <span className="og-card-title">Solver configuration</span>
          <span className="og-card-note">{latest.source || "unknown source"}</span>
        </div>
        <div className="og-stats" style={{ marginBottom: 0 }}>
          <Stat value={target} label="Target judges / project" />
          <Stat value={config.anchorsPerTrack ?? latest.anchorsPerTrack ?? "—"} label="Anchors per track" />
          <Stat value={config.maxProjectsPerJudge ?? "none"} label="Max projects / judge" />
          <Stat value={latest.status ?? (connectivityOk ? "ok" : "unknown")} label="Run status" />
        </div>
      </div>

      {/* Coverage histogram */}
      <div className="og-card">
        <div className="og-card-head">
          <span className="og-card-title">Coverage histogram — judges per project</span>
          <span className="og-card-note">
            {coverage.min != null && `min ${coverage.min} · median ${coverage.median ?? "—"} · max ${coverage.max ?? "—"}`}
          </span>
        </div>
        {histEntries.length === 0 ? (
          <div className="og-empty">This run did not report a coverage histogram.</div>
        ) : (
          <div className="og-hist">
            {histEntries.map(([judgesPer, count]) => (
              <div className="og-hist-row" key={judgesPer}>
                <span className="og-hist-key">
                  {judgesPer} judge{judgesPer === 1 ? "" : "s"}
                </span>
                <div className="og-hist-bar-wrap">
                  <div
                    className={`og-hist-bar ${judgesPer < target ? "bad" : judgesPer >= target ? "good" : ""}`}
                    style={{ width: histMax > 0 ? `${Math.max(2, (count / histMax) * 100)}%` : "0%" }}
                  />
                </div>
                <span className="og-hist-val">
                  {count} proj{count === 1 ? "" : "s"}
                </span>
              </div>
            ))}
          </div>
        )}
        {belowTarget.length > 0 && (
          <p className="og-inline-note">
            <strong style={{ color: "#b3261e" }}>{belowTarget.length} project(s)</strong> below the
            target of {target} judges in this assignment.
          </p>
        )}
      </div>

      {/* Per-track table */}
      {tracks.length > 0 && (
        <div className="og-card">
          <div className="og-card-head">
            <span className="og-card-title">Per-track preflight</span>
          </div>
          <div className="og-table-wrap">
            <table className="og-table">
              <thead>
                <tr>
                  <th>Track</th>
                  <th className="right">Judges</th>
                  <th className="right">Projects</th>
                  <th className="right">Islands</th>
                  <th className="right">Overlap</th>
                  <th className="center">Connected</th>
                </tr>
              </thead>
              <tbody>
                {tracks.map((t) => (
                  <tr key={t.track}>
                    <td>{t.track}</td>
                    <td className="right og-mono">{t.judges ?? "—"}</td>
                    <td className="right og-mono">{t.projects ?? "—"}</td>
                    <td className="right og-mono">{t.components ?? "—"}</td>
                    <td className="right og-mono">
                      {typeof t.overlapPct === "number" ? `${t.overlapPct}%` : "—"}
                    </td>
                    <td className="center">
                      {t.connected === false ? (
                        <span className="og-tag red">Disconnected</span>
                      ) : t.connected === true ? (
                        <span className="og-tag green">OK</span>
                      ) : (
                        <span className="og-tag">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {warnings.length > 0 && (
        <div className="og-card">
          <div className="og-card-head">
            <span className="og-card-title">Warnings from the run ({warnings.length})</span>
          </div>
          <ul className="og-ul" style={{ fontSize: 13, color: "#5a534a" }}>
            {warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      {previous.length > 0 && (
        <div className="og-card">
          <div className="og-card-head">
            <span className="og-card-title">Previous runs</span>
          </div>
          <div className="og-table-wrap">
            <table className="og-table">
              <thead>
                <tr>
                  <th>Run</th>
                  <th>Generated</th>
                  <th className="right">Judges</th>
                  <th className="right">Projects</th>
                  <th className="center">Connectivity</th>
                </tr>
              </thead>
              <tbody>
                {previous.map((r) => (
                  <tr key={r.id}>
                    <td className="og-mono">{r.id}</td>
                    <td>{fmtDateTime(toDate(r.generatedAt))}</td>
                    <td className="right og-mono">{r.judgeCount ?? "—"}</td>
                    <td className="right og-mono">{r.projectCount ?? "—"}</td>
                    <td className="center">
                      {r.connectivityOk === true ? (
                        <span className="og-tag green">OK</span>
                      ) : r.connectivityOk === false ? (
                        <span className="og-tag red">Failed</span>
                      ) : (
                        <span className="og-tag">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  );
}

/* ────────────────────────────────────────────────────────────────────────────
   Section 3 — Live coverage
   ──────────────────────────────────────────────────────────────────────────── */

function CoverageSection({ projects, evaluations, judges, checkins, runs, loading, loadError }) {
  const [showAllUnder, setShowAllUnder] = useState(false);

  const latestRun = runs[0] ?? null;
  const target =
    latestRun?.config?.minJudgesPerProject ??
    latestRun?.minJudgesPerProject ??
    DEFAULT_TARGET_JUDGES;

  const judgesPerProject = useMemo(() => {
    const m = new Map();
    for (const ev of evaluations) {
      if (!ev.projectId || !ev.judgeId) continue;
      let s = m.get(ev.projectId);
      if (!s) {
        s = new Set();
        m.set(ev.projectId, s);
      }
      s.add(ev.judgeId);
    }
    return m;
  }, [evaluations]);

  const scored = (id) => judgesPerProject.get(id)?.size ?? 0;

  const trackProgress = useMemo(() => {
    const m = new Map();
    for (const p of projects) {
      const n = scored(p.id);
      for (const t of projectTracksOf(p)) {
        const b = m.get(t) ?? { track: t, total: 0, met: 0, unscored: 0 };
        b.total += 1;
        if (n >= target) b.met += 1;
        if (n === 0) b.unscored += 1;
        m.set(t, b);
      }
    }
    return [...m.values()].sort(
      (a, b) => a.met / (a.total || 1) - b.met / (b.total || 1) || a.track.localeCompare(b.track)
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projects, judgesPerProject, target]);

  const underTarget = useMemo(() => {
    return projects
      .map((p) => ({ project: p, n: scored(p.id) }))
      .filter((x) => x.n < target)
      .sort(
        (a, b) =>
          a.n - b.n ||
          (a.project.tableNumber ?? Number.MAX_SAFE_INTEGER) -
            (b.project.tableNumber ?? Number.MAX_SAFE_INTEGER)
      );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projects, judgesPerProject, target]);

  const attribution = useMemo(
    () => attributeEvaluations(judges, evaluations, latestRun?.judgeIndex),
    [judges, evaluations, latestRun]
  );

  const activeCheckins = useMemo(() => checkins.filter((c) => c.checkedIn !== false), [checkins]);

  const judgeById = useMemo(() => new Map(judges.map((j) => [j.id, j])), [judges]);

  const silentJudges = useMemo(() => {
    return activeCheckins
      .filter((c) => (attribution.submittedByJudge.get(c.id)?.size ?? 0) === 0)
      .sort((a, b) => (a.name || a.id).localeCompare(b.name || b.id));
  }, [activeCheckins, attribution]);

  const partialJudges = useMemo(() => {
    const rows = [];
    for (const c of activeCheckins) {
      const done = attribution.submittedByJudge.get(c.id)?.size ?? 0;
      if (done === 0) continue;
      const assigned = judgeById.get(c.id)?.assignedProjects?.length ?? 0;
      if (assigned > 0 && done < assigned) rows.push({ checkin: c, done, assigned });
    }
    return rows.sort((a, b) => a.done / a.assigned - b.done / b.assigned);
  }, [activeCheckins, attribution, judgeById]);

  const totalProjects = projects.length;
  const metProjects = projects.filter((p) => scored(p.id) >= target).length;
  const unscoredProjects = projects.filter((p) => scored(p.id) === 0).length;

  if (loading) {
    return (
      <div className="og-card">
        <div className="og-empty">Loading live coverage…</div>
      </div>
    );
  }

  const visibleUnder = showAllUnder ? underTarget : underTarget.slice(0, 60);

  return (
    <>
      {loadError && (
        <Banner kind="err" title="Could not load live coverage data">
          {loadError}
        </Banner>
      )}

      <div className="og-stats" aria-live="polite">
        <Stat
          value={`${metProjects}/${totalProjects}`}
          label={`At target (${target}+ judges)`}
          tone={totalProjects > 0 && metProjects === totalProjects ? "green" : "gold"}
        />
        <Stat
          value={unscoredProjects}
          label="Completely unscored"
          tone={unscoredProjects > 0 ? "red" : "green"}
        />
        <Stat value={evaluations.length} label="Evaluations submitted" />
        <Stat
          value={silentJudges.length}
          label="Checked in, nothing submitted"
          tone={silentJudges.length > 0 ? "gold" : "green"}
        />
      </div>

      <div className="og-card">
        <div className="og-card-head">
          <span className="og-card-title">Per-track progress</span>
          <span className="og-card-note">projects at {target}+ judges / total</span>
        </div>
        {trackProgress.length === 0 ? (
          <div className="og-empty">No projects loaded.</div>
        ) : (
          <div className="og-track-grid">
            {trackProgress.map((t) => (
              <TrackBar key={t.track} name={t.track} done={t.met} total={t.total} dangerWhenIncomplete />
            ))}
          </div>
        )}
        <p className="og-inline-note">
          A project listing two tracks counts toward both, matching how the leaderboard aggregates.
        </p>
      </div>

      <div className="og-card">
        <div className="og-card-head">
          <span className="og-card-title">Projects below target — most urgent first</span>
          <span className="og-card-note">{underTarget.length} project(s)</span>
        </div>
        {underTarget.length === 0 ? (
          <div className="og-empty">Every project has reached {target} judges. Nothing to chase.</div>
        ) : (
          <>
            <div className="og-table-wrap">
              <table className="og-table">
                <thead>
                  <tr>
                    <th>Project</th>
                    <th className="center">Table</th>
                    <th>Tracks</th>
                    <th className="right">Judges</th>
                    <th className="right">Still needs</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleUnder.map(({ project, n }) => (
                    <tr key={project.id}>
                      <td>{project.name || project.id}</td>
                      <td className="center og-mono">{project.tableNumber ?? "—"}</td>
                      <td style={{ color: "#8a7f6e" }}>{projectTracksOf(project).join(", ")}</td>
                      <td className="right">
                        <span className={`og-tag ${n === 0 ? "red" : "gold"}`}>{n}</span>
                      </td>
                      <td className="right og-mono">{target - n}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {underTarget.length > visibleUnder.length && (
              <button
                type="button"
                className="og-btn secondary"
                style={{ marginTop: 12 }}
                onClick={() => setShowAllUnder(true)}
              >
                Show all {underTarget.length}
              </button>
            )}
          </>
        )}
      </div>

      <div className="og-card">
        <div className="og-card-head">
          <span className="og-card-title">Checked in, nothing submitted</span>
          <span className="og-card-note">{silentJudges.length} judge(s)</span>
        </div>

        {attribution.unattributedUids.length > 0 && (
          <Banner kind="warn" title="Attribution is approximate">
            {attribution.unattributedUids.length} submitting account(s) could not be matched to a judge
            record, so up to that many of the judges below may in fact have submitted. Evaluations are
            keyed by Auth UID while judge documents are keyed by username, and nothing joins them.
            Publish <span className="og-code">judgeIndex</span> in the run document (see{" "}
            <span className="og-code">docs/runs-contract.md</span>) or add a{" "}
            <span className="og-code">uid</span> field to judge docs for exact attribution.
          </Banner>
        )}

        {activeCheckins.length === 0 ? (
          <div className="og-empty">Nobody has checked in yet.</div>
        ) : silentJudges.length === 0 ? (
          <div className="og-empty">Every checked-in judge has submitted at least one evaluation.</div>
        ) : (
          <div className="og-list">
            {silentJudges.map((c) => {
              const at = toDate(c.checkedInAt);
              return (
                <div className="og-row" key={c.id}>
                  <div className="og-row-main">
                    <div className="og-row-name">{c.name || c.id}</div>
                    <div className="og-row-meta">
                      {c.track || "no track"}
                      {at ? ` · checked in ${fmtTime(at)}` : ""}
                      {judgeById.get(c.id)?.assignedProjects?.length
                        ? ` · ${judgeById.get(c.id).assignedProjects.length} assigned`
                        : ""}
                    </div>
                  </div>
                  <div className="og-row-right">
                    {c.floater === true && <span className="og-tag gold">Floater</span>}
                    <span className="og-tag red">0 submitted</span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {partialJudges.length > 0 && (
        <div className="og-card">
          <div className="og-card-head">
            <span className="og-card-title">Partway through</span>
            <span className="og-card-note">{partialJudges.length} judge(s)</span>
          </div>
          <div className="og-list">
            {partialJudges.map(({ checkin, done, assigned }) => (
              <div className="og-row" key={checkin.id}>
                <div className="og-row-main">
                  <div className="og-row-name">{checkin.name || checkin.id}</div>
                  <div className="og-row-meta">{checkin.track || "no track"}</div>
                </div>
                <div className="og-row-right">
                  <span className="og-tag blue">
                    {done} of {assigned}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  );
}

/* ────────────────────────────────────────────────────────────────────────────
   Page
   ──────────────────────────────────────────────────────────────────────────── */

export default function Organizer() {
  const navigate = useNavigate();
  const { data, errors } = useOrganizerData();
  const [tab, setTab] = useState("checkin");
  const [cutoffValue, setCutoffValue] = useState(defaultCutoffValue);
  const [now, setNow] = useState(() => Date.now());
  const tabRefs = useRef({});

  // Tick so "time until cutoff" stays honest without a reload.
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 15000);
    return () => clearInterval(id);
  }, []);

  const handleCutoffChange = useCallback((value) => {
    setCutoffValue(value);
    try {
      localStorage.setItem(CUTOFF_STORAGE_KEY, value);
    } catch (err) {
      console.error("Could not persist cutoff to localStorage:", err);
    }
  }, []);

  const handleSignOut = async () => {
    try {
      await signOut(auth);
    } catch (err) {
      console.error("Sign out failed:", err);
    }
    navigate("/", { replace: true });
  };

  const judges = data.judges ?? EMPTY;
  const projects = data.projects ?? EMPTY;
  const evaluations = data.evaluations ?? EMPTY;
  const checkins = data.checkins ?? EMPTY;
  const runs = data.runs ?? EMPTY;

  const bootLoading = data.judges === null && data.projects === null && !errors.judges;

  const checkedInCount = checkins.filter((c) => c.checkedIn !== false).length;

  const onTabKeyDown = (e) => {
    const i = TABS.findIndex((t) => t.id === tab);
    let next = null;
    if (e.key === "ArrowRight") next = TABS[(i + 1) % TABS.length];
    if (e.key === "ArrowLeft") next = TABS[(i - 1 + TABS.length) % TABS.length];
    if (!next) return;
    e.preventDefault();
    setTab(next.id);
    tabRefs.current[next.id]?.focus();
  };

  if (bootLoading) {
    return (
      <>
        <style>{styles}</style>
        <div className="og-loading">
          <div className="og-loading-inner" role="status">
            <div className="og-loading-ring" />
            Loading organizer console…
          </div>
        </div>
      </>
    );
  }

  const tabCounts = {
    checkin: `${checkedInCount}/${judges.length}`,
    assignments: runs.length ? null : "0",
    coverage: null,
  };

  return (
    <>
      <style>{styles}</style>
      <div className="og-root">
        <div className="og-topbar">
          <div className="og-topbar-left">
            <div className="og-topbar-icon">
              <svg viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
                <rect x="2" y="2" width="7" height="7" rx="1.5" fill="white" />
                <rect x="11" y="2" width="7" height="7" rx="1.5" fill="white" fillOpacity="0.5" />
                <rect x="2" y="11" width="7" height="7" rx="1.5" fill="white" fillOpacity="0.5" />
                <rect x="11" y="11" width="7" height="7" rx="1.5" fill="white" fillOpacity="0.75" />
              </svg>
            </div>
            <span className="og-topbar-wordmark">DataHacks Judging</span>
            <div className="og-topbar-sep" />
            <span className="og-topbar-badge">
              <span className="og-topbar-badge-dot" />
              Organizer
            </span>
          </div>
          <div className="og-topbar-right">
            <button type="button" className="og-btn-quiet" onClick={() => navigate("/leaderboard")}>
              Leaderboard
            </button>
            <button type="button" className="og-btn-quiet" onClick={handleSignOut}>
              Sign out
            </button>
          </div>
        </div>

        <div className="og-body">
          <div className="og-header">
            <div className="og-eyebrow">
              <span className="og-eyebrow-dot" />
              Event Operations · 2026
            </div>
            <h1 className="og-title">Organizer Console</h1>
            <p className="og-sub">
              Check judges in, verify the assignment is safe to judge against, and watch coverage fill in
              live.
            </p>
          </div>

          <div className="og-tabs" role="tablist" aria-label="Organizer sections" onKeyDown={onTabKeyDown}>
            {TABS.map((t) => (
              <button
                key={t.id}
                type="button"
                role="tab"
                id={`og-tab-${t.id}`}
                aria-selected={tab === t.id}
                aria-controls={`og-panel-${t.id}`}
                tabIndex={tab === t.id ? 0 : -1}
                ref={(el) => {
                  tabRefs.current[t.id] = el;
                }}
                className="og-tab"
                onClick={() => setTab(t.id)}
              >
                {t.label}
                {tabCounts[t.id] && <span className="og-tab-count">{tabCounts[t.id]}</span>}
              </button>
            ))}
          </div>

          <div id={`og-panel-${tab}`} role="tabpanel" aria-labelledby={`og-tab-${tab}`} tabIndex={-1}>
            {tab === "checkin" && (
              <CheckInSection
                judges={judges}
                checkins={checkins}
                loading={data.judges === null && !errors.judges}
                loadError={errors.judges || errors.checkins || null}
                cutoffValue={cutoffValue}
                onCutoffChange={handleCutoffChange}
                now={now}
              />
            )}

            {tab === "assignments" && (
              <AssignmentSection
                runs={runs}
                judges={judges}
                checkins={checkins}
                loading={data.runs === null && !errors.runs}
                loadError={errors.runs || null}
              />
            )}

            {tab === "coverage" && (
              <CoverageSection
                projects={projects}
                evaluations={evaluations}
                judges={judges}
                checkins={checkins}
                runs={runs}
                loading={data.projects === null && !errors.projects}
                loadError={errors.projects || errors.evaluations || errors.checkins || null}
              />
            )}
          </div>
        </div>
      </div>
    </>
  );
}
