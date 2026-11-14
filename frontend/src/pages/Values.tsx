// Feature 2: the user's ethical / moral / political stances. Owner: Person C.
// The form serializes straight to the UserProfile JSON document.
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { CATEGORIES, CATEGORY_LABELS, type UserProfile } from "../api/types";

const WEIGHT_LABELS = ["Don't care", "A little", "Somewhat", "Matters", "A lot", "Deal-breaker"];

export default function Values() {
  const [p, setP] = useState<UserProfile | null>(null);
  const [source, setSource] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.profile().then(setP);
  }, []);
  if (!p) return <div className="loading">Loading…</div>;

  const update = (patch: Partial<UserProfile>) => {
    setSaved(false);
    setP({ ...p, ...patch });
  };

  const addSource = () => {
    const s = source.trim();
    if (s && !p.trusted_sources.includes(s)) update({ trusted_sources: [...p.trusted_sources, s] });
    setSource("");
  };

  return (
    <div className="values">
      <p className="eyebrow">Your values</p>
      <h1>What matters to you?</h1>
      <p className="muted lead">We weigh each merchant's record by what you care about. Nothing here is shared.</p>

      <div className="card">
        <h3>How much does each issue matter?</h3>
        {CATEGORIES.map((c) => (
          <div className="weight-row" key={c}>
            <label htmlFor={c}>{CATEGORY_LABELS[c]}</label>
            <input
              id={c}
              type="range"
              min={0}
              max={5}
              value={p.weights[c] ?? 0}
              onChange={(e) => update({ weights: { ...p.weights, [c]: Number(e.target.value) } })}
            />
            <span className="weight-label">{WEIGHT_LABELS[p.weights[c] ?? 0]}</span>
          </div>
        ))}
      </div>

      <div className="card">
        <h3>In your own words</h3>
        <textarea
          rows={4}
          placeholder="e.g. I care most about fair wages and avoiding companies with poor labor records…"
          value={p.stances}
          onChange={(e) => update({ stances: e.target.value })}
        />
      </div>

      <div className="card">
        <h3>Sources you trust</h3>
        <div className="chips">
          {p.trusted_sources.map((s) => (
            <button key={s} className="chip" onClick={() => update({ trusted_sources: p.trusted_sources.filter((x) => x !== s) })}>
              {s} ×
            </button>
          ))}
        </div>
        <div className="inline-input">
          <input
            placeholder="e.g. reuters.com"
            value={source}
            onChange={(e) => setSource(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && addSource()}
          />
          <button onClick={addSource}>Add</button>
        </div>
      </div>

      <label className="card toggle">
        <input type="checkbox" checked={p.prefer_local} onChange={(e) => update({ prefer_local: e.target.checked })} />
        Prefer local alternatives when suggesting swaps
      </label>

      <div className="actions">
        <button className="primary" onClick={() => api.saveProfile(p).then(() => setSaved(true))}>
          Save my values
        </button>
        {saved && <span className="muted">Saved. Scores updated.</span>}
      </div>
    </div>
  );
}
