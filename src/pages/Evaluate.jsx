import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { auth, db } from "../firebase";
import { getCriteriaForTrack, getDefaultScores, getMaxTotal, SCORE_GUIDE, getPerformanceNoteForTrack } from "../config/trackRubrics";
import {
  doc,
  getDoc,
  collection,
  query,
  where,
  getDocs,
  addDoc,
  updateDoc
} from "firebase/firestore";

const styles = `
  @import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=DM+Sans:wght@300;400;500;600&display=swap');

  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  .ev-root {
    min-height: 100vh;
    background: #F7F5F0;
    font-family: 'DM Sans', sans-serif;
    color: #1a1a1a;
  }

  /* ── Top bar ── */
  .ev-topbar {
    background: #fff;
    border-bottom: 1px solid #E8E4DC;
    padding: 0 40px;
    height: 60px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    position: sticky;
    top: 0;
    z-index: 100;
  }

  .ev-topbar-left {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .ev-topbar-icon {
    width: 30px;
    height: 30px;
    background: #1a1a1a;
    border-radius: 7px;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
  }

  .ev-topbar-icon svg { width: 16px; height: 16px; }

  .ev-topbar-wordmark {
    font-size: 14px;
    font-weight: 600;
    color: #1a1a1a;
    letter-spacing: -0.01em;
  }

  .ev-topbar-sep {
    width: 1px;
    height: 18px;
    background: #E0DBD2;
  }

  .ev-topbar-page {
    font-size: 13px;
    color: #8a7f6e;
    font-weight: 400;
  }

  .ev-topbar-right {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .ev-back-btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
    font-weight: 500;
    color: #5a534a;
    background: none;
    border: 1px solid #E0DBD2;
    border-radius: 8px;
    padding: 6px 14px;
    cursor: pointer;
    transition: background 0.15s, border-color 0.15s;
    font-family: 'DM Sans', sans-serif;
  }

  .ev-back-btn:hover {
    background: #F0EDE7;
    border-color: #ccc8c0;
  }

  /* ── Layout ── */
  .ev-body {
    max-width: 860px;
    margin: 0 auto;
    padding: 40px 24px 80px;
  }

  /* ── Project header ── */
  .ev-project-header {
    background: #fff;
    border: 1px solid #E8E4DC;
    border-radius: 14px;
    padding: 28px 32px;
    margin-bottom: 28px;
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 20px;
    animation: ev-fadeup 0.4s ease both;
  }

  .ev-project-meta { flex: 1; }

  .ev-project-eyebrow {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: #8a7f6e;
    margin-bottom: 10px;
  }

  .ev-project-eyebrow-dot {
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: #C9A96E;
  }

  .ev-project-name {
    font-family: 'DM Serif Display', serif;
    font-size: 28px;
    letter-spacing: -0.02em;
    line-height: 1.15;
    color: #111;
    margin-bottom: 8px;
  }

  .ev-project-desc {
    font-size: 14px;
    color: #7a736a;
    line-height: 1.6;
    font-weight: 300;
    max-width: 520px;
  }

  .ev-progress-pill {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 6px;
    flex-shrink: 0;
  }

  .ev-progress-label {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: #a09488;
  }

  .ev-progress-ring-wrap {
    position: relative;
    width: 64px;
    height: 64px;
  }

  .ev-progress-ring-wrap svg {
    transform: rotate(-90deg);
  }

  .ev-progress-ring-bg {
    fill: none;
    stroke: #EDE9E2;
    stroke-width: 5;
  }

  .ev-progress-ring-fill {
    fill: none;
    stroke: #C9A96E;
    stroke-width: 5;
    stroke-linecap: round;
    transition: stroke-dashoffset 0.5s ease;
  }

  .ev-progress-pct {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 13px;
    font-weight: 600;
    color: #1a1a1a;
  }

  /* ── Criteria cards ── */
  .ev-criteria-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    margin-bottom: 20px;
  }

  @media (max-width: 600px) {
    .ev-criteria-grid { grid-template-columns: 1fr; }
  }

  .ev-criterion {
    background: #fff;
    border: 1px solid #E8E4DC;
    border-radius: 14px;
    padding: 22px 24px;
    transition: border-color 0.2s, box-shadow 0.2s;
    animation: ev-fadeup 0.4s ease both;
  }

  .ev-criterion.scored {
    border-color: #BAD5C4;
    box-shadow: 0 2px 12px rgba(186,213,196,0.2);
  }

  .ev-criterion-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 6px;
  }

  .ev-criterion-label {
    font-size: 13px;
    font-weight: 600;
    color: #1a1a1a;
    letter-spacing: -0.01em;
  }

  .ev-criterion-check {
    width: 20px;
    height: 20px;
    border-radius: 50%;
    background: #BAD5C4;
    display: flex;
    align-items: center;
    justify-content: center;
    opacity: 0;
    transform: scale(0.6);
    transition: opacity 0.2s, transform 0.2s;
  }

  .ev-criterion.scored .ev-criterion-check {
    opacity: 1;
    transform: scale(1);
  }

  .ev-criterion-hint {
    font-size: 12px;
    color: #a09488;
    font-weight: 300;
    margin-bottom: 16px;
    line-height: 1.5;
  }

  /* Score buttons */
  .ev-scale {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(40px, 1fr));
    gap: 6px;
  }

  .ev-scale-btn {
    min-height: 44px;
    border: 1.5px solid #E0DBD2;
    background: #F7F5F0;
    border-radius: 9px;
    font-family: 'DM Sans', sans-serif;
    font-size: 14px;
    font-weight: 600;
    color: #8a7f6e;
    cursor: pointer;
    transition: all 0.15s ease;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  @media (max-width: 600px) {
    .ev-scale {
      grid-template-columns: repeat(auto-fill, minmax(36px, 1fr));
    }

    .ev-scale-btn {
      min-height: 40px;
      font-size: 13px;
    }
  }

  .ev-scale-btn:hover {
    background: #EDE9E2;
    border-color: #ccc8c0;
    color: #1a1a1a;
    transform: translateY(-2px);
  }

  .ev-scale-btn.active {
    background: #1a1a1a;
    border-color: #1a1a1a;
    color: #fff;
    transform: translateY(-2px);
    box-shadow: 0 4px 10px rgba(0,0,0,0.15);
  }

  /* ── Comments ── */
  .ev-comments-card {
    background: #fff;
    border: 1px solid #E8E4DC;
    border-radius: 14px;
    padding: 22px 24px;
    margin-bottom: 24px;
    animation: ev-fadeup 0.4s ease 0.15s both;
  }

  .ev-comments-label {
    font-size: 13px;
    font-weight: 600;
    color: #1a1a1a;
    margin-bottom: 4px;
  }

  .ev-comments-sub {
    font-size: 12px;
    color: #a09488;
    font-weight: 300;
    margin-bottom: 14px;
  }

  .ev-textarea {
    width: 100%;
    min-height: 100px;
    resize: vertical;
    border: 1.5px solid #E0DBD2;
    border-radius: 10px;
    background: #F7F5F0;
    font-family: 'DM Sans', sans-serif;
    font-size: 14px;
    font-weight: 300;
    color: #1a1a1a;
    padding: 14px 16px;
    line-height: 1.6;
    outline: none;
    transition: border-color 0.2s, box-shadow 0.2s;
  }

  .ev-textarea:focus {
    border-color: #C9A96E;
    box-shadow: 0 0 0 3px rgba(201,169,110,0.12);
    background: #fff;
  }

  .ev-textarea::placeholder { color: #c0b8ae; }

  /* ── Submit bar ── */
  .ev-submit-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    animation: ev-fadeup 0.4s ease 0.2s both;
  }

  .ev-score-preview {
    display: flex;
    align-items: baseline;
    gap: 6px;
  }

  .ev-score-preview-value {
    font-family: 'DM Serif Display', serif;
    font-size: 30px;
    letter-spacing: -0.02em;
    color: #111;
  }

  .ev-score-preview-denom {
    font-size: 13px;
    color: #a09488;
    font-weight: 400;
  }

  .ev-score-preview-label {
    font-size: 12px;
    color: #a09488;
    font-weight: 400;
    letter-spacing: 0.02em;
  }

  .ev-submit-btn {
    display: inline-flex;
    align-items: center;
    gap: 10px;
    padding: 14px 28px;
    background: #1a1a1a;
    color: #fff;
    border: none;
    border-radius: 10px;
    font-family: 'DM Sans', sans-serif;
    font-size: 15px;
    font-weight: 500;
    cursor: pointer;
    transition: background 0.2s, transform 0.15s, box-shadow 0.2s;
    box-shadow: 0 2px 8px rgba(0,0,0,0.12);
    letter-spacing: -0.01em;
  }

  .ev-submit-btn:hover:not(:disabled) {
    background: #2d2d2d;
    transform: translateY(-1px);
    box-shadow: 0 6px 18px rgba(0,0,0,0.18);
  }

  .ev-submit-btn:active:not(:disabled) {
    transform: translateY(0);
  }

  .ev-submit-btn:disabled {
    background: #d0ccc6;
    box-shadow: none;
    cursor: not-allowed;
  }

  .ev-submit-btn.saved {
    background: #4a7c5f;
  }

  .ev-submit-hint {
    font-size: 12px;
    color: #a09488;
    text-align: right;
  }

  /* ── Spinner ── */
  .ev-spinner {
    width: 16px;
    height: 16px;
    border: 2px solid rgba(255,255,255,0.35);
    border-top-color: #fff;
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
  }

  @keyframes spin { to { transform: rotate(360deg); } }

  /* ── Loading ── */
  .ev-loading {
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    background: #F7F5F0;
    font-family: 'DM Sans', sans-serif;
  }

  .ev-loading-inner {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 16px;
    color: #8a7f6e;
    font-size: 14px;
  }

  .ev-loading-ring {
    width: 36px;
    height: 36px;
    border: 3px solid #E8E4DC;
    border-top-color: #C9A96E;
    border-radius: 50%;
    animation: spin 0.9s linear infinite;
  }

  /* ── Animations ── */
  @keyframes ev-fadeup {
    from { opacity: 0; transform: translateY(14px); }
    to   { opacity: 1; transform: translateY(0); }
  }

  /* ── Rubric button ── */
  .ev-rubric-btn {
    display: inline-flex; align-items: center; gap: 6px;
    font-size: 13px; font-weight: 500; color: #5a534a;
    background: #fff; border: 1px solid #E0DBD2; border-radius: 8px;
    padding: 7px 14px; cursor: pointer; font-family: 'DM Sans', sans-serif;
    margin-bottom: 20px; transition: background 0.15s;
  }
  .ev-rubric-btn:hover { background: #F0EDE7; }

  /* ── Bottom sheet ── */
  .ev-sheet-backdrop {
    position: fixed; inset: 0; background: rgba(0,0,0,0.4); z-index: 200;
    display: flex; align-items: flex-end;
  }
  @keyframes ev-sheet-up {
    from { transform: translateY(100%); }
    to   { transform: translateY(0); }
  }
  .ev-sheet {
    background: #fff; border-radius: 20px 20px 0 0;
    width: 100%; max-height: 85vh; overflow-y: auto;
    padding: 12px 20px 48px;
    animation: ev-sheet-up 0.25s cubic-bezier(0.16,1,0.3,1) both;
  }
  .ev-sheet-handle {
    width: 36px; height: 4px; background: #E0DBD2; border-radius: 2px;
    margin: 0 auto 16px;
  }
  .ev-sheet-header {
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 20px; padding-bottom: 14px; border-bottom: 1px solid #F0EDE7;
  }
  .ev-sheet-title { font-size: 15px; font-weight: 600; color: #1a1a1a; }
  .ev-sheet-close {
    background: none; border: none; font-size: 16px; color: #8a7f6e;
    cursor: pointer; padding: 4px 8px; border-radius: 6px;
  }
  .ev-sheet-close:hover { background: #F0EDE7; }
  .ev-sheet-body { display: flex; flex-direction: column; gap: 0; }
  .ev-sheet-criterion {
    padding: 14px 0; border-bottom: 1px solid #F0EDE7;
  }
  .ev-sheet-criterion:last-child { border-bottom: none; }
  .ev-sheet-cname {
    font-size: 13px; font-weight: 600; color: #1a1a1a; margin-bottom: 4px;
  }
  .ev-sheet-cname span { font-weight: 400; color: #8a7f6e; font-size: 11px; margin-left: 4px; }
  .ev-sheet-chint { font-size: 12px; color: #5a534a; margin-bottom: 8px; line-height: 1.4; }
  .ev-sheet-levels { display: flex; flex-direction: column; gap: 5px; }
  .ev-sheet-level { display: flex; gap: 8px; align-items: flex-start; }
  .ev-sheet-range {
    font-size: 10px; font-weight: 700; color: #5a534a;
    background: #F0EDE7; border-radius: 4px; padding: 2px 6px;
    white-space: nowrap; flex-shrink: 0; margin-top: 1px;
  }
  .ev-sheet-desc { font-size: 12px; color: #3a3530; line-height: 1.4; }
  .ev-sheet-guide {
    margin-top: 20px; padding-top: 16px; border-top: 1px solid #F0EDE7;
  }
  .ev-sheet-guide-title {
    font-size: 10px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;
    color: #a09488; margin-bottom: 10px;
  }
  .ev-sheet-guide-levels { display: flex; flex-direction: column; gap: 5px; }
  .ev-sheet-guide-label { font-size: 11px; font-weight: 600; color: #5a534a; }
`;

function ProgressRing({ filled, total }) {
  const r = 26;
  const circ = 2 * Math.PI * r;
  const pct = total === 0 ? 0 : filled / total;
  const offset = circ * (1 - pct);
  return (
    <div className="ev-progress-ring-wrap">
      <svg width="64" height="64" viewBox="0 0 64 64">
        <circle className="ev-progress-ring-bg" cx="32" cy="32" r={r} />
        <circle
          className="ev-progress-ring-fill"
          cx="32"
          cy="32"
          r={r}
          strokeDasharray={circ}
          strokeDashoffset={offset}
        />
      </svg>
      <div className="ev-progress-pct">
        {filled}/{total}
      </div>
    </div>
  );
}

export default function Evaluate() {
  const { projectId } = useParams();

  const [project, setProject] = useState(null);
  const [judgeTrack, setJudgeTrack] = useState(null);
  const [scores, setScores] = useState({});
  const [comment, setComment] = useState("");
  const [existingId, setExistingId] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [saved, setSaved] = useState(false);
  const [rubricOpen, setRubricOpen] = useState(false);

  const criteria = getCriteriaForTrack(judgeTrack);
  const perfNote = getPerformanceNoteForTrack(judgeTrack);
  const maxTotal = getMaxTotal(criteria);

  const scoredCount = Object.values(scores).filter((v) => v !== null).length;
  const totalCount = criteria.length;
  const isComplete = scoredCount === totalCount;

  const totalScore = Object.values(scores).reduce(
    (sum, v) => sum + (v ?? 0),
    0
  );

  useEffect(() => {
    const load = async () => {
      const user = auth.currentUser;

      // Load project
      const projectDoc = await getDoc(doc(db, "projects", projectId));
      setProject(projectDoc.data());

      // Load judge track (UID first, fallback to email)
      let track = null;
      const judgeDoc = await getDoc(doc(db, "judges", user.uid));
      if (judgeDoc.exists()) {
        track = judgeDoc.data().track || null;
      } else {
        const eq = query(collection(db, "judges"), where("email", "==", user.email));
        const eSnap = await getDocs(eq);
        if (!eSnap.empty) track = eSnap.docs[0].data().track || null;
      }
      setJudgeTrack(track);

      // Seed scores from track criteria
      const trackCriteria = getCriteriaForTrack(track);
      setScores(getDefaultScores(trackCriteria));

      // Load existing evaluation
      const q = query(
        collection(db, "evaluations"),
        where("judgeId", "==", user.uid),
        where("projectId", "==", projectId)
      );
      const snap = await getDocs(q);
      if (!snap.empty) {
        const data = snap.docs[0];
        setExistingId(data.id);
        setScores(data.data().scores);
        setComment(data.data().comment || "");
      }
    };
    load();
  }, [projectId]);

  const handleScore = (field, value) => {
    setScores((prev) => ({ ...prev, [field]: value }));
  };

  const handleSubmit = async () => {
    if (!isComplete || submitting) return;
    setSubmitting(true);
    const user = auth.currentUser;
    const payload = {
      judgeId: user.uid,
      projectId,
      track: judgeTrack,
      scores,
      comment,
      timestamp: new Date()
    };
    try {
      if (existingId) {
        await updateDoc(doc(db, "evaluations", existingId), payload);
      } else {
        const ref = await addDoc(collection(db, "evaluations"), payload);
        setExistingId(ref.id);
      }
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } finally {
      setSubmitting(false);
    }
  };

  if (!project) {
    return (
      <>
        <style>{styles}</style>
        <div className="ev-loading">
          <div className="ev-loading-inner">
            <div className="ev-loading-ring" />
            Loading project…
          </div>
        </div>
      </>
    );
  }

  return (
    <>
      <style>{styles}</style>
      <div className="ev-root">
        {/* Top bar */}
        <div className="ev-topbar">
          <div className="ev-topbar-left">
            <div className="ev-topbar-icon">
              <svg viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
                <rect x="2" y="2" width="7" height="7" rx="1.5" fill="white"/>
                <rect x="11" y="2" width="7" height="7" rx="1.5" fill="white" fillOpacity="0.5"/>
                <rect x="2" y="11" width="7" height="7" rx="1.5" fill="white" fillOpacity="0.5"/>
                <rect x="11" y="11" width="7" height="7" rx="1.5" fill="white" fillOpacity="0.75"/>
              </svg>
            </div>
            <span className="ev-topbar-wordmark">DataHacks Judging</span>
            <div className="ev-topbar-sep" />
            <span className="ev-topbar-page">Evaluate Project</span>
          </div>
          <div className="ev-topbar-right">
            <button className="ev-back-btn" onClick={() => window.history.back()}>
              ← Back
            </button>
          </div>
        </div>

        <div className="ev-body">
          {/* Project header */}
          <div className="ev-project-header">
            <div className="ev-project-meta">
              <div className="ev-project-eyebrow">
                <span className="ev-project-eyebrow-dot" />
                Project Evaluation
              </div>
              <h1 className="ev-project-name">{project.name}</h1>
              {project.description && (
                <p className="ev-project-desc">{project.description}</p>
              )}
            </div>
            <div className="ev-progress-pill">
              <span className="ev-progress-label">Progress</span>
              <ProgressRing filled={scoredCount} total={totalCount} />
            </div>
          </div>

          {/* Rubric button */}
          <button className="ev-rubric-btn" onClick={() => setRubricOpen(true)}>
            📋 View Rubric
          </button>

          {/* Criteria grid */}
          <div className="ev-criteria-grid">
            {criteria.map((c) => (
              <div
                key={c.id}
                className={`ev-criterion${scores[c.id] !== null ? " scored" : ""}`}
              >
                <div className="ev-criterion-top">
                  <span className="ev-criterion-label">{c.label}</span>
                  <div className="ev-criterion-check">
                    <svg width="10" height="8" viewBox="0 0 10 8" fill="none">
                      <path d="M1 4l3 3 5-6" stroke="#2d6a46" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
                    </svg>
                  </div>
                </div>
                <p className="ev-criterion-hint">{c.hint}</p>
                <div className="ev-scale">
                  {Array.from({ length: c.maxScore }, (_, i) => i + 1).map((n) => (
                    <button
                      key={n}
                      className={`ev-scale-btn${scores[c.id] === n ? " active" : ""}`}
                      onClick={() => handleScore(c.id, n)}
                    >
                      {n}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>

          {/* Comments */}
          <div className="ev-comments-card">
            <div className="ev-comments-label">Judge Notes</div>
            <div className="ev-comments-sub">Optional — visible only to organizers</div>
            <textarea
              className="ev-textarea"
              placeholder="Share your observations, standout moments, or concerns about this project…"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
            />
          </div>

          {/* Submit bar */}
          <div className="ev-submit-bar">
            <div>
              {isComplete ? (
                <div className="ev-score-preview">
                  <span className="ev-score-preview-value">{totalScore}</span>
                  <span className="ev-score-preview-denom">/ {maxTotal}</span>
                  <span className="ev-score-preview-label">total score</span>
                </div>
              ) : (
                <p className="ev-submit-hint">
                  {totalCount - scoredCount} criterion{totalCount - scoredCount !== 1 ? "a" : "on"} remaining
                </p>
              )}
            </div>
            <button
              className={`ev-submit-btn${saved ? " saved" : ""}`}
              disabled={!isComplete || submitting}
              onClick={handleSubmit}
            >
              {submitting ? (
                <span className="ev-spinner" />
              ) : saved ? (
                <>
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                    <path d="M3 8.5l3.5 3.5 7-7" stroke="white" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                  Saved
                </>
              ) : (
                <>
                  {existingId ? "Update Evaluation" : "Submit Evaluation"}
                  <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                    <path d="M2 7h10M8 3l4 4-4 4" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                </>
              )}
            </button>
          </div>
        </div>

        {/* Rubric bottom sheet */}
        {rubricOpen && (
          <div className="ev-sheet-backdrop" onClick={() => setRubricOpen(false)}>
            <div className="ev-sheet" onClick={e => e.stopPropagation()}>
              <div className="ev-sheet-handle" />
              <div className="ev-sheet-header">
                <span className="ev-sheet-title">{judgeTrack || "Judging"} Rubric</span>
                <button className="ev-sheet-close" onClick={() => setRubricOpen(false)}>✕</button>
              </div>
              <div className="ev-sheet-body">
                {criteria.map(c => (
                  <div key={c.id} className="ev-sheet-criterion">
                    <div className="ev-sheet-cname">
                      {c.label}
                      <span>(1–{c.maxScore} pts)</span>
                    </div>
                    {c.hint && <div className="ev-sheet-chint">{c.hint}</div>}
                  </div>
                ))}
                {perfNote && (
                  <div className="ev-sheet-guide">
                    <div className="ev-sheet-guide-title">Performance Scoring (not judge-scored)</div>
                    <div className="ev-sheet-desc">{perfNote.description}</div>
                  </div>
                )}
                <div className="ev-sheet-guide">
                  <div className="ev-sheet-guide-title">Scoring Guide</div>
                  <div className="ev-sheet-guide-levels">
                    {SCORE_GUIDE.map(l => (
                      <div key={l.range} className="ev-sheet-level">
                        <span className="ev-sheet-range">{l.range}</span>
                        <span className="ev-sheet-desc">{l.label}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </>
  );
}
