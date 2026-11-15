// Log in / Register. Owner: Person C (layout), Person A (accounts).
// Logging in calls POST /api/auth/login and keeps the session token (lib/session.ts); every API call then
// shows that user's own purchases. Accounts are created by `python -m scripts.seed_users`; there are no
// public sign-ups during the demo, so the register page says so instead of pretending to create one.
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ApiError, api } from "../api/client";
import type { SupplierReport } from "../api/types";
import Globe, { type GlobeLayout } from "../components/Globe";
import { currentSession, setSession } from "../lib/session";
import { toGlobe } from "../lib/suppliers";

function loginError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 401) return "That email and password don't match an account.";
    if (e.status === 429) return "Too many tries. Wait a few minutes, then try again.";
    if (e.status === 422) return "Enter your email and password.";
  }
  return "We couldn't reach Accountable. Check your connection and try again.";
}

export default function Auth({ mode }: { mode: "login" | "register" }) {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [reports, setReports] = useState<SupplierReport[]>([]);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const layout = useRef<GlobeLayout>({ cx: 0.5, cy: 1.05, r: 1.6 });
  const isLogin = mode === "login";
  const expired = params.get("expired") === "1";
  const signedIn = currentSession()?.user;

  useEffect(() => {
    api.allSuppliers().then(setReports).catch(() => setReports([]));
  }, []);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!email.trim() || !password) {
      setError("Enter your email and password.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const { token, user } = await api.login(email.trim(), password);
      setSession({ token, user });
      navigate("/dashboard");
    } catch (err) {
      setError(loginError(err));
      setBusy(false);
    }
  };

  return (
    <div className="auth">
      <div className="landing-globe auth-globe" aria-hidden="true">
        <Globe {...toGlobe(reports)} interactive={false} layoutRef={layout} spin={0.03} />
      </div>

      <header className="public-bar">
        <Link to="/" className="brand"><span className="brand-mark" /> Accountable</Link>
      </header>

      {isLogin ? (
        <form className="auth-card" onSubmit={onSubmit} noValidate aria-describedby={error ? "auth-error" : undefined}>
          <h1>Welcome back</h1>
          {expired && !error && <p className="auth-demo" role="status">Your session ended. Log in again.</p>}
          {signedIn && !expired && !error && (
            <p className="auth-demo" role="status">
              You're logged in as {signedIn.name}. <Link to="/dashboard">Go to your dashboard</Link>, or log in as
              someone else.
            </p>
          )}
          {error && <p id="auth-error" className="auth-error" role="alert">{error}</p>}

          <label className="field">
            <span>Email</span>
            <input name="email" type="email" autoComplete="username" placeholder="you@example.com"
                   value={email} onChange={(e) => setEmail(e.target.value)} maxLength={254} required />
          </label>
          <label className="field">
            <span>Password</span>
            <input name="password" type="password" autoComplete="current-password" placeholder="Your password"
                   value={password} onChange={(e) => setPassword(e.target.value)} maxLength={200} required />
          </label>

          <button type="submit" className="button-light button-lg button-block" disabled={busy}>
            {busy ? "Logging in…" : "Log in"}
          </button>
          <p className="auth-switch">
            Just looking? <Link to="/dashboard">Try the demo</Link>
          </p>
        </form>
      ) : (
        <div className="auth-card">
          <h1>Create your account</h1>
          <p className="auth-demo">
            Sign-ups aren't open yet. Log in with an existing account, or try the demo.
          </p>
          <Link to="/login" className="button-light button-lg button-block">Log in</Link>
          <p className="auth-switch">
            Just looking? <Link to="/dashboard">Try the demo</Link>
          </p>
        </div>
      )}
    </div>
  );
}
