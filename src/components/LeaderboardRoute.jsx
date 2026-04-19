import { useEffect, useState } from "react";
import { auth } from "../firebase";
import { onAuthStateChanged } from "firebase/auth";
import { Navigate } from "react-router-dom";

const ALLOWED_EMAIL = "ds3@ucsd.edu";

export default function LeaderboardRoute({ children }) {
  const [user, setUser] = useState(undefined);

  useEffect(() => {
    return onAuthStateChanged(auth, (u) => setUser(u));
  }, []);

  if (user === undefined) return null; // still resolving
  if (!user) return <Navigate to="/" replace />;
  if (user.email !== ALLOWED_EMAIL) return <Navigate to="/dashboard" replace />;

  return children;
}
