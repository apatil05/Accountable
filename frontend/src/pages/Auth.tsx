// Log in / Register. Owner: Person C.
// DEMO ONLY: there is no account system yet (backend auth is Person A's area). Nothing typed here is
// stored or sent anywhere; submitting just opens the demo dashboard. Replace `onSubmit` with a real
// call (e.g. POST /api/auth/login) when the backend supports it.
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { SupplyChain } from "../api/types";
import Globe, { type GlobeLayout } from "../components/Globe";

export default function Auth({ mode }: { mode: "login" | "register" }) {
  const navigate = useNavigate();
  const [chains, setChains] = useState<SupplyChain[]>([]);
  const layout = useRef<GlobeLayout>({ cx: 0.5, cy: 1.05, r: 1.6 });
  const isLogin = mode === "login";

  useEffect(() => {
    api.allSupplyChains().then(setChains).catch(() => setChains([]));
  }, []);

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    navigate("/dashboard");
  };

  return (
    <div className="auth">
      <div className="landing-globe auth-globe" aria-hidden="true">
        <Globe chains={chains} interactive={false} layoutRef={layout} spin={0.03} />
      </div>

      <header className="public-bar">
        <Link to="/" className="brand"><span className="brand-mark" /> Accountable</Link>
      </header>

      <form className="auth-card" onSubmit={onSubmit} noValidate>
        <h1>{isLogin ? "Welcome back" : "Create your account"}</h1>
        <p className="auth-demo">Demo: accounts aren't connected yet. Nothing you type is saved or sent.</p>

        {!isLogin && (
          <label className="field">
            <span>Name</span>
            <input name="name" autoComplete="off" placeholder="Your name" />
          </label>
        )}
        <label className="field">
          <span>Email</span>
          <input name="email" type="email" autoComplete="off" placeholder="you@example.com" />
        </label>
        <label className="field">
          <span>Password</span>
          <input name="password" type="password" autoComplete="off" placeholder={isLogin ? "Your password" : "At least 8 characters"} />
        </label>

        <button type="submit" className="button-light button-lg button-block">
          {isLogin ? "Log in" : "Create account"}
        </button>
        <p className="auth-switch">
          {isLogin ? "New to Accountable? " : "Already have an account? "}
          <Link to={isLogin ? "/register" : "/login"}>{isLogin ? "Create an account" : "Log in"}</Link>
        </p>
      </form>
    </div>
  );
}
