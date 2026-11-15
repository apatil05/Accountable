// Ask the server to research a company that has no research yet, and wait for it. Owner: Person A.
// POST /api/merchants/{id}/research starts it (a few minutes, once per company, capped per day); asking again
// reports progress. The page keeps asking every few seconds until the research is saved, then reloads it.
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";

export type ResearchProgress = "idle" | "working" | "capped" | "failed" | "unavailable";

export const POLL_MS = 8000;
const MAX_POLLS = 200; // about 26 minutes; a research normally takes 3-5

export function progressFor(status: string): ResearchProgress {
  if (status === "started" || status === "running" || status === "busy") return "working";
  if (status === "capped" || status === "failed" || status === "unavailable") return status;
  return "idle"; // "ready" or anything unexpected
}

export function useOnDemandResearch(id: string, needed: boolean, onReady: () => void): ResearchProgress {
  const [progress, setProgress] = useState<ResearchProgress>("idle");
  const ready = useRef(onReady);
  ready.current = onReady;

  useEffect(() => {
    if (!needed || !id) {
      setProgress("idle");
      return;
    }
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const ask = (attempt: number) => {
      api
        .requestResearch(id)
        .then(({ status }) => {
          if (stopped) return;
          if (status === "ready") {
            setProgress("idle");
            ready.current();
            return;
          }
          const next = progressFor(status);
          setProgress(next);
          if (next === "working" && attempt < MAX_POLLS) timer = setTimeout(() => ask(attempt + 1), POLL_MS);
        })
        .catch(() => {
          if (!stopped) setProgress("unavailable"); // server unreachable: show the placeholder, don't keep hammering it
        });
    };
    ask(1);
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }, [id, needed]);

  return progress;
}
