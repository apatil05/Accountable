// "What we found" on the company page: one research topic at a time, as tabs. Owner: Person C.
// Opens on the first topic, so it never looks like a choice was made for the user. Only validated, sourced claims are shown.
import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { CATEGORY_LABELS, type CategoryScore } from "../api/types";
import { firstSentence } from "../lib/topics";

const MAX_SOURCES = 2; // keep each topic scannable; best source tier first (company's own site is tier 4), then most confident
const label = (c: string) => CATEGORY_LABELS[c as keyof typeof CATEGORY_LABELS] ?? c;

export default function TopicTabs({ topics, initial }: { topics: CategoryScore[]; initial: string | null }) {
  const base = useId();
  const [active, setActive] = useState(initial);
  const touched = useRef(false);
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);

  // The profile can arrive after the report: follow the default until the user picks a tab.
  useEffect(() => {
    if (!touched.current) setActive(initial);
  }, [initial]);

  const index = Math.max(0, topics.findIndex((t) => t.category === active));
  const topic = topics[index];
  if (!topic) return null;

  const choose = (i: number, focus = false) => {
    touched.current = true;
    setActive(topics[i].category);
    if (focus) tabs.current[i]?.focus();
  };
  const onKey = (e: KeyboardEvent) => {
    const last = topics.length - 1;
    const next = { ArrowRight: index === last ? 0 : index + 1, ArrowLeft: index === 0 ? last : index - 1, Home: 0, End: last }[e.key];
    if (next === undefined) return;
    e.preventDefault();
    choose(next, true);
  };

  const sources = topic.claims
    .filter((c) => c.validated && c.source_url)
    .sort((a, b) => (a.source_tier ?? 9) - (b.source_tier ?? 9) || b.confidence - a.confidence)
    .slice(0, MAX_SOURCES);
  const tabId = (i: number) => `${base}-tab-${i}`;
  const panelId = `${base}-panel`;

  return (
    <div className="co-tabs-wrap">
      {topics.length > 1 && (
        <div className="co-tabs" role="tablist" aria-label="Research topics" onKeyDown={onKey}>
          {topics.map((t, i) => (
            <button
              key={t.category}
              ref={(el) => { tabs.current[i] = el; }}
              type="button"
              role="tab"
              id={tabId(i)}
              className="co-tab"
              aria-selected={i === index}
              aria-controls={panelId}
              tabIndex={i === index ? 0 : -1}
              onClick={() => choose(i)}
            >
              {label(t.category)}
            </button>
          ))}
        </div>
      )}
      <article
        className="co-tabpanel"
        id={panelId}
        role={topics.length > 1 ? "tabpanel" : undefined}
        aria-labelledby={topics.length > 1 ? tabId(index) : undefined}
      >
        <h3>{label(topic.category)}</h3>
        <p className="co-topic-summary">{firstSentence(topic.summary)}</p>
        {sources.length === 0 ? (
          <p className="co-source is-missing">No verified sources yet</p>
        ) : (
          <ul className="co-claims">
            {sources.map((c, i) => (
              <li key={i}>
                <span className="status is-verified">Verified</span>
                <a href={c.source_url!} target="_blank" rel="noreferrer" className="co-source" title={c.text}>
                  {c.source_name ?? new URL(c.source_url!).hostname}
                </a>
              </li>
            ))}
          </ul>
        )}
      </article>
    </div>
  );
}
