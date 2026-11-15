import { clearSession, currentSession } from "../lib/session";
import type {
  Alternative,
  AuthUser,
  ResearchStatus,
  LoginResponse,
  CompanyEmissions,
  EthicsReport,
  LaborSummary,
  SpendingSummary,
  SupplierReport,
  SupplyChain,
  UserProfile,
} from "./types";

/** An error with the HTTP status, so callers can tell "wrong password" from "server down". */
export class ApiError extends Error {
  constructor(readonly status: number, readonly detail: string) {
    super(`${status} ${detail}`);
  }
}

async function detailOf(res: Response): Promise<string> {
  const text = await res.text();
  try {
    const body = JSON.parse(text) as { detail?: unknown };
    return typeof body.detail === "string" ? body.detail : text;
  } catch {
    return text;
  }
}

// Pages anyone can see. A stale token there is dropped quietly instead of bouncing the visitor to /login.
const PUBLIC_PAGES = ["/", "/login", "/register"];

interface Auth {
  /** Send the session token (default). Off for login, so a typo can't end the session you already have. */
  sendToken?: boolean;
  /** Treat a 401 as "your session ended" (default). Off for logout, which ends it anyway. */
  expireOn401?: boolean;
}

async function req<T>(path: string, init?: RequestInit, { sendToken = true, expireOn401 = true }: Auth = {}): Promise<T> {
  const token = sendToken ? currentSession()?.token : undefined;
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init?.headers,
    },
  });
  if (res.status === 401 && token && expireOn401) {
    // The session ended (expired, or logged out elsewhere). Never fall back to someone else's data
    // inside the app; on a public page just forget the token and show the public view.
    clearSession();
    if (PUBLIC_PAGES.includes(window.location.pathname)) return req<T>(path, init, { sendToken: false });
    window.location.assign("/login?expired=1");
  }
  if (!res.ok) throw new ApiError(res.status, await detailOf(res));
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  login: (email: string, password: string) =>
    req<LoginResponse>("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }, { sendToken: false }),
  logout: () => req<void>("/auth/logout", { method: "POST" }, { expireOn401: false }),
  me: () => req<AuthUser>("/auth/me"),
  health: () => req<{ ok: boolean; mock_data: boolean; gemini_configured: boolean }>("/health"),
  summary: () => req<SpendingSummary>("/spending/summary"),
  profile: () => req<UserProfile>("/profile"),
  saveProfile: (p: UserProfile) => req<UserProfile>("/profile", { method: "PUT", body: JSON.stringify(p) }),
  ethics: (id: string) => req<EthicsReport>(`/merchants/${id}/ethics`),
  /** Start (or check on) live research for a company that has none yet. */
  requestResearch: (id: string) => req<ResearchStatus>(`/merchants/${id}/research`, { method: "POST" }),
  supplyChain: (id: string) => req<SupplyChain>(`/merchants/${id}/supply-chain`),
  alternatives: (id: string) => req<Alternative[]>(`/merchants/${id}/alternatives`),
  allSupplyChains: () => req<SupplyChain[]>("/supply-chains"),
  suppliers: (id: string) => req<SupplierReport>(`/merchants/${id}/suppliers`),
  allSuppliers: () => req<SupplierReport[]>("/suppliers"),
  /** Any company by name, looked up on ImportYeti (the Map's search bar). */
  lookupSuppliers: (name: string) => req<SupplierReport>(`/suppliers/lookup?name=${encodeURIComponent(name)}`),
  emissions: (id: string) => req<CompanyEmissions[]>(`/merchants/${id}/emissions`),
  labor: (id: string) => req<LaborSummary>(`/merchants/${id}/labor`),
};
