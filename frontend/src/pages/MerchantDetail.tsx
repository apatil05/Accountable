// Merchant deep-dive: ethics breakdown (B), supply-chain map + alternatives (D). Page owner: Person C.
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import { CATEGORY_LABELS, type Alternative, type EthicsReport, type SupplyChain } from "../api/types";
import ScoreBadge, { scoreTone } from "../components/ScoreBadge";
import WorldMap from "../components/WorldMap";
import MockBanner from "../components/MockBanner";

export default function MerchantDetail() {
  const { id = "" } = useParams();
  const [report, setReport] = useState<EthicsReport | null>(null);
  const [chain, setChain] = useState<SupplyChain | null>(null);
  const [alts, setAlts] = useState<Alternative[]>([]);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    api.ethics(id).then(setReport).catch(() => setReport(null));
    api.supplyChain(id).then(setChain).catch(() => setChain(null));
    api.alternatives(id).then(setAlts).catch(() => setAlts([]));
  }, [id]);

  if (!report) return <div className="loading">Researching…</div>;

  return (
    <>
      {report.data_source === "mock" && <MockBanner />}
      <Link to="/" className="back">← All merchants</Link>
      <section className="hero">
        <div>
          <p className="eyebrow">Merchant report</p>
          <h1>{report.merchant_name}</h1>
        </div>
        <div className="stats">
          <div className="stat"><ScoreBadge score={report.personalized_score} size="lg" />for you</div>
          <div className="stat"><ScoreBadge score={report.overall_score} size="lg" />overall</div>
        </div>
      </section>

      <div className="grid-2">
        <div className="card">
          <h3>Ethics breakdown</h3>
          {report.categories.map((c) => (
            <div key={c.category} className="cat">
              <button className="cat-head" onClick={() => setOpen(open === c.category ? null : c.category)}>
                <span>{CATEGORY_LABELS[c.category] ?? c.category}</span>
                <div className={`cat-bar tone-${scoreTone(c.score)}`}><div style={{ width: `${c.score}%` }} /></div>
                <span className="cat-score">{Math.round(c.score)}</span>
              </button>
              {open === c.category && (
                <div className="cat-body">
                  <p>{c.summary}</p>
                  <ul className="claims">
                    {c.claims.map((cl, i) => (
                      <li key={i}>
                        <span className={`pill ${cl.validated ? "pill-ok" : "pill-warn"}`}>
                          {cl.validated ? "Verified" : "Unverified"}
                        </span>
                        {cl.text}{" "}
                        {cl.source_url && <a href={cl.source_url} target="_blank" rel="noreferrer">{cl.source_name ?? "source"}</a>}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          ))}
        </div>

        <div className="card">
          <h3>Better-aligned alternatives</h3>
          {alts.length === 0 && <p className="muted">No suggestions yet.</p>}
          <ul className="alts">
            {alts.map((a) => (
              <li key={a.name}>
                <strong>{a.name}</strong> {a.is_local && <span className="pill pill-ok">Local</span>}
                <div className="muted">{a.reason}</div>
              </li>
            ))}
          </ul>
        </div>
      </div>

      {chain && (
        <div className="card">
          <h3>From source to you: {chain.product}</h3>
          <WorldMap chains={[chain]} labels={{ [chain.merchant_id]: report.merchant_name }} />
          <ol className="stages">
            {chain.stages.map((s) => (
              <li key={s.order}><strong>{s.stage}</strong><span className="muted">{s.place}</span></li>
            ))}
          </ol>
        </div>
      )}
    </>
  );
}
