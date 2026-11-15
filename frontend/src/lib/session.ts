// Who is logged in. Owner: Person A.
// The session token lives in localStorage so a refresh keeps you logged in. Storage can be blocked
// (private windows, strict settings): then the login lasts for this page only, and nothing crashes.
// Why not an httpOnly cookie: the frontend's production host isn't decided (it may be cross-origin from the
// API), and a bearer token works either way. localStorage is readable by injected script, so the app must
// never render raw HTML (it has no dangerouslySetInnerHTML); sessions also expire after 7 days server-side.
import type { AuthUser } from "../api/types";

export const SESSION_KEY = "accountable.session";
const KEY = SESSION_KEY;
const CHANGED = "accountable:session";

export interface Session {
  token: string;
  user: AuthUser;
}

// Used only when localStorage is unavailable, so a login still works until the page is closed.
let memory: Session | null = null;

function isSession(value: unknown): value is Session {
  const s = value as Session | null;
  return (
    !!s && typeof s.token === "string" && s.token.length > 0 && !!s.user &&
    typeof s.user.email === "string" && typeof s.user.name === "string" && typeof s.user.customer_id === "string"
  );
}

function stored(): Session | null {
  try {
    const raw = window.localStorage.getItem(KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    return isSession(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export function currentSession(): Session | null {
  return stored() ?? memory;
}

function announce(): void {
  window.dispatchEvent(new Event(CHANGED));
}

export function setSession(session: Session): void {
  memory = session;
  try {
    window.localStorage.setItem(KEY, JSON.stringify(session));
  } catch {
    // storage blocked: `memory` keeps the login for this page
  }
  announce();
}

export function clearSession(): void {
  memory = null;
  try {
    window.localStorage.removeItem(KEY);
  } catch {
    // nothing was stored
  }
  announce();
}

export function onSessionChange(listener: () => void): () => void {
  window.addEventListener(CHANGED, listener);
  window.addEventListener("storage", listener); // logged in or out in another tab
  return () => {
    window.removeEventListener(CHANGED, listener);
    window.removeEventListener("storage", listener);
  };
}
