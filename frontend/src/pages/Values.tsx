// Profile + values. Owner: Person C.
// The profile shows what Accountable knows from your Capital One (Nessie) data; the values form saves the
// UserProfile document. Fields no longer offered in the UI (trusted sources, prefer local) are kept as-is
// when saving, so the backend contract is unchanged. Political topics are not offered.
import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { CATEGORIES, CATEGORY_LABELS, type SpendingSummary, type SupplyChain, type UserProfile } from "../api/types";

const LEVELS = ["Not at all", "A little", "Somewhat", "Matters", "A lot", "Most of all"];
const TOPIC_HINTS: Record<string, string> = {
  labor: "Wages, working conditions and how workers are treated in the supply chain.",
  environment: "Emissions, waste, water and land use.",
  animal_welfare: "Animal testing, farming practices and sourcing.",
  community: "Local jobs, taxes and impact on the places a company operates.",
  privacy: "How much data a company collects about you and what it does with it.",
};
const SHOWN = CATEGORIES; // "political" was removed from the categories in the backend
const usd = (n: number) => n.toLocaleString("en-US", { style: "currency", currency: "USD" });
const shortDate = (iso: string) => {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
};

export default function Values() {
  const [p, setP] = useState<UserProfile | null>(null);
  const [summary, setSummary] = useState<SpendingSummary | null>(null);
  const [chains, setChains] = useState<SupplyChain[]>([]);
  const [mock, setMock] = useState<boolean | null>(null);
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.profile().then(setP).catch((e) => setErr(String(e)));
    api.summary().then(setSummary).catch(() => setSummary(null));
    api.allSupplyChains().then(setChains).catch(() => setChains([]));
    api.health().then((h) => setMock(h.mock_data)).catch(() => setMock(null));
  }, []);

  const facts = useMemo(() => {
    if (!summary) return null;
    const ms = summary.merchants;
    const purchases = ms.reduce((a, m) => a + m.purchase_count, 0);
    const latest = ms.reduce((a, m) => (m.last_purchase > a ? m.last_purchase : a), "");
    const frequent = ms.reduce<(typeof ms)[number] | null>((b, m) => (!b || m.purchase_count > b.purchase_count ? m : b), null);
    const byCat = new Map<string, number>();
    ms.forEach((m) => m.category[0] && byCat.set(m.category[0], (byCat.get(m.category[0]) ?? 0) + m.total_spent));
    const topCategory = [...byCat.entries()].sort((a, b) => b[1] - a[1])[0]?.[0];
    return { purchases, latest, frequent, topCategory };
  }, [summary]);
  const home = chains[0]?.stages[chains[0].stages.length - 1]?.place;

  if (err) {
    return (
      <div className="vp">
        <div className="state-panel on-dark" role="alert">
          <h1>We couldn't load your profile</h1>
          <p>The Accountable server didn't respond. Check that it's running, then try again.</p>
          <button className="button-light" onClick={() => window.location.reload()}>Try again</button>
        </div>
      </div>
    );
  }
  if (!p) return <div className="vp"><p className="vp-loading" role="status">Loading your profile…</p></div>;

  const update = (patch: Partial<UserProfile>) => {
    setStatus("idle");
    setP({ ...p, ...patch });
  };
  const save = () => {
    setStatus("saving");
    api.saveProfile(p).then(() => setStatus("saved")).catch(() => setStatus("error"));
  };
  const initials = p.name.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase() || "?";

  return (
    <div className="vp">
      <div className="vp-grid">
        {/* Profile: what Accountable knows from your bank data */}
        <aside className="vp-profile" aria-labelledby="profile-title">
          <div className="vp-id">
            <span className="vp-avatar" aria-hidden="true">{initials}</span>
            <div>
              <h1 id="profile-title">{p.name}</h1>
              <p>{home ? `Shopping around ${home}` : "Your profile"}</p>
            </div>
          </div>

          <dl className="vp-facts">
            {summary && (
              <>
                <div><dt>Spent</dt><dd>{usd(summary.total_spent)}</dd></div>
                <div><dt>Purchases</dt><dd>{facts?.purchases}</dd></div>
                <div><dt>Companies</dt><dd>{summary.merchant_count}</dd></div>
                {facts?.latest && <div><dt>Latest purchase</dt><dd>{shortDate(facts.latest)}</dd></div>}
                {facts?.frequent && <div><dt>Most visited</dt><dd>{facts.frequent.name}</dd></div>}
                {facts?.topCategory && <div><dt>Top category</dt><dd>{facts.topCategory}</dd></div>}
              </>
            )}
          </dl>

          <div className="vp-source">
            <h2>Connected data</h2>
            <p>
              <span className="vp-dot" aria-hidden="true" />
              {mock === null ? "Capital One (Nessie)" : mock ? "Sample Capital One (Nessie) data" : "Capital One Nessie sandbox"}
            </p>
            {summary && <p className="vp-mono">Customer ID {summary.customer_id}</p>}
          </div>
        </aside>

        {/* Values: what to weigh each company against */}
        <section className="vp-values" aria-labelledby="values-title">
          <h2 id="values-title" className="vp-title">What matters to you?</h2>
          <p className="vp-lede">
            Accountable never rates companies. Tell us which issues you care about and we'll keep them in view as you
            look at the companies you pay. Nothing here is shared.
          </p>

          <ul className="vp-topics">
            {SHOWN.map((c) => {
              const v = p.weights[c] ?? 0;
              return (
                <li key={c} className="vp-topic">
                  <div className="vp-topic-head">
                    <label htmlFor={`w-${c}`}>{CATEGORY_LABELS[c]}</label>
                    <span className={`vp-level ${v >= 4 ? "is-high" : ""}`}>{LEVELS[v]}</span>
                  </div>
                  <p className="vp-hint">{TOPIC_HINTS[c]}</p>
                  <input
                    id={`w-${c}`}
                    type="range"
                    min={0}
                    max={5}
                    step={1}
                    value={v}
                    aria-valuetext={LEVELS[v]}
                    style={{ ["--fill" as string]: `${(v / 5) * 100}%` }}
                    onChange={(e) => update({ weights: { ...p.weights, [c]: Number(e.target.value) } })}
                  />
                </li>
              );
            })}
          </ul>

          <label className="vp-words" htmlFor="stances">
            <span>In your own words</span>
            <textarea
              id="stances"
              rows={4}
              maxLength={2000}
              placeholder="For example: I care most about fair wages and want to know where my clothes are made."
              value={p.stances}
              onChange={(e) => update({ stances: e.target.value })}
            />
          </label>

          <div className="vp-actions">
            <button className="button-light button-lg" onClick={save} disabled={status === "saving"}>
              {status === "saving" ? "Saving…" : "Save my values"}
            </button>
            <span className="vp-status" role="status">
              {status === "saved" && "Saved."}
              {status === "error" && "Couldn't save. Check the server and try again."}
            </span>
          </div>
        </section>
      </div>
    </div>
  );
}
