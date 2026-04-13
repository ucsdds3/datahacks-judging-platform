import { useEffect, useState } from "react";
import { db } from "../firebase";
import { collection, getDocs } from "firebase/firestore";
import { MAX_TOTAL_SCORE } from "../config/rubric";

const styles = `
  @import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=DM+Sans:wght@300;400;500;600&display=swap');

  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  .ad-root {
    min-height: 100vh;
    background: #F7F5F0;
    font-family: 'DM Sans', sans-serif;
    color: #1a1a1a;
  }

  /* ── Top bar ── */
  .ad-topbar {
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

  .ad-topbar-left {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .ad-topbar-icon {
    width: 30px;
    height: 30px;
    background: #1a1a1a;
    border-radius: 7px;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
  }

  .ad-topbar-icon svg { width: 16px; height: 16px; }

  .ad-topbar-wordmark {
    font-size: 14px;
    font-weight: 600;
    color: #1a1a1a;
    letter-spacing: -0.01em;
  }

  .ad-topbar-sep {
    width: 1px;
    height: 18px;
    background: #E0DBD2;
  }

  .ad-topbar-badge {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.07em;
    text-transform: uppercase;
    color: #7a5c2e;
    background: #FDF0DC;
    border: 1px solid #F0D9A8;
    border-radius: 100px;
    padding: 3px 10px;
  }

  .ad-topbar-badge-dot {
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: #C9A96E;
  }

  .ad-topbar-right {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .ad-export-btn {
    display: inline-flex;
    align-items: center;
    gap: 7px;
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

  .ad-export-btn:hover {
    background: #F0EDE7;
    border-color: #ccc8c0;
  }

  /* ── Body ── */
  .ad-body {
    max-width: 1000px;
    margin: 0 auto;
    padding: 40px 24px 80px;
  }

  /* ── Page header ── */
  .ad-header {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    margin-bottom: 32px;
    animation: ad-fadeup 0.4s ease both;
  }

  .ad-header-eyebrow {
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

  .ad-header-dot {
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: #C9A96E;
  }

  .ad-header-title {
    font-family: 'DM Serif Display', serif;
    font-size: 32px;
    letter-spacing: -0.02em;
    color: #111;
  }

  .ad-header-sub {
    font-size: 13px;
    color: #a09488;
    margin-top: 4px;
  }

  /* ── Summary stats ── */
  .ad-stats {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 14px;
    margin-bottom: 28px;
    animation: ad-fadeup 0.4s ease 0.05s both;
  }

  @media (max-width: 700px) {
    .ad-stats { grid-template-columns: repeat(2, 1fr); }
  }

  .ad-stat {
    background: #fff;
    border: 1px solid #E8E4DC;
    border-radius: 12px;
    padding: 18px 20px;
  }

  .ad-stat-value {
    font-family: 'DM Serif Display', serif;
    font-size: 28px;
    letter-spacing: -0.02em;
    color: #111;
    line-height: 1;
    display: block;
    margin-bottom: 4px;
  }

  .ad-stat-value.gold { color: #C9A96E; }
  .ad-stat-value.green { color: #4a7c5f; }
  .ad-stat-value.blue { color: #4a6a8a; }

  .ad-stat-label {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.07em;
    text-transform: uppercase;
    color: #a09488;
  }

  /* ── Section heading ── */
  .ad-section-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 14px;
    animation: ad-fadeup 0.4s ease 0.1s both;
  }

  .ad-section-title {
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: #8a7f6e;
  }

  .ad-sort-tabs {
    display: flex;
    gap: 4px;
  }

  .ad-sort-tab {
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

  .ad-sort-tab:hover { background: #EDE9E2; }
  .ad-sort-tab.active {
    background: #fff;
    border-color: #E0DBD2;
    color: #1a1a1a;
  }

  /* ── Project table ── */
  .ad-table-wrap {
    background: #fff;
    border: 1px solid #E8E4DC;
    border-radius: 14px;
    overflow: hidden;
    animation: ad-fadeup 0.4s ease 0.12s both;
  }

  .ad-table {
    width: 100%;
    border-collapse: collapse;
  }

  .ad-table thead tr {
    border-bottom: 1px solid #E8E4DC;
    background: #FAFAF8;
  }

  .ad-table th {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: #a09488;
    padding: 12px 20px;
    text-align: left;
  }

  .ad-table th.right { text-align: right; }
  .ad-table th.center { text-align: center; }

  .ad-table tbody tr {
    border-bottom: 1px solid #F0EDE7;
    transition: background 0.15s;
  }

  .ad-table tbody tr:last-child { border-bottom: none; }
  .ad-table tbody tr:hover { background: #FAFAF8; }

  .ad-table td {
    padding: 14px 20px;
    font-size: 14px;
    color: #1a1a1a;
    vertical-align: middle;
  }

  .ad-table td.right { text-align: right; }
  .ad-table td.center { text-align: center; }

  .ad-project-name-cell {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .ad-rank-badge {
    width: 26px;
    height: 26px;
    border-radius: 7px;
    background: #F0EDE7;
    border: 1px solid #E0DBD2;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 11px;
    font-weight: 700;
    color: #8a7f6e;
    flex-shrink: 0;
  }

  .ad-rank-badge.top {
    background: #FDF0DC;
    border-color: #F0D9A8;
    color: #7a5c2e;
  }

  .ad-project-label {
    font-weight: 500;
    letter-spacing: -0.01em;
  }

  .ad-project-id {
    font-size: 11px;
    color: #b0a89a;
    font-family: 'SF Mono', 'Fira Code', monospace;
    margin-top: 1px;
  }

  /* Submission count bar */
  .ad-count-bar-wrap {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .ad-count-bar-track {
    flex: 1;
    height: 6px;
    background: #EDE9E2;
    border-radius: 100px;
    overflow: hidden;
    min-width: 80px;
  }

  .ad-count-bar-fill {
    height: 100%;
    border-radius: 100px;
    background: linear-gradient(90deg, #C9A96E, #a8845a);
    transition: width 0.6s cubic-bezier(0.16, 1, 0.3, 1);
  }

  .ad-count-bar-fill.full {
    background: linear-gradient(90deg, #4a7c5f, #3a6b4f);
  }

  .ad-count-num {
    font-size: 13px;
    font-weight: 600;
    color: #1a1a1a;
    min-width: 24px;
    text-align: right;
  }

  /* Score chip */
  .ad-score-chip {
    display: inline-flex;
    align-items: baseline;
    gap: 2px;
  }

  .ad-score-val {
    font-family: 'DM Serif Display', serif;
    font-size: 17px;
    letter-spacing: -0.02em;
    color: #111;
  }

  .ad-score-denom {
    font-size: 11px;
    color: #a09488;
  }

  /* Coverage pill */
  .ad-coverage-pill {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 3px 10px;
    border-radius: 100px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.04em;
  }

  .ad-coverage-pill.full {
    background: #e0f0e8;
    color: #2d6a46;
  }

  .ad-coverage-pill.partial {
    background: #FDF0DC;
    color: #7a5c2e;
  }

  .ad-coverage-pill.none {
    background: #F0EDE7;
    color: #8a7f6e;
  }

  /* ── Loading ── */
  .ad-loading {
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    background: #F7F5F0;
  }

  .ad-loading-inner {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 16px;
    color: #8a7f6e;
    font-family: 'DM Sans', sans-serif;
    font-size: 14px;
  }

  .ad-loading-ring {
    width: 36px;
    height: 36px;
    border: 3px solid #E8E4DC;
    border-top-color: #C9A96E;
    border-radius: 50%;
    animation: spin 0.9s linear infinite;
  }

  @keyframes spin { to { transform: rotate(360deg); } }

  @keyframes ad-fadeup {
    from { opacity: 0; transform: translateY(12px); }
    to   { opacity: 1; transform: translateY(0); }
  }
`;

const SORT_OPTIONS = ["Submissions", "Avg Score", "Coverage"];

export default function Admin() {
  const [projects, setProjects] = useState([]);
  const [judges, setJudges] = useState([]);
  const [loading, setLoading] = useState(true);
  const [sort, setSort] = useState("Submissions");

  useEffect(() => {
    const load = async () => {
      // Load all evaluations
      const evalSnap = await getDocs(collection(db, "evaluations"));
      const evals = evalSnap.docs.map((d) => ({ id: d.id, ...d.data() }));

      // Load all projects
      const projectSnap = await getDocs(collection(db, "projects"));
      const projectList = projectSnap.docs.map((d) => ({ id: d.id, ...d.data() }));

      // Load all judges
      const judgeSnap = await getDocs(collection(db, "judges"));
      const judgeList = judgeSnap.docs.map((d) => ({ id: d.id, ...d.data() }));
      setJudges(judgeList);

      // Aggregate per project
      const aggMap = {};
      evals.forEach(({ projectId, scores, judgeId }) => {
        if (!aggMap[projectId]) {
          aggMap[projectId] = { submissions: 0, totalScore: 0, judgeIds: new Set() };
        }
        aggMap[projectId].submissions += 1;
        aggMap[projectId].judgeIds.add(judgeId);
        if (scores) {
          const s = Object.values(scores).reduce((a, b) => a + (b || 0), 0);
          aggMap[projectId].totalScore += s;
        }
      });

      // Merge with project data
      const merged = projectList.map((p) => {
        const agg = aggMap[p.id] || { submissions: 0, totalScore: 0, judgeIds: new Set() };
        const assignedCount = (p.assignedJudges || []).length || 1;
        return {
          ...p,
          submissions: agg.submissions,
          avgScore: agg.submissions > 0
            ? (agg.totalScore / agg.submissions).toFixed(1)
            : null,
          judgesCompleted: agg.judgeIds.size,
          assignedCount,
          coverage: agg.judgeIds.size / assignedCount,
        };
      });

      setProjects(merged);
      setLoading(false);
    };

    load();
  }, []);

  const totalEvals = projects.reduce((s, p) => s + p.submissions, 0);
  const totalProjects = projects.length;
  const fullyEvaluated = projects.filter((p) => p.coverage >= 1).length;
  const avgScoreOverall = projects.filter((p) => p.avgScore !== null).length > 0
    ? (
        projects.reduce((s, p) => s + (parseFloat(p.avgScore) || 0), 0) /
        projects.filter((p) => p.avgScore !== null).length
      ).toFixed(1)
    : "—";

  const maxSubmissions = Math.max(...projects.map((p) => p.submissions), 1);

  const sorted = [...projects].sort((a, b) => {
    if (sort === "Avg Score") return (parseFloat(b.avgScore) || 0) - (parseFloat(a.avgScore) || 0);
    if (sort === "Coverage") return b.coverage - a.coverage;
    return b.submissions - a.submissions;
  });

  const handleExport = () => {
    const rows = [
      ["Project ID", "Project Name", "Submissions", "Avg Score", "Judges Completed"],
      ...sorted.map((p) => [
        p.id,
        p.name || p.id,
        p.submissions,
        p.avgScore ?? "—",
        p.judgesCompleted,
      ]),
    ];
    const csv = rows.map((r) => r.join(",")).join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "datahacks-results.csv";
    a.click();
    URL.revokeObjectURL(url);
  };

  if (loading) {
    return (
      <>
        <style>{styles}</style>
        <div className="ad-loading">
          <div className="ad-loading-inner">
            <div className="ad-loading-ring" />
            Loading evaluation data…
          </div>
        </div>
      </>
    );
  }

  return (
    <>
      <style>{styles}</style>
      <div className="ad-root">
        {/* Topbar */}
        <div className="ad-topbar">
          <div className="ad-topbar-left">
            <div className="ad-topbar-icon">
              <svg viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
                <rect x="2" y="2" width="7" height="7" rx="1.5" fill="white"/>
                <rect x="11" y="2" width="7" height="7" rx="1.5" fill="white" fillOpacity="0.5"/>
                <rect x="2" y="11" width="7" height="7" rx="1.5" fill="white" fillOpacity="0.5"/>
                <rect x="11" y="11" width="7" height="7" rx="1.5" fill="white" fillOpacity="0.75"/>
              </svg>
            </div>
            <span className="ad-topbar-wordmark">DataHacks Judging</span>
            <div className="ad-topbar-sep" />
            <span className="ad-topbar-badge">
              <span className="ad-topbar-badge-dot" />
              Admin
            </span>
          </div>
          <div className="ad-topbar-right">
            <button className="ad-export-btn" onClick={handleExport}>
              <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
                <path d="M6.5 1v8M3 6.5l3.5 3.5 3.5-3.5M1 11h11" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
              Export CSV
            </button>
          </div>
        </div>

        <div className="ad-body">
          {/* Page header */}
          <div className="ad-header">
            <div>
              <div className="ad-header-eyebrow">
                <span className="ad-header-dot" />
                Admin Panel · 2026
              </div>
              <h1 className="ad-header-title">Evaluation Overview</h1>
              <p className="ad-header-sub">Live results across all projects and judges</p>
            </div>
          </div>

          {/* Stats */}
          <div className="ad-stats">
            <div className="ad-stat">
              <span className="ad-stat-value">{totalProjects}</span>
              <span className="ad-stat-label">Total Projects</span>
            </div>
            <div className="ad-stat">
              <span className="ad-stat-value gold">{totalEvals}</span>
              <span className="ad-stat-label">Evaluations</span>
            </div>
            <div className="ad-stat">
              <span className="ad-stat-value green">{fullyEvaluated}</span>
              <span className="ad-stat-label">Fully Covered</span>
            </div>
            <div className="ad-stat">
              <span className="ad-stat-value blue">{avgScoreOverall}</span>
              <span className="ad-stat-label">Avg Score</span>
            </div>
          </div>

          {/* Table */}
          <div className="ad-section-heading">
            <span className="ad-section-title">Projects</span>
            <div className="ad-sort-tabs">
              {SORT_OPTIONS.map((opt) => (
                <button
                  key={opt}
                  className={`ad-sort-tab${sort === opt ? " active" : ""}`}
                  onClick={() => setSort(opt)}
                >
                  {opt}
                </button>
              ))}
            </div>
          </div>

          <div className="ad-table-wrap">
            <table className="ad-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Project</th>
                  <th>Submissions</th>
                  <th className="center">Avg Score</th>
                  <th className="center">Coverage</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((p, i) => {
                  const coveragePct = Math.min(p.coverage * 100, 100);
                  const coverageClass =
                    coveragePct >= 100 ? "full" : coveragePct > 0 ? "partial" : "none";
                  const coverageLabel =
                    coveragePct >= 100 ? "Complete" : coveragePct > 0 ? "Partial" : "None";

                  return (
                    <tr key={p.id}>
                      <td>
                        <div className={`ad-rank-badge${i < 3 ? " top" : ""}`}>
                          {i + 1}
                        </div>
                      </td>
                      <td>
                        <div className="ad-project-name-cell">
                          <div>
                            <div className="ad-project-label">{p.name || p.id}</div>
                            <div className="ad-project-id">{p.id}</div>
                          </div>
                        </div>
                      </td>
                      <td>
                        <div className="ad-count-bar-wrap">
                          <div className="ad-count-bar-track">
                            <div
                              className={`ad-count-bar-fill${p.submissions === maxSubmissions && p.submissions > 0 ? " full" : ""}`}
                              style={{ width: `${(p.submissions / maxSubmissions) * 100}%` }}
                            />
                          </div>
                          <span className="ad-count-num">{p.submissions}</span>
                        </div>
                      </td>
                      <td className="center">
                        {p.avgScore !== null ? (
                          <span className="ad-score-chip">
                            <span className="ad-score-val">{p.avgScore}</span>
                            <span className="ad-score-denom">/{MAX_TOTAL_SCORE}</span>
                          </span>
                        ) : (
                          <span style={{ color: "#c0b8ae", fontSize: 13 }}>—</span>
                        )}
                      </td>
                      <td className="center">
                        <span className={`ad-coverage-pill ${coverageClass}`}>
                          {coverageLabel}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </>
  );
}
