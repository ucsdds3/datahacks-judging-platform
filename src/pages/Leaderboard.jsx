import { useEffect, useState } from "react";
import { auth, db } from "../firebase";
import { collection, getDocs } from "firebase/firestore";
import { MAX_TOTAL_SCORE } from "../config/rubric";
import { signOut } from "firebase/auth";
import { useNavigate } from "react-router-dom";

const TRACK_ORDER = [
  "AI/ML",
  "Analytics",
  "Cloud",
  "Entrepreneurship & Product",
  "UI/UX & Web Dev",
  "Hardware & IoT",
  "Mechanical Design & Biotechnology",
  "Economics",
];

const TRACK_COLORS = {
  "AI/ML":                              { bg: "#EEF2FF", border: "#C7D2FE", text: "#3730A3", dot: "#6366F1" },
  "Analytics":                          { bg: "#FDF4FF", border: "#E9D5FF", text: "#6B21A8", dot: "#A855F7" },
  "Cloud":                              { bg: "#EFF6FF", border: "#BFDBFE", text: "#1E40AF", dot: "#3B82F6" },
  "Entrepreneurship & Product":         { bg: "#FFF7ED", border: "#FED7AA", text: "#9A3412", dot: "#F97316" },
  "UI/UX & Web Dev":                    { bg: "#F0FDF4", border: "#BBF7D0", text: "#166534", dot: "#22C55E" },
  "Hardware & IoT":                     { bg: "#FFF1F2", border: "#FECDD3", text: "#9F1239", dot: "#F43F5E" },
  "Mechanical Design & Biotechnology":  { bg: "#F0FDFA", border: "#99F6E4", text: "#134E4A", dot: "#14B8A6" },
  "Economics":                          { bg: "#FEFCE8", border: "#FEF08A", text: "#713F12", dot: "#EAB308" },
};

const DEFAULT_COLOR = { bg: "#F7F5F0", border: "#E8E4DC", text: "#5a534a", dot: "#C9A96E" };

const styles = `
  @import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=DM+Sans:wght@300;400;500;600&display=swap');
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  .lb-root { min-height: 100vh; background: #F7F5F0; font-family: 'DM Sans', sans-serif; color: #1a1a1a; overflow: clip; }

  .lb-topbar {
    background: #fff; border-bottom: 1px solid #E8E4DC;
    padding: 0 40px; height: 60px;
    display: flex; align-items: center; justify-content: space-between;
    position: sticky; top: 0; z-index: 100;
  }
  .lb-topbar-left { display: flex; align-items: center; gap: 12px; }
  .lb-topbar-icon {
    width: 30px; height: 30px; background: #1a1a1a; border-radius: 7px;
    display: flex; align-items: center; justify-content: center; flex-shrink: 0;
  }
  .lb-topbar-icon svg { width: 16px; height: 16px; }
  .lb-topbar-wordmark { font-size: 14px; font-weight: 600; color: #1a1a1a; letter-spacing: -0.01em; }
  .lb-topbar-sep { width: 1px; height: 18px; background: #E0DBD2; }
  .lb-topbar-badge {
    display: inline-flex; align-items: center; gap: 5px;
    font-size: 11px; font-weight: 600; letter-spacing: 0.07em; text-transform: uppercase;
    color: #1e40af; background: #EEF2FF; border: 1px solid #C7D2FE;
    border-radius: 100px; padding: 3px 10px;
  }
  .lb-topbar-badge-dot { width: 5px; height: 5px; border-radius: 50%; background: #6366F1; }
  .lb-topbar-right { display: flex; align-items: center; gap: 10px; }
  .lb-sign-out-btn {
    font-size: 13px; font-weight: 500; color: #5a534a;
    background: none; border: 1px solid #E0DBD2; border-radius: 8px;
    padding: 6px 14px; cursor: pointer; font-family: 'DM Sans', sans-serif;
    transition: background 0.15s;
  }
  .lb-sign-out-btn:hover { background: #F0EDE7; }

  @media (max-width: 480px) {
    .lb-topbar { padding: 0 16px; }
    .lb-topbar-wordmark { display: none; }
    .lb-topbar-sep { display: none; }
  }
  @media (max-width: 360px) {
    .lb-topbar { padding: 0 10px; }
    .lb-topbar-badge-label { display: none; }
    .lb-sign-out-btn { padding: 6px 10px; font-size: 12px; }
  }

  .lb-body { max-width: 960px; margin: 0 auto; padding: 40px 24px 160px; }

  .lb-header { margin-bottom: 36px; animation: lb-up 0.4s ease both; }
  .lb-eyebrow {
    display: inline-flex; align-items: center; gap: 7px;
    font-size: 11px; font-weight: 600; letter-spacing: 0.1em; text-transform: uppercase;
    color: #8a7f6e; margin-bottom: 8px;
  }
  .lb-eyebrow-dot { width: 5px; height: 5px; border-radius: 50%; background: #C9A96E; }
  .lb-title { font-family: 'DM Serif Display', serif; font-size: 34px; letter-spacing: -0.02em; color: #111; }
  .lb-sub { font-size: 13px; color: #a09488; margin-top: 4px; }

  .lb-track-nav {
    display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 32px;
    animation: lb-up 0.4s ease 0.05s both;
  }
  .lb-track-pill {
    font-size: 12px; font-weight: 500; padding: 5px 13px;
    border-radius: 100px; border: 1px solid transparent;
    cursor: pointer; font-family: 'DM Sans', sans-serif;
    transition: all 0.15s; background: #fff; border-color: #E0DBD2; color: #5a534a;
  }
  .lb-track-pill:hover { border-color: #ccc8c0; background: #F0EDE7; }
  .lb-track-pill.active { font-weight: 600; }

  .lb-track-section { margin-bottom: 40px; animation: lb-up 0.35s ease both; }
  .lb-track-header {
    display: flex; align-items: center; gap: 12px; margin-bottom: 14px;
  }
  .lb-track-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
  .lb-track-name {
    font-size: 12px; font-weight: 700; letter-spacing: 0.09em; text-transform: uppercase;
  }
  .lb-track-meta { font-size: 12px; color: #a09488; margin-left: auto; }

  .lb-table-wrap {
    background: #fff; border: 1px solid #E8E4DC; border-radius: 14px;
    overflow-x: auto; overflow-y: hidden;
  }
  .lb-table { width: 100%; border-collapse: collapse; table-layout: fixed; }
  .lb-table thead tr { border-bottom: 1px solid #E8E4DC; background: #FAFAF8; }
  .lb-table th {
    font-size: 10px; font-weight: 600; letter-spacing: 0.07em; text-transform: uppercase;
    color: #a09488; padding: 9px 10px; text-align: left; white-space: nowrap; overflow: hidden;
  }
  .lb-table th.right { text-align: right; }
  .lb-table th.center { text-align: center; }
  .lb-table tbody tr { border-bottom: 1px solid #F0EDE7; transition: background 0.15s; }
  .lb-table tbody tr:last-child { border-bottom: none; }
  .lb-table tbody tr:hover { background: #FAFAF8; }
  .lb-table td { padding: 11px 10px; font-size: 12px; color: #1a1a1a; vertical-align: middle; }
  .lb-table td.right { text-align: right; }
  .lb-table td.center { text-align: center; }

  /* Column widths: # | Project | Table | Avg Score | Evals */
  .lb-table .col-rank  { width: 34px; }
  .lb-table .col-table { width: 46px; }
  .lb-table .col-score { width: 38%; }
  .lb-table .col-evals { width: 42px; }

  .lb-rank {
    width: 22px; height: 22px; border-radius: 6px;
    background: #F0EDE7; border: 1px solid #E0DBD2;
    display: flex; align-items: center; justify-content: center;
    font-size: 10px; font-weight: 700; color: #8a7f6e; flex-shrink: 0;
  }
  .lb-rank.gold   { background: #FDF0DC; border-color: #F0D9A8; color: #7a5c2e; }
  .lb-rank.silver { background: #F3F4F6; border-color: #D1D5DB; color: #4B5563; }
  .lb-rank.bronze { background: #FEF3C7; border-color: #FDE68A; color: #92400E; }

  .lb-project-name { font-weight: 500; letter-spacing: -0.01em; font-size: 12px; word-break: break-word; }
  .lb-table-num {
    display: inline-flex; align-items: center; justify-content: center;
    width: 28px; height: 20px; border-radius: 5px;
    background: #F0EDE7; border: 1px solid #E0DBD2;
    font-size: 10px; font-weight: 600; color: #8a7f6e;
    font-family: 'SF Mono', monospace;
  }

  .lb-score-bar-wrap { display: flex; align-items: center; gap: 6px; }
  .lb-score-bar-track {
    flex: 1; height: 5px; background: #EDE9E2;
    border-radius: 100px; overflow: hidden; min-width: 0;
  }
  .lb-score-bar-fill {
    height: 100%; border-radius: 100px;
    background: linear-gradient(90deg, #C9A96E, #a8845a);
    transition: width 0.7s cubic-bezier(0.16, 1, 0.3, 1);
  }
  .lb-score-bar-fill.top { background: linear-gradient(90deg, #6366F1, #4F46E5); }
  .lb-score-val { font-family: 'DM Serif Display', serif; font-size: 13px; letter-spacing: -0.02em; color: #111; white-space: nowrap; }
  .lb-score-denom { font-size: 10px; color: #a09488; }

  .lb-eval-count {
    font-size: 11px; color: #8a7f6e; font-weight: 600; text-align: right;
  }

  @media (max-width: 480px) {
    .lb-body { padding: 24px 12px 160px; }
    .lb-title { font-size: 22px; }
    .lb-table .col-rank  { width: 28px; }
    .lb-table .col-table { width: 36px; }
    .lb-table .col-score { width: 34%; }
    .lb-table .col-evals { width: 32px; }
    .lb-table td, .lb-table th { padding: 8px 6px; }
    .lb-rank { width: 20px; height: 20px; }
  }

  @media (max-width: 360px) {
    .lb-body { padding: 16px 10px 160px; }
    .lb-title { font-size: 19px; }
    .lb-table .col-rank  { width: 24px; }
    .lb-table .col-table { width: 30px; }
    .lb-table .col-score { width: 52px; }
    .lb-table .col-evals { width: 28px; }
    .lb-table td, .lb-table th { padding: 6px 4px; }
    .lb-rank { width: 18px; height: 18px; font-size: 9px; }
    .lb-score-bar-track { display: none; }
    .lb-score-val { font-size: 11px; }
    .lb-score-denom { font-size: 9px; }
    .lb-table-num { width: 22px; height: 18px; font-size: 9px; }
  }

  .lb-empty { padding: 32px 20px; text-align: center; font-size: 13px; color: #a09488; }

  .lb-loading {
    min-height: 100vh; display: flex; align-items: center; justify-content: center;
    background: #F7F5F0;
  }
  .lb-loading-inner { display: flex; flex-direction: column; align-items: center; gap: 16px; color: #8a7f6e; font-family: 'DM Sans', sans-serif; font-size: 14px; }
  .lb-loading-ring {
    width: 36px; height: 36px; border: 3px solid #E8E4DC;
    border-top-color: #C9A96E; border-radius: 50%; animation: spin 0.9s linear infinite;
  }

  @keyframes spin { to { transform: rotate(360deg); } }
  @keyframes lb-up { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
`;

function rankClass(i) {
  if (i === 0) return "gold";
  if (i === 1) return "silver";
  if (i === 2) return "bronze";
  return "";
}

export default function Leaderboard() {
  const [trackData, setTrackData] = useState({}); // track → sorted [{project, avgScore, evalCount}]
  const [loading, setLoading] = useState(true);
  const [activeTrack, setActiveTrack] = useState(null);
  const navigate = useNavigate();

  useEffect(() => {
    const load = async () => {
      // Load all three collections in parallel
      const [projectSnap, judgeSnap, evalSnap] = await Promise.all([
        getDocs(collection(db, "projects")),
        getDocs(collection(db, "judges")),
        getDocs(collection(db, "evaluations")),
      ]);

      const projects = projectSnap.docs.map((d) => ({ id: d.id, ...d.data() }));
      const judges = judgeSnap.docs.map((d) => ({ id: d.id, ...d.data() }));
      const evals = evalSnap.docs.map((d) => ({ id: d.id, ...d.data() }));

      // Map judgeId → track(s). A judge doc stores track as string or array.
      const judgeTrackMap = {};
      for (const j of judges) {
        const tracks = Array.isArray(j.tracks) ? j.tracks : j.track ? [j.track] : [];
        judgeTrackMap[j.id] = tracks;
      }

      // For each project, sum scores per track bucket:
      // score counts toward a track if the evaluating judge judges that track
      // AND that track is one of the project's tracks.
      const projectMap = {};
      for (const p of projects) {
        projectMap[p.id] = p;
      }

      // trackBuckets: track → projectId → { total, count }
      const trackBuckets = {};

      for (const ev of evals) {
        const { judgeId, projectId, scores } = ev;
        const project = projectMap[projectId];
        if (!project || !scores) continue;

        const total = Object.values(scores).reduce((s, v) => s + (v ?? 0), 0);
        const judgeTracks = judgeTrackMap[judgeId] || [];

        // Score goes only into the judge's own track bucket(s)
        for (const track of judgeTracks) {
          if (!trackBuckets[track]) trackBuckets[track] = {};
          if (!trackBuckets[track][projectId]) trackBuckets[track][projectId] = { total: 0, count: 0 };
          trackBuckets[track][projectId].total += total;
          trackBuckets[track][projectId].count += 1;
        }
      }

      // Ensure every project appears in all its tracks even with 0 evals
      for (const p of projects) {
        for (const track of (p.tracks || [])) {
          if (!trackBuckets[track]) trackBuckets[track] = {};
          if (!trackBuckets[track][p.id]) trackBuckets[track][p.id] = { total: 0, count: 0 };
        }
      }

      // Build sorted track → project list
      const result = {};
      for (const [track, projBucket] of Object.entries(trackBuckets)) {
        const entries = Object.entries(projBucket).map(([pid, { total, count }]) => ({
          project: projectMap[pid] || { id: pid, name: pid },
          avgScore: count > 0 ? total / count : null,
          evalCount: count,
        }));
        entries.sort((a, b) => {
          if (a.avgScore === null && b.avgScore === null) return 0;
          if (a.avgScore === null) return 1;
          if (b.avgScore === null) return -1;
          return b.avgScore - a.avgScore;
        });
        result[track] = entries;
      }

      setTrackData(result);
      const firstTrack = TRACK_ORDER.find((t) => result[t]);
      setActiveTrack(firstTrack || Object.keys(result)[0] || null);
      setLoading(false);
    };

    load();
  }, []);

  const handleSignOut = async () => {
    await signOut(auth);
    navigate("/", { replace: true });
  };

  const availableTracks = TRACK_ORDER.filter((t) => trackData[t]);

  if (loading) {
    return (
      <>
        <style>{styles}</style>
        <div className="lb-loading">
          <div className="lb-loading-inner">
            <div className="lb-loading-ring" />
            Loading leaderboard…
          </div>
        </div>
      </>
    );
  }

  const renderTrackSection = (track) => {
    const entries = trackData[track] || [];
    const color = TRACK_COLORS[track] || DEFAULT_COLOR;
    const topScore = entries[0]?.avgScore ?? 0;

    return (
      <div className="lb-track-section" key={track}>
        <div className="lb-track-header">
          <span className="lb-track-dot" style={{ background: color.dot }} />
          <span className="lb-track-name" style={{ color: color.text }}>{track}</span>
          <span className="lb-track-meta">{entries.length} project{entries.length !== 1 ? "s" : ""}</span>
        </div>

        <div className="lb-table-wrap">
          <table className="lb-table">
            <thead>
              <tr>
                <th className="col-rank">#</th>
                <th>Project</th>
                <th className="center col-table">T#</th>
                <th className="col-score">Avg Score</th>
                <th className="right col-evals">Ev.</th>
              </tr>
            </thead>
            <tbody>
              {entries.length === 0 ? (
                <tr><td colSpan={5} className="lb-empty">No projects in this track yet.</td></tr>
              ) : entries.map((entry, i) => {
                const pct = entry.avgScore !== null && topScore > 0
                  ? (entry.avgScore / MAX_TOTAL_SCORE) * 100
                  : 0;
                return (
                  <tr key={entry.project.id}>
                    <td className="col-rank">
                      <div className={`lb-rank ${rankClass(i)}`}>{i + 1}</div>
                    </td>
                    <td>
                      <span className="lb-project-name">{entry.project.name || entry.project.id}</span>
                    </td>
                    <td className="center col-table">
                      {entry.project.tableNumber != null
                        ? <span className="lb-table-num">{entry.project.tableNumber}</span>
                        : <span style={{ color: "#c0b8ae" }}>—</span>}
                    </td>
                    <td className="col-score">
                      {entry.avgScore !== null ? (
                        <div className="lb-score-bar-wrap">
                          <div className="lb-score-bar-track">
                            <div
                              className={`lb-score-bar-fill${i === 0 ? " top" : ""}`}
                              style={{ width: `${pct}%`, background: i === 0 ? `linear-gradient(90deg, ${color.dot}, ${color.dot}cc)` : undefined }}
                            />
                          </div>
                          <span className="lb-score-val">{entry.avgScore.toFixed(1)}</span>
                          <span className="lb-score-denom">/{MAX_TOTAL_SCORE}</span>
                        </div>
                      ) : (
                        <span style={{ color: "#c0b8ae", fontSize: 11 }}>—</span>
                      )}
                    </td>
                    <td className="right col-evals">
                      <span className="lb-eval-count">{entry.evalCount}</span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    );
  };

  return (
    <>
      <style>{styles}</style>
      <div className="lb-root">
        <div className="lb-topbar">
          <div className="lb-topbar-left">
            <div className="lb-topbar-icon">
              <svg viewBox="0 0 20 20" fill="none">
                <rect x="2" y="2" width="7" height="7" rx="1.5" fill="white"/>
                <rect x="11" y="2" width="7" height="7" rx="1.5" fill="white" fillOpacity="0.5"/>
                <rect x="2" y="11" width="7" height="7" rx="1.5" fill="white" fillOpacity="0.5"/>
                <rect x="11" y="11" width="7" height="7" rx="1.5" fill="white" fillOpacity="0.75"/>
              </svg>
            </div>
            <span className="lb-topbar-wordmark">DataHacks Judging</span>
            <div className="lb-topbar-sep" />
            <span className="lb-topbar-badge">
              <span className="lb-topbar-badge-dot" />
              <span className="lb-topbar-badge-label">Leaderboard</span>
            </span>
          </div>
          <div className="lb-topbar-right">
            <button className="lb-sign-out-btn" onClick={handleSignOut}>Sign Out</button>
          </div>
        </div>

        <div className="lb-body">
          <div className="lb-header">
            <div className="lb-eyebrow">
              <span className="lb-eyebrow-dot" />
              Live Results · 2026
            </div>
            <h1 className="lb-title">Track Leaderboards</h1>
            <p className="lb-sub">Scores averaged across all judges per track. Updates in real time.</p>
          </div>

          {/* Track pills nav */}
          <div className="lb-track-nav">
            {availableTracks.map((track) => {
              const color = TRACK_COLORS[track] || DEFAULT_COLOR;
              const isActive = activeTrack === track;
              return (
                <button
                  key={track}
                  className={`lb-track-pill${isActive ? " active" : ""}`}
                  style={isActive ? { background: color.bg, borderColor: color.border, color: color.text } : {}}
                  onClick={() => setActiveTrack(track)}
                >
                  {track}
                </button>
              );
            })}
            <button
              className={`lb-track-pill${activeTrack === "all" ? " active" : ""}`}
              style={activeTrack === "all" ? { background: "#F0EDE7", borderColor: "#D4CFC7", color: "#1a1a1a" } : {}}
              onClick={() => setActiveTrack("all")}
            >
              All Tracks
            </button>
          </div>

          {/* Track content */}
          {activeTrack === "all"
            ? availableTracks.map((t) => renderTrackSection(t))
            : activeTrack && renderTrackSection(activeTrack)}
        </div>
      </div>
    </>
  );
}
