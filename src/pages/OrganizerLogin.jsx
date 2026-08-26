import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { auth } from "../firebase";
import {
  onAuthStateChanged,
  setPersistence,
  browserLocalPersistence,
  signInWithEmailAndPassword
} from "firebase/auth";

/**
 * Organizer sign-in.
 *
 * Deliberately separate from the judge login. The judge form builds a password
 * as `DH` + a zero-padded 4-digit PIN, which is right for judges and makes it
 * impossible to type an ordinary password. Organizers need their password sent
 * through untouched, so this page does exactly that.
 */

const ORGANIZER_DOMAIN = "datahacks2026.ucsd";

const styles = `
  @import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=DM+Sans:wght@300;400;500;600&display=swap');

  .ol-root {
    min-height: 100vh; display: flex; align-items: center; justify-content: center;
    background: #F7F5F0; font-family: 'DM Sans', sans-serif; color: #1a1a1a;
    padding: 24px;
  }
  .ol-card {
    background: #fff; border: 1px solid #E8E4DC; border-radius: 16px;
    padding: 40px 40px 34px; width: 100%; max-width: 400px;
    box-shadow: 0 4px 24px rgba(0,0,0,0.04);
  }
  @media (max-width: 460px) { .ol-card { padding: 30px 24px 26px; } }

  .ol-brand { display: flex; align-items: center; gap: 11px; margin-bottom: 26px; }
  .ol-brand-icon {
    width: 30px; height: 30px; background: #1a1a1a; border-radius: 7px;
    display: flex; align-items: center; justify-content: center; flex-shrink: 0;
  }
  .ol-brand-icon svg { width: 16px; height: 16px; }
  .ol-brand-text { font-size: 14px; font-weight: 600; letter-spacing: -0.01em; }

  .ol-eyebrow {
    display: inline-flex; align-items: center; gap: 7px; font-size: 11px;
    font-weight: 600; letter-spacing: 0.1em; text-transform: uppercase;
    color: #8a7f6e; margin-bottom: 10px;
  }
  .ol-eyebrow-dot { width: 5px; height: 5px; border-radius: 50%; background: #C9A96E; }

  .ol-title {
    font-family: 'DM Serif Display', serif; font-size: 27px; letter-spacing: -0.02em;
    line-height: 1.15; color: #111; margin-bottom: 7px;
  }
  .ol-sub { font-size: 13.5px; color: #8a7f6e; font-weight: 300; margin-bottom: 26px; line-height: 1.55; }

  .ol-field { margin-bottom: 16px; }
  .ol-label {
    display: block; font-size: 12px; font-weight: 600; color: #5a534a;
    margin-bottom: 6px; letter-spacing: 0.01em;
  }
  .ol-input {
    width: 100%; border: 1.5px solid #E0DBD2; border-radius: 10px; background: #F7F5F0;
    font-family: 'DM Sans', sans-serif; font-size: 15px; color: #1a1a1a;
    padding: 12px 14px; outline: none; transition: border-color 0.2s, box-shadow 0.2s, background 0.2s;
  }
  .ol-input:focus {
    border-color: #C9A96E; box-shadow: 0 0 0 3px rgba(201,169,110,0.12); background: #fff;
  }
  .ol-input::placeholder { color: #c0b8ae; }
  .ol-hint { font-size: 11.5px; color: #a09488; margin-top: 5px; }

  .ol-btn {
    width: 100%; padding: 13px 20px; background: #1a1a1a; color: #fff; border: none;
    border-radius: 10px; font-family: 'DM Sans', sans-serif; font-size: 15px;
    font-weight: 500; cursor: pointer; margin-top: 8px;
    transition: background 0.2s, transform 0.15s; letter-spacing: -0.01em;
  }
  .ol-btn:hover:not(:disabled) { background: #2d2d2d; transform: translateY(-1px); }
  .ol-btn:disabled { background: #d0ccc6; cursor: not-allowed; }

  .ol-error {
    display: flex; align-items: flex-start; gap: 9px;
    background: #FCEEEC; border: 1px solid #E9BDB5; color: #8C3325;
    border-radius: 10px; padding: 11px 14px; font-size: 13px; line-height: 1.5;
    margin-bottom: 18px;
  }
  .ol-error svg { flex-shrink: 0; margin-top: 2px; }

  .ol-foot {
    margin-top: 22px; padding-top: 18px; border-top: 1px solid #F0EDE7;
    font-size: 12.5px; color: #a09488; text-align: center;
  }
  .ol-foot a { color: #5a534a; text-decoration: underline; text-underline-offset: 2px; }
  .ol-spinner {
    width: 15px; height: 15px; border: 2px solid rgba(255,255,255,0.35);
    border-top-color: #fff; border-radius: 50%; display: inline-block;
    animation: ol-spin 0.7s linear infinite;
  }
  @keyframes ol-spin { to { transform: rotate(360deg); } }
`;

export default function OrganizerLogin() {
  const navigate = useNavigate();
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let mounted = true;
    const unsub = onAuthStateChanged(auth, (user) => {
      if (mounted && user) navigate("/organizer", { replace: true });
    });
    return () => { mounted = false; unsub(); };
  }, [navigate]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");

    if (!identifier.trim() || !password) {
      setError("Enter your organizer account and password.");
      return;
    }

    setLoading(true);
    try {
      // Accept either a bare username or a full address, and send the password
      // exactly as typed — no PIN transformation.
      const raw = identifier.trim().toLowerCase();
      const email = raw.includes("@") ? raw : `${raw}@${ORGANIZER_DOMAIN}`;

      await setPersistence(auth, browserLocalPersistence);
      await signInWithEmailAndPassword(auth, email, password);
      navigate("/organizer", { replace: true });
    } catch (err) {
      console.error("Organizer sign-in failed:", err);
      const map = {
        "auth/invalid-credential": "That account and password don't match.",
        "auth/user-not-found": "No organizer account with that name.",
        "auth/wrong-password": "Incorrect password.",
        "auth/invalid-email": "That doesn't look like a valid account name.",
        "auth/too-many-requests": "Too many attempts. Wait a moment and try again.",
        "auth/network-request-failed": "Network problem — check your connection."
      };
      setError(map[err.code] || "Sign-in failed. Please try again.");
      setLoading(false);
    }
  };

  return (
    <>
      <style>{styles}</style>
      <div className="ol-root">
        <div className="ol-card">
          <div className="ol-brand">
            <div className="ol-brand-icon">
              <svg viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
                <rect x="2" y="2" width="7" height="7" rx="1.5" fill="white"/>
                <rect x="11" y="2" width="7" height="7" rx="1.5" fill="white" fillOpacity="0.5"/>
                <rect x="2" y="11" width="7" height="7" rx="1.5" fill="white" fillOpacity="0.5"/>
                <rect x="11" y="11" width="7" height="7" rx="1.5" fill="white" fillOpacity="0.75"/>
              </svg>
            </div>
            <span className="ol-brand-text">DataHacks Judging</span>
          </div>

          <div className="ol-eyebrow"><span className="ol-eyebrow-dot" />Organizer</div>
          <h1 className="ol-title">Sign in to the console</h1>
          <p className="ol-sub">Check judges in, verify the assignment, and watch coverage during judging.</p>

          {error && (
            <div className="ol-error" role="alert">
              <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <circle cx="8" cy="8" r="7" stroke="currentColor" strokeWidth="1.5"/>
                <path d="M8 4.5v4.2M8 11.2v.1" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"/>
              </svg>
              <span>{error}</span>
            </div>
          )}

          <form onSubmit={handleSubmit} noValidate>
            <div className="ol-field">
              <label className="ol-label" htmlFor="ol-id">Organizer account</label>
              <input
                id="ol-id" className="ol-input" type="text" autoComplete="username"
                placeholder="ds3" value={identifier} autoFocus
                onChange={(e) => setIdentifier(e.target.value)}
              />
              <p className="ol-hint">Just the name — <code>@{ORGANIZER_DOMAIN}</code> is added for you.</p>
            </div>

            <div className="ol-field">
              <label className="ol-label" htmlFor="ol-pw">Password</label>
              <input
                id="ol-pw" className="ol-input" type="password" autoComplete="current-password"
                placeholder="Your organizer password" value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              <p className="ol-hint">Sent exactly as typed — this is not a judge PIN.</p>
            </div>

            <button className="ol-btn" type="submit" disabled={loading}>
              {loading ? <span className="ol-spinner" /> : "Sign in"}
            </button>
          </form>

          <p className="ol-foot">
            Judging a track? <a href="/">Use the judge sign-in</a> instead.
          </p>
        </div>
      </div>
    </>
  );
}
