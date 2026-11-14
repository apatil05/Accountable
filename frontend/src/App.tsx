import { NavLink, Route, Routes } from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import MapPage from "./pages/MapPage";
import MerchantDetail from "./pages/MerchantDetail";
import Values from "./pages/Values";

export default function App() {
  return (
    <div className="app">
      <header className="topbar">
        <NavLink to="/" className="brand">
          <span className="brand-mark" /> Accountable
        </NavLink>
        <nav>
          <NavLink to="/" end>Spending</NavLink>
          <NavLink to="/map">Map</NavLink>
          <NavLink to="/values">My values</NavLink>
        </nav>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/map" element={<MapPage />} />
          <Route path="/values" element={<Values />} />
          <Route path="/merchant/:id" element={<MerchantDetail />} />
        </Routes>
      </main>
    </div>
  );
}
