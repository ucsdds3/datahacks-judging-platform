import { useEffect, useState } from "react";
import { auth } from "../firebase";
import {
  browserLocalPersistence,
  onAuthStateChanged,
  setPersistence,
  signInWithEmailAndPassword
} from "firebase/auth";
import { useNavigate } from "react-router-dom";

const styles = `
  @import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=DM+Sans:wght@300;400;500;600&display=swap');

  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  .login-root {
    min-height: 100vh;
    display: flex;
    font-family: 'DM Sans', sans-serif;
    background: #F7F5F0;
    color: #1a1a1a;
  }

  /* ── Left panel ── */
  .login-left {
    width: 52%;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    padding: 52px 64px;
    background: #FFFFFF;
    border-right: 1px solid #E8E4DC;
    position: relative;
    overflow: hidden;
  }

  .login-left::before {
    content: '';
    position: absolute;
    top: -120px;
    left: -120px;
    width: 420px;
    height: 420px;
    border-radius: 50%;
    background: radial-gradient(circle, rgba(234,212,168,0.35) 0%, transparent 70%);
    pointer-events: none;
  }

  .login-left::after {
    content: '';
    position: absolute;
    bottom: -80px;
    right: -80px;
    width: 300px;
    height: 300px;
    border-radius: 50%;
    background: radial-gradient(circle, rgba(186,213,196,0.3) 0%, transparent 70%);
    pointer-events: none;
  }

  .login-wordmark {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .login-wordmark-icon {
    width: 36px;
    height: 36px;
    background: #1a1a1a;
    border-radius: 8px;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
  }

  .login-wordmark-icon svg {
    width: 20px;
    height: 20px;
  }

  .login-wordmark-text {
    font-size: 15px;
    font-weight: 600;
    letter-spacing: -0.01em;
    color: #1a1a1a;
  }

  .login-hero {
    position: relative;
    z-index: 1;
  }

  .login-eyebrow {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: #8a7f6e;
    margin-bottom: 28px;
  }

  .login-eyebrow-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: #C9A96E;
  }

  .login-headline {
    font-family: 'DM Serif Display', serif;
    font-size: clamp(38px, 4vw, 52px);
    line-height: 1.1;
    letter-spacing: -0.02em;
    color: #111;
    margin-bottom: 20px;
  }

  .login-headline em {
    font-style: italic;
    color: #C9A96E;
  }

  .login-subtext {
    font-size: 15px;
    font-weight: 300;
    line-height: 1.7;
    color: #6b6560;
    max-width: 360px;
  }

  .login-footer-note {
    font-size: 12px;
    color: #b0a89a;
    font-weight: 400;
  }

  /* ── Right panel ── */
  .login-right {
    flex: 1;
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    padding: 52px 64px;
    background: #F7F5F0;
  }

  .login-card {
    width: 100%;
    max-width: 400px;
  }

  .login-card-title {
    font-family: 'DM Serif Display', serif;
    font-size: 28px;
    letter-spacing: -0.02em;
    color: #111;
    margin-bottom: 6px;
  }

  .login-card-sub {
    font-size: 14px;
    color: #8a7f6e;
    margin-bottom: 40px;
  }

  /* Stats row */
  .login-stats {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 12px;
    margin-bottom: 40px;
  }

  .login-stat {
    background: #FFFFFF;
    border: 1px solid #E8E4DC;
    border-radius: 10px;
    padding: 16px 14px;
    text-align: center;
  }

  .login-stat-value {
    font-family: 'DM Serif Display', serif;
    font-size: 22px;
    color: #111;
    display: block;
    letter-spacing: -0.02em;
  }

  .login-stat-label {
    font-size: 11px;
    color: #a09488;
    font-weight: 500;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    display: block;
    margin-top: 2px;
  }

  /* Form elements */
  .login-form-group {
    margin-bottom: 16px;
  }

  .login-label {
    display: block;
    font-size: 13px;
    font-weight: 500;
    color: #5a534a;
    margin-bottom: 6px;
    letter-spacing: -0.01em;
  }

  .login-input {
    width: 100%;
    padding: 12px 14px;
    border: 1px solid #E8E4DC;
    border-radius: 8px;
    font-family: 'DM Sans', sans-serif;
    font-size: 14px;
    color: #1a1a1a;
    background: #FFFFFF;
    transition: border-color 0.2s, box-shadow 0.2s;
  }

  .login-input:focus {
    outline: none;
    border-color: #C9A96E;
    box-shadow: 0 0 0 3px rgba(201, 169, 110, 0.1);
  }

  .login-input::placeholder {
    color: #c9c1b5;
  }

  /* Button */
  .login-btn {
    width: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 12px;
    padding: 15px 24px;
    background: #1a1a1a;
    color: #fff;
    border: none;
    border-radius: 8px;
    font-family: 'DM Sans', sans-serif;
    font-size: 15px;
    font-weight: 500;
    cursor: pointer;
    transition: background 0.2s, transform 0.15s, box-shadow 0.2s;
    box-shadow: 0 2px 8px rgba(0,0,0,0.12);
    letter-spacing: -0.01em;
  }

  .login-btn:hover:not(:disabled) {
    background: #2d2d2d;
    transform: translateY(-1px);
    box-shadow: 0 6px 20px rgba(0,0,0,0.18);
  }

  .login-btn:active:not(:disabled) {
    transform: translateY(0);
    box-shadow: 0 2px 6px rgba(0,0,0,0.12);
  }

  .login-btn:disabled {
    opacity: 0.6;
    cursor: not-allowed;
    transform: none;
  }

  .login-btn-spinner {
    width: 18px;
    height: 18px;
    border: 2px solid rgba(255,255,255,0.3);
    border-top-color: #fff;
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
  }

  @keyframes spin { to { transform: rotate(360deg); } }

  .login-error {
    margin-top: 16px;
    padding: 12px 14px;
    background: #FEF2F2;
    border: 1px solid #FECACA;
    border-radius: 8px;
    font-size: 13px;
    color: #B91C1C;
    line-height: 1.5;
  }

  .login-hint {
    margin-top: 20px;
    font-size: 12px;
    color: #b0a89a;
    text-align: center;
    line-height: 1.6;
  }

  .login-toggle {
    margin-top: 24px;
    text-align: center;
    font-size: 13px;
    color: #8a7f6e;
  }

  .login-toggle button {
    background: none;
    border: none;
    color: #C9A96E;
    cursor: pointer;
    font-weight: 500;
    text-decoration: none;
    transition: color 0.2s;
  }

  .login-toggle button:hover {
    color: #a88b4e;
    text-decoration: underline;
  }

  /* Info badges */
  .login-badges {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 32px;
  }

  .login-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 6px 12px;
    background: #FFFFFF;
    border: 1px solid #E8E4DC;
    border-radius: 100px;
    font-size: 12px;
    font-weight: 500;
    color: #5a534a;
  }

  .login-badge-dot {
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: #BAD5C4;
  }

  /* Responsive */
  @media (max-width: 768px) {
    .login-root { flex-direction: column; }
    .login-left {
      width: 100%;
      padding: 36px 32px;
      border-right: none;
      border-bottom: 1px solid #E8E4DC;
    }
    .login-right { 
      padding: 40px 32px;
      justify-content: flex-start;
      padding-top: 60px;
    }
    .login-footer-note { display: none; }
    .login-stats { margin-bottom: 32px; }
  }
`;

export default function Login() {
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let isMounted = true;
    let unsubscribe = null;

    const initAuth = async () => {
      try {
        await setPersistence(auth, browserLocalPersistence);
      } catch (err) {
        console.error("Persistence error:", err);
      }

      unsubscribe = onAuthStateChanged(auth, (user) => {
        if (isMounted && user) {
          const dest = user.email === "ds3@ucsd.edu" ? "/leaderboard" : "/dashboard";
          navigate(dest, { replace: true });
        }
      });
    };

    initAuth();

    return () => {
      isMounted = false;
      if (unsubscribe) {
        unsubscribe();
      }
    };
  }, [navigate]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError("");

    if (!username || !password) {
      setError("Please fill in all fields.");
      setLoading(false);
      return;
    }

    try {
      const rawUsername = username.trim().toLowerCase().split("@")[0];
      const email = `${rawUsername}@datahacks2026.ucsd`;
      const cred = await signInWithEmailAndPassword(auth, email, `DH${password}`);
      const dest = cred.user.email === "ds3@ucsd.edu" ? "/leaderboard" : "/dashboard";
      navigate(dest, { replace: true });
    } catch (err) {
      console.error("Auth error:", err);
      let message = "Sign-in failed. Please try again.";

      if (err.code === "auth/user-not-found" || err.code === "auth/invalid-credential") {
        message = "No judge account found for that username/password.";
      } else if (err.code === "auth/wrong-password") {
        message = "Incorrect password. Please try again.";
      } else if (err.code === "auth/too-many-requests") {
        message = "Too many attempts. Please wait a moment and try again.";
      }

      setError(message);
      setLoading(false);
    }
  };

  return (
    <>
      <style>{styles}</style>
      <div className="login-root">
        {/* ── Left Panel ── */}
        <div className="login-left">
          <div className="login-wordmark">
            <div className="login-wordmark-icon">
              <svg viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
                <rect x="2" y="2" width="7" height="7" rx="1.5" fill="white"/>
                <rect x="11" y="2" width="7" height="7" rx="1.5" fill="white" fillOpacity="0.5"/>
                <rect x="2" y="11" width="7" height="7" rx="1.5" fill="white" fillOpacity="0.5"/>
                <rect x="11" y="11" width="7" height="7" rx="1.5" fill="white" fillOpacity="0.75"/>
              </svg>
            </div>
            <span className="login-wordmark-text">DataHacks Judging</span>
          </div>

          <div className="login-hero">
            <div className="login-eyebrow">
              <span className="login-eyebrow-dot"></span>
              Judge Portal · 2026
            </div>
            <h1 className="login-headline">
              Evaluate.<br />
              Score. <em>Decide.</em>
            </h1>
            <p className="login-subtext">
              Your centralized workspace for reviewing submissions, scoring criteria, and collaborating with fellow judges throughout the event.
            </p>
          </div>

          <p className="login-footer-note">Secure, username-based authentication.</p>
        </div>

        {/* ── Right Panel ── */}
        <div className="login-right">
          <div className="login-card">
            <h2 className="login-card-title">Welcome back</h2>
            <p className="login-card-sub">Enter your name (no spaces) and 4-digit PIN.</p>

            <div className="login-stats">
              <div className="login-stat">
                <span className="login-stat-value">150+</span>
                <span className="login-stat-label">Teams</span>
              </div>
              <div className="login-stat">
                <span className="login-stat-value">8</span>
                <span className="login-stat-label">Tracks</span>
              </div>
              <div className="login-stat">
                <span className="login-stat-value">36h</span>
                <span className="login-stat-label">Duration</span>
              </div>
            </div>

            <div className="login-badges">
              <span className="login-badge"><span className="login-badge-dot"></span>Rubric-based scoring</span>
              <span className="login-badge"><span className="login-badge-dot"></span>Real-time results</span>
              <span className="login-badge"><span className="login-badge-dot"></span>Collaborative notes</span>
            </div>

            <form onSubmit={handleSubmit}>
              <div className="login-form-group">
                <label className="login-label">Username</label>
                <input
                  type="text"
                  className="login-input"
                  placeholder="e.g. ChandraKanth"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  disabled={loading}
                  autoComplete="username"
                />
              </div>

              <div className="login-form-group">
                <label className="login-label">PIN</label>
                <input
                  type="password"
                  className="login-input"
                  placeholder="••••"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  disabled={loading}
                  autoComplete="current-password"
                />
              </div>

              <button
                type="submit"
                className="login-btn"
                disabled={loading}
              >
                {loading ? (
                  <>
                    <span className="login-btn-spinner"></span>
                    Signing in…
                  </>
                ) : (
                  "Sign In"
                )}
              </button>
            </form>

            {error && <div className="login-error">{error}</div>}

            <p className="login-hint">
              Use the username and PIN provided by your event organizer.
              <br />
              Contact them if you have trouble signing in.
            </p>
          </div>
        </div>
      </div>
    </>
  );
}
