import { useEffect, useState } from "react";
import { auth, db } from "../firebase";
import { doc, getDoc, collection, query, where, getDocs } from "firebase/firestore";
import { useNavigate } from "react-router-dom";

const styles = `
  @import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=DM+Sans:wght@300;400;500;600&display=swap');

  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  .db-root {
    min-height: 100vh;
    background: #F7F5F0;
    font-family: 'DM Sans', sans-serif;
    color: #1a1a1a;
  }

  /* ── Top bar ── */
  .db-topbar {
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

  .db-topbar-left {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .db-topbar-icon {
    width: 30px;
    height: 30px;
    background: #1a1a1a;
    border-radius: 7px;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
  }

  .db-topbar-icon svg { width: 16px; height: 16px; }

  .db-topbar-wordmark {
    font-size: 14px;
    font-weight: 600;
    color: #1a1a1a;
    letter-spacing: -0.01em;
  }

  .db-topbar-sep {
    width: 1px;
    height: 18px;
    background: #E0DBD2;
  }

  .db-topbar-page {
    font-size: 13px;
    color: #8a7f6e;
  }

  .db-topbar-right {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .db-sign-out-btn {
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

  .db-sign-out-btn:hover {
    background: #F0EDE7;
    border-color: #ccc8c0;
  }

  /* ── Body ── */
  .db-body {
    max-width: 900px;
    margin: 0 auto;
    padding: 40px 24px 80px;
  }

  /* ── Page header ── */
  .db-header {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    margin-bottom: 32px;
    animation: db-fadeup 0.4s ease both;
  }

  .db-header-eyebrow {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: #8a7f6e;
    margin-bottom: 8px;
  }

  .db-header-dot {
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: #C9A96E;
  }

  .db-header-title {
    font-family: 'DM Serif Display', serif;
    font-size: 32px;
    letter-spacing: -0.02em;
    color: #111;
  }

  .db-header-meta {
    font-size: 13px;
    color: #a09488;
    font-weight: 400;
    text-align: right;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .db-header-meta-line strong {
    color: #5a534a;
    font-weight: 600;
  }

  /* ── Stats row ── */
  .db-stats {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 14px;
    margin-bottom: 28px;
    animation: db-fadeup 0.4s ease 0.05s both;
  }

  .db-stat {
    background: #fff;
    border: 1px solid #E8E4DC;
    border-radius: 12px;
    padding: 18px 20px;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .db-stat-value {
    font-family: 'DM Serif Display', serif;
    font-size: 26px;
    letter-spacing: -0.02em;
    color: #111;
    line-height: 1;
  }

  .db-stat-value.gold { color: #C9A96E; }
  .db-stat-value.green { color: #4a7c5f; }

  .db-stat-label {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.07em;
    text-transform: uppercase;
    color: #a09488;
  }

  /* ── Progress bar ── */
  .db-progress-card {
    background: #fff;
    border: 1px solid #E8E4DC;
    border-radius: 12px;
    padding: 18px 24px;
    margin-bottom: 28px;
    animation: db-fadeup 0.4s ease 0.1s both;
  }

  .db-progress-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 12px;
  }

  .db-progress-label {
    font-size: 12px;
    font-weight: 600;
    color: #5a534a;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }

  .db-progress-pct {
    font-size: 13px;
    font-weight: 600;
    color: #1a1a1a;
  }

  .db-progress-track {
    height: 8px;
    background: #EDE9E2;
    border-radius: 100px;
    overflow: hidden;
  }

  .db-progress-fill {
    height: 100%;
    background: linear-gradient(90deg, #C9A96E, #a8845a);
    border-radius: 100px;
    transition: width 0.8s cubic-bezier(0.16, 1, 0.3, 1);
  }

  .db-progress-fill.complete {
    background: linear-gradient(90deg, #4a7c5f, #3a6b4f);
  }

  /* ── Section heading ── */
  .db-section-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 14px;
    animation: db-fadeup 0.4s ease 0.12s both;
  }

  .db-section-title {
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: #8a7f6e;
  }

  .db-filter-tabs {
    display: flex;
    gap: 4px;
  }

  .db-filter-tab {
    font-size: 12px;
    font-weight: 500;
    color: #8a7f6e;
    background: none;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 4px 10px;
    cursor: pointer;
    font-family: 'DM Sans', sans-serif;
    transition: all 0.15s;
  }

  .db-filter-tab:hover { background: #EDE9E2; }

  .db-filter-tab.active {
    background: #fff;
    border-color: #E0DBD2;
    color: #1a1a1a;
  }

  /* ── Project list ── */
  .db-project-list {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .db-project-card {
    background: #fff;
    border: 1px solid #E8E4DC;
    border-radius: 12px;
    padding: 16px 20px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    cursor: pointer;
    transition: border-color 0.2s, box-shadow 0.2s, transform 0.15s;
    animation: db-fadeup 0.4s ease both;
    text-align: left;
    width: 100%;
    font-family: 'DM Sans', sans-serif;
  }

  .db-project-card:hover {
    border-color: #ccc8c0;
    box-shadow: 0 4px 16px rgba(0,0,0,0.07);
    transform: translateY(-1px);
  }

  .db-project-card.done {
    border-color: #BAD5C4;
    background: #fafcfb;
  }

  .db-project-card.done:hover {
    border-color: #90c4a4;
  }

  .db-project-card-left {
    display: flex;
    align-items: center;
    gap: 14px;
    flex: 1;
    min-width: 0;
  }

  .db-project-index {
    width: 28px;
    height: 28px;
    border-radius: 7px;
    background: #F0EDE7;
    border: 1px solid #E0DBD2;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 12px;
    font-weight: 600;
    color: #8a7f6e;
    flex-shrink: 0;
    transition: background 0.2s, border-color 0.2s;
  }

  .db-project-card.done .db-project-index {
    background: #e0f0e8;
    border-color: #BAD5C4;
  }

  .db-project-info { min-width: 0; }

  .db-project-name {
    font-size: 15px;
    font-weight: 500;
    color: #1a1a1a;
    letter-spacing: -0.01em;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .db-project-title-row {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
  }

  .db-project-table {
    flex-shrink: 0;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: #8a7f6e;
    background: #F5F2EB;
    border: 1px solid #E8E4DC;
    border-radius: 999px;
    padding: 3px 8px;
  }

  .db-project-members {
    font-size: 12px;
    color: #a09488;
    font-weight: 400;
    margin-top: 2px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .db-project-track-row {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 8px;
  }

  .db-project-track {
    display: inline-flex;
    align-items: center;
    font-size: 11px;
    font-weight: 600;
    color: #6d6255;
    background: #F5F2EB;
    border: 1px solid #E8E4DC;
    border-radius: 999px;
    padding: 4px 8px;
  }

  .db-project-card-right {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-shrink: 0;
  }

  .db-status-badge {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 4px 10px;
    border-radius: 100px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }

  .db-status-badge.done {
    background: #e0f0e8;
    color: #2d6a46;
  }

  .db-status-badge.pending {
    background: #F0EDE7;
    color: #8a7f6e;
  }

  .db-status-dot {
    width: 5px;
    height: 5px;
    border-radius: 50%;
  }

  .db-status-badge.done .db-status-dot { background: #4a7c5f; }
  .db-status-badge.pending .db-status-dot { background: #C9A96E; }

  .db-arrow {
    color: #c0b8ae;
    font-size: 16px;
    transition: color 0.15s, transform 0.15s;
  }

  .db-project-card:hover .db-arrow {
    color: #1a1a1a;
    transform: translateX(2px);
  }

  /* ── Empty state ── */
  .db-empty {
    text-align: center;
    padding: 48px 24px;
    color: #a09488;
    font-size: 14px;
  }

  /* ── Loading ── */
  .db-loading {
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    background: #F7F5F0;
  }

  .db-loading-inner {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 16px;
    color: #8a7f6e;
    font-family: 'DM Sans', sans-serif;
    font-size: 14px;
  }

  .db-loading-ring {
    width: 36px;
    height: 36px;
    border: 3px solid #E8E4DC;
    border-top-color: #C9A96E;
    border-radius: 50%;
    animation: spin 0.9s linear infinite;
  }

  @keyframes spin { to { transform: rotate(360deg); } }

  @keyframes db-fadeup {
    from { opacity: 0; transform: translateY(12px); }
    to   { opacity: 1; transform: translateY(0); }
  }

  @media (max-width: 600px) {
    .db-topbar { padding: 0 20px; }
    .db-body { padding: 24px 16px 60px; }
    .db-stats { grid-template-columns: repeat(3, 1fr); gap: 8px; }
    .db-stat { padding: 14px; }
    .db-header { flex-direction: column; align-items: flex-start; gap: 4px; }
    .db-header-meta { text-align: left; }
    .db-project-card {
      align-items: flex-start;
    }
    .db-project-card-right {
      padding-top: 2px;
    }
    .db-project-title-row {
      flex-wrap: wrap;
      row-gap: 6px;
    }
    .db-project-members {
      white-space: normal;
    }
  }
`;

const FILTERS = ["All", "Pending", "Done"];

export default function Dashboard() {
  const [projects, setProjects] = useState([]);
  const [completed, setCompleted] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("All");
  const [judge, setJudge] = useState(null);
  const [accessError, setAccessError] = useState("");
  const navigate = useNavigate();

  useEffect(() => {
    const load = async () => {
      try {
        const user = auth.currentUser;
        if (!user) {
          navigate("/", { replace: true });
          return;
        }

        let judgeData = null;
        let judgeDocId = null;

        // Primary lookup: by Firebase Auth UID
        const judgeDocByUid = await getDoc(doc(db, "judges", user.uid));
        if (judgeDocByUid.exists()) {
          judgeData = judgeDocByUid.data();
          judgeDocId = user.uid;
        } else {
          // Fallback: judge doc was created by assign.js using email slug as ID
          const emailQuery = query(collection(db, "judges"), where("email", "==", user.email));
          const emailSnap = await getDocs(emailQuery);
          if (!emailSnap.empty) {
            judgeData = emailSnap.docs[0].data();
            judgeDocId = emailSnap.docs[0].id;
          }
        }

        if (!judgeData) {
          setAccessError("Your account is signed in, but it is not linked to a judge profile yet.");
          setLoading(false);
          return;
        }

        setJudge(judgeData);
        const projectIds = Array.isArray(judgeData.assignedProjects)
          ? judgeData.assignedProjects
          : [];

        const projectDocs = await Promise.all(
          projectIds.map((id) => getDoc(doc(db, "projects", id)))
        );

        setProjects(
          projectDocs
            .filter((d) => d.exists())
            .map((d) => ({ id: d.id, ...d.data() }))
            .sort((a, b) => (a.tableNumber ?? Infinity) - (b.tableNumber ?? Infinity))
        );

        const q = query(
          collection(db, "evaluations"),
          where("judgeId", "==", user.uid)
        );
        const snap = await getDocs(q);
        setCompleted(snap.docs.map((d) => d.data().projectId));
      } catch (err) {
        console.error("Dashboard load error:", err);
        setAccessError("We couldn't load your judge assignments right now.");
      } finally {
        setLoading(false);
      }
    };

    load();
  }, [navigate]);

  const pct = projects.length > 0
    ? Math.round((completed.length / projects.length) * 100)
    : 0;

  const remaining = projects.length - completed.length;

  const filtered = projects.filter((p) => {
    if (filter === "Done") return completed.includes(p.id);
    if (filter === "Pending") return !completed.includes(p.id);
    return true;
  });

  const getProjectTracks = (project) => {
    if (Array.isArray(project.tracks) && project.tracks.length > 0) return project.tracks;
    if (project.track) return [project.track];
    return [];
  };

  const getProjectMembers = (project) => {
    if (Array.isArray(project.members)) return project.members.filter(Boolean);
    return [];
  };

  const judgeName =
    judge?.name ||
    auth.currentUser?.displayName ||
    auth.currentUser?.email?.split("@")[0] ||
    "Judge";

  if (loading) {
    return (
      <>
        <style>{styles}</style>
        <div className="db-loading">
          <div className="db-loading-inner">
            <div className="db-loading-ring" />
            Loading your assignments…
          </div>
        </div>
      </>
    );
  }

  if (accessError) {
    return (
      <>
        <style>{styles}</style>
        <div className="db-root">
          <div className="db-topbar">
            <div className="db-topbar-left">
              <div className="db-topbar-icon">
                <svg viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
                  <rect x="2" y="2" width="7" height="7" rx="1.5" fill="white"/>
                  <rect x="11" y="2" width="7" height="7" rx="1.5" fill="white" fillOpacity="0.5"/>
                  <rect x="2" y="11" width="7" height="7" rx="1.5" fill="white" fillOpacity="0.5"/>
                  <rect x="11" y="11" width="7" height="7" rx="1.5" fill="white" fillOpacity="0.75"/>
                </svg>
              </div>
              <span className="db-topbar-wordmark">DataHacks Judging</span>
            </div>
            <div className="db-topbar-right">
              <button
                className="db-sign-out-btn"
                onClick={() => auth.signOut().then(() => navigate("/"))}
              >
                Sign out
              </button>
            </div>
          </div>

          <div className="db-body">
            <div className="db-empty">
              {accessError}
              <br />
              Contact your event organizer to assign this login to a judge record.
            </div>
          </div>
        </div>
      </>
    );
  }

  return (
    <>
      <style>{styles}</style>
      <div className="db-root">
        {/* Top bar */}
        <div className="db-topbar">
          <div className="db-topbar-left">
            <div className="db-topbar-icon">
              <svg viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
                <rect x="2" y="2" width="7" height="7" rx="1.5" fill="white"/>
                <rect x="11" y="2" width="7" height="7" rx="1.5" fill="white" fillOpacity="0.5"/>
                <rect x="2" y="11" width="7" height="7" rx="1.5" fill="white" fillOpacity="0.5"/>
                <rect x="11" y="11" width="7" height="7" rx="1.5" fill="white" fillOpacity="0.75"/>
              </svg>
            </div>
            <span className="db-topbar-wordmark">DataHacks Judging</span>
            <div className="db-topbar-sep" />
            <span className="db-topbar-page">Dashboard</span>
          </div>
          <div className="db-topbar-right">
            <button
              className="db-sign-out-btn"
              onClick={() => auth.signOut().then(() => navigate("/"))}
            >
              Sign out
            </button>
          </div>
        </div>

        <div className="db-body">
          {/* Page header */}
          <div className="db-header">
            <div>
              <div className="db-header-eyebrow">
                <span className="db-header-dot" />
                Judge Portal · 2026
              </div>
              <h1 className="db-header-title">Your Assignments</h1>
            </div>
            <div className="db-header-meta">
              <div className="db-header-meta-line">
                <strong>Judge Name:</strong> {judgeName}
              </div>
              {judge?.track && (
                <div className="db-header-meta-line">
                  <strong>Assigned Track:</strong> {judge.track}
                </div>
              )}
            </div>
          </div>

          {/* Stats */}
          <div className="db-stats">
            <div className="db-stat">
              <span className="db-stat-value">{projects.length}</span>
              <span className="db-stat-label">Assigned</span>
            </div>
            <div className="db-stat">
              <span className="db-stat-value green">{completed.length}</span>
              <span className="db-stat-label">Completed</span>
            </div>
            <div className="db-stat">
              <span className={`db-stat-value${remaining > 0 ? " gold" : " green"}`}>
                {remaining}
              </span>
              <span className="db-stat-label">Remaining</span>
            </div>
          </div>

          {/* Progress */}
          <div className="db-progress-card">
            <div className="db-progress-top">
              <span className="db-progress-label">Overall Progress</span>
              <span className="db-progress-pct">{pct}%</span>
            </div>
            <div className="db-progress-track">
              <div
                className={`db-progress-fill${pct === 100 ? " complete" : ""}`}
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>

          {/* Project list */}
          <div className="db-section-heading">
            <span className="db-section-title">Projects</span>
            <div className="db-filter-tabs">
              {FILTERS.map((f) => (
                <button
                  key={f}
                  className={`db-filter-tab${filter === f ? " active" : ""}`}
                  onClick={() => setFilter(f)}
                >
                  {f}
                </button>
              ))}
            </div>
          </div>

          <div className="db-project-list">
            {filtered.length === 0 ? (
              <div className="db-empty">No projects in this category.</div>
            ) : (
              filtered.map((p, i) => {
                const done = completed.includes(p.id);
                const globalIdx = projects.findIndex((pr) => pr.id === p.id);
                return (
                  <button
                    key={p.id}
                    className={`db-project-card${done ? " done" : ""}`}
                    style={{ animationDelay: `${0.15 + i * 0.04}s` }}
                    onClick={() => navigate(`/evaluate/${p.id}`)}
                  >
                    <div className="db-project-card-left">
                      <div className="db-project-index">
                        {done ? (
                          <svg width="12" height="10" viewBox="0 0 12 10" fill="none">
                            <path d="M1 5l3.5 3.5 7-8" stroke="#4a7c5f" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
                          </svg>
                        ) : (
                          globalIdx + 1
                        )}
                      </div>
                      <div className="db-project-info">
                        <div className="db-project-title-row">
                          <div className="db-project-name">{p.name}</div>
                          {p.tableNumber !== undefined && p.tableNumber !== null && (
                            <div className="db-project-table">Table {p.tableNumber}</div>
                          )}
                        </div>
                        {getProjectMembers(p).length > 0 && (
                          <div className="db-project-members">
                            {getProjectMembers(p).join(", ")}
                          </div>
                        )}
                        {getProjectTracks(p).length > 0 && (
                          <div className="db-project-track-row">
                            {getProjectTracks(p).map((track) => (
                              <div key={`${p.id}-${track}`} className="db-project-track">
                                {track}
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                    <div className="db-project-card-right">
                      <span className={`db-status-badge${done ? " done" : " pending"}`}>
                        <span className="db-status-dot" />
                        {done ? "Done" : "Pending"}
                      </span>
                      <span className="db-arrow">→</span>
                    </div>
                  </button>
                );
              })
            )}
          </div>
        </div>
      </div>
    </>
  );
}
