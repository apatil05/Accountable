// Feature 1: merchants + frequency + total. Owner: Person C.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { SpendingSummary } from "../api/types";
import ScoreBadge from "../components/ScoreBadge";
import MockBanner from "../components/MockBanner";

const usd = (n: number) => n.toLocaleString("en-US", { style: "currency", currency: "USD" });

export default function Dashboard() {
  const [data, setData] = useState<SpendingSummary | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.summary().then(setData).catch((e) => setErr(String(e)));
  }, []);

  if (err) return <div className="error">Couldn't reach the API. Is the backend running on :8000?<br />{err}</div>;
  if (!data) return <div className="loading">Loading your spending…</div>;

  const max = Math.max(...data.merchants.map((m) => m.total_spent));
  const scored = data.merchants.filter((m) => m.personalized_score != null);
  const weighted =
    scored.reduce((a, m) => a + (m.personalized_score ?? 0) * m.total_spent, 0) /
    (scored.reduce((a, m) => a + m.total_spent, 0) || 1);

  return (
    <>
      <MockBanner />
      <section className="hero">
        <div>
          <p className="eyebrow">Last 90 days</p>
          <h1>Where your money went</h1>
        </div>
        <div className="stats">
          <div className="stat"><span>{usd(data.total_spent)}</span>spent</div>
          <div className="stat"><span>{data.merchant_count}</span>merchants</div>
          <div className="stat"><ScoreBadge score={weighted} size="lg" />values alignment</div>
        </div>
      </section>

      <ul className="merchant-list">
        {data.merchants.map((m) => (
          <li key={m.merchant_id}>
            <Link to={`/merchant/${m.merchant_id}`} className="merchant-row">
              <div className="merchant-name">
                <strong>{m.name}</strong>
                <span className="muted">{m.category.join(" · ")}</span>
              </div>
              <div className="merchant-bar">
                <div style={{ width: `${(m.total_spent / max) * 100}%` }} />
              </div>
              <div className="merchant-num">
                <strong>{usd(m.total_spent)}</strong>
                <span className="muted">{m.purchase_count}× purchases</span>
              </div>
              <ScoreBadge score={m.personalized_score} />
            </Link>
          </li>
        ))}
      </ul>
    </>
  );
}
