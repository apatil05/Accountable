import type {
  Alternative,
  EthicsReport,
  SpendingSummary,
  SupplyChain,
  UserProfile,
} from "./types";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json() as Promise<T>;
}

export const api = {
  health: () => req<{ ok: boolean; mock_data: boolean; gemini_configured: boolean }>("/health"),
  summary: () => req<SpendingSummary>("/spending/summary"),
  profile: () => req<UserProfile>("/profile"),
  saveProfile: (p: UserProfile) => req<UserProfile>("/profile", { method: "PUT", body: JSON.stringify(p) }),
  ethics: (id: string) => req<EthicsReport>(`/merchants/${id}/ethics`),
  supplyChain: (id: string) => req<SupplyChain>(`/merchants/${id}/supply-chain`),
  alternatives: (id: string) => req<Alternative[]>(`/merchants/${id}/alternatives`),
  allSupplyChains: () => req<SupplyChain[]>("/supply-chains"),
};
