import { useEffect, useState } from "react";
import { auth } from "../firebase";
import { onAuthStateChanged } from "firebase/auth";
import { Navigate } from "react-router-dom";

// Fallback for the known organizer account. The real gate is the `admin`
// custom claim; this keeps the console reachable before claims are minted.
const ORGANIZER_EMAIL = "ds3@datahacks2026.ucsd";

const guardStyles = `
  .og-guard {
    min-height: 100vh; display: flex; align-items: center; justify-content: center;
    background: #F7F5F0; font-family: 'DM Sans', sans-serif;
  }
  .og-guard-inner {
    display: flex; flex-direction: column; align-items: center; gap: 16px;
    color: #8a7f6e; font-size: 14px;
  }
  .og-guard-ring {
    width: 36px; height: 36px; border: 3px solid #E8E4DC;
    border-top-color: #C9A96E; border-radius: 50%;
    animation: og-guard-spin 0.9s linear infinite;
  }
  @keyframes og-guard-spin { to { transform: rotate(360deg); } }
`;

/**
 * Route guard for the organizer console.
 *
 * Allowed when the signed-in user carries the `admin` custom claim, or (as a
 * fallback while claims are being provisioned) when their email matches the
 * known organizer account. Anyone else is bounced to their own dashboard.
 */
export default function OrganizerRoute({ children }) {
  // "loading" | "anon" | "allowed" | "denied"
  const [status, setStatus] = useState("loading");

  useEffect(() => {
    let cancelled = false;

    const unsub = onAuthStateChanged(auth, async (user) => {
      if (cancelled) return;

      if (!user) {
        setStatus("anon");
        return;
      }

      let isAdmin = false;
      try {
        const token = await user.getIdTokenResult();
        isAdmin = token?.claims?.admin === true;
      } catch (err) {
        // Never fail closed silently — log it, then fall back to the email check.
        console.error("OrganizerRoute: could not read ID token claims:", err);
      }

      if (cancelled) return;
      setStatus(isAdmin || user.email === ORGANIZER_EMAIL ? "allowed" : "denied");
    });

    return () => {
      cancelled = true;
      unsub();
    };
  }, []);

  if (status === "loading") {
    return (
      <>
        <style>{guardStyles}</style>
        <div className="og-guard">
          <div className="og-guard-inner" role="status">
            <div className="og-guard-ring" />
            Checking organizer access…
          </div>
        </div>
      </>
    );
  }

  if (status === "anon") return <Navigate to="/organizer/login" replace />;
  if (status === "denied") return <Navigate to="/dashboard" replace />;

  return children;
}
