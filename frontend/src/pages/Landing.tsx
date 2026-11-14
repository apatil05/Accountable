// Public landing page: a huge decorative globe and the way in. Owner: Person C.
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { SupplyChain } from "../api/types";
import Globe, { type GlobeLayout } from "../components/Globe";

export default function Landing() {
  const [chains, setChains] = useState<SupplyChain[]>([]);
  // The globe rises from the lower right like a horizon; on phones it sits low and centered.
  const narrow = typeof window !== "undefined" && window.matchMedia("(max-width: 900px)").matches;
  const layout = useRef<GlobeLayout>(narrow ? { cx: 0.5, cy: 0.98, r: 1.5 } : { cx: 0.72, cy: 0.74, r: 1.42 });

  useEffect(() => {
    // Routes are for show here; if the API is down the globe simply spins without them.
    api.allSupplyChains().then(setChains).catch(() => setChains([]));
  }, []);

  return (
    <div className="landing">
      <div className="landing-globe" aria-hidden="true">
        <Globe chains={chains} interactive={false} layoutRef={layout} spin={0.035} />
      </div>

      <header className="public-bar">
        <Link to="/" className="brand"><span className="brand-mark" /> Accountable</Link>
        <nav className="public-actions">
          <Link to="/login" className="button-ghost">Log in</Link>
          <Link to="/register" className="button-light">Register</Link>
        </nav>
      </header>

      <section className="landing-copy">
        <h1>Know who you're paying.</h1>
        <p>
          Accountable turns your card purchases into a map of the companies you pay and where their products come
          from. Facts with sources, never ratings, so you can decide what matters.
        </p>
        <div className="landing-ctas">
          <Link to="/register" className="button-light button-lg">Create an account</Link>
          <Link to="/login" className="button-ghost button-lg">Log in</Link>
        </div>
        <p className="landing-note">Demo build: runs on sample Capital One (Nessie) transactions.</p>
      </section>
    </div>
  );
}
