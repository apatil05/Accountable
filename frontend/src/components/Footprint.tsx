// "Footprint and record" on the company page: your estimated CO2e, what the company reports,
// and its US labor record. Owner: Person A. Presentational only; the page fetches the data.
// Rules (docs/DATA.md): label estimates as estimates, show source + year next to every number,
// and never show a missing value as zero.
import type { CompanyEmissions, EmissionsFigures, LaborSummary, MerchantSummary } from "../api/types";
import { FIGURE_LABELS, formatKg, formatTonnes, headlineFigure, pickEmissions, possessive } from "../lib/footprint";

type Loadable<T> = T | null | "missing"; // null = loading, "missing" = request failed

const usd0 = (n: number) => n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const FIGURE_ORDER: (keyof EmissionsFigures)[] = ["scope1", "scope2_market", "scope12_market", "scope3", "total", "owned_assets"];

type Props = {
  name: string;
  me: MerchantSummary | null;
  emissions: Loadable<CompanyEmissions[]>;
  labor: Loadable<LaborSummary>;
};

export default function Footprint({ name, me, emissions, labor }: Props) {
  return (
    <section className="co-section" aria-labelledby="footprint-title">
      <div className="co-section-head">
        <h2 id="footprint-title">Footprint and record</h2>
        <p>Numbers with their source and year. Estimates are marked as estimates; nothing missing is shown as zero.</p>
      </div>
      <div className="facts">
        <YourCarbon name={name} me={me} />
        <CompanyEmissionsCard name={name} emissions={emissions} />
        <LaborCard name={name} labor={labor} />
      </div>
    </section>
  );
}

function YourCarbon({ name, me }: { name: string; me: MerchantSummary | null }) {
  const c = me?.carbon;
  return (
    <article className="fact">
      <h3>Your purchases</h3>
      {!me ? (
        <p className="co-muted">Loading…</p>
      ) : !c ? (
        <p className="co-muted">No estimate for this kind of purchase yet.</p>
      ) : (
        <>
          <p className="fact-figure">≈ {formatKg(c.kg_co2e)}<span className="fact-unit">CO₂e</span></p>
          <p className="fact-caption">
            Estimated emissions behind the {usd0(me.total_spent)} you spent at {name}
            {c.includes_use_phase ? ", including burning the fuel" : ""}.
          </p>
          <span className="status">Estimate · {c.confidence} confidence</span>
          <p className="fact-source">
            US average for {c.factor_label.charAt(0).toLowerCase() + c.factor_label.slice(1)}, {c.data_year} data. Not {possessive(name)} own footprint.
          </p>
        </>
      )}
    </article>
  );
}

function SourceLine({ doc }: { doc: CompanyEmissions }) {
  const p = doc.provenance;
  const where = p.locator ? `, ${p.locator}` : "";
  return p.source_url ? (
    <a className="co-source" href={p.source_url} target="_blank" rel="noopener noreferrer">{p.source_name}{where}</a>
  ) : (
    <span className="fact-source">{p.source_name}</span>
  );
}

function CompanyEmissionsCard({ name, emissions }: { name: string; emissions: Loadable<CompanyEmissions[]> }) {
  const { reported, independent } = Array.isArray(emissions) ? pickEmissions(emissions) : {};
  const facilities = independent?.figures?.owned_assets ?? null;
  return (
    <article className="fact">
      <h3>{reported ? `What ${name} reports` : `${possessive(name)} emissions`}</h3>
      {emissions === null ? (
        <p className="co-muted">Loading…</p>
      ) : !reported && facilities === null ? (
        <p className="co-muted">No emissions figures found yet.</p>
      ) : reported ? (
        <>
          <ReportedEmissions name={name} doc={reported} />
          {independent && facilities !== null && (
            <div className="fact-aside">
              <p className="fact-caption">
                Independent estimate: {formatTonnes(facilities)} CO₂e from facilities {name} owns ({independent.data_year}).
              </p>
              <SourceLine doc={independent} />
            </div>
          )}
        </>
      ) : (
        independent && facilities !== null && <IndependentEmissions name={name} doc={independent} tonnes={facilities} />
      )}
    </article>
  );
}

function IndependentEmissions({ name, doc, tonnes }: { name: string; doc: CompanyEmissions; tonnes: number }) {
  return (
    <>
      <p className="fact-figure">{formatTonnes(tonnes)}<span className="fact-unit">CO₂e</span></p>
      <p className="fact-caption">From facilities {name} owns, {doc.data_year}. Counts a facility fully toward a majority owner.</p>
      <span className="status">Independent estimate</span>
      <SourceLine doc={doc} />
    </>
  );
}

function ReportedEmissions({ name, doc }: { name: string; doc: CompanyEmissions }) {
  if (doc.status === "not_reported" || !doc.figures) {
    return (
      <>
        <p className="fact-figure is-word">Doesn't report</p>
        <p className="fact-caption">{doc.reason ?? `${name} publishes no emissions figures.`}</p>
      </>
    );
  }
  const head = headlineFigure(doc.figures);
  const f = doc.figures;
  const rows = FIGURE_ORDER.filter((k) => f[k] != null && k !== "owned_assets" && k !== head?.key);
  return (
    <>
      {head && <p className="fact-figure">{formatTonnes(head.tonnes)}<span className="fact-unit">CO₂e</span></p>}
      {head && <p className="fact-caption">{head.label}, {doc.data_year}</p>}
      {doc.provenance.method === "pdf_verified" && <span className="status is-verified">Checked against the report</span>}
      {rows.length > 0 && (
        <dl className="fact-list">
          {rows.map((k) => (
            <div key={k}><dt>{FIGURE_LABELS[k]}</dt><dd>{formatTonnes(f[k] as number)}</dd></div>
          ))}
        </dl>
      )}
      <SourceLine doc={doc} />
    </>
  );
}

function LaborCard({ name, labor }: { name: string; labor: Loadable<LaborSummary> }) {
  return (
    <article className="fact">
      <h3>US labor record</h3>
      {labor === null ? (
        <p className="co-muted">Loading…</p>
      ) : labor === "missing" || !labor.loaded ? (
        <p className="co-muted">Not checked yet for {name}.</p>
      ) : labor.case_count === 0 ? (
        <>
          <p className="fact-figure is-word">No cases found</p>
          <p className="fact-caption">No concluded wage and hour cases under {possessive(name)} name.</p>
          <a className="co-source" href={labor.source_url} target="_blank" rel="noopener noreferrer">{labor.source_name}</a>
        </>
      ) : (
        <>
          <p className="fact-figure">{labor.case_count.toLocaleString("en-US")}<span className="fact-unit">{labor.case_count === 1 ? "case" : "cases"}</span></p>
          <p className="fact-caption">
            Concluded wage and hour cases
            {labor.earliest_finding && labor.latest_finding ? `, ${labor.earliest_finding.slice(0, 4)}–${labor.latest_finding.slice(0, 4)}` : ""}
          </p>
          <dl className="fact-list">
            <div><dt>Back wages owed to workers</dt><dd>{usd0(labor.back_wages)}</dd></div>
            <div><dt>Civil penalties</dt><dd>{usd0(labor.penalties)}</dd></div>
            <div><dt>Workers affected</dt><dd>{labor.employees_affected.toLocaleString("en-US")}</dd></div>
            <div><dt>Cases with child labor violations</dt><dd>{labor.child_labor_cases}</dd></div>
          </dl>
          <p className="fact-source">{labor.note}</p>
          <a className="co-source" href={labor.source_url} target="_blank" rel="noopener noreferrer">{labor.source_name}</a>
        </>
      )}
    </article>
  );
}
