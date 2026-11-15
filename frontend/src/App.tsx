import { useEffect, useState } from "react";
import { Link, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api } from "./api/client";
import { SESSION_KEY, clearSession, currentSession, onSessionChange } from "./lib/session";
import Auth from "./pages/Auth";
import Dashboard from "./pages/Dashboard";
import Landing from "./pages/Landing";
import MapPage from "./pages/MapPage";
import MerchantDetail from "./pages/MerchantDetail";
import Values from "./pages/Values";

// Public pages draw their own chrome; the app pages share the top bar.
const PUBLIC = ["/", "/login", "/register"];
// Pages that own the whole viewport (globe backdrops) skip the centered content column.
const FULL_BLEED = ["/dashboard", "/map", "/values"];

function Account() {
  const navigate = useNavigate();
  const [session, setSession] = useState(currentSession);
  useEffect(() => onSessionChange(() => setSession(currentSession())), []);

  const logOut = async () => {
    try {
      await api.logout();
    } catch {
      // Ending the session on the server is best effort; the browser forgets it either way.
    }
    clearSession();
    navigate("/");
  };

  if (!session) {
    return (
      <div className="account">
        <span className="account-name">Sample data</span>
        <Link to="/login" className="account-button">Log in</Link>
      </div>
    );
  }
  return (
    <div className="account">
      <span className="account-name" title={session.user.email}>{session.user.name}</span>
      <button type="button" className="account-button" onClick={logOut}>Log out</button>
    </div>
  );
}

export default function App() {
  const { pathname } = useLocation();
  const isPublic = PUBLIC.includes(pathname);
  const fullBleed = isPublic || FULL_BLEED.includes(pathname) || pathname.startsWith("/merchant/");

  // Logged in or out in another tab: reload, so this tab never keeps showing the previous person's data.
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key === SESSION_KEY || e.key === null) window.location.reload();
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  // Globe pages paint the whole document dark, so no light ground ever shows past their edges.
  useEffect(() => {
    document.body.classList.toggle("on-field", fullBleed);
  }, [fullBleed]);

  return (
    <div className="app">
      {!isPublic && (
        <header className="topbar">
          <NavLink to="/dashboard" className="brand">
            <span className="brand-mark" /> Accountable
          </NavLink>
          <div className="topbar-right">
            <nav>
              <NavLink to="/dashboard">Spending</NavLink>
              <NavLink to="/map">Map</NavLink>
              <NavLink to="/values">My values</NavLink>
            </nav>
            <Account />
          </div>
        </header>
      )}
      <main className={fullBleed ? "main-full" : undefined}>
        <Routes>
          <Route path="/" element={<Landing />} />
          <Route path="/login" element={<Auth mode="login" />} />
          <Route path="/register" element={<Auth mode="register" />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/map" element={<MapPage />} />
          <Route path="/values" element={<Values />} />
          <Route path="/merchant/:id" element={<MerchantDetail />} />
        </Routes>
      </main>
    </div>
  );
}
