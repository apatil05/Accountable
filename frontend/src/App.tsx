import { useEffect } from "react";
import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import Auth from "./pages/Auth";
import Dashboard from "./pages/Dashboard";
import Landing from "./pages/Landing";
import MapPage from "./pages/MapPage";
import MerchantDetail from "./pages/MerchantDetail";
import Values from "./pages/Values";

// Public pages draw their own chrome; the app pages share the top bar.
const PUBLIC = ["/", "/login", "/register"];
// Pages that own the whole viewport (globe backdrops) skip the centered content column.
const FULL_BLEED = ["/dashboard", "/map"];

export default function App() {
  const { pathname } = useLocation();
  const isPublic = PUBLIC.includes(pathname);
  const fullBleed = isPublic || FULL_BLEED.includes(pathname);

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
          <nav>
            <NavLink to="/dashboard">Spending</NavLink>
            <NavLink to="/map">Map</NavLink>
            <NavLink to="/values">My values</NavLink>
          </nav>
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
