import { BrowserRouter, Routes, Route } from "react-router-dom";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Evaluate from "./pages/Evaluate";
import Leaderboard from "./pages/Leaderboard";
import ProtectedRoute from "./components/ProtectedRoute";
import LeaderboardRoute from "./components/LeaderboardRoute";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Login />} />

        <Route
          path="/dashboard"
          element={
            <ProtectedRoute>
              <Dashboard />
            </ProtectedRoute>
          }
        />

        <Route
          path="/evaluate/:projectId"
          element={
            <ProtectedRoute>
              <Evaluate />
            </ProtectedRoute>
          }
        />

        <Route
          path="/leaderboard"
          element={
            <LeaderboardRoute>
              <Leaderboard />
            </LeaderboardRoute>
          }
        />
      </Routes>
    </BrowserRouter>
  );
}