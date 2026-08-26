import { BrowserRouter, Routes, Route } from "react-router-dom";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Evaluate from "./pages/Evaluate";
import Leaderboard from "./pages/Leaderboard";
import Organizer from "./pages/Organizer";
import OrganizerLogin from "./pages/OrganizerLogin";
import ProtectedRoute from "./components/ProtectedRoute";
import LeaderboardRoute from "./components/LeaderboardRoute";
import OrganizerRoute from "./components/OrganizerRoute";
import WipBanner from "./components/WipBanner";

export default function App() {
  return (
    <BrowserRouter>
      <WipBanner />
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

        <Route path="/organizer/login" element={<OrganizerLogin />} />

        <Route
          path="/organizer"
          element={
            <OrganizerRoute>
              <Organizer />
            </OrganizerRoute>
          }
        />
      </Routes>
    </BrowserRouter>
  );
}