// Public landing page: a huge decorative globe and the way in. Owner: Person C.
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { SupplierReport } from "../api/types";
import Globe, { type GlobeLayout } from "../components/Globe";
import { toGlobe } from "../lib/suppliers";

export default function Landing() {
  const [reports, setReports] = useState<SupplierReport[]>([]);
  // The globe rises from the lower right like a horizon; on phones it sits low and centered.
  const narrow = typeof window !== "undefined" && window.matchMedia("(max-width: 900px)").matches;
  const layout = useRef<GlobeLayout>(narrow ? { cx: 0.5, cy: 0.98, r: 1.5 } : { cx: 0.72, cy: 0.74, r: 1.42 });

  useEffect(() => {
    // Real supplier routes from import records, for atmosphere; if the API is down the globe spins without them.
    api.allSuppliers().then(setReports).catch(() => setReports([]));
  }, []);

  return (
    <div className="landing">
      <div className="landing-globe" aria-hidden="true">
        <Globe {...toGlobe(reports)} interactive={false} layoutRef={layout} spin={0.035} />
      </div>

      <header className="public-bar">
        <Link to="/" className="brand"><span className="brand-mark" /> Accountable</Link>
        <nav className="public-actions">
          <Link to="/dashboard" className="button-ghost">Try the demo</Link>
          <Link to="/login" className="button-light">Log in</Link>
        </nav>
      </header>

      <section className="landing-copy">
        <h1>Know who you're paying.</h1>
        <p>
          Accountable turns your card purchases into a map of the companies you pay and where their products come
          from. Facts with sources, never ratings, so you can decide what matters.
        </p>
        <div className="landing-ctas">
          <Link to="/login" className="button-light button-lg">Log in</Link>
          <Link to="/dashboard" className="button-ghost button-lg">Try the demo</Link>
        </div>
        <p className="landing-note">Demo build: runs on sample Capital One (Nessie) transactions.</p>
      </section>
    </div>
  );
}
