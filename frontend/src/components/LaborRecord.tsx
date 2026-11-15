// US labor record on the company page: right to organize (NLRB), workplace safety (OSHA), and
// wages (DOL), all for the last two years. Owner: Person A. Presentational only.
// Rules (docs/DATA.md): charges are allegations, so outcomes are shown with them; "not checked"
// is never shown as zero; every section links to its agency.
import type { LaborSummary, OrganizingSummary, SafetySummary } from "../api/types";
import { possessive } from "../lib/footprint";

type Loadable<T> = T | null | "missing";

const usd0 = (n: number) => n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const count = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;
const monthYear = (iso: string) =>
  new Date(`${iso}T00:00:00`).toLocaleDateString("en-US", { month: "short", year: "numeric" });
// "8(a)(3) Discharge (Including Layoff...)" -> "Discharge (Including Layoff...)"
const plainAllegation = (a: string) => a.replace(/^\d\([a-z]\)(\(\d\))?\s*/i, "");

function Source({ name, url }: { name: string; url: string }) {
  return <a className="co-source" href={url} target="_blank" rel="noopener noreferrer">{name}</a>;
}

function Organizing({ name, o }: { name: string; o: OrganizingSummary | null }) {
  if (!o?.loaded) return <p className="co-muted">Not checked yet.</p>;
  if (o.charges === 0 && o.petitions === 0) return <p className="fact-caption">No charges or election petitions involving {name}.</p>;
  return (
    <>
      <p className="labor-headline">{count(o.charges, "unfair labor practice charge")}</p>
      <dl className="fact-list">
        <div><dt>Still open</dt><dd>{o.charges_open}</dd></div>
        <div><dt>Settled or remedied</dt><dd>{o.charges_remedied}</dd></div>
        <div><dt>Dismissed or withdrawn</dt><dd>{o.charges_dismissed}</dd></div>
        <div><dt>Union election petitions</dt><dd>{o.petitions}</dd></div>
        <div><dt>Elections won by the union</dt><dd>{o.unions_certified}</dd></div>
        <div><dt>Elections the union lost</dt><dd>{o.elections_union_lost}</dd></div>
      </dl>
      {o.top_allegations.length > 0 && (
        <p className="fact-source">Most common allegation: {plainAllegation(o.top_allegations[0])}.</p>
      )}
      <p className="fact-source">A charge is an allegation filed with the NLRB, not a finding.</p>
      <Source name={o.source_name} url={o.source_url} />
    </>
  );
}

function Safety({ name, s }: { name: string; s: SafetySummary | null }) {
  if (!s?.loaded) return <p className="co-muted">Not checked yet.</p>;
  if (s.inspections === 0) return <p className="fact-caption">No OSHA inspections at {possessive(name)} workplaces.</p>;
  return (
    <>
      <p className="labor-headline">{count(s.inspections, "OSHA inspection")}</p>
      <dl className="fact-list">
        <div><dt>Opened after an accident or death</dt><dd>{s.after_accident}</dd></div>
        <div><dt>Serious violations</dt><dd>{s.serious}</dd></div>
        <div><dt>Willful or repeat violations</dt><dd>{s.willful + s.repeat}</dd></div>
        <div><dt>Penalties</dt><dd>{usd0(s.penalties)}</dd></div>
      </dl>
      <Source name={s.source_name} url={s.source_url} />
    </>
  );
}

function Wages({ name, l }: { name: string; l: LaborSummary }) {
  if (!l.loaded) return <p className="co-muted">Not checked yet.</p>;
  if (l.case_count === 0) return <p className="fact-caption">No concluded wage and hour cases under {possessive(name)} name.</p>;
  return (
    <>
      <p className="labor-headline">{count(l.case_count, "wage and hour case")}</p>
      <dl className="fact-list">
        <div><dt>Back wages owed to workers</dt><dd>{usd0(l.back_wages)}</dd></div>
        <div><dt>Civil penalties</dt><dd>{usd0(l.penalties)}</dd></div>
        <div><dt>Workers affected</dt><dd>{l.employees_affected.toLocaleString("en-US")}</dd></div>
        <div><dt>Cases with child labor violations</dt><dd>{l.child_labor_cases}</dd></div>
      </dl>
      <Source name={l.source_name} url={l.source_url} />
    </>
  );
}

export default function LaborRecord({ name, labor }: { name: string; labor: Loadable<LaborSummary> }) {
  return (
    <article className="fact is-wide">
      <h3>US labor record</h3>
      {labor === null ? (
        <p className="co-muted">Loading…</p>
      ) : labor === "missing" ? (
        <p className="co-muted">Couldn't load the labor record.</p>
      ) : (
        <>
          <p className="fact-caption">
            Last two years{labor.window_start ? `, since ${monthYear(labor.window_start)}` : ""}. {labor.note}
          </p>
          <div className="labor-parts">
            <section aria-label="Right to organize"><h4>Right to organize</h4><Organizing name={name} o={labor.organizing} /></section>
            <section aria-label="Workplace safety"><h4>Workplace safety</h4><Safety name={name} s={labor.safety} /></section>
            <section aria-label="Wages"><h4>Wages</h4><Wages name={name} l={labor} /></section>
          </div>
        </>
      )}
    </article>
  );
}
